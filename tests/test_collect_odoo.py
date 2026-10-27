import json

import httpx
import pytest

from app import collect_odoo, ledger

USERS = {2: "admin", 6: "stock@alohas.test"}
PRODUCTS = {11: "AL01-36"}
LOCATIONS = {3: "Milan"}
START = "2026-10-26 09:00:01"  # where `make seed` ended: what came before is seed data


class FakeOdoo:
    """Answers JSON-2 calls the way Odoo 19 does, and remembers what was asked."""

    def __init__(self, records=None, writable=(), forbidden=()):
        self.records = records or {}
        self.writable = set(writable)
        self.forbidden = set(forbidden)  # (model, id) reads or (model, "search_read") that raise AccessError
        self.calls = []

    def __call__(self, request):
        _, _, _, model, method = request.url.path.split("/")
        body = json.loads(request.content)
        self.calls.append((model, method, body, dict(request.headers)))
        if (model, method) in self.forbidden or any((model, i) in self.forbidden for i in body.get("ids", [])):
            return httpx.Response(403, json={"name": "odoo.exceptions.AccessError", "message": "no"})
        if method == "has_access":
            return httpx.Response(200, json=model in self.writable and body["operation"] == "write")
        if method == "read":
            names = {"res.users": USERS, "product.product": PRODUCTS, "stock.location": LOCATIONS}[model]
            [attr] = body["fields"]
            return httpx.Response(200, json=[{"id": i, attr: names[i]} for i in body["ids"]])
        rows = self.records.get(model, [])
        for field, op, value in body["domain"]:
            if field == "write_date":
                assert op == ">="
                rows = [r for r in rows if r["write_date"] >= value]
        if body.get("order") == "write_date desc":
            rows = sorted(rows, key=lambda r: r["write_date"], reverse=True)
        offset = body.get("offset", 0)
        return httpx.Response(200, json=rows[offset: offset + body["limit"]] if "limit" in body else rows)


def quant(id=7, uid=6, written="2026-10-26 10:00:00", created="2026-10-20 09:00:00", qty=4.0):
    return {
        "id": id, "write_uid": [uid, "Someone"], "write_date": written, "create_date": created,
        "product_id": [11, "[AL01-36] Rife Sneaker (36)"], "location_id": [3, "WH/Milan"], "quantity": qty,
    }


@pytest.fixture
def db(tmp_path):
    return ledger.connect(tmp_path / "ledger.db")


def http_for(odoo):
    return httpx.Client(base_url="http://odoo", transport=httpx.MockTransport(odoo))


def test_quant_record_becomes_a_write():
    resolve = {"write_uid": "stock@alohas.test", "product_id": "AL01-36", "location_id": "Milan"}
    write = collect_odoo.to_write("stock.quant", quant(), lambda field, value: resolve[field])
    assert write == {
        "system": "odoo", "model": "stock.quant", "record_ref": "7",
        "identity": "stock@alohas.test", "action": "write",
        "written_at": "2026-10-26T10:00:00Z",
        "payload": {"sku": "AL01-36", "location": "Milan", "quantity": 4.0},
    }


def test_same_create_and_write_date_means_create():
    record = quant(written="2026-10-26 10:00:00", created="2026-10-26 10:00:00")
    assert collect_odoo.to_write("stock.quant", record, lambda f, v: "x")["action"] == "create"


def test_only_allowlisted_fields_reach_the_ledger():
    order = {"id": 1, "write_uid": [6, "x"], "write_date": "2026-10-26 10:00:00",
             "create_date": "2026-10-26 10:00:00", "name": "S00001", "state": "draft",
             "amount_total": 120.0, "partner_id": [9, "Laura Pérez"], "partner_shipping_id": [9, "x"]}
    payload = collect_odoo.to_write("sale.order", order, lambda f, v: "sync@alohas.test")["payload"]
    assert payload == {"name": "S00001", "state": "draft", "amount_total": 120.0}


def test_first_poll_starts_where_the_seed_ended(db):
    odoo = FakeOdoo({"stock.quant": [quant(written="2026-10-26 09:00:00"), quant(id=8, uid=2, written="2026-10-26 09:00:07")]})
    assert collect_odoo.poll_model(http_for(odoo), db, "stock.quant", {}, START) == 1
    assert [r["record_ref"] for r in ledger.latest_writes(db)] == ["8"]
    assert ledger.get_cursor(db, "odoo:stock.quant:since") == START


def test_a_key_file_from_an_older_seed_waits_for_make_seed(db, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"ledger": {"login": "ledger@alohas.test", "key": "k"}}))  # no "since"
    monkeypatch.setattr(collect_odoo, "KEYS_FILE", str(keys))
    assert collect_odoo.poll_once(db, {}) == 0
    assert "make seed" in caplog.text


def test_writes_between_the_seed_and_the_first_poll_are_not_missed(db, tmp_path, monkeypatch):
    """Regression: the first poll used to start from its own moment, so traffic played right after
    `make seed`, before that poll, was never recorded (found in an end-to-end run)."""
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"ledger": {"login": "ledger@alohas.test", "key": "k"}, "since": "2026-10-26 09:00:05"}))
    monkeypatch.setattr(collect_odoo, "KEYS_FILE", str(keys))
    monkeypatch.setattr(collect_odoo, "URL", "http://odoo")
    seed, traffic = quant(written="2026-10-26 09:00:00"), quant(id=8, uid=2, written="2026-10-26 09:00:07")
    odoo = FakeOdoo({"stock.quant": [seed, traffic]})
    assert collect_odoo.poll_once(db, {}, transport=httpx.MockTransport(odoo)) == 1
    [row] = ledger.latest_writes(db)
    assert (row["record_ref"], row["identity"]) == ("8", "admin")


def test_next_poll_records_new_writes_and_moves_the_cursor(db):
    odoo = FakeOdoo({"stock.quant": [quant(written="2026-10-26 09:00:00")]})
    http = http_for(odoo)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(id=8, uid=2, written="2026-10-26 10:00:00"))
    assert collect_odoo.poll_model(http, db, "stock.quant", {}, START) == 1
    [row] = ledger.latest_writes(db)
    assert (row["record_ref"], row["identity"]) == ("8", "admin")
    assert ledger.get_cursor(db, "odoo:stock.quant") == "2026-10-26 10:00:00"


def test_overlapping_polls_never_duplicate(db):
    odoo = FakeOdoo({"stock.quant": []})
    http = http_for(odoo)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(written="2026-10-26 10:00:00"))
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(id=9, written="2026-10-26 10:00:00"))
    assert collect_odoo.poll_model(http, db, "stock.quant", {}, START) == 1
    assert len(ledger.latest_writes(db)) == 2


def test_logins_are_looked_up_once(db):
    odoo = FakeOdoo({"stock.quant": []})
    http = http_for(odoo)
    cache = {}
    collect_odoo.poll_model(http, db, "stock.quant", cache, START)
    odoo.records["stock.quant"] += [quant(id=i, written=f"2026-10-26 10:0{i}:00") for i in range(3)]
    collect_odoo.poll_model(http, db, "stock.quant", cache, START)
    user_reads = [c for c in odoo.calls if c[:2] == ("res.users", "read")]
    assert len(user_reads) == 1


def test_requests_carry_key_and_database():
    odoo = FakeOdoo()
    with collect_odoo.client("http://odoo", "writers", "secret-key", transport=httpx.MockTransport(odoo)) as http:
        collect_odoo.call(http, "stock.quant", "has_access", operation="write")
    headers = odoo.calls[0][3]
    assert headers["authorization"] == "bearer secret-key"
    assert headers["x-odoo-database"] == "writers"


def test_refuses_to_poll_with_a_key_that_can_write(db, caplog):
    odoo = FakeOdoo({"stock.quant": [quant()]}, writable={"stock.quant"})
    assert collect_odoo.poll_all(http_for(odoo), db, {}, START) == 0
    assert not [c for c in odoo.calls if c[1] == "search_read"]
    assert "can write to ['stock.quant']" in caplog.text


def test_odoo_being_down_does_not_crash_the_collector(db, tmp_path, monkeypatch, caplog):
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"ledger": {"login": "ledger@alohas.test", "key": "super-secret-key-123"},
                                "since": START}))
    monkeypatch.setattr(collect_odoo, "KEYS_FILE", str(keys))

    def down(request):
        raise httpx.ConnectError("connection refused")

    assert collect_odoo.poll_once(db, {}, transport=httpx.MockTransport(down)) == 0
    assert "Odoo poll failed" in caplog.text
    assert "super-secret-key-123" not in caplog.text


def test_without_keys_the_collector_waits_for_the_seed(db, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(collect_odoo, "KEYS_FILE", str(tmp_path / "missing.json"))
    assert collect_odoo.poll_once(db, {}) == 0
    assert "make seed" in caplog.text


def test_only_quants_in_real_locations_are_watched(db):
    odoo = FakeOdoo({"stock.quant": []})
    http = http_for(odoo)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    domains = [c[2]["domain"] for c in odoo.calls if c[1] == "search_read"]
    assert all(["location_id.usage", "=", "internal"] in d for d in domains)


def test_a_bulk_write_bigger_than_a_page_cannot_blind_the_collector(db, monkeypatch):
    """One Odoo bulk write gives every record the same write_date; we must page past it."""
    monkeypatch.setattr(collect_odoo, "PAGE", 2)
    odoo = FakeOdoo({"stock.quant": []})
    http = http_for(odoo)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"] += [quant(id=i, uid=2, written="2026-10-26 10:00:00") for i in range(5)]
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(id=99, written="2026-10-26 10:00:07"))
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    assert len(ledger.latest_writes(db)) == 6
    assert ledger.get_cursor(db, "odoo:stock.quant") == "2026-10-26 10:00:07"


def test_a_write_committed_late_is_still_seen(db):
    """Odoo stamps write_date when a transaction starts; a long one can commit after we polled past it."""
    odoo = FakeOdoo({"stock.quant": []})
    http = http_for(odoo)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(id=1, written="2026-10-26 10:00:10"))
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(id=2, uid=2, written="2026-10-26 10:00:05"))  # committed late
    assert collect_odoo.poll_model(http, db, "stock.quant", {}, START) == 1
    assert {r["record_ref"] for r in ledger.latest_writes(db)} == {"1", "2"}


def test_a_user_named_like_the_sentinel_is_not_mistaken_for_unattributed():
    resolve = {"write_uid": "unattributed", "product_id": "AL01-36", "location_id": "Milan"}
    write = collect_odoo.to_write("stock.quant", quant(), lambda field, value: resolve[field])
    assert write["identity"] == '"unattributed"'


def test_a_writer_we_cannot_read_is_recorded_as_unknown_instead_of_stalling(db):
    odoo = FakeOdoo({"stock.quant": []}, forbidden={("res.users", 13)})
    http = http_for(odoo)
    collect_odoo.poll_model(http, db, "stock.quant", {}, START)
    odoo.records["stock.quant"].append(quant(uid=13, written="2026-10-26 10:00:00"))
    assert collect_odoo.poll_model(http, db, "stock.quant", {}, START) == 1
    assert ledger.latest_writes(db)[0]["identity"] == "res.users#13"
    assert ledger.get_cursor(db, "odoo:stock.quant") == "2026-10-26 10:00:00"


def test_one_failing_model_does_not_stop_the_others(db, caplog):
    odoo = FakeOdoo({"stock.quant": []}, forbidden={("stock.picking", "search_read")})
    http = http_for(odoo)
    collect_odoo.poll_all(http, db, {}, START)
    odoo.records["stock.quant"].append(quant(written="2026-10-26 10:00:00"))
    assert collect_odoo.poll_all(http, db, {}, START) == 1
    assert "stock.picking" in caplog.text
