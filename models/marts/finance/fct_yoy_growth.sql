/*
    Year-over-year growth, one row per company-year.

    Grain: one row per (cik, fiscal_year).

    The prior year is found with a self-join on fiscal_year - 1, not lag():
    lag() returns the previous ROW, so a company with a missing year would get
    a two-year change labelled as one-year growth.

    Growth is a fraction (0.17 = 17%) and is NULL when the prior-year base is
    zero or negative: growth "from -$5M to $10M" has no meaningful percentage.
*/

with annual as (

    select
        annual_financials_key,
        company_key,
        cik,
        company_name,
        sector,
        fiscal_year,
        revenue,
        operating_income,
        net_income,
        operating_cash_flow,
        total_assets
    from {{ ref('fct_company_financials_annual') }}

)

select
    current_year.annual_financials_key,
    current_year.company_key,
    current_year.cik,
    current_year.company_name,
    current_year.sector,
    current_year.fiscal_year,
    prior_year.fiscal_year is not null as has_prior_year,

    current_year.revenue,
    prior_year.revenue as prior_revenue,
    case
        when prior_year.revenue > 0
            then {{ safe_divide('current_year.revenue - prior_year.revenue', 'prior_year.revenue') }}
    end as revenue_growth_rate,

    current_year.operating_income,
    prior_year.operating_income as prior_operating_income,
    case
        when prior_year.operating_income > 0
            then {{ safe_divide(
                'current_year.operating_income - prior_year.operating_income',
                'prior_year.operating_income'
            ) }}
    end as operating_income_growth_rate,

    current_year.net_income,
    prior_year.net_income as prior_net_income,
    case
        when prior_year.net_income > 0
            then {{ safe_divide('current_year.net_income - prior_year.net_income', 'prior_year.net_income') }}
    end as net_income_growth_rate,

    current_year.operating_cash_flow,
    prior_year.operating_cash_flow as prior_operating_cash_flow,
    case
        when prior_year.operating_cash_flow > 0
            then {{ safe_divide(
                'current_year.operating_cash_flow - prior_year.operating_cash_flow',
                'prior_year.operating_cash_flow'
            ) }}
    end as operating_cash_flow_growth_rate,

    current_year.total_assets,
    prior_year.total_assets as prior_total_assets,
    case
        when prior_year.total_assets > 0
            then {{ safe_divide('current_year.total_assets - prior_year.total_assets', 'prior_year.total_assets') }}
    end as total_assets_growth_rate

from annual as current_year
left join annual as prior_year
    on
        current_year.cik = prior_year.cik
        and prior_year.fiscal_year = current_year.fiscal_year - 1
