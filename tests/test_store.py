from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandera.errors
import polars as pl
import pytest

from pitdemo.schemas import EstimatesFrame
from pitdemo.store import Store
from tests.helpers import NVDA, est, make_store

SRC = Path(__file__).resolve().parents[1] / "src"


def test_no_update_or_delete_paths_in_code() -> None:
    pattern = re.compile(r"\b(UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b", re.I)
    offenders = [
        str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if pattern.search(p.read_text())
    ]
    assert offenders == []


def test_superseded_at_is_derived() -> None:
    store = make_store(
        [est("A", 1.0, "2024-01-02"), est("A", 1.1, "2024-02-01"), est("B", 2.0, "2024-01-03")]
    )
    df = store.estimates_for(NVDA).sort("entered_at")
    assert df["superseded_at"].to_list() == [dt.datetime(2024, 2, 1, 15), None, None]
    assert "superseded_at" not in EstimatesFrame.polars_schema()


def test_active_estimates_window() -> None:
    store = make_store(
        [
            est("A", 1.0, "2024-01-02"),
            est("A", 1.1, "2024-02-01"),
            est("B", 2.0, "2024-01-03"),
            est("B", None, "2024-01-20"),  # stop
        ]
    )
    at = store.active_estimates(NVDA, "FY2025", dt.datetime(2024, 1, 10))
    assert sorted(at["value"].to_list()) == [1.0, 2.0]
    at = store.active_estimates(NVDA, "FY2025", dt.datetime(2024, 1, 25))
    assert at["value"].to_list() == [1.0]
    at = store.active_estimates(NVDA, "FY2025", dt.datetime(2024, 2, 1, 15))
    assert at["value"].to_list() == [1.1]  # boundary: superseded at exactly this instant


def test_versions_only_go_up() -> None:
    store = make_store([est("A", 1.0, "2024-01-02")])
    v1 = store.versions(NVDA)
    store.insert_estimates(
        pl.DataFrame([est("A", 1.2, "2024-01-05")]).cast({"share_basis_date": pl.Date})
    )
    v2 = store.versions(NVDA)
    assert v2["estimates"] == v1["estimates"] + 1
    assert v2["factors"] == v1["factors"]


def test_duplicate_estimate_id_rejected() -> None:
    store = make_store([est("A", 1.0, "2024-01-02", estimate_id="X")])
    with pytest.raises(Exception, match=r"(?i)constraint|duplicate"):
        store.insert_estimates(
            pl.DataFrame([est("B", 2.0, "2024-01-03", estimate_id="X")]).cast(
                {"share_basis_date": pl.Date}
            )
        )


def test_bad_frames_fail_loudly() -> None:
    store = Store()
    bad = pl.DataFrame([est("A", None, "2024-01-02", action="estimate")]).cast(
        {"share_basis_date": pl.Date, "value": pl.Float64}
    )
    with pytest.raises(pandera.errors.SchemaError):
        store.insert_estimates(bad)
    wrong_period = pl.DataFrame([est("A", 1.0, "2024-01-02", period="FY1")]).cast(
        {"share_basis_date": pl.Date}
    )
    with pytest.raises(pandera.errors.SchemaError):
        store.insert_estimates(wrong_period)


def test_read_only_store_cannot_write(tmp_path: Path) -> None:
    path = tmp_path / "x.duckdb"
    make_store([]).close()
    Store(path).close()
    ro = Store(path, read_only=True)
    with pytest.raises(Exception, match=r"(?i)read.only"):
        ro.insert_estimates(
            pl.DataFrame([est("A", 1.0, "2024-01-02")]).cast({"share_basis_date": pl.Date})
        )
