"""Pydantic models for the third-party payloads we read (SEC EDGAR, Yahoo chart).

These describe other people's formats, not our API, so they live here rather than in
``schemas.py``. They ignore fields we don't use (upstream may add fields at any time)
but require every field we do use, with the type we rely on. A format change then
fails at the edge with a message naming the field, instead of a ``KeyError`` deep in
parsing. Frames built from these payloads are validated again by Pandera before they
reach the store.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ACCESSION = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"
ACCEPTANCE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$"


class UpstreamFormatError(ValueError):
    """An upstream payload no longer has the shape this pipeline relies on."""


class Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# --------------------------------------------------------------------- SEC EDGAR


class EdgarEpsFact(Upstream):
    """One ``EarningsPerShareDiluted`` fact from companyfacts (one period, one filing)."""

    start: dt.date
    end: dt.date
    val: float
    accn: str = Field(pattern=ACCESSION)
    form: str
    filed: dt.date
    fy: int | None = None
    fp: str | None = None
    frame: str | None = None

    @model_validator(mode="after")
    def _period_order(self) -> Self:
        if self.end < self.start:
            raise ValueError(f"period ends before it starts ({self.start} > {self.end})")
        return self


class _EpsUnits(Upstream):
    usd_per_share: list[EdgarEpsFact] = Field(alias="USD/shares")


class _EpsConcept(Upstream):
    units: _EpsUnits


class _UsGaap(Upstream):
    EarningsPerShareDiluted: _EpsConcept


class _Facts(Upstream):
    us_gaap: _UsGaap = Field(alias="us-gaap")


class CompanyFacts(Upstream):
    """``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`` (the parts we use)."""

    cik: int
    entityName: str
    facts: _Facts

    @property
    def eps_diluted(self) -> list[EdgarEpsFact]:
        return self.facts.us_gaap.EarningsPerShareDiluted.units.usd_per_share


class SubmissionsColumns(Upstream):
    """One page of the filing index: parallel arrays, one entry per filing."""

    accessionNumber: list[str]
    filingDate: list[str]
    acceptanceDateTime: list[str]
    form: list[str]
    items: list[str]

    @model_validator(mode="after")
    def _same_length(self) -> Self:
        lengths = {
            len(self.accessionNumber),
            len(self.filingDate),
            len(self.acceptanceDateTime),
            len(self.form),
            len(self.items),
        }
        if len(lengths) != 1:
            raise ValueError(f"filing index columns have different lengths: {sorted(lengths)}")
        return self


class EdgarFiling(Upstream):
    accession: str = Field(pattern=ACCESSION)
    form: str
    filing_date: dt.date
    acceptance: str = Field(pattern=ACCEPTANCE)
    items: str = ""


class EdgarSlim(Upstream):
    """The slimmed file committed under ``data/raw/edgar/`` (written by ``edgar.slim``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: int
    entity_name: str
    sources: list[str]
    tag: str
    eps_diluted: list[EdgarEpsFact]
    filings: list[EdgarFiling]


# ------------------------------------------------------------------ Yahoo chart


class YahooSplit(Upstream):
    date: int
    numerator: float = Field(gt=0)
    denominator: float = Field(gt=0)


class _YahooQuote(Upstream):
    close: list[float | None]
    volume: list[float | None]


class _YahooIndicators(Upstream):
    quote: list[_YahooQuote] = Field(min_length=1)


class _YahooEvents(Upstream):
    splits: dict[str, YahooSplit] = Field(default_factory=dict)


class YahooChartResult(Upstream):
    timestamp: list[int]
    indicators: _YahooIndicators
    events: _YahooEvents = Field(default_factory=_YahooEvents)

    @model_validator(mode="after")
    def _aligned(self) -> Self:
        quote = self.indicators.quote[0]
        if not len(self.timestamp) == len(quote.close) == len(quote.volume):
            raise ValueError("timestamps and quote arrays have different lengths")
        return self


def parse_upstream[M: Upstream](model: type[M], payload: Any, what: str) -> M:
    """Validate ``payload`` or raise ``UpstreamFormatError`` naming the source and fields."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()[:5]
        )
        more = f" (+{exc.error_count() - 5} more)" if exc.error_count() > 5 else ""
        raise UpstreamFormatError(f"{what} has an unexpected format: {fields}{more}") from exc
