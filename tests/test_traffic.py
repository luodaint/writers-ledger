import pytest

from sim import traffic

CONTEXT = {"inventory_mode": True}


class Odoo:
    """Answers the JSON-2 calls the traffic makes, and keeps them."""

    def __init__(self, quants):
        self.quants, self.calls = quants, []

    def __call__(self, model, method, **params):
        self.calls.append((model, method, params))
        return {"search_read": self.quants, "create": [99]}.get(method, True)


@pytest.fixture(autouse=True)
def one_step_down(monkeypatch):
    monkeypatch.setattr(traffic.rng, "choice", lambda steps: -1)


def test_a_stock_count_writes_the_quant_that_is_there():
    # Not Odoo's create upsert: it sets the count by assignment, which the ORM skips when it equals the reset 0.
    odoo = Odoo(quants=[{"id": 7, "quantity": 1.0}])
    assert traffic.adjust_stock(odoo, {"AL09-38": 5}, {"Barcelona": 14}, "AL09-38", "Barcelona") == 0
    assert odoo.calls[1:] == [
        ("stock.quant", "write", {"ids": [7], "vals": {"inventory_quantity": 0}, "context": CONTEXT}),
        ("stock.quant", "action_apply_inventory", {"ids": [7], "context": CONTEXT}),
    ]


def test_a_stock_count_brings_back_a_quant_odoo_deleted_at_zero():
    # Odoo's scheduler deletes empty quants, so a second `make traffic` found none and crashed.
    odoo = Odoo(quants=[])
    assert traffic.adjust_stock(odoo, {"AL10-39": 5}, {"Milan": 14}, "AL10-39", "Milan") == 0
    assert odoo.calls[1:] == [
        ("stock.quant", "create", {"vals_list": [{"product_id": 5, "location_id": 14, "inventory_quantity": 0}],
                                   "context": CONTEXT}),
        ("stock.quant", "action_apply_inventory", {"ids": [99], "context": CONTEXT}),
    ]
