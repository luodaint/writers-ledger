"""Play out a few simulated days of normal traffic from every writer in writers.yaml.

Odoo writes go through the JSON-2 API with each writer's own key, so Odoo records
who did what. Shopify writes go through the Admin API as each writer's own app:
the fake Shopify offline, your dev store live. Days are compressed into seconds.
"""
import json
import os
import random

import httpx

from sim import catalog, shopify

DAYS = int(os.environ.get("TRAFFIC_DAYS", "3"))
KEYS_FILE = os.environ.get("ODOO_KEYS_FILE", "/secrets/odoo-keys.json")
rng = random.Random(int(os.environ.get("TRAFFIC_SEED", "7")))
# Made-up customers, so the redaction in front of the LLM has something to remove.
CUSTOMERS = [("Laura Pérez", "laura.perez@example.com"), ("Marco Rossi", "marco.rossi@example.com"),
             ("Ana Vidal", "ana.vidal@example.com"), ("Giulia Bianchi", "giulia.b@example.com")]


class Odoo:
    """JSON-2 calls signed with one writer's API key."""

    def __init__(self, role: str):
        key = json.load(open(KEYS_FILE))[role]["key"]
        self.http = httpx.Client(
            base_url=os.environ["ODOO_URL"], timeout=30,
            headers={"Authorization": f"bearer {key}", "X-Odoo-Database": os.environ.get("ODOO_DB", "writers")},
        )

    def __call__(self, model: str, method: str, **params):
        response = self.http.post(f"/json/2/{model}/{method}", json=params)
        if response.is_error:
            raise RuntimeError(f"Odoo {model}.{method}: {response.json().get('message', response.text)[:300]}")
        return response.json()


def ids_by(odoo: Odoo, model: str, field: str) -> dict:
    return {r[field]: r["id"] for r in odoo(model, "search_read", domain=[], fields=[field])}


def sync_order(odoo: Odoo, products: dict, skus: list[str]) -> None:
    """shopify-odoo-sync: a Shopify order arrives, so it creates the customer and the sale order."""
    name, email = rng.choice(CUSTOMERS)
    [partner] = odoo("res.partner", "create", vals_list=[{"name": name, "email": email}])
    lines = [[0, 0, {"product_id": products[sku], "product_uom_qty": rng.randint(1, 2)}] for sku in skus]
    odoo("sale.order", "create", vals_list=[{"partner_id": partner, "order_line": lines}])


def adjust_stock(odoo: Odoo, products: dict, locations: dict, sku: str, location: str) -> int:
    """stock-cron: counts stock in Odoo; returns the new quantity to push to Shopify."""
    domain = [["product_id", "=", products[sku]], ["location_id", "=", locations[location]]]
    [quant] = odoo("stock.quant", "search_read", domain=domain, fields=["quantity"])
    quantity = max(0, int(quant["quantity"]) + rng.choice([-2, -1, 1, 2, 3]))
    context = {"inventory_mode": True}
    odoo("stock.quant", "write", ids=[quant["id"]], vals={"inventory_quantity": quantity}, context=context)
    odoo("stock.quant", "action_apply_inventory", ids=[quant["id"]], context=context)
    return quantity


def main() -> None:
    sync, stock = Odoo("sync"), Odoo("stock")
    products = ids_by(stock, "product.product", "default_code")
    locations = {name: id for name, id in ids_by(stock, "stock.location", "name").items() if name in catalog.LOCATIONS}
    shop = shopify.connect()
    skus = [v[0] for v in catalog.variants()]
    counts = {"orders": 0, "stock": 0, "products": 0}
    for day in range(1, DAYS + 1):
        for _ in range(4):
            order = rng.sample(skus, rng.randint(1, 2))
            shop.checkout(order)
            sync_order(sync, products, order)
            counts["orders"] += 1
        for _ in range(3):
            sku, location = rng.choice(skus), rng.choice(catalog.LOCATIONS)
            shop.set_stock(sku, location, adjust_stock(stock, products, locations, sku, location))
            counts["stock"] += 1
        shop.update_product(rng.choice(catalog.SHOES), {"drop": f"day-{day}"})
        counts["products"] += 1
        print(f"day {day}: {counts}")
    print(f"Traffic done: {DAYS} simulated days, {counts}.")


if __name__ == "__main__":
    main()
