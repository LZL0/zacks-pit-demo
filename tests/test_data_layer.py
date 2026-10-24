"""Ingest, calendar and synthetic data checks."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from pitdemo.calendar import holidays, is_trading_day, shift_trading_days
from pitdemo.ingest.prices import PriceFetch, SplitEvent, cross_check_splits, is_share_split
from pitdemo.ingest.synth import SPECS
from pitdemo.pipeline import ESTIMATES_PATH, RawData, synthesize


def test_synthetic_data_is_reproducible(raw: RawData) -> None:
    a = synthesize(raw, seed=42)
    committed = pl.read_parquet(ESTIMATES_PATH)
    assert a.equals(committed), "data/synthetic/estimates.parquet is stale: run `demo synth`"
    assert not synthesize(raw, seed=7).equals(a)


def test_synthetic_rows_are_labelled(raw: RawData) -> None:
    df = pl.read_parquet(ESTIMATES_PATH)
    assert set(df["source"].unique()) == {"SYNTHETIC"}
    per_sec = df.group_by("security_id").agg(pl.col("broker_id").n_unique()).rows()
    assert all(8 <= n <= 12 for _, n in per_sec)
    assert {s.security_id for s in SPECS} == set(df["security_id"].unique())


def test_some_brokers_flag_basis_and_some_do_not() -> None:
    df = pl.read_parquet(ESTIMATES_PATH).filter(pl.col("security_id") == "SEC-0001")
    flagged = df.group_by("broker_id").agg(pl.col("share_basis_date").is_not_null().any())
    assert 0 < flagged["share_basis_date"].sum() < flagged.height


def test_split_restatement_lags_span_the_ex_date() -> None:
    """Some NVDA brokers move to post-split numbers before the ex-date, some after."""
    df = pl.read_parquet(ESTIMATES_PATH).filter(
        (pl.col("security_id") == "SEC-0001") & (pl.col("fiscal_period") == "FY2025")
    )
    first_post = (
        df.filter(pl.col("value") < 6).group_by("broker_id").agg(pl.col("entered_at").min())
    )
    days = [d.date() for d in first_post["entered_at"]]
    ex = dt.date(2024, 6, 10)
    assert any(d < ex for d in days)
    assert any(d > ex for d in days)
    laggard_rows = df.filter(
        (pl.col("entered_at") >= dt.datetime(2024, 6, 10)) & (pl.col("value") > 6)
    )
    assert laggard_rows.height >= 1  # pre-split numbers published after the ex-date


def test_special_cases_present() -> None:
    df = pl.read_parquet(ESTIMATES_PATH)
    stops = df.filter(pl.col("action") == "stop")
    assert set(stops["security_id"].unique()) == {"SEC-0001", "SEC-0005"}
    # The TSLA outlier: one broker's fat-finger estimate, caught after basis conversion.
    from pitdemo.pipeline import memory_store
    from pitdemo.transforms.consensus import consensus

    store = memory_store()
    flagged = set()
    day = dt.date(2022, 10, 24)
    while day <= dt.date(2023, 1, 20):
        r = consensus(store, "SEC-0003", "FY2022", day)
        flagged |= {x.broker_id for x in r.excluded if x.reason == "outlier"}
        day += dt.timedelta(days=7)
    assert len(flagged) == 1  # exactly the one fat-finger broker


def test_report_events_exclude_tesla_delivery_releases(raw: RawData) -> None:
    tsla = raw.reports["SEC-0003"]
    q3_2022 = tsla.filter(
        pl.col("reported_at").is_between(dt.datetime(2022, 10, 1), dt.datetime(2022, 10, 31))
    )
    # Deliveries 8-K (2022-10-03, also item 2.02) is not an earnings release. The release
    # was after the close on 2022-10-19: SEC header 17:15:46 New York = 21:15:46 UTC.
    assert q3_2022["reported_at"].to_list() == [dt.datetime(2022, 10, 19, 21, 15, 46)]


def test_nyse_calendar() -> None:
    assert dt.date(2024, 3, 29) in holidays(2024)  # Good Friday
    assert dt.date(2024, 6, 19) in holidays(2024)  # Juneteenth
    assert dt.date(2021, 6, 19) not in holidays(2021)  # before it was an NYSE holiday
    assert dt.date(2022, 12, 26) in holidays(2022)  # Christmas observed
    assert dt.date(2021, 12, 31) not in holidays(2021)  # NYSE skips New Year's on Saturday
    assert not is_trading_day(dt.date(2025, 1, 9))  # national day of mourning
    assert shift_trading_days(dt.date(2024, 6, 10), -3) == dt.date(2024, 6, 5)
    assert shift_trading_days(dt.date(2024, 6, 10), 10) == dt.date(2024, 6, 25)  # skips 06-19


def test_price_cross_check_treats_spinoff_factors_as_price_only(raw: RawData) -> None:
    assert is_share_split(10.0)
    assert is_share_split(0.125)
    assert not is_share_split(1.281)
    fetched = [
        PriceFetch(
            "GE",
            None,
            (
                SplitEvent("GE", dt.date(2021, 8, 2), 0.125),
                SplitEvent("GE", dt.date(2023, 1, 4), 1.281),
                SplitEvent("GE", dt.date(2024, 4, 2), 1.253),
            ),
            None,
        ),
        PriceFetch("NVDA", None, (SplitEvent("NVDA", dt.date(2024, 6, 10), 10.0),), None),
        PriceFetch("SIVB", None, (), "No data found, symbol may be delisted"),
    ]
    ok = cross_check_splits(raw.actions, fetched, dt.date(2021, 1, 1), dt.date(2025, 1, 1))
    assert ok.errors == [
        "NVDA: CSV has split (datetime.date(2021, 7, 20), 4.0), price source does not"
    ]
    assert any("1.281 price factor" in n for n in ok.notes)
    assert any("SIVB: no price data" in n for n in ok.notes)


@pytest.mark.parametrize("ticker", ["NVDA", "AAPL", "TSLA", "GE", "SIVB"])
def test_every_corporate_action_cites_a_primary_source(raw: RawData, ticker: str) -> None:
    rows = raw.actions.filter(pl.col("ticker") == ticker)
    assert rows.height >= 1
    for url in rows["source_url"]:
        assert url.startswith(("https://www.sec.gov/", "https://www.fdic.gov/"))


@pytest.mark.parametrize(
    ("json_value", "header_new_york", "utc"),
    [
        # (submissions JSON, SEC filing header ACCEPTANCE-DATETIME, true UTC)
        (
            "2024-05-23T00:21:29.000Z",
            "2024-05-22 16:21:29 EDT",
            dt.datetime(2024, 5, 22, 20, 21, 29),
        ),
        (
            "2024-02-22T02:22:09.000Z",
            "2024-02-21 16:22:09 EST",
            dt.datetime(2024, 2, 21, 21, 22, 9),
        ),
        (
            "2024-03-01T02:54:09.000Z",
            "2024-02-29 16:54:09 EST",
            dt.datetime(2024, 2, 29, 21, 54, 9),
        ),
        (
            "2022-10-20T01:15:46.000Z",
            "2022-10-19 17:15:46 EDT",
            dt.datetime(2022, 10, 19, 21, 15, 46),
        ),
    ],
)
def test_sec_acceptance_times_are_converted_to_true_utc(
    json_value: str, header_new_york: str, utc: dt.datetime
) -> None:
    """SEC's submissions JSON says 'Z' but is shifted by the Eastern offset."""
    from pitdemo.ingest.edgar import parse_acceptance

    assert parse_acceptance(json_value) == utc


def test_corporate_actions_known_on_their_announcement_day(raw: RawData) -> None:
    """In New York time, every announcement lands on the day the notes say it happened."""
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    expected = {
        ("NVDA", dt.date(2024, 6, 10)): dt.date(2024, 5, 22),
        ("NVDA", dt.date(2021, 7, 20)): dt.date(2021, 5, 21),
        ("AAPL", dt.date(2020, 8, 31)): dt.date(2020, 7, 30),
        ("TSLA", dt.date(2020, 8, 31)): dt.date(2020, 8, 11),
        ("TSLA", dt.date(2022, 8, 25)): dt.date(2022, 8, 5),
        ("GE", dt.date(2023, 1, 4)): dt.date(2022, 11, 30),
        ("GE", dt.date(2024, 4, 2)): dt.date(2024, 2, 29),
    }
    for row in raw.actions.iter_rows(named=True):
        key = (row["ticker"], row["ex_date"])
        if key in expected:
            local = row["known_at"].replace(tzinfo=dt.UTC).astimezone(ny).date()
            assert local == expected[key], key
