"""Fixed fiscal labels never jump; relative labels (FY1) roll and are flagged.

NVIDIA released FY2024 results on 2024-02-21 after the close (8-K accepted
2024-02-22 ~02:22 UTC). That release rolls FY1 from FY2024 to FY2025.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pitdemo.ids import SECURITIES
from pitdemo.periods import FiscalYearEnd, fiscal_label_for_period_end, resolve_period
from pitdemo.pipeline import RawData
from pitdemo.service import Service
from pitdemo.store import Store

NVDA = "SEC-0001"


def test_fy1_resolves_and_rolls_at_the_release(full_store: Store) -> None:
    reports = full_store.report_times(NVDA)
    rule = FiscalYearEnd(month=1, weekday=6)
    before = resolve_period("FY1", rule, dt.datetime(2024, 2, 21, 20, 0), reports)
    after = resolve_period("FY1", rule, dt.datetime(2024, 2, 22, 12, 0), reports)
    assert before.fiscal_period == "FY2024"
    assert after.fiscal_period == "FY2025"
    assert resolve_period("FY2", rule, dt.datetime(2024, 2, 22, 12, 0), reports).fiscal_period == (
        "FY2026"
    )
    assert after.near_roll
    assert after.note is not None
    assert "rolled to FY2025" in after.note
    quiet = resolve_period("FY1", rule, dt.datetime(2024, 4, 15), reports)
    assert quiet.fiscal_period == "FY2025"
    assert not quiet.near_roll


def test_fixed_label_passes_through(full_store: Store) -> None:
    r = resolve_period(
        "FY2025", FiscalYearEnd(1, 6), dt.datetime(2024, 2, 22, 12), full_store.report_times(NVDA)
    )
    assert r.fiscal_period == "FY2025"
    assert not r.near_roll
    assert r.note is None


def test_relative_series_jumps_fixed_series_does_not(service: Service) -> None:
    fy1_before = service.get_consensus("NVDA", "FY1", "2024-02-21")
    fy1_after = service.get_consensus("NVDA", "FY1", "2024-02-23")
    fixed_before = service.get_consensus("NVDA", "FY2025", "2024-02-21")
    fixed_after = service.get_consensus("NVDA", "FY2025", "2024-02-23")
    assert fy1_before.fiscal_period == "FY2024"
    assert fy1_after.fiscal_period == "FY2025"
    assert fixed_before.fiscal_period == fixed_after.fiscal_period == "FY2025"
    # FY1 is literally the FY2024 consensus, then the FY2025 one.
    assert fy1_before.mean == service.get_consensus("NVDA", "FY2024", "2024-02-21").mean
    assert fy1_after.mean == fixed_after.mean
    # ... so it jumps by the gap between two different years, and it says so.
    assert fy1_after.mean / fy1_before.mean > 1.5  # type: ignore[operator]
    assert any("rolled to FY2025" in w for w in fy1_after.warnings)
    assert not any("rolled" in w for w in fixed_after.warnings)
    assert fy1_after.requested_period == "FY1"


@pytest.mark.parametrize("sec", SECURITIES, ids=lambda s: s.name)
def test_fiscal_year_end_rules_match_edgar(sec: object, raw: RawData) -> None:
    """Our year-end rules reproduce every annual period end SEC has on file."""
    from pitdemo.ids import SecurityMaster

    assert isinstance(sec, SecurityMaster)
    df = raw.actuals[sec.security_id]
    for label, end in df.select("fiscal_period", "period_end").unique().rows():
        assert fiscal_label_for_period_end(end) == label
        assert sec.fye.end_of(int(label[2:])) == end, (sec.name, label, end)
