"""Daily prices and split events, used to cross-check ``corporate_actions.csv``.

Prices come from Yahoo Finance's public chart endpoint. They are written to
``data/raw/prices/`` but **not committed**: they are a third party's data, and the
consensus pipeline does not need them. What the pipeline does need, the split
ratios and dates, is cross-checked here against the primary-source CSV, and the
ingest fails if the two disagree.

SVB Financial (SIVB/SIVBQ) is no longer served by Yahoo; that gap is reported, not
papered over.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pitdemo.ingest.upstream import UpstreamFormatError, YahooChartEnvelope, parse_upstream
from pitdemo.schemas import PricesFrame

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
NEW_YORK_OFFSET = dt.timedelta(hours=-4)  # good enough to turn 13:30 UTC opens into dates


@dataclass(frozen=True)
class SplitEvent:
    ticker: str
    ex_date: dt.date
    ratio: float  # new shares per old share


@dataclass(frozen=True)
class PriceFetch:
    ticker: str
    prices: pl.DataFrame | None
    splits: tuple[SplitEvent, ...]
    error: str | None


def fetch_chart(
    client: httpx.Client, ticker: str, security_id: str, start: dt.date, end: dt.date
) -> PriceFetch:
    p1 = int(dt.datetime.combine(start, dt.time()).replace(tzinfo=dt.UTC).timestamp())
    p2 = int(dt.datetime.combine(end, dt.time()).replace(tzinfo=dt.UTC).timestamp())
    resp = client.get(
        CHART_URL.format(ticker=ticker),
        params={"period1": p1, "period2": p2, "interval": "1d", "events": "split"},
    )
    what = f"Yahoo chart for {ticker}"
    try:
        body: Any = resp.json()
    except ValueError as exc:
        if resp.status_code != 200:
            return PriceFetch(ticker, None, (), f"HTTP {resp.status_code}")
        raise UpstreamFormatError(f"{what} is not JSON") from exc
    envelope = parse_upstream(YahooChartEnvelope, body, what).chart
    if envelope.error is not None:
        # The source declared an error (e.g. a delisted symbol): report, don't fail.
        return PriceFetch(ticker, None, (), envelope.error.description or "error")
    if resp.status_code != 200:
        return PriceFetch(ticker, None, (), f"HTTP {resp.status_code}")
    if not envelope.result:
        # A 200 with neither a result nor an error is a format change, not missing data:
        # treating it as "no data" would silently skip the split cross-check.
        raise UpstreamFormatError(f"{what} returned neither a result nor an error")
    res = envelope.result[0]

    def to_date(ts: int) -> dt.date:
        return (dt.datetime.fromtimestamp(ts, dt.UTC) + NEW_YORK_OFFSET).date()

    quote = res.indicators.quote[0]
    prices = pl.DataFrame(
        {
            "security_id": [security_id] * len(res.timestamp),
            "date": [to_date(t) for t in res.timestamp],
            "close": quote.close,
            "volume": quote.volume,
        },
        schema=PricesFrame.polars_schema(),
    ).drop_nulls("close")
    splits = tuple(
        SplitEvent(ticker, to_date(s.date), s.numerator / s.denominator)
        for s in res.events.splits.values()
    )
    return PriceFetch(ticker, PricesFrame.validate(prices), splits, None)


def is_share_split(ratio: float) -> bool:
    """True for n-for-1 or 1-for-n ratios; price vendors also emit spin-off price factors
    (GE 2023-01-04 shows up as a "1.281 split") which are not share-count changes."""
    n = ratio if ratio >= 1 else 1 / ratio
    return abs(n - round(n)) < 1e-6


@dataclass(frozen=True)
class CrossCheck:
    errors: list[str]
    notes: list[str]


def cross_check_splits(
    actions: pl.DataFrame, fetched: list[PriceFetch], start: dt.date, end: dt.date
) -> CrossCheck:
    """Compare the primary-source CSV with the price source's split events."""
    errors: list[str] = []
    notes: list[str] = []
    in_range = actions.filter(pl.col("ex_date").is_between(start, end))
    for f in fetched:
        if f.error is not None:
            notes.append(f"{f.ticker}: no price data ({f.error}); split cross-check skipped")
            continue
        mine = in_range.filter(pl.col("ticker") == f.ticker)
        ours = {
            (r["ex_date"], round(r["ratio"], 6))
            for r in mine.filter(pl.col("action_type").is_in(["SPLIT", "REVERSE_SPLIT"])).iter_rows(
                named=True
            )
        }
        spin_dates = set(mine.filter(pl.col("action_type") == "SPINOFF")["ex_date"].to_list())
        vendor = [s for s in f.splits if start <= s.ex_date <= end]
        theirs = {(s.ex_date, round(s.ratio, 6)) for s in vendor if is_share_split(s.ratio)}
        for s in vendor:
            if is_share_split(s.ratio):
                continue
            if s.ex_date in spin_dates:
                notes.append(
                    f"{f.ticker}: price source models the {s.ex_date} spin-off as a "
                    f"{s.ratio:g} price factor; correct for prices, wrong for EPS"
                )
            else:
                errors.append(f"{f.ticker}: price factor {s.ratio:g} on {s.ex_date} has no CSV row")
        for missing in sorted(theirs - ours):
            errors.append(f"{f.ticker}: price source has split {missing}, CSV does not")
        for extra in sorted(ours - theirs):
            errors.append(f"{f.ticker}: CSV has split {extra}, price source does not")
    return CrossCheck(errors, notes)


def fetch_prices(
    out_dir: Path, tickers: dict[str, str], start: dt.date, end: dt.date
) -> list[PriceFetch]:
    """Fetch every ticker (``{ticker: security_id}``) and write Parquet files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[PriceFetch] = []
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (pitdemo)"}, timeout=30.0) as client:
        for ticker, security_id in tickers.items():
            f = fetch_chart(client, ticker, security_id, start, end)
            if f.prices is not None:
                f.prices.write_parquet(out_dir / f"{ticker}.parquet")
            results.append(f)
    return results
