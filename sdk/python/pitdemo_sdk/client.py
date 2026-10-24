"""Thin httpx client. Every response is parsed into a generated Pydantic model."""

from __future__ import annotations

import datetime as dt
from typing import Literal, TypeVar

import httpx
import polars as pl
from pydantic import BaseModel

from pitdemo_sdk.models import (
    AdjustmentExplanation,
    ConsensusResult,
    ConsensusSeries,
    CorporateActionList,
    ErrorResponse,
    RevisionSignal,
)

M = TypeVar("M", bound=BaseModel)
Basis = Literal["adjusted", "unadjusted"]

SERIES_SCHEMA: dict[str, pl.DataType] = {
    "security_id": pl.String(),
    "fiscal_period": pl.String(),
    "basis": pl.String(),
    "as_of": pl.Date(),
    "mean": pl.Float64(),
    "median": pl.Float64(),
    "count": pl.Int64(),
    "insufficient": pl.Boolean(),
}


class PitApiError(Exception):
    """The API returned a typed error (NotFound, InsufficientEstimates, InvalidRequest)."""

    def __init__(self, status: int, error: ErrorResponse) -> None:
        super().__init__(f"{status} {error.error.value}: {error.message}")
        self.status = status
        self.error = error


def _iso(value: str | dt.date | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


class PitClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def __enter__(self) -> PitClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, model: type[M], params: dict[str, str | int | None]) -> M:
        resp = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
        if resp.status_code >= 400:
            raise PitApiError(resp.status_code, ErrorResponse.model_validate(resp.json()))
        return model.model_validate(resp.json())

    def consensus(
        self,
        ticker: str,
        period: str,
        as_of: str | dt.date,
        basis: Basis = "adjusted",
        known_as_of: str | dt.date | None = None,
    ) -> ConsensusResult:
        """Point-in-time consensus. ``as_of`` as a date means end of that day, UTC."""
        return self._get(
            "/consensus",
            ConsensusResult,
            {
                "ticker": ticker,
                "period": period,
                "as_of": _iso(as_of),
                "basis": basis,
                "known_as_of": _iso(known_as_of),
            },
        )

    def consensus_series(
        self,
        ticker: str,
        period: str,
        start: str | dt.date,
        end: str | dt.date,
        basis: Basis = "adjusted",
        known_as_of: str | dt.date | None = None,
    ) -> ConsensusSeries:
        return self._get(
            "/consensus/series",
            ConsensusSeries,
            {
                "ticker": ticker,
                "period": period,
                "start": _iso(start),
                "end": _iso(end),
                "basis": basis,
                "known_as_of": _iso(known_as_of),
            },
        )

    def consensus_frame(
        self,
        ticker: str,
        period: str,
        start: str | dt.date,
        end: str | dt.date,
        basis: Basis = "adjusted",
    ) -> pl.DataFrame:
        """Bulk: one row per trading day, as a Polars frame with a fixed schema.

        The rows were already validated as Pydantic models; the frame is then checked
        for the invariants a consumer relies on (unique, sorted dates; stats present
        exactly when the point is sufficient).
        """
        series = self.consensus_series(ticker, period, start, end, basis)
        df = pl.DataFrame(
            [
                {
                    "security_id": series.security.security_id,
                    "fiscal_period": series.fiscal_period,
                    "basis": series.basis.value,
                    "as_of": p.as_of,
                    "mean": p.mean,
                    "median": p.median,
                    "count": p.count,
                    "insufficient": p.insufficient,
                }
                for p in series.points
            ],
            schema=SERIES_SCHEMA,
        )
        if df["as_of"].n_unique() != df.height or not df["as_of"].is_sorted():
            raise ValueError("series dates must be unique and ascending")
        bad = df.filter(pl.col("insufficient") == pl.col("mean").is_not_null())
        if bad.height:
            raise ValueError("mean must be present exactly when the point is sufficient")
        return df

    def corporate_actions(self, ticker: str) -> CorporateActionList:
        return self._get("/corporate-actions", CorporateActionList, {"ticker": ticker})

    def explain(
        self, ticker: str, date: str | dt.date, period: str = "FY1"
    ) -> AdjustmentExplanation:
        return self._get(
            "/explain",
            AdjustmentExplanation,
            {"ticker": ticker, "date": _iso(date), "period": period},
        )

    def revisions(
        self, ticker: str, period: str, as_of: str | dt.date, lookback_days: int = 30
    ) -> RevisionSignal:
        return self._get(
            "/revisions",
            RevisionSignal,
            {
                "ticker": ticker,
                "period": period,
                "as_of": _iso(as_of),
                "lookback_days": lookback_days,
            },
        )
