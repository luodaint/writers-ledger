"""The rules: writes and the register in, findings out. Pure functions, no I/O, no AI.

A finding is deduplicated by its fingerprint (rule + system + model + identity, or rule +
SKU + location for drift), so 40 rogue writes are one finding with count 40.
"""

from app.register import UNATTRIBUTED, Writer, writer_for

SEVERITIES = ("critical", "high", "medium", "low", "info")  # most severe first
SEVERITY = {"unknown_writer": "high", "out_of_scope": "high", "ownerless_writer": "medium", "stock_drift": "high", "unattributed_write": "info"}
SUMMARY = {
    "unknown_writer": "{identity} changed {system}:{model} {times}, but writers.yaml doesn't list it.",
    "out_of_scope": "{writer_id} changed {system}:{model} {times}, which writers.yaml doesn't allow it to.",
    "ownerless_writer": "{writer_id} changed {system}:{model} {times}, and nobody owns it.",
    "stock_drift": "{sku} in {location}: Odoo has {odoo:g}, Shopify has {shopify:g}.",
    "unattributed_write": "{system}:{model} changed {times} without saying who did it.",
}
STOCK = {"stock.quant": ("odoo", "quantity"), "inventory_level": ("shopify", "available")}  # model -> side, field


def writer_rules(write: dict, writers: list[Writer]) -> list[tuple[str, Writer | None]]:
    """The rules one write breaks, each with the registered writer behind it."""
    if write["identity"] == UNATTRIBUTED:
        return [("unattributed_write", None)]
    writer = writer_for(writers, write["system"], write["identity"])
    if writer is None:
        return [("unknown_writer", None)]
    scope = f"{write['system']}:{write['model']}"
    return [(rule, writer) for rule, broken in [("out_of_scope", scope not in writer.may_write), ("ownerless_writer", not writer.owner)] if broken]


def latest_stock(writes: list[dict]) -> dict:
    """(sku, location) -> {"odoo" | "shopify": (quantity, written_at)}, the newest level on each side."""
    latest = {}
    for w in sorted((w for w in writes if w["model"] in STOCK), key=lambda w: w["written_at"]):
        side, field = STOCK[w["model"]]
        if (quantity := w["payload"].get(field)) is not None:
            latest.setdefault((w["payload"].get("sku"), w["payload"].get("location")), {})[side] = (float(quantity), w["written_at"])
    return latest


def finding(rule: str, system: str, model: str, identity: str, writer: Writer | None, at: str, **facts) -> dict:
    return {
        "rule": rule,
        "severity": SEVERITY[rule],
        "system": system,
        "model": model,
        "identity": identity,
        "writer_id": writer.id if writer else None,
        "owner": writer.owner if writer else "",
        "count": 0,
        "first_seen": at,
        "last_seen": at,
        "facts": facts,
    }


def check(writes: list[dict], writers: list[Writer], since: str, settled: str) -> list[dict]:
    """Writer rules count the writes since `since`. Drift compares the newest level on each
    side, however old, once both changed before `settled`: a sync in flight isn't drift."""
    found = {}
    for w in (w for w in writes if w["written_at"] >= since):
        for rule, writer in writer_rules(w, writers):
            f = found.setdefault("|".join((rule, w["system"], w["model"], w["identity"])), finding(rule, w["system"], w["model"], w["identity"], writer, w["written_at"]))
            f["count"] += 1
            f["first_seen"], f["last_seen"] = min(f["first_seen"], w["written_at"]), max(f["last_seen"], w["written_at"])
    sync = next((w for w in writers if "shopify:inventory_level" in w.may_write), None)
    for (sku, location), sides in latest_stock(writes).items():
        if len(sides) < 2:
            continue
        (odoo, odoo_at), (shopify, shopify_at) = sides["odoo"], sides["shopify"]
        if odoo != shopify and (at := max(odoo_at, shopify_at)) <= settled:
            found[f"stock_drift|{sku}|{location}"] = finding("stock_drift", "odoo", "stock.quant", "", sync, at, sku=sku, location=location, odoo=odoo, shopify=shopify) | {
                "count": 1
            }
    return [
        f | {"fingerprint": key, "summary": SUMMARY[f["rule"]].format(**f, **f["facts"], times="once" if f["count"] == 1 else f"{f['count']} times")}
        for key, f in found.items()
    ]


def rank(findings: list[dict]) -> list[dict]:
    """Open findings first, then the most severe; otherwise the order is kept."""
    return sorted(findings, key=lambda f: (f["status"] == "resolved", SEVERITIES.index(f["severity"])))


def raised(floor: str, severity: str) -> str:
    """The more severe of the two: a rule's severity is a floor that triage may raise, never lower."""
    return min(floor, severity, key=SEVERITIES.index)
