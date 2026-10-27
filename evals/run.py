"""Score Claude's own rating of each finding in evals/cases.yaml, before the rule's floor.

`make eval`. Needs ANTHROPIC_API_KEY (from .env). It goes through the app's own
triage.prepare() and rating call, redaction included, and only pays for what it scores:
the small model, well under a cent a run. The explanations are tested with a stub and
read on the page.
"""
import pathlib
import sys

import anthropic
import yaml

from app import rules, triage
from app.register import parse_register

CASES = pathlib.Path(__file__).parent / "cases.yaml"


def main() -> int:
    claude = triage.client()
    if claude is None:
        sys.exit("make eval needs ANTHROPIC_API_KEY: put it in .env")
    spec = yaml.safe_load(CASES.read_text())
    writers, cases = parse_register(spec["register"]), spec["cases"]
    passed, cost = 0, 0.0
    for case in cases:
        finding = {"fingerprint": case["name"], "writer_id": None, "facts": {}, "count": 1} | case["finding"]
        finding["severity"] = rules.SEVERITY[finding["rule"]]
        try:
            rating, call = triage.ask(claude, "rate", triage.prepare(finding, writers)[0])
        except anthropic.APIError as e:
            sys.exit(f"Claude refused or couldn't be reached ({type(e).__name__}). Check ANTHROPIC_API_KEY in .env.")
        cost += call["cost_usd"] or 0
        got = {"severity": rating.get("severity"), "category": rating.get("category")}
        ok = got == case["expect"]
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['name']:<44} {got['severity']}, {got['category']}"
              + ("" if ok else f"   (expected {case['expect']['severity']}, {case['expect']['category']})"))
    print(f"\n{passed}/{len(cases)} cases passed. This run cost ${cost:.4f}.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
