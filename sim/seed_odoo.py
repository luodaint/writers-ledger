"""Seed Odoo for the demo. Runs inside `odoo shell` (see `make seed`): `env` is Odoo's.

Creates the bot users, each with its own API key, 15 shoes in sizes 36-41, the
three stock locations and starting stock. Safe to re-run: data is kept and every
key is rotated. Keys never go to the output. They go to two files (mode 0640) on
two volumes, so each container gets only what it needs:
- /secrets/collector/odoo-key.json: the read-only collector key, for the app;
- /secrets/writers/odoo-keys.json: the writers' keys and the admin key, for the
  traffic simulator only. The admin key plays the forgotten cron and lasts 7 days.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, "/opt/writers-ledger")
from sim import catalog  # noqa: E402

COLLECTOR_FILE = "/secrets/collector/odoo-key.json"
WRITERS_FILE = "/secrets/writers/odoo-keys.json"
KEY_NAME = "writers-ledger demo"
KEY_DAYS = {"admin": 7}  # everyone else 89: Odoo caps internal users at 90; `make seed` rotates
BOTS = {  # role -> login, name, extra groups
    "ledger": ("ledger@alohas.test", "Writers Ledger collector (read-only)", []),
    "sync": ("sync@alohas.test", "Shopify-Odoo sync",
             ["sales_team.group_sale_salesman_all_leads", "base.group_partner_manager"]),
    "stock": ("stock@alohas.test", "Stock cron", ["stock.group_stock_user"]),
    "pmo": ("pmo@alohas.test", "PMO scripts", []),
}


def read_only_group():
    """Internal users can't read sale orders or pickings; this adds read, and only read."""
    group = env["res.groups"].search([("name", "=", "Writers Ledger: read-only")], limit=1)
    if not group:
        group = env["res.groups"].create({"name": "Writers Ledger: read-only"})
        for model in ("sale.order", "stock.picking"):
            env["ir.model.access"].create({
                "name": f"writers-ledger read {model}", "group_id": group.id,
                "model_id": env["ir.model"]._get_id(model), "perm_read": True,
            })
    return group


def bot(login, name, groups):
    users = env["res.users"].with_context(active_test=False, no_reset_password=True)
    vals = {"name": name, "login": login, "active": True,
            "group_ids": [(6, 0, [env.ref("base.group_user").id, *(g.id for g in groups)])]}
    user = users.search([("login", "=", login)], limit=1)
    if user:
        user.write(vals)
    else:
        user = users.create(vals)  # no password: the API key is the only way in
    return user


def rotate_key(role, user):
    env["res.users.apikeys"].search([("user_id", "=", user.id), ("name", "=", KEY_NAME)]).unlink()
    expires = datetime.datetime.now() + datetime.timedelta(days=KEY_DAYS.get(role, 89))
    return env["res.users.apikeys"].with_user(user).sudo()._generate("rpc", KEY_NAME, expires)


def products():
    size = env["product.attribute"].search([("name", "=", "Size")], limit=1) or env["product.attribute"].create({
        "name": "Size", "create_variant": "always",
        "value_ids": [(0, 0, {"name": str(s)}) for s in catalog.SIZES],
    })
    for n, name in enumerate(catalog.SHOES):
        template = env["product.template"].search([("name", "=", name)], limit=1) or env["product.template"].create({
            "name": name, "type": "consu", "is_storable": True, "list_price": 120 + 10 * n,
            "attribute_line_ids": [(0, 0, {"attribute_id": size.id, "value_ids": [(6, 0, size.value_ids.ids)]})],
        })
        for variant in template.product_variant_ids:
            sku = catalog.sku(n, int(variant.product_template_attribute_value_ids.name))
            if variant.default_code != sku:  # unchanged records stay untouched: a re-seed must not look like writes
                variant.default_code = sku


def locations():
    parent = env.ref("stock.warehouse0").view_location_id
    Location = env["stock.location"]
    return {
        name: Location.search([("name", "=", name), ("location_id", "=", parent.id)], limit=1)
        or Location.create({"name": name, "location_id": parent.id, "usage": "internal"})
        for name in catalog.LOCATIONS
    }


def stock(locs):
    Quant = env["stock.quant"].with_context(inventory_mode=True)
    new = Quant.browse()
    for variant in env["product.product"].search([("default_code", "=like", "AL%")]):
        for name, location in locs.items():
            if not Quant.search_count([("product_id", "=", variant.id), ("location_id", "=", location.id)]):
                new |= Quant.create({"product_id": variant.id, "location_id": location.id,
                                     "inventory_quantity": catalog.initial_stock(variant.default_code, name)})
    new.action_apply_inventory()
    return len(new)


def write_keys(path, keys):
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.unlink(tmp)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640), "w") as f:
        json.dump(keys, f)
    os.replace(tmp, path)


env["res.config.settings"].create({"group_stock_multi_locations": True}).execute()
products()
quants = stock(locations())
readonly = read_only_group()
users = {role: bot(login, name, [env.ref(g) for g in groups] + ([readonly] if role == "ledger" else []))
         for role, (login, name, groups) in BOTS.items()}
users["admin"] = env.ref("base.user_admin")
keys = {role: {"login": user.login, "key": rotate_key(role, user)} for role, user in users.items()}
env.cr.commit()
env.registry.signal_changes()  # tell the running Odoo server its access caches are stale
write_keys(COLLECTOR_FILE, {"ledger": keys.pop("ledger")})
write_keys(WRITERS_FILE, keys)
print(f"Seeded Odoo: {len(catalog.variants())} variants, {quants} new stock lines. "
      f"Collector key in {COLLECTOR_FILE}; keys for {', '.join(keys)} in {WRITERS_FILE}.")
