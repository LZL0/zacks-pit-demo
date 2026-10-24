"""Re-verify the real data sources against what is committed. Needs network.

Run weekly by .github/workflows/verify-sources.yml. Fetches fresh copies into a
temporary directory (the committed data/raw is never modified) and checks:

1. **SEC data still parses**: the fresh EDGAR responses pass the same Pandera
   validation as ingest.
2. **History is unchanged**: every committed EPS fact and filing is still present
   in SEC's data with the same values and acceptance times. New filings are fine
   and are listed; changed or vanished ones fail the run, because point-in-time
   history depends on them.
3. **Splits still cross-check**: prices up to today vs. corporate_actions.csv (a
   new split or spin-off without a CSV row fails).
4. **Primary sources resolve**: every source URL in corporate_actions.csv.

    SEC_USER_AGENT="Name you@example.com" uv run python scripts/ci/verify_sources.py
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from pitdemo.ids import SECURITIES, TICKER_SPANS
from pitdemo.ingest.actions import load_corporate_actions
from pitdemo.ingest.edgar import fetch_all, load_raw, parse_actuals, parse_report_events
from pitdemo.ingest.prices import cross_check_splits, fetch_prices

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
URL_RE = re.compile(r"https://[^\s\"')]+")


def _key(row: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(f) for f in fields)


def compare_history(old: dict[str, Any], new: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(problems, notes): committed rows must survive unchanged in the fresh data."""
    problems: list[str] = []
    notes: list[str] = []
    name = old["entity_name"]
    eps_fields = ("start", "end", "val", "accn", "form", "filed")
    new_eps = {_key(r, eps_fields) for r in new["eps_diluted"]}
    old_eps = {_key(r, eps_fields) for r in old["eps_diluted"]}
    for missing in sorted(old_eps - new_eps, key=str):
        problems.append(f"{name}: EPS fact changed or removed upstream: {missing}")
    filing_fields = ("accession", "form", "acceptance", "items")
    new_filings = {_key(f, filing_fields) for f in new["filings"]}
    old_filings = {_key(f, filing_fields) for f in old["filings"]}
    for missing in sorted(old_filings - new_filings, key=str):
        problems.append(f"{name}: filing changed or removed upstream: {missing}")
    added_eps = len(new_eps - old_eps)
    added_filings = len(new_filings - old_filings)
    if added_eps or added_filings:
        notes.append(f"{name}: {added_eps} new EPS facts, {added_filings} new filings since commit")
    return problems, notes


def check_urls(urls: list[str], user_agent: str) -> list[str]:
    problems: list[str] = []
    with httpx.Client(headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True) as c:
        for url in urls:
            try:
                status = c.get(url).status_code
            except httpx.HTTPError as exc:
                problems.append(f"source URL unreachable: {url} ({exc.__class__.__name__})")
                continue
            if status != 200:
                problems.append(f"source URL returned HTTP {status}: {url}")
            time.sleep(0.2)  # SEC fair-access limit
    return problems


def main() -> int:
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if not user_agent:
        print("SEC_USER_AGENT is not set (SEC asks for 'Name email').", file=sys.stderr)
        return 2
    problems: list[str] = []
    notes: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        fetch_all(fresh, SECURITIES)
        for sec in SECURITIES:
            old, new = load_raw(RAW, sec.cik), load_raw(fresh, sec.cik)
            try:
                parse_actuals(new, sec)
                parse_report_events(new, sec)
            except Exception as exc:  # validation errors of any kind fail the run
                problems.append(f"{sec.name}: fresh SEC data no longer validates: {exc}")
            p, n = compare_history(old, new)
            problems += p
            notes += n

        actions = load_corporate_actions(RAW / "corporate_actions.csv")
        tickers = {s.ticker: s.security_id for s in TICKER_SPANS if s.ticker != "SIVBQ"}
        start, end = dt.date(2020, 1, 1), dt.datetime.now(dt.UTC).date()
        fetched = fetch_prices(fresh / "prices", tickers, start, end)
        check = cross_check_splits(actions, fetched, start, end)
        problems += check.errors
        notes += check.notes

    urls = sorted(
        {
            u.rstrip(".,;")
            for r in actions.iter_rows(named=True)
            for u in [r["source_url"], *URL_RE.findall(r["notes"] or "")]
        }
    )
    problems += check_urls(urls, user_agent)
    notes.append(f"checked {len(urls)} primary-source URLs")

    lines = ["## Source verification", ""]
    lines += [f"- :x: {p}" for p in problems] or ["- :white_check_mark: no problems"]
    lines += ["", "### Notes", ""] + [f"- {n}" for n in notes]
    report = "\n".join(lines) + "\n"
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
