import pytest

from app import ledger


@pytest.fixture
def db(tmp_path):
    return ledger.connect(tmp_path / "ledger.db")


def write(**over):
    base = {
        "system": "odoo",
        "model": "stock.quant",
        "record_ref": "7",
        "identity": "stock@alohas.test",
        "action": "write",
        "written_at": "2026-10-26T10:00:00Z",
        "payload": {"sku": "AL01-36", "location": "Milan", "quantity": 4},
    }
    return base | over


def test_recorded_write_comes_back(db):
    assert ledger.record_write(db, write()) is True
    [row] = ledger.latest_writes(db)
    assert row["identity"] == "stock@alohas.test"
    assert row["payload"] == {"sku": "AL01-36", "location": "Milan", "quantity": 4}
    assert row["seen_at"]


def test_same_write_twice_is_stored_once(db):
    assert ledger.record_write(db, write()) is True
    assert ledger.record_write(db, write()) is False
    assert len(ledger.latest_writes(db)) == 1


def test_two_writers_on_the_same_record_in_the_same_second_are_both_kept(db):
    assert ledger.record_write(db, write(identity="stock@alohas.test")) is True
    assert ledger.record_write(db, write(identity="admin")) is True
    assert {r["identity"] for r in ledger.latest_writes(db)} == {"stock@alohas.test", "admin"}


def test_latest_writes_are_newest_first_and_limited(db):
    for minute in range(5):
        ledger.record_write(db, write(record_ref=str(minute), written_at=f"2026-10-26T10:0{minute}:00Z"))
    rows = ledger.latest_writes(db, limit=3)
    assert [r["record_ref"] for r in rows] == ["4", "3", "2"]


def test_filters_match_exactly(db):
    ledger.record_write(db, write())
    ledger.record_write(db, write(system="shopify", model="product", record_ref="9", identity="pricing-tool"))
    assert [r["system"] for r in ledger.latest_writes(db, system="shopify")] == ["shopify"]
    assert [r["model"] for r in ledger.latest_writes(db, model="stock.quant")] == ["stock.quant"]
    assert ledger.latest_writes(db, identity="pricing") == []


def test_filter_values_are_never_sql(db):
    ledger.record_write(db, write())
    assert ledger.latest_writes(db, identity="x' OR '1'='1") == []
    assert len(ledger.latest_writes(db)) == 1


def test_unknown_filter_is_rejected(db):
    with pytest.raises(ValueError):
        ledger.latest_writes(db, payload="anything")


def test_filter_options_list_distinct_values(db):
    ledger.record_write(db, write())
    ledger.record_write(db, write(record_ref="8"))
    ledger.record_write(db, write(system="shopify", model="product", record_ref="9", identity="pricing-tool"))
    assert ledger.filter_options(db) == {
        "system": ["odoo", "shopify"],
        "model": ["product", "stock.quant"],
        "identity": ["pricing-tool", "stock@alohas.test"],
    }


def test_activity_counts_writes_per_identity(db):
    ledger.record_write(db, write(written_at="2026-10-26T10:00:00Z"))
    ledger.record_write(db, write(record_ref="8", written_at="2026-10-26T11:00:00Z"))
    assert ledger.activity(db) == {
        ("odoo", "stock@alohas.test"): {"writes": 2, "last_seen": "2026-10-26T11:00:00Z"}
    }


def test_cursor_round_trip(db):
    assert ledger.get_cursor(db, "odoo:stock.quant") is None
    ledger.set_cursor(db, "odoo:stock.quant", "2026-10-26 10:00:00")
    ledger.set_cursor(db, "odoo:stock.quant", "2026-10-26 11:00:00")
    assert ledger.get_cursor(db, "odoo:stock.quant") == "2026-10-26 11:00:00"


def test_webhook_delivery_is_first_only_once(db):
    assert ledger.first_delivery(db, "b54557e4-bdd9") is True
    assert ledger.first_delivery(db, "b54557e4-bdd9") is False


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-26 10:00:00", "2026-10-26T10:00:00Z"),
        ("2026-10-26T12:00:00+02:00", "2026-10-26T10:00:00Z"),
        ("2026-10-26T10:00:00Z", "2026-10-26T10:00:00Z"),
        ("2026-10-26T10:00:00.708732Z", "2026-10-26T10:00:00Z"),
    ],
)
def test_timestamps_are_normalised_to_utc(value, expected):
    assert ledger.utc(value) == expected


@pytest.mark.parametrize("name, stored", [
    ("stock@alohas.test", "stock@alohas.test"), (None, "unattributed"), ("", "unattributed"),
    ("unattributed", '"unattributed"'),
])
def test_identity_keeps_the_unattributed_sentinel_for_nobody(name, stored):
    assert ledger.identity(name) == stored


# --- findings and checks -------------------------------------------------------------

def found(**over):
    base = {"fingerprint": "unknown_writer|odoo|stock.quant|admin", "rule": "unknown_writer", "severity": "high",
            "system": "odoo", "model": "stock.quant", "identity": "admin", "writer_id": None, "owner": "",
            "summary": "admin changed odoo:stock.quant 3 times, but writers.yaml doesn't list it.",
            "facts": {}, "count": 3, "first_seen": "2026-10-26T10:00:00Z", "last_seen": "2026-10-26T11:00:00Z"}
    return base | over


def test_a_check_stores_its_findings_as_open(db):
    assert ledger.get_cursor(db, "check:last") is None
    ledger.save_check(db, [found(facts={"sku": "AL01-36"})], "2026-10-27T10:00:00Z")
    [finding] = ledger.list_findings(db)
    assert (finding["status"], finding["count"], finding["facts"]) == ("open", 3, {"sku": "AL01-36"})
    assert ledger.get_cursor(db, "check:last") == "2026-10-27T10:00:00Z"


def test_repeating_a_check_does_not_inflate_the_count(db):
    for minute in range(3):
        ledger.save_check(db, [found()], f"2026-10-27T10:0{minute}:00Z")
    [finding] = ledger.list_findings(db)
    assert finding["count"] == 3


def test_count_follows_the_latest_check_and_first_seen_keeps_the_earliest(db):
    ledger.save_check(db, [found()], "2026-10-27T10:00:00Z")
    later = found(count=5, first_seen="2026-10-26T10:30:00Z", last_seen="2026-10-26T12:00:00Z")
    ledger.save_check(db, [later], "2026-10-27T10:05:00Z")
    [finding] = ledger.list_findings(db)
    assert (finding["count"], finding["first_seen"], finding["last_seen"]) == (
        5, "2026-10-26T10:00:00Z", "2026-10-26T12:00:00Z")


def test_a_finding_the_next_check_does_not_find_is_resolved_and_can_reopen(db):
    ledger.save_check(db, [found()], "2026-10-27T10:00:00Z")
    ledger.save_check(db, [], "2026-10-27T10:05:00Z")
    [finding] = ledger.list_findings(db)
    assert (finding["status"], finding["resolved_at"]) == ("resolved", "2026-10-27T10:05:00Z")
    ledger.save_check(db, [found()], "2026-10-27T10:10:00Z")
    [finding] = ledger.list_findings(db)
    assert (finding["status"], finding["resolved_at"]) == ("open", None)


def test_findings_come_most_recently_seen_first(db):
    older, newer = found(fingerprint="a", last_seen="2026-10-26T09:00:00Z"), found(fingerprint="b")
    ledger.save_check(db, [older, newer], "2026-10-27T10:00:00Z")
    assert [f["fingerprint"] for f in ledger.list_findings(db)] == ["b", "a"]


def test_a_check_reads_recent_writes_and_every_stock_level(db):
    old = "2026-10-01T10:00:00Z"
    ledger.record_write(db, write(written_at=old))
    ledger.record_write(db, write(system="shopify", model="inventory_level", record_ref="8", identity="unattributed",
                                  written_at=old, payload={"sku": "AL01-36", "location": "Milan", "available": 4}))
    ledger.record_write(db, write(model="sale.order", record_ref="9", written_at=old, payload={"name": "S1"}))
    ledger.record_write(db, write(model="sale.order", record_ref="10", payload={"name": "S2"}))
    rows = ledger.writes_to_check(db, "2026-10-20T00:00:00Z", ("stock.quant", "inventory_level"))
    assert sorted((r["model"], r["record_ref"]) for r in rows) == [
        ("inventory_level", "8"), ("sale.order", "10"), ("stock.quant", "7")]
    assert all(isinstance(r["payload"], dict) for r in rows)


# --- triage results and calls ---------------------------------------------------------

def test_triage_results_keep_every_key_and_the_latest_usable_answer(db):
    ledger.save_triage(db, "k1", "fp", {"severity": "high"})
    ledger.save_triage(db, "k2", "fp", {"severity": "critical"})
    ledger.save_triage(db, "k3", "fp", {})  # declined: remembered, so it isn't asked again
    assert ledger.triaged_keys(db) == {"k1", "k2", "k3"}
    assert ledger.latest_triage(db) == {"fp": {"severity": "critical"}}


def test_the_cost_shown_is_the_last_run_that_called_claude(db):
    assert ledger.last_run_cost(db) is None
    call = {"model": "claude-haiku-5-5", "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.0002}
    ledger.save_calls(db, "2026-10-27T10:00:00Z", "fp1", [call, call | {"cost_usd": 0.004}])
    ledger.save_calls(db, "2026-10-27T10:05:00Z", "fp2", [call])
    ledger.save_calls(db, "2026-10-27T10:05:00Z", "fp3", [call | {"cost_usd": None}])  # a model we can't price
    assert ledger.last_run_cost(db) == pytest.approx(0.0002)
