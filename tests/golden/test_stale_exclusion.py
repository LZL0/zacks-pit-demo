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


def test_lapsed_estimates_stay_out_during_the_next_grace_window() -> None:
    """An estimate that lapsed after release 1 must not return during release 2's grace.

    (Found by review: the rule used only the latest release, so for 7 days after every
    release, estimates already excluded for the previous one came back.) OLD is young
    enough that the 120-day staleness rule cannot be what excludes it.
    """
    rows = [
        est("A", 10.0, "2024-01-20"),
        est("B", 11.0, "2024-01-20"),
        est("C", 12.0, "2024-01-20"),
        est("OLD", 20.0, "2024-02-15"),
        est("A", 10.5, "2024-03-04"),
        est("B", 11.5, "2024-03-04"),
        est("C", 12.5, "2024-03-04"),
    ]
    r1 = dt.datetime(2024, 3, 1, 21, 0)
    r2 = dt.datetime(2024, 5, 1, 21, 0)
    store = make_store(rows, reports=[(NVDA, r1), (NVDA, r2)])
    for day in (dt.date(2024, 4, 30), dt.date(2024, 5, 3), dt.date(2024, 5, 7)):
        r = consensus(store, NVDA, "FY2025", day)
        assert "OLD" not in {c.broker_id for c in r.contributors}, day
        assert [x.reason for x in r.excluded] == ["not_reconfirmed_after_report"], day
        assert r.mean == pytest.approx(11.5), day
    # Once release 2's grace period is over, A/B/C lapse too.
    after = consensus(store, NVDA, "FY2025", dt.date(2024, 5, 9))
    assert after.insufficient


def test_reported_period_does_not_resurface(full_store: Store) -> None:
    """NVDA FY2024 was reported in Feb 2024; its stale estimates stay out for good."""
    for day in (dt.date(2024, 5, 20), dt.date(2024, 5, 24), dt.date(2024, 5, 31)):
        r = consensus(full_store, NVDA, "FY2024", day)
        assert r.count == 0, day
