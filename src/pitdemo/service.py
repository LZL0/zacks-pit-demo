"""Query service shared by the HTTP API and the MCP server.

Both front ends call the same methods, so a human using the REST API and an agent
using MCP get the same numbers, the same exclusions and the same warnings.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pitdemo.calendar import trading_days
from pitdemo.ids import (
    TickerSpan,
    UnknownTickerError,
    by_ticker_today,
    master,
    resolve_ticker,
    spans_from_frame,
    universe,
)
from pitdemo.periods import is_relative, parse_fixed, resolve_period
from pitdemo.schemas import (
    AdjustmentExplanation,
    Basis,
    ConsensusResult,
    ConsensusSeries,
    CorporateActionList,
    ErrorKind,
    ErrorResponse,
    RevisionSignal,
    Versions,
)
from pitdemo.store import Store
from pitdemo.transforms.cache import ConsensusCache, cache_key
from pitdemo.transforms.consensus import (
    FN_VERSION,
    SecurityInputs,
    as_of_instant,
    compute_consensus,
    consensus_naive,
    consensus_series,
    load_inputs,
    revision_signal,
)
from pitdemo.transforms.factors import cumulative_factor

MAX_SERIES_DAYS = 400


class ServiceError(Exception):
    kind: ErrorKind = "InvalidRequest"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(error=self.kind, message=self.message, hint=self.hint)


class NotFoundError(ServiceError):
    kind: ErrorKind = "NotFound"


class InsufficientEstimatesError(ServiceError):
    kind: ErrorKind = "InsufficientEstimates"


class InvalidRequestError(ServiceError):
    kind: ErrorKind = "InvalidRequest"


def parse_day(value: str | dt.date, name: str) -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidRequestError(
            f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}"
        ) from exc


def parse_instant(value: str | dt.date | dt.datetime | None, name: str) -> dt.datetime | None:
    """ISO date -> end of that day (UTC); ISO datetime -> as given (UTC, naive)."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return (
            value.replace(tzinfo=None)
            if value.tzinfo is None
            else (value.astimezone(dt.UTC).replace(tzinfo=None))
        )
    if isinstance(value, dt.date):
        return as_of_instant(value)
    text = value.strip()
    try:
        if "T" in text or " " in text:
            return parse_instant(dt.datetime.fromisoformat(text.replace("Z", "+00:00")), name)
        return as_of_instant(dt.date.fromisoformat(text))
    except ValueError as exc:
        raise InvalidRequestError(f"{name} must be an ISO date or datetime, got {value!r}") from exc


@dataclass
class Service:
    store: Store
    cache: ConsensusCache

    @classmethod
    def create(cls, store: Store, cache_size: int = 4096) -> Service:
        return cls(store, ConsensusCache(cache_size))

    # ----------------------------------------------------------- resolution

    def _spans(self) -> list[TickerSpan]:
        return spans_from_frame(self.store.security_ids())

    def resolve(self, ticker: str, on: dt.date) -> str:
        try:
            return resolve_ticker(self._spans(), ticker, on).security_id
        except UnknownTickerError as exc:
            raise NotFoundError(
                str(exc),
                hint="Tickers are resolved as of the query date. Pass a security_id "
                "(e.g. SEC-0005) to query a security regardless of its ticker.",
            ) from exc

    def resolve_any_date(self, ticker_or_id: str) -> str:
        key = ticker_or_id.strip().upper()
        if key.startswith("SEC-"):
            if not any(s.security_id == key for s in self._spans()):
                raise NotFoundError(f"unknown security_id {key!r}")
            return key
        try:
            return by_ticker_today(key).security_id
        except UnknownTickerError as exc:
            raise NotFoundError(f"ticker {key!r} is not in this dataset") from exc

    def _security(self, ticker_or_id: str, on: dt.date) -> str:
        key = ticker_or_id.strip().upper()
        return self.resolve_any_date(key) if key.startswith("SEC-") else self.resolve(key, on)

    def inputs(self, security_id: str) -> SecurityInputs:
        return load_inputs(self.store, security_id)

    # --------------------------------------------------------------- queries

    def get_consensus(
        self,
        ticker: str,
        fiscal_period: str,
        as_of: str | dt.date | dt.datetime,
        basis: Basis = "adjusted",
        known_as_of: str | dt.date | dt.datetime | None = None,
        *,
        max_items: int | None = None,
    ) -> ConsensusResult:
        instant = parse_instant(as_of, "as_of")
        assert instant is not None
        known = parse_instant(known_as_of, "known_as_of")
        if basis not in ("adjusted", "unadjusted"):
            raise InvalidRequestError(f"basis must be 'adjusted' or 'unadjusted', got {basis!r}")
        sid = self._security(ticker, instant.date())
        period = fiscal_period.strip().upper()
        try:
            resolved = resolve_period(
                period, master(sid).fye, instant, self.store.report_times(sid)
            )
        except ValueError as exc:
            raise InvalidRequestError(str(exc), hint="Use FY2025-style labels or FY1/FY2.") from exc
        v = self.store.versions(sid)
        versions = Versions(
            estimates=v["estimates"], factors=v["factors"], reports=v["reports"], fn=FN_VERSION
        )
        key = cache_key(
            sid, f"{resolved.fiscal_period}|{period}|{max_items}", instant, basis, known, versions
        )
        warnings = [resolved.note] if resolved.note else []
        return self.cache.get_or_compute(
            key,
            lambda: compute_consensus(
                self.inputs(sid),
                resolved.fiscal_period,
                instant,
                basis,
                known,
                requested_period=period,
                max_items=max_items,
                extra_warnings=warnings,
            ),
        )

    def get_series(
        self,
        ticker: str,
        fiscal_period: str,
        start: str | dt.date,
        end: str | dt.date,
        basis: Basis = "adjusted",
        known_as_of: str | dt.date | dt.datetime | None = None,
    ) -> ConsensusSeries:
        d0, d1 = parse_day(start, "start"), parse_day(end, "end")
        if d1 < d0:
            raise InvalidRequestError("end must not be before start")
        days = trading_days(d0, d1)
        if len(days) > MAX_SERIES_DAYS:
            raise InvalidRequestError(f"at most {MAX_SERIES_DAYS} trading days per request")
        period = fiscal_period.strip().upper()
        if is_relative(period):
            raise InvalidRequestError(
                "series need a fixed label (e.g. FY2025); FY1/FY2 jump at every fiscal-year roll"
            )
        try:
            parse_fixed(period)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        sid = self._security(ticker, d0)
        inputs = self.inputs(sid)
        known = parse_instant(known_as_of, "known_as_of")
        return ConsensusSeries(
            security=inputs.ref(d1),
            fiscal_period=period,
            basis=basis,
            known_as_of=known,
            points=consensus_series(inputs, period, days, basis, known),
        )

    def corporate_actions(self, ticker_or_id: str) -> CorporateActionList:
        sid = self.resolve_any_date(ticker_or_id)
        inputs = self.inputs(sid)
        # Describe the security by its most recent ticker (SIVBQ for SVB, say).
        last_day = max((s.valid_to or dt.date.max for s in inputs.spans), default=dt.date.max)
        on = min(last_day, dt.datetime.now(dt.UTC).date())
        return CorporateActionList(
            security=inputs.ref(on),
            actions=[f.to_model() for f in inputs.factors],
        )

    def universe(self, on: str | dt.date) -> list[tuple[str, str]]:
        return universe(self._spans(), parse_day(on, "date"))

    def revisions(
        self, ticker: str, fiscal_period: str, as_of: str | dt.date, lookback_days: int = 30
    ) -> RevisionSignal:
        instant = parse_instant(as_of, "as_of")
        assert instant is not None
        sid = self._security(ticker, instant.date())
        return revision_signal(self.inputs(sid), fiscal_period.upper(), instant, lookback_days)

    def explain(
        self,
        ticker: str,
        date: str | dt.date,
        fiscal_period: str = "FY1",
        *,
        max_items: int | None = 25,
    ) -> AdjustmentExplanation:
        day = parse_day(date, "date")
        result = self.get_consensus(ticker, fiscal_period, day, "adjusted", max_items=max_items)
        sid = result.security.security_id
        inputs = self.inputs(sid)
        naive = consensus_naive(inputs, result.fiscal_period, as_of_instant(day))
        lines: list[str] = []
        if result.corporate_actions_applied:
            for a in result.corporate_actions_applied:
                lines.append(
                    f"{a.action_type} ratio {a.ratio:g} (ex-date {a.ex_date}, announced "
                    f"{a.known_at:%Y-%m-%d %H:%M} UTC): estimates on the earlier share basis "
                    f"are divided by {a.ratio:g}. Source: {a.source_url}"
                )
        else:
            lines.append("No share-count action separates these estimates from the target basis.")
        by_source: dict[str, int] = {}
        for c in result.contributors:
            by_source[c.basis_source] = by_source.get(c.basis_source, 0) + 1
        if by_source:
            lines.append(
                "Basis decisions: "
                + ", ".join(f"{n} {src.replace('_', ' ')}" for src, n in sorted(by_source.items()))
                + "."
            )
        for c in result.contributors:
            if c.basis_source in ("ratio_inferred", "date_fallback") or c.factor != 1.0:
                lines.append(
                    f"{c.broker_id}: published {c.raw_value:g} on {c.entered_at.date()}, basis "
                    f"{c.basis_date} ({c.basis_source}), factor {c.factor:g} -> {c.value:.4g}"
                )
        for x in result.excluded:
            lines.append(f"Excluded {x.broker_id} ({x.reason}): {x.detail}")
        if naive is not None and result.mean is not None:
            lines.append(
                f"Naive average of raw published values would be {naive:.4g} vs "
                f"{result.mean:.4g} on the adjusted basis "
                f"(ratio {naive / result.mean:.3g})."
            )
        future_splits = [f for f in inputs.factors if f.is_share_count and f.ex_date > day]
        if future_splits:
            f_all = cumulative_factor(list(inputs.factors), day, dt.date.max)
            lines.append(
                f"Splits after {day} restate this consensus by a cumulative factor of {f_all:g} "
                "relative to what a screen showed that day (use basis='unadjusted' for that view)."
            )
        return AdjustmentExplanation(
            security=result.security,
            as_of=result.as_of,
            fiscal_period=result.fiscal_period,
            consensus_adjusted=result,
            naive_mean=naive,
            narrative=lines,
        )

    def require_sufficient(self, result: ConsensusResult) -> ConsensusResult:
        if result.insufficient:
            reasons: dict[str, int] = {}
            for x in result.excluded:
                reasons[x.reason] = reasons.get(x.reason, 0) + 1
            detail = ", ".join(f"{n} {r}" for r, n in reasons.items()) or "none live"
            raise InsufficientEstimatesError(
                f"only {result.count} qualifying estimate(s) for {result.security.security_id} "
                f"{result.fiscal_period} as of {result.as_of.date()} (minimum 3). "
                f"Exclusions: {detail}.",
                hint="Try a date when the security was covered, or check list_corporate_actions "
                "for a delisting.",
            )
        return result
