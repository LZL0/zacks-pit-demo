"""End-to-end wiring: raw files -> validated frames -> synthetic estimates -> DuckDB."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from pitdemo.ids import SECURITIES, security_ids_frame
from pitdemo.ingest.actions import load_corporate_actions
from pitdemo.ingest.edgar import load_raw, parse_actuals, parse_report_events
from pitdemo.ingest.synth import SEED, generate
from pitdemo.store import Store
from pitdemo.transforms.factors import Factor, factors_from_frame

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
ESTIMATES_PATH = ROOT / "data" / "synthetic" / "estimates.parquet"
DB_PATH = ROOT / "data" / "pitdemo.duckdb"


@dataclass(frozen=True)
class RawData:
    actions: pl.DataFrame
    actuals: dict[str, pl.DataFrame]
    reports: dict[str, pl.DataFrame]

    def factors(self, security_id: str) -> list[Factor]:
        return factors_from_frame(self.actions.filter(pl.col("security_id") == security_id))

    def report_times(self, security_id: str) -> list[dt.datetime]:
        return list(self.reports[security_id]["reported_at"])


def load_raw_data(raw_dir: Path = RAW_DIR) -> RawData:
    actions = load_corporate_actions(raw_dir / "corporate_actions.csv")
    actuals: dict[str, pl.DataFrame] = {}
    reports: dict[str, pl.DataFrame] = {}
    for sec in SECURITIES:
        raw = load_raw(raw_dir, sec.cik)
        actuals[sec.security_id] = parse_actuals(raw, sec)
        reports[sec.security_id] = parse_report_events(raw, sec)
    return RawData(actions, actuals, reports)


def synthesize(raw: RawData, seed: int = SEED) -> pl.DataFrame:
    ids = [s.security_id for s in SECURITIES]
    return generate(
        raw.actuals,
        {sid: raw.report_times(sid) for sid in ids},
        {sid: raw.factors(sid) for sid in ids},
        seed=seed,
    )


def build_store(store: Store, raw: RawData, estimates: pl.DataFrame) -> dict[str, int]:
    counts = {
        "security_ids": store.insert_security_ids(security_ids_frame()),
        "adjustment_factors": store.insert_corporate_actions(raw.actions),
        "actuals": sum(store.insert_actuals(df) for df in raw.actuals.values()),
        "report_events": sum(store.insert_report_events(df) for df in raw.reports.values()),
        "estimates": store.insert_estimates(estimates),
    }
    return counts


def build_database(
    db_path: Path = DB_PATH,
    raw_dir: Path = RAW_DIR,
    estimates_path: Path = ESTIMATES_PATH,
) -> dict[str, int]:
    """Rebuild the DuckDB file from scratch (the file is a cache of the raw inputs)."""
    for p in (db_path, db_path.with_suffix(".duckdb.wal")):
        p.unlink(missing_ok=True)
    store = Store(db_path)
    try:
        return build_store(store, load_raw_data(raw_dir), pl.read_parquet(estimates_path))
    finally:
        store.close()


def memory_store(raw_dir: Path = RAW_DIR, estimates_path: Path | None = ESTIMATES_PATH) -> Store:
    """An in-memory store with the full dataset (used by tests and as a fallback)."""
    raw = load_raw_data(raw_dir)
    est = (
        pl.read_parquet(estimates_path)
        if estimates_path is not None and estimates_path.exists()
        else synthesize(raw)
    )
    store = Store()
    build_store(store, raw, est)
    return store
