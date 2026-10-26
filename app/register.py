"""The register (writers.yaml): who may write to what, and who owns them."""
import re
from dataclasses import dataclass

import yaml

IDENTITY_KINDS = ("odoo_user", "shopify_app")
KEYS = {"id", "owner", "purpose", "identity", "may_write"}
SCOPE = re.compile(r"(odoo|shopify):[a-z][a-z0-9_]*(\.[a-z0-9_]+)*")


class RegisterError(ValueError):
    """The register is broken. The message lists every problem found."""


@dataclass(frozen=True)
class Writer:
    id: str
    owner: str
    purpose: str
    identity: dict
    may_write: tuple


def load_register(path) -> list[Writer]:
    with open(path, encoding="utf-8") as f:
        return parse_register(f.read())


def parse_register(text: str) -> list[Writer]:
    try:
        entries = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RegisterError(f"writers.yaml is not valid YAML: {e}") from None
    if not isinstance(entries, list) or not entries:
        raise RegisterError("writers.yaml must be a non-empty list of writers")
    problems, writers, claimed = [], [], {}
    for n, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append(f"entry {n}: must be a mapping with {', '.join(sorted(KEYS))}")
            continue
        wid = entry.get("id")
        name = wid if isinstance(wid, str) and wid else f"entry {n}"
        if name != wid:
            problems.append(f"{name}: missing 'id'")
        elif any(w.id == wid for w in writers):
            problems.append(f"duplicate id '{wid}'")
        problems += [f"{name}: unknown key '{k}'" for k in sorted(entry.keys() - KEYS)]
        if "owner" not in entry:
            problems.append(f"{name}: missing 'owner' (write owner: \"\" if nobody owns it)")
        owner, purpose = entry.get("owner") or "", entry.get("purpose") or ""
        if not isinstance(owner, str) or not isinstance(purpose, str):
            problems.append(f"{name}: owner and purpose must be text")
        identity = entry.get("identity") or {}
        if not isinstance(identity, dict):
            problems.append(f"{name}: identity must be a mapping")
            identity = {}
        for kind, value in identity.items():
            if kind not in IDENTITY_KINDS:
                problems.append(f"{name}: unknown identity kind '{kind}'")
            elif not isinstance(value, str) or not value:
                problems.append(f"{name}: {kind} must be non-empty text")
            elif (kind, value) in claimed:
                problems.append(f"identity '{value}' is claimed by {claimed[kind, value]} and {name}")
            else:
                claimed[kind, value] = name
        if not identity:
            problems.append(f"{name}: needs at least one identity ({' or '.join(IDENTITY_KINDS)})")
        may_write = entry.get("may_write") or []
        if not isinstance(may_write, list):
            problems.append(f"{name}: may_write must be a list")
            may_write = []
        for scope in may_write:
            if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
                problems.append(f"{name}: bad may_write entry '{scope}' (expected system:model, e.g. odoo:stock.quant)")
        writers.append(Writer(name, owner, purpose, identity, tuple(may_write)))
    if problems:
        raise RegisterError("writers.yaml has problems:\n- " + "\n- ".join(problems))
    return writers
