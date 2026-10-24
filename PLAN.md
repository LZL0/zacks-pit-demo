# PLAN.md — Point-in-Time Consensus Demo (`zacks-pit-demo`)

## Goal

Build a small, working demo that shows how financial estimates data should be stored, adjusted and served:

1. A **naive consensus breaks** around a real stock split (fake ~90% collapse on NVDA, June 2024).
2. **This pipeline handles it correctly**: point-in-time, per-estimate split adjustment, tested.
3. The result is **served to humans and agents**: typed Python/TS SDKs and an MCP server Claude can query.

Audience: Paul Beland (GM, Research and Data Solutions, Zacks). Target: link in a LinkedIn DM.
Timebox: one weekend (~12–16 hours of work).

## Non-goals

- No real analyst estimates. They are licensed; we generate synthetic ones and say so clearly.
- No production infra, auth, or deployment. Local + GitHub only.
- No UI beyond one chart and one screen recording.
- No more than 5 securities.

## Success criteria

- [x] `uv run pytest` passes, including every golden edge-case test.
- [x] `uv run demo chart` produces `docs/nvda_consensus.png`: naive vs. correct consensus around the NVDA split.
- [x] Claude (Desktop or Code) answers via MCP: *"What was NVDA's FY2025 EPS consensus on 2024-05-31, split-adjusted?"* with value, as-of date and method.
- [x] Python SDK (Pydantic) and TS SDK (Zod) generated from one schema; CI fails if they drift.
- [x] `docs/methodology.md` explains consensus construction and corporate-action handling in under 2 pages.
- [ ] README opens with the chart and a 60-second recording.

## Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12, managed with `uv` |
| Frames | Polars |
| Store | DuckDB + Parquet |
| Frame validation | Pandera (Polars backend) |
| Boundary types | Pydantic v2 |
| API | FastAPI |
| MCP | Official Python MCP SDK (FastMCP), streamable HTTP + stdio |
| TS SDK | Zod, generated from Pydantic JSON Schema (`json-schema-to-zod`) |
| Quality | pytest, hypothesis, ruff, mypy `--strict` |
| CI | GitHub Actions |
| Chart | matplotlib |

## Securities & events

| Ticker | Event | Date | Why it's in the demo |
|---|---|---|---|
| NVDA | 10:1 split | ex-date 2024-06-10 | The headline case; fiscal year ends late January |
| AAPL | 4:1 split | ex-date 2020-08-31 | Second split, different fiscal calendar (Sept FYE) |
| TSLA | 3:1 split | ex-date 2022-08-25 | Third split, calendar-year FYE |
| GE | Spinoff (GE HealthCare) | 2023-01-04 | Non-split corporate action |
| SIVB | Bankruptcy / delisting | March 2023 | Survivorship-bias case; must stay in history |

Verify every date against a primary source while building the data layer, and record the source URL in `data/raw/corporate_actions.csv`.

## Repository layout

```
zacks-pit-demo/
├── pyproject.toml
├── README.md
├── PLAN.md
├── data/
│   ├── raw/                  # corporate_actions.csv, edgar/*.json, prices/*.parquet
│   └── synthetic/            # estimates.parquet (seeded, reproducible)
├── src/pitdemo/
│   ├── ids.py                # permanent security IDs + PIT ticker mapping
│   ├── schemas.py            # Pydantic models + Pandera frame schemas (single source of truth)
│   ├── ingest/
│   │   ├── edgar.py          # SEC companyfacts → as-reported actuals with filed dates
│   │   ├── prices.py         # prices + split events
│   │   └── synth.py          # synthetic estimate generator
│   ├── store.py              # DuckDB bitemporal tables
│   ├── transforms/
│   │   ├── factors.py        # cumulative adjustment factors, known-as-of aware
│   │   ├── basis.py          # per-estimate share-basis (flagged or inferred)
│   │   ├── consensus.py      # consensus(security, period, as_of, basis)
│   │   └── cache.py          # content-addressed cache
│   ├── api/
│   │   ├── http.py           # FastAPI
│   │   └── mcp_server.py     # MCP tools
│   └── cli.py                # `demo` entry point: ingest, build, chart, serve
├── sdk/
│   ├── python/               # generated Pydantic client
│   └── ts/                   # generated Zod client
├── tests/
│   ├── golden/               # one file per edge case
│   ├── property/             # hypothesis tests
│   └── test_sdk_parity.py
├── docs/
│   ├── methodology.md
│   ├── nvda_consensus.png
│   └── llms.txt
├── .claude/skills/query-pit-consensus/SKILL.md
└── .github/workflows/ci.yml
```

## Data model (bitemporal)

```sql
-- Individual analyst estimates. Never updated in place.
estimates(
  estimate_id     TEXT PRIMARY KEY,
  security_id     TEXT,          -- permanent ID, not ticker
  broker_id       TEXT,
  metric          TEXT,          -- 'EPS'
  fiscal_period   TEXT,          -- fixed label, e.g. 'FY2025'
  value           DOUBLE,
  share_basis_date DATE NULL,    -- share-count basis if flagged by source
  entered_at      TIMESTAMP,     -- knowledge time
  superseded_at   TIMESTAMP NULL
)

-- Corporate actions with knowledge time.
adjustment_factors(
  security_id TEXT, action_type TEXT, ex_date DATE,
  ratio DOUBLE,                  -- e.g. 10.0 for 10:1
  known_at TIMESTAMP, source_url TEXT
)

-- As-reported actuals; restatements are new rows.
actuals(
  security_id TEXT, metric TEXT, fiscal_period TEXT,
  value DOUBLE, basis TEXT,      -- 'GAAP_DILUTED'
  filed_at TIMESTAMP, accession TEXT
)

-- PIT identifier mapping.
security_ids(security_id TEXT, ticker TEXT, cik TEXT, valid_from DATE, valid_to DATE NULL,
             delisted_on DATE NULL, delist_reason TEXT NULL)
```

## Phases

### Phase 0 — Scaffold (1h)
- [x] `uv init`, add deps, `src/` layout, `demo` CLI entry point.
- [x] ruff + mypy strict + pytest configured; CI workflow runs all three.
- [x] `schemas.py` skeleton: `SecurityId`, `FiscalPeriod`, `Basis = Literal["adjusted", "unadjusted"]`, `NewType`s `AdjustedEPS` / `UnadjustedEPS`.

### Phase 1 — Real data ingest (2–3h)
- [x] `edgar.py`: fetch `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json` (set a descriptive `User-Agent`). Extract `EarningsPerShareDiluted` with `fy`, `fp`, `filed`, `accn`. Keep **every** filing's value, not just the latest.
  - Note: filings after a split restate prior-period EPS on the new share basis. Keeping both versions gives a genuine restatement case for free.
- [x] `prices.py`: daily prices + split events for the 5 tickers. Write to Parquet. Cross-check split ratios against `corporate_actions.csv`.
- [x] `ids.py`: permanent IDs (`SEC-0001`…), ticker/CIK mapping with validity ranges, SIVB delisting recorded.
- [x] Pandera schemas validate every ingested frame; ingest fails loudly on violation.

### Phase 2 — Synthetic estimates (2h)
- [x] `synth.py`, fully deterministic (`seed=42`), clearly marked synthetic in data and README.
- [x] 8–12 brokers per security; estimates for FY1/FY2 by fixed fiscal label; revisions as a noisy walk converging toward the EDGAR actual.
- [x] **Split behavior (the important part):** each broker restates to the post-split basis with a random lag of −3 to +10 trading days around the ex-date. Some brokers' rows carry `share_basis_date`; others don't (forces basis inference).
- [x] Include: one broker dropping coverage, one broker going stale (>120 days), one outlier estimate, SIVB estimates stopping at delisting.

### Phase 3 — Store (1h)
- [x] `store.py`: create DuckDB tables above, load Parquet, enforce append-only (no UPDATE paths in code).
- [x] Helper: `active_estimates(security_id, period, as_of)` = `entered_at <= as_of AND (superseded_at IS NULL OR superseded_at > as_of)`.

### Phase 4 — Transforms (3h)
- [x] `factors.py`: `cumulative_factor(security_id, from_date, to_date, known_as_of)` — only factors with `known_at <= known_as_of` apply. Backward adjustment (today's values unchanged).
- [x] `basis.py`: per-estimate basis resolution:
  1. use `share_basis_date` if present;
  2. else infer: if value ≈ broker's prior estimate ÷ ratio (within tolerance) after the ex-date, treat as post-split;
  3. else fall back to `entered_at` vs. ex-date and flag `basis_inferred=True`.
- [x] `consensus.py`: `consensus(security_id, period, as_of, basis) -> ConsensusResult` with mean, median, high, low, stdev, count, excluded list with reasons. Rules: exclude estimates older than 120 days; exclude estimates entered before the latest actual report for that company unless re-confirmed; minimum 3 contributors or return `insufficient=True`.
- [x] `consensus_naive(...)`: averages raw values with no basis handling. Exists only for the chart and tests.
- [x] `cache.py`: key = `sha256(security_id, period, as_of, basis, estimates_version, factors_version, FN_VERSION)`. Versions are per-security monotonic counters bumped on any insert. No invalidation logic; LRU eviction only.

### Phase 5 — Golden tests (2h)
One test file per case in `tests/golden/`, each asserting exact expected values:
- [x] `test_nvda_mixed_basis.py` — around 2024-06-10, correct consensus is continuous; naive drops ~90%.
- [x] `test_split_known_as_of.py` — querying `as_of=2024-05-31` with `known_as_of` before the split announcement applies no factor.
- [x] `test_broker_drop.py` — consensus moves; revision signal (per-broker deltas) does not.
- [x] `test_stale_exclusion.py` — 121-day-old estimate excluded, reason recorded.
- [x] `test_restatement.py` — both EDGAR versions present; PIT query before the restating filing returns the original.
- [x] `test_delisted_sivb.py` — SIVB present in universe queries before delisting, absent after, history intact.
- [x] `test_fy_roll.py` — fixed labels never jump; relative FY1 labels flagged at the roll.
- [x] `test_cache.py` — new split event → new key; same inputs → cache hit.
- [x] `tests/property/`: hypothesis — adjusting then un-adjusting by the same factors is identity; consensus count never exceeds active estimate count.

### Phase 6 — Serving (2h)
- [x] `api/http.py`: `GET /consensus?ticker=&period=&as_of=&basis=` (ticker resolved PIT), `GET /corporate-actions?ticker=`.
- [x] `api/mcp_server.py` tools, each with an LLM-oriented description:
  - `get_consensus(ticker, fiscal_period, as_of, basis="adjusted")`
  - `explain_adjustment(ticker, date)` — factors applied, basis decisions, exclusions
  - `list_corporate_actions(ticker)`
  - Cap result sizes; return typed errors (`NotFound`, `InsufficientEstimates`).
- [x] Support stdio (Claude Desktop/Code) and streamable HTTP.

### Phase 7 — SDKs (1–2h)
- [x] Export JSON Schema from Pydantic models in `schemas.py` → `sdk/schema.json`.
- [x] Python SDK: thin `httpx` client returning Pydantic models; bulk method returns a validated Polars frame.
- [x] TS SDK: generate Zod schemas from `schema.json`; thin `fetch` client that parses every response with Zod.
- [x] `test_sdk_parity.py`: regenerate schema in CI and diff against committed SDKs; fail on drift.

### Phase 8 — Docs & artifacts (2h)
- [x] `docs/methodology.md` (≤2 pages): consensus rules, stale/outlier handling, per-estimate split basis, PIT guarantees, what is synthetic vs. real.
- [x] `demo chart` → `docs/nvda_consensus.png`: naive vs. correct FY2025 consensus, May–July 2024, ex-date marked.
- [x] `docs/llms.txt` + `.claude/skills/query-pit-consensus/SKILL.md`: how an agent should query (always pass `as_of`, state basis).
- [ ] Record a ≤60s screen capture: Claude asking the NVDA question via MCP, then `explain_adjustment`. Link it at the top of the README.
- [ ] README top section: one-sentence pitch, chart, recording, `uv run demo serve` quickstart.

## Commands

```bash
uv run demo ingest        # EDGAR + prices + corporate actions
uv run demo synth         # synthetic estimates (seed 42)
uv run demo build         # load DuckDB
uv run demo chart         # docs/nvda_consensus.png
uv run demo serve         # FastAPI on :8000
uv run demo mcp           # MCP over stdio
uv run pytest
uv run mypy src --strict && uv run ruff check
```

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| EDGAR XBRL gaps or tag inconsistencies | Restrict to `EarningsPerShareDiluted`; hand-check NVDA values; document gaps |
| Scope creep into a "platform" | Hard cap: 5 tickers, 1 metric (EPS), 3 MCP tools |
| Synthetic data looks fake | Seeded noise calibrated to converge on real actuals; label it honestly |
| Reviewer can't run it | README quickstart + committed chart and recording, so it lands without running anything |

## Definition of done

The repo is public, CI is green, the README shows the chart and the recording above the fold, and the DM to Paul links to it with one line: *"I built a small demo of how I'd handle split adjustment and point-in-time consensus, including MCP delivery."*

## Build notes (deviations from the plan)

- **MCP SDK v2** renamed FastMCP to `MCPServer` (`mcp.server.mcpserver`); same decorator API.
- **`report_events` table added**: earnings-release times from SEC 8-K item 2.02 (acceptance timestamps), needed for the re-confirmation rule and FY1/FY2 rolls. Tesla files delivery numbers under 2.02 too, so the release for each quarter is the latest 2.02 8-K before that quarter's 10-Q/10-K.
- **`superseded_at` is derived** in a view, not stored, so estimates are strictly append-only. Coverage drops are `action='stop'` rows; re-confirmations are `action='confirm'` rows.
- **Re-confirmation rule has a 7-day grace period** after each earnings release; without it every consensus is empty for a few days after each report.
- **Spin-offs are not factors.** GE's EPS gets no factor; pre-spin estimates for later periods are excluded after the ex-date. Yahoo encodes spin-offs as non-integer "splits" (GE 1.281, 1.253); the cross-check treats those as price-only.
- **Corporate actions added beyond the five headline events** so EDGAR restatements can be checked: NVDA 4:1 (2021), TSLA 5:1 (2020), GE 1:8 reverse (2021), GE Vernova spin-off (2024).
- **SIVB delisting** takes effect 2023-05-12 (Form 25-NSE); trading was suspended 2023-03-28 and the ticker became SIVBQ.
- **Cache key includes `known_as_of`** and a reports version, as well as estimates/factors versions.
- **Prices are not committed** (third-party data); they are fetched only to cross-check split dates.
- **API timestamps are explicit UTC** (`...Z`) on the wire; internally all timestamps are naive UTC.
