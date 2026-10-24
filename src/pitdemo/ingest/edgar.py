"""SEC EDGAR ingest: as-reported diluted EPS and earnings-release dates.

Two public endpoints are used:

* ``companyfacts`` gives every XBRL fact a company has filed, one row per
  (period, filing). We keep **every** filing's value for ``EarningsPerShareDiluted``,
  not just the latest, so post-split restatements show up as separate rows.
* ``submissions`` gives the filing index with acceptance timestamps. We use it for
  8-K item 2.02 ("Results of Operations"), i.e. earnings releases, and to put an
  exact knowledge time on each 10-K / 10-Q / 8-K.

The raw responses are several MB each, so ``slim()`` keeps only what the demo uses
and that slim JSON is what gets committed under ``data/raw/edgar/``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from pitdemo.ids import SECURITIES, SecurityMaster
from pitdemo.ingest.upstream import CompanyFacts, EdgarSlim, SubmissionsColumns, parse_upstream
from pitdemo.periods import fiscal_label_for_period_end
from pitdemo.schemas import ActualsFrame, ReportEventsFrame

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
EPS_TAG = "EarningsPerShareDiluted"
KEEP_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "25-NSE", "15-12G"}
KEEP_FILINGS_SINCE = "2017-01-01"
DEFAULT_USER_AGENT = "pitdemo-research-demo contact@example.com"


def user_agent() -> str:
    """SEC asks for a descriptive User-Agent with a contact address.

    Set ``SEC_USER_AGENT="Your Name you@example.com"`` before running ingest.
    """
    return os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    for attempt in range(4):
        resp = client.get(url)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())
    resp.raise_for_status()
    raise RuntimeError(f"unreachable: {url}")


def fetch_companyfacts(client: httpx.Client, cik: int) -> dict[str, Any]:
    return _get_json(client, COMPANYFACTS_URL.format(cik=cik))


def fetch_submissions(client: httpx.Client, cik: int) -> list[dict[str, Any]]:
    """Return the filing index as a flat list of dicts, including overflow pages."""
    head = _get_json(client, SUBMISSIONS_URL.format(cik=cik))
    pages = [head["filings"]["recent"]]
    for extra in head["filings"].get("files", []):
        if extra.get("filingTo", "9999") < KEEP_FILINGS_SINCE:
            continue
        time.sleep(0.15)  # SEC fair-access limit is 10 req/s; stay well under it
        pages.append(_get_json(client, SUBMISSIONS_PAGE_URL.format(name=extra["name"])))
    out: list[dict[str, Any]] = []
    for n, page in enumerate(pages):
        parse_upstream(SubmissionsColumns, page, f"SEC filing index for CIK {cik} (page {n})")
        cols = list(page.keys())
        for i in range(len(page["accessionNumber"])):
            out.append({c: page[c][i] for c in cols})
    return out


def slim(cik: int, facts: dict[str, Any], filings: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep only diluted EPS facts and the filing index entries the demo needs."""
    parsed = parse_upstream(CompanyFacts, facts, f"SEC companyfacts for CIK {cik}")
    # Keep SEC's own JSON for each fact (validated above) so the committed file is faithful.
    eps_rows = facts["facts"]["us-gaap"][EPS_TAG]["units"]["USD/shares"]
    kept = [
        {
            "accession": f["accessionNumber"],
            "form": f["form"],
            "filing_date": f["filingDate"],
            "acceptance": f["acceptanceDateTime"],
            "items": f.get("items", ""),
        }
        for f in filings
        if f["form"] in KEEP_FORMS and f["filingDate"] >= KEEP_FILINGS_SINCE
    ]
    kept.sort(key=lambda r: (r["acceptance"], r["accession"]))
    return {
        "cik": cik,
        "entity_name": parsed.entityName,
        "sources": [COMPANYFACTS_URL.format(cik=cik), SUBMISSIONS_URL.format(cik=cik)],
        "tag": f"us-gaap:{EPS_TAG}",
        "eps_diluted": sorted(eps_rows, key=lambda r: (r["end"], r["filed"], r["accn"])),
        "filings": kept,
    }


def raw_path(raw_dir: Path, cik: int) -> Path:
    return raw_dir / "edgar" / f"CIK{cik:010d}.json"


def fetch_all(raw_dir: Path, securities: tuple[SecurityMaster, ...] = SECURITIES) -> list[Path]:
    """Download companyfacts + submissions for every security and write slim JSON."""
    written: list[Path] = []
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"}
    with httpx.Client(headers=headers, timeout=60.0) as client:
        for sec in securities:
            facts = fetch_companyfacts(client, sec.cik)
            time.sleep(0.15)
            filings = fetch_submissions(client, sec.cik)
            time.sleep(0.15)
            path = raw_path(raw_dir, sec.cik)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(slim(sec.cik, facts, filings), indent=1) + "\n")
            written.append(path)
    return written


def load_raw(raw_dir: Path, cik: int) -> dict[str, Any]:
    """Load a slim EDGAR file, checking it has the shape the parsers rely on."""
    path = raw_path(raw_dir, cik)
    data = cast(dict[str, Any], json.loads(path.read_text()))
    parse_upstream(EdgarSlim, data, str(path))
    return data


NEW_YORK = ZoneInfo("America/New_York")


def parse_acceptance(value: str) -> dt.datetime:
    """SEC ``acceptanceDateTime`` -> true UTC (naive).

    Despite the trailing ``Z``, the submissions API's value is *not* UTC: it is the
    true UTC instant shifted forward by the Eastern-time offset (+4h in summer, +5h
    in winter). Checked against filing headers, e.g. NVIDIA's 8-K
    0001045810-24-000113: header ``ACCEPTANCE-DATETIME 20240522162129`` (16:21:29 New
    York = 20:21:29 UTC), JSON ``2024-05-23T00:21:29.000Z``.

    So subtract the Eastern offset, choosing the one that is consistent with the
    resulting UTC instant (this also handles DST changeover days).
    """
    shifted = dt.datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)
    for hours in (4, 5):
        utc = shifted - dt.timedelta(hours=hours)
        offset = utc.replace(tzinfo=dt.UTC).astimezone(NEW_YORK).utcoffset()
        if offset == dt.timedelta(hours=-hours):
            return utc
    return shifted - dt.timedelta(hours=5)  # unreachable for real dates; be conservative


def end_of_day(day: dt.date) -> dt.datetime:
    """When only a date is known, assume the fact became public at the very end of it.

    This is the conservative choice for point-in-time work: a query on that date
    will not see it until the day is over, so it can never leak backwards.
    """
    return dt.datetime.combine(day, dt.time(23, 59, 59))


def acceptance_index(raw: dict[str, Any]) -> dict[str, dt.datetime]:
    return {f["accession"]: parse_acceptance(f["acceptance"]) for f in raw["filings"]}


def parse_actuals(raw: dict[str, Any], sec: SecurityMaster) -> pl.DataFrame:
    """Annual diluted EPS, one row per (fiscal period, filing).

    EDGAR's own ``fy`` field is the fiscal year of the *filing*, not of the period
    (the FY2023 10-K also carries FY2022 and FY2021 comparatives), so the period
    label is derived from the period end date instead.
    """
    accepted = acceptance_index(raw)
    rows: list[dict[str, Any]] = []
    for r in raw["eps_diluted"]:
        start = dt.date.fromisoformat(r["start"])
        end = dt.date.fromisoformat(r["end"])
        if not 350 <= (end - start).days <= 380:
            continue  # quarterly or year-to-date figure
        filed_day = dt.date.fromisoformat(r["filed"])
        rows.append(
            {
                "security_id": sec.security_id,
                "metric": "EPS",
                "fiscal_period": fiscal_label_for_period_end(end),
                "period_end": end,
                "value": float(r["val"]),
                "basis": "GAAP_DILUTED",
                "filed_at": accepted.get(r["accn"], end_of_day(filed_day)),
                "accession": r["accn"],
                "form": r["form"],
            }
        )
    df = pl.DataFrame(rows, schema=ActualsFrame.polars_schema()).unique(
        subset=["security_id", "fiscal_period", "accession"], keep="first", maintain_order=True
    )
    return ActualsFrame.validate(df.sort("fiscal_period", "filed_at"))


RELEASE_LOOKBACK = dt.timedelta(days=60)
SAME_DAY = dt.timedelta(days=1)  # release and 10-Q are sometimes accepted minutes apart


def parse_report_events(raw: dict[str, Any], sec: SecurityMaster) -> pl.DataFrame:
    """Earnings releases, one per periodic report.

    Item 2.02 ("Results of Operations") alone is not enough: Tesla, for example, also
    files its quarterly delivery numbers under 2.02. So for each 10-Q / 10-K we take
    the latest item-2.02 8-K accepted in the 60 days before it as that quarter's
    earnings release.
    """
    filings = raw["filings"]
    releases = sorted(
        (parse_acceptance(f["acceptance"]), f["accession"])
        for f in filings
        if f["form"] == "8-K" and "2.02" in f["items"].split(",")
    )
    periodic = sorted(
        parse_acceptance(f["acceptance"]) for f in filings if f["form"] in {"10-Q", "10-K"}
    )
    picked: dict[str, dt.datetime] = {}
    for filed in periodic:
        window = [r for r in releases if filed - RELEASE_LOOKBACK <= r[0] <= filed + SAME_DAY]
        if window:
            at, accession = window[-1]
            picked[accession] = at
    rows = [
        {
            "security_id": sec.security_id,
            "reported_at": at,
            "accession": accession,
            "source": "8-K item 2.02 (latest before 10-Q/10-K)",
        }
        for accession, at in picked.items()
    ]
    df = pl.DataFrame(rows, schema=ReportEventsFrame.polars_schema())
    return ReportEventsFrame.validate(df.sort("reported_at"))
