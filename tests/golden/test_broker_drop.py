"""A broker dropping coverage moves the consensus but not the revision signal.

Four brokers each revise up exactly 1% on 2024-02-01. Broker X (the high one)
drops coverage on 2024-02-15. The mean falls 17% purely from composition; the
per-broker revision signal is +1% before and after.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pitdemo.store import Store
from pitdemo.transforms.consensus import as_of_instant, consensus, load_inputs, revision_signal
from tests.helpers import NVDA, est, make_store


@pytest.fixture
def store() -> Store:
    rows = [
        est("A", 10.00, "2024-01-02"),
        est("B", 11.00, "2024-01-02"),
        est("C", 12.00, "2024-01-02"),
        est("X", 20.00, "2024-01-02"),
        est("A", 10.10, "2024-02-01"),
        est("B", 11.11, "2024-02-01"),
        est("C", 12.12, "2024-02-01"),
        est("X", 20.20, "2024-02-01"),
        est("X", None, "2024-02-15"),  # stop: coverage dropped
    ]
    return make_store(rows)


def test_consensus_moves_when_broker_drops(store: Store) -> None:
    before = consensus(store, NVDA, "FY2025", dt.date(2024, 2, 14))
    after = consensus(store, NVDA, "FY2025", dt.date(2024, 2, 16))
    assert before.count == 4
    assert after.count == 3
    assert before.mean == pytest.approx(53.53 / 4)
    assert after.mean == pytest.approx(11.11)
    assert after.mean / before.mean - 1 == pytest.approx(11.11 / (53.53 / 4) - 1)  # type: ignore[operator]
    assert after.mean / before.mean - 1 < -0.16  # type: ignore[operator]
    assert "X" not in {c.broker_id for c in after.contributors}


def test_revision_signal_does_not_move(store: Store) -> None:
    inputs = load_inputs(store, NVDA)
    before = revision_signal(inputs, "FY2025", as_of_instant(dt.date(2024, 2, 14)), 30)
    after = revision_signal(inputs, "FY2025", as_of_instant(dt.date(2024, 2, 16)), 30)
    assert before.brokers_compared == 4
    assert after.brokers_compared == 3
    assert before.up == 4
    assert after.up == 3
    assert before.down == after.down == 0
    assert before.mean_pct_change == pytest.approx(0.01)
    assert after.mean_pct_change == pytest.approx(0.01)


def test_seeded_nvda_broker_drop(full_store: Store) -> None:
    """In the synthetic data one NVDA broker stops coverage on 2024-08-14."""
    est_df = full_store.estimates_for(NVDA, "FY2025")
    stops = est_df.filter(est_df["action"] == "stop")
    assert stops.height == 1
    dropped = stops["broker_id"][0]
    on = stops["entered_at"][0].date()
    before = consensus(full_store, NVDA, "FY2025", on - dt.timedelta(days=1))
    after = consensus(full_store, NVDA, "FY2025", on)
    assert dropped in {c.broker_id for c in before.contributors}
    assert dropped not in {c.broker_id for c in after.contributors}
