"""A spin-off is not a split: GE HealthCare, ex-date 2023-01-04.

No share factor applies to GE's EPS. Instead, FY2023 estimates entered before the
spin-off (which still included HealthCare) are excluded once it takes effect.
"""

from __future__ import annotations

import datetime as dt

from pitdemo.store import Store
from pitdemo.transforms.consensus import consensus
from tests.helpers import est, make_store

GE = "SEC-0004"
SPIN = {
    "security_id": GE,
    "ticker": "GE",
    "action_type": "SPINOFF",
    "known_at": dt.datetime(2022, 12, 1, 2, 40, 8),
    "record_date": dt.date(2022, 12, 16),
    "ex_date": dt.date(2023, 1, 4),
    "ratio": 1.0,
    "terms": "1 GEHC share per 3 GE shares",
    "source_url": "https://www.sec.gov/Archives/edgar/data/40545/000119312522295436/d416221dex991.htm",
    "notes": None,
}


def _store() -> Store:
    rows = (
        [
            est(b, v, "2022-12-12", security_id=GE, period="FY2023")
            for b, v in zip("ABCD", [3.0, 3.1, 3.2, 3.3], strict=True)
        ]
        + [
            est(b, v, "2023-01-06", security_id=GE, period="FY2023")
            for b, v in zip("ABC", [2.2, 2.3, 2.4], strict=True)
        ]
        + [
            est("A", 2.0, "2022-12-12", security_id=GE, period="FY2022"),
            est("B", 2.1, "2022-12-12", security_id=GE, period="FY2022"),
            est("C", 2.2, "2022-12-12", security_id=GE, period="FY2022"),
        ]
    )
    return make_store(rows, [SPIN])


def test_pre_spin_estimates_excluded_after_ex_date() -> None:
    r = consensus(_store(), GE, "FY2023", dt.date(2023, 1, 10))
    assert r.count == 3
    assert abs(r.mean - 2.3) < 1e-9  # type: ignore[operator]
    (x,) = r.excluded
    assert x.broker_id == "D"
    assert x.reason == "pre_spinoff_perimeter"
    assert r.corporate_actions_applied == []  # no share factor


def test_before_ex_date_estimates_count_but_warn() -> None:
    r = consensus(_store(), GE, "FY2023", dt.date(2022, 12, 20))
    assert r.count == 4
    assert r.excluded == []
    assert any("spin-off takes effect 2023-01-04" in w for w in r.warnings)


def test_period_ending_before_spin_is_unaffected() -> None:
    r = consensus(_store(), GE, "FY2022", dt.date(2023, 1, 10))
    assert r.count == 3
    assert r.excluded == []


def test_seeded_ge_rebases(full_store: Store) -> None:
    before = consensus(full_store, GE, "FY2023", dt.date(2022, 12, 30))
    after = consensus(full_store, GE, "FY2023", dt.date(2023, 1, 20))
    assert not before.insufficient
    assert not after.insufficient
    assert after.mean < before.mean  # type: ignore[operator]
    assert all(c.entered_at.date() >= dt.date(2023, 1, 4) for c in after.contributors)
