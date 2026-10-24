"""Content-addressed cache: same inputs hit; a new split event means a new key."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from pitdemo.schemas import CorporateActionsFrame, Versions
from pitdemo.service import Service
from pitdemo.transforms.cache import ConsensusCache, cache_key
from tests.helpers import NVDA, NVDA_SPLIT, est, make_store


@pytest.fixture
def service() -> Service:
    rows = [est(b, v, "2024-05-01") for b, v in zip("ABC", [24.0, 25.0, 26.0], strict=True)]
    return Service.create(make_store(rows))


def test_same_inputs_hit(service: Service) -> None:
    a = service.get_consensus("NVDA", "FY2025", "2024-06-20")
    b = service.get_consensus("NVDA", "FY2025", "2024-06-20")
    assert a is b
    assert service.cache.stats.hits == 1
    assert service.cache.stats.misses == 1


def test_new_split_event_gives_new_key(service: Service) -> None:
    before = service.get_consensus("NVDA", "FY2025", "2024-06-20")
    assert before.mean == pytest.approx(25.0)
    service.store.insert_corporate_actions(
        pl.DataFrame([NVDA_SPLIT], schema=CorporateActionsFrame.polars_schema())
    )
    after = service.get_consensus("NVDA", "FY2025", "2024-06-20")
    assert after is not before
    assert after.mean == pytest.approx(2.5)
    assert after.versions.factors == before.versions.factors + 1
    assert service.cache.stats.misses == 2
    # Asking again with unchanged data is a hit on the new key.
    assert service.get_consensus("NVDA", "FY2025", "2024-06-20") is after


def test_new_estimate_gives_new_key(service: Service) -> None:
    service.get_consensus("NVDA", "FY2025", "2024-06-20")
    service.store.insert_estimates(
        pl.DataFrame([est("D", 30.0, "2024-05-02")]).cast({"share_basis_date": pl.Date})
    )
    after = service.get_consensus("NVDA", "FY2025", "2024-06-20")
    assert after.count == 4
    assert service.cache.stats.misses == 2


def test_key_covers_every_input() -> None:
    v = Versions(estimates=10, factors=2, reports=5, fn="consensus-v1")
    t = dt.datetime(2024, 5, 31, 23, 59)
    base = cache_key(NVDA, "FY2025", t, "adjusted", None, v)
    variants = [
        cache_key("SEC-0002", "FY2025", t, "adjusted", None, v),
        cache_key(NVDA, "FY2026", t, "adjusted", None, v),
        cache_key(NVDA, "FY2025", t + dt.timedelta(seconds=1), "adjusted", None, v),
        cache_key(NVDA, "FY2025", t, "unadjusted", None, v),
        cache_key(NVDA, "FY2025", t, "adjusted", dt.datetime(2024, 5, 1), v),
        cache_key(NVDA, "FY2025", t, "adjusted", None, v.model_copy(update={"estimates": 11})),
        cache_key(NVDA, "FY2025", t, "adjusted", None, v.model_copy(update={"factors": 3})),
        cache_key(NVDA, "FY2025", t, "adjusted", None, v.model_copy(update={"reports": 6})),
        cache_key(NVDA, "FY2025", t, "adjusted", None, v.model_copy(update={"fn": "v2"})),
    ]
    assert len({base, *variants}) == len(variants) + 1
    assert cache_key(NVDA, "FY2025", t, "adjusted", None, v) == base


def test_lru_eviction() -> None:
    cache = ConsensusCache(maxsize=2)
    svc = Service.create(make_store([est(b, 1.0, "2024-05-01") for b in "ABC"]))
    r = svc.get_consensus("NVDA", "FY2025", "2024-05-02")
    cache.get_or_compute("a", lambda: r)
    cache.get_or_compute("b", lambda: r)
    cache.get_or_compute("a", lambda: r)  # touch a
    cache.get_or_compute("c", lambda: r)  # evicts b
    assert "a" in cache
    assert "c" in cache
    assert "b" not in cache
