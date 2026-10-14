/*
    Fastest revenue growth by fiscal year, restricted to years the data-quality
    mart marks as analysis-ready. Compiled, never materialized.
*/

select
    growth.fiscal_year,
    growth.company_name,
    growth.sector,
    growth.prior_revenue,
    growth.revenue,
    round(growth.revenue_growth_rate, 4) as revenue_growth_rate,
    round(growth.net_income_growth_rate, 4) as net_income_growth_rate
from {{ ref('fct_yoy_growth') }} as growth
inner join {{ ref('mart_data_quality_summary') }} as quality
    on growth.fiscal_year = quality.fiscal_year
where
    quality.is_analysis_ready
    and growth.revenue_growth_rate is not null
order by growth.fiscal_year desc, growth.revenue_growth_rate desc
