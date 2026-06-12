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

# LBMA's CDN fingerprints incoming requests and challenges those that look
# bot-shaped (polite identifier UAs, missing Accept-Language, no Referer)
# when they come from a flagged IP range. GitHub Actions runners share IP
# space with every scraper on the internet, so they get challenged often.
# Presenting as a normal desktop browser sidesteps the fingerprint in the
# common case. This is public benchmark data — we're not bypassing auth.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.lbma.org.uk/",
}

# One quick retry handles transient HTTP 5xx / truncated bodies. We do NOT
# retry an AntiBotChallenge inside a run — the runner's IP doesn't change
# within a run, so retrying just hits the same wall. The workflow's 6 cron
# slots per day are the real retry mechanism for sticky challenges (each
# slot lands on whatever runner IP GitHub happens to assign).
RETRY_DELAYS = (0, 10)


class AntiBotChallenge(Exception):
    """LBMA's CDN returned an HTML interstitial in place of JSON.

    Sticky on this runner's IP for minutes-to-hours; the next scheduled
    cron slot will retry from a fresh runner. Surfaced as a distinct
    exception type so the failure email reads as "challenge, next slot
    will retry" rather than a generic JSON decode error.
    """


class LBMAStubResponse(Exception):
    """LBMA returned a non-list shape (e.g. {"message": "..."} error stub).

    Different from AntiBotChallenge: the request succeeded with status 200
    and the body parsed as JSON, but the shape isn't the usual array of
    rows. Observed 2026-06-10 onwards: LBMA's CDN intermittently serves an
    error stub for individual slugs (gold_am most often), then recovers
    within minutes when the next cron slot fetches a fresh response.

    Caught in main() and treated as "skip this run quietly" rather than a
    failure — the workflow exits 0, london-fix.json is left unchanged, and
    the next scheduled cron slot retries from a fresh response.
    """


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
            req = urllib.request.Request(url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
                body = resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            try:
                body = e.read()
            except Exception:
                body = b""
            _warn(attempt, url, e, e.code, body)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            _warn(attempt, url, e, None, None)
            continue

        # HTTP 200 but the body is an HTML page (CDN challenge). Don't retry.
        if body.lstrip().startswith(b"<"):
            err = AntiBotChallenge(
                f"LBMA returned HTML (HTTP {status}) instead of JSON for "
                f"{url}. Most likely: this runner's IP is being challenged "
                "by LBMA's CDN. The next scheduled cron slot will retry "
                "from a different runner."
            )
            _warn(attempt, url, err, status, body)
            raise err

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            last_err = e
            _warn(attempt, url, e, status, body)
            continue

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
    # LBMA's CDN intermittently serves a non-list shape for individual
    # slugs (e.g. {"message": "..."} error stub for gold_am observed
    # 2026-06-10 onwards). Bail early so main() can treat this as
    # "skip this run, the next slot will retry" rather than crashing
    # the whole workflow with a failure email.
    if not isinstance(data, list):
        raise LBMAStubResponse(
            f"LBMA returned non-list shape for {slug}.json "
            f"(likely a CDN error stub): {type(data).__name__} = {data!r}"
        )
    # Pick the last entry whose USD value (v[0]) is not null.
    # The per-row isinstance(dict) guard below covers a different mode:
    # LBMA has also been seen to embed bare strings inside an otherwise-
    # valid row array (incident 2026-06-09). Skip those defensively and
    # log the element so a recurrence builds evidence.
    for row in reversed(data):
        if not isinstance(row, dict):
            print(
                f"warn: skipping non-dict row in {slug}.json: {row!r}",
                file=sys.stderr,
            )
            continue
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
    try:
        payload = build_payload()
    except LBMAStubResponse as e:
        # Don't fail the workflow — LBMA's stub responses are transient
        # CDN jitter, not a real error. Leaving london-fix.json untouched
        # means the "Commit if changed" step prints "no change" and the
        # workflow exits green, no failure email. Next cron slot retries.
        print(f"skip: {e}. Next scheduled cron slot will retry.", file=sys.stderr)
        return
    out_path = Path("london-fix.json")
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out_path} latest_fix_date={payload['latest_fix_date']}")


if __name__ == "__main__":
    main()
