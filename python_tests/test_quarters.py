from datetime import date

import pytest

from ingestion.quarters import (
    expand_quarters,
    latest_completed_quarter,
    parse_quarter,
    previous_quarter,
    quarter_of,
)


def test_parse_and_quarter_of():
    assert parse_quarter("2024Q3") == (2024, 3)
    assert quarter_of(date(2024, 2, 23)) == "2024q1"
    assert quarter_of(date(2024, 12, 31)) == "2024q4"


@pytest.mark.parametrize("bad", ["2024q5", "2024-1", "q1", "2008q4", ""])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_quarter(bad)


def test_previous_quarter_wraps_year():
    assert previous_quarter("2024q1") == "2023q4"
    assert previous_quarter("2024q3") == "2024q2"


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 14), "2026q3"),  # two weeks after Q3 closed
        (date(2026, 1, 1), "2025q4"),
        (date(2026, 3, 31), "2025q4"),  # Q1 not over yet
    ],
)
def test_latest_completed_quarter(today, expected):
    assert latest_completed_quarter(today) == expected


def test_expand_ranges_lists_and_latest():
    assert expand_quarters(["2023q3:2024q2"]) == ["2023q3", "2023q4", "2024q1", "2024q2"]
    assert expand_quarters(["2024q1", "2023q4", "2024q1"]) == ["2023q4", "2024q1"]
    assert expand_quarters(["latest"], today=date(2026, 10, 14)) == ["2026q3"]


def test_expand_rejects_reversed_range():
    with pytest.raises(ValueError):
        expand_quarters(["2024q2:2024q1"])
