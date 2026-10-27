import pathlib
import re

SAFE_DEFAULTS = {"ODOO_DB": "writers", "POLL_SECONDS": "30", "SHOPIFY_STORE": "your-dev-store.myshopify.com",
                 "TRIAGE_MODEL_SMALL": "claude-haiku-5-5", "TRIAGE_MODEL_LARGE": "claude-sonnet-5-5"}


def test_env_example_lists_names_not_values():
    """.env.example is public. A key pasted there by mistake fails here, before it can be committed."""
    text = (pathlib.Path(__file__).parent.parent / ".env.example").read_text()
    values = dict(re.findall(r"^([A-Z_]+)=(.*)$", text, re.MULTILINE))
    assert values, "no settings found"
    assert {name: v for name, v in values.items() if v and SAFE_DEFAULTS.get(name) != v} == {}
