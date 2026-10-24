"""HTTP API (FastAPI). Every response model comes from ``pitdemo.schemas``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from pitdemo.schemas import (
    AdjustmentExplanation,
    Basis,
    ConsensusResult,
    ConsensusSeries,
    CorporateActionList,
    ErrorResponse,
    RevisionSignal,
)
from pitdemo.service import Service, ServiceError
from pitdemo.store import Store

STATUS = {"NotFound": 404, "InsufficientEstimates": 422, "InvalidRequest": 400}
ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
}


def create_app(service: Service | None = None, db_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            yield
            return
        from pitdemo.pipeline import DB_PATH

        store = Store(db_path or DB_PATH, read_only=True)
        app.state.service = Service.create(store)
        try:
            yield
        finally:
            store.close()

    app = FastAPI(
        title="Point-in-time consensus demo",
        version="0.1.0",
        description=(
            "Point-in-time EPS consensus with per-estimate split adjustment. "
            "Analyst estimates are SYNTHETIC; actuals (SEC EDGAR) and corporate actions "
            "(primary sources) are real."
        ),
        lifespan=lifespan,
    )
    if service is not None:
        app.state.service = service

    def svc(request: Request) -> Service:
        s = request.app.state.service
        assert isinstance(s, Service)
        return s

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=STATUS[exc.kind], content=exc.to_response().model_dump(mode="json")
        )

    @app.get("/consensus", response_model=ConsensusResult, responses=ERRORS)
    def get_consensus(
        request: Request,
        ticker: Annotated[str, Query(description="Ticker as of `as_of`, or a SEC-#### id.")],
        period: Annotated[str, Query(description="FY2025-style label, or FY1/FY2.")],
        as_of: Annotated[str, Query(description="ISO date (end of day UTC) or datetime.")],
        basis: Annotated[Basis, Query()] = "adjusted",
        known_as_of: Annotated[
            str | None, Query(description="Corporate-action knowledge cutoff.")
        ] = None,
    ) -> ConsensusResult:
        return svc(request).get_consensus(ticker, period, as_of, basis, known_as_of)

    @app.get("/consensus/series", response_model=ConsensusSeries, responses=ERRORS)
    def get_series(
        request: Request,
        ticker: str,
        period: str,
        start: str,
        end: str,
        basis: Basis = "adjusted",
        known_as_of: str | None = None,
    ) -> ConsensusSeries:
        return svc(request).get_series(ticker, period, start, end, basis, known_as_of)

    @app.get("/corporate-actions", response_model=CorporateActionList, responses=ERRORS)
    def get_corporate_actions(request: Request, ticker: str) -> CorporateActionList:
        return svc(request).corporate_actions(ticker)

    @app.get("/explain", response_model=AdjustmentExplanation, responses=ERRORS)
    def get_explain(
        request: Request, ticker: str, date: str, period: str = "FY1"
    ) -> AdjustmentExplanation:
        return svc(request).explain(ticker, date, period, max_items=None)

    @app.get("/revisions", response_model=RevisionSignal, responses=ERRORS)
    def get_revisions(
        request: Request, ticker: str, period: str, as_of: str, lookback_days: int = 30
    ) -> RevisionSignal:
        return svc(request).revisions(ticker, period, as_of, lookback_days)

    @app.get("/universe")
    def get_universe(request: Request, date: str) -> list[dict[str, str]]:
        """Securities listed on `date` (delisted names drop out; their history stays)."""
        return [{"security_id": sid, "ticker": t} for sid, t in svc(request).universe(date)]

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, object]:
        s = svc(request)
        return {"ok": True, "cache": {"size": len(s.cache), **s.cache.stats.__dict__}}

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "docs": "/docs",
            "example": "/consensus?ticker=NVDA&period=FY2025&as_of=2024-05-31&basis=adjusted",
        }

    return app
