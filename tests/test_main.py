import importlib

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.register import RegisterError

AUTH = ("reviewer", "a-long-test-password")
# The tests' own register: writers.yaml as shipped. The demo has you edit the real one.
REGISTER = """
- id: shopify-odoo-sync
  owner: backend
  purpose: Creates sale orders in Odoo from Shopify orders
  identity: { odoo_user: sync@alohas.test, shopify_app: order-sync }
  may_write: [odoo:sale.order, odoo:res.partner]
- id: stock-cron
  owner: sysadmin
  purpose: Pushes Odoo stock levels to Shopify locations
  identity: { odoo_user: stock@alohas.test, shopify_app: stock-sync }
  may_write: [odoo:stock.quant, shopify:inventory_level]
- id: shopify-storefront
  owner: ecommerce
  purpose: Customers' own orders on the online store
  identity: { shopify_app: Online Store }
  may_write: [shopify:order]
- id: pmo-pricing-script
  owner: ""
  purpose: Bulk-updates product metafields for drops
  identity: { shopify_app: pricing-tool }
  may_write: [shopify:product]
"""
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
def main(monkeypatch, db_path, tmp_path):
    return start(monkeypatch, LEDGER_DB=str(db_path), REGISTER_PATH=str(register_file(tmp_path)))


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


# --- checks and findings -------------------------------------------------------------

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}


def register_file(tmp_path, extra=""):
    path = tmp_path / "writers.yaml"
    path.write_text(REGISTER + extra)
    return path


ADMIN_CRON = """
- id: forgotten-cron
  owner: sysadmin
  purpose: Nightly stock reset in Milan, found by the ledger
  identity: { odoo_user: admin }
  may_write: [odoo:stock.quant]
"""


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                                     {"Origin": "https://evil.example"}, {"Origin": "null"}])
def test_run_check_now_refuses_posts_from_other_sites(client, headers):
    assert client.post("/check", auth=AUTH, headers=headers, follow_redirects=False).status_code == 403


def test_our_own_form_posts_keep_their_origin(client):
    """With Referrer-Policy no-referrer, browsers send `Origin: null` on every form post,
    so the page's own Run check now button was refused (found in a real browser)."""
    policy = client.get("/", auth=AUTH).headers["referrer-policy"]
    assert policy == "same-origin"  # the origin for our own posts, nothing at all for other sites


@pytest.mark.parametrize("headers", [SAME_ORIGIN, {}])  # {}: curl or `make check`, not a browser
def test_run_check_now_runs_a_check_and_goes_back_to_the_findings(client, headers):
    response = client.post("/check", auth=AUTH, headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/#findings"


def test_a_check_turns_an_unknown_writer_into_a_card(client, db_path):
    seen(db_path, identity="admin")
    client.post("/check", auth=AUTH, headers=SAME_ORIGIN)
    page = client.get("/", auth=AUTH).text
    findings = page[page.index('id="findings"'):page.index('id="writers"')]
    assert 'class="finding red"' in findings
    assert "admin changed odoo:stock.quant once" in findings
    assert "Last check: never" not in page


def test_unattributed_writes_are_a_note_not_a_card(client, db_path):
    seen(db_path, system="shopify", model="inventory_level", identity="unattributed",
         payload={"sku": "AL01-36", "location": "Milan", "available": 4})
    client.post("/check", auth=AUTH, headers=SAME_ORIGIN)
    page = client.get("/", auth=AUTH).text
    findings = page[page.index('id="findings"'):page.index('id="writers"')]
    assert 'class="finding' not in findings
    assert 'class="note"' in findings and "without saying who" in findings


def test_editing_the_register_resolves_the_finding_without_a_restart(monkeypatch, tmp_path, db_path):
    path = register_file(tmp_path)
    main = start(monkeypatch, LEDGER_DB=str(db_path), REGISTER_PATH=str(path))
    seen(db_path, identity="admin")
    main.run_check()
    path.write_text(path.read_text() + ADMIN_CRON)
    main.run_check()
    [finding] = ledger.list_findings(ledger.connect(db_path))
    assert (finding["rule"], finding["status"]) == ("unknown_writer", "resolved")
    page = TestClient(main.app).get("/", auth=AUTH).text
    assert "forgotten-cron" in page and "Resolved" in page


def test_a_broken_register_edit_pauses_checks_and_keeps_the_findings(monkeypatch, tmp_path, db_path):
    path = register_file(tmp_path)
    main = start(monkeypatch, LEDGER_DB=str(db_path), REGISTER_PATH=str(path))
    seen(db_path, identity="admin")
    main.run_check()
    path.write_text("- id: half-saved\n  owner: x\n")
    assert main.run_check() is None
    [finding] = ledger.list_findings(ledger.connect(db_path))
    assert finding["status"] == "open"
    page = TestClient(main.app).get("/", auth=AUTH).text
    assert "writers.yaml has a problem" in page and "half-saved" in page
    assert "stock-cron" in page  # the last good register still shows
