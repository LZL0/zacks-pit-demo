"""Synthetic analyst estimates. SYNTHETIC: these are not real broker estimates.

Real analyst estimates are licensed data, so this module generates a stand-in that
behaves like the real thing where it matters for the demo:

* Deterministic (``seed=42``). Every row carries ``source='SYNTHETIC'``.
* 8 to 12 brokers per security. Estimates for FY1/FY2 under fixed labels, revised
  after each real earnings release (from EDGAR) and occasionally in between. The
  revisions are a noisy walk that converges on the **real** EDGAR actual.
* **Split behavior.** Each broker restates to the post-split basis on its own
  schedule, from 3 trading days before to 10 trading days after the ex-date. Some
  laggards publish another pre-split revision after the ex-date first. About half
  of the brokers flag ``share_basis_date``; the rest do not, so the pipeline has to
  infer the basis.
* Special cases: one NVDA broker drops coverage, one AAPL broker goes stale, one
  TSLA estimate is an outlier, GE estimates re-base after the GE HealthCare
  spin-off, and SIVB coverage stops when the bank fails.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from pitdemo.calendar import is_trading_day, shift_trading_days, trading_days
from pitdemo.ids import SecurityMaster, master
from pitdemo.periods import annual_report_time, parse_fixed
from pitdemo.schemas import EstimatesFrame
from pitdemo.transforms.factors import LATEST, Factor, cumulative_factor

SEED = 42
CONFIRM_BAND = 0.03  # re-confirm instead of revising when the change would be under 3%
BROKERS = tuple(f"BRK-{i:02d}" for i in range(1, 15))
D = dt.date


@dataclass(frozen=True)
class Shock:
    """A news day on which most brokers cut (or raise) estimates."""

    day: dt.date
    probability: float
    low: float
    high: float


@dataclass(frozen=True)
class SynthSpec:
    security_id: str
    start: dt.date
    end: dt.date
    periods: tuple[str, ...]
    start_ratio: float  # first consensus as a fraction of the eventual actual
    target_overrides: Mapping[str, float] = field(default_factory=dict)
    perimeter_uplift: float = 1.0  # pre-spin-off estimates include the spun business
    drop_coverage_on: dt.date | None = None
    stale_after: dt.date | None = None
    outlier_on: dt.date | None = None
    stop_all_on: dt.date | None = None
    shock: Shock | None = None


SPECS: tuple[SynthSpec, ...] = (
    SynthSpec(
        "SEC-0001",
        D(2023, 9, 1),
        D(2025, 3, 31),
        ("FY2024", "FY2025", "FY2026"),
        start_ratio=0.55,
        drop_coverage_on=D(2024, 8, 14),
    ),
    SynthSpec(
        "SEC-0002",
        D(2019, 10, 1),
        D(2021, 1, 31),
        ("FY2020", "FY2021"),
        start_ratio=0.92,
        stale_after=D(2020, 2, 15),
    ),
    SynthSpec(
        "SEC-0003",
        D(2021, 11, 1),
        D(2023, 2, 28),
        ("FY2022", "FY2023"),
        start_ratio=0.80,
        outlier_on=D(2022, 10, 24),
    ),
    SynthSpec(
        "SEC-0004",
        D(2022, 6, 1),
        D(2023, 6, 30),
        ("FY2023", "FY2024"),
        start_ratio=0.90,
        perimeter_uplift=1.35,
    ),
    SynthSpec(
        "SEC-0005",
        D(2022, 3, 1),
        D(2023, 3, 31),
        ("FY2022", "FY2023", "FY2024"),
        start_ratio=1.20,
        # SVB never reported FY2023 or FY2024; these targets are invented.
        target_overrides={"FY2023": 22.00, "FY2024": 24.00},
        stop_all_on=D(2023, 3, 13),
        shock=Shock(D(2023, 3, 9), 0.7, 0.55, 0.80),
    ),
)


@dataclass
class _BrokerProfile:
    broker_id: str
    flags_basis: bool
    bias: float
    split_lags: dict[dt.date, int]
    revisit_after_report: float
    intra_quarter_rate: float
    laggard_extra_revision: bool


@dataclass(frozen=True)
class _Entry:
    when: dt.datetime
    kind: str  # 'init' | 'report' | 'intra' | 'restate' | 'laggard' | 'rebase' | 'shock' ...


def target_on_latest_basis(
    actuals: pl.DataFrame, factors: Sequence[Factor], period: str
) -> float | None:
    """The first-filed actual for ``period``, restated to the latest share basis.

    Using the as-reported value (not a later restatement) and our own factors keeps
    full precision: NVDA FY2024 is 11.93 / 10 = 1.193, not the rounded 1.19.
    """
    rows = actuals.filter(pl.col("fiscal_period") == period).sort("filed_at")
    if rows.height == 0:
        return None
    first = rows.row(0, named=True)
    return float(first["value"]) / cumulative_factor(factors, first["filed_at"].date(), LATEST)


def _at(rng: random.Random, day: dt.date, not_before: dt.datetime | None = None) -> dt.datetime:
    t = dt.datetime.combine(
        day, dt.time(rng.randint(12, 20), rng.randint(0, 59), rng.randint(0, 59))
    )
    if not_before is not None and t <= not_before:
        t = not_before + dt.timedelta(minutes=rng.randint(30, 240))
    return t


def _latent_ratio(
    t: dt.datetime,
    start: dt.datetime,
    final: dt.datetime,
    r0: float,
    reports: Sequence[dt.datetime],
) -> float:
    """Consensus as a fraction of the actual; steps up at earnings releases."""
    past = [r for r in reports if start < r <= t]
    anchor = past[-1] if past else start
    s = min(1.0, max(0.0, (anchor - start) / (final - start)))
    return 1.0 - (1.0 - r0) * (1.0 - s) ** 2


def _truth_basis_date(day: dt.date, profile: _BrokerProfile, splits: Sequence[Factor]) -> dt.date:
    for f in splits:
        restate = shift_trading_days(f.ex_date, profile.split_lags[f.ex_date])
        if restate <= day < f.ex_date:
            return f.ex_date  # early restater: already on the post-split basis
        if f.ex_date <= day < restate:
            return f.ex_date - dt.timedelta(days=1)  # laggard: still on the old basis
    return day


def _profiles(rng: random.Random, n: int, splits: Sequence[Factor]) -> list[_BrokerProfile]:
    chosen = sorted(rng.sample(BROKERS, n))
    return [
        _BrokerProfile(
            broker_id=b,
            flags_basis=rng.random() < 0.5,
            bias=rng.gauss(0.0, 0.05),
            split_lags={f.ex_date: rng.randint(-3, 10) for f in splits},
            revisit_after_report=0.93,
            intra_quarter_rate=1 / 45,
            laggard_extra_revision=rng.random() < 0.6,
        )
        for b in chosen
    ]


def _coverage(
    spec: SynthSpec, sec: SecurityMaster, period: str, reports: Sequence[dt.datetime]
) -> tuple[dt.datetime, dt.datetime, dt.datetime]:
    """(coverage start, coverage end, final report time) for one period.

    A period is covered from when it becomes FY2 (the release reporting the year two
    before it) until its own annual results come out.
    """
    fy = parse_fixed(period)
    becomes_fy2 = annual_report_time(sec.fye, fy - 2, reports)
    start = dt.datetime.combine(spec.start, dt.time(12))
    if becomes_fy2 is not None and becomes_fy2 > start:
        start = becomes_fy2
    final = annual_report_time(sec.fye, fy, reports)
    end = dt.datetime.combine(spec.end, dt.time(23, 59))
    if final is not None and final < end:
        end = final
    if final is None:
        final = dt.datetime.combine(sec.fye.end_of(fy) + dt.timedelta(days=30), dt.time(12))
    return start, end, final


def _schedule(
    rng: random.Random,
    spec: SynthSpec,
    stale_after: dt.date | None,
    profile: _BrokerProfile,
    start: dt.datetime,
    end: dt.datetime,
    reports: Sequence[dt.datetime],
    splits: Sequence[Factor],
    spinoffs: Sequence[Factor],
) -> list[_Entry]:
    entries = [_Entry(_at(rng, shift_trading_days(start.date(), rng.randint(1, 4)), start), "init")]
    for r in reports:
        if start < r < end and rng.random() < profile.revisit_after_report:
            day = shift_trading_days(r.date(), rng.randint(0, 2))
            entries.append(_Entry(_at(rng, day, r), "report"))
    for day in trading_days(start.date(), end.date()):
        if rng.random() < profile.intra_quarter_rate:
            entries.append(_Entry(_at(rng, day), "intra"))
    for f in splits:
        restate = shift_trading_days(f.ex_date, profile.split_lags[f.ex_date])
        if start.date() < restate <= end.date():
            entries.append(_Entry(_at(rng, restate, f.known_at), "restate"))
            lag = profile.split_lags[f.ex_date]
            if lag > 1 and profile.laggard_extra_revision:
                day = shift_trading_days(f.ex_date, rng.randint(0, lag - 1))
                entries.append(_Entry(_at(rng, day), "laggard"))
    for f in spinoffs:
        if start.date() < f.ex_date <= end.date():
            day = shift_trading_days(f.ex_date, rng.randint(0, 5))
            entries.append(_Entry(_at(rng, day), "rebase"))
    shock = spec.shock
    if (
        shock is not None
        and start.date() < shock.day <= end.date()
        and rng.random() < shock.probability
    ):
        entries.append(_Entry(_at(rng, shock.day), "shock"))
    entries = [e for e in entries if start < e.when < end and is_trading_day(e.when.date())]
    # Around a spin-off, a broker publishes nothing between the ex-date and its re-base.
    for f in spinoffs:
        rebase = [e.when for e in entries if e.kind == "rebase"]
        if rebase:
            entries = [
                e
                for e in entries
                if not (f.ex_date <= e.when.date() and e.when < rebase[0] and e.kind != "rebase")
            ]
    if stale_after is not None:
        entries = [e for e in entries if e.when.date() <= stale_after or e.kind == "init"]
    entries.sort(key=lambda e: e.when)
    dedup: list[_Entry] = []
    for e in entries:
        if dedup and e.when.date() == dedup[-1].when.date():
            continue  # one row per broker per day is plenty
        dedup.append(e)
    return dedup


def generate_security(
    spec: SynthSpec,
    actuals: pl.DataFrame,
    reports: Sequence[dt.datetime],
    factors: Sequence[Factor],
    seed: int = SEED,
) -> list[dict[str, Any]]:
    sec = master(spec.security_id)
    rng = random.Random(f"{seed}:{spec.security_id}")
    splits = [f for f in factors if f.is_share_count and spec.start <= f.ex_date <= spec.end]
    spinoffs = [
        f for f in factors if f.action_type == "SPINOFF" and spec.start <= f.ex_date <= spec.end
    ]
    profiles = _profiles(rng, rng.randint(8, 12), splits)
    stale_broker = profiles[rng.randrange(len(profiles))].broker_id if spec.stale_after else None
    drop_broker = (
        profiles[rng.randrange(len(profiles))].broker_id if spec.drop_coverage_on else None
    )
    outlier_broker = profiles[rng.randrange(len(profiles))].broker_id if spec.outlier_on else None

    rows: list[dict[str, Any]] = []
    for period in spec.periods:
        target = spec.target_overrides.get(period) or target_on_latest_basis(
            actuals, factors, period
        )
        if target is None:
            raise ValueError(f"no actual and no override for {spec.security_id} {period}")
        start, end, final = _coverage(spec, sec, period, reports)
        period_end = sec.fye.end_of(parse_fixed(period))
        for p in profiles:
            stale_after = spec.stale_after if p.broker_id == stale_broker else None
            entries = _schedule(rng, spec, stale_after, p, start, end, reports, splits, spinoffs)
            last_value: float | None = None
            last_basis: dt.date | None = None
            shock_mult = 1.0
            outlier_pending = spec.outlier_on is not None and p.broker_id == outlier_broker
            for e in entries:
                if (
                    spec.drop_coverage_on is not None
                    and p.broker_id == drop_broker
                    and e.when.date() >= spec.drop_coverage_on
                ):
                    break
                if spec.stop_all_on is not None and e.when.date() >= spec.stop_all_on:
                    break
                day = e.when.date()
                basis_date = _truth_basis_date(day, p, splits)
                s = min(1.0, max(0.0, (e.when - start) / (final - start)))
                latent = target * _latent_ratio(e.when, start, final, spec.start_ratio, reports)
                if e.kind == "shock" and spec.shock is not None:
                    shock_mult *= rng.uniform(spec.shock.low, spec.shock.high)
                noise = rng.gauss(0.0, 0.02 * (1 - s) + 0.004)
                value_latest = latent * shock_mult * math.exp(p.bias * (1 - s) + noise)
                pre_spin = any(day < f.ex_date and period_end > f.ex_date for f in spinoffs)
                if pre_spin:
                    value_latest *= spec.perimeter_uplift
                if e.kind in {"restate"} and last_value is not None and last_basis is not None:
                    # A pure restatement: same view, new share count.
                    value = last_value / cumulative_factor(splits, last_basis, basis_date)
                else:
                    value = value_latest * cumulative_factor(splits, basis_date, LATEST)
                is_outlier = (
                    outlier_pending
                    and spec.outlier_on is not None
                    and day >= spec.outlier_on
                    and e.kind != "restate"
                )
                if is_outlier:
                    value *= 1.6  # a fat-finger estimate; the broker's next one is normal
                    outlier_pending = False
                value = round(value, 2)
                action = "estimate"
                if (
                    e.kind == "report"
                    and not is_outlier
                    and last_value is not None
                    and last_basis is not None
                    and cumulative_factor(splits, last_basis, basis_date) == 1.0
                    and abs(value / last_value - 1) < CONFIRM_BAND
                ):
                    # The fresh view is within a few percent of the published number,
                    # so the broker re-confirms it rather than publishing a new one.
                    value, action = last_value, "confirm"
                rows.append(
                    {
                        "security_id": spec.security_id,
                        "broker_id": p.broker_id,
                        "metric": "EPS",
                        "fiscal_period": period,
                        "action": action,
                        "value": value,
                        "share_basis_date": basis_date if p.flags_basis else None,
                        "entered_at": e.when,
                        "source": "SYNTHETIC",
                    }
                )
                last_value, last_basis = value, basis_date
            stop_day: dt.date | None = None
            if spec.drop_coverage_on is not None and p.broker_id == drop_broker:
                stop_day = spec.drop_coverage_on
            if spec.stop_all_on is not None:
                stop_day = spec.stop_all_on
            if (
                stop_day is not None
                and last_value is not None
                and start.date() < stop_day <= end.date()
            ):
                rows.append(
                    {
                        "security_id": spec.security_id,
                        "broker_id": p.broker_id,
                        "metric": "EPS",
                        "fiscal_period": period,
                        "action": "stop",
                        "value": None,
                        "share_basis_date": None,
                        "entered_at": _at(rng, stop_day),
                        "source": "SYNTHETIC",
                    }
                )
    return rows


def generate(
    actuals: Mapping[str, pl.DataFrame],
    reports: Mapping[str, Sequence[dt.datetime]],
    factors: Mapping[str, Sequence[Factor]],
    specs: Sequence[SynthSpec] = SPECS,
    seed: int = SEED,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for spec in specs:
        rows.extend(
            generate_security(
                spec,
                actuals[spec.security_id],
                reports[spec.security_id],
                factors[spec.security_id],
                seed,
            )
        )
    df = pl.DataFrame(
        rows, schema={k: v for k, v in EstimatesFrame.polars_schema().items() if k != "estimate_id"}
    )
    df = df.sort("security_id", "entered_at", "broker_id", "fiscal_period")
    df = df.with_columns(
        (
            pl.lit("EST-")
            + pl.col("security_id").str.slice(4)
            + pl.lit("-")
            + pl.int_range(1, pl.len() + 1).over("security_id").cast(pl.String).str.zfill(6)
        ).alias("estimate_id")
    ).select(list(EstimatesFrame.polars_schema()))
    return EstimatesFrame.validate(df)
