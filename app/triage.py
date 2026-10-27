"""Triage: Claude rates each new or changed finding and explains the serious ones.

The model sees an allowlisted summary, never payloads, and redact.py masks it again. Its
answers are checked against the schema and capped. The rule's severity is a floor.
"""

import hashlib
import json
import logging
import os
import pathlib
import re

import anthropic
import yaml

from app import redact, rules

log = logging.getLogger("triage")
SMALL = os.environ.get("TRIAGE_MODEL_SMALL", "claude-haiku-5-5")
LARGE = os.environ.get("TRIAGE_MODEL_LARGE", "claude-sonnet-5-5")
PROMPTS = yaml.safe_load((pathlib.Path(__file__).parent / "triage.yaml").read_text())
PRICES = {  # $ per million tokens: in, out
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),  # where Sonnet 5.5's declined requests fall back
}
MAX_TEXT = 1500  # characters kept of any answer
PER_RUN = 20  # findings triaged per run, so a flood of findings can't run up the bill
BUCKETS = (5, 1)  # writes round down to the rubric's one count line (triage.yaml): a growing count rarely re-pays
SCOPE = re.compile(r"\b(?:odoo|shopify):[a-z][a-z0-9_.]*[a-z0-9_]")
IDENTITY = re.compile(r"(?:odoo_user|shopify_app)\s*:\s*[\"']?([^\"'\n},]+)")
FINAL = (anthropic.BadRequestError, anthropic.NotFoundError, anthropic.UnprocessableEntityError)  # asking again won't help


def client() -> anthropic.Anthropic | None:
    """None without ANTHROPIC_API_KEY: triage is off and findings keep their rule severity."""
    return anthropic.Anthropic(timeout=60) if os.environ.get("ANTHROPIC_API_KEY") else None


def prepare(finding: dict, writers) -> tuple[dict, str]:
    """What the model sees, and the cache key: the finding, its writes rounded down, the register entry
    and the prompts. Drift quantities stay out of the key, so a sync outage doesn't pay on every sale."""
    writer = next((w for w in writers if w.id == finding["writer_id"]), None)
    identity = finding["identity"]
    payload = redact.redact(
        {
            "rule": finding["rule"],
            "rule_severity": finding["severity"],
            "what_changed": f"{finding['system']}:{finding['model']}",
            "writes": finding["count"],
            "identity": "[login]" if redact.EMAIL.fullmatch(identity) else identity,
            "stock": finding["facts"] or None,
            "writer": writer and {"id": writer.id, "owner": writer.owner, "purpose": writer.purpose, "may_write": list(writer.may_write)},
        }
    )
    bucket = next(b for b in BUCKETS if finding["count"] >= b)
    keyed = [finding["fingerprint"], payload | {"writes": bucket, "stock": None}, PROMPTS, SMALL, LARGE]
    return payload, hashlib.sha256(json.dumps(keyed, sort_keys=True).encode()).hexdigest()[:16]


def cost(model: str, usage) -> float | None:
    """Dollars for one call, or None for a model whose price we don't know."""
    if (price := PRICES.get(model)) is None:
        log.warning("No price for %s: the cost shown leaves its calls out", model)
        return None
    return (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1e6


def conform(answer, schema: dict) -> dict:
    """The answer if it has exactly the schema's fields, typed and within its enums, else {}; strings capped."""
    if not isinstance(answer, dict) or set(answer) != set(schema["required"]):
        return {}
    for field, spec in schema["properties"].items():
        value = answer[field]
        if spec["type"] == "number" and not (isinstance(value, int | float) and 0 <= value <= 1):  # confidence
            return {}
        if spec["type"] == "string" and not (isinstance(value, str) and value in spec.get("enum", [value])):
            return {}
    return {k: v[:MAX_TEXT] if isinstance(v, str) else float(v) for k, v in answer.items()}


def ask(claude, step: str, payload: dict) -> tuple[dict, dict]:
    """One structured call: the answer ({} if declined or off the schema) and the call's tokens and cost."""
    spec = PROMPTS[step]
    request = {
        "model": SMALL if step == "rate" else LARGE,
        "max_tokens": spec["max_tokens"],
        "system": spec["system"],
        "output_config": {"effort": spec["effort"], "format": {"type": "json_schema", "schema": spec["schema"]}},
        "messages": [{"role": "user", "content": json.dumps(payload, sort_keys=True)}],
    }
    if "fallbacks" in spec:  # only on the beta endpoint
        response = claude.beta.messages.create(**request, betas=["server-side-fallback-2026-07-01"], fallbacks=spec["fallbacks"])
    else:
        response = claude.messages.create(**request)
    call = {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": cost(response.model, response.usage),
    }
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        answer = conform(json.loads(text), spec["schema"]) if response.stop_reason == "end_turn" else {}
    except ValueError:
        answer = {}
    if not answer:
        log.warning("Triage %s: no usable answer from %s (%s)", step, response.model, response.stop_reason)
    return answer, call


def vetted(fix: str, payload: dict) -> str:
    """Claude's suggested fix, or "" if it would let anyone write more than this finding is about.

    Logins and app names come from outside, so a fix could be steered into granting more. Every
    scope in it must be the one that was changed or one the writer already has, and every
    identity the one that wrote."""
    allowed = {payload["what_changed"], *(payload["writer"] or {}).get("may_write", [])}
    if set(SCOPE.findall(fix)) <= allowed and {i.strip() for i in IDENTITY.findall(fix)} <= {payload["identity"], "[login]"}:
        return fix
    log.warning("Dropped a suggested fix that grants more than the finding is about")
    return ""


def triage(claude, payload: dict) -> tuple[dict | None, list[dict]]:
    """Rate with the small model, then explain what is high or critical once the rule's floor applies.

    The result is Claude's answer as given, or {} if Claude declined, answered off the schema or
    refused the request (don't pay to ask again for this input), or None if Claude couldn't be
    reached (try again later). Then the calls it made."""
    calls = []
    try:
        result, call = ask(claude, "rate", payload)
        calls.append(call)
        if result and (severity := rules.raised(payload["rule_severity"], result["severity"])) in ("critical", "high"):
            explained, call = ask(claude, "explain", payload | {"severity": severity})
            calls.append(call)
            result |= explained and explained | {"suggested_fix": vetted(explained["suggested_fix"], payload)}
        return result, calls
    except FINAL as e:
        log.warning("Triage request refused, not asking again for this input: %s", type(e).__name__)
        return {}, calls
    except anthropic.APIError as e:
        log.warning("Triage failed, will try again: %s", type(e).__name__)
        return None, calls
