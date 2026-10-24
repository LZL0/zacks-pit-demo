---
name: query-pit-consensus
description: Answer questions about point-in-time analyst EPS consensus (NVDA, AAPL, TSLA, GE, SIVB) using the pit-consensus MCP server or the demo's HTTP API. Use when asked what the consensus was on a date, how a stock split or spin-off affected it, or why it changed.
---

# Querying point-in-time consensus

The `pit-consensus` MCP server (run with `uv run demo mcp`) answers "what was the
consensus *as it could have been known* on date X". Estimates in this demo are
synthetic; actuals and corporate actions are real.

## Always

1. **Pass an explicit `as_of`.** Use the date the user names, as `YYYY-MM-DD` (end of day,
   UTC). If none is named, ask, or say which date you assumed.
2. **Choose and state the basis.**
   - `adjusted` (default): every estimate on the latest share basis. Use this to compare
     across a split, or when the user says "split-adjusted".
   - `unadjusted`: the share basis in effect on `as_of`, which is what a screen showed that
     day. Use it for "what did people see then".
3. **Use fixed fiscal labels** (`FY2025`). If the user says "next year" or "FY1",
   resolve it, and report the fixed label from `fiscal_period` in the result.
4. **Report**: value (mean; mention the median if it differs much), `as_of`, basis,
   `count`, and that estimates are synthetic. Surface any `warnings`.

## When to call what

| Question | Tool |
|---|---|
| "What was the consensus on ...?" | `get_consensus` |
| "Why did it change / jump?" or anything near a split or spin-off | `explain_adjustment` |
| "When was the split / spin-off?" | `list_corporate_actions` |

## Reading results

- `insufficient` / `InsufficientEstimates`: fewer than 3 qualifying estimates. Report
  that; don't average the remainder. Check `list_corporate_actions` for a delisting.
- `NotFound` for a ticker on a date: tickers are point-in-time (SIVB became SIVBQ, then
  was delisted). Retry with the `security_id` from the error hint (e.g. `SEC-0005`).
- `basis_inferred=true` on a contributor means its share basis was guessed; say so if
  it matters to the answer.
- `known_as_of` limits which corporate actions are applied. Use it only to reproduce
  what a system knew before an announcement.

## Example

User: "What was NVDA's FY2025 EPS consensus on 2024-05-31, split-adjusted?"

Call `get_consensus(ticker="NVDA", fiscal_period="FY2025", as_of="2024-05-31", basis="adjusted")`, then answer like:

> USD 2.59 (mean of 8 estimates, synthetic data) as of 2024-05-31, on the post-split share
> basis (after the 10-for-1 split, ex-date 2024-06-10, announced 2024-05-22). On that
> day's own share basis it was USD 25.88. One estimate was excluded: it was not
> re-confirmed after the 2024-05-22 earnings release.

Write amounts as "USD 2.49", not with a dollar sign followed by a digit: skill text
substitutes positional arguments into that pattern.
