"""The web app: one page behind basic auth, a public health check, and the collectors."""
import logging
import os
import pathlib
import secrets
import threading
from contextlib import asynccontextmanager, closing

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import collect_odoo, collect_shopify, ledger
from app.register import load_register, writer_for

HERE = pathlib.Path(__file__).parent
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Odoo call is noise
APP_USER = os.environ.get("APP_USER", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if not APP_USER or not APP_PASSWORD:
    raise SystemExit("APP_USER and APP_PASSWORD must be set")
if len(APP_PASSWORD) < 12:
    raise SystemExit("APP_PASSWORD must be at least 12 characters")
REGISTER = load_register(os.environ.get("REGISTER_PATH", "writers.yaml"))
LEDGER_DB = os.environ.get("LEDGER_DB", "ledger.db")

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
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


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    if collect_odoo.URL:
        threading.Thread(target=collect_odoo.run_forever, args=(LEDGER_DB, stop), daemon=True).start()
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
    with closing(ledger.connect(LEDGER_DB)) as conn:
        unknown, writers = writer_rows(REGISTER, ledger.activity(conn))
        context = {"unknown": unknown, "writers": writers, "filters": filters, "loc": LOC,
                   "writes": ledger.latest_writes(conn, **filters), "options": ledger.filter_options(conn)}
    context["unregistered"] = {(u["system"], u["identity"]) for u in unknown if u["identity"] != ledger.UNATTRIBUTED}
    return templates.TemplateResponse(request, "index.html", context)


@pages.get("/style.css")
def stylesheet():
    return FileResponse(HERE / "static" / "style.css", media_type="text/css")


app.include_router(pages)
app.include_router(collect_shopify.router)  # public: Shopify proves itself with the HMAC signature
