# Generated from sdk/schema.json by `demo sdk`. Do not edit by hand.

from __future__ import annotations

from datetime import date as date_aliased
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel


class PitdemoApi(RootModel[Any]):
    root: Annotated[
        Any,
        Field(
            description="Boundary types for the point-in-time consensus demo. Generated from src/pitdemo/schemas.py; do not edit by hand.",
            title="pitdemo API",
        ),
    ]


class ConsensusPoint(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    as_of: Annotated[date_aliased, Field(title="As Of")]
    count: Annotated[int, Field(title="Count")]
    insufficient: Annotated[bool, Field(title="Insufficient")]
    mean: Annotated[float | None, Field(title="Mean")]
    median: Annotated[float | None, Field(title="Median")]


class Basis(StrEnum):
    adjusted = "adjusted"
    unadjusted = "unadjusted"


class BasisSource(StrEnum):
    flagged = "flagged"
    ratio_inferred = "ratio_inferred"
    date_fallback = "date_fallback"
    entry_date = "entry_date"


class Contributor(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    basis_date: Annotated[
        date_aliased,
        Field(
            description="Date whose share count the raw value is on.",
            title="Basis Date",
        ),
    ]
    basis_inferred: Annotated[
        bool,
        Field(
            description="True when the basis is a guess: a date fallback, or inferred from a prior estimate whose own basis was a guess.",
            title="Basis Inferred",
        ),
    ]
    basis_source: Annotated[
        BasisSource,
        Field(
            description="flagged: source supplied the basis; ratio_inferred: matched the broker's prior estimate divided by the split ratio; date_fallback: guessed from entry date near a split; entry_date: no split nearby, so the entry date is the basis.",
            title="Basis Source",
        ),
    ]
    broker_id: Annotated[str, Field(title="Broker Id")]
    entered_at: Annotated[AwareDatetime, Field(title="Entered At")]
    estimate_id: Annotated[str, Field(title="Estimate Id")]
    factor: Annotated[
        float,
        Field(
            description="Cumulative share factor from basis_date to the target.",
            title="Factor",
        ),
    ]
    raw_value: Annotated[
        float,
        Field(
            description="Value as published, on its own share basis.", title="Raw Value"
        ),
    ]
    value: Annotated[
        float,
        Field(
            description="raw_value / factor: the value on the target basis.",
            title="Value",
        ),
    ]


class ActionType(StrEnum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    SPINOFF = "SPINOFF"
    DELISTING = "DELISTING"


class CorporateAction(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    action_type: Annotated[ActionType, Field(title="Action Type")]
    ex_date: Annotated[
        date_aliased,
        Field(description="First trading day on the new basis.", title="Ex Date"),
    ]
    known_at: Annotated[
        AwareDatetime,
        Field(description="When the action became public (UTC).", title="Known At"),
    ]
    notes: Annotated[str | None, Field(title="Notes")] = None
    ratio: Annotated[
        float,
        Field(
            description="New shares per old share (10.0 for 10-for-1, 0.125 for 1-for-8). 1.0 when the share count does not change.",
            title="Ratio",
        ),
    ]
    record_date: Annotated[date_aliased | None, Field(title="Record Date")]
    security_id: Annotated[str, Field(title="Security Id")]
    source_url: Annotated[
        str,
        Field(
            description="Primary source (SEC filing or regulator release).",
            title="Source Url",
        ),
    ]
    terms: Annotated[str, Field(title="Terms")]
    ticker: Annotated[
        str, Field(description="Ticker at the time of the action.", title="Ticker")
    ]


class Error(StrEnum):
    NotFound = "NotFound"
    InsufficientEstimates = "InsufficientEstimates"
    InvalidRequest = "InvalidRequest"


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    error: Annotated[Error, Field(title="Error")]
    hint: Annotated[str | None, Field(title="Hint")] = None
    message: Annotated[str, Field(title="Message")]


class Reason(StrEnum):
    stale = "stale"
    not_reconfirmed_after_report = "not_reconfirmed_after_report"
    pre_spinoff_perimeter = "pre_spinoff_perimeter"
    outlier = "outlier"


class Exclusion(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    broker_id: Annotated[str, Field(title="Broker Id")]
    detail: Annotated[str, Field(title="Detail")]
    entered_at: Annotated[AwareDatetime, Field(title="Entered At")]
    estimate_id: Annotated[str, Field(title="Estimate Id")]
    raw_value: Annotated[float, Field(title="Raw Value")]
    reason: Annotated[Reason, Field(title="Reason")]


class InstantParam(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="ISO date (read as the end of that day, UTC) or ISO datetime (UTC unless an offset is given).",
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?)?$",
        ),
    ]


class SecurityRef(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    cik: Annotated[
        str,
        Field(
            description="SEC Central Index Key, zero-padded to 10 digits.", title="Cik"
        ),
    ]
    name: Annotated[str, Field(title="Name")]
    security_id: Annotated[
        str,
        Field(
            description="Permanent identifier, e.g. SEC-0001. Never reused.",
            title="Security Id",
        ),
    ]
    ticker: Annotated[
        str | None,
        Field(description="Ticker in use on the as-of date, if any.", title="Ticker"),
    ]


class UniverseMember(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    security_id: Annotated[str, Field(title="Security Id")]
    ticker: Annotated[
        str, Field(description="Ticker in use on the requested date.", title="Ticker")
    ]


class UniverseQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    date: Annotated[date_aliased, Field(title="Date")]


class Versions(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    estimates: Annotated[int, Field(title="Estimates")]
    factors: Annotated[int, Field(title="Factors")]
    fn: Annotated[str, Field(title="Fn")]
    reports: Annotated[int, Field(title="Reports")]


class ConsensusQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    as_of: Annotated[
        str,
        Field(
            description="ISO date (read as the end of that day, UTC) or ISO datetime (UTC unless an offset is given).",
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?)?$",
        ),
    ]
    basis: Annotated[Basis | None, Field(title="Basis")] = Basis.adjusted
    known_as_of: Annotated[
        InstantParam | None,
        Field(description="Ignore corporate actions announced after this instant."),
    ] = None
    period: Annotated[
        str,
        Field(
            description="Fixed fiscal-year label (FY2025, preferred) or relative FY1/FY2.",
            pattern="^\\s*[Ff][Yy]([0-9]{4}|[12])\\s*$",
        ),
    ]
    ticker: Annotated[
        str,
        Field(
            description="Ticker as of the query date (e.g. NVDA, SIVB), or a permanent security id (e.g. SEC-0005).",
            pattern="^\\s*([A-Za-z][A-Za-z0-9.\\-]{0,9}|[Ss][Ee][Cc]-[0-9]{4})\\s*$",
        ),
    ]


class ConsensusResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    as_of: Annotated[
        AwareDatetime,
        Field(description="Estimates known at this instant (UTC).", title="As Of"),
    ]
    basis: Annotated[Basis, Field(title="Basis")]
    contributors: Annotated[list[Contributor], Field(title="Contributors")]
    contributors_total: Annotated[int, Field(title="Contributors Total")]
    corporate_actions_applied: Annotated[
        list[CorporateAction], Field(title="Corporate Actions Applied")
    ]
    count: Annotated[int, Field(title="Count")]
    excluded: Annotated[list[Exclusion], Field(title="Excluded")]
    excluded_total: Annotated[int, Field(title="Excluded Total")]
    fiscal_period: Annotated[
        str,
        Field(
            description="Fixed fiscal-year label, e.g. FY2025.", title="Fiscal Period"
        ),
    ]
    high: Annotated[float | None, Field(title="High")]
    insufficient: Annotated[
        bool,
        Field(
            description="True when fewer than 3 estimates qualify.",
            title="Insufficient",
        ),
    ]
    known_as_of: Annotated[
        AwareDatetime | None,
        Field(
            description="Corporate-action knowledge cutoff. None means everything known today.",
            title="Known As Of",
        ),
    ]
    low: Annotated[float | None, Field(title="Low")]
    mean: Annotated[float | None, Field(title="Mean")]
    median: Annotated[float | None, Field(title="Median")]
    method: Annotated[str, Field(title="Method")]
    metric: Annotated[Literal["EPS"], Field(title="Metric")] = "EPS"
    requested_period: Annotated[
        str,
        Field(
            description="Label as requested (may be FY1/FY2).", title="Requested Period"
        ),
    ]
    security: SecurityRef
    stdev: Annotated[
        float | None,
        Field(description="Sample standard deviation (n-1).", title="Stdev"),
    ]
    synthetic_estimates: Annotated[
        bool | None,
        Field(
            description="Analyst estimates in this demo are synthetic; actuals and corporate actions are real.",
            title="Synthetic Estimates",
        ),
    ] = True
    target_basis: Annotated[
        str,
        Field(
            description="Plain-language description of the share basis.",
            title="Target Basis",
        ),
    ]
    target_basis_date: Annotated[
        date_aliased | None,
        Field(
            description="Share basis of the returned values. None means the latest known basis.",
            title="Target Basis Date",
        ),
    ]
    versions: Versions
    warnings: Annotated[list[str], Field(title="Warnings")]


class ConsensusSeries(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    basis: Annotated[Basis, Field(title="Basis")]
    fiscal_period: Annotated[str, Field(title="Fiscal Period")]
    known_as_of: Annotated[AwareDatetime | None, Field(title="Known As Of")]
    points: Annotated[list[ConsensusPoint], Field(title="Points")]
    security: SecurityRef


class CorporateActionList(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    actions: Annotated[list[CorporateAction], Field(title="Actions")]
    security: SecurityRef


class CorporateActionsQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ticker: Annotated[
        str,
        Field(
            description="Ticker as of the query date (e.g. NVDA, SIVB), or a permanent security id (e.g. SEC-0005).",
            pattern="^\\s*([A-Za-z][A-Za-z0-9.\\-]{0,9}|[Ss][Ee][Cc]-[0-9]{4})\\s*$",
        ),
    ]


class ExplainQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    date: Annotated[date_aliased, Field(title="Date")]
    period: Annotated[
        str | None,
        Field(
            description="Fixed fiscal-year label (FY2025, preferred) or relative FY1/FY2.",
            pattern="^\\s*[Ff][Yy]([0-9]{4}|[12])\\s*$",
        ),
    ] = "FY1"
    ticker: Annotated[
        str,
        Field(
            description="Ticker as of the query date (e.g. NVDA, SIVB), or a permanent security id (e.g. SEC-0005).",
            pattern="^\\s*([A-Za-z][A-Za-z0-9.\\-]{0,9}|[Ss][Ee][Cc]-[0-9]{4})\\s*$",
        ),
    ]


class RevisionSignal(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    as_of: Annotated[AwareDatetime, Field(title="As Of")]
    brokers_compared: Annotated[int, Field(title="Brokers Compared")]
    down: Annotated[int, Field(title="Down")]
    fiscal_period: Annotated[str, Field(title="Fiscal Period")]
    lookback_days: Annotated[int, Field(title="Lookback Days")]
    mean_pct_change: Annotated[float | None, Field(title="Mean Pct Change")]
    security: SecurityRef
    unchanged: Annotated[int, Field(title="Unchanged")]
    up: Annotated[int, Field(title="Up")]


class RevisionsQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    as_of: Annotated[
        str,
        Field(
            description="ISO date (read as the end of that day, UTC) or ISO datetime (UTC unless an offset is given).",
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?)?$",
        ),
    ]
    lookback_days: Annotated[int | None, Field(ge=1, le=365, title="Lookback Days")] = (
        30
    )
    period: Annotated[
        str,
        Field(
            description="Fixed fiscal-year label (FY2025, preferred) or relative FY1/FY2.",
            pattern="^\\s*[Ff][Yy]([0-9]{4}|[12])\\s*$",
        ),
    ]
    ticker: Annotated[
        str,
        Field(
            description="Ticker as of the query date (e.g. NVDA, SIVB), or a permanent security id (e.g. SEC-0005).",
            pattern="^\\s*([A-Za-z][A-Za-z0-9.\\-]{0,9}|[Ss][Ee][Cc]-[0-9]{4})\\s*$",
        ),
    ]


class SeriesQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    basis: Annotated[Basis | None, Field(title="Basis")] = Basis.adjusted
    end: Annotated[date_aliased, Field(title="End")]
    known_as_of: InstantParam | None = None
    period: Annotated[
        str,
        Field(
            description="Fixed fiscal-year label, e.g. FY2025.",
            pattern="^\\s*[Ff][Yy][0-9]{4}\\s*$",
        ),
    ]
    start: Annotated[date_aliased, Field(title="Start")]
    ticker: Annotated[
        str,
        Field(
            description="Ticker as of the query date (e.g. NVDA, SIVB), or a permanent security id (e.g. SEC-0005).",
            pattern="^\\s*([A-Za-z][A-Za-z0-9.\\-]{0,9}|[Ss][Ee][Cc]-[0-9]{4})\\s*$",
        ),
    ]


class Universe(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    date: Annotated[date_aliased, Field(title="Date")]
    securities: Annotated[list[UniverseMember], Field(title="Securities")]


class AdjustmentExplanation(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    as_of: Annotated[AwareDatetime, Field(title="As Of")]
    consensus_adjusted: ConsensusResult
    fiscal_period: Annotated[str, Field(title="Fiscal Period")]
    naive_mean: Annotated[
        float | None,
        Field(
            description="Average of raw published values with no basis handling, for contrast.",
            title="Naive Mean",
        ),
    ]
    narrative: Annotated[list[str], Field(title="Narrative")]
    security: SecurityRef
