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
