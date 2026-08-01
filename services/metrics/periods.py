"""Period parsing for metrics queries — pure functions, unit-tested.

Accepted forms:
    "lifetime"          -> business first_revenue_date .. today
    "ytd"               -> Jan 1 of current year .. today
    "2026"              -> 2026-01-01 .. 2026-12-31 (clamped to today)
    "2026-07"           -> that calendar month (clamped to today)
    "2026-01:2026-06"   -> explicit inclusive month range

The clamp-to-today rule prevents a "2026" query in July from implying
full-year coverage — the label carries the real end date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    from datetime import timedelta

    return start, end - timedelta(days=1)


def parse_period(spec: str, *, today: date, lifetime_start: date | None = None) -> Period:
    spec = spec.strip().lower()

    if spec == "lifetime":
        if lifetime_start is None:
            raise ValueError("lifetime period requires the business's first_revenue_date")
        return Period(lifetime_start, today, f"lifetime ({lifetime_start} .. {today})")

    if spec == "ytd":
        start = date(today.year, 1, 1)
        return Period(start, today, f"{today.year} YTD ({start} .. {today})")

    m = re.fullmatch(r"(\d{4})", spec)
    if m:
        year = int(m.group(1))
        start, end = date(year, 1, 1), date(year, 12, 31)
        if end > today:
            end = today
            return Period(start, end, f"{year} YTD ({start} .. {end})")
        return Period(start, end, f"{year} full year")

    m = re.fullmatch(r"(\d{4})-(\d{2})", spec)
    if m:
        start, end = _month_bounds(int(m.group(1)), int(m.group(2)))
        if end > today:
            end = today
        return Period(start, end, f"{start:%Y-%m} ({start} .. {end})")

    m = re.fullmatch(r"(\d{4})-(\d{2}):(\d{4})-(\d{2})", spec)
    if m:
        start, _ = _month_bounds(int(m.group(1)), int(m.group(2)))
        _, end = _month_bounds(int(m.group(3)), int(m.group(4)))
        if end > today:
            end = today
        return Period(start, end, f"{start} .. {end}")

    raise ValueError(
        f"Unrecognized period '{spec}'. Use lifetime | ytd | YYYY | YYYY-MM | YYYY-MM:YYYY-MM"
    )
