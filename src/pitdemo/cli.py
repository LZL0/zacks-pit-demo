"""``demo`` command-line entry point."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from pitdemo.pipeline import DB_PATH, ESTIMATES_PATH, RAW_DIR, ROOT

app = typer.Typer(
    help="Point-in-time consensus demo: ingest, synth, build, chart, serve, mcp.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def ingest(
    prices: Annotated[bool, typer.Option(help="Also fetch prices and cross-check splits.")] = True,
) -> None:
    """Fetch SEC EDGAR data (and prices) into data/raw. Needs network.

    Set SEC_USER_AGENT="Your Name you@example.com" first; SEC asks for a contact.
    """
    from pitdemo.ids import SECURITIES, TICKER_SPANS
    from pitdemo.ingest.actions import load_corporate_actions
    from pitdemo.ingest.edgar import fetch_all
    from pitdemo.ingest.prices import cross_check_splits, fetch_prices
    from pitdemo.pipeline import load_raw_data

    for path in fetch_all(RAW_DIR, SECURITIES):
        typer.echo(f"wrote {path.relative_to(ROOT)}")
    raw = load_raw_data(RAW_DIR)  # parses and validates everything; fails loudly
    for sid, df in raw.actuals.items():
        typer.echo(f"{sid}: {df.height} actuals rows, {raw.reports[sid].height} earnings releases")
    if not prices:
        return
    actions = load_corporate_actions(RAW_DIR / "corporate_actions.csv")
    tickers = {s.ticker: s.security_id for s in TICKER_SPANS if s.ticker != "SIVBQ"}
    start, end = dt.date(2020, 1, 1), dt.date(2026, 6, 30)
    fetched = fetch_prices(RAW_DIR / "prices", tickers, start, end)
    check = cross_check_splits(actions, fetched, start, end)
    for note in check.notes:
        typer.echo(f"note: {note}")
    if check.errors:
        for err in check.errors:
            typer.echo(f"ERROR: {err}", err=True)
        raise typer.Exit(1)
    typer.echo("split cross-check passed")


@app.command()
def synth(seed: int = 42) -> None:
    """Generate SYNTHETIC analyst estimates into data/synthetic/estimates.parquet."""
    from pitdemo.pipeline import load_raw_data, synthesize

    df = synthesize(load_raw_data(RAW_DIR), seed=seed)
    ESTIMATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(ESTIMATES_PATH)
    typer.echo(f"wrote {df.height} synthetic estimates to {ESTIMATES_PATH.relative_to(ROOT)}")


@app.command()
def build() -> None:
    """Load everything into DuckDB (data/pitdemo.duckdb)."""
    from pitdemo.pipeline import build_database

    counts = build_database(DB_PATH, RAW_DIR, ESTIMATES_PATH)
    for table, n in counts.items():
        typer.echo(f"{table:20s} {n:6d} rows")
    typer.echo(f"built {DB_PATH.relative_to(ROOT)}")


def _ensure_db() -> None:
    if not DB_PATH.exists():
        typer.echo("no database yet; building it first", err=True)
        build()


@app.command()
def chart(
    out: Annotated[Path, typer.Option(help="Output PNG.")] = ROOT / "docs" / "nvda_consensus.png",
) -> None:
    """Draw naive vs. correct NVDA FY2025 consensus around the June 2024 split."""
    from pitdemo.chart import render_nvda_chart
    from pitdemo.pipeline import memory_store

    store = memory_store(RAW_DIR, ESTIMATES_PATH)
    path = render_nvda_chart(store, out)
    typer.echo(f"wrote {path}")


@app.command()
def sdk() -> None:
    """Regenerate openapi.json, sdk/schema.json, both SDKs' models and sdk/mcp-tools.json."""
    import shutil
    import subprocess

    from pitdemo.sdkgen import SDK, write_all

    for path in write_all():
        typer.echo(f"wrote {path.relative_to(ROOT)}")
    ts = SDK / "ts"
    npm = shutil.which("npm")
    if npm is None:
        typer.echo("npm not found; skipped the TypeScript SDK", err=True)
        return
    if not (ts / "node_modules").exists():
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=ts, check=True)
    subprocess.run([npm, "run", "-s", "gen"], cwd=ts, check=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the FastAPI server."""
    import uvicorn

    from pitdemo.api.http import create_app

    _ensure_db()
    uvicorn.run(create_app(db_path=DB_PATH), host=host, port=port)


@app.command()
def mcp(
    transport: Annotated[
        str, typer.Option(help="stdio (Claude Desktop/Code) or streamable-http.")
    ] = "stdio",
    port: int = 8001,
) -> None:
    """Run the MCP server (stdio by default)."""
    from pitdemo.api.mcp_server import run

    if not DB_PATH.exists():
        # stdout belongs to the MCP protocol in stdio mode, so log to stderr only.
        print("no database yet; building it first", file=sys.stderr)
        from pitdemo.pipeline import build_database

        build_database(DB_PATH, RAW_DIR, ESTIMATES_PATH)
    if transport not in ("stdio", "streamable-http"):
        raise typer.BadParameter("transport must be stdio or streamable-http")
    mode: Literal["stdio", "streamable-http"] = (
        "stdio" if transport == "stdio" else ("streamable-http")
    )
    run(mode, port)


@app.command()
def query(
    ticker: str,
    period: str,
    as_of: str,
    basis: str = "adjusted",
    known_as_of: str | None = None,
) -> None:
    """Print a consensus as JSON (handy for checking the CLI and the API agree)."""
    from pitdemo.service import Service, ServiceError
    from pitdemo.store import Store

    _ensure_db()
    store = Store(DB_PATH, read_only=True)
    try:
        result = Service.create(store).get_consensus(
            ticker,
            period,
            as_of,
            "unadjusted" if basis == "unadjusted" else "adjusted",
            known_as_of,
        )
        typer.echo(result.model_dump_json(indent=2))
    except ServiceError as exc:
        typer.echo(exc.to_response().model_dump_json(indent=2), err=True)
        raise typer.Exit(1) from exc
    finally:
        store.close()


if __name__ == "__main__":
    app()
