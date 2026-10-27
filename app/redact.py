"""Remove customer personal data from anything about to go to the LLM. Pure, no I/O.

Triage only ever sends an allowlisted summary, never payloads; this is the second layer.
"""
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+\d[\d ()-]{6,}\d|\(?\b\d{3}\)?[ .-]?\d{3}[ .-]\d{3,4}\b")
PERSONAL = {"name", "first_name", "last_name", "customer", "email", "phone", "mobile", "address", "address1",
            "address2", "street", "street2", "city", "zip", "shipping_address", "billing_address"}


def redact(value):
    """A copy of `value` without personal keys, with emails and phone numbers masked in every string."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if k.lower() not in PERSONAL}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return PHONE.sub("[phone]", EMAIL.sub("[email]", value))
    return value
