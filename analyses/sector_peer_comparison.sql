/*
    How does each company compare with its sector's median, by year?
    Compiled by `dbt compile` (target/compiled/...), never materialized.

    Only sector-years that met the minimum company count appear in
    mart_sector_benchmarks, so the inner join drops thin sectors on purpose.
*/

select
    ratios.fiscal_year,
    ratios.sector,
    ratios.company_name,
    round(ratios.operating_margin, 3) as operating_margin,
    round(benchmarks.median_operating_margin, 3) as sector_median_operating_margin,
    round(ratios.operating_margin - benchmarks.median_operating_margin, 3) as vs_median,
    case
        when ratios.operating_margin >= benchmarks.p75_operating_margin then 'top quartile'
        when ratios.operating_margin <= benchmarks.p25_operating_margin then 'bottom quartile'
        else 'middle half'
    end as operating_margin_band
from {{ ref('fct_financial_ratios') }} as ratios
inner join {{ ref('mart_sector_benchmarks') }} as benchmarks
    on
        ratios.sector = benchmarks.sector
        and ratios.fiscal_year = benchmarks.fiscal_year
order by ratios.fiscal_year asc, ratios.sector asc, vs_median desc
