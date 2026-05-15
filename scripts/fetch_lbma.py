#!/usr/bin/env python3
"""Fetch the most recent LBMA Fix prices and write london-fix.json.

LBMA publishes daily auction results at prices.lbma.org.uk/json/<slug>.json.
Each file is a JSON array of [{"is_cms_locked": int, "d": "YYYY-MM-DD", "v": [USD, GBP, EUR]}].
Position 0 of the "v" array is the USD price per troy ounce.

We pick the most recent row for each metal/fix and compose one summary file
that the prismx.com /metal-rates page consumes.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LBMA_BASE = "https://prices.lbma.org.uk/json"
FIXES = {
    "gold_am":      ("gold",      "am"),
    "gold_pm":      ("gold",      "pm"),
    "silver":       ("silver",    "noon"),
    "platinum_am":  ("platinum",  "am"),
    "platinum_pm":  ("platinum",  "pm"),
    "palladium_am": ("palladium", "am"),
    "palladium_pm": ("palladium", "pm"),
}

USER_AGENT = "prismx-lbma-fix/1.0 (+https://github.com/ashishkamdar/prismx-lbma-fix)"

# LBMA's static JSON occasionally returns non-JSON during brief upstream blips,
# which crashes a single sweep even though the next one would succeed.
# Three attempts, ~20s cap, well under the workflow's per-job budget.
RETRY_DELAYS = (0, 5, 15)


def fetch_json(url: str) -> object:
    """GET *url* and parse JSON, retrying on transient HTTP/JSON failures.

    Diagnostics are written to stderr so they surface in the GitHub Actions
    log only — never into london-fix.json or any file the website reads.
    """
    last_err: Exception | None = None
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        body: bytes | None = None
        status: int | None = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
                body = resp.read()
            return json.loads(body)
        except urllib.error.HTTPError as e:
            last_err = e
            try:
                body = e.read()
            except Exception:
                body = b""
            _warn(attempt, url, e, e.code, body)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            _warn(attempt, url, e, None, None)
        except json.JSONDecodeError as e:
            last_err = e
            _warn(attempt, url, e, status, body)
    assert last_err is not None
    raise last_err


def _warn(attempt: int, url: str, err: Exception, status: int | None, body: bytes | None) -> None:
    n = len(RETRY_DELAYS)
    print(f"warn: attempt {attempt}/{n} for {url} failed: {type(err).__name__}: {err}", file=sys.stderr)
    if status is not None:
        print(f"  http status: {status}", file=sys.stderr)
    if body:
        snippet = body[:200].decode("utf-8", errors="replace")
        print(f"  body[:200]: {snippet!r}", file=sys.stderr)


def latest_row(slug: str) -> dict:
    data = fetch_json(f"{LBMA_BASE}/{slug}.json")
    # Pick the last entry whose USD value (v[0]) is not null.
    for row in reversed(data):
        v = row.get("v") or []
        if v and v[0] is not None:
            return {"d": row["d"], "usd": v[0]}
    raise RuntimeError(f"no usable rows in {slug}.json")


def build_payload() -> dict:
    fixes: dict[str, dict] = {}
    latest_date = None
    for slug, (metal, window) in FIXES.items():
        row = latest_row(slug)
        fixes.setdefault(metal, {})[window] = {"usd": row["usd"], "date": row["d"]}
        if latest_date is None or row["d"] > latest_date:
            latest_date = row["d"]
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_fix_date": latest_date,
        "currency": "USD",
        "unit": "troy_ounce",
        "source": "LBMA (prices.lbma.org.uk)",
        "fixes": fixes,
    }


def main() -> None:
    payload = build_payload()
    out_path = Path("london-fix.json")
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out_path} latest_fix_date={payload['latest_fix_date']}")


if __name__ == "__main__":
    main()
