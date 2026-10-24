# AGENTS.md

Guidance for coding agents working in this repo. Read `README.md` for what the project
does and `docs/methodology.md` for the consensus rules; this file covers how to work on it
without breaking what it guarantees.

## What this is

A point-in-time (PIT) EPS consensus pipeline: per-estimate share-basis handling around
stock splits, bitemporal append-only storage, served over HTTP (FastAPI) and MCP, with
Python and TypeScript SDKs generated from one schema. **Analyst estimates are synthetic**
(seed 42); EDGAR actuals, earnings-release times and corporate actions are real.

## Setup and commands

```bash
uv sync                                   # Python 3.12+; dev tools included
npm ci --prefix sdk/ts                    # only needed for the TS SDK / its tests
uv run pytest                             # all tests (offline)
uv run ruff check && uv run ruff format --check
uv run mypy src --strict && uv run mypy --strict sdk/python/pitdemo_sdk scripts/record scripts/ci
uv run python scripts/ci/smoke.py         # end to end: server + both SDKs + MCP stdio
uv run demo build | serve | mcp | chart | sdk | synth | query NVDA FY2025 2024-05-31
```

`demo ingest` and `scripts/ci/verify_sources.py` need network and `SEC_USER_AGENT="Name email"`.
Everything else runs offline from the committed `data/raw/` and `data/synthetic/`.

## Invariants (do not break)

- **Point-in-time.** Nothing with a knowledge time after `as_of` may affect a result:
  estimates by `entered_at`, releases by `reported_at`, actuals by `filed_at`, corporate
  actions by `known_at` (bounded by `known_as_of`). Basis inference may only look at a
  broker's *earlier* rows. Any look-ahead is a severe bug.
- **Append-only store.** No `UPDATE`/`DELETE`/`TRUNCATE`/`DROP TABLE` anywhere in `src/`
  (a test greps for it). Revisions are new rows; `superseded_at` is derived in the
  `estimates_v` view; data versions are row counts and the cache key depends on them.
- **Time.** Internally every timestamp is naive UTC. On the wire it is explicit UTC
  (`...Z`, see `UtcDateTime` in `schemas.py`). A bare date `as_of` means end of that day.
  When only a date is known, use 23:59:59 that day (never earlier).
- **SEC timestamps.** The submissions API's `acceptanceDateTime` ends in `Z` but is shifted
  by the Eastern offset. Always go through `ingest/edgar.py:parse_acceptance`.
- **Identifiers.** Key everything on `security_id` (`SEC-0001`). Tickers are resolved as of
  a date (`ids.py`); SIVB became SIVBQ and was delisted, and its history must stay queryable.
- **Corporate actions.** Only `SPLIT`/`REVERSE_SPLIT` produce share factors. Spin-offs get
  no EPS factor (pre-spin estimates are excluded instead). Every row in
  `data/raw/corporate_actions.csv` cites a primary source (SEC filing, FDIC, exchange or
  OCC notice) that you have opened and confirmed; `known_at` is that filing's acceptance
  time in UTC.
- **Single source of truth for shapes.** Frames entering the store are validated by the
  Pandera models in `schemas.py`. Everything crossing the API boundary is a Pydantic
  model in `schemas.py`: responses, and request query models built from shared
  constrained types (`TickerParam`, `PeriodParam`, `InstantParam`) that the HTTP API,
  the MCP tool signatures and both SDKs all use. Third-party payloads (SEC, Yahoo) are
  validated by the models in `ingest/upstream.py` before anything reads them. The
  Python SDK parses responses with `strict=True`; keep it that way.

## Generated files: regenerate, never hand-edit

| File(s) | Regenerate with |
|---|---|
| `openapi.json`, `sdk/schema.json`, `sdk/python/pitdemo_sdk/models.py`, `sdk/ts/src/schemas.ts`, `sdk/mcp-tools.json` | `uv run demo sdk` (CI fails on drift) |
| `data/synthetic/estimates.parquet` | `uv run demo synth` (a test fails if stale) |
| `docs/nvda_consensus.png` | `uv run demo chart` |
| `docs/demo.cast`, `docs/demo.mp4`, `docs/demo.gif` | `scripts/record/capture.py` then `render.py` (runs real Claude Code) |

After changing consensus logic or the synthetic data, run `uv run pytest
tests/test_docs_numbers.py`: it recomputes every figure quoted in the README,
methodology and agent skill, and its failure message tells you which sentence to update.

## Tests

- Golden tests (`tests/golden/`, one file per edge case) build small stores by hand with
  `tests/helpers.py` (`make_store`, `est`) and assert exact values. Seeded-dataset tests
  assert structure and ranges, not exact numbers, so regenerating data doesn't break them.
- A bug fix comes with a regression test that **fails on the old code**; check that it does.
- Compare floats with a tolerance (the mean of three 1.94s is 1.9400000000000002).
- Real-data tests pin values to primary sources (e.g. SEC filing-header times); don't
  update a pinned value without re-checking the source.

## Gotchas

- `.claude/skills/**/SKILL.md`: never write `$` followed by a digit (skills substitute
  arguments there); write `USD 2.59`. A test enforces this.
- DuckDB 2: use `con.sql(...)`; `con.execute(...)` leaves an unfinished result that blocks
  the next statement.
- MCP Python SDK 2.x: the server class is `MCPServer` (`mcp.server.mcpserver`), not FastMCP.
- Don't commit price data (`data/raw/prices/`, third-party), the DuckDB file, or
  `sdk/ts/node_modules` / `dist`.
- The `mypy` config targets Python 3.12; CI tests 3.12 through 3.15.
- The API version is the dated `API_VERSION` in `src/pitdemo/__init__.py` (it becomes
  `info.version` in `openapi.json`). A breaking change to a request or response model
  means a new dated version, not an edit in place.

## Workflow

Work on a branch and open a PR; CI runs lint, mypy, tests on Python 3.12–3.15, the SDK
drift check and the end-to-end smoke job. Renovate manages dependency updates. A weekly
workflow (`verify-sources.yml`) re-verifies SEC history, split cross-checks and source URLs.
