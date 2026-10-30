#!/usr/bin/env python3
"""Build EvalForge Lite test cases, one per synthetic scenario.

Each case holds the exact prompt the product uses, so the eval measures the real
thing. EvalForge scores it in two ways:

  checks  fast pass/fail rules: JSON validity, the right status, cause and driver
  rubric  an LLM judge reads for what rules cannot: is the reasoning sound, and
          is the recommended action specific and useful?

The rule checks here mirror verify.py's conclusion checks but do not replace it.
score_results.py runs the real verifier on every response afterwards.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spend_explainer.analytics import load_dataset  # noqa: E402
from spend_explainer.analyze import analyze  # noqa: E402
from spend_explainer.prompt import build_prompt  # noqa: E402

OUTPUT = ROOT / "evals" / "test_cases.json"
MAX_ANSWER_CHARS = 2500

RUBRICS = {
    "quiet": "The answer must say spend is normal and must not invent a problem or recommend urgent action.",
    "one_day_outlier": (
        "The answer must say the recent window is normal. It may mention the single high day in the "
        "baseline, but must not treat that day as a current spike."
    ),
    "model_mix_shift": (
        "The answer must explain that a more expensive model replaced a cheaper one in the search workspace "
        "while request volume stayed flat. The action should be to find what changed the model."
    ),
    "cache_regression": (
        "The answer must explain that the cache hit rate collapsed in the support workspace while requests "
        "stayed flat, so input tokens are billed at the full rate. The action should target what broke "
        "the cached prompt prefix."
    ),
    "volume_increase": (
        "The answer must name the jobs API key as the source of a sharp traffic rise. The action should be "
        "to confirm whether the traffic is expected or a runaway process."
    ),
    "broad_growth": (
        "The answer must say the rise is spread across workspaces with no single driver, and must not "
        "blame one team. The action should compare it with expected growth."
    ),
}


def checks_for(verdict) -> list:
    checks = [
        {"type": "json_valid"},
        {"type": "max_length", "value": MAX_ANSWER_CHARS},
        {"type": "regex", "value": rf'"status"\s*:\s*"{verdict.status}"'},
        {"type": "regex", "value": rf'"primary_cause"\s*:\s*"{verdict.primary_cause}"'},
    ]
    if verdict.driver_value:
        checks.append({"type": "contains", "value": verdict.driver_value})
    else:
        checks.append({"type": "regex", "value": r'"driver"\s*:\s*null'})
    return checks


def build_cases() -> list:
    cases = []
    for name, rubric in RUBRICS.items():
        analysis = analyze(load_dataset(ROOT / "fixtures" / name))
        cases.append({
            "name": name,
            "prompt": build_prompt(analysis.facts),
            "rubric": rubric,
            "checks": checks_for(analysis.verdict),
        })
    return cases


def main() -> None:
    cases = build_cases()
    OUTPUT.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"Wrote {len(cases)} test cases to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
