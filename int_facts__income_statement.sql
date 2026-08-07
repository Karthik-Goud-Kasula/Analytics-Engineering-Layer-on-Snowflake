{{
    config(
        materialized='table'
    )
}}

/*
    Income statement line items, pivoted from tag rows into named columns.

    Two hard problems this model solves:

    1. TAG SYNONYMS. There is no single revenue tag. Depending on filer and
       taxonomy year, top-line revenue is `Revenues`,
       `RevenueFromContractWithCustomerExcludingAssessedTax`,
       `SalesRevenueNet`, `SalesRevenueGoodsNet`, and several others. The seed
       `financial_tag_mapping.csv` maps every known variant to a canonical
       concept, with a priority so the most specific tag wins when a filer
       reports more than one.

    2. DURATION MIXING. `qtrs = 4` is an annual figure and `qtrs = 1` is a
       single quarter. Summing across them inflates results ~5x. This model
       keeps them separate by carrying `duration_quarters` in the grain.

    Grain: one row per (company, period_end_date, duration_quarters).
*/

with facts as (

    select *
    from {{ ref('int_facts__classified') }}
    where statement_name in ('income_statement', 'comprehensive_income')
      and unit_of_measure = 'USD'
      and duration_quarters > 0      -- income statement items are always flows
      and not is_custom_tag          -- cross-company comparability

),

mapping as (

    select
        tag,
        concept,
        priority
    from {{ ref('financial_tag_mapping') }}
    where statement = 'income_statement'

),

mapped as (

    select
        f.company_id,
        f.filing_id,
        f.period_end_date,
        f.duration_quarters,
        f.period_type,
        m.concept,
        f.fact_value,
        m.priority,
        -- When a filer reports two tags that both map to the same concept,
        -- take the highest-priority (most specific) one, then the most recent
        -- filing as a tiebreak.
        row_number() over (
            partition by f.company_id, f.period_end_date, f.duration_quarters, m.concept
            order by m.priority asc, f.filed_date desc, abs(f.fact_value) desc
        ) as concept_rank
    from facts f
    inner join mapping m
        on f.tag = m.tag

),

deduped as (

    select * from mapped where concept_rank = 1

),

pivoted as (

    select
        company_id,
        period_end_date,
        duration_quarters,
        max(period_type)                                                as period_type,
        max(filing_id)                                                  as source_filing_id,

        max(case when concept = 'revenue'              then fact_value end) as revenue,
        max(case when concept = 'cost_of_revenue'      then fact_value end) as cost_of_revenue,
        max(case when concept = 'gross_profit'         then fact_value end) as gross_profit,
        max(case when concept = 'operating_expenses'   then fact_value end) as operating_expenses,
        max(case when concept = 'research_development' then fact_value end) as research_and_development,
        max(case when concept = 'sga_expense'          then fact_value end) as sga_expense,
        max(case when concept = 'operating_income'     then fact_value end) as operating_income,
        max(case when concept = 'interest_expense'     then fact_value end) as interest_expense,
        max(case when concept = 'pretax_income'        then fact_value end) as pretax_income,
        max(case when concept = 'income_tax_expense'   then fact_value end) as income_tax_expense,
        max(case when concept = 'net_income'           then fact_value end) as net_income,
        max(case when concept = 'eps_basic'            then fact_value end) as eps_basic,
        max(case when concept = 'eps_diluted'          then fact_value end) as eps_diluted

    from deduped
    group by 1, 2, 3

)

select
    {{ dbt_utils.generate_surrogate_key([
        'company_id', 'period_end_date', 'duration_quarters'
    ]) }}                                           as income_statement_key,
    company_id,
    period_end_date,
    duration_quarters,
    period_type,
    source_filing_id,
    revenue,
    cost_of_revenue,
    -- Derive gross profit when the filer did not tag it explicitly. Many do not.
    coalesce(gross_profit, revenue - cost_of_revenue) as gross_profit,
    operating_expenses,
    research_and_development,
    sga_expense,
    operating_income,
    interest_expense,
    pretax_income,
    income_tax_expense,
    net_income,
    eps_basic,
    eps_diluted
from pivoted
