"""The fallback path: a plain report built only from the deterministic analysis.

It is less readable than a good model answer, but it cannot invent anything,
because every value comes straight from facts and verdict.
"""

from .analyze import (
    CAUSE_BROAD,
    CAUSE_CACHE,
    CAUSE_MODEL_MIX,
    CAUSE_NONE,
    CAUSE_UNCLASSIFIED,
    CAUSE_VOLUME,
    Analysis,
)

CAUSE_TEXT = {
    CAUSE_MODEL_MIX: "A more expensive model took over a larger share of this workspace's cost.",
    CAUSE_CACHE: "Prompt cache hits fell, so tokens that were cheap cache reads are now billed as full input.",
    CAUSE_VOLUME: "Request volume rose sharply.",
    CAUSE_BROAD: "Spend rose across workspaces, with no single workspace behind at least half of it.",
    CAUSE_UNCLASSIFIED: "One workspace drove the rise, but it matches no known pattern.",
    CAUSE_NONE: "Spend is within its normal range.",
}

CAUSE_ACTION = {
    CAUSE_MODEL_MIX: "Check for a recent config or deploy that changed the model in this workspace.",
    CAUSE_CACHE: "Check for a recent change to the prompt prefix, such as a timestamp or reordered tools.",
    CAUSE_VOLUME: "Confirm whether this key's new traffic is expected, and look for a retry loop if it is not.",
    CAUSE_BROAD: "Compare against product growth before treating this as a problem.",
    CAUSE_UNCLASSIFIED: "Review this workspace's usage by hand.",
    CAUSE_NONE: "No action needed.",
}


def build_report(analysis: Analysis) -> dict:
    """Return the same fields as a model answer, so callers can treat both alike."""
    verdict = analysis.verdict
    totals = analysis.facts["totals"]
    period = analysis.facts["period"]
    baseline, window = totals["baseline_daily_usd"], totals["window_daily_usd"]

    driver = None
    driver_text = ""
    if verdict.driver_value:
        driver = {"dimension": verdict.driver_dimension, "value": verdict.driver_value}
        driver_text = f" Driver: {verdict.driver_value}."

    return {
        "status": verdict.status,
        "primary_cause": verdict.primary_cause,
        "driver": driver,
        "headline": f"Daily spend went from ${baseline:,.2f} to ${window:,.2f} ({totals['ratio']}x).",
        "explanation": (
            f"Baseline {period['baseline_start']} to {period['baseline_end']}, "
            f"window {period['window_start']} to {period['window_end']}. "
            f"{CAUSE_TEXT[verdict.primary_cause]}{driver_text}"
        ),
        "recommended_action": CAUSE_ACTION[verdict.primary_cause],
    }
