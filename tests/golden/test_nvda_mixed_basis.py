"""NVDA 10-for-1, ex-date 2024-06-10: brokers restate on different days.

Hand-built case (exact values):

=====  ==========  ==============================  ===========================
broker 2024-05-24   during the split window         basis evidence
=====  ==========  ==============================  ===========================
A      26.00        2.60 on 06-14                   ratio vs prior (post)
B      25.00        2.50 on 06-05 (*before* ex)     ratio vs prior (post)
C      27.00        27.50 on 06-12 (*after* ex)     ratio vs prior (pre)
D      24.00        2.40 on 06-11                   flagged by source
E      28.00        (no change)                     ratio vs prior (pre)
=====  ==========  ==============================  ===========================

Every broker also has an earlier estimate (2024-05-01), so the chain has a prior.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from pitdemo.calendar import trading_days
from pitdemo.store import Store
from pitdemo.transforms.consensus import (
    as_of_instant,
    consensus,
    consensus_entry_date,
    consensus_naive,
    load_inputs,
)
from tests.helpers import NVDA, NVDA_SPLIT, est, make_store


@pytest.fixture
def store() -> Store:
    rows = [
        est("A", 25.50, "2024-05-01"),
        est("B", 24.60, "2024-05-01"),
        est("C", 26.80, "2024-05-01"),
        est("D", 23.90, "2024-05-01", basis="2024-05-01"),
        est("E", 27.70, "2024-05-01"),
        est("A", 26.00, "2024-05-24"),
        est("B", 25.00, "2024-05-24"),
        est("C", 27.00, "2024-05-24"),
        est("D", 24.00, "2024-05-24", basis="2024-05-24"),
        est("E", 28.00, "2024-05-24"),
        est("B", 2.50, "2024-06-05"),  # early restater, unflagged
        est("D", 2.40, "2024-06-11", basis="2024-06-11"),  # flagged
        est("C", 27.50, "2024-06-12"),  # laggard: pre-split number after the ex-date
        est("A", 2.60, "2024-06-14"),
        est("C", 2.75, "2024-06-20"),
    ]
    return make_store(rows, [NVDA_SPLIT])


def test_adjusted_consensus_is_continuous(store: Store) -> None:
    before = consensus(store, NVDA, "FY2025", dt.date(2024, 5, 31))
    mid = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 7))
    after = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 14))
    assert before.mean == pytest.approx(2.60)
    assert mid.mean == pytest.approx(2.60)
    assert after.mean == pytest.approx(2.61)
    assert after.median == pytest.approx(2.60)
    assert after.high == pytest.approx(2.80)
    assert after.low == pytest.approx(2.40)
    assert after.count == 5
    assert [a.ex_date for a in after.corporate_actions_applied] == [dt.date(2024, 6, 10)]


def test_naive_and_entry_date_shortcut_break(store: Store) -> None:
    inputs = load_inputs(store, NVDA)
    t = as_of_instant(dt.date(2024, 6, 14))
    # Raw average of what was published: a mix of two share bases.
    assert consensus_naive(inputs, "FY2025", as_of_instant(dt.date(2024, 5, 31))) == pytest.approx(
        26.0
    )
    assert consensus_naive(inputs, "FY2025", t) == pytest.approx(12.6)
    # Shortcut that divides everything entered before the ex-date by 10: B (already
    # post-split on 06-05) is divided again, C (still pre-split on 06-12) is not.
    assert consensus_entry_date(inputs, "FY2025", t) == pytest.approx(7.11)


def test_each_estimate_gets_its_own_basis(store: Store) -> None:
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 14))
    by = {c.broker_id: c for c in r.contributors}
    assert by["A"].basis_source == "ratio_inferred"
    assert by["A"].factor == 1.0
    assert by["B"].basis_source == "ratio_inferred"
    assert by["B"].basis_date == dt.date(2024, 6, 10)
    assert by["C"].basis_source == "ratio_inferred"
    assert by["C"].basis_date == dt.date(2024, 6, 9)
    assert by["C"].factor == 10.0
    assert by["C"].value == pytest.approx(2.75)
    assert by["D"].basis_source == "flagged"
    assert by["E"].factor == 10.0
    assert not any(c.basis_inferred for c in r.contributors)
    assert r.warnings == []


def test_unadjusted_follows_the_share_basis_of_the_day(store: Store) -> None:
    pre = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 7), basis="unadjusted")
    post = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 14), basis="unadjusted")
    # On 06-07 the shares had not split yet: B's post-split 2.50 is shown as 25.00.
    assert pre.mean == pytest.approx(26.0)
    assert {c.broker_id: c.value for c in pre.contributors}["B"] == pytest.approx(25.0)
    assert post.mean == pytest.approx(2.61)
    assert pre.target_basis_date == dt.date(2024, 6, 7)


def test_seeded_dataset_naive_collapses_correct_does_not(full_store: Store) -> None:
    """The synthetic NVDA data: naive drops ~90%, the pipeline moves only on revisions."""
    inputs = load_inputs(full_store, NVDA)
    start, end = as_of_instant(dt.date(2024, 5, 31)), as_of_instant(dt.date(2024, 7, 1))
    naive_drop = 1 - consensus_naive(inputs, "FY2025", end) / consensus_naive(  # type: ignore[operator]
        inputs, "FY2025", start
    )
    assert 0.85 < naive_drop < 0.95
    means = []
    for d in trading_days(dt.date(2024, 5, 28), dt.date(2024, 7, 31)):
        r = consensus(full_store, NVDA, "FY2025", d)
        assert not r.insufficient
        means.append(r.mean)
    assert max(means) / min(means) < 1.06  # type: ignore[type-var,operator]
    day_moves = [abs(b / a - 1) for a, b in itertools.pairwise(means)]  # type: ignore[operator]
    assert max(day_moves) < 0.03
