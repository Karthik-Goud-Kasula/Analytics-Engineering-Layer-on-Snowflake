/*
    Income statement, balance sheet and cash flow joined into one row per
    company and fiscal year, from each year's authoritative 10-K (or 10-K/A).

    Grain: one row per (company_id, fiscal_year).

    All three statements come from the same filing, joined on filing_id. The
    duration filter on each side (4 quarters for flows, the single balance
    sheet per filing) is what prevents fan-out: joining on company and period
    alone would multiply each year by the number of durations present.

    If a company files two 10-Ks labelled with the same fiscal year (a change
    of fiscal year end), the later period wins so the grain holds.
*/

with annual_filings as (

    select *
    from {{ ref('int_filings__deduplicated') }}
    where base_form_type = '10-K'

),

income as (

    select *
    from {{ ref('int_facts__income_statement') }}
    where duration_quarters = 4

),

balance as (

    select * from {{ ref('int_facts__balance_sheet') }}

),

cash_flow as (

    select *
    from {{ ref('int_facts__cash_flow') }}
    where duration_quarters = 4

),

joined as (

    select
        annual_filings.company_id,
        annual_filings.fiscal_year,
        annual_filings.period_end_date as fiscal_year_end_date,
        annual_filings.filing_id as source_filing_id,
        annual_filings.form_type as source_form_type,
        annual_filings.is_amendment as is_from_amendment,
        annual_filings.filed_date,
        annual_filings.accepted_at,

        income.revenue,
        income.revenue_tag,
        income.cost_of_revenue,
        income.gross_profit,
        income.operating_expenses,
        income.research_and_development,
        income.sga_expense,
        income.operating_income,
        income.interest_expense,
        income.pretax_income,
        income.income_tax_expense,
        income.net_income,
        income.net_income_tag,
        income.eps_basic,
        income.eps_diluted,

        balance.cash_and_equivalents,
        balance.short_term_investments,
        balance.accounts_receivable,
        balance.inventory,
        balance.total_current_assets,
        balance.total_assets,
        balance.total_current_liabilities,
        balance.total_liabilities,
        balance.total_debt,
        balance.total_equity,

        cash_flow.operating_cash_flow,
        cash_flow.investing_cash_flow,
        cash_flow.financing_cash_flow,
        cash_flow.capital_expenditure,
        cash_flow.free_cash_flow,
        cash_flow.dividends_paid,
        cash_flow.share_repurchases,

        income.filing_id is not null as has_income_statement,
        balance.filing_id is not null as has_balance_sheet,
        cash_flow.filing_id is not null as has_cash_flow

    from annual_filings
    left join income
        on annual_filings.filing_id = income.filing_id
    left join balance
        on annual_filings.filing_id = balance.filing_id
    left join cash_flow
        on annual_filings.filing_id = cash_flow.filing_id

)

select * from joined
qualify row_number() over (
    partition by company_id, fiscal_year
    order by fiscal_year_end_date desc, filed_date desc, source_filing_id desc
) = 1
