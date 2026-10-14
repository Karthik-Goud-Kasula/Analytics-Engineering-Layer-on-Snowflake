/*
    Quarterly income statement figures, with Q4 derived.

    Grain: one row per (company_id, fiscal_year, fiscal_quarter).

    There is no Q4 10-Q: a company files three 10-Qs and one 10-K, and the
    10-K covers the full year. So Q4 is computed for FLOW measures:

        Q4 = annual - Q1 - Q2 - Q3

    Safeguards:
    * Q4 is derived only when all three quarters were reported for that fiscal
      year, and per measure only when all three quarters have that measure.
      Deriving from two quarters would produce a confidently wrong number.
    * Per-share figures are never derived: EPS is not additive across quarters
      (share counts change), so derived-Q4 EPS is NULL.
    * is_derived_q4 marks every computed row.

    Q4 inherits any restatement made between the 10-Qs and the 10-K. That is a
    real limitation of the arithmetic, not a bug; see docs/MODELING.md.
*/

{% set flow_measures = ['revenue', 'cost_of_revenue', 'gross_profit', 'operating_income', 'net_income'] %}
{# Explicit, shared column list for the UNION ALL: never rely on select * order. #}
{% set union_columns = ['company_id', 'fiscal_year', 'fiscal_quarter', 'period_end_date', 'source_filing_id']
    + flow_measures + ['eps_basic', 'eps_diluted'] %}

with quarterly_filings as (

    select
        filing_id,
        company_id,
        fiscal_year,
        period_end_date,
        case fiscal_period
            when 'Q1' then 1
            when 'Q2' then 2
            when 'Q3' then 3
        end as fiscal_quarter,
        filed_date
    from {{ ref('int_filings__deduplicated') }}
    where
        base_form_type = '10-Q'
        and fiscal_period in ('Q1', 'Q2', 'Q3')

),

reported as (

    select
        quarterly_filings.company_id,
        quarterly_filings.fiscal_year,
        quarterly_filings.fiscal_quarter,
        quarterly_filings.period_end_date,
        quarterly_filings.filing_id as source_filing_id,
        {% for measure in flow_measures %}
        income.{{ measure }},
        {% endfor %}
        income.eps_basic,
        income.eps_diluted
    from quarterly_filings
    inner join {{ ref('int_facts__income_statement') }} as income
        on
            quarterly_filings.filing_id = income.filing_id
            and income.duration_quarters = 1
    qualify row_number() over (
        partition by
            quarterly_filings.company_id,
            quarterly_filings.fiscal_year,
            quarterly_filings.fiscal_quarter
        order by quarterly_filings.period_end_date desc, quarterly_filings.filed_date desc
    ) = 1

),

quarter_totals as (

    select
        company_id,
        fiscal_year,
        {% for measure in flow_measures %}
        count({{ measure }}) as {{ measure }}_quarters,
        sum({{ measure }}) as {{ measure }}_q1_to_q3,
        {% endfor %}
        count(*) as quarters_reported
    from reported
    group by company_id, fiscal_year

),

derived_q4 as (

    select
        annual.company_id,
        annual.fiscal_year,
        4 as fiscal_quarter,
        annual.fiscal_year_end_date as period_end_date,
        annual.source_filing_id,
        {% for measure in flow_measures %}
        case
            when quarter_totals.{{ measure }}_quarters = 3
                then annual.{{ measure }} - quarter_totals.{{ measure }}_q1_to_q3
        end as {{ measure }},
        {% endfor %}
        cast(null as decimal(28, 4)) as eps_basic,
        cast(null as decimal(28, 4)) as eps_diluted
    from {{ ref('int_financials__annual') }} as annual
    inner join quarter_totals
        on
            annual.company_id = quarter_totals.company_id
            and annual.fiscal_year = quarter_totals.fiscal_year
    where quarter_totals.quarters_reported = 3

),

unioned as (

    select
        {% for column in union_columns %}
        {{ column }},
        {% endfor %}
        false as is_derived_q4
    from reported

    union all

    select
        {% for column in union_columns %}
        {{ column }},
        {% endfor %}
        true as is_derived_q4
    from derived_q4

)

select
    {{ dbt_utils.generate_surrogate_key(['unioned.company_id', 'unioned.fiscal_year', 'unioned.fiscal_quarter']) }}
        as quarterly_financials_key,
    unioned.company_id,
    unioned.fiscal_year,
    unioned.fiscal_quarter,
    unioned.period_end_date,
    unioned.source_filing_id,
    {% for measure in flow_measures %}
    unioned.{{ measure }},
    {% endfor %}
    unioned.eps_basic,
    unioned.eps_diluted,
    unioned.is_derived_q4,
    coalesce(quarter_totals.quarters_reported = 3, false) as has_complete_quarters
from unioned
left join quarter_totals
    on
        unioned.company_id = quarter_totals.company_id
        and unioned.fiscal_year = quarter_totals.fiscal_year
