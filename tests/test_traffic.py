from sim import traffic


class Odoo:
    """Answers the JSON-2 calls the traffic makes, and keeps them."""

    def __init__(self, quants):
        self.quants, self.calls = quants, []

    def __call__(self, model, method, **params):
        self.calls.append((model, method, params))
        return {"search_read": self.quants, "create": [99]}.get(method, True)


def test_a_stock_count_brings_back_a_quant_odoo_deleted_at_zero():
    # Odoo's scheduler deletes empty quants, so a second `make traffic` found none and crashed.
    odoo = Odoo(quants=[])
    quantity = traffic.adjust_stock(odoo, {"AL10-39": 5}, {"Milan": 14}, "AL10-39", "Milan")
    [(_, _, created)] = [c for c in odoo.calls if c[1] == "create"]
    assert created["vals_list"] == [{"product_id": 5, "location_id": 14, "inventory_quantity": quantity}]
    assert ("stock.quant", "action_apply_inventory", {"ids": [99], "context": {"inventory_mode": True}}) in odoo.calls


def test_a_stock_count_updates_the_quant_that_is_there():
    odoo = Odoo(quants=[{"id": 7, "quantity": 4.0}])
    quantity = traffic.adjust_stock(odoo, {"AL01-36": 5}, {"Milan": 14}, "AL01-36", "Milan")
    assert ("stock.quant", "write", {"ids": [7], "vals": {"inventory_quantity": quantity},
                                     "context": {"inventory_mode": True}}) in odoo.calls
    assert not [c for c in odoo.calls if c[1] == "create"]
