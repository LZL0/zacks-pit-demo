"""Cumulative share-count adjustment factors, aware of knowledge time.

Conventions:

* A value's **basis date** ``b`` is the date whose share count it is expressed on.
  A split with ex-date ``e`` is part of basis ``b`` when ``e <= b``.
* ``cumulative_factor(b, T)`` is the product of split ratios with ``b < e <= T``.
  Restating a per-share value from ``b`` to ``T`` divides by it. When ``T`` is before
  ``b`` the factor is the reciprocal, so restating backwards also works.
* **Backward adjustment.** The latest basis is the reference: values published
  today are never changed; history is restated to today's share count.
* **Knowledge time.** Only actions with ``known_at <= known_as_of`` apply, so a query
  can reproduce what a system would have done before a split was announced.
* Only share-count actions (splits, reverse splits) produce factors. A spin-off
  changes what the company *is*, not how many shares there are, so it has no
  factor for EPS (see ``consensus.py`` for how it is handled).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import polars as pl

from pitdemo.schemas import SHARE_COUNT_ACTIONS, AdjustedEPS, CorporateAction, UnadjustedEPS

LATEST = dt.date.max
"""Target basis meaning "the latest share basis known"."""


@dataclass(frozen=True)
class Factor:
    security_id: str
    ticker: str
    action_type: str
    ex_date: dt.date
    ratio: float
    known_at: dt.datetime
    record_date: dt.date | None
    terms: str
    source_url: str
    notes: str | None

    @property
    def is_share_count(self) -> bool:
        return self.action_type in SHARE_COUNT_ACTIONS

    def to_model(self) -> CorporateAction:
        return CorporateAction(
            security_id=self.security_id,
            ticker=self.ticker,
            action_type=self.action_type,
            known_at=self.known_at,
            record_date=self.record_date,
            ex_date=self.ex_date,
            ratio=self.ratio,
            terms=self.terms,
            source_url=self.source_url,
            notes=self.notes,
        )


def factors_from_frame(df: pl.DataFrame) -> list[Factor]:
    return [
        Factor(
            security_id=r["security_id"],
            ticker=r["ticker"],
            action_type=r["action_type"],
            ex_date=r["ex_date"],
            ratio=float(r["ratio"]),
            known_at=r["known_at"],
            record_date=r["record_date"],
            terms=r["terms"],
            source_url=r["source_url"],
            notes=r["notes"],
        )
        for r in df.iter_rows(named=True)
    ]


def known_share_factors(factors: Iterable[Factor], known_as_of: dt.datetime | None) -> list[Factor]:
    return [
        f
        for f in factors
        if f.is_share_count and (known_as_of is None or f.known_at <= known_as_of)
    ]


def factors_between(
    factors: Iterable[Factor],
    from_date: dt.date,
    to_date: dt.date,
    known_as_of: dt.datetime | None = None,
) -> list[Factor]:
    """Share-count actions that separate basis ``from_date`` from basis ``to_date``."""
    lo, hi = sorted((from_date, to_date))
    return sorted(
        (f for f in known_share_factors(factors, known_as_of) if lo < f.ex_date <= hi),
        key=lambda f: f.ex_date,
    )


def cumulative_factor(
    factors: Sequence[Factor],
    from_date: dt.date,
    to_date: dt.date,
    known_as_of: dt.datetime | None = None,
) -> float:
    """Factor that restates a per-share value from basis ``from_date`` to ``to_date``."""
    product = math.prod(f.ratio for f in factors_between(factors, from_date, to_date, known_as_of))
    return product if to_date >= from_date else 1.0 / product


def adjust(value: UnadjustedEPS, factor: float) -> AdjustedEPS:
    """Restate a per-share value onto the target basis."""
    return AdjustedEPS(value / factor)


def unadjust(value: AdjustedEPS, factor: float) -> UnadjustedEPS:
    """Inverse of ``adjust``: back to the share basis the value was published on."""
    return UnadjustedEPS(value * factor)
