"""End-to-end check of the offline demo, run inside the app container after
`make up seed traffic`: every writer shows up in the ledger, no customer data got in,
the four planted incidents are open findings and normal traffic caused none.

Waits for the collectors and the drift settle window. CI runs it as `make smoke`.
"""
import json
import os
import sys
import time

from app import ledger
from sim.traffic import CUSTOMERS, DRIFT

EXPECTED = {  # (system, model, identity) that the traffic must leave in the ledger
    ("odoo", "sale.order", "sync@alohas.test"),
    ("odoo", "stock.quant", "stock@alohas.test"),
    ("odoo", "stock.quant", "admin"),
    ("odoo", "product.product", "sync@alohas.test"),
    ("shopify", "order", "Online Store"),
    ("shopify", "product", "pricing-tool"),
    ("shopify", "inventory_level", "unattributed"),
}
PLANTED = {  # fingerprints of the four incidents in sim/traffic.py
    "unknown_writer|odoo|stock.quant|admin",
    "out_of_scope|odoo|product.product|sync@alohas.test",
    "ownerless_writer|shopify|product|pricing-tool",
    f"stock_drift|{DRIFT[0]}|{DRIFT[1]}",
}
PERSONAL = [part for customer in CUSTOMERS for part in customer] + ["@"]  # names, emails, any address


def seen(conn) -> set:
    return {tuple(r) for r in conn.execute("SELECT DISTINCT system, model, identity FROM writes")}


def problems(conn) -> set:
    """Fingerprints of the open findings, leaving out the informational unattributed writes."""
    return {f["fingerprint"] for f in ledger.list_findings(conn) if f["status"] == "open" and f["severity"] != "info"}


def wait(missing, what: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while gaps := missing():
        if time.monotonic() > deadline:
            sys.exit(f"{what} after {timeout}s: {sorted(gaps)}")
        time.sleep(5)


def main(timeout: int = 240) -> None:
    conn = ledger.connect(os.environ.get("LEDGER_DB", "/data/ledger.db"))
    wait(lambda: EXPECTED - seen(conn), "Not in the ledger", timeout)
    for system, model, identity, n in conn.execute(
            "SELECT system, model, identity, count(*) FROM writes GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"):
        print(f"{system:8} {model:16} {identity:22} {n:4} writes")
    payloads = [json.dumps(json.loads(p), ensure_ascii=False) for (p,) in conn.execute("SELECT payload FROM writes")]
    if leaks := sum(any(part in p for part in PERSONAL) for p in payloads):
        sys.exit(f"Customer data in {leaks} ledger payloads")
    wait(lambda: PLANTED - problems(conn), "Planted incidents not found", timeout)
    if extra := problems(conn) - PLANTED:
        sys.exit(f"Findings that normal traffic should not cause: {sorted(extra)}")
    for f in ledger.list_findings(conn):
        print(f"{f['status']:8} {f['severity']:6} {f['summary']}")
    print(f"Smoke test passed: {len(payloads)} writes, every writer seen, no customer data, "
          "the four planted incidents found and nothing else.")


if __name__ == "__main__":
    main()
