import pathlib

import pytest

from app.register import RegisterError, Writer, load_register, parse_register, writer_for

REPO = pathlib.Path(__file__).parent.parent

VALID = """
- id: stock-cron
  owner: sysadmin
  purpose: Pushes stock
  identity: { odoo_user: stock@alohas.test }
  may_write: [odoo:stock.quant]
"""


def problems(text):
    with pytest.raises(RegisterError) as err:
        parse_register(text)
    return str(err.value)


def test_repo_register_loads():
    """The shipped writers.yaml is valid. The demo edits it, so its content is checked end to end
    by sim/smoke.py rather than pinned here."""
    assert load_register(REPO / "writers.yaml")


def test_valid_writer_is_parsed_into_a_writer():
    assert parse_register(VALID) == [
        Writer(
            id="stock-cron",
            owner="sysadmin",
            purpose="Pushes stock",
            identity={"odoo_user": "stock@alohas.test"},
            may_write=("odoo:stock.quant",),
        )
    ]


def test_duplicate_id_is_rejected():
    assert "duplicate id 'stock-cron'" in problems(VALID + VALID.replace("stock@", "other@"))


def test_writer_without_identity_is_rejected():
    assert "stock-cron: needs at least one identity" in problems(VALID.replace("{ odoo_user: stock@alohas.test }", "{}"))


def test_unknown_identity_kind_is_rejected():
    assert "unknown identity kind 'sap_user'" in problems(VALID.replace("odoo_user:", "sap_user:"))


@pytest.mark.parametrize("scope", ["stock.quant", "sap:stock.quant", "odoo:", "odoo:Stock Quant"])
def test_may_write_must_be_system_colon_model(scope):
    assert f"bad may_write entry '{scope}'" in problems(VALID.replace("[odoo:stock.quant]", f"['{scope}']"))


def test_missing_owner_key_is_rejected_but_empty_owner_is_allowed():
    assert "stock-cron: missing 'owner'" in problems(VALID.replace("  owner: sysadmin\n", ""))
    assert parse_register(VALID.replace("owner: sysadmin", 'owner: ""'))[0].owner == ""


def test_identity_claimed_by_two_writers_is_rejected():
    other = VALID.replace("id: stock-cron", "id: other-cron")
    assert "identity 'stock@alohas.test' is claimed by stock-cron and other-cron" in problems(VALID + other)


def test_misspelt_key_is_rejected():
    assert "unknown key 'may_wirte'" in problems(VALID.replace("may_write:", "may_wirte:"))


def test_every_problem_is_reported_at_once():
    text = VALID.replace("{ odoo_user: stock@alohas.test }", "{}").replace("[odoo:stock.quant]", "[stock.quant]")
    message = problems(text)
    assert "needs at least one identity" in message
    assert "bad may_write entry" in message


@pytest.mark.parametrize("text", ["", "id: lonely", "- just a string"])
def test_register_must_be_a_list_of_writers(text):
    assert problems(text)


def test_yaml_tags_are_never_executed(tmp_path):
    marker = tmp_path / "pwned"
    text = f"- !!python/object/apply:os.system ['touch {marker}']"
    with pytest.raises(RegisterError):
        parse_register(text)
    assert not marker.exists()


def test_writer_for_matches_the_identity_of_that_system():
    writers = parse_register("""
- id: stock-cron
  owner: sysadmin
  purpose: Pushes Odoo stock levels to Shopify locations
  identity: { odoo_user: stock@alohas.test, shopify_app: stock-sync }
  may_write: [odoo:stock.quant, shopify:inventory_level]
""")
    assert writer_for(writers, "odoo", "stock@alohas.test").id == "stock-cron"
    assert writer_for(writers, "shopify", "stock-sync").id == "stock-cron"
    assert writer_for(writers, "shopify", "stock@alohas.test") is None
    assert writer_for(writers, "odoo", "admin") is None
    assert writer_for(writers, "shopify", "unattributed") is None
