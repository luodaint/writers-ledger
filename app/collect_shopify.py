"""Shopify webhooks: verify the signature, drop redeliveries, find out who wrote, store.

Webhooks don't say which app made a change, so after answering Shopify we ask the
Admin API: orders name their app, products have an event log, inventory has neither.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import closing
from datetime import datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app import ledger

log = logging.getLogger("collect_shopify")
STORE = os.environ.get("SHOPIFY_STORE", "")
BASE = STORE if "://" in STORE else f"https://{STORE}"  # a full URL only for the offline fake
API_VERSION = "2026-10"
CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
MAX_BODY = 512 * 1024
EVENT_WINDOW = (-30, 5)  # seconds around a change in which an event counts as its cause
TOPICS = {  # topic -> (model, action)
    "orders/create": ("order", "create"),
    "orders/updated": ("order", "write"),
    "inventory_levels/update": ("inventory_level", "write"),
    "products/update": ("product", "write"),
}
FIELDS = {"order": ("name", "total_price", "currency", "financial_status"),
          "inventory_level": ("available",), "product": ("title", "status")}
EVENTS = "events(first: 10, sortKey: CREATED_AT, reverse: true) { nodes { appTitle attributeToApp createdAt } }"
QUERIES = {  # topic -> the Admin API question that tells us who wrote
    "orders/create": "query OrderApp($id: ID!) { order(id: $id) { app { name } } }",
    "orders/updated": "query OrderEvents($id: ID!) { order(id: $id) { " + EVENTS + " } }",
    "products/update": "query ProductEvents($id: ID!) { product(id: $id) { " + EVENTS + " } }",
    "inventory_levels/update": "query InventoryLevel($id: ID!) { inventoryLevel(id: $id) {"
                               " item { sku } location { name } } }",
}
http = httpx.Client(timeout=10)
_token = {"value": "", "expires": 0.0}
router = APIRouter()


def verify(body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest())
    return hmac.compare_digest(expected, signature.encode())


def to_write(topic: str, payload: dict, identity: str, details: dict) -> dict:
    """Keep only allowlisted fields: customer names, emails and addresses never get in."""
    model, action = TOPICS[topic]
    kept = details | {f: payload.get(f) for f in FIELDS[model]}
    if model == "order":
        kept["skus"] = [line.get("sku") for line in payload.get("line_items", [])]
    return {"system": "shopify", "model": model, "record_ref": payload["admin_graphql_api_id"],
            "identity": identity, "action": action, "written_at": ledger.utc(payload["updated_at"]),
            "payload": kept}


def offset(at: str, changed_at: str) -> float:
    try:
        return (datetime.fromisoformat(at) - datetime.fromisoformat(changed_at)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def author_of(events: list[dict], changed_at: str) -> str:
    """The app behind the event of this change: unattributed if no event, or more than one author, fits."""
    low, high = EVENT_WINDOW
    authors = {e.get("attributeToApp") and e.get("appTitle") or None
               for e in events if low <= offset(e.get("createdAt"), changed_at) <= high}
    return ledger.identity(authors.pop() if len(authors) == 1 else None)


def identity_from(topic: str, data: dict, changed_at: str) -> tuple[str, dict]:
    if topic == "orders/create":  # the order's app created it; later updates come from its events
        return ledger.identity(((data.get("order") or {}).get("app") or {}).get("name")), {}
    if topic == "inventory_levels/update":
        level = data.get("inventoryLevel") or {}
        return ledger.UNATTRIBUTED, {"sku": (level.get("item") or {}).get("sku"),
                                     "location": (level.get("location") or {}).get("name")}
    record = data.get("order") or data.get("product") or {}
    return author_of((record.get("events") or {}).get("nodes") or [], changed_at), {}


def token() -> str:
    """Client-credentials token; Shopify makes them last 24 hours, so refresh shortly before."""
    if time.time() > _token["expires"] - 300:
        response = http.post(f"{BASE}/admin/oauth/access_token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "client_credentials"})
        response.raise_for_status()
        body = response.json()
        _token.update(value=body["access_token"], expires=time.time() + body["expires_in"])
    return _token["value"]


def lookup(topic: str, gid: str, changed_at: str) -> tuple[str, dict]:
    response = http.post(f"{BASE}/admin/api/{API_VERSION}/graphql.json",
                         json={"query": QUERIES[topic], "variables": {"id": gid}},
                         headers={"X-Shopify-Access-Token": token()})
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return identity_from(topic, body["data"], changed_at)


def record(db_path, topic: str, payload: dict) -> None:
    try:
        identity, details = lookup(topic, payload["admin_graphql_api_id"], payload["updated_at"])
    except Exception as e:  # noqa: BLE001 - a write we can't attribute is still a write
        log.warning("Shopify lookup failed for %s, storing it as unattributed: %s: %s", topic, type(e).__name__, e)
        identity, details = ledger.UNATTRIBUTED, {}
    with closing(ledger.connect(db_path)) as conn:
        ledger.record_write(conn, to_write(topic, payload, identity, details))


@router.post("/webhooks/shopify")
async def webhook(request: Request, tasks: BackgroundTasks):
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise HTTPException(413, "Payload too large")
    topic = request.headers.get("x-shopify-topic", "")
    if not verify(bytes(body), request.headers.get("x-shopify-hmac-sha256", ""), WEBHOOK_SECRET):
        log.warning("Rejected a Shopify webhook with a bad signature (topic %r)", topic[:64])
        raise HTTPException(401, "Bad signature")
    if topic not in TOPICS:
        return {"ignored": topic}
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Body is not JSON") from None
    webhook_id = request.headers.get("x-shopify-webhook-id", "")
    with closing(ledger.connect(request.app.state.ledger_db)) as conn:
        first = ledger.first_delivery(conn, webhook_id) if webhook_id else True
    if first:  # the lookup runs after we answer: Shopify gives up after 5 seconds
        tasks.add_task(record, request.app.state.ledger_db, topic, payload)
    return {"ok": True}
