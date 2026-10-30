#!/usr/bin/env python3
"""Test the eval itself, offline and for free.

An eval that passes bad answers is worse than none, so before spending a live
run this applies each case's rule checks to the stand-in answers. Good answers
must pass every rule. Each bad answer must fail at least one rule, except the
ones listed in CAUGHT_ONLY_BY_VERIFIER.

run_check matches EvalForge's checks.py behaviour.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from build_cases import build_cases  # noqa: E402
from spend_explainer import RECORDED  # noqa: E402

# EvalForge's rules read the conclusion, not the prose, so these two defects
# get past them. This is why score_results.py also runs the real verifier.
CAUGHT_ONLY_BY_VERIFIER = {"wrong_number", "invented_id"}


def run_check(check: dict, text: str) -> bool:
    kind = check["type"]
    if kind == "contains":
        return check["value"] in text
    if kind == "regex":
        return re.search(check["value"], text) is not None
    if kind == "max_length":
        return len(text) <= check["value"]
    if kind == "json_valid":
        try:
            json.loads(text)
            return True
        except json.JSONDecodeError:
            return False
    raise ValueError(f"Unknown check type: {kind!r}")


def main() -> int:
    mistakes = 0
    print(f"{'scenario':<18}{'answer':<15}rule checks")
    for case in build_cases():
        for path in sorted((RECORDED / case["name"]).glob("*.txt")):
            passed = all(run_check(check, path.read_text()) for check in case["checks"])
            expected = path.stem == "good" or path.stem in CAUGHT_ONLY_BY_VERIFIER
            mistakes += passed != expected
            print(f"{case['name']:<18}{path.stem:<15}{'pass' if passed else 'fail'}{'' if passed == expected else '  UNEXPECTED'}")
    print(f"\nUnexpected outcomes: {mistakes}")
    return 1 if mistakes else 0


if __name__ == "__main__":
    sys.exit(main())
