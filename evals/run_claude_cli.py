#!/usr/bin/env python3
"""Run the test cases on real Claude models through your Claude Code login.

This is the eval to use when you have no OpenRouter key. It needs only the
`claude` command, and it reaches models EvalForge's catalog does not list yet.

It writes the same result shape as EvalForge, so score_results.py reads either.

Compared with EvalForge:
  - it runs the rule checks and the verifier, but NOT the rubric judge
  - Claude Code adds a short system prompt, while EvalForge sends only the prompt
  - it covers Claude models only

It sends the synthetic prompts only. Tools, settings and MCP servers are turned off.
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

from dry_run import run_check  # noqa: E402

CASES = ROOT / "evals" / "test_cases.json"
RESULTS = ROOT / "evals" / "results"
TIMEOUT_SECONDS = 300
MAX_WORKERS = 3

DEFAULT_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")

COMMAND = (
    "claude", "-p", "--tools", "", "--setting-sources", "", "--strict-mcp-config",
    "--no-session-persistence", "--output-format", "json",
)


def call_claude(prompt: str, model: str) -> dict:
    """Return Claude Code's JSON result. The prompt goes over stdin, so it stays out of `ps`."""
    done = subprocess.run(
        [*COMMAND, "--model", model], input=prompt, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()[:300] or f"claude exited with {done.returncode}")
    return json.loads(done.stdout)


def run_cell(case: dict, model: str, call: Callable = call_claude) -> dict:
    started = time.monotonic()
    cell = {"model_id": model, "blocked": False, "error": None, "response_text": None, "checks": [], "cost_usd": None}
    try:
        reply = call(case["prompt"], model)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        cell["error"] = str(error)
        return cell
    if reply.get("is_error"):
        cell["error"] = str(reply.get("result"))[:300]
        return cell
    text = reply.get("result") or ""
    cell.update(
        response_text=text,
        latency_ms=round((time.monotonic() - started) * 1000),
        cost_usd=reply.get("total_cost_usd"),
        checks=[{"check": check, "passed": run_check(check, text)} for check in case["checks"]],
    )
    return cell


def run_all(cases: list, models: list, call: Callable = call_claude) -> dict:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {(case["name"], model): pool.submit(run_cell, case, model, call) for case in cases for model in models}
    return {
        "source": "claude-cli",
        "results": [
            {"test_case": case, "cells": {model: futures[(case["name"], model)].result() for model in models}}
            for case in cases
        ],
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    args = parser.parse_args(argv)

    run = run_all(json.loads(CASES.read_text()), args.models)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "latest.json"
    path.write_text(json.dumps(run, indent=1) + "\n")
    print(f"Saved {path.relative_to(ROOT)}. Next: python3 evals/score_results.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
