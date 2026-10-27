"""End-to-end check of the offline demo, run inside the app container after
`make up seed traffic`: every writer shows up in the ledger, and no customer data got in.

Waits for the collectors, so it can run right after the traffic. CI runs it as `make smoke`.
"""
import json
import os
import sys
import time

from app import ledger
from sim.traffic import CUSTOMERS

EXPECTED = {  # (system, model, identity) that normal traffic must leave in the ledger
    ("odoo", "sale.order", "sync@alohas.test"),
    ("odoo", "stock.quant", "stock@alohas.test"),
    ("shopify", "order", "Online Store"),
    ("shopify", "product", "pricing-tool"),
    ("shopify", "inventory_level", "unattributed"),
}
PERSONAL = [part for customer in CUSTOMERS for part in customer] + ["@"]  # names, emails, any address


def main(timeout: int = 120) -> None:
    conn = ledger.connect(os.environ.get("LEDGER_DB", "/data/ledger.db"))
    deadline = time.monotonic() + timeout
    while missing := EXPECTED - {tuple(r) for r in conn.execute("SELECT DISTINCT system, model, identity FROM writes")}:
        if time.monotonic() > deadline:
            sys.exit(f"Not in the ledger after {timeout}s: {sorted(missing)}")
        time.sleep(5)
    for system, model, identity, n in conn.execute(
            "SELECT system, model, identity, count(*) FROM writes GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"):
        print(f"{system:8} {model:16} {identity:22} {n:4} writes")
    payloads = [json.dumps(json.loads(p), ensure_ascii=False) for (p,) in conn.execute("SELECT payload FROM writes")]
    if leaks := sum(any(part in p for part in PERSONAL) for p in payloads):
        sys.exit(f"Customer data in {leaks} ledger payloads")
    print(f"Smoke test passed: {len(payloads)} writes, every writer seen, no customer data.")


if __name__ == "__main__":
    main()
