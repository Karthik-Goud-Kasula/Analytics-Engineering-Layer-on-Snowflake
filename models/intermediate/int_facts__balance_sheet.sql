/*
    Balance sheet line items, pivoted into named columns.

    Grain: one row per filing_id. Balance sheet items are instants
    (duration_quarters = 0) at the filing's own period end.

    total_debt is derived: SEC filers split debt across current portion,
    short-term borrowings and non-current tags, and many report no total. It is
    NULL only when none of the three is reported (no debt tagged), not when one
    of them is missing. Lease liabilities are not included.
*/

{% set concepts = [
    'cash_and_equivalents', 'short_term_investments', 'accounts_receivable',
    'inventory', 'total_current_assets', 'total_assets',
    'total_current_liabilities', 'total_liabilities', 'debt_current',
    'short_term_borrowings', 'debt_noncurrent', 'total_equity',
    'total_liabilities_and_equity'
] %}

with facts as (

    select *
    from {{ ref('int_facts__classified') }}
    where
        statement_name = 'balance_sheet'
        and duration_quarters = 0
        and not is_custom_tag

),

mapping as (

    select
        tag,
        unit_of_measure,
        concept,
        priority
    from {{ ref('financial_tag_mapping') }}
    where statement = 'balance_sheet'

),

deduped as (

    select
        facts.filing_id,
        facts.company_id,
        facts.period_end_date,
        mapping.concept,
        facts.tag,
        facts.fact_value
    from facts
    inner join mapping
        on
            facts.tag = mapping.tag
            and facts.unit_of_measure = mapping.unit_of_measure
    qualify row_number() over (
        partition by facts.filing_id, mapping.concept
        order by mapping.priority asc, facts.tag asc
    ) = 1

),

pivoted as (

    select
        filing_id,
        company_id,
        period_end_date,
        {{ pivot_concepts(concepts) }}
    from deduped
    group by
        filing_id,
        company_id,
        period_end_date

)

select
    {{ dbt_utils.generate_surrogate_key(['filing_id']) }} as balance_sheet_key,
    filing_id,
    company_id,
    period_end_date,
    cash_and_equivalents,
    short_term_investments,
    accounts_receivable,
    inventory,
    total_current_assets,
    total_assets,
    total_current_liabilities,
    total_liabilities,
    debt_current,
    short_term_borrowings,
    debt_noncurrent,
    case
        when coalesce(debt_current, short_term_borrowings, debt_noncurrent) is not null
            then
                coalesce(debt_current, 0)
                + coalesce(short_term_borrowings, 0)
                + coalesce(debt_noncurrent, 0)
    end as total_debt,
    total_equity,
    total_liabilities_and_equity
from pivoted
