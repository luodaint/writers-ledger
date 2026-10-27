import dataclasses

from app import rules
from app.register import parse_register

SINCE, SETTLED = "2026-10-20T00:00:00Z", "2026-10-27T09:50:00Z"  # window start; drift must be older than this
REGISTER = parse_register("""
- id: shopify-odoo-sync
  owner: backend
  purpose: Creates sale orders in Odoo from Shopify orders
  identity: { odoo_user: sync@alohas.test }
  may_write: [odoo:sale.order]
- id: stock-cron
  owner: sysadmin
  purpose: Pushes Odoo stock levels to Shopify locations
  identity: { odoo_user: stock@alohas.test, shopify_app: stock-sync }
  may_write: [odoo:stock.quant, shopify:inventory_level]
- id: shopify-storefront
  owner: ecommerce
  purpose: Customers buying on the online store
  identity: { shopify_app: Online Store }
  may_write: [shopify:order]
- id: pmo-pricing-script
  owner: pmo
  purpose: Bulk-updates product metafields for drops
  identity: { shopify_app: pricing-tool }
  may_write: [shopify:product]
""")


def write(identity="stock@alohas.test", system="odoo", model="stock.quant", at="2026-10-26T10:00:00Z", **payload):
    return {"system": system, "model": model, "record_ref": "7", "identity": identity, "action": "write",
            "written_at": at, "payload": payload or {"sku": "AL01-36", "location": "Milan", "quantity": 4.0}}


def level(available, sku="AL01-36", location="Milan", at="2026-10-26T10:00:00Z"):
    return write("unattributed", "shopify", "inventory_level", at, sku=sku, location=location, available=available)


def quant(quantity, sku="AL01-36", location="Milan", at="2026-10-26T10:00:00Z", identity="stock@alohas.test"):
    return write(identity, "odoo", "stock.quant", at, sku=sku, location=location, quantity=quantity)


def check(writes, register=REGISTER):
    return rules.check(writes, register, SINCE, SETTLED)


def by_rule(findings, rule):
    return [f for f in findings if f["rule"] == rule]


def problems(findings):
    """Everything except the informational unattributed_write."""
    return [f for f in findings if f["severity"] != "info"]


# --- benign traffic ----------------------------------------------------------------

def test_benign_traffic_produces_no_findings():
    benign = [
        write("sync@alohas.test", model="sale.order", name="S00001"),
        quant(7.0), level(7),
        write("Online Store", "shopify", "order", name="#1001"),
        write("pricing-tool", "shopify", "product", title="Rife Sneaker"),
    ]
    assert problems(check(benign)) == []


# --- unknown_writer ------------------------------------------------------------------

def test_unknown_writer_is_a_high_finding():
    [finding] = problems(check([quant(5.0, identity="admin"), level(5)]))  # Shopify followed: no drift
    assert finding["rule"] == "unknown_writer"
    assert finding["severity"] == "high"
    assert (finding["system"], finding["model"], finding["identity"]) == ("odoo", "stock.quant", "admin")
    assert finding["writer_id"] is None
    assert "admin" in finding["summary"]


def test_a_registered_identity_is_not_unknown():
    assert by_rule(check([quant(5.0)]), "unknown_writer") == []


def test_unattributed_is_informational_not_unknown():
    [finding] = check([level(5)])
    assert (finding["rule"], finding["severity"]) == ("unattributed_write", "info")


def test_a_writer_named_like_the_sentinel_is_unknown():
    [finding] = check([write('"unattributed"', "shopify", "product", title="x")])
    assert finding["rule"] == "unknown_writer"


# --- out_of_scope ----------------------------------------------------------------------

def test_writing_outside_may_write_is_out_of_scope():
    [finding] = check([write("sync@alohas.test", model="product.product", default_code="AL01-36", lst_price=99.0)])
    assert (finding["rule"], finding["severity"]) == ("out_of_scope", "high")
    assert (finding["writer_id"], finding["owner"]) == ("shopify-odoo-sync", "backend")
    assert "odoo:product.product" in finding["summary"]


def test_writing_inside_may_write_is_not_out_of_scope():
    assert check([write("sync@alohas.test", model="sale.order", name="S00001")]) == []


# --- ownerless_writer ----------------------------------------------------------------

OWNERLESS = [dataclasses.replace(REGISTER[3], owner="")]  # pmo-pricing-script, as in the demo


def test_an_ownerless_writer_that_writes_is_a_medium_finding():
    [finding] = check([write("pricing-tool", "shopify", "product", title="Rife Sneaker")], OWNERLESS)
    assert (finding["rule"], finding["severity"]) == ("ownerless_writer", "medium")
    assert (finding["writer_id"], finding["owner"]) == ("pmo-pricing-script", "")


def test_an_ownerless_writer_that_did_not_write_in_the_window_is_not_a_finding():
    old = write("pricing-tool", "shopify", "product", at="2026-10-01T10:00:00Z", title="Rife Sneaker")
    assert check([old], OWNERLESS) == []


def test_a_writer_with_an_owner_is_not_ownerless():
    assert check([write("pricing-tool", "shopify", "product", title="Rife Sneaker")]) == []


# --- stock_drift -----------------------------------------------------------------------

def test_stock_that_differs_after_the_settle_window_is_drift():
    [finding] = by_rule(check([quant(7.0, "AL05-38", "Barcelona"), level(10, "AL05-38", "Barcelona")]), "stock_drift")
    assert (finding["rule"], finding["severity"]) == ("stock_drift", "high")
    assert finding["facts"] == {"sku": "AL05-38", "location": "Barcelona", "odoo": 7.0, "shopify": 10.0}
    assert finding["writer_id"] == "stock-cron"  # the writer that keeps the two in step
    assert "AL05-38" in finding["summary"] and "Barcelona" in finding["summary"]


def test_equal_stock_is_not_drift_even_as_float_and_int():
    assert by_rule(check([quant(7.0), level(7)]), "stock_drift") == []


def test_a_difference_inside_the_settle_window_is_not_drift_yet():
    recent = "2026-10-27T09:55:00Z"
    assert by_rule(check([quant(7.0, at=recent), level(10)]), "stock_drift") == []


def test_stock_seen_on_one_side_only_is_not_drift():
    assert by_rule(check([quant(7.0)]), "stock_drift") == []


def test_drift_compares_the_newest_level_on_each_side():
    writes = [quant(7.0), level(10), level(7, at="2026-10-26T10:05:00Z")]
    assert by_rule(check(writes), "stock_drift") == []


def test_drift_uses_the_newest_level_whatever_order_the_writes_come_in():
    newest_first = [level(7, at="2026-10-26T10:05:00Z"), quant(7.0), level(10)]  # how the ledger returns them
    assert by_rule(check(newest_first), "stock_drift") == []


def test_drift_is_checked_however_old_the_levels_are():
    old = "2026-10-01T10:00:00Z"
    assert len(by_rule(check([quant(7.0, at=old), level(10, at=old)]), "stock_drift")) == 1


# --- deduplication by fingerprint --------------------------------------------------------

def test_forty_rogue_writes_make_one_finding_with_count_forty():
    writes = [quant(5.0, identity="admin", at=f"2026-10-26T10:{n:02d}:00Z") for n in range(40)]
    [finding] = problems(check(writes + [level(5, at="2026-10-26T10:45:00Z")]))
    assert finding["count"] == 40
    assert (finding["first_seen"], finding["last_seen"]) == ("2026-10-26T10:00:00Z", "2026-10-26T10:39:00Z")
    assert finding["fingerprint"] == "unknown_writer|odoo|stock.quant|admin"


def test_different_identities_or_models_are_different_findings():
    writes = [write("admin", model="sale.order", name="S1"), write("admin", model="product.product", lst_price=1.0),
              write("root", model="sale.order", name="S2")]
    assert len({f["fingerprint"] for f in check(writes)}) == 3


def test_writes_before_the_window_are_not_counted():
    writes = [write("admin", model="sale.order", at="2026-10-01T10:00:00Z", name="S1"),
              write("admin", model="sale.order", name="S2")]
    [finding] = check(writes)
    assert (finding["count"], finding["first_seen"]) == (1, "2026-10-26T10:00:00Z")


# --- ranking ---------------------------------------------------------------------------

def test_open_findings_come_first_then_the_most_severe():
    findings = [{"fingerprint": f, "status": s, "severity": v} for f, s, v in [
        ("a", "open", "medium"), ("b", "resolved", "critical"), ("c", "open", "info"), ("d", "open", "high"),
        ("e", "open", "high")]]
    assert [f["fingerprint"] for f in rules.rank(findings)] == ["d", "e", "a", "c", "b"]  # ties keep their order


def test_the_rule_severity_is_a_floor_others_may_only_raise():
    assert rules.raised("high", "critical") == "critical"
    assert rules.raised("high", "low") == "high"
    assert rules.raised("medium", "medium") == "medium"
