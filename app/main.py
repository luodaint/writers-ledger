"""The web app: one page behind basic auth, plus a public health check."""
import os
import pathlib
import secrets

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.register import load_register

HERE = pathlib.Path(__file__).parent
APP_USER = os.environ.get("APP_USER", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if not APP_USER or not APP_PASSWORD:
    raise SystemExit("APP_USER and APP_PASSWORD must be set")
if len(APP_PASSWORD) < 12:
    raise SystemExit("APP_PASSWORD must be at least 12 characters")
REGISTER = load_register(os.environ.get("REGISTER_PATH", "writers.yaml"))

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


app = FastAPI(title="Writers Ledger", docs_url=None, redoc_url=None, openapi_url=None)
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
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"writers": REGISTER, "loc": LOC})


@pages.get("/style.css")
def stylesheet():
    return FileResponse(HERE / "static" / "style.css", media_type="text/css")


app.include_router(pages)
