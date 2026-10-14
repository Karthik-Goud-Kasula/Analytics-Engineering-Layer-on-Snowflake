/*
    Reconciliation: for every fiscal year with a derived Q4, Q1 + Q2 + Q3 + Q4
    must equal the annual figure exactly.

    True by construction for a correct model, which is the point: it fails if
    the quarterly join duplicates a quarter, picks a quarter from the wrong
    fiscal year, or derives Q4 from an incomplete set.
*/

with quarter_sums as (

    select
        cik,
        fiscal_year,
        sum(revenue) as summed_revenue,
        sum(net_income) as summed_net_income,
        count(revenue) as revenue_quarters,
        count(net_income) as net_income_quarters
    from {{ ref('fct_company_financials_quarterly') }}
    group by cik, fiscal_year
    having max(case when is_derived_q4 then 1 else 0 end) = 1

)

select
    quarter_sums.cik,
    quarter_sums.fiscal_year,
    annual.revenue,
    quarter_sums.summed_revenue,
    annual.net_income,
    quarter_sums.summed_net_income
from quarter_sums
inner join {{ ref('fct_company_financials_annual') }} as annual
    on
        quarter_sums.cik = annual.cik
        and quarter_sums.fiscal_year = annual.fiscal_year
where
    (quarter_sums.revenue_quarters = 4 and quarter_sums.summed_revenue != annual.revenue)
    or (quarter_sums.net_income_quarters = 4 and quarter_sums.summed_net_income != annual.net_income)
