"""Builders for small, hand-made stores whose answers can be computed by hand."""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterable, Sequence
from typing import Any

import polars as pl

from pitdemo.ids import security_ids_frame
from pitdemo.schemas import CorporateActionsFrame, EstimatesFrame, ReportEventsFrame
from pitdemo.store import Store

_ids = itertools.count(1)

NVDA = "SEC-0001"

NVDA_SPLIT: dict[str, Any] = {
    "security_id": NVDA,
    "ticker": "NVDA",
    "action_type": "SPLIT",
    "known_at": dt.datetime(2024, 5, 22, 20, 21, 29),  # 8-K header: 16:21:29 New York
    "record_date": dt.date(2024, 6, 6),
    "ex_date": dt.date(2024, 6, 10),
    "ratio": 10.0,
    "terms": "10-for-1 forward split",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000113/q1fy25pr.htm",
    "notes": None,
}


def ts(day: str, hour: int = 15) -> dt.datetime:
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hour))


def est(
    broker: str,
    value: float | None,
    entered: str | dt.datetime,
    *,
    period: str = "FY2025",
    security_id: str = NVDA,
    basis: str | None = None,
    action: str | None = None,
    estimate_id: str | None = None,
) -> dict[str, Any]:
    when = ts(entered) if isinstance(entered, str) else entered
    return {
        "estimate_id": estimate_id or f"T-{next(_ids):06d}",
        "security_id": security_id,
        "broker_id": broker,
        "metric": "EPS",
        "fiscal_period": period,
        "action": action or ("stop" if value is None else "estimate"),
        "value": value,
        "share_basis_date": dt.date.fromisoformat(basis) if basis else None,
        "entered_at": when,
        "source": "SYNTHETIC",
    }


def report(security_id: str, at: dt.datetime, n: int) -> dict[str, Any]:
    return {
        "security_id": security_id,
        "reported_at": at,
        "accession": f"0000000000-24-{n:06d}",
        "source": "test",
    }


def make_store(
    estimates: Iterable[dict[str, Any]],
    actions: Sequence[dict[str, Any]] = (),
    reports: Sequence[tuple[str, dt.datetime]] = (),
) -> Store:
    store = Store()
    store.insert_security_ids(security_ids_frame())
    store.insert_estimates(pl.DataFrame(list(estimates), schema=EstimatesFrame.polars_schema()))
    if actions:
        store.insert_corporate_actions(
            pl.DataFrame(list(actions), schema=CorporateActionsFrame.polars_schema())
        )
    if reports:
        store.insert_report_events(
            pl.DataFrame(
                [report(sid, at, i) for i, (sid, at) in enumerate(reports, 1)],
                schema=ReportEventsFrame.polars_schema(),
            )
        )
    return store
