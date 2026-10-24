"""Per-estimate share-basis resolution.

Around a split, the date an estimate was *entered* does not tell you which share
count it is on. Brokers restate on their own schedule: some publish post-split
numbers a few days before the ex-date, others keep publishing pre-split numbers
for a week or two after it. Adjusting by entry date gets both groups wrong.

So each estimate gets its own basis decision, in this order:

1. **flagged**: the source supplied ``share_basis_date``. Use it.
2. **ratio_inferred**: the estimate was entered while one or more splits were
   pending or recent. Restate the same broker's previous estimate onto each
   candidate share regime (before the first pending split, after each one) and
   pick the regime it is closest to, provided the revision is under 25% *and*
   under 40% of the gap between neighbouring regimes (so a 3-for-2 split is not
   confused with an ordinary revision). If the previous estimate's basis was
   itself a guess, the new decision is flagged as a guess too.
3. **date_fallback**: no conclusive previous estimate. Use the regime in effect on
   the entry date and flag ``basis_inferred=True`` so consumers can see the guess.

Estimates entered nowhere near a split simply use their entry date (``entry_date``).
Resolution only looks at a broker's own earlier rows, so it is point-in-time safe.
"""

from __future__ import annotations

import datetime as dt
import itertools
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


def pending_events(entered_at: dt.datetime, factors: Sequence[Factor]) -> list[Factor]:
    """Splits that make this entry time ambiguous, oldest first.

    Ambiguous = announced already, and the ex-date is in the future or less than
    ``WINDOW_AFTER_EX`` ago. Before the announcement nobody can be on the new basis.
    """
    return sorted(
        (
            f
            for f in factors
            if f.known_at <= entered_at and entered_at.date() <= f.ex_date + WINDOW_AFTER_EX
        ),
        key=lambda f: f.ex_date,
    )


def _log_distance(a: float, b: float) -> float | None:
    if a == 0 or b == 0 or (a > 0) != (b > 0):
        return None
    return abs(math.log(a / b))


def _split_label(f: Factor) -> str:
    return f"{f.ratio:g}-for-1" if f.ratio >= 1 else f"1-for-{1 / f.ratio:g}"


def _candidates(events: Sequence[Factor]) -> list[tuple[dt.date, str]]:
    """One candidate basis per share regime: before the first pending split, then after
    each one. Returned as (basis_date, description)."""
    first = (events[0].ex_date - dt.timedelta(days=1), "pre-split")
    if len(events) == 1:
        return [first, (events[0].ex_date, "post-split")]
    return [first, *((f.ex_date, f"after the {f.ex_date} split") for f in events)]


def resolve_one(
    row: EstimateRow,
    prev: tuple[float, dt.date, bool] | None,
    factors: Sequence[Factor],
) -> BasisDecision:
    """Basis decision for one row given the broker's previous (value, basis_date, guessed)."""
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
    events = pending_events(row.entered_at, factors)
    if not events:
        return BasisDecision(
            row.estimate_id,
            entered,
            "entry_date",
            False,
            None,
            "no split pending or recent at entry; basis is the entry date",
        )
    latest = events[-1]
    splits = ", ".join(f"{_split_label(f)} ex {f.ex_date}" for f in events)
    candidates = _candidates(events)
    if prev is not None and row.value is not None:
        prev_value, prev_basis, prev_guessed = prev
        scored: list[tuple[float, dt.date, str, float]] = []
        for basis_date, side in candidates:
            ref = prev_value / cumulative_factor(factors, prev_basis, basis_date)
            d = _log_distance(row.value, ref)
            if d is not None:
                scored.append((d, basis_date, side, ref))
        # The hypotheses are |log(ratio)| apart. A revision may only count as evidence if
        # it is smaller than half that gap, or a 3-for-2 split could be "explained" by an
        # ordinary 20% revision.
        gaps = [
            abs(math.log(cumulative_factor(factors, a[0], b[0])))
            for a, b in itertools.pairwise(candidates)
        ]
        tolerance = min([MAX_LOG_DISTANCE, *(0.4 * g for g in gaps)])
        if len(scored) == len(candidates):
            scored.sort()
            best = scored[0]
            if best[0] <= tolerance:
                d, basis_date, side, ref = best
                guessed = " (prior estimate's basis was itself a guess)" if prev_guessed else ""
                return BasisDecision(
                    row.estimate_id,
                    basis_date,
                    "ratio_inferred",
                    prev_guessed,
                    latest,
                    f"{row.value:g} is closest to the broker's prior estimate on the {side} "
                    f"basis ({ref:.4g}) for {splits}{guessed}",
                )
    passed = [f for f in events if entered >= f.ex_date]
    basis_date = passed[-1].ex_date if passed else events[0].ex_date - dt.timedelta(days=1)
    return BasisDecision(
        row.estimate_id,
        basis_date,
        "date_fallback",
        True,
        latest,
        f"no conclusive prior estimate; assumed the share basis in effect on the entry date "
        f"{entered} ({splits})",
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
        prev: tuple[float, dt.date, bool] | None = None
        for r in chain:
            if r.action == "stop" or r.value is None:
                prev = None  # coverage dropped; a later re-initiation starts fresh
                continue
            decision = resolve_one(r, prev, usable)
            out[r.estimate_id] = decision
            prev = (r.value, decision.basis_date, decision.inferred)
    return out
