import json

import pytest

from app.redact import redact


@pytest.mark.parametrize("text", ["laura.perez@example.com", "Write to Laura <laura.perez@example.com> today", "giulia.b+orders@mail.example.it"])
def test_emails_are_masked(text):
    assert "@" not in redact(text)
    assert "[email]" in redact(text)


@pytest.mark.parametrize("text", ["+34600000000", "+34 600 000 000", "call (555) 123-4567", "600 000 000", "+39 02 1234 5678"])
def test_phone_numbers_are_masked(text):
    masked = redact(text)
    assert "[phone]" in masked
    assert not any(ch.isdigit() for ch in masked)


@pytest.mark.parametrize(
    "text", ["2026-10-27T10:33:00Z", "AL05-38", "odoo:stock.quant", "res.users#13", "12 times", "Odoo has 5, Shopify has 8", "gid://shopify/Product/788032119674292922"]
)
def test_what_triage_needs_is_kept(text):
    assert redact(text) == text


def test_personal_keys_are_dropped_and_strings_masked_however_deep():
    finding = {
        "rule": "unknown_writer",
        "count": 3,
        "facts": {
            "customer": {"name": "Laura Pérez"},
            "email": "x@y.com",
            "sku": "AL01-36",
            "notes": ["ring +34 600 000 000", {"address": "Carrer de Mallorca 1", "city": "Barcelona"}],
        },
    }
    clean = redact(finding)
    assert clean == {"rule": "unknown_writer", "count": 3, "facts": {"sku": "AL01-36", "notes": ["ring [phone]", {}]}}
    for personal in ("Laura", "Pérez", "x@y.com", "600", "Mallorca", "Barcelona"):
        assert personal not in json.dumps(clean, ensure_ascii=False)


def test_the_input_is_not_changed():
    finding = {"facts": {"email": "x@y.com"}, "identity": "a@b.com"}
    redact(finding)
    assert finding == {"facts": {"email": "x@y.com"}, "identity": "a@b.com"}
