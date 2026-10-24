"""NYSE trading calendar (rule-based, so the synthetic data needs no network).

Covers the regular holidays plus the one-off closure in the demo's range
(2025-01-09, national day of mourning for President Carter).
"""

from __future__ import annotations

import datetime as dt
from functools import cache

SPECIAL_CLOSURES = frozenset({dt.date(2018, 12, 5), dt.date(2025, 1, 9)})


def _easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: dt.date) -> dt.date | None:
    """Saturday holidays move to Friday, Sunday holidays to Monday.

    NYSE exception: New Year's Day on a Saturday is not observed on the prior Friday.
    """
    if day.weekday() == 5:
        return None if (day.month, day.day) == (1, 1) else day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


@cache
def holidays(year: int) -> frozenset[dt.date]:
    fixed = [dt.date(year, 1, 1), dt.date(year, 7, 4), dt.date(year, 12, 25)]
    if year >= 2022:
        fixed.append(dt.date(year, 6, 19))
    days = {d for d in (_observed(x) for x in fixed) if d is not None}
    days |= {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    return frozenset(days | {d for d in SPECIAL_CLOSURES if d.year == year})


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def shift_trading_days(day: dt.date, n: int) -> dt.date:
    """Move ``n`` trading days from ``day`` (``n`` may be negative).

    ``n == 0`` rolls a non-trading day forward to the next trading day.
    """
    d = day
    if n == 0:
        while not is_trading_day(d):
            d += dt.timedelta(days=1)
        return d
    step = dt.timedelta(days=1 if n > 0 else -1)
    remaining = abs(n)
    while remaining:
        d += step
        if is_trading_day(d):
            remaining -= 1
    return d
