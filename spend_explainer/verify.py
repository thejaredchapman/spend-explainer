"""The reliability check: is this model answer safe to show?

Every check is plain code, so it costs nothing and gives the same result each
run. Any failure rejects the whole answer, because a report that is mostly right
but states one wrong dollar figure is still a wrong report.

What this does NOT prove: that a correct number is attached to the right claim,
or that the recommended action is any good. The EvalForge rubric covers those
qualitative parts offline. See docs/ARCHITECTURE.md.
"""

import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set

from .analyze import ALL_CAUSES, STATUS_NONE, STATUS_SPIKE, Analysis

REQUIRED_FIELDS = ("status", "primary_cause", "driver", "headline", "explanation", "recommended_action")
PROSE_FIELDS = ("headline", "explanation", "recommended_action")
UNIT_SUFFIXES = ("_usd", "_pct")

# ── Patterns ─────────────────────────────────────────────────────────────────
ID_PATTERN = re.compile(r"\b(?:wrkspc|apikey)_\w+|\bclaude-[a-z0-9][a-z0-9.-]*[a-z0-9]")
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?\b")
NUMBER = r"\d[\d,]*(?:\.\d+)?"
MONEY_PATTERN = re.compile(rf"\$\s?({NUMBER})")
PERCENT_PATTERN = re.compile(rf"({NUMBER})\s?(?:%|percent\b)")
RATIO_PATTERN = re.compile(rf"({NUMBER})\s?(?:x\b|×|times\b)")
BARE_PATTERN = re.compile(rf"(?<![\w.])({NUMBER})(?![\w])")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Verification:
    checks: tuple
    answer: Optional[dict]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> List[Check]:
        return [check for check in self.checks if not check.passed]


# ── Which numbers are allowed ────────────────────────────────────────────────
def _walk(value, key=""):
    """Yield (key, number) for every number nested anywhere in the facts."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield key, float(value)
    elif isinstance(value, dict):
        for child_key, child in value.items():
            # Maps such as daily_spend_usd are keyed by date, so the unit is on the parent.
            yield from _walk(child, key if key.endswith(UNIT_SUFFIXES) else str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)


def _allowed(facts: dict):
    """Sort known numbers by kind, so "$12" cannot be justified by "12%"."""
    money, percent, ratio, plain = set(), set(), set(), set()
    for key, value in _walk(facts):
        # Compare magnitudes: "fell by $0.53" is a fair way to state -0.53, and
        # the conclusion check already covers whether spend rose or fell.
        number = abs(value)
        if key.endswith("_usd"):
            money.add(number)
        elif key.endswith("_pct"):
            percent.add(number)
        elif key.endswith("ratio"):
            ratio.add(number)
        else:
            plain.add(number)
    return money, percent, ratio, plain


def _matches(token: str, known: Iterable[float]) -> bool:
    """A token matches a fact if rounding the fact to the token's precision gives the token."""
    text = token.replace(",", "")
    decimals = len(text.split(".")[1]) if "." in text else 0
    value = float(text)
    return any(round(number, decimals) == value for number in known)


def _prose(answer: dict) -> str:
    return " ".join(str(answer.get(field, "")) for field in PROSE_FIELDS)


def _unsupported_numbers(text: str, facts: dict) -> List[str]:
    money, percent, ratio, plain = _allowed(facts)
    text = DATE_PATTERN.sub(" ", ID_PATTERN.sub(" ", text))  # digits inside IDs are not claims
    bad = []
    for pattern, known, label in (
        (MONEY_PATTERN, money, "$"),
        (PERCENT_PATTERN, percent, "%"),
        (RATIO_PATTERN, ratio, "x"),
    ):
        for token in pattern.findall(text):
            if not _matches(token, known):
                bad.append(f"{label}{token}")
        text = pattern.sub(" ", text)  # consumed, so the bare pass does not re-check it
    for token in BARE_PATTERN.findall(text):
        if not _matches(token, money | percent | ratio | plain):
            bad.append(token)
    return bad


def _known_ids(facts: dict) -> Set[str]:
    return {
        item["value"]
        for items in facts["by_dimension"].values()
        for item in items
    }


# ── Checks ───────────────────────────────────────────────────────────────────
def _check_schema(answer: dict) -> Check:
    missing = [field for field in REQUIRED_FIELDS if field not in answer]
    if missing:
        return Check("schema", False, f"missing fields: {', '.join(missing)}")
    if answer["status"] not in (STATUS_SPIKE, STATUS_NONE):
        return Check("schema", False, f"unknown status {answer['status']!r}")
    if answer["primary_cause"] not in ALL_CAUSES:
        return Check("schema", False, f"unknown cause {answer['primary_cause']!r}")
    driver = answer["driver"]
    if driver is not None and not (isinstance(driver, dict) and "dimension" in driver and "value" in driver):
        return Check("schema", False, "driver must be null or have dimension and value")
    return Check("schema", True, "all fields present and valid")


def _check_conclusion(answer: dict, analysis: Analysis) -> Check:
    expected = (analysis.verdict.status, analysis.verdict.primary_cause)
    actual = (answer["status"], answer["primary_cause"])
    if actual != expected:
        return Check("conclusion", False, f"answer says {actual}, the data says {expected}")
    return Check("conclusion", True, f"matches the rules: {expected[0]}, {expected[1]}")


def _check_driver(answer: dict, analysis: Analysis) -> Check:
    driver = answer["driver"]
    accepted = analysis.verdict.accepted_drivers
    if not accepted:
        if driver is not None:
            return Check("driver", False, f"answer names {driver['value']}, but no single driver exists")
        return Check("driver", True, "correctly names no driver")
    if driver is None:
        return Check("driver", False, f"answer names no driver; expected one of {list(accepted)}")
    if driver["value"] not in accepted:
        return Check("driver", False, f"answer names {driver['value']}; expected one of {list(accepted)}")
    return Check("driver", True, f"{driver['value']} is the top contributor")


def _check_ids(answer: dict, analysis: Analysis) -> Check:
    known = _known_ids(analysis.facts)
    invented = sorted(set(ID_PATTERN.findall(_prose(answer))) - known)
    if invented:
        return Check("ids", False, f"not in the data: {', '.join(invented)}")
    return Check("ids", True, "every ID in the prose exists in the data")


def _check_numbers(answer: dict, analysis: Analysis) -> Check:
    bad = _unsupported_numbers(_prose(answer), analysis.facts)
    if bad:
        return Check("numbers", False, f"not found in the data: {', '.join(bad)}")
    return Check("numbers", True, "every number traces back to the data")


def verify(raw_text: str, analysis: Analysis) -> Verification:
    """Run all checks. Later checks need valid JSON, so a parse failure stops early."""
    try:
        answer = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as error:
        return Verification((Check("json", False, f"not valid JSON: {error}"),), None)
    if not isinstance(answer, dict):
        return Verification((Check("json", False, "top level must be an object"),), None)

    json_check = Check("json", True, "parsed")
    schema = _check_schema(answer)
    if not schema.passed:
        return Verification((json_check, schema), None)

    checks = (
        json_check,
        schema,
        _check_conclusion(answer, analysis),
        _check_driver(answer, analysis),
        _check_ids(answer, analysis),
        _check_numbers(answer, analysis),
    )
    return Verification(checks, answer)
