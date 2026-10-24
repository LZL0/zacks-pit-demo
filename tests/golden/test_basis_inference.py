"""Edge cases of per-estimate basis inference (from an independent code review)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from pitdemo.transforms.consensus import consensus
from tests.helpers import NVDA, est, make_store


def split(ex: str, ratio: float, known: str = "2024-05-20") -> dict[str, Any]:
    return {
        "security_id": NVDA,
        "ticker": "NVDA",
        "action_type": "SPLIT",
        "known_at": dt.datetime.fromisoformat(known + "T20:00:00"),
        "record_date": None,
        "ex_date": dt.date.fromisoformat(ex),
        "ratio": ratio,
        "terms": f"{ratio:g}-for-1",
        "source_url": "https://www.sec.gov/test",
        "notes": None,
    }


def test_small_ratio_split_is_not_confused_with_a_revision() -> None:
    """3-for-2: an ordinary 20% cut must not be read as the split.

    Broker A cuts from 3.00 to 2.40 on 05-28, still on the pre-split basis. 2.40 is
    closer to 3.00 / 1.5 = 2.00 than to 3.00 in log terms only barely; with a fixed
    25% tolerance the old code called it post-split (adjusted 2.40 instead of 1.60).
    """
    rows = [
        est("A", 3.00, "2024-05-01"),
        est("A", 2.40, "2024-05-28"),
        est("B", 3.10, "2024-05-01"),
        est("C", 2.90, "2024-05-01"),
    ]
    r = consensus(
        make_store(rows, [split("2024-06-10", 1.5)]), NVDA, "FY2025", dt.date(2024, 5, 30)
    )
    a = {c.broker_id: c for c in r.contributors}["A"]
    assert a.basis_source == "date_fallback"
    assert a.basis_inferred
    assert a.basis_date == dt.date(2024, 6, 9)
    assert a.value == pytest.approx(1.60)


def test_large_ratio_still_inferred_confidently() -> None:
    rows = [est("A", 3.00, "2024-05-01"), est("A", 0.31, "2024-06-05")]
    r = consensus(
        make_store(rows, [split("2024-06-10", 10.0)]), NVDA, "FY2025", dt.date(2024, 6, 6)
    )
    (a,) = r.contributors
    assert a.basis_source == "ratio_inferred"
    assert not a.basis_inferred
    assert a.basis_date == dt.date(2024, 6, 10)


def test_guessed_basis_propagates_through_the_chain() -> None:
    """A row inferred from a guessed prior is itself a guess, and is reported as one."""
    rows = [
        est("X", 2.50, "2024-06-05"),  # new broker, early post-split number: no prior
        est("X", 2.55, "2024-06-07"),  # "matches" the guessed prior
    ]
    store = make_store(rows, [split("2024-06-10", 10.0)])
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 7))
    (x,) = r.contributors
    assert x.basis_source == "ratio_inferred"
    assert x.basis_inferred
    assert any("guessed" in w for w in r.warnings)


def test_two_pending_splits_laggard_on_the_oldest_basis() -> None:
    """A broker still on the basis before *both* pending splits is matched by ratio.

    2-for-1 (ex 06-10) and 3-for-1 (ex 06-20) both announced 05-20. Broker A is at 60
    before either, then publishes 61 on 06-25, still on the original share count.
    Correct adjusted value: 61 / 6.
    """
    rows = [est("A", 60.0, "2024-05-01"), est("A", 61.0, "2024-06-25")]
    store = make_store(rows, [split("2024-06-10", 2.0), split("2024-06-20", 3.0)])
    r = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 26))
    (a,) = r.contributors
    assert a.basis_source == "ratio_inferred"
    assert a.basis_date == dt.date(2024, 6, 9)
    assert a.factor == 6.0
    assert a.value == pytest.approx(61 / 6)


def test_two_pending_splits_middle_regime() -> None:
    rows = [est("A", 60.0, "2024-05-01"), est("A", 30.5, "2024-06-25")]
    store = make_store(rows, [split("2024-06-10", 2.0), split("2024-06-20", 3.0)])
    (a,) = consensus(store, NVDA, "FY2025", dt.date(2024, 6, 26)).contributors
    assert a.basis_date == dt.date(2024, 6, 10)
    assert a.value == pytest.approx(30.5 / 3)
