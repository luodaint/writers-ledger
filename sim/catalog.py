"""The demo catalogue, shared by the Odoo seed, the fake Shopify and the traffic."""

SHOES = [
    "Rife Sneaker",
    "Tide Sneaker",
    "Mary Jane Flat",
    "Lunar Loafer",
    "Ballet Flat",
    "Chelsea Boot",
    "Western Boot",
    "Slingback",
    "Riviera Mule",
    "Lara Sandal",
    "Derby",
    "Clog",
    "Ankle Boot",
    "Platform Loafer",
    "Kitten Heel",
]
SIZES = [36, 37, 38, 39, 40, 41]
LOCATIONS = ["Barcelona", "Milan", "Online warehouse"]


def sku(shoe: int, size: int) -> str:
    return f"AL{shoe + 1:02d}-{size}"


def variants() -> list[tuple[str, str, int]]:
    """(sku, shoe name, size) for every variant."""
    return [(sku(n, size), name, size) for n, name in enumerate(SHOES) for size in SIZES]


def initial_stock(sku: str, location: str) -> int:
    """Starting quantity, the same in Odoo and Shopify so nothing drifts at the start."""
    return sum(map(ord, sku + location)) % 12 + 2
