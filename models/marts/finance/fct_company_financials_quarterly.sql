/*
    Quarterly income statement, one row per company per fiscal quarter, with
    year-over-year growth against the same quarter of the prior fiscal year.

    Grain: one row per (cik, fiscal_year, fiscal_quarter).

    Q4 is DERIVED (annual minus Q1-Q3), not reported. Filter on is_derived_q4
    and has_complete_quarters before drawing conclusions from a trend.
    Cash flow is not included: 10-Q cash flow statements are year-to-date only.
*/

with quarterly as (

    select * from {{ ref('int_financials__quarterly') }}

),

companies as (

    select
        company_key,
        cik,
        company_name
    from {{ ref('dim_company') }}

)

select
    quarterly.quarterly_financials_key,
    companies.company_key,
    quarterly.company_id as cik,
    companies.company_name,
    quarterly.fiscal_year,
    quarterly.fiscal_quarter,
    quarterly.period_end_date,
    quarterly.source_filing_id,
    quarterly.revenue,
    quarterly.cost_of_revenue,
    quarterly.gross_profit,
    quarterly.operating_income,
    quarterly.net_income,
    quarterly.eps_basic,
    quarterly.eps_diluted,
    quarterly.is_derived_q4,
    quarterly.has_complete_quarters,
    prior_year.revenue as prior_year_revenue,
    case
        when prior_year.revenue > 0
            then {{ safe_divide('quarterly.revenue - prior_year.revenue', 'prior_year.revenue') }}
    end as revenue_yoy_growth_rate
from quarterly
inner join companies
    on quarterly.company_id = companies.cik
left join quarterly as prior_year
    on
        quarterly.company_id = prior_year.company_id
        and prior_year.fiscal_year = quarterly.fiscal_year - 1
        and quarterly.fiscal_quarter = prior_year.fiscal_quarter
