import importlib

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.register import RegisterError

AUTH = ("reviewer", "a-long-test-password")
PUBLIC = {"/healthz", "/webhooks/shopify"}


def start(monkeypatch, **env):
    monkeypatch.setenv("APP_USER", AUTH[0])
    monkeypatch.setenv("APP_PASSWORD", AUTH[1])
    monkeypatch.delenv("ODOO_URL", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.main

    return importlib.reload(app.main)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "ledger.db"


@pytest.fixture
def main(monkeypatch, db_path):
    return start(monkeypatch, LEDGER_DB=str(db_path))


def seen(db_path, **over):
    write = {"system": "odoo", "model": "stock.quant", "record_ref": "7", "identity": "stock@alohas.test",
             "action": "write", "written_at": "2026-10-26T10:00:00Z",
             "payload": {"sku": "AL01-36", "location": "Milan", "quantity": 4}} | over
    ledger.record_write(ledger.connect(db_path), write)


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
    routes = [*main.app.routes, *main.pages.routes, *main.collect_shopify.router.routes]
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


def test_ledger_shows_writes_with_their_details(client, db_path):
    seen(db_path)
    page = client.get("/", auth=AUTH).text
    assert "AL01-36" in page and "Milan" in page
    assert "No writes recorded yet" not in page


def test_ledger_filters_by_query_string(client, db_path):
    seen(db_path)
    seen(db_path, system="shopify", model="product", record_ref="gid://shopify/Product/9", identity="pricing-tool",
         payload={"title": "Rife Sneaker"})
    page = client.get("/?system=shopify", auth=AUTH).text
    assert "Product/9" in page
    assert "AL01-36" not in page


def test_odd_filter_values_are_harmless(client, db_path):
    seen(db_path)
    response = client.get("/", params={"identity": "x' OR 1=1 --<b>"}, auth=AUTH)
    assert response.status_code == 200
    assert "AL01-36" not in response.text
    assert "<b>" not in response.text


def test_writers_show_their_activity(client, db_path):
    seen(db_path, record_ref="1", written_at="2026-10-26T09:00:00Z")
    seen(db_path, record_ref="2", written_at="2026-10-26T10:30:00Z")
    seen(db_path, system="shopify", model="inventory_level", record_ref="3", identity="stock-sync",
         written_at="2026-10-26T11:00:00Z")
    page = client.get("/", auth=AUTH).text
    row = page[page.index("stock-cron"):]
    row = row[: row.index("</tr>")]
    assert "2026-10-26 11:00 UTC" in row
    assert ">3<" in row


def test_identities_missing_from_the_register_come_first_in_red(client, db_path):
    seen(db_path, identity="admin")
    page = client.get("/", auth=AUTH).text
    writers = page[page.index('id="writers"'):page.index('id="ledger"')]
    assert writers.index("admin") < writers.index("shopify-odoo-sync")
    assert 'class="unregistered"' in writers
    assert "Not in the register" in writers


def test_unattributed_writes_are_not_called_unregistered(client, db_path):
    seen(db_path, system="shopify", model="inventory_level", identity="unattributed")
    page = client.get("/", auth=AUTH).text
    writers = page[page.index('id="writers"'):page.index('id="ledger"')]
    assert "Not in the register" not in writers
    assert "Shopify did not say who" in writers


def test_a_writer_named_unattributed_still_shows_in_red(client, db_path):
    seen(db_path, system="shopify", model="product", identity='"unattributed"')
    page = client.get("/", auth=AUTH).text
    writers = page[page.index('id="writers"'):page.index('id="ledger"')]
    assert "Not in the register" in writers
    assert "did not say who" not in writers
