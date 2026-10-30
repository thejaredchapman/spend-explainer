#!/usr/bin/env python3
"""Send the test cases to a running EvalForge Lite web app and save the result.

Start EvalForge first (see docs/EVALS.md). Its server reads OPENROUTER_API_KEY
from its own environment, so this script never handles a key.

Each call counts against EvalForge's run limit, so all cases go in one run.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "test_cases.json"
RESULTS = ROOT / "evals" / "results"
TIMEOUT_SECONDS = 900  # EvalForge answers only after every cell and judge call finishes

DEFAULT_MODELS = (
    "~anthropic/claude-opus-latest",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    args = parser.parse_args()

    body = {"test_cases": json.loads(CASES.read_text()), "models": args.models}
    request = urllib.request.Request(
        f"{args.url}/api/run",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            run = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(f"EvalForge returned HTTP {error.code}: {error.read().decode('utf-8')[:500]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"Could not reach EvalForge at {args.url}: {error.reason}", file=sys.stderr)
        return 1

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "latest.json"
    path.write_text(json.dumps(run, indent=1) + "\n")
    print(f"Saved {path.relative_to(ROOT)}. Next: python3 evals/score_results.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
