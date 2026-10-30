#!/usr/bin/env python3
"""Run the real verifier over an EvalForge result and report each model's fallback rate.

EvalForge answers "how good is this model on these prompts?". This answers the
product question: "how often would users see this model, and how often the
fallback?" It also lists the failed checks, which is how to tell a true catch
from a verifier that is too strict.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spend_explainer.analytics import load_dataset  # noqa: E402
from spend_explainer.analyze import analyze  # noqa: E402
from spend_explainer.verify import verify  # noqa: E402

DEFAULT_RESULT = ROOT / "evals" / "results" / "latest.json"


def score(run: dict) -> dict:
    """Return {model: {"shown": n, "rules": n, "total": n, "failures": Counter, "errors": n}}."""
    scores = defaultdict(lambda: {"shown": 0, "rules": 0, "total": 0, "failures": Counter(), "errors": 0})
    for entry in run["results"]:
        scenario = entry["test_case"]["name"]
        analysis = analyze(load_dataset(ROOT / "fixtures" / scenario))
        for model, cell in entry["cells"].items():
            tally = scores[model]
            tally["total"] += 1
            if cell.get("error") or cell.get("blocked"):
                tally["errors"] += 1
                continue
            verification = verify(cell["response_text"], analysis)
            tally["shown"] += verification.passed
            tally["rules"] += all(check["passed"] for check in cell.get("checks") or [])
            tally["failures"].update(check.name for check in verification.failures)
    return scores


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RESULT
    if not path.exists():
        print(f"No result at {path}. Run evals/run_evalforge.py first.", file=sys.stderr)
        return 1

    print(f"{'model':<34}{'rules':>7}{'shown':>7}{'fallback':>10}{'errors':>8}  failed checks")
    for model, tally in score(json.loads(path.read_text())).items():
        fallback = tally["total"] - tally["shown"] - tally["errors"]
        failed = ", ".join(f"{name} x{count}" for name, count in tally["failures"].most_common()) or "-"
        print(f"{model:<34}{tally['rules']:>7}{tally['shown']:>7}{fallback:>10}{tally['errors']:>8}  {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
