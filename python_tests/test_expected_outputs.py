"""End-to-end: the built marts must equal what the sample spec says they should be.

The oracle is ingestion/sample_data.py's specification of what each fictional
company reported, combined with the business rules re-implemented here in plain
Python (which filing is authoritative, how Q4 is derived, how sectors and
medians are computed). Agreement between two independent implementations is the
evidence that the SQL is right.

Requires a built local warehouse:  make load-sample build   (or `make ci`).
Skipped, with a message, when the warehouse has not been built.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from ingestion.sample_data import (
    BIRCH_2023_RESTATED,
    BIRCH_NEW_NAME,
    COMPANIES,
    FILINGS,
    YEARS,
    current_period_values,
)

duckdb = pytest.importorskip("duckdb")

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DUCKDB_PATH", REPO_ROOT / "data" / "warehouse" / "sec_analytics.duckdb"))
MARTS = "analytics_marts"

# Mart columns that come straight from a reported concept of the same name.
DIRECT_COLUMNS = [
    "revenue", "cost_of_revenue", "gross_profit", "operating_expenses",
    "research_and_development", "sga_expense", "operating_income", "interest_expense",
    "pretax_income", "income_tax_expense", "net_income", "eps_basic", "eps_diluted",
    "cash_and_equivalents", "short_term_investments", "accounts_receivable", "inventory",
    "total_current_assets", "total_assets", "total_current_liabilities",
    "total_liabilities", "total_equity", "operating_cash_flow", "investing_cash_flow",
    "financing_cash_flow", "capital_expenditure",
]  # fmt: skip


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip(f"{DB_PATH} not found: run `make load-sample build` first")
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        connection.execute(f"select 1 from {MARTS}.fct_company_financials_annual limit 1")
    except duckdb.CatalogException:
        pytest.skip("marts not built: run `make build` first")
    yield connection
    connection.close()


def rows(con, sql) -> list[dict]:
    result = con.execute(sql)
    names = [d[0] for d in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def num(value):
    return None if value is None else float(value)


# --------------------------------------------------------------------------- #
# Oracle
# --------------------------------------------------------------------------- #


def authoritative_filings():
    """Latest-filed 10-K/10-Q per (company, period, base form) that has statements."""
    groups: dict[tuple, list] = {}
    for filing in FILINGS:
        base = filing.form.replace("/A", "")
        if base in ("10-K", "10-Q") and filing.year is not None:
            groups.setdefault((filing.cik, filing.period, base), []).append(filing)
    return {key: max(group, key=lambda f: (f.filed, f.adsh)) for key, group in groups.items()}


def expected_annual() -> dict[tuple[int, int], dict]:
    out = {}
    for (cik, _, base), filing in authoritative_filings().items():
        if base == "10-K":
            values = current_period_values(filing)
            out[(cik, filing.fiscal_year)] = {**values[4], **values[0], "_filing": filing}
    return out


def expected_quarters() -> dict[tuple[int, int, int], dict]:
    reported = {}
    for (cik, _, base), filing in authoritative_filings().items():
        if base == "10-Q":
            reported[(cik, filing.fiscal_year, filing.quarter_number)] = current_period_values(filing)[1]
    out = {key: {**values, "derived": False} for key, values in reported.items()}
    for (cik, fiscal_year), annual in expected_annual().items():
        quarters = [reported.get((cik, fiscal_year, q)) for q in (1, 2, 3)]
        if all(quarters):
            out[(cik, fiscal_year, 4)] = {
                "revenue": annual["revenue"] - sum(q["revenue"] for q in quarters),
                "net_income": annual["net_income"] - sum(q["net_income"] for q in quarters),
                "derived": True,
            }
    return out


def sector_of(sic: int) -> str:
    with (REPO_ROOT / "seeds" / "sic_sector_mapping.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if int(row["sic_low"]) <= sic <= int(row["sic_high"]):
                return row["sector"]
    return "Unclassified"


def percentile_cont(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_annual_financials_match_the_spec(con):
    expected = expected_annual()
    actual = {
        (r["cik"], r["fiscal_year"]): r
        for r in rows(con, f"select * from {MARTS}.fct_company_financials_annual")
    }
    assert set(actual) == set(expected), "wrong set of company-years"

    for key, spec in expected.items():
        row = actual[key]
        assert row["source_filing_id"] == spec["_filing"].adsh, key
        for column in DIRECT_COLUMNS:
            assert num(row[column]) == spec.get(column), (key, column)
        debt_parts = [spec[c] for c in ("debt_current", "debt_noncurrent") if c in spec]
        assert num(row["total_debt"]) == (sum(debt_parts) if debt_parts else None), key


def test_quarterly_financials_and_derived_q4(con):
    expected = expected_quarters()
    actual = {
        (r["cik"], r["fiscal_year"], r["fiscal_quarter"]): r
        for r in rows(con, f"select * from {MARTS}.fct_company_financials_quarterly")
    }
    assert set(actual) == set(expected)
    for key, spec in expected.items():
        assert actual[key]["is_derived_q4"] == spec["derived"], key
        assert num(actual[key]["revenue"]) == spec["revenue"], key
        assert num(actual[key]["net_income"]) == spec["net_income"], key
        if spec["derived"]:
            assert actual[key]["eps_basic"] is None, key


def test_authoritative_filing_choices(con):
    chosen = {
        r["filing_id"] for r in rows(con, f"select filing_id from {MARTS}.dim_filing where is_authoritative")
    }
    assert chosen == {f.adsh for f in authoritative_filings().values()}
    # CEDAR: the Part III amendment (no statements) must not win.
    cedar = next(f for f in FILINGS if f.company == "CEDAR" and f.form == "10-K/A")
    assert cedar.adsh not in chosen


def test_material_restatement(con):
    [row] = rows(con, f"select * from {MARTS}.mart_restatements")
    original = YEARS[("BIRCH", 2023)].income.statement()["net_income"]
    restated = BIRCH_2023_RESTATED.income.statement()["net_income"]
    assert row["cik"] == COMPANIES["BIRCH"].cik
    assert num(row["original_net_income"]) == original
    assert num(row["restated_net_income"]) == restated
    assert row["net_income_change_rate"] == pytest.approx((restated - original) / abs(original))
    assert row["is_material_restatement"] is True


def test_company_history_scd2(con):
    versions = rows(
        con,
        f"select * from {MARTS}.dim_company_history where cik = {COMPANIES['BIRCH'].cik} order by valid_from",
    )
    assert [v["company_name"] for v in versions] == ["BIRCH DATA SYSTEMS INC", BIRCH_NEW_NAME]
    assert [v["is_current"] for v in versions] == [False, True]
    assert versions[0]["valid_to"] == versions[1]["valid_from"]
    current = rows(con, f"select company_name from {MARTS}.dim_company where cik = {COMPANIES['BIRCH'].cik}")
    assert current == [{"company_name": BIRCH_NEW_NAME}]


def test_sector_benchmarks(con):
    # Gross margin of every company-year with revenue, grouped by sector.
    by_sector_year: dict[tuple[str, int], dict[int, float | None]] = {}
    for (cik, fiscal_year), spec in expected_annual().items():
        sic = next(c.sic for c in COMPANIES.values() if c.cik == cik)
        margin = (
            spec["gross_profit"] / spec["revenue"]
            if spec.get("gross_profit") is not None and spec["revenue"]
            else None
        )
        by_sector_year.setdefault((sector_of(sic), fiscal_year), {})[cik] = margin

    eligible = {k: v for k, v in by_sector_year.items() if len(v) >= 5}
    actual = {
        (r["sector"], r["fiscal_year"]): r for r in rows(con, f"select * from {MARTS}.mart_sector_benchmarks")
    }
    assert set(actual) == set(eligible)
    for key, margins in eligible.items():
        values = [m for m in margins.values() if m is not None]
        assert actual[key]["company_count"] == len(margins)
        assert actual[key]["median_gross_margin"] == pytest.approx(percentile_cont(values, 0.5))
        assert actual[key]["p25_gross_margin"] == pytest.approx(percentile_cont(values, 0.25))
        assert actual[key]["p75_gross_margin"] == pytest.approx(percentile_cont(values, 0.75))


def test_yoy_growth_uses_the_exact_prior_year(con):
    annual = expected_annual()
    for row in rows(con, f"select * from {MARTS}.fct_yoy_growth"):
        prior = annual.get((row["cik"], row["fiscal_year"] - 1))
        assert row["has_prior_year"] == (prior is not None)
        if prior and prior["revenue"] > 0:
            current = annual[(row["cik"], row["fiscal_year"])]
            assert row["revenue_growth_rate"] == pytest.approx(current["revenue"] / prior["revenue"] - 1)
        else:
            assert row["revenue_growth_rate"] is None


def test_scope_and_data_quality_signals(con):
    annual_ciks = {
        r["cik"] for r in rows(con, f"select distinct cik from {MARTS}.fct_company_financials_annual")
    }
    assert COMPANIES["JUNIPER"].cik not in annual_ciks  # 20-F: out of scope
    assert len(rows(con, f"select * from {MARTS}.dim_company")) == len(COMPANIES)
    [unparsed] = con.execute(
        "select count(*) from analytics_staging.stg_sec__numeric_facts "
        "where fact_value is null and fact_value_raw is not null"
    ).fetchone()
    assert unparsed == 1  # ELM's deliberately malformed value
