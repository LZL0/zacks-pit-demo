"""Per-estimate share-basis resolution.

Around a split, the date an estimate was *entered* does not tell you which share
count it is on. Brokers restate on their own schedule: some publish post-split
numbers a few days before the ex-date, others keep publishing pre-split numbers
for a week or two after it. Adjusting by entry date gets both groups wrong.

So each estimate gets its own basis decision, in this order:

1. **flagged**: the source supplied ``share_basis_date``. Use it.
2. **ratio_inferred**: the estimate was entered while a split was pending or
   recent. Compare it with the same broker's previous estimate on both candidate
   bases. If it is within 25% of the previous value on exactly one of them
   (closer on that one), use that basis.
3. **date_fallback**: no usable previous estimate. Fall back to entry date vs.
   ex-date and flag ``basis_inferred=True`` so consumers can see the guess.

Estimates entered nowhere near a split simply use their entry date (``entry_date``).
Resolution only looks at a broker's own earlier rows, so it is point-in-time safe.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from pitdemo.schemas import BasisSource
from pitdemo.transforms.factors import Factor, cumulative_factor, known_share_factors

WINDOW_AFTER_EX = dt.timedelta(days=30)
"""How long after an ex-date an unflagged estimate may still be on the old basis."""

MAX_LOG_DISTANCE = math.log(1.25)
"""A revision bigger than 25% is not used as evidence of basis."""


@dataclass(frozen=True)
class EstimateRow:
    estimate_id: str
    broker_id: str
    action: str
    value: float | None
    share_basis_date: dt.date | None
    entered_at: dt.datetime


@dataclass(frozen=True)
class BasisDecision:
    estimate_id: str
    basis_date: dt.date
    source: BasisSource
    inferred: bool
    event: Factor | None
    detail: str


def rows_from_frame(df: pl.DataFrame) -> list[EstimateRow]:
    return [
        EstimateRow(
            r["estimate_id"],
            r["broker_id"],
            r["action"],
            r["value"],
            r["share_basis_date"],
            r["entered_at"],
        )
        for r in df.iter_rows(named=True)
    ]


def pending_event(entered_at: dt.datetime, factors: Sequence[Factor]) -> Factor | None:
    """The split, if any, that makes this entry time ambiguous.

    Ambiguous = announced already, and the ex-date is in the future or less than
    ``WINDOW_AFTER_EX`` ago. Before the announcement nobody can be on the new basis.
    """
    hits = [
        f
        for f in factors
        if f.known_at <= entered_at and entered_at.date() <= f.ex_date + WINDOW_AFTER_EX
    ]
    return max(hits, key=lambda f: f.ex_date) if hits else None


def _log_distance(a: float, b: float) -> float | None:
    if a == 0 or b == 0 or (a > 0) != (b > 0):
        return None
    return abs(math.log(a / b))


def resolve_one(
    row: EstimateRow,
    prev: tuple[float, dt.date] | None,
    factors: Sequence[Factor],
) -> BasisDecision:
    """Basis decision for one row given the broker's previous (value, basis_date)."""
    if row.share_basis_date is not None:
        return BasisDecision(
            row.estimate_id,
            row.share_basis_date,
            "flagged",
            False,
            None,
            f"source flagged share basis {row.share_basis_date}",
        )
    entered = row.entered_at.date()
    event = pending_event(row.entered_at, factors)
    if event is None:
        return BasisDecision(
            row.estimate_id,
            entered,
            "entry_date",
            False,
            None,
            "no split pending or recent at entry; basis is the entry date",
        )
    pre_date = event.ex_date - dt.timedelta(days=1)
    post_date = event.ex_date
    split = f"{event.ratio:g}-for-1" if event.ratio >= 1 else f"1-for-{1 / event.ratio:g}"
    if prev is not None and row.value is not None:
        prev_value, prev_basis = prev
        on_pre = prev_value / cumulative_factor(factors, prev_basis, pre_date)
        on_post = prev_value / cumulative_factor(factors, prev_basis, post_date)
        d_pre = _log_distance(row.value, on_pre)
        d_post = _log_distance(row.value, on_post)
        if d_pre is not None and d_post is not None:
            best = min(d_pre, d_post)
            if best <= MAX_LOG_DISTANCE and d_pre != d_post:
                post = d_post < d_pre
                ref = on_post if post else on_pre
                side = "post-split" if post else "pre-split"
                return BasisDecision(
                    row.estimate_id,
                    post_date if post else pre_date,
                    "ratio_inferred",
                    False,
                    event,
                    f"{row.value:g} is closest to the broker's prior estimate on the {side} "
                    f"basis ({ref:.4g}) for the {split} split, ex-date {event.ex_date}",
                )
    post = entered >= event.ex_date
    return BasisDecision(
        row.estimate_id,
        post_date if post else pre_date,
        "date_fallback",
        True,
        event,
        f"no usable prior estimate; assumed {'post' if post else 'pre'}-split basis from entry "
        f"date {entered} vs ex-date {event.ex_date} ({split})",
    )


def resolve_bases(
    rows: Sequence[EstimateRow],
    factors: Sequence[Factor],
    known_as_of: dt.datetime | None = None,
) -> dict[str, BasisDecision]:
    """Resolve every row; ``rows`` may mix brokers and must be one security and period."""
    usable = known_share_factors(factors, known_as_of)
    by_broker: dict[str, list[EstimateRow]] = {}
    for r in rows:
        by_broker.setdefault(r.broker_id, []).append(r)
    out: dict[str, BasisDecision] = {}
    for chain in by_broker.values():
        chain.sort(key=lambda r: (r.entered_at, r.estimate_id))
        prev: tuple[float, dt.date] | None = None
        for r in chain:
            if r.action == "stop" or r.value is None:
                prev = None  # coverage dropped; a later re-initiation starts fresh
                continue
            decision = resolve_one(r, prev, usable)
            out[r.estimate_id] = decision
            prev = (r.value, decision.basis_date)
    return out
