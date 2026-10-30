"""Parse responses from the Usage and Cost Analytics API into plain daily rows.

Three API behaviours are easy to get wrong, and each is handled here:

1. `cost.amount` is a decimal string in CENTS: "41280.000000" is $412.80.
2. Data later than `data_refreshed_at` is incomplete, so a partial day must
   be dropped or it will look like a sudden fall in spend.
3. A group with no usage on a day has no row at all, rather than a zero row.
   analyze.py fills those gaps with zeros.

Docs: https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Code execution rows carry model: null, so nulls need a printable label.
NONE_LABEL = "(none)"


@dataclass(frozen=True)
class DayRow:
    """One group's activity for one day, joined from /usage and /cost."""

    day: str  # YYYY-MM-DD, the bucket's UTC start
    workspace_id: str
    api_key_id: str
    model: str
    cost_usd: Decimal = Decimal(0)
    requests: int = 0
    uncached_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Dataset:
    rows: Tuple[DayRow, ...]
    data_refreshed_at: Optional[str]
    dropped_days: Tuple[str, ...]  # days excluded for passing the watermark


def cents_to_usd(amount: str) -> Decimal:
    """Convert the API's cent string to dollars. Decimal avoids float drift in money."""
    return Decimal(amount) / 100


def _parse_time(value: str) -> datetime:
    # datetime.fromisoformat() rejects a trailing Z before Python 3.11.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _key(row: dict) -> Tuple[str, str, str, str]:
    group = row["group"]
    return (
        row["starting_at"][:10],
        group.get("workspace_id") or NONE_LABEL,
        group.get("api_key_id") or NONE_LABEL,
        group.get("model") or NONE_LABEL,
    )


def _is_complete(row: dict, watermark: Optional[datetime]) -> bool:
    return watermark is None or _parse_time(row["ending_at"]) <= watermark


def parse_dataset(usage: dict, cost: dict) -> Dataset:
    """Join one /usage response and one /cost response on day and group."""
    refreshed = usage.get("data_refreshed_at") or cost.get("data_refreshed_at")
    watermark = _parse_time(refreshed) if refreshed else None

    merged: Dict[Tuple[str, str, str, str], dict] = {}
    dropped = set()

    for row in usage.get("data", []):
        key = _key(row)
        if not _is_complete(row, watermark):
            dropped.add(key[0])
            continue
        tokens = row["usage"]
        cache_write = tokens.get("cache_creation") or {}
        merged.setdefault(key, {}).update(
            requests=tokens.get("requests") or 0,
            uncached_input_tokens=tokens.get("uncached_input_tokens") or 0,
            cache_write_tokens=(cache_write.get("ephemeral_5m_input_tokens") or 0)
            + (cache_write.get("ephemeral_1h_input_tokens") or 0),
            cache_read_tokens=tokens.get("cache_read_input_tokens") or 0,
            output_tokens=tokens.get("output_tokens") or 0,
        )

    for row in cost.get("data", []):
        key = _key(row)
        if not _is_complete(row, watermark):
            dropped.add(key[0])
            continue
        merged.setdefault(key, {})["cost_usd"] = cents_to_usd(row["cost"]["amount"])

    rows = tuple(
        DayRow(day=key[0], workspace_id=key[1], api_key_id=key[2], model=key[3], **fields)
        for key, fields in sorted(merged.items())
    )
    return Dataset(rows=rows, data_refreshed_at=refreshed, dropped_days=tuple(sorted(dropped)))


def load_dataset(directory: Path) -> Dataset:
    """Load usage.json and cost.json from a fixture or `fetch` output directory."""
    usage = json.loads((directory / "usage.json").read_text())
    cost = json.loads((directory / "cost.json").read_text())
    return parse_dataset(usage, cost)


def days_in(rows: List[DayRow]) -> List[str]:
    return sorted({row.day for row in rows})
