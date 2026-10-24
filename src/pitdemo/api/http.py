"""HTTP API (FastAPI). Every response model comes from ``pitdemo.schemas``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pitdemo import API_VERSION
from pitdemo.schemas import (
    AdjustmentExplanation,
    CacheInfo,
    ConsensusQuery,
    ConsensusResult,
    ConsensusSeries,
    CorporateActionList,
    CorporateActionsQuery,
    ErrorResponse,
    ExplainQuery,
    Health,
    RevisionSignal,
    RevisionsQuery,
    SeriesQuery,
    Universe,
    UniverseMember,
    UniverseQuery,
)
from pitdemo.service import Service, ServiceError, describe_validation_errors
from pitdemo.store import Store

STATUS = {"NotFound": 404, "InsufficientEstimates": 422, "InvalidRequest": 400}

# Documented error responses. Too few estimates is not an HTTP error: it is a 200 with
# `insufficient: true` and the exclusions that caused it.
ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "InvalidRequest: a parameter is malformed"},
    404: {
        "model": ErrorResponse,
        "description": "NotFound: unknown ticker, or ticker not in use on the requested date",
    },
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
        version=API_VERSION,
        description=(
            "Point-in-time EPS consensus with per-estimate split adjustment. "
            "Analyst estimates are SYNTHETIC; actuals (SEC EDGAR) and corporate actions "
            "(primary sources) are real."
        ),
        lifespan=lifespan,
        servers=[{"url": "http://127.0.0.1:8000", "description": "Local (`uv run demo serve`)"}],
    )
    if service is not None:
        app.state.service = service

    generate_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        """FastAPI documents a 422 for every endpoint with parameters, but validation
        errors here are typed 400 ``InvalidRequest`` responses (see the handler below),
        so the default 422 and its schemas are removed to keep the spec truthful."""
        if app.openapi_schema is None:
            spec = generate_openapi()
            for path in spec.get("paths", {}).values():
                for operation in path.values():
                    operation.get("responses", {}).pop("422", None)
            for name in ("HTTPValidationError", "ValidationError"):
                spec.get("components", {}).get("schemas", {}).pop(name, None)
            app.openapi_schema = spec
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]

    def svc(request: Request) -> Service:
        s = request.app.state.service
        assert isinstance(s, Service)
        return s

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(
            error="InvalidRequest",
            message=describe_validation_errors(exc.errors()),
            hint="See /docs for parameter types and allowed values.",
        )
        return JSONResponse(status_code=400, content=body.model_dump(mode="json"))

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=STATUS[exc.kind], content=exc.to_response().model_dump(mode="json")
        )

    # Query parameters are Pydantic models (schemas.py), shared with the SDKs.

    @app.get("/consensus", response_model=ConsensusResult, responses=ERRORS)
    def get_consensus(request: Request, q: Annotated[ConsensusQuery, Query()]) -> ConsensusResult:
        return svc(request).get_consensus(q.ticker, q.period, q.as_of, q.basis, q.known_as_of)

    @app.get("/consensus/series", response_model=ConsensusSeries, responses=ERRORS)
    def get_series(request: Request, q: Annotated[SeriesQuery, Query()]) -> ConsensusSeries:
        return svc(request).get_series(q.ticker, q.period, q.start, q.end, q.basis, q.known_as_of)

    @app.get("/corporate-actions", response_model=CorporateActionList, responses=ERRORS)
    def get_corporate_actions(
        request: Request, q: Annotated[CorporateActionsQuery, Query()]
    ) -> CorporateActionList:
        return svc(request).corporate_actions(q.ticker)

    @app.get("/explain", response_model=AdjustmentExplanation, responses=ERRORS)
    def get_explain(request: Request, q: Annotated[ExplainQuery, Query()]) -> AdjustmentExplanation:
        return svc(request).explain(q.ticker, q.date, q.period, max_items=None)

    @app.get("/revisions", response_model=RevisionSignal, responses=ERRORS)
    def get_revisions(request: Request, q: Annotated[RevisionsQuery, Query()]) -> RevisionSignal:
        return svc(request).revisions(q.ticker, q.period, q.as_of, q.lookback_days)

    @app.get("/universe", response_model=Universe, responses=ERRORS)
    def get_universe(request: Request, q: Annotated[UniverseQuery, Query()]) -> Universe:
        """Securities listed on `date` (delisted names drop out; their history stays)."""
        members = [
            UniverseMember(security_id=sid, ticker=t) for sid, t in svc(request).universe(q.date)
        ]
        return Universe(date=q.date, securities=members)

    @app.get("/healthz", response_model=Health)
    def healthz(request: Request) -> Health:
        s = svc(request)
        return Health(
            ok=True,
            api_version=API_VERSION,
            cache=CacheInfo(
                size=len(s.cache), hits=s.cache.stats.hits, misses=s.cache.stats.misses
            ),
        )

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "docs": "/docs",
            "example": "/consensus?ticker=NVDA&period=FY2025&as_of=2024-05-31&basis=adjusted",
        }

    return app
