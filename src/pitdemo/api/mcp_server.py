"""MCP server: three tools an agent can use to answer point-in-time consensus questions.

Runs over stdio (Claude Desktop / Claude Code) or streamable HTTP. Tool results
are typed (the Pydantic models in ``pitdemo.schemas``), list sizes are capped,
and failures come back as typed errors (``NotFound``, ``InsufficientEstimates``,
``InvalidRequest``) the model can read and act on.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from pitdemo.schemas import (
    AdjustmentExplanation,
    Basis,
    ConsensusResult,
    CorporateActionList,
    InstantParam,
    PeriodParam,
    TickerParam,
)
from pitdemo.service import Service, ServiceError
from pitdemo.store import Store

MAX_ITEMS = 25
T = TypeVar("T")

INSTRUCTIONS = """\
Point-in-time EPS consensus for a small demo universe (NVDA, AAPL, TSLA, GE, SIVB/SIVBQ).
Analyst estimates are SYNTHETIC (generated, seeded); actuals and corporate actions are real.

How to query:
- Always pass an explicit as_of date. A bare date means end of that day, UTC.
- Prefer fixed fiscal labels (FY2025). FY1/FY2 roll when the company reports; say so if used.
- basis="adjusted" (default) puts every estimate on the latest share basis, so values are
  comparable across splits. basis="unadjusted" shows what a screen showed on as_of.
- When answering, state the value, the as_of date, the basis, and the contributor count,
  and mention that the estimates are synthetic.
- Use explain_adjustment when a split, spin-off or exclusion could change the answer.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


def _call[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ServiceError as exc:
        raise ToolError(json.dumps(exc.to_response().model_dump(mode="json"))) from exc


def create_server(service: Service | None = None, db_path: Path | None = None) -> MCPServer:
    holder: dict[str, Service] = {}
    if service is not None:
        holder["svc"] = service

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        if "svc" in holder:
            yield
            return
        from pitdemo.pipeline import DB_PATH

        store = Store(db_path or DB_PATH, read_only=True)
        holder["svc"] = Service.create(store)
        try:
            yield
        finally:
            store.close()
            holder.pop("svc", None)

    mcp = MCPServer(
        name="pit-consensus",
        title="Point-in-time consensus (demo)",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )

    def svc() -> Service:
        return holder["svc"]

    @mcp.tool(
        title="Get point-in-time EPS consensus",
        annotations=READ_ONLY,
        description=(
            "EPS consensus for one security and fiscal year, exactly as it could have been "
            "computed at `as_of` (no look-ahead in estimates). Returns mean, median, high, low, "
            "stdev and count; every contributing estimate with its share-basis decision; every "
            "excluded estimate with a reason (stale, not re-confirmed after an earnings "
            "release, pre-spin-off perimeter, outlier); the corporate actions applied; and "
            "warnings. `ticker` is resolved as of `as_of` (SIVB stopped trading in 2023; "
            "SEC-#### ids always work). `fiscal_period`: fixed label like 'FY2025' "
            "(preferred) or 'FY1'/'FY2'. `as_of`: ISO date, read as end of that day UTC. "
            "`basis`: 'adjusted' = latest share basis (continuous across splits), "
            "'unadjusted' = share basis on as_of. `known_as_of` (optional ISO date) ignores "
            "corporate actions announced after it. Errors: NotFound (ticker/date), "
            "InsufficientEstimates (<3 qualifying), InvalidRequest."
        ),
    )
    def get_consensus(
        ticker: TickerParam,
        fiscal_period: PeriodParam,
        as_of: InstantParam,
        basis: Basis = "adjusted",
        known_as_of: InstantParam | None = None,
    ) -> ConsensusResult:
        s = svc()
        return _call(
            lambda: s.require_sufficient(
                s.get_consensus(
                    ticker, fiscal_period, as_of, basis, known_as_of, max_items=MAX_ITEMS
                )
            )
        )

    @mcp.tool(
        title="Explain split adjustment and exclusions",
        annotations=READ_ONLY,
        description=(
            "Explain how the adjusted consensus for `ticker` on `date` was built: which "
            "corporate actions (with primary-source URLs) were applied, how each estimate's "
            "share basis was decided (flagged by source / inferred from the split ratio / "
            "guessed from entry date), which estimates were excluded and why, and what a "
            "naive average of raw published values would have shown instead. "
            "`fiscal_period` defaults to FY1 as of `date`."
        ),
    )
    def explain_adjustment(
        ticker: TickerParam, date: dt.date, fiscal_period: PeriodParam = "FY1"
    ) -> AdjustmentExplanation:
        s = svc()
        return _call(lambda: s.explain(ticker, date, fiscal_period, max_items=MAX_ITEMS))

    @mcp.tool(
        title="List corporate actions",
        annotations=READ_ONLY,
        description=(
            "All recorded corporate actions for a security: splits, reverse splits, "
            "spin-offs and delistings, each with announcement time (known_at), record date, "
            "ex-date, share ratio (new shares per old share) and a primary-source URL. "
            "`ticker` may be a current or former ticker, or a SEC-#### id."
        ),
    )
    def list_corporate_actions(ticker: TickerParam) -> CorporateActionList:
        s = svc()
        return _call(lambda: s.corporate_actions(ticker))

    return mcp


def run(transport: Literal["stdio", "streamable-http"] = "stdio", port: int = 8001) -> None:
    server = create_server()
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host="127.0.0.1", port=port)
