"""Hypothesis: adjustment algebra and consensus invariants."""

from __future__ import annotations

import datetime as dt

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pitdemo.schemas import UnadjustedEPS
from pitdemo.transforms.consensus import as_of_instant, consensus
from pitdemo.transforms.factors import Factor, adjust, cumulative_factor, unadjust
from tests.helpers import NVDA, est, make_store

DAY0 = dt.date(2015, 1, 1)
days = st.integers(min_value=0, max_value=4000).map(lambda n: DAY0 + dt.timedelta(days=n))
ratios = st.sampled_from([2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 1 / 8, 1 / 10, 1.5])


@st.composite
def factor_lists(draw: st.DrawFn) -> list[Factor]:
    n = draw(st.integers(min_value=0, max_value=6))
    out = []
    for _ in range(n):
        ex = draw(days)
        known = dt.datetime.combine(ex - dt.timedelta(days=draw(st.integers(0, 60))), dt.time(12))
        out.append(
            Factor(
                "SEC-0001",
                "NVDA",
                "SPLIT" if (r := draw(ratios)) > 1 else "REVERSE_SPLIT",
                ex,
                r,
                known,
                None,
                "",
                "https://example.com",
                None,
            )
        )
    return out


values = st.floats(min_value=-1e4, max_value=1e4, allow_nan=False).filter(lambda v: abs(v) > 1e-6)


@given(factor_lists(), days, days, values)
def test_adjust_then_unadjust_is_identity(
    fs: list[Factor], a: dt.date, b: dt.date, v: float
) -> None:
    f = cumulative_factor(fs, a, b)
    back = unadjust(adjust(UnadjustedEPS(v), f), f)
    assert abs(back - v) <= 1e-9 * max(1.0, abs(v))


@given(factor_lists(), days, days)
def test_round_trip_factor_is_one(fs: list[Factor], a: dt.date, b: dt.date) -> None:
    assert abs(cumulative_factor(fs, a, b) * cumulative_factor(fs, b, a) - 1.0) < 1e-9


@given(factor_lists(), days, days, days)
def test_factors_compose(fs: list[Factor], a: dt.date, b: dt.date, c: dt.date) -> None:
    lhs = cumulative_factor(fs, a, c)
    rhs = cumulative_factor(fs, a, b) * cumulative_factor(fs, b, c)
    assert abs(lhs / rhs - 1.0) < 1e-9


@given(
    factor_lists(),
    days,
    days,
    st.datetimes(min_value=dt.datetime(2014, 1, 1), max_value=dt.datetime(2027, 1, 1)),
)
def test_unknown_factors_never_apply(
    fs: list[Factor], a: dt.date, b: dt.date, known_as_of: dt.datetime
) -> None:
    visible = [f for f in fs if f.known_at <= known_as_of]
    assert cumulative_factor(fs, a, b, known_as_of) == cumulative_factor(visible, a, b)


estimate_rows = st.lists(
    st.tuples(
        st.sampled_from(["A", "B", "C", "D", "E", "F"]),
        st.one_of(st.none(), st.floats(min_value=0.5, max_value=50.0)),
        st.integers(min_value=0, max_value=200),
    ),
    min_size=0,
    max_size=25,
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(estimate_rows, st.integers(min_value=0, max_value=220))
def test_count_never_exceeds_active(rows: list[tuple[str, float | None, int]], q: int) -> None:
    start = dt.datetime(2024, 1, 1, 15)
    data = [
        est(b, None if v is None else round(v, 2), start + dt.timedelta(days=d, minutes=i))
        for i, (b, v, d) in enumerate(rows)
    ]
    store = make_store(data)
    as_of = start.date() + dt.timedelta(days=q)
    r = consensus(store, NVDA, "FY2025", as_of)
    active = store.active_estimates(NVDA, "FY2025", as_of_instant(as_of))
    assert r.count <= active.height
    assert r.count + r.excluded_total == active.height
    assert len({c.broker_id for c in r.contributors}) == r.count  # one per broker
    if not r.insufficient:
        assert r.low <= r.median <= r.high  # type: ignore[operator]
        assert r.low <= r.mean <= r.high  # type: ignore[operator]
