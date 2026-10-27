import base64
import hashlib
import hmac
import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import collect_shopify, ledger

SECRET = "observer-app-client-secret"
ORDER = {
    "id": 820982911946154508,
    "admin_graphql_api_id": "gid://shopify/Order/820982911946154508",
    "name": "#1001",
    "created_at": "2026-10-26T12:00:00+02:00",
    "updated_at": "2026-10-26T12:00:00+02:00",
    "app_id": 580111,
    "source_name": "web",
    "total_price": "240.00",
    "currency": "EUR",
    "financial_status": "paid",
    "email": "laura.perez@example.com",
    "phone": "+34600000000",
    "customer": {"first_name": "Laura", "last_name": "Pérez", "email": "laura.perez@example.com"},
    "shipping_address": {"address1": "Carrer de Mallorca 1", "city": "Barcelona"},
    "line_items": [{"sku": "AL01-36", "quantity": 1, "price": "120.00", "name": "Rife Sneaker - 36"}],
}
LEVEL = {
    "inventory_item_id": 271878346596884015,
    "location_id": 24826418,
    "available": 6,
    "updated_at": "2026-10-26T10:00:00-05:00",
    "admin_graphql_api_id": "gid://shopify/InventoryLevel/24826418?inventory_item_id=271878346596884015",
}
PRODUCT = {
    "id": 788032119674292922,
    "admin_graphql_api_id": "gid://shopify/Product/788032119674292922",
    "title": "Rife Sneaker",
    "status": "active",
    "handle": "rife-sneaker",
    "updated_at": "2026-10-26T10:00:00Z",
    "variants": [{"sku": "AL01-36", "inventory_item_id": 1}],
}


def sign(body: bytes, secret=SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


# --- signature -----------------------------------------------------------------


def test_valid_signature_is_accepted():
    body = json.dumps(ORDER).encode()
    assert collect_shopify.verify(body, sign(body), SECRET)


@pytest.mark.parametrize("case", ["tampered", "wrong secret", "missing", "no secret configured"])
def test_bad_signatures_are_rejected(case):
    body = json.dumps(ORDER).encode()
    signature, secret = sign(body), SECRET
    if case == "tampered":
        body = body.replace(b"240.00", b"0.01")
    elif case == "wrong secret":
        signature = sign(body, "someone-else")
    elif case == "missing":
        signature = ""
    else:
        secret = ""
    assert not collect_shopify.verify(body, signature, secret)


# --- payload to write ----------------------------------------------------------


def test_order_becomes_a_write_without_customer_data():
    write = collect_shopify.to_write("orders/create", ORDER, "Online Store", {})
    assert write == {
        "system": "shopify",
        "model": "order",
        "record_ref": "gid://shopify/Order/820982911946154508",
        "identity": "Online Store",
        "action": "create",
        "written_at": "2026-10-26T10:00:00Z",
        "payload": {"name": "#1001", "total_price": "240.00", "currency": "EUR", "financial_status": "paid", "skus": ["AL01-36"]},
    }
    text = json.dumps(write)
    for personal in ("Laura", "Pérez", "laura.perez", "+34600000000", "Mallorca"):
        assert personal not in text


def test_inventory_level_becomes_a_write_with_sku_and_location():
    write = collect_shopify.to_write("inventory_levels/update", LEVEL, "unattributed", {"sku": "AL01-36", "location": "Milan"})
    assert write["model"] == "inventory_level"
    assert write["action"] == "write"
    assert write["written_at"] == "2026-10-26T15:00:00Z"
    assert write["payload"] == {"sku": "AL01-36", "location": "Milan", "available": 6}


def test_product_becomes_a_write():
    write = collect_shopify.to_write("products/update", PRODUCT, "pricing-tool", {})
    assert (write["model"], write["identity"]) == ("product", "pricing-tool")
    assert write["payload"] == {"title": "Rife Sneaker", "status": "active"}


# --- attribution from the Admin API ---------------------------------------------

CHANGED = "2026-10-26T10:00:00Z"


def event(app, at="2026-10-26T10:00:01Z"):
    return {"appTitle": app, "attributeToApp": bool(app), "createdAt": at}


def product_events(*nodes):
    return {"product": {"events": {"nodes": list(nodes)}}}


def test_a_new_order_is_attributed_to_the_app_that_created_it():
    created = collect_shopify.identity_from
    assert created("orders/create", {"order": {"app": {"name": "Online Store"}}}, CHANGED) == ("Online Store", {})
    assert created("orders/create", {"order": {"app": None}}, CHANGED) == ("unattributed", {})


def test_an_order_update_is_attributed_by_its_events_not_its_creator():
    edited = {"order": {"events": {"nodes": [event("ops-script")]}}}
    assert collect_shopify.identity_from("orders/updated", edited, CHANGED) == ("ops-script", {})
    assert collect_shopify.identity_from("orders/updated", {"order": {"events": {"nodes": []}}}, CHANGED) == ("unattributed", {})


def test_product_identity_is_the_app_behind_the_event_of_that_change():
    assert collect_shopify.identity_from("products/update", product_events(event("pricing-tool")), CHANGED) == ("pricing-tool", {})
    assert collect_shopify.identity_from("products/update", product_events(event(None)), CHANGED) == ("unattributed", {})


def test_an_old_app_event_is_not_blamed_for_a_new_change():
    week_old = product_events(event("pricing-tool", at="2026-10-19T10:00:00Z"))
    assert collect_shopify.identity_from("products/update", week_old, CHANGED) == ("unattributed", {})


def test_a_later_event_by_another_app_does_not_take_the_blame():
    later = product_events(event("pricing-tool", at="2026-10-26T10:01:00Z"), event("rogue-app"))
    assert collect_shopify.identity_from("products/update", later, CHANGED) == ("rogue-app", {})


def test_two_authors_at_the_moment_of_the_change_is_ambiguous():
    both = product_events(event("pricing-tool"), event("rogue-app", at="2026-10-26T09:59:58Z"))
    assert collect_shopify.identity_from("products/update", both, CHANGED) == ("unattributed", {})


def test_an_app_named_like_the_sentinel_is_not_mistaken_for_unattributed():
    named = {"order": {"app": {"name": "unattributed"}}}
    assert collect_shopify.identity_from("orders/create", named, CHANGED) == ('"unattributed"', {})


def test_inventory_levels_are_never_attributed_but_get_sku_and_location():
    data = {"inventoryLevel": {"item": {"sku": "AL01-36"}, "location": {"name": "Milan"}}}
    assert collect_shopify.identity_from("inventory_levels/update", data, CHANGED) == ("unattributed", {"sku": "AL01-36", "location": "Milan"})


def test_access_token_comes_from_client_credentials_and_is_reused(monkeypatch):
    calls = []

    def shopify(request):
        calls.append(request)
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": "tok-1", "scope": "read_orders", "expires_in": 86399})
        assert request.headers["x-shopify-access-token"] == "tok-1"
        return httpx.Response(200, json={"data": {"order": {"app": {"name": "Online Store"}}}})

    monkeypatch.setattr(collect_shopify, "http", httpx.Client(transport=httpx.MockTransport(shopify)))
    monkeypatch.setattr(collect_shopify, "BASE", "https://dev.myshopify.com")
    monkeypatch.setattr(collect_shopify, "CLIENT_ID", "id-1")
    monkeypatch.setattr(collect_shopify, "CLIENT_SECRET", "secret-1")
    monkeypatch.setattr(collect_shopify, "_token", {"value": "", "expires": 0})
    for _ in range(2):
        assert collect_shopify.lookup("orders/create", "gid://shopify/Order/1", CHANGED) == ("Online Store", {})
    token_calls = [c for c in calls if c.url.path == "/admin/oauth/access_token"]
    assert len(token_calls) == 1
    assert dict(httpx.QueryParams(token_calls[0].content.decode())) == {"client_id": "id-1", "client_secret": "secret-1", "grant_type": "client_credentials"}
    assert calls[1].url.path == "/admin/api/2026-10/graphql.json"


# --- the endpoint ----------------------------------------------------------------


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_USER", "reviewer")
    monkeypatch.setenv("APP_PASSWORD", "a-long-test-password")
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.setattr(collect_shopify, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(collect_shopify, "lookup", lambda topic, gid, changed_at: ("Online Store", {}))
    import app.main

    main = importlib.reload(app.main)
    return main, TestClient(main.app), ledger.connect(tmp_path / "ledger.db")


def deliver(client, payload, topic="orders/create", webhook_id="wh-1", signature=None):
    body = json.dumps(payload).encode()
    headers = {
        "X-Shopify-Topic": topic,
        "X-Shopify-Webhook-Id": webhook_id,
        "X-Shopify-Hmac-Sha256": signature if signature is not None else sign(body),
        "Content-Type": "application/json",
    }
    return client.post("/webhooks/shopify", content=body, headers=headers)


def test_signed_webhook_is_stored_without_a_login(app):
    _, client, db = app
    assert deliver(client, ORDER).status_code == 200
    [row] = ledger.latest_writes(db)
    assert (row["model"], row["identity"]) == ("order", "Online Store")


def test_badly_signed_webhook_is_rejected_and_not_stored(app, caplog):
    _, client, db = app
    assert deliver(client, ORDER, signature=sign(b"other")).status_code == 401
    assert ledger.latest_writes(db) == []
    assert "bad signature" in caplog.text


def test_redelivered_webhook_is_stored_once(app):
    _, client, db = app
    deliver(client, ORDER)
    deliver(client, ORDER)
    assert len(ledger.latest_writes(db)) == 1


def test_replayed_body_under_a_new_delivery_id_is_stored_once(app):
    _, client, db = app
    deliver(client, ORDER, webhook_id="wh-1")
    deliver(client, ORDER, webhook_id="wh-forged")
    assert len(ledger.latest_writes(db)) == 1


def test_deliveries_without_an_id_are_not_mistaken_for_redeliveries(app):
    _, client, db = app
    deliver(client, ORDER, webhook_id="")
    other = ORDER | {"admin_graphql_api_id": "gid://shopify/Order/2", "name": "#1002"}
    deliver(client, other, webhook_id="")
    assert len(ledger.latest_writes(db)) == 2


def test_topics_we_do_not_watch_are_acknowledged_and_ignored(app):
    _, client, db = app
    assert deliver(client, {"id": 1}, topic="customers/create").status_code == 200
    assert ledger.latest_writes(db) == []


def test_oversized_body_is_refused(app):
    _, client, db = app
    huge = ORDER | {"note": "x" * (collect_shopify.MAX_BODY + 1)}
    assert deliver(client, huge).status_code == 413
    assert ledger.latest_writes(db) == []


def test_failed_lookup_still_records_the_write_as_unattributed(app, monkeypatch, caplog):
    _, client, db = app

    def down(topic, gid, changed_at):
        raise httpx.ConnectError("shopify unreachable")

    monkeypatch.setattr(collect_shopify, "lookup", down)
    assert deliver(client, ORDER).status_code == 200
    [row] = ledger.latest_writes(db)
    assert row["identity"] == "unattributed"
    assert "lookup failed" in caplog.text
