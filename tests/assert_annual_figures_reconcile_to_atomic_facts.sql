/*
    Reconciliation between the two independent paths through the project:

      * the pivot path (classified -> pivoted -> annual), and
      * the incremental atomic fact table (fct_financial_facts).

    Every annual revenue and net income figure must equal the consolidated
    full-year fact, in fct_financial_facts, for the same filing and the tag the
    pivot recorded as its source. A mismatch means one path dropped,
    duplicated or altered a value -- for example, an incremental run that
    missed a reloaded quarter.
*/

with annual as (

    select
        source_filing_id,
        fiscal_year_end_date,
        'revenue' as concept,
        revenue_tag as tag,
        revenue as value
    from {{ ref('fct_company_financials_annual') }}
    where revenue is not null

    union all

    select
        source_filing_id,
        fiscal_year_end_date,
        'net_income' as concept,
        net_income_tag as tag,
        net_income as value
    from {{ ref('fct_company_financials_annual') }}
    where net_income is not null

),

facts as (

    select
        filing_id,
        tag,
        period_end_date,
        fact_value
    from {{ ref('fct_financial_facts') }}
    where
        is_consolidated
        and duration_quarters = 4
        and unit_of_measure = 'USD'

)

select
    annual.source_filing_id,
    annual.concept,
    annual.tag,
    annual.value as annual_value,
    facts.fact_value as atomic_value
from annual
left join facts
    on
        annual.source_filing_id = facts.filing_id
        and annual.tag = facts.tag
        and abs({{ dbt.datediff('annual.fiscal_year_end_date', 'facts.period_end_date', 'day') }}) <= 15
where
    facts.fact_value is null
    or facts.fact_value != annual.value
