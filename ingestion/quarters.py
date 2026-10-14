"""Quarter identifiers as used by the SEC data sets, e.g. "2024q1".

Each quarterly file holds the filings *submitted* during that calendar quarter,
not the fiscal periods they report on. A 10-K for fiscal 2023 filed in February
2024 is in 2024q1.
"""

from __future__ import annotations

import re
from datetime import date

_QUARTER = re.compile(r"^(\d{4})q([1-4])$")


def parse_quarter(value: str) -> tuple[int, int]:
    """Parse "2024q1" into (2024, 1)."""
    match = _QUARTER.match(value.strip().lower())
    if not match:
        raise ValueError(f"invalid quarter {value!r}; expected the form 2024q1")
    year, quarter = int(match.group(1)), int(match.group(2))
    if year < 2009:
        raise ValueError(f"{value}: the SEC data sets start in 2009q1")
    return year, quarter


def format_quarter(year: int, quarter: int) -> str:
    return f"{year}q{quarter}"


def quarter_of(day: date) -> str:
    """Return the quarter identifier a given date falls in."""
    return format_quarter(day.year, (day.month - 1) // 3 + 1)


def previous_quarter(value: str) -> str:
    year, quarter = parse_quarter(value)
    return format_quarter(year - 1, 4) if quarter == 1 else format_quarter(year, quarter - 1)


def latest_completed_quarter(today: date) -> str:
    """The most recent quarter that has fully ended on `today`.

    This is the newest file the SEC can have published. It may not be published
    yet in the first days after quarter end; callers treat a 404 as "not yet",
    not as an error.
    """
    return previous_quarter(quarter_of(today))


def expand_quarters(specs: list[str], today: date | None = None) -> list[str]:
    """Expand CLI quarter arguments into a sorted, de-duplicated list.

    Accepts single quarters ("2024q1"), inclusive ranges ("2023q1:2024q2") and
    the keyword "latest" (the latest completed quarter).
    """
    quarters: set[str] = set()
    for spec in specs:
        spec = spec.strip().lower()
        if spec == "latest":
            quarters.add(latest_completed_quarter(today or date.today()))
        elif ":" in spec:
            start, end = spec.split(":", 1)
            start_year, start_q = parse_quarter(start)
            end_year, end_q = parse_quarter(end)
            if (start_year, start_q) > (end_year, end_q):
                raise ValueError(f"invalid range {spec!r}: start is after end")
            year, quarter = start_year, start_q
            while (year, quarter) <= (end_year, end_q):
                quarters.add(format_quarter(year, quarter))
                year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
        else:
            parse_quarter(spec)
            quarters.add(spec)
    return sorted(quarters, key=parse_quarter)
