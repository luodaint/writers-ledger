"""The Shopify Admin API as each simulated writer's own app.

The same requests go to the fake Shopify offline and to a real dev store live.
"""
import os
import uuid

import httpx

API_VERSION = "2026-10"
CATALOG = """query Catalog {
  locations(first: 50) { nodes { id name } }
  productVariants(first: 250) { nodes { sku inventoryItem { id } product { id title } } }
}"""
SET_STOCK = """mutation SetStock($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) { userErrors { code field message } }
}"""
UPDATE_PRODUCT = """mutation UpdateProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) { userErrors { field message } }
}"""


class App:
    """One Shopify app, signed in with the client credentials grant."""

    def __init__(self, base: str, client_id: str, client_secret: str):
        response = httpx.post(f"{base}/admin/oauth/access_token", timeout=30, data={
            "client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
        response.raise_for_status()
        self.http = httpx.Client(base_url=base, timeout=30,
                                 headers={"X-Shopify-Access-Token": response.json()["access_token"]})

    def graphql(self, query: str, **variables) -> dict:
        response = self.http.post(f"/admin/api/{API_VERSION}/graphql.json",
                                  json={"query": query, "variables": variables})
        response.raise_for_status()
        body = response.json()
        results = (body.get("data") or {}).values()
        errors = body.get("errors") or [e for r in results if isinstance(r, dict) for e in r.get("userErrors", [])]
        if errors:
            raise RuntimeError(f"Shopify said: {errors}")
        return body["data"]


class Shop:
    """What the simulated writers do in Shopify."""

    def __init__(self):
        store = os.environ["SHOPIFY_STORE"]
        self.base = store if "://" in store else f"https://{store}"
        self.stock_sync = App(self.base, os.environ["STOCK_SYNC_CLIENT_ID"], os.environ["STOCK_SYNC_CLIENT_SECRET"])
        self.pricing_tool = App(self.base, os.environ["PRICING_TOOL_CLIENT_ID"],
                                os.environ["PRICING_TOOL_CLIENT_SECRET"])
        found = self.stock_sync.graphql(CATALOG)
        self.locations = {node["name"]: node["id"] for node in found["locations"]["nodes"]}
        variants = found["productVariants"]["nodes"]
        self.items = {v["sku"]: v["inventoryItem"]["id"] for v in variants}
        self.products = {v["product"]["title"]: v["product"]["id"] for v in variants}

    def checkout(self, skus: list[str]) -> None:
        """A customer buys on the Online Store. Offline only: the fake Shopify plays the shop."""
        httpx.post(f"{self.base}/_sim/checkout", json={"skus": skus}, timeout=30).raise_for_status()

    def set_stock(self, sku: str, location: str, quantity: int) -> None:
        """stock-sync pushes an Odoo stock level to Shopify."""
        self.stock_sync.graphql(SET_STOCK, key=str(uuid.uuid4()), input={
            "name": "available", "reason": "correction", "referenceDocumentUri": "app://stock-sync/odoo",
            "quantities": [{"inventoryItemId": self.items[sku], "locationId": self.locations[location],
                            "quantity": quantity, "changeFromQuantity": None}],
        })

    def update_product(self, title: str, metafields: dict) -> None:
        """pricing-tool sets drop metafields on a product."""
        self.pricing_tool.graphql(UPDATE_PRODUCT, product={
            "id": self.products[title],
            "metafields": [{"namespace": "drops", "key": k, "type": "single_line_text_field", "value": v}
                           for k, v in metafields.items()],
        })


def connect() -> Shop:
    return Shop()
