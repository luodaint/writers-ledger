"""A fake Shopify for offline mode: just enough of the Admin API and webhooks, and honest.

- Client credentials tokens, one per app: writers-ledger (the observer), stock-sync, pricing-tool.
- The GraphQL operations the simulator and the ledger send, matched by operation name,
  with the 2026-10 rules that bite: @idempotent and changeFromQuantity are required,
  and each app may only call the mutations its access scopes allow.
- Signed webhooks to the ledger, shaped like Shopify's. Like the real thing it never
  says which app changed an inventory level; orders name their app, products log events.
- POST /_sim/checkout plays a customer buying on the Online Store.
- State (stock, orders, product events, tokens) is kept in a JSON file on a volume,
  so a rebuild doesn't reset Shopify while Odoo keeps its stock.
"""

import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import httpx

from sim import catalog

API_VERSION = "2026-10"
SHOP = "alohas-dev.myshopify.com"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://app:8000/webhooks/shopify")
STATE_FILE = os.environ.get("FAKE_SHOPIFY_STATE", "/data/fake-shopify.json")
APPS = {  # client id -> client secret; the client id doubles as the app's name
    "writers-ledger": os.environ.get("SHOPIFY_CLIENT_SECRET", "local-only-ledger-secret"),
    "stock-sync": os.environ.get("STOCK_SYNC_CLIENT_SECRET", "local-only-stock-sync-secret"),
    "pricing-tool": os.environ.get("PRICING_TOOL_CLIENT_SECRET", "local-only-pricing-tool-secret"),
}
MUTATIONS = {"SetStock": {"stock-sync"}, "UpdateProduct": {"pricing-tool"}}  # access scopes, in short
CUSTOMERS = [("Laura", "Pérez", "laura.perez@example.com"), ("Marco", "Rossi", "marco.rossi@example.com")]

LOCATIONS = {name: n for n, name in enumerate(catalog.LOCATIONS, 1)}
PRODUCTS = {title: n for n, title in enumerate(catalog.SHOES, 1)}
ITEMS = {sku: n for n, (sku, _, _) in enumerate(catalog.variants(), 1)}
SKU_OF = {n: sku for sku, n in ITEMS.items()}
LOCATION_OF = {n: name for name, n in LOCATIONS.items()}
LEVELS = {(sku, loc): catalog.initial_stock(sku, loc) for sku in ITEMS for loc in LOCATIONS}
TOKENS, ORDER_APPS, EVENTS, NEXT = {}, {}, {}, {"order": 5001}
lock = threading.Lock()


def save() -> None:
    state = {"levels": [[sku, loc, q] for (sku, loc), q in LEVELS.items()], "tokens": TOKENS, "order_apps": ORDER_APPS, "events": EVENTS, "next": NEXT}
    with open(STATE_FILE + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


def load() -> None:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        LEVELS.update({(sku, loc): q for sku, loc, q in state["levels"]})
        for name, store in [("tokens", TOKENS), ("order_apps", ORDER_APPS), ("events", EVENTS), ("next", NEXT)]:
            store.update(state[name])


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def num(gid: str) -> int:
    return int(gid.rsplit("/", 1)[1].split("?")[0])


def emit(topic: str, payload: dict) -> None:
    """Deliver a signed webhook the way Shopify does, in the background."""
    body = json.dumps(payload).encode()
    signature = base64.b64encode(hmac.new(APPS["writers-ledger"].encode(), body, hashlib.sha256).digest())
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Topic": topic,
        "X-Shopify-Hmac-Sha256": signature.decode(),
        "X-Shopify-Shop-Domain": SHOP,
        "X-Shopify-API-Version": API_VERSION,
        "X-Shopify-Webhook-Id": str(uuid.uuid4()),
        "X-Shopify-Event-Id": str(uuid.uuid4()),
        "X-Shopify-Triggered-At": now(),
    }

    def deliver():
        try:
            httpx.post(WEBHOOK_URL, content=body, headers=headers, timeout=5).raise_for_status()
        except httpx.HTTPError as e:
            print(f"webhook {topic} not delivered: {e}", flush=True)

    threading.Thread(target=deliver, daemon=True).start()


def variants_of(title: str) -> list[dict]:
    return [{"sku": sku, "inventory_item_id": ITEMS[sku], "price": f"{120 + 10 * PRODUCTS[name] - 10}.00"} for sku, name, _ in catalog.variants() if name == title]


def catalog_query(app, variables, query):
    return {
        "locations": {"nodes": [{"id": f"gid://shopify/Location/{n}", "name": name} for name, n in LOCATIONS.items()]},
        "productVariants": {
            "nodes": [
                {"sku": sku, "inventoryItem": {"id": f"gid://shopify/InventoryItem/{ITEMS[sku]}"}, "product": {"id": f"gid://shopify/Product/{PRODUCTS[name]}", "title": name}}
                for sku, name, _ in catalog.variants()
            ]
        },
    }


def set_stock(app, variables, query):
    if "@idempotent" not in query:
        raise ValueError("The @idempotent directive is required for this mutation.")
    changed = []
    for q in variables["input"]["quantities"]:
        if "changeFromQuantity" not in q:
            raise ValueError("changeFromQuantity is required (use null to skip the check).")
        sku, location = SKU_OF[num(q["inventoryItemId"])], LOCATION_OF[num(q["locationId"])]
        if LEVELS[sku, location] != q["quantity"]:
            LEVELS[sku, location] = q["quantity"]
            changed.append((ITEMS[sku], LOCATIONS[location], q["quantity"]))
    save()
    for item, location, quantity in changed:  # Shopify's payload: no word on which app did it
        emit(
            "inventory_levels/update",
            {
                "inventory_item_id": item,
                "location_id": location,
                "available": quantity,
                "updated_at": now(),
                "admin_graphql_api_id": f"gid://shopify/InventoryLevel/{location}?inventory_item_id={item}",
            },
        )
    return {"inventorySetQuantities": {"userErrors": []}}


def update_product(app, variables, query):
    product_id = num(variables["product"]["id"])
    title = next(t for t, n in PRODUCTS.items() if n == product_id)
    gid = f"gid://shopify/Product/{product_id}"
    EVENTS.setdefault(gid, []).insert(0, {"appTitle": app, "attributeToApp": True, "attributeToUser": False, "action": "update", "createdAt": now()})
    save()
    emit(
        "products/update",
        {
            "id": product_id,
            "admin_graphql_api_id": gid,
            "title": title,
            "handle": title.lower().replace(" ", "-"),
            "status": "active",
            "updated_at": now(),
            "variants": variants_of(title),
        },
    )
    return {"productUpdate": {"userErrors": []}}


def order_app(app, variables, query):
    name = ORDER_APPS.get(variables["id"])
    return {"order": {"app": {"name": name}} if name else None}


def product_events(app, variables, query):
    return {"product": {"events": {"nodes": EVENTS.get(variables["id"], [])}}}


def order_events(app, variables, query):
    return {"order": {"events": {"nodes": EVENTS.get(variables["id"], [])}}}


def inventory_level(app, variables, query):
    location, item = re.fullmatch(r"gid://shopify/InventoryLevel/(\d+)\?inventory_item_id=(\d+)", variables["id"]).groups()
    return {"inventoryLevel": {"item": {"sku": SKU_OF[int(item)]}, "location": {"name": LOCATION_OF[int(location)]}}}


OPERATIONS = {
    "Catalog": catalog_query,
    "SetStock": set_stock,
    "UpdateProduct": update_product,
    "OrderApp": order_app,
    "OrderEvents": order_events,
    "ProductEvents": product_events,
    "InventoryLevel": inventory_level,
}


def checkout(skus: list[str]) -> dict:
    """A customer orders on the Online Store: the order's app is the Online Store itself."""
    order_id = NEXT["order"]
    NEXT["order"] += 1
    gid = f"gid://shopify/Order/{order_id}"
    ORDER_APPS[gid] = "Online Store"
    save()
    first, last, email = random.choice(CUSTOMERS)
    lines = [{"sku": sku, "quantity": 1, "price": "120.00", "name": sku} for sku in skus]
    emit(
        "orders/create",
        {
            "id": order_id,
            "admin_graphql_api_id": gid,
            "name": f"#{order_id}",
            "created_at": now(),
            "updated_at": now(),
            "app_id": 580111,
            "source_name": "web",
            "currency": "EUR",
            "total_price": f"{120 * len(skus)}.00",
            "financial_status": "paid",
            "email": email,
            "customer": {"first_name": first, "last_name": last, "email": email},
            "shipping_address": {"first_name": first, "last_name": last, "address1": "Carrer de Mallorca 1", "city": "Barcelona", "zip": "08013", "phone": "+34 600 000 000"},
            "line_items": lines,
        },
    )
    return {"order": gid}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"ok": True})
        self.reply(404, {"errors": "Not Found"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/admin/oauth/access_token":
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            client_id = form.get("client_id", "")
            if form.get("grant_type") != "client_credentials" or not hmac.compare_digest(APPS.get(client_id, ""), form.get("client_secret", "")) or client_id not in APPS:
                return self.reply(400, {"error": "invalid_client"})
            token = secrets.token_hex(16)
            with lock:
                TOKENS[token] = client_id
                save()
            return self.reply(200, {"access_token": token, "scope": "fake", "expires_in": 86399})
        if self.path == "/_sim/checkout":
            with lock:
                return self.reply(200, checkout(json.loads(body)["skus"]))
        if not re.fullmatch(r"/admin/api/\d{4}-\d{2}/graphql\.json", self.path):
            return self.reply(404, {"errors": "Not Found"})
        app = TOKENS.get(self.headers.get("X-Shopify-Access-Token", ""))
        if not app:
            return self.reply(401, {"errors": "[API] Invalid API key or access token"})
        request = json.loads(body)
        match = re.search(r"\b(?:query|mutation)\s+(\w+)", request["query"])
        operation = OPERATIONS.get(match.group(1) if match else "")
        if not operation:
            return self.reply(200, {"errors": [{"message": "The fake Shopify does not know this operation."}]})
        if app not in MUTATIONS.get(match.group(1), {app}):
            return self.reply(200, {"errors": [{"message": "Access denied for this field.", "extensions": {"code": "ACCESS_DENIED"}}]})
        try:
            with lock:
                return self.reply(200, {"data": operation(app, request.get("variables") or {}, request["query"])})
        except (ValueError, KeyError, StopIteration, AttributeError) as e:
            return self.reply(200, {"errors": [{"message": str(e)}]})


if __name__ == "__main__":
    load()
    print(f"Fake Shopify on :8080, webhooks to {WEBHOOK_URL}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
