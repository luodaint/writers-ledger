#!/bin/bash
# One-shot Odoo bootstrap, safe to re-run: create the database with a strong
# admin password (it never exists as admin/admin) and install Sales + Inventory.
# Postgres connection comes from PGHOST/PGUSER/PGPASSWORD.
set -euo pipefail
: "${ODOO_DB:?}" "${ODOO_ADMIN_PASSWORD:?}"

sql() { psql -X -q -t -A -v ON_ERROR_STOP=1 -v db="$ODOO_DB" -d "$1"; }

if [ -z "$(echo "SELECT 1 FROM pg_database WHERE datname = :'db'" | sql postgres)" ]; then
  odoo db init "$ODOO_DB" --password "$ODOO_ADMIN_PASSWORD"
fi
installed=$(echo "SELECT count(*) FROM ir_module_module
                  WHERE name IN ('sale_management', 'stock') AND state = 'installed'" | sql "$ODOO_DB")
if [ "$installed" != "2" ]; then
  odoo module install -d "$ODOO_DB" sale_management stock
fi
echo "Odoo database '$ODOO_DB' is ready."
