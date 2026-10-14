/*
    Standard financial ratios, one row per company-year.

    Every division goes through safe_divide(), which returns NULL rather than
    erroring or producing infinity when the denominator is zero or null. That
    matters more than it sounds: a company with zero equity is not "infinitely
    leveraged", it is a company whose leverage ratio is undefined, and a NULL
    says that honestly where a large number would lie.

    Ratios are also NULLed where the inputs are negative in a way that makes the
    result meaningless -- negative equity makes ROE uninterpretable, not just
    negative.

    Balance-sheet denominators use the fiscal-year-end balance, not an average
    of opening and closing balances: a deliberate simplification, since the
    opening balance needs the prior year's filing, which is not always loaded.

    Ratios are fractions: 0.25 means 25%.
*/

with financials as (

    select * from {{ ref('fct_company_financials_annual') }}

)

select
    annual_financials_key,
    company_key,
    cik,
    company_name,
    sector,
    industry_group,
    fiscal_year,
    fiscal_year_end_date,

    -- profitability
    {{ safe_divide('gross_profit', 'revenue') }} as gross_margin,
    {{ safe_divide('operating_income', 'revenue') }} as operating_margin,
    {{ safe_divide('net_income', 'revenue') }} as net_margin,
    {{ safe_divide('free_cash_flow', 'revenue') }} as fcf_margin,
    {{ safe_divide('research_and_development', 'revenue') }} as rd_intensity,
    {{ safe_divide('sga_expense', 'revenue') }} as sga_ratio,

    -- returns. Guarded because negative denominators invert the sign and make
    -- a distressed company look highly profitable.
    case
        when total_equity > 0 then {{ safe_divide('net_income', 'total_equity') }}
    end as return_on_equity,
    case
        when total_assets > 0 then {{ safe_divide('net_income', 'total_assets') }}
    end as return_on_assets,
    -- NULL when no debt is tagged: "no debt reported" is not proven to mean
    -- "no debt" (the mapping may not cover a filer's debt tags), and this
    -- matches debt_to_equity below.
    case
        when (total_debt + total_equity) > 0
            then {{ safe_divide('operating_income', 'total_debt + total_equity') }}
    end as return_on_capital,

    -- liquidity
    {{ safe_divide('total_current_assets', 'total_current_liabilities') }} as current_ratio,
    {{ safe_divide(
        'total_current_assets - coalesce(inventory, 0)',
        'total_current_liabilities'
    ) }} as quick_ratio,
    {{ safe_divide(
        'coalesce(cash_and_equivalents, 0) + coalesce(short_term_investments, 0)',
        'total_current_liabilities'
    ) }} as cash_ratio,

    -- leverage
    case
        when total_equity > 0 then {{ safe_divide('total_debt', 'total_equity') }}
    end as debt_to_equity,
    {{ safe_divide('total_debt', 'total_assets') }} as debt_to_assets,
    case
        when interest_expense > 0
            then {{ safe_divide('operating_income', 'interest_expense') }}
    end as interest_coverage,

    -- efficiency
    {{ safe_divide('revenue', 'total_assets') }} as asset_turnover,
    {{ safe_divide('revenue', 'accounts_receivable') }} as receivables_turnover,
    case
        when inventory > 0 then {{ safe_divide('cost_of_revenue', 'inventory') }}
    end as inventory_turnover,

    -- cash conversion. Guarded like ROE: operating cash flow over a net LOSS
    -- produces a negative ratio for a company generating cash, and a positive
    -- one for a company burning cash -- the sign means the opposite of what it
    -- appears to mean.
    case
        when net_income > 0 then {{ safe_divide('operating_cash_flow', 'net_income') }}
    end as cash_conversion_ratio,
    {{ safe_divide('capital_expenditure', 'revenue') }} as capex_intensity,

    -- context for filtering
    revenue,
    net_income,
    total_assets,
    total_equity,
    has_balance_sheet,
    has_cash_flow

from financials
where revenue is not null
