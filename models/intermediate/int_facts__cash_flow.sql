/*
    Cash flow statement line items, pivoted into named columns.

    Grain: one row per (filing_id, duration_quarters). Note that 10-Q cash flow
    statements are year-to-date (Q2 = 2 quarters, Q3 = 3), never a single
    quarter, so there is no quarterly cash flow in the marts.

    Capital expenditure is reported as a positive payment.
*/

{% set concepts = [
    'operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow',
    'capital_expenditure', 'depreciation_amortization', 'dividends_paid',
    'share_repurchases'
] %}

with facts as (

    select *
    from {{ ref('int_facts__classified') }}
    where
        statement_name = 'cash_flow'
        and duration_quarters > 0
        and not is_custom_tag

),

mapping as (

    select
        tag,
        unit_of_measure,
        concept,
        priority
    from {{ ref('financial_tag_mapping') }}
    where statement = 'cash_flow'

),

deduped as (

    select
        facts.filing_id,
        facts.company_id,
        facts.period_end_date,
        facts.duration_quarters,
        mapping.concept,
        facts.tag,
        facts.fact_value
    from facts
    inner join mapping
        on
            facts.tag = mapping.tag
            and facts.unit_of_measure = mapping.unit_of_measure
    qualify row_number() over (
        partition by facts.filing_id, facts.duration_quarters, mapping.concept
        order by mapping.priority asc, facts.tag asc
    ) = 1

),

pivoted as (

    select
        filing_id,
        company_id,
        period_end_date,
        duration_quarters,
        {{ pivot_concepts(concepts) }}
    from deduped
    group by
        filing_id,
        company_id,
        period_end_date,
        duration_quarters

)

select
    {{ dbt_utils.generate_surrogate_key(['filing_id', 'duration_quarters']) }} as cash_flow_key,
    filing_id,
    company_id,
    period_end_date,
    duration_quarters,
    operating_cash_flow,
    investing_cash_flow,
    financing_cash_flow,
    capital_expenditure,
    depreciation_amortization,
    dividends_paid,
    share_repurchases,
    -- NULL unless both inputs are reported: free cash flow without capex
    -- would silently overstate it.
    operating_cash_flow - capital_expenditure as free_cash_flow
from pivoted
