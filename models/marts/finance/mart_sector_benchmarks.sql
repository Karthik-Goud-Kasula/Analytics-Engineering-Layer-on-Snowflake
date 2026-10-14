/*
    Sector benchmarks: the median and interquartile range of key ratios for
    each sector and fiscal year.

    Grain: one row per (sector, fiscal_year).

    Medians, not means: financial ratios are heavily right-skewed, and one
    company with a near-zero equity base drags a mean somewhere useless. An
    analyst asking "is a 22% operating margin good for software?" wants the
    median and the 25th-75th percentile range.

    Sector-years with fewer than var('min_companies_per_benchmark') companies
    are excluded entirely: a "median" over three companies is not a benchmark,
    and publishing it invites misuse.

    Percentiles use percentile_cont (linear interpolation); NULL ratios are
    ignored, so each metric's percentiles may cover fewer companies than
    company_count.
*/

{% set metrics = [
    'gross_margin', 'operating_margin', 'net_margin', 'return_on_equity',
    'current_ratio', 'debt_to_equity'
] %}

with ratios as (

    select * from {{ ref('fct_financial_ratios') }}

),

eligible as (

    select
        sector,
        fiscal_year,
        count(distinct cik) as company_count
    from ratios
    group by sector, fiscal_year
    having count(distinct cik) >= {{ var('min_companies_per_benchmark') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['ratios.sector', 'ratios.fiscal_year']) }}
        as sector_benchmark_key,
    ratios.sector,
    ratios.fiscal_year,
    eligible.company_count,
    {% for metric in metrics %}
    median(ratios.{{ metric }}) as median_{{ metric }},
    percentile_cont(0.25) within group (order by ratios.{{ metric }}) as p25_{{ metric }},
    percentile_cont(0.75) within group (order by ratios.{{ metric }}) as p75_{{ metric }},
    {% endfor %}
    median(ratios.revenue) as median_revenue
from ratios
inner join eligible
    on
        ratios.sector = eligible.sector
        and ratios.fiscal_year = eligible.fiscal_year
group by
    ratios.sector,
    ratios.fiscal_year,
    eligible.company_count
