"""Play out a few simulated days of normal traffic from every writer in writers.yaml,
plus the four planted incidents the ledger must find:

1. The forgotten cron: a script with the admin key resets Milan stock every night.
2. Scope creep: shopify-odoo-sync starts copying Shopify prices into Odoo products.
3. Ownerless: pmo-pricing-script updates products, and nobody owns it.
4. Drift: one stock push to Barcelona sends the wrong number.

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
DRIFT = ("AL05-38", "Barcelona")  # incident 4: the SKU and shop whose stock push goes wrong
# Made-up customers, so the redaction in front of the LLM has something to remove.
CUSTOMERS = [
    ("Laura Pérez", "laura.perez@example.com"),
    ("Marco Rossi", "marco.rossi@example.com"),
    ("Ana Vidal", "ana.vidal@example.com"),
    ("Giulia Bianchi", "giulia.b@example.com"),
]


class Odoo:
    """JSON-2 calls signed with one writer's API key."""

    def __init__(self, role: str):
        with open(KEYS_FILE) as f:
            key = json.load(f)[role]["key"]
        self.http = httpx.Client(
            base_url=os.environ["ODOO_URL"],
            timeout=30,
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
    where = {"product_id": products[sku], "location_id": locations[location]}
    quants = odoo("stock.quant", "search_read", domain=[[k, "=", v] for k, v in where.items()], fields=["quantity"])
    quantity = max(0, int(quants[0]["quantity"] if quants else 0) + rng.choice([-2, -1, 1, 2, 3]))
    context = {"inventory_mode": True}
    if quants:  # write, not create: Odoo's create upsert sets the count by assignment, which skips a 0
        ids = [quants[0]["id"]]
        odoo("stock.quant", "write", ids=ids, vals={"inventory_quantity": quantity}, context=context)
    else:  # Odoo's scheduler deletes a quant that reached zero; counting stock there makes a new one
        ids = odoo("stock.quant", "create", vals_list=[where | {"inventory_quantity": quantity}], context=context)
    odoo("stock.quant", "action_apply_inventory", ids=ids, context=context)
    return quantity


def main() -> None:
    sync, stock, admin = Odoo("sync"), Odoo("stock"), Odoo("admin")
    products = ids_by(stock, "product.product", "default_code")
    locations = {name: id for name, id in ids_by(stock, "stock.location", "name").items() if name in catalog.LOCATIONS}
    shop = shopify.connect()
    skus = [v[0] for v in catalog.variants()]
    counts = {"orders": 0, "stock": 0, "products": 0, "forgotten cron": 0, "scope creep": 0}
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
        shop.update_product(rng.choice(catalog.SHOES), {"drop": f"day-{day}"})  # incident 3: no owner
        counts["products"] += 1
        for sku in rng.sample(skus, 4):  # incident 1, overnight; stock-cron then pushes what Odoo has
            shop.set_stock(sku, "Milan", adjust_stock(admin, products, locations, sku, "Milan"))
            counts["forgotten cron"] += 1
        sku = rng.choice(skus)  # incident 2: the sync writes a product price, outside its scope
        sync("product.product", "write", ids=[products[sku]], vals={"lst_price": float(rng.randint(100, 260))})
        counts["scope creep"] += 1
        print(f"day {day}: {counts}")
    shop.set_stock(*DRIFT, adjust_stock(stock, products, locations, *DRIFT) + 3)  # incident 4: Shopify gets 3 too many
    print(f"Traffic done: {DAYS} simulated days, {counts}, and drift on {DRIFT[0]} in {DRIFT[1]}.")


if __name__ == "__main__":
    main()
