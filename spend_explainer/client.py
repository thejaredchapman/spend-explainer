"""Optional live HTTP calls. Nothing else in the package touches the network.

Both functions use only the standard library, so the demo and tests need no
install. Errors raise SpendClientError, and the CLI turns a model failure into
the deterministic fallback rather than stopping.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Tuple

API_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
ANALYTICS_BETA = "analytics-usage-cost-2026-08-24"
GROUP_BY = ("workspace_id", "api_key_id", "model")
MAX_SPAN_DAYS = 31  # the API's limit for a single request
PAGE_LIMIT = 1000
RESTART_LIMIT = 1
TIMEOUT_SECONDS = 60

DEFAULT_MODEL = "claude-haiku-5-5"
# Sent without temperature or prefill: Haiku 5.5 rejects both with a 400.
MAX_TOKENS = 4096


class SpendClientError(RuntimeError):
    pass


def _send(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


# ── Model call ───────────────────────────────────────────────────────────────
def extract_text(response: dict) -> str:
    """Join text blocks. Newer models can also return thinking blocks, so skip those."""
    return "".join(block.get("text", "") for block in response.get("content", []) if block.get("type") == "text")


def call_model(prompt: str, model: str = DEFAULT_MODEL) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SpendClientError("ANTHROPIC_API_KEY is not set.")
    body = {"model": model, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        f"{API_BASE}/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
    )
    try:
        return extract_text(_send(request))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise SpendClientError(f"Model call failed: {error}") from error


# ── Analytics fetch ──────────────────────────────────────────────────────────
def _analytics_url(endpoint: str, start: str, end: str, page: str = "") -> str:
    params: List[Tuple[str, str]] = [
        ("starting_at", start),
        ("ending_at", end),
        ("time_bucket_width", "1d"),
        ("limit", str(PAGE_LIMIT)),
        ("order_by", "time"),
    ]
    params += [("group_by[]", dimension) for dimension in GROUP_BY]
    if page:
        params.append(("page", page))
    return f"{API_BASE}/v1/organizations/analytics/{endpoint}?{urllib.parse.urlencode(params)}"


def _fetch_all_pages(endpoint: str, start: str, end: str, key: str, send: Callable) -> dict:
    """Follow next_page until done. A 410 means the cursor expired, so restart."""
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "anthropic-beta": ANALYTICS_BETA}

    for attempt in range(RESTART_LIMIT + 1):
        rows: List[dict] = []
        page = ""
        refreshed = None
        try:
            while True:
                request = urllib.request.Request(_analytics_url(endpoint, start, end, page), headers=headers)
                body = send(request)
                rows.extend(body.get("data", []))
                refreshed = body.get("data_refreshed_at") or refreshed
                if not body.get("has_more"):
                    return {"data": rows, "data_refreshed_at": refreshed, "has_more": False, "next_page": None}
                page = body["next_page"]
        except urllib.error.HTTPError as error:
            if error.code == 410 and attempt < RESTART_LIMIT:
                continue
            raise SpendClientError(f"{endpoint}: HTTP {error.code} {error.reason}") from error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise SpendClientError(f"{endpoint}: {error}") from error
    raise SpendClientError(f"{endpoint}: pagination cursor kept expiring.")


def fetch_analytics(start: str, end: str, send: Callable = _send) -> Dict[str, dict]:
    """Fetch both endpoints. Requires an Admin API key (or a read:analytics key)."""
    key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")
    if not key:
        raise SpendClientError("ANTHROPIC_ADMIN_API_KEY is not set.")
    first = datetime.fromisoformat(start.replace("Z", "+00:00"))
    last = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if last - first > timedelta(days=MAX_SPAN_DAYS):
        raise SpendClientError(f"The API allows at most {MAX_SPAN_DAYS} days per request.")
    return {endpoint: _fetch_all_pages(endpoint, start, end, key, send) for endpoint in ("usage", "cost")}
