/*
    Material restatements, largest net-income change first, with how long after
    the original filing the correction came. Compiled, never materialized.
*/

select
    company_name,
    fiscal_year,
    fiscal_period,
    amended_form_type,
    original_net_income,
    restated_net_income,
    round(net_income_change_rate, 4) as net_income_change_rate,
    round(revenue_change_rate, 4) as revenue_change_rate,
    days_until_restatement
from {{ ref('mart_restatements') }}
where is_material_restatement
order by abs(net_income_change_rate) desc
