"""An estimate older than 120 days is excluded, and the reason is recorded."""

from __future__ import annotations

import datetime as dt

import pytest

from pitdemo.store import Store
from pitdemo.transforms.consensus import consensus
from tests.helpers import NVDA, est, make_store


@pytest.fixture
def store() -> Store:
    rows = [
        est("OLD", 9.00, "2024-01-01", estimate_id="OLD-1"),
        est("A", 10.00, "2024-04-01"),
        est("B", 11.00, "2024-04-01"),
        est("C", 12.00, "2024-04-01"),
    ]
    return make_store(rows)


def test_120_days_is_kept(store: Store) -> None:
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 4, 30))  # 2024 is a leap year
    assert r.count == 4
    assert r.mean == pytest.approx(10.5)
    assert r.excluded == []


def test_121_days_is_excluded_with_reason(store: Store) -> None:
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 5, 1))
    assert r.count == 3
    assert r.mean == pytest.approx(11.0)
    (x,) = r.excluded
    assert x.estimate_id == "OLD-1"
    assert x.reason == "stale"
    assert x.detail == "entered 121 days before as_of (> 120)"
    assert r.excluded_total == 1


def test_not_reconfirmed_after_report() -> None:
    """Separate rule: estimates from before an earnings release lapse 7 days after it."""
    rows = [
        est("A", 10.0, "2024-04-01"),
        est("B", 11.0, "2024-04-01"),
        est("C", 12.0, "2024-04-01"),
        est("D", 13.0, "2024-04-01"),
        est("A", 10.5, "2024-04-12"),
        est("B", 11.0, "2024-04-12", action="confirm"),  # re-confirmed, same value
        est("C", 12.5, "2024-04-13"),
    ]
    released = dt.datetime(2024, 4, 11, 21, 0)
    store = make_store(rows, reports=[(NVDA, released)])
    within_grace = consensus(store, NVDA, "FY2025", dt.date(2024, 4, 17))
    lapsed = consensus(store, NVDA, "FY2025", dt.date(2024, 4, 19))
    assert within_grace.count == 4
    assert lapsed.count == 3
    assert lapsed.mean == pytest.approx(34.0 / 3)
    (x,) = lapsed.excluded
    assert x.broker_id == "D"
    assert x.reason == "not_reconfirmed_after_report"


def test_insufficient_below_three() -> None:
    store = make_store([est("A", 10.0, "2024-04-01"), est("B", 11.0, "2024-04-01")])
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 4, 2))
    assert r.insufficient
    assert r.count == 2
    assert r.mean is None
    assert r.median is None
    assert r.stdev is None


def test_outlier_excluded_after_basis_conversion() -> None:
    rows = [
        est(b, v, "2024-04-01") for b, v in zip("ABCDE", [10.0, 10.2, 9.9, 10.1, 16.0], strict=True)
    ]
    r = consensus(make_store(rows), NVDA, "FY2025", dt.date(2024, 4, 2))
    assert r.count == 4
    assert [x.broker_id for x in r.excluded] == ["E"]
    assert r.excluded[0].reason == "outlier"
    assert r.mean == pytest.approx(10.05)
