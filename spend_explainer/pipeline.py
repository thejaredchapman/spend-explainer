"""The one decision the package exists to make: show the model's answer or the fallback."""

from dataclasses import dataclass
from typing import Optional

from .analyze import Analysis
from .fallback import build_report
from .verify import Verification, verify

SOURCE_MODEL = "model (verified)"
SOURCE_FALLBACK = "deterministic fallback"


@dataclass(frozen=True)
class Result:
    report: dict
    source: str
    analysis: Analysis
    verification: Optional[Verification]  # None if no model answer was given


def decide(analysis: Analysis, raw_answer: Optional[str]) -> Result:
    """Show the model's answer only if it passes every check."""
    if raw_answer is None:
        return Result(build_report(analysis), SOURCE_FALLBACK, analysis, None)
    verification = verify(raw_answer, analysis)
    if verification.passed:
        return Result(verification.answer, SOURCE_MODEL, analysis, verification)
    return Result(build_report(analysis), SOURCE_FALLBACK, analysis, verification)
