"""Point-in-time consensus construction.

``consensus(store, security_id, period, as_of, basis)`` answers: *which estimates
were live at ``as_of``, what share basis is each one on, and what do they average
to on the requested basis?* Rules, applied in order:

1. **Active set.** Entered at or before ``as_of`` and not superseded (by the same
   broker's next row) or stopped by then.
2. **Basis.** Each estimate's share basis is resolved individually (``basis.py``)
   and restated to the target basis with cumulative split factors known as of
   ``known_as_of``. ``adjusted`` targets the latest known basis; ``unadjusted``
   targets the basis in effect at ``as_of``.
3. **Exclusions**, each recorded with a reason:

   * ``stale``: entered more than 120 days before ``as_of``.
   * ``not_reconfirmed_after_report``: entered before the company's latest earnings
     release and not revised or re-confirmed within 7 days after it.
   * ``pre_spinoff_perimeter``: for periods ending after a spin-off, estimates
     entered before the ex-date describe a company that no longer exists. No
     share factor can fix that, so they are dropped once the spin-off is effective.
   * ``outlier``: with at least 5 estimates, more than 5 robust standard deviations
     (MAD-based) from the median, and more than 25% away from it.

4. **Minimum 3 contributors**, otherwise ``insufficient=True`` and no statistics.

``consensus_naive`` exists only for the chart and the tests: it averages raw
published values with no basis handling at all.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import polars as pl

from pitdemo.ids import TickerSpan, master, spans_from_frame, ticker_as_of
from pitdemo.periods import FiscalYearEnd, parse_fixed
from pitdemo.schemas import (
    Basis,
    ConsensusPoint,
    ConsensusResult,
    Contributor,
    CorporateAction,
    Exclusion,
    RevisionSignal,
    SecurityRef,
    UnadjustedEPS,
    Versions,
)
from pitdemo.store import Store
from pitdemo.transforms.basis import BasisDecision, resolve_bases, rows_from_frame
from pitdemo.transforms.factors import (
    LATEST,
    Factor,
    adjust,
    cumulative_factor,
    factors_between,
    factors_from_frame,
    known_share_factors,
)

FN_VERSION = "consensus-v1"
STALE_DAYS = 120
REPORT_GRACE = dt.timedelta(days=7)
MIN_CONTRIBUTORS = 3
OUTLIER_MIN_N = 5
OUTLIER_Z = 5.0
OUTLIER_MIN_REL = 0.25
MAD_TO_SD = 1.4826


def as_of_instant(value: dt.date | dt.datetime) -> dt.datetime:
    """A bare date means "at the end of that day" (UTC), so the whole day counts."""
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.combine(value, dt.time.max)


# ----------------------------------------------------------------- inputs


@dataclass(frozen=True)
class SecurityInputs:
    """Everything consensus needs about one security, loaded once from the store."""

    security_id: str
    name: str
    cik: str
    fye: FiscalYearEnd
    spans: tuple[TickerSpan, ...]
    estimates: pl.DataFrame
    factors: tuple[Factor, ...]
    report_times: tuple[dt.datetime, ...]
    versions: Versions

    def ref(self, as_of: dt.date) -> SecurityRef:
        return SecurityRef(
            security_id=self.security_id,
            ticker=ticker_as_of(self.spans, self.security_id, as_of),
            name=self.name,
            cik=self.cik,
        )

    def period_end(self, period: str) -> dt.date:
        return self.fye.end_of(parse_fixed(period))

    def delisted_on(self) -> dt.date | None:
        dates = [s.delisted_on for s in self.spans if s.delisted_on is not None]
        return min(dates) if dates else None


def load_inputs(store: Store, security_id: str) -> SecurityInputs:
    sec = master(security_id)
    spans = tuple(s for s in spans_from_frame(store.security_ids()) if s.security_id == security_id)
    v = store.versions(security_id)
    return SecurityInputs(
        security_id=security_id,
        name=sec.name,
        cik=f"{sec.cik:010d}",
        fye=sec.fye,
        spans=spans,
        estimates=store.estimates_for(security_id),
        factors=tuple(factors_from_frame(store.corporate_actions(security_id))),
        report_times=tuple(store.report_times(security_id)),
        versions=Versions(
            estimates=v["estimates"], factors=v["factors"], reports=v["reports"], fn=FN_VERSION
        ),
    )


# ------------------------------------------------------------- internals


@dataclass
class _Candidate:
    row: dict[str, object]
    decision: BasisDecision
    factor: float
    value: float
    applied: list[Factor] = field(default_factory=list)

    @property
    def estimate_id(self) -> str:
        return str(self.row["estimate_id"])

    @property
    def entered_at(self) -> dt.datetime:
        v = self.row["entered_at"]
        assert isinstance(v, dt.datetime)
        return v

    @property
    def raw(self) -> float:
        v = self.row["value"]
        assert isinstance(v, float)
        return v


def _history(inputs: SecurityInputs, period: str, as_of: dt.datetime) -> pl.DataFrame:
    e = inputs.estimates
    return e.filter((pl.col("fiscal_period") == period) & (pl.col("entered_at") <= as_of))


def _active(history: pl.DataFrame, as_of: dt.datetime) -> pl.DataFrame:
    return history.filter(
        (pl.col("superseded_at").is_null() | (pl.col("superseded_at") > as_of))
        & (pl.col("action") != "stop")
    ).sort("broker_id", "entered_at")


def _exclusion(c: _Candidate, reason: str, detail: str) -> Exclusion:
    return Exclusion(
        estimate_id=c.estimate_id,
        broker_id=str(c.row["broker_id"]),
        entered_at=c.entered_at,
        raw_value=c.raw,
        reason=reason,
        detail=detail,
    )


def _outliers(cands: list[_Candidate]) -> set[str]:
    if len(cands) < OUTLIER_MIN_N:
        return set()
    values = [c.value for c in cands]
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    robust_sd = MAD_TO_SD * mad
    out: set[str] = set()
    for c in cands:
        dev = abs(c.value - med)
        far_in_sd = robust_sd == 0 or dev > OUTLIER_Z * robust_sd
        far_in_pct = med != 0 and dev > OUTLIER_MIN_REL * abs(med)
        if far_in_sd and far_in_pct:
            out.add(c.estimate_id)
    return out


def _split_label(f: Factor) -> str:
    return f"{f.ratio:g}-for-1" if f.ratio >= 1 else f"1-for-{1 / f.ratio:g}"


def _target_description(
    basis: Basis, as_of: dt.datetime, factors: Sequence[Factor], known_as_of: dt.datetime | None
) -> str:
    known = known_share_factors(factors, known_as_of)
    if basis == "unadjusted":
        past = [f for f in known if f.ex_date <= as_of.date()]
        last = f", after the {past[-1].ex_date} {_split_label(past[-1])} split" if past else ""
        return f"share basis in effect on {as_of.date()}{last}"
    if not known:
        return "latest known share basis (no splits known)"
    last_f = max(known, key=lambda f: f.ex_date)
    cutoff = f" as of {known_as_of}" if known_as_of is not None else ""
    return (
        f"latest known share basis{cutoff}: after the {last_f.ex_date} {_split_label(last_f)} split"
    )


# ------------------------------------------------------------- consensus


def compute_consensus(
    inputs: SecurityInputs,
    period: str,
    as_of: dt.datetime,
    basis: Basis = "adjusted",
    known_as_of: dt.datetime | None = None,
    *,
    requested_period: str | None = None,
    max_items: int | None = None,
    extra_warnings: Iterable[str] = (),
) -> ConsensusResult:
    parse_fixed(period)
    history = _history(inputs, period, as_of)
    active = _active(history, as_of)
    decisions = resolve_bases(rows_from_frame(history), inputs.factors, known_as_of)
    target = as_of.date() if basis == "unadjusted" else LATEST
    known = known_share_factors(inputs.factors, known_as_of)

    cands: list[_Candidate] = []
    for row in active.iter_rows(named=True):
        d = decisions[row["estimate_id"]]
        factor = cumulative_factor(known, d.basis_date, target)
        cands.append(
            _Candidate(
                row,
                d,
                factor,
                adjust(UnadjustedEPS(row["value"]), factor),
                factors_between(known, d.basis_date, target),
            )
        )

    excluded: list[Exclusion] = []
    kept: list[_Candidate] = []
    # The release that sets the re-confirmation cutoff is the latest one whose grace
    # period has already run out. Using simply the latest release would let estimates
    # that lapsed after the *previous* release come back during the new grace window.
    lapsed = [t for t in inputs.report_times if t + REPORT_GRACE <= as_of]
    cutoff_report = lapsed[-1] if lapsed else None
    period_end = inputs.period_end(period)
    spinoffs = [
        f
        for f in inputs.factors
        if f.action_type == "SPINOFF"
        and (known_as_of is None or f.known_at <= known_as_of)
        and f.ex_date <= as_of.date()
        and period_end > f.ex_date
    ]
    for c in cands:
        age = (as_of.date() - c.entered_at.date()).days
        if age > STALE_DAYS:
            excluded.append(
                _exclusion(c, "stale", f"entered {age} days before as_of (> {STALE_DAYS})")
            )
            continue
        if cutoff_report is not None and c.entered_at < cutoff_report:
            excluded.append(
                _exclusion(
                    c,
                    "not_reconfirmed_after_report",
                    f"entered {c.entered_at:%Y-%m-%d}, before the earnings release of "
                    f"{cutoff_report:%Y-%m-%d %H:%M} UTC, and not revised or re-confirmed "
                    f"within {REPORT_GRACE.days} days after it",
                )
            )
            continue
        sbd = c.row["share_basis_date"]
        spin = next(
            (
                s
                for s in spinoffs
                if c.entered_at.date() < s.ex_date
                and not (isinstance(sbd, dt.date) and sbd >= s.ex_date)
            ),
            None,
        )
        if spin is not None:
            excluded.append(
                _exclusion(
                    c,
                    "pre_spinoff_perimeter",
                    f"entered before the {spin.ex_date} spin-off ({spin.terms}); it describes "
                    "the pre-spin company and no share factor converts it",
                )
            )
            continue
        kept.append(c)

    outlier_ids = _outliers(kept)
    if outlier_ids:
        med = statistics.median(c.value for c in kept)
        for c in kept:
            if c.estimate_id in outlier_ids:
                excluded.append(
                    _exclusion(
                        c,
                        "outlier",
                        f"{c.value:.4g} on the target basis vs median {med:.4g} "
                        f"(> {OUTLIER_Z:g} robust SD and > {OUTLIER_MIN_REL:.0%} away)",
                    )
                )
        kept = [c for c in kept if c.estimate_id not in outlier_ids]

    values = [c.value for c in kept]
    n = len(values)
    insufficient = n < MIN_CONTRIBUTORS
    contributors = [
        Contributor(
            estimate_id=c.estimate_id,
            broker_id=str(c.row["broker_id"]),
            entered_at=c.entered_at,
            raw_value=c.raw,
            basis_date=c.decision.basis_date,
            basis_source=c.decision.source,
            basis_inferred=c.decision.inferred,
            factor=c.factor,
            value=c.value,
        )
        for c in kept
    ]
    applied: dict[tuple[dt.date, str], CorporateAction] = {}
    for c in kept:
        for act in c.applied:
            applied[(act.ex_date, act.action_type)] = act.to_model()

    warnings = list(extra_warnings)
    inferred = [c for c in kept if c.decision.inferred]
    if inferred:
        warnings.append(
            f"{len(inferred)} estimate(s) had no flagged basis and no usable prior estimate; "
            "their share basis was guessed from entry date (basis_inferred=true)."
        )
    if known_as_of is not None:
        later = [f for f in inputs.factors if f.is_share_count and f.known_at > known_as_of]
        if later:
            warnings.append(
                "Corporate actions announced after known_as_of are ignored: "
                + ", ".join(f"{_split_label(f)} split ex {f.ex_date}" for f in later)
            )
    if basis == "adjusted":
        pending_spins = [
            f
            for f in inputs.factors
            if f.action_type == "SPINOFF"
            and f.ex_date > as_of.date()
            and period_end > f.ex_date
            and (known_as_of is None or f.known_at <= known_as_of)
        ]
        for s in pending_spins:
            warnings.append(
                f"A spin-off takes effect {s.ex_date} ({s.terms}). These estimates describe the "
                "pre-spin company and are not comparable with post-spin consensus."
            )
    delisted = inputs.delisted_on()
    if delisted is not None and as_of.date() >= delisted:
        warnings.append(f"Security was delisted on {delisted}; no longer covered.")

    target_desc = _target_description(basis, as_of, inputs.factors, known_as_of)
    method = (
        f"Mean of {n} broker estimate(s) live at {as_of.isoformat(sep=' ')} UTC for {period}. "
        f"Each estimate's share basis is resolved individually (flagged by source, inferred "
        f"from the broker's prior estimate and the split ratio, or guessed from entry date), "
        f"then restated to the {target_desc} using split factors known "
        f"{'as of ' + known_as_of.isoformat(sep=' ') if known_as_of else 'today'}. "
        f"Excluded: older than {STALE_DAYS} days; not re-confirmed within {REPORT_GRACE.days} "
        f"days of an earnings release; pre-spin-off perimeter; robust outliers. "
        f"At least {MIN_CONTRIBUTORS} contributors required."
    )
    limit = max_items if max_items is not None else len(contributors) + len(excluded)
    return ConsensusResult(
        security=inputs.ref(as_of.date()),
        fiscal_period=period,
        requested_period=requested_period or period,
        as_of=as_of,
        known_as_of=known_as_of,
        basis=basis,
        target_basis_date=None if basis == "adjusted" else as_of.date(),
        target_basis=target_desc,
        mean=None if insufficient else statistics.fmean(values),
        median=None if insufficient else statistics.median(values),
        high=None if insufficient else max(values),
        low=None if insufficient else min(values),
        stdev=None if insufficient or n < 2 else statistics.stdev(values),
        count=n,
        insufficient=insufficient,
        contributors=contributors[:limit],
        contributors_total=len(contributors),
        excluded=excluded[:limit],
        excluded_total=len(excluded),
        corporate_actions_applied=sorted(applied.values(), key=lambda a: a.ex_date),
        warnings=warnings,
        method=method,
        versions=inputs.versions,
    )


def consensus(
    store: Store,
    security_id: str,
    period: str,
    as_of: dt.date | dt.datetime,
    basis: Basis = "adjusted",
    known_as_of: dt.date | dt.datetime | None = None,
) -> ConsensusResult:
    """Point-in-time consensus straight from the store (see module docstring)."""
    return compute_consensus(
        load_inputs(store, security_id),
        period,
        as_of_instant(as_of),
        basis,
        as_of_instant(known_as_of) if known_as_of is not None else None,
    )


def consensus_series(
    inputs: SecurityInputs,
    period: str,
    days: Sequence[dt.date],
    basis: Basis = "adjusted",
    known_as_of: dt.datetime | None = None,
) -> list[ConsensusPoint]:
    out: list[ConsensusPoint] = []
    for d in days:
        r = compute_consensus(inputs, period, as_of_instant(d), basis, known_as_of, max_items=0)
        out.append(
            ConsensusPoint(
                as_of=d, mean=r.mean, median=r.median, count=r.count, insufficient=r.insufficient
            )
        )
    return out


# ------------------------------------------------------------- baselines


def consensus_naive(inputs: SecurityInputs, period: str, as_of: dt.datetime) -> float | None:
    """Average of raw published values. No basis handling, no exclusions.

    This is what you get by averaging an estimates table without a share-basis
    model. It exists for the chart and the tests only.
    """
    values = _active(_history(inputs, period, as_of), as_of)["value"].to_list()
    return statistics.fmean(values) if values else None


def consensus_entry_date(inputs: SecurityInputs, period: str, as_of: dt.datetime) -> float | None:
    """The common shortcut: restate by *entry date*, assuming everyone switched on the ex-date.

    Gets early restaters (post-split numbers before the ex-date) and laggards
    (pre-split numbers after it) wrong. Also only for the chart and the tests.
    """
    active = _active(_history(inputs, period, as_of), as_of)
    factors = list(inputs.factors)
    values = [
        r["value"] / cumulative_factor(factors, r["entered_at"].date(), LATEST)
        for r in active.iter_rows(named=True)
    ]
    return statistics.fmean(values) if values else None


# ------------------------------------------------------------- revisions


def revision_signal(
    inputs: SecurityInputs,
    period: str,
    as_of: dt.datetime,
    lookback_days: int = 30,
    known_as_of: dt.datetime | None = None,
) -> RevisionSignal:
    """Per-broker revisions: compare each broker with itself ``lookback_days`` earlier.

    Unlike a change in the consensus mean, this does not move when a broker drops
    or adds coverage, because only brokers present at both dates are compared,
    and both values are on the same (adjusted) basis.
    """
    start = as_of - dt.timedelta(days=lookback_days)
    now = compute_consensus(inputs, period, as_of, "adjusted", known_as_of)
    then = compute_consensus(inputs, period, start, "adjusted", known_as_of)
    now_by = {c.broker_id: c.value for c in now.contributors}
    then_by = {c.broker_id: c.value for c in then.contributors}
    common = sorted(set(now_by) & set(then_by))
    changes = [(now_by[b] - then_by[b]) / abs(then_by[b]) for b in common if then_by[b] != 0]
    eps = 1e-9
    return RevisionSignal(
        security=inputs.ref(as_of.date()),
        fiscal_period=period,
        as_of=as_of,
        lookback_days=lookback_days,
        brokers_compared=len(common),
        up=sum(1 for c in changes if c > eps),
        down=sum(1 for c in changes if c < -eps),
        unchanged=sum(1 for c in changes if abs(c) <= eps),
        mean_pct_change=statistics.fmean(changes) if changes else None,
    )
