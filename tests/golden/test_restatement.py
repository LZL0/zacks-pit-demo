"""Real EDGAR restatements: every filing's value is kept, and PIT queries see the right one.

NVIDIA's FY2024 diluted EPS was first filed as $11.93 (10-K, 2024-02-21). After the
June 2024 10-for-1 split, the next 10-K (2025-02-26) shows FY2024 as $1.19. Both rows
are in the store; which one you get depends on when you ask.
"""

from __future__ import annotations

import datetime as dt
import itertools

import polars as pl
import pytest

from pitdemo.pipeline import RawData
from pitdemo.store import Store
from pitdemo.transforms.factors import cumulative_factor

NVDA, GE = "SEC-0001", "SEC-0004"


def test_both_versions_are_stored(full_store: Store) -> None:
    rows = full_store.actuals(NVDA, "FY2024")
    assert set(rows["value"].to_list()) == {11.93, 1.19}
    first = rows.row(0, named=True)
    assert first["value"] == 11.93
    assert first["accession"] == "0001045810-24-000029"
    # Knowledge time is the SEC acceptance timestamp (UTC), not just the filing date.
    assert first["filed_at"] == dt.datetime(2024, 2, 22, 2, 36, 57)
    assert rows.row(-1, named=True)["value"] == 1.19


def test_pit_query_before_restating_filing_returns_original(full_store: Store) -> None:
    assert full_store.actual_as_of(NVDA, "FY2024", dt.datetime(2024, 3, 1)) == 11.93
    assert full_store.actual_as_of(NVDA, "FY2024", dt.datetime(2025, 2, 27, 2, 48, 32)) == 11.93
    assert full_store.actual_as_of(NVDA, "FY2024", dt.datetime(2025, 2, 27, 2, 48, 33)) == 1.19
    assert full_store.actual_as_of(NVDA, "FY2024", dt.datetime(2024, 2, 1)) is None


def test_non_split_restatement_ge(full_store: Store) -> None:
    """GE recast FY2022 after the GE HealthCare spin-off: -0.05 became +0.05.

    No share-count factor explains that; it is a change in what the numbers cover.
    """
    assert full_store.actual_as_of(GE, "FY2022", dt.datetime(2023, 3, 1)) == -0.05
    assert full_store.actual_as_of(GE, "FY2022", dt.datetime(2023, 5, 1)) == 0.05


def test_split_restatements_are_explained_by_our_factors(raw: RawData) -> None:
    """Cross-check the corporate-actions table against how the companies restated EPS.

    For every pair of consecutive filings of the same fiscal year with a split between
    them, the later value must equal the earlier one divided by our cumulative factor
    (to the cent, allowing 1% for reclassifications such as GE's 2021 recast).
    """
    checked = 0
    for sid, actuals in raw.actuals.items():
        factors = raw.factors(sid)
        for (_, period), group in actuals.group_by(["security_id", "fiscal_period"]):
            g = group.sort("filed_at").unique(subset=["value", "filed_at"], maintain_order=True)
            vals = g.select("value", "filed_at").rows()
            for (v0, t0), (v1, t1) in itertools.pairwise(vals):
                f = cumulative_factor(factors, t0.date(), t1.date())
                if f == 1.0:
                    continue
                expected = v0 / f
                assert abs(v1 - expected) <= 0.01 + 0.01 * abs(v1), (sid, period, v0, v1, f)
                checked += 1
    # NVDA x2 splits, AAPL, TSLA x2, GE reverse split: plenty of pairs.
    assert checked >= 12


def test_restatement_pairs_found_in_real_data(raw: RawData) -> None:
    nv = raw.actuals["SEC-0001"].filter(pl.col("fiscal_period") == "FY2023")
    assert nv["value"].to_list()[:1] == [1.74]
    assert nv["value"].to_list()[-1] == pytest.approx(0.17)
