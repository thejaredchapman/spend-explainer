#!/usr/bin/env python3
"""
spend-explainer — explain a Claude spend spike, with a safety net.

No key needed:
  python3 explain_spend.py demo                       walk through one scenario
  python3 explain_spend.py demo --all                 every scenario and bad answer
  python3 explain_spend.py analyze --scenario quiet   print the computed facts
  python3 explain_spend.py prompt  --scenario quiet   print the model prompt
  python3 explain_spend.py verify  --scenario quiet --answer FILE
  python3 explain_spend.py project --scenario quiet --days 30

Keys needed:
  python3 explain_spend.py run --scenario quiet       ANTHROPIC_API_KEY
  python3 explain_spend.py fetch --start ... --end ... --out DIR
                                                      ANTHROPIC_ADMIN_API_KEY
"""

import argparse
import json
import sys
from pathlib import Path

from spend_explainer import client
from spend_explainer.analytics import load_dataset
from spend_explainer.analyze import DEFAULT_WINDOW_DAYS, InsufficientData, analyze
from spend_explainer.pipeline import SOURCE_MODEL, Result, decide
from spend_explainer.prompt import build_prompt

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
RECORDED = ROOT / "recorded"
DEMO_SCENARIO = "cache_regression"
DEMO_ANSWERS = ("good", "wrong_number")

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_USAGE = 2


def scenario_names():
    return sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())


def load_analysis(args):
    directory = Path(args.data) if args.data else FIXTURES / args.scenario
    return analyze(load_dataset(directory), args.window_days)


# ── Printing ─────────────────────────────────────────────────────────────────
def print_report(result: Result) -> None:
    report = result.report
    print(f"  shown from : {result.source}")
    print(f"  status     : {report['status']} ({report['primary_cause']})")
    if report["driver"]:
        print(f"  driver     : {report['driver']['value']}")
    print(f"  headline   : {report['headline']}")
    print(f"  why        : {report['explanation']}")
    print(f"  next step  : {report['recommended_action']}")


def print_checks(result: Result, details: bool = True) -> None:
    for check in result.verification.checks:
        mark = "pass" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name:<11}{check.detail if details else ''}".rstrip())


def walk_through(scenario: str, window_days: int) -> None:
    """Print each pipeline stage, so a reader can see what happens and why."""
    analysis = analyze(load_dataset(FIXTURES / scenario), window_days)
    facts, verdict = analysis.facts, analysis.verdict
    totals, period = facts["totals"], facts["period"]

    print(f"\nSCENARIO: {scenario} (synthetic data)\n")

    print("STEP 1  Parse the API data (spend_explainer/analytics.py)")
    print(f"  dropped incomplete days : {', '.join(facts['excluded_incomplete_days']) or 'none'}")
    print(f"  data_refreshed_at       : {facts['data_refreshed_at']}\n")

    print("STEP 2  Analyze with rules, no model (spend_explainer/analyze.py)")
    print(f"  baseline {period['baseline_start']} to {period['baseline_end']}: ${totals['baseline_daily_usd']:,.2f}/day (median)")
    print(f"  window   {period['window_start']} to {period['window_end']}: ${totals['window_daily_usd']:,.2f}/day (mean)")
    print(f"  rules conclude: {verdict.status}, {verdict.primary_cause}, driver {verdict.driver_value}")
    print("  This conclusion is withheld from the model.\n")

    prompt = build_prompt(facts)
    print("STEP 3  Build the prompt (spend_explainer/prompt.py)")
    print(f"  {len(prompt):,} characters. See it with: python3 explain_spend.py prompt --scenario {scenario}\n")

    for name in DEMO_ANSWERS:
        result = decide(analysis, (RECORDED / scenario / f"{name}.txt").read_text())
        print(f"STEP 4  Model answer arrives: recorded/{scenario}/{name}.txt (hand-made stand-in)")
        print("        Verify it (spend_explainer/verify.py)")
        print_checks(result)
        print("\nSTEP 5  Decide what to show (spend_explainer/pipeline.py)")
        print_report(result)
        print()


def summary_table(window_days: int) -> int:
    """Run every stand-in answer through the checks and confirm the expected outcome."""
    print(f"{'scenario':<18}{'answer':<15}{'shown from':<25}failed check")
    mistakes = 0
    for scenario in scenario_names():
        analysis = analyze(load_dataset(FIXTURES / scenario), window_days)
        for path in sorted((RECORDED / scenario).glob("*.txt")):
            result = decide(analysis, path.read_text())
            failed = ", ".join(check.name for check in result.verification.failures) or "-"
            print(f"{scenario:<18}{path.stem:<15}{result.source:<25}{failed}")
            mistakes += (result.source == SOURCE_MODEL) != (path.stem == "good")
    print(f"\nUnexpected outcomes: {mistakes}")
    return EXIT_REJECTED if mistakes else EXIT_OK


# ── Commands ─────────────────────────────────────────────────────────────────
def cmd_demo(args) -> int:
    if args.all:
        return summary_table(args.window_days)
    walk_through(args.scenario or DEMO_SCENARIO, args.window_days)
    print("Next: python3 explain_spend.py demo --all")
    return EXIT_OK


def cmd_analyze(args) -> int:
    analysis = load_analysis(args)
    print(json.dumps({"facts": analysis.facts, "verdict": analysis.verdict.__dict__}, indent=2))
    return EXIT_OK


def cmd_prompt(args) -> int:
    print(build_prompt(load_analysis(args).facts))
    return EXIT_OK


def cmd_verify(args) -> int:
    """Used by the skill. Exit 0 if the answer is shown, 1 if the fallback is."""
    result = decide(load_analysis(args), Path(args.answer).read_text())
    # --summary hides check details, which can quote the rejected values.
    print_checks(result, details=not args.summary)
    print()
    print_report(result)
    return EXIT_OK if result.source == SOURCE_MODEL else EXIT_REJECTED


def cmd_project(args) -> int:
    """Project the increase forward with code, so nobody does this arithmetic by hand."""
    facts = load_analysis(args).facts
    extra_per_day = facts["totals"]["increase_daily_usd"]
    period = facts["period"]
    print(f"  extra per day : ${extra_per_day:,.2f} (window mean minus baseline median)")
    print(f"  over {args.days} days  : ${round(extra_per_day * args.days, 2):,.2f}")
    print(f"  assumes       : the {period['window_start']} to {period['window_end']} level holds for all {args.days} days")
    print("  This is a straight-line projection, not a forecast or a verified fact.")
    return EXIT_OK


def cmd_run(args) -> int:
    """Ask a real model. Any failure still produces a usable report."""
    analysis = load_analysis(args)
    try:
        raw = client.call_model(build_prompt(analysis.facts), args.model)
    except client.SpendClientError as error:
        print(f"  model unavailable: {error}\n", file=sys.stderr)
        raw = None
    result = decide(analysis, raw)
    if result.verification:
        print_checks(result)
        print()
    print_report(result)
    return EXIT_OK


def cmd_fetch(args) -> int:
    try:
        responses = client.fetch_analytics(args.start, args.end)
    except client.SpendClientError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_REJECTED
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, body in responses.items():
        (out / f"{name}.json").write_text(json.dumps(body, indent=1) + "\n")
    print(f"Wrote {out}/usage.json and {out}/cost.json")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain a Claude spend spike, with a safety net.")
    commands = parser.add_subparsers(dest="command", required=True)

    def add_source(sub):
        group = sub.add_mutually_exclusive_group(required=True)
        group.add_argument("--scenario", choices=scenario_names(), help="a bundled synthetic scenario")
        group.add_argument("--data", help="a directory holding usage.json and cost.json")
        sub.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)

    demo = commands.add_parser("demo", help="walk through the pipeline with no key")
    demo.add_argument("--scenario", choices=scenario_names())
    demo.add_argument("--all", action="store_true", help="summarize every scenario and bad answer")
    demo.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    demo.set_defaults(handler=cmd_demo)

    analyze_cmd = commands.add_parser("analyze", help="print facts and the rules' verdict")
    add_source(analyze_cmd)
    analyze_cmd.set_defaults(handler=cmd_analyze)

    prompt = commands.add_parser("prompt", help="print the model prompt")
    add_source(prompt)
    prompt.set_defaults(handler=cmd_prompt)

    verify = commands.add_parser("verify", help="check an answer file, and fall back if it fails")
    add_source(verify)
    verify.add_argument("--answer", required=True)
    verify.add_argument("--summary", action="store_true", help="name failed checks without their details")
    verify.set_defaults(handler=cmd_verify)

    project = commands.add_parser("project", help="project the daily increase over a period")
    add_source(project)
    project.add_argument("--days", type=int, default=30)
    project.set_defaults(handler=cmd_project)

    run = commands.add_parser("run", help="ask a real model (needs ANTHROPIC_API_KEY)")
    add_source(run)
    run.add_argument("--model", default=client.DEFAULT_MODEL)
    run.set_defaults(handler=cmd_run)

    fetch = commands.add_parser("fetch", help="download real data (needs ANTHROPIC_ADMIN_API_KEY)")
    fetch.add_argument("--start", required=True, help="RFC 3339, e.g. 2026-10-01T00:00:00Z")
    fetch.add_argument("--end", required=True, help="RFC 3339, exclusive, at most 31 days after start")
    fetch.add_argument("--out", required=True)
    fetch.set_defaults(handler=cmd_fetch)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except InsufficientData as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
