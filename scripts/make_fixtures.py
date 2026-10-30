#!/usr/bin/env python3
"""Generate the synthetic data and the stand-in model answers.

Output is fully determined by SEED, so rerunning gives identical files. All IDs,
teams and traffic are invented. Prices come from the public price list, so the
dollar amounts are realistic, but nothing here describes a real organization.

Each scenario changes traffic only in the final 3 complete days. Every fixture
also ends with a partial day past the data_refreshed_at watermark, which must be
excluded or it would look like a sudden fall.
"""

import json
import random
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spend_explainer import FIXTURES, RECORDED  # noqa: E402
from spend_explainer.analytics import load_dataset  # noqa: E402
from spend_explainer.analyze import analyze  # noqa: E402
from spend_explainer.fallback import build_report  # noqa: E402

SEED = 20261030
START = date(2026, 10, 1)
BASELINE_DAYS = 14
WINDOW_DAYS = 3
NOISE = 0.05
PARTIAL_DAY_FRACTION = 0.15

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5"

# USD per million tokens, from the Claude pricing page.
PRICES = {
    OPUS: {"input": 4.00, "output": 20.00, "write": 5.00, "read": 0.20},
    SONNET: {"input": 2.00, "output": 10.00, "write": 2.50, "read": 0.20},
    HAIKU: {"input": 1.00, "output": 5.00, "write": 1.25, "read": 0.10},
}

SUPPORT = "wrkspc_01SYNTHSUPPORT"
SEARCH = "wrkspc_01SYNTHSEARCH"
RESEARCH = "wrkspc_01SYNTHRESEARCH"
SUPPORT_KEY = "apikey_01SYNTHSUPPORTBOT"
SEARCH_KEY_A = "apikey_01SYNTHSEARCHWEB"
SEARCH_KEY_B = "apikey_01SYNTHSEARCHJOBS"
RESEARCH_KEY = "apikey_01SYNTHRESEARCHLAB"


@dataclass(frozen=True)
class Stream:
    """One steady source of traffic, with per-request token counts."""

    workspace: str
    key: str
    model: str
    requests: int
    uncached: int
    cache_read: int
    cache_write: int
    output: int


NORMAL = (
    Stream(SUPPORT, SUPPORT_KEY, SONNET, 20000, 600, 5000, 100, 300),
    Stream(SEARCH, SEARCH_KEY_A, HAIKU, 30000, 800, 3000, 50, 200),
    Stream(SEARCH, SEARCH_KEY_B, HAIKU, 8000, 800, 3000, 50, 200),
    Stream(RESEARCH, RESEARCH_KEY, OPUS, 1500, 4000, 20000, 500, 1500),
)


# ── Scenarios: each changes traffic on selected days ─────────────────────────
def quiet(stream, day_index):
    return stream


def one_day_outlier(stream, day_index):
    """A single expensive day in the baseline. The median must absorb it."""
    if day_index == 6 and stream.key == RESEARCH_KEY:
        return replace(stream, requests=stream.requests * 8)
    return stream


def model_mix_shift(stream, day_index):
    if day_index >= BASELINE_DAYS and stream.key == SEARCH_KEY_A:
        return replace(stream, model=OPUS)
    return stream


def cache_regression(stream, day_index):
    if day_index >= BASELINE_DAYS and stream.key == SUPPORT_KEY:
        moved = int(stream.cache_read * 0.9)
        return replace(stream, cache_read=stream.cache_read - moved, uncached=stream.uncached + moved)
    return stream


def volume_increase(stream, day_index):
    if day_index >= BASELINE_DAYS and stream.key == SEARCH_KEY_B:
        return replace(stream, requests=stream.requests * 12)
    return stream


def broad_growth(stream, day_index):
    if day_index >= BASELINE_DAYS:
        return replace(stream, requests=int(stream.requests * 1.8))
    return stream


SCENARIOS = {
    "quiet": quiet,
    "one_day_outlier": one_day_outlier,
    "model_mix_shift": model_mix_shift,
    "cache_regression": cache_regression,
    "volume_increase": volume_increase,
    "broad_growth": broad_growth,
}


# ── API-shaped rows ──────────────────────────────────────────────────────────
def _timestamp(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


def _group(stream: Stream) -> dict:
    """Dimensions not in group_by[] are null in real responses, so a few are shown as null."""
    return {
        "workspace_id": stream.workspace,
        "api_key_id": stream.key,
        "model": stream.model,
        "actor": None,
        "product": None,
        "service_tier": None,
    }


def _cost_cents(stream: Stream, requests: int) -> Decimal:
    price = PRICES[stream.model]
    dollars = (
        requests * stream.uncached * price["input"]
        + requests * stream.cache_read * price["read"]
        + requests * stream.cache_write * price["write"]
        + requests * stream.output * price["output"]
    ) / 1_000_000
    return Decimal(str(round(dollars * 100, 6)))


def build_rows(scenario, rng: random.Random):
    usage_rows, cost_rows = [], []
    total_days = BASELINE_DAYS + WINDOW_DAYS + 1

    for day_index in range(total_days):
        day = START + timedelta(days=day_index)
        partial = day_index == total_days - 1
        for base in NORMAL:
            stream = scenario(base, day_index)
            requests = int(stream.requests * rng.uniform(1 - NOISE, 1 + NOISE))
            if partial:
                requests = int(requests * PARTIAL_DAY_FRACTION)
            window = {"starting_at": _timestamp(day), "ending_at": _timestamp(day + timedelta(days=1))}
            usage_rows.append({
                **window,
                "group": _group(stream),
                "usage": {
                    "uncached_input_tokens": requests * stream.uncached,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 0,
                        "ephemeral_5m_input_tokens": requests * stream.cache_write,
                    },
                    "cache_read_input_tokens": requests * stream.cache_read,
                    "output_tokens": requests * stream.output,
                    "server_tool_use": {"web_search_requests": 0},
                    "total_tokens": requests
                    * (stream.uncached + stream.cache_write + stream.cache_read + stream.output),
                    "requests": requests,
                },
            })
            cost_rows.append({
                **window,
                "group": _group(stream),
                "cost": {
                    # Cents, as a string with six decimals, exactly as the API returns it.
                    "amount": f"{_cost_cents(stream, requests):.6f}",
                    "currency": "USD",
                    "list_amount": f"{_cost_cents(stream, requests):.6f}",
                    "requests": requests,
                },
            })
    return usage_rows, cost_rows


def write_fixture(directory: Path, scenario) -> None:
    rng = random.Random(f"{SEED}:{directory.name}")  # stable per scenario
    usage_rows, cost_rows = build_rows(scenario, rng)
    # The watermark falls inside the last day, making that day incomplete.
    last_day = START + timedelta(days=BASELINE_DAYS + WINDOW_DAYS)
    meta = {"data_refreshed_at": f"{last_day.isoformat()}T04:00:00Z", "has_more": False, "next_page": None}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "usage.json").write_text(json.dumps({"data": usage_rows, **meta}, indent=1) + "\n")
    (directory / "cost.json").write_text(json.dumps({"data": cost_rows, **meta}, indent=1) + "\n")


# ── Stand-in answers ─────────────────────────────────────────────────────────
def good_answer(analysis) -> dict:
    """A correct answer in the model's format, written from the facts.

    This is a stand-in for offline demos, NOT real model output.
    """
    report = build_report(analysis)
    verdict = analysis.verdict
    total = analysis.facts["totals"]
    report["explanation"] = (
        f"Daily spend moved from ${total['baseline_daily_usd']:.2f} to ${total['window_daily_usd']:.2f} "
        f"between {analysis.facts['period']['window_start']} and {analysis.facts['period']['window_end']}. "
        + report["explanation"].split(". ", 1)[-1]
    )
    if verdict.driver_value:
        report["driver"] = {"dimension": verdict.driver_dimension, "value": verdict.driver_value}
    return report


def write_recorded(directory: Path, analysis) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    good = good_answer(analysis)
    (directory / "good.txt").write_text(json.dumps(good, indent=2) + "\n")

    window = f"{analysis.facts['totals']['window_daily_usd']:.2f}"

    wrong_number = dict(good)
    wrong_number["explanation"] = good["explanation"].replace(window, "9999.99")
    (directory / "wrong_number.txt").write_text(json.dumps(wrong_number, indent=2) + "\n")

    wrong_cause = dict(good)
    wrong_cause["primary_cause"] = "volume_increase" if good["primary_cause"] != "volume_increase" else "cache_regression"
    (directory / "wrong_cause.txt").write_text(json.dumps(wrong_cause, indent=2) + "\n")

    invented_id = dict(good)
    invented_id["recommended_action"] = "Rotate apikey_01MADEUP right away."
    (directory / "invented_id.txt").write_text(json.dumps(invented_id, indent=2) + "\n")

    (directory / "fenced_json.txt").write_text("```json\n" + json.dumps(good, indent=2) + "\n```\n")


def main() -> None:
    for name, scenario in SCENARIOS.items():
        fixture = FIXTURES / name
        write_fixture(fixture, scenario)
        analysis = analyze(load_dataset(fixture))
        write_recorded(RECORDED / name, analysis)
        (fixture / "expected.json").write_text(json.dumps({
            "status": analysis.verdict.status,
            "primary_cause": analysis.verdict.primary_cause,
            "driver": analysis.verdict.driver_value,
            "accepted_drivers": list(analysis.verdict.accepted_drivers),
        }, indent=2) + "\n")
        totals = analysis.facts["totals"]
        print(
            f"{name:18} ${totals['baseline_daily_usd']:>8.2f} -> ${totals['window_daily_usd']:>8.2f}"
            f"  {analysis.verdict.status:9} {analysis.verdict.primary_cause:18} {analysis.verdict.driver_value}"
        )


if __name__ == "__main__":
    main()
