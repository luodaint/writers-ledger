"""The web app: one page behind basic auth, a public health check, the collectors and the checks."""
import logging
import os
import pathlib
import secrets
import threading
from contextlib import asynccontextmanager, closing
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import collect_odoo, collect_shopify, ledger, rules
from app.register import UNATTRIBUTED, load_register, writer_for

HERE = pathlib.Path(__file__).parent
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Odoo call is noise
log = logging.getLogger("check")
APP_USER = os.environ.get("APP_USER", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if not APP_USER or not APP_PASSWORD:
    raise SystemExit("APP_USER and APP_PASSWORD must be set")
if len(APP_PASSWORD) < 12:
    raise SystemExit("APP_PASSWORD must be at least 12 characters")
REGISTER_PATH = os.environ.get("REGISTER_PATH", "writers.yaml")
register = load_register(REGISTER_PATH)  # the last good one: a broken edit of writers.yaml never replaces it
LEDGER_DB = os.environ.get("LEDGER_DB", "ledger.db")
WINDOW = timedelta(days=7)  # writer rules count a week of writes, so a writer that stopped resolves
SETTLE = timedelta(minutes=int(os.environ.get("DRIFT_SETTLE_MINUTES", "10")))
CHECK_LOCK = threading.Lock()

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",  # no-referrer would make our own form posts say `Origin: null`
    "Strict-Transport-Security": "max-age=31536000",
    "Cache-Control": "no-store",
}


def count_loc() -> int:
    """Non-blank, non-comment Python lines in app/ (the same rule as `make loc`)."""
    lines = (line.strip() for f in HERE.glob("*.py") for line in f.read_text().splitlines())
    return sum(1 for line in lines if line and not line.startswith("#"))


def require_login(creds: HTTPBasicCredentials = Depends(HTTPBasic())) -> None:
    user_ok = secrets.compare_digest(creds.username.encode(), APP_USER.encode())
    password_ok = secrets.compare_digest(creds.password.encode(), APP_PASSWORD.encode())
    if not (user_ok and password_ok):
        raise HTTPException(401, "Wrong user or password", {"WWW-Authenticate": "Basic"})


def writer_rows(writers, activity: dict) -> tuple[list, list]:
    """Identities seen in the ledger but missing from the register, then each writer with its activity."""
    unknown = [{"system": system, "identity": identity, **stats}
               for (system, identity), stats in sorted(activity.items())
               if not writer_for(writers, system, identity)]
    rows = []
    for w in writers:
        mine = [stats for (system, identity), stats in activity.items() if writer_for([w], system, identity)]
        rows.append({"writer": w, "writes": sum(s["writes"] for s in mine),
                     "last_seen": max((s["last_seen"] for s in mine), default=None)})
    return unknown, rows


def reload_register() -> tuple[list, str | None]:
    """Re-read writers.yaml, so an edit applies without a restart: the writers, and the problem if it's broken."""
    global register
    try:
        register = load_register(REGISTER_PATH)
    except (OSError, ValueError) as e:
        return register, str(e)
    return register, None


def run_check() -> int | None:
    """One check: the rules over the ledger and writers.yaml. Skipped while writers.yaml is
    broken, so a half-saved edit resolves nothing."""
    writers, problem = reload_register()
    if problem:
        log.error("Check skipped: %s", problem)
        return None
    with CHECK_LOCK, closing(ledger.connect(LEDGER_DB)) as conn:
        since = ledger.now(WINDOW)
        found = rules.check(ledger.writes_to_check(conn, since, tuple(rules.STOCK)), writers, since, ledger.now(SETTLE))
        ledger.save_check(conn, found, ledger.now())
    log.info("Check done: %d findings", len(found))
    return len(found)


def work(stop: threading.Event) -> None:
    """Every POLL_SECONDS: read Odoo, then check. Shopify writes arrive by webhook meanwhile."""
    conn, cache = ledger.connect(LEDGER_DB), {}
    while not stop.wait(collect_odoo.POLL_SECONDS):
        if collect_odoo.URL:
            collect_odoo.poll_once(conn, cache)
        try:
            run_check()
        except Exception:  # one failed check must not stop the next ones
            log.exception("Check failed")


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    threading.Thread(target=work, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Writers Ledger", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.state.ledger_db = LEDGER_DB
pages = APIRouter(dependencies=[Depends(require_login)])
templates = Jinja2Templates(directory=HERE / "templates")
LOC = count_loc()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


@pages.get("/")
def index(request: Request, system: str = "", model: str = "", identity: str = ""):
    filters = {"system": system, "model": model, "identity": identity}
    writers, problem = reload_register()
    with closing(ledger.connect(LEDGER_DB)) as conn:
        unknown, rows = writer_rows(writers, ledger.activity(conn))
        context = {"unknown": unknown, "writers": rows, "filters": filters, "loc": LOC,
                   "writes": ledger.latest_writes(conn, **filters), "options": ledger.filter_options(conn),
                   "findings": rules.rank(ledger.list_findings(conn)),
                   "last_check": ledger.get_cursor(conn, "check:last"), "register_error": problem}
    context["unregistered"] = {(u["system"], u["identity"]) for u in unknown if u["identity"] != UNATTRIBUTED}
    return templates.TemplateResponse(request, "index.html", context)


@pages.post("/check")
def check_now(request: Request):
    """The "Run check now" button. Browsers send basic auth along with cross-site form posts
    too, so only a post from this page counts."""
    site, origin = request.headers.get("sec-fetch-site"), request.headers.get("origin")
    if site not in (None, "same-origin") or (origin and urlsplit(origin).netloc != request.headers.get("host")):
        raise HTTPException(403, "Run the check from the Writers Ledger page")
    run_check()
    return RedirectResponse("/#findings", status_code=303)


@pages.get("/style.css")
def stylesheet():
    return FileResponse(HERE / "static" / "style.css", media_type="text/css")


app.include_router(pages)
app.include_router(collect_shopify.router)  # public: Shopify proves itself with the HMAC signature
