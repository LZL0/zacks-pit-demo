from __future__ import annotations

from collections.abc import Iterator

import pytest

from pitdemo.pipeline import RawData, load_raw_data, memory_store
from pitdemo.service import Service
from pitdemo.store import Store


@pytest.fixture(scope="session")
def raw() -> RawData:
    return load_raw_data()


@pytest.fixture(scope="session")
def full_store() -> Iterator[Store]:
    """The full demo dataset: real EDGAR + corporate actions, seeded synthetic estimates."""
    store = memory_store()
    yield store
    store.close()


@pytest.fixture(scope="session")
def service(full_store: Store) -> Service:
    return Service.create(full_store)
