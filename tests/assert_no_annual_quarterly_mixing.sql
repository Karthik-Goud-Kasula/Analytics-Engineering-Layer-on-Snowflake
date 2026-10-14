/*
    Annual revenue can never be smaller than a single quarter of the same
    fiscal year. If it is, durations have been mixed somewhere: a quarterly
    value landed in the annual table, or an annual one in a quarter.

    Derived Q4 rows are excluded: they are computed FROM the annual figure, so
    comparing them to it proves nothing (and a restatement between the 10-Qs
    and the 10-K can legitimately push a derived Q4 negative).
*/

select
    annual.cik,
    annual.fiscal_year,
    annual.revenue as annual_revenue,
    quarterly.fiscal_quarter,
    quarterly.revenue as quarterly_revenue
from {{ ref('fct_company_financials_annual') }} as annual
inner join {{ ref('fct_company_financials_quarterly') }} as quarterly
    on
        annual.cik = quarterly.cik
        and annual.fiscal_year = quarterly.fiscal_year
where
    not quarterly.is_derived_q4
    and quarterly.revenue > 0
    and annual.revenue < quarterly.revenue
