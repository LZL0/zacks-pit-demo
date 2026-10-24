"""SVB Financial: in the universe while listed, gone after, history intact.

Timeline (primary sources in data/raw/corporate_actions.csv): bank closed
2023-03-10, Nasdaq suspended trading 2023-03-28 (OTC as SIVBQ from then), Form 25
delisting effective 2023-05-12.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pitdemo.service import NotFoundError, Service
from pitdemo.store import Store
from pitdemo.transforms.consensus import consensus

SIVB = "SEC-0005"


def test_universe_membership(service: Service) -> None:
    assert (SIVB, "SIVB") in service.universe("2023-03-01")
    assert (SIVB, "SIVBQ") in service.universe("2023-04-15")
    assert SIVB not in {sid for sid, _ in service.universe("2023-05-12")}
    assert SIVB not in {sid for sid, _ in service.universe("2024-01-02")}
    assert len(service.universe("2023-03-01")) == 5
    assert len(service.universe("2024-01-02")) == 4


def test_history_is_intact_after_delisting(full_store: Store, service: Service) -> None:
    r = service.get_consensus("SIVB", "FY2023", "2023-02-15")
    assert r.security.security_id == SIVB
    assert r.security.ticker == "SIVB"
    assert not r.insufficient
    assert r.count >= 8
    # Same query by permanent id gives the same answer.
    by_id = consensus(full_store, SIVB, "FY2023", dt.date(2023, 2, 15))
    assert by_id.mean == r.mean
    assert full_store.estimates_for(SIVB).height > 0


def test_estimates_stop_when_the_bank_fails(full_store: Store) -> None:
    after = consensus(full_store, SIVB, "FY2023", dt.date(2023, 3, 20))
    assert after.insufficient
    assert after.count == 0
    late = full_store.estimates_for(SIVB).filter(
        full_store.estimates_for(SIVB)["entered_at"] > dt.datetime(2023, 3, 14)
    )
    assert late.height == 0


def test_shock_before_failure_is_visible(full_store: Store) -> None:
    calm = consensus(full_store, SIVB, "FY2023", dt.date(2023, 3, 1))
    shocked = consensus(full_store, SIVB, "FY2023", dt.date(2023, 3, 9))
    assert shocked.mean < calm.mean * 0.9  # type: ignore[operator]


def test_ticker_resolution_is_point_in_time(service: Service) -> None:
    with pytest.raises(NotFoundError, match="not in use on 2023-06-01"):
        service.get_consensus("SIVB", "FY2023", "2023-06-01")
    r = service.get_consensus("SEC-0005", "FY2023", "2023-06-01")
    assert r.insufficient
    assert any("delisted on 2023-05-12" in w for w in r.warnings)
