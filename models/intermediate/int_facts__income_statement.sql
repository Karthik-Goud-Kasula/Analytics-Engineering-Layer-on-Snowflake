/*
    Income statement line items, pivoted from tag rows into named columns.

    Grain: one row per (filing_id, duration_quarters). A 10-K yields one annual
    row (4 quarters); a 10-Q yields a quarter row (1) and, for Q2/Q3, a
    year-to-date row (2 or 3). Every value in a row comes from the same filing.

    Two hard problems this model solves:

    1. TAG SYNONYMS. There is no single revenue tag. Depending on filer and
       taxonomy year, top-line revenue is `Revenues`,
       `RevenueFromContractWithCustomerExcludingAssessedTax`,
       `SalesRevenueNet`, and several others. The seed
       `financial_tag_mapping.csv` maps every known variant to a canonical
       concept, with a priority so the most specific tag wins when a filer
       reports more than one. The tag that won is kept (`revenue_tag`,
       `net_income_tag`) so every figure can be traced back to its source.

    2. DURATION MIXING. `qtrs = 4` is an annual figure and `qtrs = 1` is a
       single quarter. Summing across them inflates results ~5x. This model
       keeps them separate by carrying `duration_quarters` in the grain.

    The mapping also matches on unit: earnings per share is reported in
    USD/shares, so a USD-only filter would silently null every EPS value.
*/

{% set concepts = [
    'revenue', 'cost_of_revenue', 'gross_profit', 'operating_expenses',
    'research_and_development', 'sga_expense', 'operating_income',
    'interest_expense', 'pretax_income', 'income_tax_expense', 'net_income',
    'eps_basic', 'eps_diluted'
] %}

with facts as (

    select *
    from {{ ref('int_facts__classified') }}
    where
        statement_name in ('income_statement', 'comprehensive_income')
        and duration_quarters > 0      -- income statement items are always flows
        and not is_custom_tag          -- cross-company comparability

),

mapping as (

    select
        tag,
        unit_of_measure,
        concept,
        priority
    from {{ ref('financial_tag_mapping') }}
    where statement = 'income_statement'

),

mapped as (

    select
        facts.filing_id,
        facts.company_id,
        facts.period_end_date,
        facts.duration_quarters,
        facts.period_type,
        mapping.concept,
        facts.tag,
        facts.fact_value,
        -- When a filer reports two tags that both map to the same concept,
        -- take the highest-priority (most specific) one. Tag name is a
        -- deterministic final tiebreak so reruns never flip the answer.
        row_number() over (
            partition by facts.filing_id, facts.duration_quarters, mapping.concept
            order by mapping.priority asc, facts.tag asc
        ) as concept_rank
    from facts
    inner join mapping
        on
            facts.tag = mapping.tag
            and facts.unit_of_measure = mapping.unit_of_measure

),

deduped as (

    select *
    from mapped
    where concept_rank = 1

),

pivoted as (

    select
        filing_id,
        company_id,
        period_end_date,
        duration_quarters,
        period_type,
        {{ pivot_concepts(concepts, with_source_tag=['revenue', 'net_income']) }}
    from deduped
    group by
        filing_id,
        company_id,
        period_end_date,
        duration_quarters,
        period_type

),

flagged as (

    select
        *,
        gross_profit is null
        and revenue is not null
        and cost_of_revenue is not null as is_gross_profit_derived
    from pivoted

)

select
    {{ dbt_utils.generate_surrogate_key(['filing_id', 'duration_quarters']) }} as income_statement_key,
    filing_id,
    company_id,
    period_end_date,
    duration_quarters,
    period_type,
    revenue,
    revenue_tag,
    cost_of_revenue,
    -- Derive gross profit when the filer did not tag it explicitly. Many do not.
    coalesce(gross_profit, revenue - cost_of_revenue) as gross_profit,
    is_gross_profit_derived,
    operating_expenses,
    research_and_development,
    sga_expense,
    operating_income,
    interest_expense,
    pretax_income,
    income_tax_expense,
    net_income,
    net_income_tag,
    eps_basic,
    eps_diluted
from flagged
