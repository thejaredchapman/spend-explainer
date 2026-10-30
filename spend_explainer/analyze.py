"""Deterministic spike analysis. No model runs here.

This module is the package's source of truth. It produces:

  - facts:   every number the model may quote (shown to the model)
  - verdict: the status, cause and driver the rules reached (hidden from the
             model, so the eval measures whether it reaches them unaided)

The method, in order:

  1. Split the days into a baseline and a recent window (the last 3 days).
  2. Compare the window's daily average with the baseline's daily MEDIAN.
     The median means one odd day in the baseline cannot distort it.
  3. Find the workspace responsible for most of the increase. If none
     accounts for at least half, the rise is broad rather than one team's.
  4. Inside that workspace, test for causes from most to least specific.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional

from .analytics import Dataset, DayRow, days_in

# ── Thresholds ───────────────────────────────────────────────────────────────
SPIKE_RATIO = 1.5  # window average must reach 1.5x the baseline
MIN_INCREASE_USD = 25.0  # ignore proportionally large but trivial increases
DRIVER_SHARE = 0.5  # one workspace must own half of the increase
VOLUME_RATIO = 1.5  # requests up 1.5x means more traffic, not costlier traffic
MIX_SHIFT = 0.30  # one model's cost share moved 30 points
CACHE_DROP = 0.20  # cache hit rate fell 20 points

DEFAULT_WINDOW_DAYS = 3
MIN_BASELINE_DAYS = 3

STATUS_SPIKE = "spike"
STATUS_NONE = "no_spike"

CAUSE_MODEL_MIX = "model_mix_shift"
CAUSE_CACHE = "cache_regression"
CAUSE_VOLUME = "volume_increase"
CAUSE_BROAD = "broad_growth"
CAUSE_UNCLASSIFIED = "unclassified"
CAUSE_NONE = "none"

ALL_CAUSES = (CAUSE_MODEL_MIX, CAUSE_CACHE, CAUSE_VOLUME, CAUSE_BROAD, CAUSE_UNCLASSIFIED, CAUSE_NONE)


@dataclass(frozen=True)
class Verdict:
    status: str
    primary_cause: str
    driver_dimension: Optional[str]
    driver_value: Optional[str]
    # Equivalent right answers: the key behind a one-key workspace is also correct.
    accepted_drivers: tuple


@dataclass(frozen=True)
class Analysis:
    facts: dict
    verdict: Verdict


class InsufficientData(ValueError):
    """Too few complete days to compare a baseline with a window."""


def _usd(value) -> float:
    return round(float(value), 2)


def _daily_series(rows: List[DayRow], days: List[str], field: str) -> Dict[str, Decimal]:
    totals = {day: Decimal(0) for day in days}  # zero-fill: quiet days have no row
    for row in rows:
        if row.day in totals:
            totals[row.day] += Decimal(getattr(row, field))
    return totals


def _baseline_vs_window(rows, baseline_days, window_days, field="cost_usd"):
    series = _daily_series(rows, baseline_days + window_days, field)
    baseline = median(float(series[day]) for day in baseline_days)
    window = sum(float(series[day]) for day in window_days) / len(window_days)
    return baseline, window


def _rank_dimension(rows, dimension, baseline_days, window_days) -> List[dict]:
    """Rank one dimension's values by how much each added to daily spend."""
    groups = defaultdict(list)
    for row in rows:
        groups[getattr(row, dimension)].append(row)

    ranked = []
    for value, group_rows in groups.items():
        baseline, window = _baseline_vs_window(group_rows, baseline_days, window_days)
        ranked.append({"value": value, "baseline": baseline, "window": window, "delta": window - baseline})

    increase = sum(item["delta"] for item in ranked if item["delta"] > 0)
    ranked.sort(key=lambda item: (-item["delta"], item["value"]))  # the value breaks ties stably
    return [
        {
            "value": item["value"],
            "baseline_daily_usd": _usd(item["baseline"]),
            "window_daily_usd": _usd(item["window"]),
            "delta_usd": _usd(item["delta"]),
            "share_of_increase_pct": round(100 * max(item["delta"], 0) / increase, 1) if increase else 0.0,
        }
        for item in ranked
    ]


def _cache_hit_pct(rows: List[DayRow]) -> float:
    read = sum(row.cache_read_tokens for row in rows)
    total = read + sum(row.uncached_input_tokens + row.cache_write_tokens for row in rows)
    return round(100 * read / total, 1) if total else 0.0


def _model_shares(rows: List[DayRow]) -> Dict[str, float]:
    total = sum(row.cost_usd for row in rows)
    if not total:
        return {}
    shares = defaultdict(Decimal)
    for row in rows:
        shares[row.model] += row.cost_usd
    return {model: round(float(100 * cost / total), 1) for model, cost in sorted(shares.items())}


def _workspace_profile(rows, baseline_days, window_days) -> dict:
    """Per-workspace behaviour before and during the window, for all workspaces.

    Every workspace is profiled, not only the driver, so the prompt does not
    reveal which one the rules picked.
    """
    baseline_rows = [row for row in rows if row.day in baseline_days]
    window_rows = [row for row in rows if row.day in window_days]
    baseline_requests, window_requests = _baseline_vs_window(rows, baseline_days, window_days, "requests")
    return {
        "baseline_requests_per_day": round(baseline_requests),
        "window_requests_per_day": round(window_requests),
        "requests_ratio": round(window_requests / baseline_requests, 2) if baseline_requests else 0.0,
        "baseline_cache_hit_pct": _cache_hit_pct(baseline_rows),
        "window_cache_hit_pct": _cache_hit_pct(window_rows),
        "baseline_model_cost_share_pct": _model_shares(baseline_rows),
        "window_model_cost_share_pct": _model_shares(window_rows),
    }


def _classify(profile: dict) -> str:
    """Name the cause inside the driving workspace, most specific test first."""
    if profile["requests_ratio"] >= VOLUME_RATIO:
        return CAUSE_VOLUME

    before = profile["baseline_model_cost_share_pct"]
    after = profile["window_model_cost_share_pct"]
    shifts = [abs(after.get(model, 0.0) - before.get(model, 0.0)) for model in set(before) | set(after)]
    if shifts and max(shifts) >= MIX_SHIFT * 100:
        return CAUSE_MODEL_MIX

    if profile["baseline_cache_hit_pct"] - profile["window_cache_hit_pct"] >= CACHE_DROP * 100:
        return CAUSE_CACHE

    return CAUSE_UNCLASSIFIED


def _decide(rows, totals, workspaces, profiles, baseline_days, window_days) -> Verdict:
    increase = totals["window_daily_usd"] - totals["baseline_daily_usd"]
    is_spike = (
        totals["baseline_daily_usd"] > 0
        and totals["window_daily_usd"] >= SPIKE_RATIO * totals["baseline_daily_usd"]
        and increase >= MIN_INCREASE_USD
    )
    if not is_spike:
        return Verdict(STATUS_NONE, CAUSE_NONE, None, None, ())

    top = workspaces[0]
    if top["share_of_increase_pct"] < DRIVER_SHARE * 100:
        return Verdict(STATUS_SPIKE, CAUSE_BROAD, None, None, ())

    workspace = top["value"]
    cause = _classify(profiles[workspace])
    accepted = [workspace]

    workspace_rows = [row for row in rows if row.workspace_id == workspace]
    top_key = _rank_dimension(workspace_rows, "api_key_id", baseline_days, window_days)[0]
    if top_key["share_of_increase_pct"] >= DRIVER_SHARE * 100:
        accepted.append(top_key["value"])

    if cause == CAUSE_VOLUME:
        # Traffic growth is best pinned on the key that sent it.
        return Verdict(STATUS_SPIKE, cause, "api_key_id", top_key["value"], tuple(accepted))

    if cause == CAUSE_MODEL_MIX:
        top_model = _rank_dimension(workspace_rows, "model", baseline_days, window_days)[0]
        accepted.append(top_model["value"])

    return Verdict(STATUS_SPIKE, cause, "workspace_id", workspace, tuple(accepted))


def analyze(dataset: Dataset, window_days: int = DEFAULT_WINDOW_DAYS) -> Analysis:
    rows = list(dataset.rows)
    days = days_in(rows)
    if len(days) < window_days + MIN_BASELINE_DAYS:
        raise InsufficientData(
            f"Need at least {window_days + MIN_BASELINE_DAYS} complete days, found {len(days)}."
        )

    baseline_days, recent_days = days[:-window_days], days[-window_days:]
    daily = _daily_series(rows, days, "cost_usd")
    baseline, window = _baseline_vs_window(rows, baseline_days, recent_days)

    totals = {
        "baseline_daily_usd": _usd(baseline),
        "window_daily_usd": _usd(window),
        "increase_daily_usd": _usd(window - baseline),
        "ratio": round(window / baseline, 2) if baseline else 0.0,
        "percent_change_pct": round(100 * (window - baseline) / baseline, 1) if baseline else 0.0,
    }
    by_dimension = {
        dimension: _rank_dimension(rows, dimension, baseline_days, recent_days)
        for dimension in ("workspace_id", "api_key_id", "model")
    }
    profiles = {
        item["value"]: _workspace_profile(
            [row for row in rows if row.workspace_id == item["value"]], baseline_days, recent_days
        )
        for item in by_dimension["workspace_id"]
    }

    facts = {
        "period": {
            "baseline_start": baseline_days[0],
            "baseline_end": baseline_days[-1],
            "window_start": recent_days[0],
            "window_end": recent_days[-1],
            "baseline_day_count": len(baseline_days),
            "window_day_count": len(recent_days),
        },
        "data_refreshed_at": dataset.data_refreshed_at,
        "excluded_incomplete_days": list(dataset.dropped_days),
        # Included so an answer may cite the rule it applied.
        "thresholds": {
            "spike_ratio": SPIKE_RATIO,
            "min_increase_usd": MIN_INCREASE_USD,
            "driver_share_pct": DRIVER_SHARE * 100,
            "volume_ratio": VOLUME_RATIO,
            # Named so they do not spell out a cause, which would leak the answer.
            "model_share_move_pct": MIX_SHIFT * 100,
            "cache_hit_fall_pct": CACHE_DROP * 100,
        },
        "totals": totals,
        "daily_spend_usd": {day: _usd(amount) for day, amount in daily.items()},
        "by_dimension": by_dimension,
        "workspace_profiles": profiles,
    }
    verdict = _decide(rows, totals, by_dimension["workspace_id"], profiles, baseline_days, recent_days)
    return Analysis(facts=facts, verdict=verdict)
