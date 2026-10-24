"""Single source of truth for data shapes.

* **Pandera frame schemas** validate every frame that enters the store. Ingest fails
  loudly on a violation instead of loading bad rows.
* **Pydantic models** define everything that crosses a boundary (HTTP, MCP, SDKs).
  ``sdk/schema.json`` is exported from these models and the Python and TypeScript
  SDKs are generated from that file, so the three cannot drift silently.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal, NewType

import pandera.polars as pa
import polars as pl
from pandera.api.polars.model_config import BaseConfig
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema

# --------------------------------------------------------------------------- types

SecurityId = NewType("SecurityId", str)
"""Permanent identifier, e.g. ``SEC-0001``. Never a ticker."""

FiscalPeriod = NewType("FiscalPeriod", str)
"""Fixed fiscal-year label, e.g. ``FY2025``."""

UnadjustedEPS = NewType("UnadjustedEPS", float)
"""EPS on the share basis it was published on."""

AdjustedEPS = NewType("AdjustedEPS", float)
"""EPS restated to some other share basis by cumulative split factors."""

Basis = Literal["adjusted", "unadjusted"]
"""``adjusted``: every value on the latest known share basis (continuous across splits).
``unadjusted``: every value on the share basis in effect at ``as_of`` (what a screen
showed that day)."""

Metric = Literal["EPS"]
EstimateAction = Literal["estimate", "confirm", "stop"]
ActionType = Literal["SPLIT", "REVERSE_SPLIT", "SPINOFF", "DELISTING"]
SHARE_COUNT_ACTIONS: frozenset[str] = frozenset({"SPLIT", "REVERSE_SPLIT"})
BasisSource = Literal["flagged", "ratio_inferred", "date_fallback", "entry_date"]
ExclusionReason = Literal[
    "stale", "not_reconfirmed_after_report", "pre_spinoff_perimeter", "outlier"
]
ErrorKind = Literal["NotFound", "InsufficientEstimates", "InvalidRequest"]

# ------------------------------------------------------------------ frame schemas


class Frame(pa.DataFrameModel):
    class Config(BaseConfig):
        strict = True
        coerce = False

    @classmethod
    def polars_schema(cls) -> dict[str, pl.DataType]:
        cols = cls.to_schema().columns
        return {name: col.dtype.type for name, col in cols.items()}

    @classmethod
    def empty_frame(cls) -> pl.DataFrame:
        return pl.DataFrame(schema=cls.polars_schema())


class SecurityIdsFrame(Frame):
    security_id: str = pa.Field(str_matches=r"^SEC-\d{4}$")
    ticker: str
    cik: str = pa.Field(str_matches=r"^\d{10}$")
    name: str
    valid_from: dt.date = pa.Field(nullable=True)
    valid_to: dt.date = pa.Field(nullable=True)
    delisted_on: dt.date = pa.Field(nullable=True)
    delist_reason: str = pa.Field(nullable=True)


class EstimatesFrame(Frame):
    """Individual analyst estimates. Append-only; a revision is a new row.

    ``superseded_at`` is not stored. It is derived in the store as the broker's
    next ``entered_at`` for the same security/period, so no row is ever updated.
    """

    estimate_id: str = pa.Field(unique=True)
    security_id: str = pa.Field(str_matches=r"^SEC-\d{4}$")
    broker_id: str
    metric: str = pa.Field(isin=["EPS"])
    fiscal_period: str = pa.Field(str_matches=r"^FY\d{4}$")
    action: str = pa.Field(isin=["estimate", "confirm", "stop"])
    value: float = pa.Field(nullable=True)
    share_basis_date: dt.date = pa.Field(nullable=True)
    entered_at: dt.datetime
    source: str = pa.Field(isin=["SYNTHETIC"])

    @pa.dataframe_check
    @classmethod
    def value_present_unless_stop(cls, data: Any) -> pl.LazyFrame:
        """A 'stop' row withdraws coverage and has no value; every other row needs one."""
        lf: pl.LazyFrame = data.lazyframe
        return lf.select((pl.col("action") == "stop") == pl.col("value").is_null())


class CorporateActionsFrame(Frame):
    """Corporate actions with knowledge time (``known_at``) and effective time (``ex_date``).

    ``ratio`` is new shares per old share: 10.0 for a 10-for-1 split, 0.125 for a
    1-for-8 reverse split, 1.0 for actions that do not change the share count.
    """

    security_id: str = pa.Field(str_matches=r"^SEC-\d{4}$")
    ticker: str
    action_type: str = pa.Field(isin=["SPLIT", "REVERSE_SPLIT", "SPINOFF", "DELISTING"])
    known_at: dt.datetime
    record_date: dt.date = pa.Field(nullable=True)
    ex_date: dt.date
    ratio: float = pa.Field(gt=0)
    terms: str
    source_url: str = pa.Field(str_startswith="https://")
    notes: str = pa.Field(nullable=True)

    @pa.dataframe_check
    @classmethod
    def known_before_effective(cls, data: Any) -> pl.LazyFrame:
        """An action must be public before (or on) the day it takes effect."""
        lf: pl.LazyFrame = data.lazyframe
        return lf.select(pl.col("known_at").dt.date() <= pl.col("ex_date"))

    @pa.dataframe_check
    @classmethod
    def ratio_matches_type(cls, data: Any) -> pl.LazyFrame:
        lf: pl.LazyFrame = data.lazyframe
        t, r = pl.col("action_type"), pl.col("ratio")
        return lf.select(
            pl.when(t == "SPLIT")
            .then(r > 1)
            .when(t == "REVERSE_SPLIT")
            .then(r < 1)
            .otherwise(r == 1.0)
        )


class ActualsFrame(Frame):
    """As-reported actuals. A restatement is a new row with a later ``filed_at``."""

    security_id: str = pa.Field(str_matches=r"^SEC-\d{4}$")
    metric: str = pa.Field(isin=["EPS"])
    fiscal_period: str = pa.Field(str_matches=r"^FY\d{4}$")
    period_end: dt.date
    value: float
    basis: str = pa.Field(isin=["GAAP_DILUTED"])
    filed_at: dt.datetime
    accession: str = pa.Field(str_matches=r"^\d{10}-\d{2}-\d{6}$")
    form: str


class ReportEventsFrame(Frame):
    """Earnings releases (8-K item 2.02), used for the re-confirmation rule and FY rolls."""

    security_id: str = pa.Field(str_matches=r"^SEC-\d{4}$")
    reported_at: dt.datetime
    accession: str = pa.Field(str_matches=r"^\d{10}-\d{2}-\d{6}$")
    source: str


class PricesFrame(Frame):
    """Daily closes as published by the price source (already split-adjusted there)."""

    security_id: str = pa.Field(str_matches=r"^SEC-\d{4}$")
    date: dt.date
    close: float = pa.Field(gt=0)
    volume: float = pa.Field(ge=0, nullable=True)


# ------------------------------------------------------------- boundary models


def _utc_iso(value: dt.datetime) -> str:
    aware = value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)
    return aware.isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[
    dt.datetime,
    PlainSerializer(_utc_iso, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
"""Internally every timestamp is naive UTC; on the wire it is explicit (``...Z``)."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecurityRef(Model):
    security_id: str = Field(description="Permanent identifier, e.g. SEC-0001. Never reused.")
    ticker: str | None = Field(description="Ticker in use on the as-of date, if any.")
    name: str
    cik: str = Field(description="SEC Central Index Key, zero-padded to 10 digits.")


class CorporateAction(Model):
    security_id: str
    ticker: str = Field(description="Ticker at the time of the action.")
    action_type: ActionType
    known_at: UtcDateTime = Field(description="When the action became public (UTC).")
    record_date: dt.date | None
    ex_date: dt.date = Field(description="First trading day on the new basis.")
    ratio: float = Field(
        description="New shares per old share (10.0 for 10-for-1, 0.125 for 1-for-8). "
        "1.0 when the share count does not change."
    )
    terms: str
    source_url: str = Field(description="Primary source (SEC filing or regulator release).")
    notes: str | None = None


class Contributor(Model):
    """One estimate that went into the consensus, with its basis decision."""

    estimate_id: str
    broker_id: str
    entered_at: UtcDateTime
    raw_value: float = Field(description="Value as published, on its own share basis.")
    basis_date: dt.date = Field(description="Date whose share count the raw value is on.")
    basis_source: BasisSource = Field(
        description="flagged: source supplied the basis; ratio_inferred: matched the broker's "
        "prior estimate divided by the split ratio; date_fallback: guessed from entry date "
        "near a split; entry_date: no split nearby, so the entry date is the basis."
    )
    basis_inferred: bool = Field(description="True when the basis was a fallback guess.")
    factor: float = Field(description="Cumulative share factor from basis_date to the target.")
    value: float = Field(description="raw_value / factor: the value on the target basis.")


class Exclusion(Model):
    estimate_id: str
    broker_id: str
    entered_at: UtcDateTime
    raw_value: float
    reason: ExclusionReason
    detail: str


class Versions(Model):
    estimates: int
    factors: int
    reports: int
    fn: str


class ConsensusResult(Model):
    """Point-in-time EPS consensus for one security and fiscal period."""

    security: SecurityRef
    metric: Metric = "EPS"
    fiscal_period: str = Field(description="Fixed fiscal-year label, e.g. FY2025.")
    requested_period: str = Field(description="Label as requested (may be FY1/FY2).")
    as_of: UtcDateTime = Field(description="Estimates known at this instant (UTC).")
    known_as_of: UtcDateTime | None = Field(
        description="Corporate-action knowledge cutoff. None means everything known today."
    )
    basis: Basis
    target_basis_date: dt.date | None = Field(
        description="Share basis of the returned values. None means the latest known basis."
    )
    target_basis: str = Field(description="Plain-language description of the share basis.")
    mean: float | None
    median: float | None
    high: float | None
    low: float | None
    stdev: float | None = Field(description="Sample standard deviation (n-1).")
    count: int
    insufficient: bool = Field(description="True when fewer than 3 estimates qualify.")
    contributors: list[Contributor]
    contributors_total: int
    excluded: list[Exclusion]
    excluded_total: int
    corporate_actions_applied: list[CorporateAction]
    warnings: list[str]
    method: str
    synthetic_estimates: bool = Field(
        default=True,
        description="Analyst estimates in this demo are synthetic; actuals and corporate "
        "actions are real.",
    )
    versions: Versions


class ConsensusPoint(Model):
    as_of: dt.date
    mean: float | None
    median: float | None
    count: int
    insufficient: bool


class ConsensusSeries(Model):
    security: SecurityRef
    fiscal_period: str
    basis: Basis
    known_as_of: UtcDateTime | None
    points: list[ConsensusPoint]


class CorporateActionList(Model):
    security: SecurityRef
    actions: list[CorporateAction]


class RevisionSignal(Model):
    """Per-broker revisions over a lookback window, comparing like with like."""

    security: SecurityRef
    fiscal_period: str
    as_of: UtcDateTime
    lookback_days: int
    brokers_compared: int
    up: int
    down: int
    unchanged: int
    mean_pct_change: float | None


class AdjustmentExplanation(Model):
    security: SecurityRef
    as_of: UtcDateTime
    fiscal_period: str
    consensus_adjusted: ConsensusResult
    naive_mean: float | None = Field(
        description="Average of raw published values with no basis handling, for contrast."
    )
    narrative: list[str]


class ErrorResponse(Model):
    error: ErrorKind
    message: str
    hint: str | None = None


API_MODELS: tuple[type[Model], ...] = (
    ConsensusResult,
    ConsensusSeries,
    CorporateActionList,
    AdjustmentExplanation,
    RevisionSignal,
    ErrorResponse,
)
