"""Fiscal calendars and fiscal-period labels.

Two kinds of period label exist in estimates data:

* **Fixed labels** (``FY2025``) name one fiscal year forever. Series keyed by a fixed
  label never jump.
* **Relative labels** (``FY1``, ``FY2``) mean "the next fiscal year not yet reported"
  and roll forward the moment the company reports. A relative series jumps at the
  roll by construction, so we resolve relative labels to fixed ones up front and
  flag queries that sit close to a roll.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

FIXED_RE = re.compile(r"^FY(\d{4})$")
RELATIVE_RE = re.compile(r"^FY([12])$")
ROLL_WARNING_DAYS = 10


@dataclass(frozen=True)
class FiscalYearEnd:
    """Fiscal year-end rule.

    ``weekday=None`` means the last calendar day of ``month``. Otherwise the year
    ends on the last ``weekday`` (Mon=0 .. Sun=6) of ``month``, the usual 52/53-week
    convention (NVIDIA: last Sunday of January; Apple: last Saturday of September).
    """

    month: int
    weekday: int | None = None

    def end_of(self, fiscal_year: int) -> dt.date:
        """Period end of fiscal year ``fiscal_year``, labelled by the calendar year it ends in."""
        nxt = (
            dt.date(fiscal_year + 1, 1, 1)
            if self.month == 12
            else dt.date(fiscal_year, self.month + 1, 1)
        )
        last = nxt - dt.timedelta(days=1)
        if self.weekday is None:
            return last
        return last - dt.timedelta(days=(last.weekday() - self.weekday) % 7)


def fiscal_label_for_period_end(end: dt.date) -> str:
    """Label a fiscal year by the calendar year its period ends in.

    A 52/53-week year that spills into the first days of January belongs to the
    previous calendar year (e.g. a year ending 2022-01-01 is FY2021).
    """
    year = end.year - 1 if (end.month == 1 and end.day <= 7) else end.year
    return f"FY{year}"


def parse_fixed(label: str) -> int:
    m = FIXED_RE.match(label)
    if m is None:
        raise ValueError(f"not a fixed fiscal-year label: {label!r} (expected e.g. 'FY2025')")
    return int(m.group(1))


def is_relative(label: str) -> bool:
    return RELATIVE_RE.match(label) is not None


def fiscal_year_containing(rule: FiscalYearEnd, day: dt.date) -> int:
    """The fiscal year (by label) whose period includes ``day``."""
    year = day.year
    while rule.end_of(year - 1) >= day:
        year -= 1
    while rule.end_of(year) < day:
        year += 1
    return year


def annual_report_time(
    rule: FiscalYearEnd, fiscal_year: int, report_times: Sequence[dt.datetime]
) -> dt.datetime | None:
    """When fiscal year ``fiscal_year`` was reported: the first earnings release after year end."""
    end = rule.end_of(fiscal_year)
    after = [t for t in sorted(report_times) if t.date() > end]
    return after[0] if after else None


@dataclass(frozen=True)
class ResolvedPeriod:
    requested: str
    fiscal_period: str
    rolled_at: dt.datetime | None  # when the relative label last rolled, if relevant
    near_roll: bool
    note: str | None


def resolve_period(
    label: str,
    rule: FiscalYearEnd,
    as_of: dt.datetime,
    report_times: Sequence[dt.datetime],
) -> ResolvedPeriod:
    """Turn ``FY1``/``FY2`` into a fixed label as of ``as_of``; pass fixed labels through.

    FY1 is the earliest fiscal year whose annual results were not yet released at
    ``as_of``. It rolls at the earnings release that reports the prior year.
    """
    if not is_relative(label):
        parse_fixed(label)
        return ResolvedPeriod(label, label, None, False, None)
    offset = int(label[2:]) - 1
    known = [t for t in report_times if t <= as_of]
    current = fiscal_year_containing(rule, as_of.date())
    prior = current - 1
    prior_reported = annual_report_time(rule, prior, known)
    fy1 = current if prior_reported is not None else prior
    fixed = f"FY{fy1 + offset}"
    rolled_at = prior_reported if prior_reported is not None else None
    if rolled_at is None:
        rolled_at = annual_report_time(rule, prior - 1, known)
    next_roll = annual_report_time(rule, fy1, report_times)
    near = False
    note = None
    if rolled_at is not None and (as_of - rolled_at).days <= ROLL_WARNING_DAYS:
        near = True
        note = (
            f"{label} rolled to {fixed} on {rolled_at.date()} (prior year reported). "
            f"A {label} time series jumps here; use the fixed label {fixed} "
            "for a continuous series."
        )
    elif (
        next_roll is not None
        and next_roll > as_of
        and (next_roll - as_of).days <= ROLL_WARNING_DAYS
    ):
        near = True
        note = (
            f"{label} rolls on {next_roll.date()}; a {label} series jumps there. "
            f"Use the fixed label {fixed} for a continuous series."
        )
    return ResolvedPeriod(label, fixed, rolled_at, near, note)
