# EXCEL_PLAN.md — Excel add-in for point-in-time consensus

## Goal

Put the point-in-time consensus where most estimates users already work: a spreadsheet.

1. **Custom functions**: `=PIT.CONSENSUS("NVDA", "FY2025", DATE(2024,5,31))` returns
   the split-adjusted consensus, and related functions spill whole tables (contributors,
   exclusions, a daily series, corporate actions).
2. **Same guarantees as the API**: same request rules, same typed errors, same dated
   version, and no drift. The add-in is built on the TypeScript SDK, which is generated
   from the same Pydantic models as `openapi.json`.
3. **Explain in place**: a task pane that shows *why* a number is what it is (basis
   decisions, split factors with primary sources, exclusions) for the selected cell.

Audience: equity analysts and quants who model in Excel. It shows that the
"one schema, many outputs" design reaches the desk, not just APIs and agents.

## Non-goals

- No write-back, no auth, no user accounts. Read-only, like the API.
- No AppSource (Microsoft marketplace) submission. Sideloading plus a hosted manifest is enough for a demo.
- No hosted production API. The add-in talks to a configurable base URL; the default is
  the local `uv run demo serve` (see the open questions on hosting).
- No VBA or COM add-in, and no Windows-only tooling.
- No new analytics. The add-in only presents what the API already computes.
- **Excel on Mac and Excel on the web are deferred** (see "Deferred"). v1 targets Excel
  desktop on Windows (WebView2) only.

## Approach

**Office Add-in (Office.js) with custom functions, written in TypeScript**, for **Excel
desktop on Windows**; Mac and the web are deferred (see below). It reuses
`sdk/ts` directly: requests are validated by the generated Zod query schemas before
sending, and responses are Zod-parsed.

Alternatives considered:

| Option | Why not (for this) |
|---|---|
| Python in Excel | Runs in Microsoft's cloud, so it can't reach a local API or our package; no custom function names. |
| xlwings / COM add-in | Ties the add-in to Windows desktop for good (Mac and web could never follow) and needs a local Python install per user. |
| Power Query connector | Good for bulk refresh, but no cell-level functions and no explain view. Possible later via the bulk endpoint. |

## Functions (namespace `PIT`)

| Function | Returns | Maps to |
|---|---|---|
| `PIT.CONSENSUS(ticker, period, as_of, [basis], [known_as_of])` | number (mean) | `GET /consensus` |
| `PIT.CONSENSUS.STAT(ticker, period, as_of, stat, [basis])` | number: `mean`, `median`, `high`, `low`, `stdev` or `count` | `GET /consensus` |
| `PIT.CONSENSUS.DETAIL(ticker, period, as_of, [basis])` | spilled table: one row per contributor (broker, published value, basis date, basis source, factor, value) | `GET /consensus` |
| `PIT.EXCLUSIONS(ticker, period, as_of)` | spilled table: broker, reason, detail | `GET /consensus` |
| `PIT.SERIES(ticker, period, start, end, [basis])` | spilled table: date, mean, median, count | `GET /consensus/series` |
| `PIT.ACTIONS(ticker)` | spilled table: type, announced, ex-date, ratio, source URL | `GET /corporate-actions` |
| `PIT.UNIVERSE(date)` | spilled table: security id, ticker | `GET /universe` |
| `PIT.VERSION()` | `2026-10` (API version the add-in was built for) plus the server's, if they differ | `GET /healthz` |

Rules that carry over from the API:

- **Dates.** Excel passes dates as serial numbers. Convert in one tested helper:
  serial → calendar date (1900 date system; account for Excel's fictitious
  1900-02-29, and the 1904 date system that Mac-made workbooks often use, since those are
  opened on Windows too) → ISO `YYYY-MM-DD`. A bare date means end of day,
  UTC, exactly as in the API. ISO strings are also accepted.
- **Periods.** Fixed labels (`FY2025`) are recommended; `FY1`/`FY2` work but return a
  warning in `DETAIL`, since a relative label shifts when the year rolls.
- **Errors.** Map typed API errors to Excel errors with the API's plain-language message
  (shown on hover): `InvalidRequest` → `#VALUE!`, `NotFound` → `#N/A`,
  `insufficient: true` → `#N/A` "fewer than 3 qualifying estimates", unreachable server →
  `#N/A` "API unreachable at <url>". Verify in Phase 0 that error messages show on hover
  in Excel on Windows.
- **Synthetic data.** `DETAIL` includes a `synthetic` column and the task pane labels it.
  Plain number results can't carry a label, so the README and task pane carry it instead.

## Performance: batching and caching

A model with hundreds of `PIT.CONSENSUS` cells would otherwise send hundreds of requests
on every recalculation.

- **Client cache.** Results for a fixed `as_of` never change (append-only, point-in-time
  history), so cache by the full request (query plus API version) for the session.
  Functions are non-volatile; `as_of = TODAY()` naturally changes once a day.
- **Batch endpoint.** Add `POST /consensus/batch` (up to N queries, per-item results and
  errors) to the API: request and response models in `schemas.py`, so it appears in
  `openapi.json`, both SDKs and the drift checks. It is additive, so it stays within
  version `2026-10`.
- **Client batching.** Collect calls made in the same calculation pass (a few-ms window)
  into one batch request, with a concurrency cap for anything left over.

## Explain task pane

- Select a `PIT.*` cell, open the pane, and see the `GET /explain` narrative: split
  factors with announcement times and primary-source links, per-estimate basis
  decisions, exclusions, and the naive-average contrast.
- Settings: API base URL (default `http://127.0.0.1:8000`), stored in add-in storage
  (not the workbook), plus a "test connection" button that shows the server's version.
- Plain HTML and CSS, no framework: the pane is small.

## Repository layout (new)

```
excel/
├── manifest.xml (or manifest.json)  # see Phase 0: which manifest supports custom functions
├── package.json                     # depends on ../sdk/ts (workspace or file: link)
├── src/
│   ├── functions.ts                 # @customfunction JSDoc → generated functions.json
│   ├── dates.ts                     # Excel serial ↔ ISO, tested on its own
│   ├── errors.ts                    # API error → CustomFunctions.Error
│   ├── batch.ts                     # per-calculation batching + cache
│   └── taskpane/{index.html, taskpane.ts}
└── test/                            # unit tests (Node, mocked fetch) + smoke vs local server
```

Built assets are published with the API reference on GitHub Pages under `/excel/`, so the
hosted manifest and the reference come from the same deploy.

## Phases

### Phase 0: spike the risks (half a day)

Answer these before building anything else:
- [ ] **Localhost from an HTTPS add-in.** An add-in served from GitHub Pages (HTTPS)
  calling `http://127.0.0.1:8000`: does it work in Excel on Windows (WebView2)? Covers
  mixed content, CORS and Private Network Access preflights (WebView2 is
  Chromium-based). The API will need `CORSMiddleware` for the add-in's origin, and
  possibly an `Access-Control-Allow-Private-Network` header.
- [ ] **Manifest format.** Do custom functions work with the unified JSON manifest, or
  do they still need the XML add-in manifest? Use whichever Excel on Windows supports
  (and note what Mac would need, for later).
- [ ] **Runtime.** Shared runtime (functions and task pane share state, so the pane can
  read the cache) vs. a separate JavaScript-only runtime. Pick based on host support.
- [ ] **Error display.** `CustomFunctions.Error` codes and on-hover messages on Windows.
- [ ] Hello-world function returning `PIT.VERSION()` from the local server, sideloaded
  in Excel on Windows.

Exit: a one-page note in this file recording each answer, and a go/no-go on localhost
(otherwise see the open question on hosting).

### Phase 1: functions (1 day)

- [ ] `PIT.CONSENSUS`, `.STAT`, `.DETAIL`, `PIT.EXCLUSIONS`, `PIT.SERIES`, `PIT.ACTIONS`,
  `PIT.UNIVERSE` and `PIT.VERSION` on top of `sdk/ts` (Zod-validated requests and responses).
- [ ] `dates.ts` and `errors.ts` with exhaustive unit tests (serials around 1900-02-29,
  the 1904 system, ISO strings, bad input).
- [ ] Session cache keyed on request plus API version.

### Phase 2: batch endpoint and client batching (half a day)

- [ ] `POST /consensus/batch` in the API, with models in `schemas.py`, golden tests, and
  `openapi.json`/SDKs regenerated (drift checks cover it).
- [ ] `batch.ts`: coalesce calls within a calculation pass; cap concurrency.
- [ ] Benchmark: 500 `PIT.CONSENSUS` cells, cold and warm.

### Phase 3: explain task pane (half a day)

- [ ] Selected-cell parsing (formula → function and arguments) and the `/explain` view.
- [ ] Settings: base URL, test connection, version check.

### Phase 4: packaging, CI, docs (half a day)

- [ ] Build into `site/excel/`; `pages.yml` publishes it alongside the API reference.
- [ ] CI job: install, type-check, unit tests, manifest validation, and a smoke run of the
  functions (in Node, against `demo serve`, as `scripts/ci/smoke.py` does for the SDKs).
- [ ] Parity test: every function parameter maps to a request-model field and uses the
  same pattern and description (like the MCP argument check).
- [ ] README section with a short GIF (sideload, type `=PIT.CONSENSUS(...)`, open
  Explain). AGENTS.md: where the add-in lives and how to sideload it.

## Success criteria

- [ ] `=PIT.CONSENSUS("NVDA","FY2025",DATE(2024,5,31))` returns `2.588…` in Excel on
  Windows, matching `GET /consensus` exactly.
- [ ] `=PIT.SERIES("NVDA","FY2025",DATE(2024,5,1),DATE(2024,7,31))` spills the same
  series as the chart; the naive-vs-correct story is reproducible in a sheet.
- [ ] Bad input shows the API's plain-language message (`#VALUE!` "period: 'next' is not
  a fiscal-year label…"); a delisted ticker shows `#N/A` with the point-in-time hint.
- [ ] 500 consensus cells recalculate in under 5 s cold and under 1 s warm (batching
  plus cache) against the local server.
- [ ] CI builds, type-checks, tests and validates the add-in; the parity test guards
  function parameters against the request models.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| HTTPS add-in can't call `http://localhost` in some hosts (mixed content, Private Network Access) | Phase 0 spike first. Fallbacks: serve the API over HTTPS locally (dev certificate from `office-addin-dev-certs`), or host a read-only demo API |
| Too many requests on recalculation | Batch endpoint, per-pass coalescing, session cache (results are immutable for a fixed `as_of`) |
| Excel date systems (1900 leap-year bug; 1904 in Mac-made workbooks) | One conversion helper with exhaustive tests; reject ambiguous input instead of guessing |
| Windows-only assumptions creep in, making Mac or web expensive later | Use only standard Office.js and custom-function APIs; no Windows-only features |
| Function signatures drift from the API | Parity test against the request models; functions are thin wrappers over the generated SDK |
| Synthetic data mistaken for real estimates | `synthetic` column in tables; label in the task pane and README |

## Deferred: Excel on Mac and Excel on the web

Not in v1. Both should be deployment and testing work, not a rewrite: functions,
batching, the task pane and the SDK are host-independent, so keep v1 free of
Windows-only APIs.

**Excel on Mac**
- **Different web view.** Mac add-ins run in WKWebView (Safari's engine), so localhost
  access, mixed-content rules and CORS behave differently from WebView2. Repeat the
  Phase 0 localhost check on Mac before committing to it.
- **Sideloading and manifest.** Mac sideloads differently (the add-in folder Excel
  reads manifests from), and manifest support for custom functions may differ; check both.
- **Testing.** Add Mac to the Phase 0 checks, the success criteria and the manual
  release check. The 1904 date system is already handled in v1.

**Excel on the web**
- **Reaching the API.** In a browser, an HTTPS add-in calling `http://localhost` runs into
  mixed-content blocking and Private Network Access rules that differ between Chrome,
  Edge and Safari, and a web user may not have a local server at all. In practice this
  needs a hosted API over HTTPS (open question 1), with CORS for the add-in origin.
- **Testing.** Add Excel on the web (Chrome, Edge, Safari) to the Phase 0 checks, the
  success criteria and the manual release check.

## Open questions

1. **Hosting the API.** Keep the add-in pointed at a local server, which fits the
   existing "no deployment" non-goal, or host a small read-only demo API so a
   reviewer can try the add-in without running anything? That changes the non-goals and
   needs basic rate limiting. It is also the main prerequisite for Excel on the web.
2. **Namespace.** `PIT.*` is short; `ZPIT.*` or similar avoids clashing with other add-ins.
3. **Scope of v1.** Is `PIT.CONSENSUS` plus `DETAIL`, `SERIES` and the task pane enough
   for the demo, leaving `STAT`, `EXCLUSIONS` and `UNIVERSE` for later?
