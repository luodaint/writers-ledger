import pytest

from sim import traffic


class Odoo:
    """Answers the JSON-2 calls the traffic makes, and keeps them."""

    def __init__(self, quants):
        self.quants, self.calls = quants, []

    def __call__(self, model, method, **params):
        self.calls.append((model, method, params))
        return {"search_read": self.quants, "create": [99]}.get(method, True)


@pytest.mark.parametrize(("quants", "counted"), [([{"id": 7, "quantity": 4.0}], 3), ([], 0)],
                         ids=["there", "deleted at zero"])
def test_a_stock_count_goes_through_odoos_inventory_mode(monkeypatch, quants, counted):
    # Odoo's scheduler deletes empty quants, so a second `make traffic` found none and crashed.
    monkeypatch.setattr(traffic.rng, "choice", lambda steps: -1)
    odoo = Odoo(quants)
    quantity = traffic.adjust_stock(odoo, {"AL10-39": 5}, {"Milan": 14}, "AL10-39", "Milan")
    context = {"inventory_mode": True}
    assert odoo.calls[1:] == [
        ("stock.quant", "create", {"vals_list": [{"product_id": 5, "location_id": 14, "inventory_quantity": quantity}],
                                   "context": context}),
        ("stock.quant", "action_apply_inventory", {"ids": [99], "context": context}),
    ]
    assert quantity == counted  # one step from what Odoo has, never below zero
