"""Generate the synthetic sample of the SEC Financial Statement Data Sets.

ALL DATA PRODUCED HERE IS FICTIONAL. Company names, CIKs (99000xx, far above any
CIK the SEC has assigned) and figures are invented. The files follow the real
SEC layout exactly (tab-delimited, UTF-8, lowercase headers, the current NUM
layout with `segments`) so the same loader and models run on sample and real data.

The data is deterministic and hand-specified, not random: every company exists
to exercise a specific behaviour of the models (see SCENARIOS below and
data/sample/README.md). python_tests/test_expected_outputs.py uses the same spec
as an oracle for what the dbt marts must contain.

    python -m ingestion.sample_data            # (re)write data/sample/
    python -m ingestion.sample_data --check    # fail if data/sample/ is stale
"""

from __future__ import annotations

import argparse
import calendar
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from ingestion.quarters import quarter_of
from ingestion.sec_layout import NUM, PRE, SUB, TAG

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "sample"

# Share of annual flows reported in Q1-Q3. Q4 is whatever remains, which is
# exactly what int_financials__quarterly has to derive.
QUARTER_SHARE = {1: 0.23, 2: 0.24, 3: 0.25}
# Prior-period comparative columns are scaled by this factor so that any
# comparative value leaking into a current-period row is detectable.
COMPARATIVE_RECAST = 1.01

SCENARIOS = {
    "ALDER": "Clean filer. Reports revenue under two tags; the ASC 606 tag must win.",
    "BIRCH": "Renamed in 2024 (SCD2). FY2023 10-K amended with a material restatement.",
    "CEDAR": "Co-registrant and segment rows that must not reach company totals; "
    "a Part III-only 10-K/A (no financial statements) that must not win dedup.",
    "DOGWOOD": "Pre-revenue, negative equity, missing Q2 10-Q (no derived Q4).",
    "ELM": "Late filer (FY2023 10-K arrives in 2024q2), a custom revenue tag, "
    "and one unparseable numeric value.",
    "FIR": "June fiscal year end; quarters span two calendar years.",
    "GROVE": "Bank: no gross profit, no current assets/liabilities.",
    "HAZEL": "Redeemable preferred (temporary equity): A != L + E, balance test warns.",
    "IRONWOOD": "Pre-ASC 606 revenue tag (SalesRevenueNet) in FY2022.",
    "JUNIPER": "Foreign private issuer filing a 20-F: out of scope for the marts.",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def k(value: float) -> int:
    """Round to whole thousands, as filers do."""
    return int(round(value / 1000.0)) * 1000


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def years_before(day: date, years: int = 1) -> date:
    return month_end(day.year - years, day.month)


def humanize(tag: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tag)


# --------------------------------------------------------------------------- #
# Concepts and their default us-gaap tags
# --------------------------------------------------------------------------- #

IS_CONCEPTS = [
    "revenue", "cost_of_revenue", "gross_profit", "research_and_development",
    "sga_expense", "operating_expenses", "operating_income", "interest_expense",
    "pretax_income", "income_tax_expense", "net_income", "eps_basic", "eps_diluted",
]  # fmt: skip
BS_CONCEPTS = [
    "cash_and_equivalents", "short_term_investments", "accounts_receivable",
    "inventory", "total_current_assets", "total_assets", "total_current_liabilities",
    "debt_current", "debt_noncurrent", "total_liabilities", "temporary_equity",
    "total_equity", "total_liabilities_and_equity",
]  # fmt: skip
CF_CONCEPTS = [
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "capital_expenditure", "depreciation_amortization",
]  # fmt: skip

DEFAULT_TAGS = {
    "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "cost_of_revenue": "CostOfRevenue",
    "gross_profit": "GrossProfit",
    "research_and_development": "ResearchAndDevelopmentExpense",
    "sga_expense": "SellingGeneralAndAdministrativeExpense",
    "operating_expenses": "OperatingExpenses",
    "operating_income": "OperatingIncomeLoss",
    "interest_expense": "InterestExpense",
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
    ),
    "income_tax_expense": "IncomeTaxExpenseBenefit",
    "net_income": "NetIncomeLoss",
    "eps_basic": "EarningsPerShareBasic",
    "eps_diluted": "EarningsPerShareDiluted",
    "cash_and_equivalents": "CashAndCashEquivalentsAtCarryingValue",
    "short_term_investments": "ShortTermInvestments",
    "accounts_receivable": "AccountsReceivableNetCurrent",
    "inventory": "InventoryNet",
    "total_current_assets": "AssetsCurrent",
    "total_assets": "Assets",
    "total_current_liabilities": "LiabilitiesCurrent",
    "debt_current": "LongTermDebtCurrent",
    "debt_noncurrent": "LongTermDebtNoncurrent",
    "total_liabilities": "Liabilities",
    "temporary_equity": "TemporaryEquityCarryingAmountAttributableToParent",
    "total_equity": "StockholdersEquity",
    "total_liabilities_and_equity": "LiabilitiesAndStockholdersEquity",
    "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
    "investing_cash_flow": "NetCashProvidedByUsedInInvestingActivities",
    "financing_cash_flow": "NetCashProvidedByUsedInFinancingActivities",
    "capital_expenditure": "PaymentsToAcquirePropertyPlantAndEquipment",
    "depreciation_amortization": "DepreciationDepletionAndAmortization",
}
PER_SHARE = {"eps_basic", "eps_diluted"}
CREDIT_CONCEPTS = {
    "revenue", "gross_profit", "operating_income", "pretax_income", "net_income",
    "total_current_liabilities", "debt_current", "debt_noncurrent",
    "total_liabilities", "temporary_equity", "total_equity",
    "total_liabilities_and_equity",
}  # fmt: skip


# --------------------------------------------------------------------------- #
# Financial specs: the "true" figures each company reports
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Income:
    """Annual income-statement drivers. Derived lines are computed, so every
    statement (annual or quarterly) is internally consistent."""

    revenue: int
    cogs: int | None
    rd: int | None
    sga: int | None
    interest: int = 0
    shares: int = 50_000_000
    tax_rate: float = 0.21
    omit: frozenset[str] = frozenset()

    def statement(self, scale: float = 1.0) -> dict[str, float]:
        revenue = k(self.revenue * scale)
        cogs = None if self.cogs is None else k(self.cogs * scale)
        rd = None if self.rd is None else k(self.rd * scale)
        sga = None if self.sga is None else k(self.sga * scale)
        interest = k(self.interest * scale)
        gross_profit = None if cogs is None else revenue - cogs
        operating_expenses = (rd or 0) + (sga or 0)
        operating_income = (gross_profit if gross_profit is not None else revenue) - (operating_expenses)
        pretax = operating_income - interest
        tax = k(pretax * self.tax_rate) if pretax > 0 else 0
        net_income = pretax - tax
        values = {
            "revenue": revenue,
            "cost_of_revenue": cogs,
            "gross_profit": gross_profit,
            "research_and_development": rd,
            "sga_expense": sga,
            "operating_expenses": operating_expenses or None,
            "operating_income": operating_income,
            "interest_expense": interest or None,
            "pretax_income": pretax,
            "income_tax_expense": tax,
            "net_income": net_income,
            "eps_basic": round(net_income / self.shares, 2),
            "eps_diluted": round(net_income / (self.shares * 1.02), 2),
        }
        return {c: v for c, v in values.items() if v is not None and c not in self.omit}


@dataclass(frozen=True)
class Balance:
    """Balance sheet at a period end. Equity is derived so the sheet balances
    (A = L + temporary equity + E)."""

    cash: int
    total_current_assets: int | None
    total_assets: int
    total_current_liabilities: int | None
    total_liabilities: int
    short_term_investments: int | None = None
    accounts_receivable: int | None = None
    inventory: int | None = None
    debt_current: int | None = None
    debt_noncurrent: int | None = None
    temporary_equity: int | None = None

    def statement(self) -> dict[str, int]:
        equity = self.total_assets - self.total_liabilities - (self.temporary_equity or 0)
        values = {
            "cash_and_equivalents": self.cash,
            "short_term_investments": self.short_term_investments,
            "accounts_receivable": self.accounts_receivable,
            "inventory": self.inventory,
            "total_current_assets": self.total_current_assets,
            "total_assets": self.total_assets,
            "total_current_liabilities": self.total_current_liabilities,
            "debt_current": self.debt_current,
            "debt_noncurrent": self.debt_noncurrent,
            "total_liabilities": self.total_liabilities,
            "temporary_equity": self.temporary_equity,
            "total_equity": equity,
            "total_liabilities_and_equity": self.total_assets,
        }
        return {c: v for c, v in values.items() if v is not None}

    def interpolate(self, later: Balance, fraction: float) -> Balance:
        def mix(a: int | None, b: int | None) -> int | None:
            if a is None or b is None:
                return b if a is None else a
            return k(a + (b - a) * fraction)

        return Balance(**{f: mix(getattr(self, f), getattr(later, f)) for f in self.__dataclass_fields__})

    def scaled(self, factor: float) -> Balance:
        return Balance(
            **{
                f: (None if getattr(self, f) is None else k(getattr(self, f) * factor))
                for f in self.__dataclass_fields__
            }
        )


@dataclass(frozen=True)
class CashFlow:
    operating: int
    investing: int
    financing: int
    capex: int | None = None
    depreciation: int | None = None

    def statement(self, scale: float = 1.0) -> dict[str, int]:
        values = {
            "operating_cash_flow": k(self.operating * scale),
            "investing_cash_flow": k(self.investing * scale),
            "financing_cash_flow": k(self.financing * scale),
            "capital_expenditure": None if self.capex is None else k(self.capex * scale),
            "depreciation_amortization": (
                None if self.depreciation is None else k(self.depreciation * scale)
            ),
        }
        return {c: v for c, v in values.items() if v is not None}


@dataclass(frozen=True)
class Year:
    """One company's fiscal year: annual flows plus the year-end balance sheet."""

    income: Income
    balance: Balance
    cash_flow: CashFlow | None


@dataclass(frozen=True)
class Company:
    key: str
    cik: int
    name: str
    sic: int
    fye: str  # mmdd
    city: str
    state: str | None
    country: str = "US"
    country_inc: str = "US"
    state_inc: str | None = "DE"
    nciks: int = 1
    aciks: str | None = None


M = 1_000_000  # figures below are written in millions for readability

COMPANIES = {
    c.key: c
    for c in [
        Company("ALDER", 9900001, "ALDER SOFTWARE INC", 7372, "1231", "AUSTIN", "TX"),
        Company("BIRCH", 9900002, "BIRCH DATA SYSTEMS INC", 7374, "1231", "DENVER", "CO"),
        Company(
            "CEDAR", 9900003, "CEDAR NETWORKS CORP", 7373, "1231", "RALEIGH", "NC",
            nciks=2, aciks="9900013",
        ),
        Company("DOGWOOD", 9900004, "DOGWOOD LABS INC", 7371, "1231", "BOSTON", "MA"),
        Company("ELM", 9900005, "ELM COMMERCE INC", 7370, "1231", "SEATTLE", "WA"),
        Company("FIR", 9900006, "FIR MEDICAL DEVICES CORP", 3841, "0630", "MINNEAPOLIS", "MN"),
        Company("GROVE", 9900007, "GROVE REGIONAL BANCORP", 6022, "1231", "COLUMBUS", "OH"),
        Company("HAZEL", 9900008, "HAZEL BIOTHERAPEUTICS INC", 2836, "1231", "SAN DIEGO", "CA"),
        Company("IRONWOOD", 9900009, "IRONWOOD SECURITY SOFTWARE INC", 7372, "1231", "RESTON", "VA"),
        Company(
            "JUNIPER", 9900010, "JUNIPER HOLDINGS PLC", 7372, "1231", "LONDON", None,
            country="GB", country_inc="GB", state_inc=None,
        ),
    ]
}  # fmt: skip

# Fiscal-year specs, keyed by (company, fiscal year).
YEARS: dict[tuple[str, int], Year] = {
    ("ALDER", 2022): Year(
        Income(410 * M, 90.2 * M, 90 * M, 150 * M, interest=2 * M),
        Balance(120 * M, 270 * M, 640 * M, 150 * M, 260 * M, short_term_investments=80 * M,
                accounts_receivable=60 * M, debt_current=10 * M, debt_noncurrent=40 * M),
        CashFlow(100 * M, -45 * M, -25 * M, capex=22 * M, depreciation=16 * M),
    ),
    ("ALDER", 2023): Year(
        Income(480 * M, 96 * M, 100 * M, 165 * M, interest=2 * M),
        Balance(160 * M, 330 * M, 720 * M, 165 * M, 280 * M, short_term_investments=90 * M,
                accounts_receivable=70 * M, debt_current=10 * M, debt_noncurrent=30 * M),
        CashFlow(120 * M, -50 * M, -30 * M, capex=25 * M, depreciation=18 * M),
    ),
    ("ALDER", 2024): Year(
        Income(560 * M, 106 * M, 112 * M, 180 * M, interest=2 * M),
        Balance(190 * M, 380 * M, 800 * M, 175 * M, 290 * M, short_term_investments=95 * M,
                accounts_receivable=80 * M, debt_current=10 * M, debt_noncurrent=20 * M),
        CashFlow(140 * M, -55 * M, -35 * M, capex=28 * M, depreciation=20 * M),
    ),
    ("BIRCH", 2023): Year(
        Income(260 * M, 93.6 * M, 35 * M, 80 * M, interest=5 * M, shares=30_000_000),
        Balance(55 * M, 120 * M, 410 * M, 90 * M, 230 * M, accounts_receivable=45 * M,
                debt_current=15 * M, debt_noncurrent=85 * M),
        CashFlow(48 * M, -30 * M, -6 * M, capex=14 * M, depreciation=11 * M),
    ),
    ("BIRCH", 2024): Year(
        Income(300 * M, 105 * M, 40 * M, 88 * M, interest=5 * M, shares=30_000_000),
        Balance(70 * M, 140 * M, 450 * M, 95 * M, 240 * M, accounts_receivable=50 * M,
                debt_current=15 * M, debt_noncurrent=75 * M),
        CashFlow(55 * M, -32 * M, -10 * M, capex=15 * M, depreciation=12 * M),
    ),
    ("CEDAR", 2023): Year(
        Income(150 * M, 67.5 * M, 20 * M, 45 * M, interest=1 * M, shares=20_000_000),
        Balance(40 * M, 95 * M, 230 * M, 60 * M, 110 * M, accounts_receivable=30 * M,
                inventory=12 * M, debt_noncurrent=25 * M),
        CashFlow(22 * M, -12 * M, -4 * M, capex=8 * M, depreciation=6 * M),
    ),
    ("DOGWOOD", 2022): Year(
        Income(0, None, 25 * M, 12 * M, interest=0.5 * M, shares=15_000_000),
        Balance(50 * M, 52 * M, 60 * M, 12 * M, 27 * M, debt_noncurrent=15 * M),
        CashFlow(-33 * M, -2 * M, 20 * M, capex=1 * M),
    ),
    ("DOGWOOD", 2023): Year(
        Income(0, None, 30 * M, 14 * M, interest=0.8 * M, shares=15_000_000),
        Balance(20 * M, 22 * M, 30 * M, 15 * M, 40 * M, debt_noncurrent=25 * M),
        CashFlow(-40 * M, -1 * M, 11 * M, capex=0.5 * M),
    ),
    ("ELM", 2022): Year(
        Income(80 * M, 25 * M, 7 * M, 34 * M, shares=12_000_000),
        Balance(30 * M, 48 * M, 95 * M, 28 * M, 40 * M, accounts_receivable=12 * M),
        CashFlow(14 * M, -5 * M, -2 * M, capex=3 * M, depreciation=2 * M),
    ),
    ("ELM", 2023): Year(
        Income(95 * M, 28.5 * M, 8 * M, 40 * M, shares=12_000_000),
        Balance(36 * M, 56 * M, 108 * M, 31 * M, 42 * M, accounts_receivable=14 * M),
        CashFlow(17 * M, -6 * M, -3 * M, capex=4 * M, depreciation=2.5 * M),
    ),
    ("ELM", 2024): Year(
        Income(112 * M, 33 * M, 9 * M, 46 * M, shares=12_000_000),
        Balance(42 * M, 64 * M, 122 * M, 33 * M, 44 * M, accounts_receivable=16 * M),
        CashFlow(20 * M, -7 * M, -3 * M, capex=4.5 * M, depreciation=3 * M),
    ),
    ("FIR", 2022): Year(
        Income(270 * M, 116 * M, 23 * M, 82 * M, interest=9.5 * M, shares=25_000_000),
        Balance(35 * M, 150 * M, 520 * M, 70 * M, 300 * M, accounts_receivable=45 * M,
                inventory=50 * M, debt_current=20 * M, debt_noncurrent=180 * M),
        CashFlow(38 * M, -24 * M, -10 * M, capex=20 * M, depreciation=17 * M),
    ),
    ("FIR", 2023): Year(
        Income(300 * M, 126 * M, 25 * M, 90 * M, interest=9 * M, shares=25_000_000),
        Balance(42 * M, 165 * M, 545 * M, 75 * M, 295 * M, accounts_receivable=50 * M,
                inventory=55 * M, debt_current=20 * M, debt_noncurrent=165 * M),
        CashFlow(45 * M, -26 * M, -15 * M, capex=22 * M, depreciation=18 * M),
    ),
    ("FIR", 2024): Year(
        Income(320 * M, 131 * M, 27 * M, 95 * M, interest=8 * M, shares=25_000_000),
        Balance(50 * M, 175 * M, 560 * M, 78 * M, 285 * M, accounts_receivable=53 * M,
                inventory=57 * M, debt_current=20 * M, debt_noncurrent=150 * M),
        CashFlow(50 * M, -28 * M, -16 * M, capex=23 * M, depreciation=19 * M),
    ),
    # Banks report non-interest expense rather than SG&A or operating income, so
    # those lines are computed (for a realistic net income) but not reported.
    ("GROVE", 2022): Year(
        Income(150 * M, None, None, 65 * M, interest=40 * M, shares=40_000_000,
               omit=frozenset({"operating_income", "operating_expenses", "sga_expense"})),
        Balance(300 * M, None, 4_000 * M, None, 3_600 * M),
        CashFlow(50 * M, -120 * M, 80 * M, capex=5 * M),
    ),
    ("GROVE", 2023): Year(
        Income(170 * M, None, None, 70 * M, interest=60 * M, shares=40_000_000,
               omit=frozenset({"operating_income", "operating_expenses", "sga_expense"})),
        Balance(320 * M, None, 4_300 * M, None, 3_870 * M),
        CashFlow(55 * M, -150 * M, 100 * M, capex=6 * M),
    ),
    ("HAZEL", 2022): Year(
        Income(8 * M, None, 105 * M, 26 * M, shares=60_000_000),
        Balance(250 * M, 300 * M, 450 * M, 35 * M, 55 * M, temporary_equity=100 * M),
        CashFlow(-110 * M, -10 * M, 150 * M, capex=6 * M),
    ),
    ("HAZEL", 2023): Year(
        Income(12 * M, None, 120 * M, 30 * M, shares=60_000_000),
        Balance(210 * M, 260 * M, 400 * M, 40 * M, 60 * M, temporary_equity=100 * M),
        CashFlow(-125 * M, -8 * M, 90 * M, capex=5 * M),
    ),
    ("IRONWOOD", 2022): Year(
        Income(60 * M, 15 * M, 15 * M, 27 * M, shares=18_000_000),
        Balance(25 * M, 40 * M, 85 * M, 20 * M, 35 * M, accounts_receivable=10 * M),
        CashFlow(9 * M, -3 * M, -1 * M, capex=2 * M),
    ),
    ("IRONWOOD", 2023): Year(
        Income(75 * M, 18.75 * M, 15 * M, 30 * M, shares=18_000_000),
        Balance(31 * M, 47 * M, 96 * M, 22 * M, 37 * M, accounts_receivable=12 * M),
        CashFlow(12 * M, -4 * M, -1 * M, capex=2.5 * M),
    ),
    ("JUNIPER", 2023): Year(
        Income(500 * M, 200 * M, 60 * M, 140 * M, interest=10 * M, shares=80_000_000),
        Balance(90 * M, 260 * M, 900 * M, 180 * M, 420 * M, accounts_receivable=70 * M),
        CashFlow(110 * M, -60 * M, -30 * M, capex=30 * M),
    ),
}  # fmt: skip

# BIRCH's FY2023 10-K/A restates SG&A up by $10M (a missed accrual), cutting net
# income by about 22% -- a material restatement.
BIRCH_2023_RESTATED = replace(
    YEARS[("BIRCH", 2023)],
    income=replace(YEARS[("BIRCH", 2023)].income, sga=90 * M),
)

BIRCH_NEW_NAME = "BIRCH CLOUD INC"


# --------------------------------------------------------------------------- #
# Filings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExtraFact:
    """A num row outside the standard statements (custom tags, coreg, segments,
    unparseable values)."""

    tag: str
    value: str
    statement: str = "IS"
    qtrs: int = 4
    uom: str = "USD"
    segments: str | None = None
    coreg: str | None = None
    custom: bool = False
    presented: bool = True


@dataclass(frozen=True)
class Filing:
    company: str
    form: str
    period: date
    fiscal_year: int
    fiscal_period: str  # FY, Q1, Q2, Q3
    filed: date
    sequence: int
    year: Year | None  # None: no financial statements (Part III amendment)
    name: str | None = None  # name at filing, when different from the current one
    former_name: str | None = None
    name_changed: date | None = None
    prevrpt: bool = False
    tag_overrides: dict[str, str] = field(default_factory=dict)
    duplicate_revenue_tag: tuple[str, int] | None = None
    extra_facts: tuple[ExtraFact, ...] = ()
    present_cash_on_cash_flow: bool = False
    comprehensive_income: bool = False

    @property
    def cik(self) -> int:
        return COMPANIES[self.company].cik

    @property
    def adsh(self) -> str:
        return f"{self.cik:010d}-{self.filed.year % 100:02d}-{self.sequence:06d}"

    @property
    def dataset(self) -> str:
        return quarter_of(self.filed)

    @property
    def quarter_number(self) -> int | None:
        return int(self.fiscal_period[1]) if self.fiscal_period.startswith("Q") else None

    @property
    def taxonomy(self) -> str:
        return f"us-gaap/{self.filed.year - 1}"


def _10k(company, fy, period, filed, seq, year=None, **kw) -> Filing:
    year = year or YEARS[(company, fy)]
    return Filing(company, kw.pop("form", "10-K"), period, fy, "FY", filed, seq, year, **kw)


def _10q(company, fy, q, period, filed, seq, **kw) -> Filing:
    return Filing(company, "10-Q", period, fy, f"Q{q}", filed, seq, YEARS[(company, fy)], **kw)


D = date
FILINGS: list[Filing] = [
    # ALDER -------------------------------------------------------------------
    _10k("ALDER", 2022, D(2022, 12, 31), D(2023, 2, 24), 1),
    _10q("ALDER", 2023, 1, D(2023, 3, 31), D(2023, 5, 5), 2),
    _10q("ALDER", 2023, 2, D(2023, 6, 30), D(2023, 8, 4), 3),
    _10q("ALDER", 2023, 3, D(2023, 9, 30), D(2023, 11, 3), 4),
    _10k(
        "ALDER", 2023, D(2023, 12, 31), D(2024, 2, 23), 1,
        # Also tags "Revenues" (incl. $2.5M other revenue). Priority must pick
        # the ASC 606 contract-revenue tag.
        duplicate_revenue_tag=("Revenues", 482_500_000),
        present_cash_on_cash_flow=True,
        comprehensive_income=True,
    ),
    _10q("ALDER", 2024, 1, D(2024, 3, 31), D(2024, 5, 3), 2),
    # BIRCH -------------------------------------------------------------------
    _10q("BIRCH", 2023, 1, D(2023, 3, 31), D(2023, 5, 9), 1),
    _10q("BIRCH", 2023, 2, D(2023, 6, 30), D(2023, 8, 8), 2),
    _10q("BIRCH", 2023, 3, D(2023, 9, 30), D(2023, 11, 7), 3),
    _10k("BIRCH", 2023, D(2023, 12, 31), D(2024, 2, 28), 1),
    _10q(
        "BIRCH", 2024, 1, D(2024, 3, 31), D(2024, 5, 8), 2,
        name=BIRCH_NEW_NAME, former_name="BIRCH DATA SYSTEMS INC", name_changed=D(2024, 4, 15),
    ),
    _10k(
        "BIRCH", 2023, D(2023, 12, 31), D(2024, 6, 14), 3, year=BIRCH_2023_RESTATED,
        form="10-K/A", name=BIRCH_NEW_NAME, former_name="BIRCH DATA SYSTEMS INC",
        name_changed=D(2024, 4, 15),
    ),
    # CEDAR -------------------------------------------------------------------
    _10q("CEDAR", 2023, 1, D(2023, 3, 31), D(2023, 5, 10), 1),
    _10q("CEDAR", 2023, 2, D(2023, 6, 30), D(2023, 8, 9), 2),
    _10q("CEDAR", 2023, 3, D(2023, 9, 30), D(2023, 11, 8), 3),
    _10k(
        "CEDAR", 2023, D(2023, 12, 31), D(2024, 2, 20), 1,
        prevrpt=True,  # amended later in the same quarter (by a Part III 10-K/A)
        extra_facts=(
            # Co-registrant (subsidiary) figures: must not replace the parent's.
            ExtraFact(DEFAULT_TAGS["revenue"], "141000000", coreg="CedarNetworksOperatingLlc"),
            ExtraFact(DEFAULT_TAGS["net_income"], "9800000", coreg="CedarNetworksOperatingLlc"),
            # Segment breakdown of revenue: must not be summed into the total.
            ExtraFact(DEFAULT_TAGS["revenue"], "90000000",
                      segments="ProductOrServiceAxis=HardwareMember;"),
            ExtraFact(DEFAULT_TAGS["revenue"], "60000000",
                      segments="ProductOrServiceAxis=SubscriptionMember;"),
        ),
    ),
    # Part III amendment: cover page and executive compensation only, no statements.
    Filing("CEDAR", "10-K/A", D(2023, 12, 31), 2023, "FY", D(2024, 3, 28), 2, None),
    # DOGWOOD ---------------------------------------------------------------
    _10k("DOGWOOD", 2022, D(2022, 12, 31), D(2023, 3, 15), 1),
    _10q("DOGWOOD", 2023, 1, D(2023, 3, 31), D(2023, 5, 12), 2),
    # (no Q2 10-Q in the data: derived Q4 must NOT be produced for FY2023)
    _10q("DOGWOOD", 2023, 3, D(2023, 9, 30), D(2023, 11, 13), 3),
    _10k("DOGWOOD", 2023, D(2023, 12, 31), D(2024, 3, 12), 1),
    # ELM ---------------------------------------------------------------------
    _10k("ELM", 2022, D(2022, 12, 31), D(2023, 3, 30), 1,
         tag_overrides={"revenue": "Revenues"}),
    _10q("ELM", 2023, 1, D(2023, 3, 31), D(2023, 5, 15), 2,
         tag_overrides={"revenue": "Revenues"}),
    _10q("ELM", 2023, 2, D(2023, 6, 30), D(2023, 8, 14), 3,
         tag_overrides={"revenue": "Revenues"}),
    _10q("ELM", 2023, 3, D(2023, 9, 30), D(2023, 11, 14), 4,
         tag_overrides={"revenue": "Revenues"}),
    _10k(
        "ELM", 2023, D(2023, 12, 31), D(2024, 4, 16), 1,  # late: lands in 2024q2
        tag_overrides={"revenue": "Revenues"},
        extra_facts=(
            # Filer-specific (custom) tag: excluded from cross-company marts.
            ExtraFact("SubscriptionAndServicesRevenue", "95000000", custom=True),
            # A malformed value (letter O for zero). Real SEC data should not
            # contain this; it demonstrates that staging nulls it instead of
            # failing the load, and that a warn-level test reports it.
            ExtraFact("OtherNonoperatingIncomeExpense", "12O5000"),
        ),
    ),
    _10q("ELM", 2024, 1, D(2024, 3, 31), D(2024, 5, 20), 2,
         tag_overrides={"revenue": "Revenues"}),
    # FIR (fiscal year ends June 30) -----------------------------------------
    _10q("FIR", 2023, 2, D(2022, 12, 31), D(2023, 2, 6), 1),
    _10q("FIR", 2023, 3, D(2023, 3, 31), D(2023, 5, 5), 2),
    _10k("FIR", 2023, D(2023, 6, 30), D(2023, 8, 25), 3),
    _10q("FIR", 2024, 1, D(2023, 9, 30), D(2023, 11, 6), 4),
    _10q("FIR", 2024, 2, D(2023, 12, 31), D(2024, 2, 5), 1),
    _10q("FIR", 2024, 3, D(2024, 3, 31), D(2024, 5, 6), 2),
    # GROVE (bank) ------------------------------------------------------------
    _10k("GROVE", 2022, D(2022, 12, 31), D(2023, 3, 1), 1, tag_overrides={"revenue": "Revenues"}),
    _10k("GROVE", 2023, D(2023, 12, 31), D(2024, 2, 29), 1, tag_overrides={"revenue": "Revenues"}),
    # HAZEL -------------------------------------------------------------------
    _10k("HAZEL", 2022, D(2022, 12, 31), D(2023, 3, 20), 1),
    _10k("HAZEL", 2023, D(2023, 12, 31), D(2024, 3, 18), 1),
    # IRONWOOD ----------------------------------------------------------------
    _10k("IRONWOOD", 2022, D(2022, 12, 31), D(2023, 2, 27), 1,
         tag_overrides={"revenue": "SalesRevenueNet"}),
    _10k("IRONWOOD", 2023, D(2023, 12, 31), D(2024, 2, 26), 1),
    # JUNIPER (20-F, out of scope) ------------------------------------------
    Filing("JUNIPER", "20-F", D(2023, 12, 31), 2023, "FY", D(2024, 4, 22), 1,
           YEARS[("JUNIPER", 2023)]),
]  # fmt: skip


# --------------------------------------------------------------------------- #
# What each filing reports
# --------------------------------------------------------------------------- #


def fiscal_year_end(company: str, fiscal_year: int) -> date:
    """Every sample company labels its fiscal year by the calendar year it ends in."""
    return month_end(fiscal_year, int(COMPANIES[company].fye[:2]))


def prior_year_balance(company: str, fiscal_year: int, current: Balance) -> Balance:
    prior = YEARS.get((company, fiscal_year - 1))
    return prior.balance if prior else current.scaled(0.9)


def current_period_values(filing: Filing) -> dict[int, dict[str, float]]:
    """Values a filing reports for its own period, keyed by duration in quarters
    (0 = balance sheet instant). This is what the marts must reproduce."""
    if filing.year is None:
        return {}
    year = filing.year
    out: dict[int, dict[str, float]] = {}
    if filing.fiscal_period == "FY":
        out[4] = year.income.statement()
        out[0] = year.balance.statement()
        if year.cash_flow:
            out[4].update(year.cash_flow.statement())
        return out

    q = filing.quarter_number
    assert q is not None
    out[1] = year.income.statement(QUARTER_SHARE[q])
    if q > 1:  # year-to-date income statement and cash flow
        out[q] = _year_to_date_income(year.income, q)
    cumulative = sum(QUARTER_SHARE[i] for i in range(1, q + 1))
    if year.cash_flow:
        out.setdefault(q, {}).update(year.cash_flow.statement(cumulative))
    opening = prior_year_balance(filing.company, filing.fiscal_year, year.balance)
    out[0] = opening.interpolate(year.balance, q / 4).statement()
    return out


def _year_to_date_income(income: Income, quarter: int) -> dict[str, float]:
    quarters = [income.statement(QUARTER_SHARE[i]) for i in range(1, quarter + 1)]
    totals: dict[str, float] = {}
    for concept in quarters[0]:
        if concept in PER_SHARE:
            continue
        totals[concept] = sum(q[concept] for q in quarters)
    return totals


def comparative_values(filing: Filing) -> list[tuple[date, int, dict[str, float]]]:
    """Prior-period comparative columns (ddate, qtrs, values). Recast by 1% so a
    leak into current-period rows is detectable."""
    if filing.year is None:
        return []

    def recast(values: dict[str, float]) -> dict[str, float]:
        return {
            c: (round(v * COMPARATIVE_RECAST, 2) if c in PER_SHARE else k(v * COMPARATIVE_RECAST))
            for c, v in values.items()
        }

    prior_fye = years_before(fiscal_year_end(filing.company, filing.fiscal_year))
    prior_balance = prior_year_balance(filing.company, filing.fiscal_year, filing.year.balance)
    comparatives = [(prior_fye, 0, recast(prior_balance.statement()))]
    prior_year = YEARS.get((filing.company, filing.fiscal_year - 1))
    if filing.fiscal_period == "FY":
        income = prior_year.income if prior_year else filing.year.income
        flows = income.statement(1.0 if prior_year else 0.9)
        if filing.year.cash_flow:
            cash = prior_year.cash_flow if prior_year and prior_year.cash_flow else None
            flows.update((cash or filing.year.cash_flow).statement(1.0 if cash else 0.9))
        comparatives.append((prior_fye, 4, recast(flows)))
    else:
        q = filing.quarter_number
        comparatives.append(
            (years_before(filing.period), 1,
             recast(filing.year.income.statement(QUARTER_SHARE[q] * 0.9)))
        )  # fmt: skip
    return comparatives


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #

STATEMENT_OF = {
    **{c: "IS" for c in IS_CONCEPTS},
    **{c: "BS" for c in BS_CONCEPTS},
    **{c: "CF" for c in CF_CONCEPTS},
}
REPORT_NUMBER = {"BS": 2, "IS": 4, "CI": 5, "CF": 7}
ABSTRACT_HEADERS = {
    "BS": "StatementOfFinancialPositionAbstract",
    "IS": "IncomeStatementAbstract",
    "CF": "StatementOfCashFlowsAbstract",
}


def _fmt_value(concept: str, value: float) -> str:
    return f"{value:.2f}" if concept in PER_SHARE else str(int(value))


def _tag_for(filing: Filing, concept: str) -> str:
    return filing.tag_overrides.get(concept, DEFAULT_TAGS[concept])


def filing_rows(filing: Filing) -> tuple[list[dict], list[dict], set[tuple]]:
    """Return (num rows, pre rows, tag keys) for one filing."""
    num: list[dict] = []
    presented: dict[str, list[tuple[str, str]]] = {"BS": [], "IS": [], "CF": [], "CI": []}
    tags: set[tuple] = set()  # (tag, version, concept-or-None, custom, abstract)

    def add(tag, version, ddate, qtrs, uom, value, segments=None, coreg=None):
        num.append({
            "adsh": filing.adsh, "tag": tag, "version": version,
            "ddate": ddate.strftime("%Y%m%d"), "qtrs": str(qtrs), "uom": uom,
            "segments": segments, "coreg": coreg, "value": value, "footnote": None,
        })  # fmt: skip

    def present(statement, tag, version):
        if (tag, version) not in presented[statement]:
            presented[statement].append((tag, version))

    periods = [(filing.period, qtrs, values) for qtrs, values in current_period_values(filing).items()]
    periods += comparative_values(filing)
    for ddate, qtrs, values in periods:
        for concept, value in values.items():
            tag = _tag_for(filing, concept)
            uom = "USD/shares" if concept in PER_SHARE else "USD"
            add(tag, filing.taxonomy, ddate, qtrs, uom, _fmt_value(concept, value))
            present(STATEMENT_OF[concept], tag, filing.taxonomy)
            tags.add((tag, filing.taxonomy, concept, False, False))

    if filing.year is not None:
        # Net income is also the first line of the cash flow statement; the
        # model's statement priority must still classify it as income statement.
        present("CF", _tag_for(filing, "net_income"), filing.taxonomy)

    if filing.duplicate_revenue_tag:
        tag, value = filing.duplicate_revenue_tag
        add(tag, filing.taxonomy, filing.period, 4, "USD", str(value))
        present("IS", tag, filing.taxonomy)
        tags.add((tag, filing.taxonomy, "revenue", False, False))

    if filing.present_cash_on_cash_flow:
        present("CF", DEFAULT_TAGS["cash_and_equivalents"], filing.taxonomy)

    if filing.comprehensive_income:
        income = current_period_values(filing)[4]
        tag = "ComprehensiveIncomeNetOfTax"
        add(tag, filing.taxonomy, filing.period, 4, "USD", str(int(income["net_income"] + 1_500_000)))
        present("CI", _tag_for(filing, "net_income"), filing.taxonomy)
        present("CI", tag, filing.taxonomy)
        tags.add((tag, filing.taxonomy, None, False, False))

    for extra in filing.extra_facts:
        version = filing.adsh if extra.custom else filing.taxonomy
        add(extra.tag, version, filing.period, extra.qtrs, extra.uom, extra.value,
            segments=extra.segments, coreg=extra.coreg)  # fmt: skip
        if extra.presented and not extra.segments and not extra.coreg:
            present(extra.statement, extra.tag, version)
        tags.add((extra.tag, version, None, extra.custom, False))

    pre: list[dict] = []
    for statement, items in presented.items():
        if not items:
            continue
        report = REPORT_NUMBER[statement]
        line = 1
        if statement in ABSTRACT_HEADERS:
            header = ABSTRACT_HEADERS[statement]
            pre.append(_pre_row(filing, report, line, statement, header, False))
            tags.add((header, filing.taxonomy, None, False, True))
            line += 1
        for tag, version in items:
            pre.append(_pre_row(filing, report, line, statement, tag, False, version))
            line += 1
        if statement == "BS":
            # A parenthetical line ("net of allowance of $x"): not a line item.
            tag = "AllowanceForDoubtfulAccountsReceivableCurrent"
            pre.append(_pre_row(filing, report + 1, 1, statement, tag, True))
            add(tag, filing.taxonomy, filing.period, 0, "USD", "1000000")
            tags.add((tag, filing.taxonomy, None, False, False))
    return num, pre, tags


def _pre_row(filing, report, line, stmt, tag, parenthetical, version=None) -> dict:
    return {
        "adsh": filing.adsh, "report": str(report), "line": str(line), "stmt": stmt,
        "inpth": "1" if parenthetical else "0", "rfile": "H", "tag": tag,
        "version": version or filing.taxonomy, "plabel": humanize(tag), "negating": "0",
    }  # fmt: skip


def sub_row(filing: Filing) -> dict:
    company = COMPANIES[filing.company]
    accepted = f"{filing.filed:%Y-%m-%d} 16:05:00.0"
    return {
        "adsh": filing.adsh, "cik": str(company.cik), "name": filing.name or company.name,
        "sic": str(company.sic), "countryba": company.country, "stprba": company.state,
        "cityba": company.city, "zipba": None, "bas1": "100 SAMPLE STREET", "bas2": None,
        "baph": "555-0100", "countryma": company.country, "stprma": company.state,
        "cityma": company.city, "zipma": None, "mas1": "100 SAMPLE STREET", "mas2": None,
        "countryinc": company.country_inc, "stprinc": company.state_inc,
        "ein": str(990000000 + company.cik % 1000), "former": filing.former_name,
        "changed": filing.name_changed.strftime("%Y%m%d") if filing.name_changed else None,
        "afs": "2-ACC", "wksi": "0", "fye": company.fye, "form": filing.form,
        "period": filing.period.strftime("%Y%m%d"), "fy": str(filing.fiscal_year),
        "fp": filing.fiscal_period, "filed": filing.filed.strftime("%Y%m%d"),
        "accepted": accepted, "prevrpt": "1" if filing.prevrpt else "0", "detail": "1",
        "instance": f"{filing.company.lower()}-{filing.period:%Y%m%d}_htm.xml",
        "nciks": str(company.nciks), "aciks": company.aciks,
    }  # fmt: skip


def tag_row(tag: str, version: str, concept: str | None, custom: bool, abstract: bool) -> dict:
    if abstract:
        datatype = iord = crdr = None
    else:
        statement = STATEMENT_OF.get(concept or "", "IS")
        iord = "I" if statement == "BS" or tag.startswith("Allowance") else "D"
        datatype = "perShare" if concept in PER_SHARE else "monetary"
        crdr = "C" if concept in CREDIT_CONCEPTS else "D"
    return {
        "tag": tag, "version": version, "custom": "1" if custom else "0",
        "abstract": "1" if abstract else "0", "datatype": datatype, "iord": iord,
        "crdr": crdr, "tlabel": humanize(tag),
        "doc": "Synthetic sample tag (fictional data, generated by ingestion/sample_data.py).",
    }  # fmt: skip


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def build_dataset() -> dict[str, dict[str, list[dict]]]:
    """Rows per quarter per file: {"2024q1": {"sub.txt": [...], ...}}."""
    datasets: dict[str, dict[str, list[dict]]] = {}
    tag_keys: dict[str, dict[tuple, tuple]] = {}
    for filing in sorted(FILINGS, key=lambda f: (f.filed, f.adsh)):
        files = datasets.setdefault(
            filing.dataset, {SUB.file_name: [], NUM.file_name: [], TAG.file_name: [], PRE.file_name: []}
        )
        files[SUB.file_name].append(sub_row(filing))
        num, pre, tags = filing_rows(filing)
        files[NUM.file_name].extend(num)
        files[PRE.file_name].extend(pre)
        quarter_tags = tag_keys.setdefault(filing.dataset, {})
        for key in tags:
            quarter_tags.setdefault(key[:2], key)
    for quarter, files in datasets.items():
        files[TAG.file_name] = [tag_row(*key) for _, key in sorted(tag_keys[quarter].items())]
    return dict(sorted(datasets.items()))


def render(rows: list[dict], columns: tuple[str, ...]) -> str:
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join("" if row.get(c) is None else str(row[c]) for c in columns))
    return "\n".join(lines) + "\n"


def write_dataset(out_dir: Path) -> dict[Path, str]:
    """Render every file; return {path: content} without touching the disk."""
    layouts = {SUB.file_name: SUB, NUM.file_name: NUM, TAG.file_name: TAG, PRE.file_name: PRE}
    rendered = {}
    for quarter, files in build_dataset().items():
        for file_name, rows in files.items():
            rendered[out_dir / quarter / file_name] = render(rows, layouts[file_name].columns)
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="Fail if files on disk are stale")
    args = parser.parse_args(argv)

    rendered = write_dataset(args.out)
    if args.check:
        stale = [
            str(path) for path, content in rendered.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]  # fmt: skip
        if stale:
            print("Sample data is out of date; run `make sample-data`:", *stale, sep="\n  ")
            return 1
        print(f"Sample data is up to date ({len(rendered)} files).")
        return 0

    for path, content in rendered.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {len(rendered)} files under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
