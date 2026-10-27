"""Poll Odoo's External JSON-2 API for writes to the watched models.

Polling sees only the last writer of each record; see README for the audit-trail answer.
"""
import json
import logging
import os
from datetime import datetime, timedelta

import httpx

from app import ledger

log = logging.getLogger("collect_odoo")
URL = os.environ.get("ODOO_URL", "")
DB = os.environ.get("ODOO_DB", "writers")
KEYS_FILE = os.environ.get("ODOO_KEYS_FILE", "/secrets/odoo-key.json")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))
PAGE = 500
OVERLAP = 300  # seconds re-read every poll: write_date is stamped when a transaction starts
TIME = "%Y-%m-%d %H:%M:%S"

# Business fields kept per model. An allowlist, so customer fields are never fetched.
MODELS = {
    "sale.order": ["name", "state", "amount_total"],
    "stock.quant": ["product_id", "location_id", "quantity"],
    "stock.picking": ["name", "state", "origin"],
    "product.product": ["default_code", "lst_price", "active"],
}
# Odoo mirrors every stock adjustment in a virtual location; only real stock places count.
DOMAINS = {"stock.quant": [["location_id.usage", "=", "internal"]]}
# Many2one fields shown as a readable value instead of an id: field -> (model, attribute, label).
LOOKUPS = {
    "write_uid": ("res.users", "login", "identity"),
    "product_id": ("product.product", "default_code", "sku"),
    "location_id": ("stock.location", "name", "location"),
}


def client(url: str, db: str, key: str, **kwargs) -> httpx.Client:
    headers = {"Authorization": f"bearer {key}", "X-Odoo-Database": db, "User-Agent": "writers-ledger"}
    return httpx.Client(base_url=url, headers=headers, timeout=10, **kwargs)


def call(http: httpx.Client, model: str, method: str, **params):
    response = http.post(f"/json/2/{model}/{method}", json=params)
    response.raise_for_status()
    return response.json()


def lookup(http, cache: dict, field: str, value):
    """[id, name] -> login, SKU or location name, asking Odoo once per id.

    A record we may not read becomes 'model#id': the write is still recorded (as an
    unknown writer, if it's the author) and the collector never stalls on it."""
    if not value:
        return None
    model, attr, _ = LOOKUPS[field]
    if (model, value[0]) not in cache:
        try:
            rows = call(http, model, "read", ids=[value[0]], fields=[attr])
        except httpx.HTTPStatusError as e:
            log.warning("Can't read %s#%s (%s); recording it by id", model, value[0], e.response.status_code)
            rows = []
        cache[model, value[0]] = (rows[0][attr] if rows else None) or f"{model}#{value[0]}"
    return cache[model, value[0]]


def to_write(model: str, record: dict, resolve) -> dict:
    payload = {
        LOOKUPS[f][2] if f in LOOKUPS else f: resolve(f, record[f]) if f in LOOKUPS else record[f]
        for f in MODELS[model]
    }
    return {
        "system": "odoo", "model": model, "record_ref": str(record["id"]),
        "identity": ledger.identity(resolve("write_uid", record["write_uid"])),
        "action": "create" if record["create_date"] == record["write_date"] else "write",
        "written_at": ledger.utc(record["write_date"]), "payload": payload,
    }


def poll_model(http, conn, model: str, cache: dict) -> int:
    name = f"odoo:{model}"
    cursor = ledger.get_cursor(conn, name)
    if cursor is None:  # first poll: observe from the next second on, not the seed data
        latest = call(http, model, "search_read", domain=DOMAINS.get(model, []), fields=["write_date"], order="write_date desc", limit=1)
        start = datetime.fromisoformat(latest[0]["write_date"]) + timedelta(seconds=1) if latest else datetime(1970, 1, 1)
        ledger.set_cursor(conn, name, start.strftime(TIME))
        ledger.set_cursor(conn, name + ":since", start.strftime(TIME))
        return 0
    # Re-read an overlap: a long transaction can commit after we polled past its write_date.
    # Never before the first poll, though. The ledger's dedupe drops what it already has.
    overlap = (datetime.fromisoformat(cursor) - timedelta(seconds=OVERLAP)).strftime(TIME)
    since = max(overlap, ledger.get_cursor(conn, name + ":since") or overlap)
    # Read every page: one bulk write can stamp thousands of records with the same second.
    records = []
    while len(records) % PAGE == 0:
        page = call(http, model, "search_read", domain=[["write_date", ">=", since], *DOMAINS.get(model, [])],
                    fields=["write_uid", "write_date", "create_date", *MODELS[model]],
                    order="write_date, id", limit=PAGE, offset=len(records))
        records += page
        if not page:
            break
    new = sum(ledger.record_write(conn, to_write(model, r, lambda f, v: lookup(http, cache, f, v))) for r in records)
    if records:
        ledger.set_cursor(conn, name, records[-1]["write_date"])
    return new


def poll_all(http, conn, cache: dict) -> int:
    if writable := [m for m in MODELS if call(http, m, "has_access", operation="write")]:
        log.error("Refusing to poll: the collector's Odoo key can write to %s. It must be read-only.", writable)
        return 0
    new = 0
    for model in MODELS:  # one model failing (say, access revoked) must not blind the others
        try:
            new += poll_model(http, conn, model, cache)
        except httpx.HTTPStatusError as e:
            log.warning("Odoo poll of %s failed: %s", model, e.response.status_code)
    return new


def poll_once(conn, cache: dict, **kwargs) -> int:
    try:
        with open(KEYS_FILE) as f:
            key = json.load(f)["ledger"]["key"]
    except (OSError, ValueError, KeyError):
        log.info("No Odoo key for the collector yet: run `make seed`.")
        return 0
    try:
        with client(URL, DB, key, **kwargs) as http:
            return poll_all(http, conn, cache)
    except Exception as e:  # noqa: BLE001 - the ledger page must keep working while Odoo is down
        log.warning("Odoo poll failed: %s: %s", type(e).__name__, e)
        return 0
