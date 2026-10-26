import importlib

import pytest
from fastapi.testclient import TestClient

from app.register import RegisterError

AUTH = ("reviewer", "a-long-test-password")
PUBLIC = {"/healthz", "/webhooks/shopify"}


def start(monkeypatch, **env):
    monkeypatch.setenv("APP_USER", AUTH[0])
    monkeypatch.setenv("APP_PASSWORD", AUTH[1])
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.main

    return importlib.reload(app.main)


@pytest.fixture
def main(monkeypatch):
    return start(monkeypatch)


@pytest.fixture
def client(main):
    return TestClient(main.app)


def test_page_requires_login(client):
    response = client.get("/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_wrong_password_is_rejected(client):
    assert client.get("/", auth=(AUTH[0], "nope")).status_code == 401
    assert client.get("/", auth=("admin", AUTH[1])).status_code == 401


def test_every_route_needs_login_except_public_ones(main, client):
    routes = [*main.app.routes, *main.pages.routes]
    endpoints = {(m, r.path) for r in routes for m in getattr(r, "methods", ())}
    assert ("GET", "/") in endpoints
    for method, path in endpoints:
        if path not in PUBLIC:
            assert client.request(method, path).status_code == 401, (method, path)


def test_healthz_is_public(client):
    assert client.get("/healthz").text == "ok"


def test_api_docs_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, auth=AUTH).status_code == 404, path


def test_security_headers_on_every_response(client):
    for response in (client.get("/", auth=AUTH), client.get("/healthz"), client.get("/")):
        csp = response.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert "script-src" not in csp
        assert response.headers["x-content-type-options"] == "nosniff"


def test_page_shows_the_register(client):
    page = client.get("/", auth=AUTH).text
    for writer_id in ("shopify-odoo-sync", "stock-cron", "pmo-pricing-script"):
        assert writer_id in page
    assert "No owner" in page
    assert "odoo:stock.quant" in page
    assert "lines of app code" in page


def test_stylesheet_is_served(client):
    response = client.get("/style.css", auth=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_register_text_is_escaped(monkeypatch, tmp_path):
    register = tmp_path / "writers.yaml"
    register.write_text(
        "- id: evil\n  owner: x\n  purpose: <script>alert(1)</script>\n"
        "  identity: { odoo_user: e@x.test }\n  may_write: [odoo:sale.order]\n"
    )
    main = start(monkeypatch, REGISTER_PATH=str(register))
    page = TestClient(main.app).get("/", auth=AUTH).text
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


@pytest.mark.parametrize("missing", ["APP_USER", "APP_PASSWORD"])
def test_refuses_to_start_without_credentials(monkeypatch, missing):
    monkeypatch.setenv(missing, "")
    with pytest.raises(SystemExit, match="APP_USER and APP_PASSWORD"):
        start(monkeypatch, **{missing: ""})


def test_refuses_to_start_with_a_short_password(monkeypatch):
    with pytest.raises(SystemExit, match="at least 12"):
        start(monkeypatch, APP_PASSWORD="short")


def test_refuses_to_start_with_a_broken_register(monkeypatch, tmp_path):
    register = tmp_path / "writers.yaml"
    register.write_text("- id: no-identity\n  owner: x\n  may_write: []\n")
    with pytest.raises(RegisterError, match="needs at least one identity"):
        start(monkeypatch, REGISTER_PATH=str(register))
