"""Corporate actions only apply once they are known.

NVDA announced its 10-for-1 split in the 8-K accepted 2024-05-23 00:21:29 UTC.
With ``known_as_of`` before that, the adjusted consensus on 2024-05-31 must apply
no factor. With full knowledge, the same estimates restate to post-split values.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pitdemo.store import Store
from pitdemo.transforms.consensus import consensus
from pitdemo.transforms.factors import LATEST, cumulative_factor, factors_from_frame
from tests.helpers import NVDA, NVDA_SPLIT, est, make_store

ANNOUNCED = dt.datetime(2024, 5, 23, 0, 21, 29)


@pytest.fixture
def store() -> Store:
    rows = [
        est("A", 26.0, "2024-05-24"),
        est("B", 25.0, "2024-05-24"),
        est("C", 27.0, "2024-05-24"),
    ]
    return make_store(rows, [NVDA_SPLIT])


def test_before_announcement_no_factor(store: Store) -> None:
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 5, 31), known_as_of=dt.date(2024, 5, 20))
    assert r.mean == pytest.approx(26.0)
    assert r.corporate_actions_applied == []
    assert all(c.factor == 1.0 for c in r.contributors)
    assert any("announced after known_as_of" in w for w in r.warnings)
    assert r.target_basis == "latest known share basis (no splits known)"


def test_one_second_before_and_after_announcement(store: Store) -> None:
    just_before = consensus(
        store, NVDA, "FY2025", dt.date(2024, 5, 31), known_as_of=ANNOUNCED - dt.timedelta(seconds=1)
    )
    at = consensus(store, NVDA, "FY2025", dt.date(2024, 5, 31), known_as_of=ANNOUNCED)
    assert just_before.mean == pytest.approx(26.0)
    assert at.mean == pytest.approx(2.60)


def test_full_knowledge_applies_the_split(store: Store) -> None:
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 5, 31))
    assert r.mean == pytest.approx(2.60)
    assert [a.ratio for a in r.corporate_actions_applied] == [10.0]
    # No flag and no prior estimate from these brokers: the basis is a guess, and says so.
    assert all(c.basis_source == "date_fallback" and c.basis_inferred for c in r.contributors)
    assert any("basis_inferred=true" in w for w in r.warnings)


def test_cumulative_factor_respects_knowledge_time(full_store: Store) -> None:
    factors = factors_from_frame(full_store.corporate_actions(NVDA))
    pre_2021 = dt.date(2021, 1, 4)
    assert cumulative_factor(factors, pre_2021, LATEST) == 40.0  # 4-for-1 then 10-for-1
    assert cumulative_factor(factors, pre_2021, LATEST, dt.datetime(2024, 1, 1)) == 4.0
    assert cumulative_factor(factors, pre_2021, LATEST, dt.datetime(2021, 5, 1)) == 1.0
    assert cumulative_factor(factors, dt.date(2024, 6, 10), dt.date(2024, 6, 9)) == 0.1
    # Splits after the target date do not apply.
    assert cumulative_factor(factors, pre_2021, dt.date(2024, 6, 9)) == 4.0
    assert cumulative_factor(NVDA_SPLIT_LIST, dt.date(2024, 6, 9), dt.date(2024, 6, 10)) == 10.0


NVDA_SPLIT_LIST = factors_from_frame(make_store([], [NVDA_SPLIT]).corporate_actions(NVDA))
