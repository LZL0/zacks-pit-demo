"""DuckDB store with bitemporal, append-only tables.

Rules this module enforces:

* **Append-only.** The only write path is ``_append`` (``INSERT``). There is no
  UPDATE or DELETE anywhere in the codebase; ``tests/test_store.py`` checks that.
* **Nothing derived is stored.** ``superseded_at`` is computed in the
  ``estimates_v`` view as the broker's next ``entered_at`` for the same
  security/metric/period, so a revision never touches the row it replaces.
* **Versions are row counts.** Because tables only grow, ``COUNT(*)`` per security is
  a monotonic version counter: any insert bumps it, and the cache keys on it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl

from pitdemo.schemas import (
    ActualsFrame,
    CorporateActionsFrame,
    EstimatesFrame,
    Frame,
    ReportEventsFrame,
    SecurityIdsFrame,
)

DDL = """
CREATE TABLE IF NOT EXISTS security_ids (
    security_id   TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    cik           TEXT NOT NULL,
    name          TEXT NOT NULL,
    valid_from    DATE,
    valid_to      DATE,
    delisted_on   DATE,
    delist_reason TEXT
);

-- Individual analyst estimates. Never updated in place.
CREATE TABLE IF NOT EXISTS estimates (
    estimate_id      TEXT PRIMARY KEY,
    security_id      TEXT NOT NULL,
    broker_id        TEXT NOT NULL,
    metric           TEXT NOT NULL,
    fiscal_period    TEXT NOT NULL,
    action           TEXT NOT NULL,     -- 'estimate' | 'confirm' | 'stop'
    value            DOUBLE,            -- NULL only for 'stop'
    share_basis_date DATE,              -- share-count basis, if the source flagged it
    entered_at       TIMESTAMP NOT NULL,-- knowledge time
    source           TEXT NOT NULL      -- 'SYNTHETIC' in this demo
);

CREATE OR REPLACE VIEW estimates_v AS
SELECT
    e.*,
    LEAD(entered_at) OVER (
        PARTITION BY security_id, broker_id, metric, fiscal_period
        ORDER BY entered_at, estimate_id
    ) AS superseded_at
FROM estimates e;

-- Corporate actions with knowledge time.
CREATE TABLE IF NOT EXISTS adjustment_factors (
    security_id TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    action_type TEXT NOT NULL,
    known_at    TIMESTAMP NOT NULL,
    record_date DATE,
    ex_date     DATE NOT NULL,
    ratio       DOUBLE NOT NULL,       -- new shares per old share
    terms       TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    notes       TEXT
);

-- As-reported actuals; restatements are new rows.
CREATE TABLE IF NOT EXISTS actuals (
    security_id   TEXT NOT NULL,
    metric        TEXT NOT NULL,
    fiscal_period TEXT NOT NULL,
    period_end    DATE NOT NULL,
    value         DOUBLE NOT NULL,
    basis         TEXT NOT NULL,       -- 'GAAP_DILUTED'
    filed_at      TIMESTAMP NOT NULL,
    accession     TEXT NOT NULL,
    form          TEXT NOT NULL
);

-- Earnings releases (knowledge time of "the company reported").
CREATE TABLE IF NOT EXISTS report_events (
    security_id TEXT NOT NULL,
    reported_at TIMESTAMP NOT NULL,
    accession   TEXT NOT NULL,
    source      TEXT NOT NULL
);
"""

TABLES: dict[str, type[Frame]] = {
    "security_ids": SecurityIdsFrame,
    "estimates": EstimatesFrame,
    "adjustment_factors": CorporateActionsFrame,
    "actuals": ActualsFrame,
    "report_events": ReportEventsFrame,
}


class Store:
    def __init__(self, path: Path | str | None = None, *, read_only: bool = False) -> None:
        self.path = str(path) if path is not None else ":memory:"
        self.con = duckdb.connect(self.path, read_only=read_only)
        if not read_only:
            self.con.sql(DDL)

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------ writes

    def _append(self, table: str, df: pl.DataFrame) -> int:
        model = TABLES[table]
        validated = model.validate(df.select(list(model.polars_schema())))
        if validated.height == 0:
            return 0
        cols = ", ".join(model.polars_schema())
        self.con.register("_incoming", validated.to_arrow())
        try:
            self.con.sql(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming")
        finally:
            self.con.unregister("_incoming")
        return validated.height

    def insert_security_ids(self, df: pl.DataFrame) -> int:
        return self._append("security_ids", df)

    def insert_estimates(self, df: pl.DataFrame) -> int:
        return self._append("estimates", df)

    def insert_corporate_actions(self, df: pl.DataFrame) -> int:
        return self._append("adjustment_factors", df)

    def insert_actuals(self, df: pl.DataFrame) -> int:
        return self._append("actuals", df)

    def insert_report_events(self, df: pl.DataFrame) -> int:
        return self._append("report_events", df)

    # ------------------------------------------------------------------- reads

    def _rows(self, sql: str, params: object = None) -> list[tuple[object, ...]]:
        return list(self.con.sql(sql, params=params).fetchall())

    def _frame(self, sql: str, params: list[object] | None = None) -> pl.DataFrame:
        table = self.con.sql(sql, params=params or None).to_arrow_table()
        df = pl.from_arrow(table)
        assert isinstance(df, pl.DataFrame)
        return df

    def security_ids(self) -> pl.DataFrame:
        return self._frame(
            "SELECT * FROM security_ids ORDER BY security_id, valid_from NULLS FIRST"
        )

    def estimates_for(self, security_id: str, fiscal_period: str | None = None) -> pl.DataFrame:
        """Every estimate row for a security (optionally one period), with ``superseded_at``."""
        sql = "SELECT * FROM estimates_v WHERE security_id = ?"
        params: list[object] = [security_id]
        if fiscal_period is not None:
            sql += " AND fiscal_period = ?"
            params.append(fiscal_period)
        df = self._frame(
            sql + " ORDER BY fiscal_period, broker_id, entered_at, estimate_id", params
        )
        if df.height == 0:
            return EstimatesFrame.empty_frame().with_columns(
                pl.lit(None, dtype=pl.Datetime("us")).alias("superseded_at")
            )
        return df

    def active_estimates(
        self, security_id: str, fiscal_period: str, as_of: dt.datetime, metric: str = "EPS"
    ) -> pl.DataFrame:
        """Estimates live at ``as_of``: entered by then and not yet superseded or stopped."""
        return self._frame(
            """
            SELECT * FROM estimates_v
            WHERE security_id = ? AND fiscal_period = ? AND metric = ?
              AND entered_at <= ?
              AND (superseded_at IS NULL OR superseded_at > ?)
              AND action <> 'stop'
            ORDER BY broker_id
            """,
            [security_id, fiscal_period, metric, as_of, as_of],
        )

    def corporate_actions(self, security_id: str) -> pl.DataFrame:
        return self._frame(
            "SELECT * FROM adjustment_factors WHERE security_id = ? ORDER BY ex_date, known_at",
            [security_id],
        )

    def report_times(self, security_id: str) -> list[dt.datetime]:
        rows = self._rows(
            "SELECT reported_at FROM report_events WHERE security_id = ? ORDER BY reported_at",
            [security_id],
        )
        return [r[0] for r in rows if isinstance(r[0], dt.datetime)]

    def actuals(
        self,
        security_id: str,
        fiscal_period: str | None = None,
        known_as_of: dt.datetime | None = None,
    ) -> pl.DataFrame:
        """As-reported actuals, every filing, optionally limited to what was filed by a time."""
        sql = "SELECT * FROM actuals WHERE security_id = ?"
        params: list[object] = [security_id]
        if fiscal_period is not None:
            sql += " AND fiscal_period = ?"
            params.append(fiscal_period)
        if known_as_of is not None:
            sql += " AND filed_at <= ?"
            params.append(known_as_of)
        return self._frame(sql + " ORDER BY fiscal_period, filed_at, accession", params)

    def actual_as_of(
        self, security_id: str, fiscal_period: str, known_as_of: dt.datetime
    ) -> float | None:
        """The value a user would have seen at ``known_as_of``: the latest filing by then."""
        df = self.actuals(security_id, fiscal_period, known_as_of)
        return None if df.height == 0 else float(df["value"][-1])

    def versions(self, security_id: str) -> dict[str, int]:
        """Monotonic per-security counters (row counts of append-only tables)."""
        (row,) = self._rows(
            """
            SELECT
              (SELECT COUNT(*) FROM estimates WHERE security_id = $sid),
              (SELECT COUNT(*) FROM adjustment_factors WHERE security_id = $sid),
              (SELECT COUNT(*) FROM report_events WHERE security_id = $sid)
            """,
            {"sid": security_id},
        )
        e, f, r = (int(str(x)) for x in row)
        return {"estimates": e, "factors": f, "reports": r}
