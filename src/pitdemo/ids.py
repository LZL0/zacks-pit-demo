"""Permanent security identifiers and point-in-time ticker mapping.

Tickers are not identifiers: they get reused, changed and retired. Every table in
the store keys on a permanent ``security_id`` and tickers are resolved *as of* a
date. SVB Financial is the example here: ``SIVB`` on Nasdaq until trading was
suspended, then ``SIVBQ`` over the counter, then formally delisted. Its history
stays in the store; it just stops appearing in universe queries.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from pitdemo.periods import FiscalYearEnd
from pitdemo.schemas import SecurityIdsFrame

D = dt.date


@dataclass(frozen=True)
class SecurityMaster:
    security_id: str
    name: str
    cik: int
    fye: FiscalYearEnd


@dataclass(frozen=True)
class TickerSpan:
    """``ticker`` refers to ``security_id`` from ``valid_from`` to ``valid_to`` inclusive.

    ``None`` bounds are open-ended (``valid_from=None``: before the demo's coverage).
    """

    security_id: str
    ticker: str
    valid_from: dt.date | None
    valid_to: dt.date | None
    delisted_on: dt.date | None = None
    delist_reason: str | None = None

    def covers(self, day: dt.date) -> bool:
        return (self.valid_from is None or self.valid_from <= day) and (
            self.valid_to is None or day <= self.valid_to
        )


SECURITIES: tuple[SecurityMaster, ...] = (
    SecurityMaster("SEC-0001", "NVIDIA Corp", 1045810, FiscalYearEnd(month=1, weekday=6)),
    SecurityMaster("SEC-0002", "Apple Inc.", 320193, FiscalYearEnd(month=9, weekday=5)),
    SecurityMaster("SEC-0003", "Tesla, Inc.", 1318605, FiscalYearEnd(month=12)),
    SecurityMaster("SEC-0004", "General Electric Co", 40545, FiscalYearEnd(month=12)),
    SecurityMaster("SEC-0005", "SVB Financial Group", 719739, FiscalYearEnd(month=12)),
)

_SIVB_DELIST = (
    "Bank closed by regulators 2023-03-10; Chapter 11 filed 2023-03-17; Nasdaq suspended "
    "trading 2023-03-28 (then OTC as SIVBQ); Form 25-NSE delisting effective 2023-05-12"
)

TICKER_SPANS: tuple[TickerSpan, ...] = (
    TickerSpan("SEC-0001", "NVDA", None, None),
    TickerSpan("SEC-0002", "AAPL", None, None),
    TickerSpan("SEC-0003", "TSLA", None, None),
    TickerSpan("SEC-0004", "GE", None, None),
    TickerSpan("SEC-0005", "SIVB", None, D(2023, 3, 27), D(2023, 5, 12), _SIVB_DELIST),
    TickerSpan("SEC-0005", "SIVBQ", D(2023, 3, 28), D(2024, 11, 7), D(2023, 5, 12), _SIVB_DELIST),
)


class UnknownTickerError(LookupError):
    """No security carried this ticker on the requested date."""


def master(security_id: str) -> SecurityMaster:
    for sec in SECURITIES:
        if sec.security_id == security_id:
            return sec
    raise KeyError(security_id)


def by_ticker_today(ticker: str) -> SecurityMaster:
    """Convenience for scripts: the security that most recently used ``ticker``."""
    spans = [s for s in TICKER_SPANS if s.ticker == ticker.upper()]
    if not spans:
        raise UnknownTickerError(ticker)
    return master(spans[-1].security_id)


def resolve_ticker(spans: Sequence[TickerSpan], ticker: str, as_of: dt.date) -> TickerSpan:
    """Map a ticker to the security that carried it on ``as_of``."""
    t = ticker.strip().upper()
    hits = [s for s in spans if s.ticker == t and s.covers(as_of)]
    if hits:
        return hits[0]
    used = [s for s in spans if s.ticker == t]
    if not used:
        raise UnknownTickerError(f"ticker {t!r} is not in this dataset")
    hints = ", ".join(
        f"{s.security_id} from {s.valid_from or 'start of coverage'} to {s.valid_to or 'today'}"
        for s in used
    )
    raise UnknownTickerError(f"ticker {t!r} was not in use on {as_of}; it mapped to {hints}")


def ticker_as_of(spans: Sequence[TickerSpan], security_id: str, as_of: dt.date) -> str | None:
    for s in spans:
        if s.security_id == security_id and s.covers(as_of):
            return s.ticker
    return None


def universe(spans: Sequence[TickerSpan], as_of: dt.date) -> list[tuple[str, str]]:
    """Listed securities on ``as_of`` as (security_id, ticker), delisted names excluded."""
    out = [
        (s.security_id, s.ticker)
        for s in spans
        if s.covers(as_of) and (s.delisted_on is None or as_of < s.delisted_on)
    ]
    return sorted(set(out))


def security_ids_frame(
    spans: Sequence[TickerSpan] = TICKER_SPANS,
    securities: Sequence[SecurityMaster] = SECURITIES,
) -> pl.DataFrame:
    by_id = {s.security_id: s for s in securities}
    rows = [
        {
            "security_id": s.security_id,
            "ticker": s.ticker,
            "cik": f"{by_id[s.security_id].cik:010d}",
            "name": by_id[s.security_id].name,
            "valid_from": s.valid_from,
            "valid_to": s.valid_to,
            "delisted_on": s.delisted_on,
            "delist_reason": s.delist_reason,
        }
        for s in spans
    ]
    return SecurityIdsFrame.validate(pl.DataFrame(rows, schema=SecurityIdsFrame.polars_schema()))


def spans_from_frame(df: pl.DataFrame) -> list[TickerSpan]:
    return [
        TickerSpan(
            r["security_id"],
            r["ticker"],
            r["valid_from"],
            r["valid_to"],
            r["delisted_on"],
            r["delist_reason"],
        )
        for r in df.iter_rows(named=True)
    ]
