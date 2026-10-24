"""Load the hand-curated corporate actions file (every row cites a primary source)."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from pitdemo.schemas import CorporateActionsFrame


def load_corporate_actions(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, try_parse_dates=False, infer_schema=False)
    df = df.with_columns(
        pl.col("known_at").str.to_datetime("%Y-%m-%dT%H:%M:%S", time_unit="us"),
        pl.col("record_date").str.to_date("%Y-%m-%d", strict=False),
        pl.col("ex_date").str.to_date("%Y-%m-%d"),
        pl.col("ratio").cast(pl.Float64),
    ).select(list(CorporateActionsFrame.polars_schema()))
    return CorporateActionsFrame.validate(df.sort("security_id", "ex_date"))
