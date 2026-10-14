/*
    The primary analytical table: one row per company per fiscal year, with
    income statement, balance sheet and cash flow side by side, as reported in
    that year's authoritative 10-K (including any amendment).

    Grain: one row per (cik, fiscal_year).

    Sector and industry group are POINT-IN-TIME: taken from the
    dim_company_history version that was current when the 10-K was accepted,
    so a company reclassified in 2025 keeps its 2023 numbers in its 2023
    sector. The name is the company's current name, for readability.

    NULL means "not reported", never zero. has_* flags distinguish "the
    statement was missing" from "the statement was there but the line was not".
*/

with annual as (

    select * from {{ ref('int_financials__annual') }}

),

companies as (

    select
        company_key,
        cik,
        company_name
    from {{ ref('dim_company') }}

),

history as (

    select
        company_history_key,
        cik,
        sector,
        industry_group,
        valid_from,
        valid_to
    from {{ ref('dim_company_history') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['annual.company_id', 'annual.fiscal_year']) }}
        as annual_financials_key,
    companies.company_key,
    history.company_history_key,
    {{ dbt_utils.generate_surrogate_key(['annual.source_filing_id']) }} as filing_key,
    annual.company_id as cik,
    companies.company_name,
    history.sector,
    history.industry_group,
    annual.fiscal_year,
    annual.fiscal_year_end_date,
    annual.filed_date,
    annual.source_filing_id,
    annual.source_form_type,
    annual.is_from_amendment,

    -- income statement
    annual.revenue,
    annual.revenue_tag,
    annual.cost_of_revenue,
    annual.gross_profit,
    annual.operating_expenses,
    annual.research_and_development,
    annual.sga_expense,
    annual.operating_income,
    annual.interest_expense,
    annual.pretax_income,
    annual.income_tax_expense,
    annual.net_income,
    annual.net_income_tag,
    annual.eps_basic,
    annual.eps_diluted,

    -- balance sheet (fiscal year end)
    annual.cash_and_equivalents,
    annual.short_term_investments,
    annual.accounts_receivable,
    annual.inventory,
    annual.total_current_assets,
    annual.total_assets,
    annual.total_current_liabilities,
    annual.total_liabilities,
    annual.total_debt,
    annual.total_equity,

    -- cash flow
    annual.operating_cash_flow,
    annual.investing_cash_flow,
    annual.financing_cash_flow,
    annual.capital_expenditure,
    annual.free_cash_flow,
    annual.dividends_paid,
    annual.share_repurchases,

    -- completeness
    annual.revenue is not null as has_revenue,
    annual.has_income_statement,
    annual.has_balance_sheet,
    annual.has_cash_flow,
    -- Assets = Liabilities + Equity, within a tolerance relative to assets.
    -- NULL when not checkable (a component is not reported).
    case
        when
            annual.total_assets is not null
            and annual.total_liabilities is not null
            and annual.total_equity is not null
            then
                abs(annual.total_assets - (annual.total_liabilities + annual.total_equity))
                <= abs(annual.total_assets) * {{ var('balance_sheet_tolerance_pct') }}
    end as balance_sheet_balances

from annual
inner join companies
    on annual.company_id = companies.cik
left join history
    on
        annual.company_id = history.cik
        and annual.accepted_at >= history.valid_from
        and (annual.accepted_at < history.valid_to or history.valid_to is null)
