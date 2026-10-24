# Methodology

How this demo builds a point-in-time EPS consensus, and how it handles corporate actions.
Every rule below is implemented in `src/pitdemo/transforms/` and pinned by a test in `tests/golden/`.

## What is real and what is synthetic

| Data | Source | Real? |
|---|---|---|
| Diluted EPS actuals, every filing including restatements | SEC EDGAR `companyfacts` (`us-gaap:EarningsPerShareDiluted`) | Real |
| Earnings-release times | SEC EDGAR filing index: 8-K item 2.02, acceptance timestamp | Real |
| Splits, spin-offs, delisting | `data/raw/corporate_actions.csv`, every row citing an SEC filing or FDIC release | Real |
| Analyst estimates (1,309 rows, 14 brokers) | `src/pitdemo/ingest/synth.py`, seed 42 | **Synthetic** |

Analyst estimates are licensed data, so the demo generates them. They are calibrated to converge on the real EDGAR actuals, revised after the real earnings releases, and every row carries `source = 'SYNTHETIC'`.

## Storage: bitemporal and append-only

* **Two clocks.** Estimates carry `entered_at` (when we learned of them). Corporate actions carry `known_at` (announcement, taken from the SEC acceptance timestamp) and `ex_date` (when they took effect). Actuals carry `filed_at` (acceptance timestamp of the filing). When only a date is known, the time is set to 23:59:59 that day, so a fact can never leak into an earlier query.
* **Nothing is updated.** A revision is a new row. `superseded_at` is derived in a view (the broker's next `entered_at`), not stored. Restated actuals are new rows: NVIDIA's FY2024 EPS is in the store as both $11.93 (10-K, Feb 2024) and $1.19 (10-K, Feb 2025), and a query sees whichever was filed by its cutoff.
* **Permanent IDs.** Everything keys on `security_id`. Tickers are resolved as of the query date (`SIVB` until 2023-03-27, `SIVBQ` after), and a delisted security stays in history.

## Consensus construction

`consensus(security, fiscal_period, as_of, basis, known_as_of=None)`:

1. **Active set.** Each broker's latest estimate entered at or before `as_of`, unless that broker has since withdrawn coverage.
2. **Share basis, per estimate** (see below), then restated to the target basis:
   * `adjusted`: the latest known share basis. Values are comparable across splits, so the series is continuous.
   * `unadjusted`: the share basis in effect at `as_of`, which is what a screen showed that day.
3. **Exclusions**, each returned with a reason:
   * *stale*: entered more than 120 days before `as_of`;
   * *not re-confirmed*: entered before the company's latest earnings release and not revised or re-confirmed within 7 days of it;
   * *pre-spin-off perimeter*: see below;
   * *outlier*: with at least 5 estimates, more than 5 robust standard deviations (MAD-based) from the median *and* more than 25% away from it. This runs **after** basis conversion, so a 10× basis error is caught rather than averaged in.
4. **At least 3 contributors**, otherwise `insufficient = true` and no statistics.
5. **Output**: mean, median, high, low, sample standard deviation and count, plus every contributor with its basis decision, every exclusion with its reason, the corporate actions applied (with source URLs), warnings, and the data versions used.

Fiscal periods are fixed labels (`FY2025`). `FY1` and `FY2` are accepted but resolved to a fixed label at query time. FY1 rolls at the earnings release that reports the prior year. Answers near a roll carry a warning, and series endpoints only accept fixed labels.

## Corporate actions

**Splits are factors, applied with knowledge time.** `cumulative_factor(from, to, known_as_of)` multiplies the ratios of splits with `from < ex_date ≤ to` that were announced by `known_as_of`. Adjustment is backward: today's values never change, and history is restated to today's share count. With `known_as_of` before NVIDIA's 8-K of 2024-05-23 00:21 UTC, no factor applies.

**The basis is decided per estimate, not per date.** Around NVIDIA's 10-for-1 split (ex-date 2024-06-10), brokers restated on their own schedules: some published post-split numbers days *before* the ex-date, others kept publishing pre-split numbers for a week or two *after* it. So each estimate gets its own decision:

1. **flagged**: the source supplied a share-basis date. Use it.
2. **ratio-inferred**: entered while a split was pending or recent (from announcement to 30 days after the ex-date). Compare the estimate with the same broker's previous one on both candidate bases, and pick the basis it is within 25% of.
3. **date fallback**: no usable prior. Assume the basis from entry date vs. ex-date and flag it `basis_inferred = true`.

The chart shows what this buys on the synthetic NVDA data:

| as of | this pipeline | naive average | adjust by entry date |
|---|---|---|---|
| 2024-05-31 | $2.548 | $25.21 | $2.52 |
| 2024-06-14 | $2.551 | $12.87 | $9.76 |
| 2024-07-31 | $2.591 | $2.59 | $2.59 |

The naive average shows a fake 90% collapse. The entry-date shortcut looks fine at both ends, but in between it divides early restaters twice and leaves laggards undivided.

**Spin-offs are not factors.** GE's distribution of GE HealthCare (ex-date 2023-01-04) changed what GE *is*, not how many shares it has. Price vendors encode it as a price factor (Yahoo shows a "1.281 split"), which is right for prices and wrong for EPS. Here the EPS factor is 1.0. Estimates for fiscal years ending after the spin-off that were entered before it describe a company that no longer exists, so they are excluded once it takes effect, and a warning is returned before then. EDGAR shows the same break: GE recast FY2022 EPS from −$0.05 to +$0.05 after the spin-off.

**Delistings end coverage, not history.** SVB Financial left the universe when its Nasdaq delisting took effect (2023-05-12). Its estimates and actuals stay queryable by `security_id`, and by ticker for dates when the ticker was live.

## Point-in-time guarantees

* No estimate, actual or earnings release with a knowledge time after `as_of` affects the answer.
* No corporate action announced after `known_as_of` is applied.
* Basis inference uses only the broker's own earlier estimates.
* Results are cached under a hash of every input, including per-security data versions. Tables are append-only, so a version only grows: a new split or estimate means a new key, and no invalidation is needed.

## How the inputs are checked

* **Against the companies' own restatements.** For all 12 pairs of EDGAR filings with a split between them (NVDA ×4, TSLA ×4, AAPL ×2, GE ×2), the later EPS equals the earlier one divided by our factor, to the cent (1% allowed for GE's 2021 recast).
* **Against a price source.** Ingest compares every split with Yahoo's split events and fails on a mismatch. Non-integer vendor "splits" are matched to recorded spin-offs instead.
* **Fiscal calendars** reproduce every annual period end SEC has on file for the five companies.
* **Frames** are validated with Pandera on the way into the store, and the boundary types are Pydantic. The Python and TypeScript SDKs are generated from one JSON Schema, and CI fails if they drift.

## Known limits

Synthetic estimates; one metric (GAAP diluted EPS, annual); five securities; ex-dates taken from the issuers' announcements (GE's 2023 ex-date is corroborated by the price source, not an exchange notice); SIVBQ's OTC start date comes from secondary press.
