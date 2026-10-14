/*
    Restatements: every amendment that re-filed financial statements, compared
    with the original filing for the same company and period.

    Grain: one row per amending filing.

    This model deliberately reads ALL filings, bypassing
    int_filings__deduplicated -- that model's job is to hide superseded
    filings, and this one's job is to look at them. Amendments with no
    financial statements (e.g. Part III amendments) are not restatements and
    are excluded.

    A restatement is flagged material when revenue or net income moves by more
    than var('materiality_threshold') of the original. 5% is the common rule of
    thumb; SEC Staff Accounting Bulletin 99 cautions it is a starting point for
    judgement, not a bright line.

    Change rates divide by the ABSOLUTE original value, so a loss that grows
    shows as a negative change.
*/

with statement_filings as (

    select distinct filing_id
    from {{ ref('stg_sec__presentation') }}
    where
        statement_code in {{ primary_statement_codes() }}
        and not is_parenthetical

),

filings as (

    select
        submissions.filing_id,
        submissions.company_id,
        submissions.period_end_date,
        submissions.base_form_type,
        submissions.form_type,
        submissions.fiscal_year,
        submissions.fiscal_period,
        submissions.filed_date,
        row_number() over (
            partition by
                submissions.company_id,
                submissions.period_end_date,
                submissions.base_form_type
            order by
                submissions.filed_date asc,
                submissions.accepted_at asc,
                submissions.filing_id asc
        ) as filing_sequence
    from {{ ref('stg_sec__submissions') }} as submissions
    inner join statement_filings
        on submissions.filing_id = statement_filings.filing_id
    where submissions.base_form_type in ('10-K', '10-Q')

),

filing_values as (

    select
        filings.*,
        income.revenue,
        income.net_income,
        balance.total_assets,
        balance.total_equity
    from filings
    left join {{ ref('int_facts__income_statement') }} as income
        on
            filings.filing_id = income.filing_id
            -- compare like with like: the full year for a 10-K, the quarter for a 10-Q
            and income.duration_quarters = case when filings.base_form_type = '10-K' then 4 else 1 end
    left join {{ ref('int_facts__balance_sheet') }} as balance
        on filings.filing_id = balance.filing_id

),

compared as (

    select
        amended.company_id,
        amended.base_form_type,
        amended.fiscal_year,
        amended.fiscal_period,
        amended.period_end_date,
        original.filing_id as original_filing_id,
        amended.filing_id as amended_filing_id,
        amended.form_type as amended_form_type,
        original.filed_date as original_filed_date,
        amended.filed_date as amended_filed_date,

        original.revenue as original_revenue,
        amended.revenue as restated_revenue,
        amended.revenue - original.revenue as revenue_change,
        {{ safe_divide('amended.revenue - original.revenue', 'abs(original.revenue)') }}
            as revenue_change_rate,

        original.net_income as original_net_income,
        amended.net_income as restated_net_income,
        amended.net_income - original.net_income as net_income_change,
        {{ safe_divide('amended.net_income - original.net_income', 'abs(original.net_income)') }}
            as net_income_change_rate,

        original.total_assets as original_total_assets,
        amended.total_assets as restated_total_assets,
        original.total_equity as original_total_equity,
        amended.total_equity as restated_total_equity

    from filing_values as amended
    inner join filing_values as original
        on
            amended.company_id = original.company_id
            and amended.period_end_date = original.period_end_date
            and amended.base_form_type = original.base_form_type
            and original.filing_sequence = 1
    where amended.filing_sequence > 1

)

select
    {{ dbt_utils.generate_surrogate_key(['compared.amended_filing_id']) }} as restatement_key,
    {{ dbt_utils.generate_surrogate_key(['compared.company_id']) }} as company_key,
    compared.company_id as cik,
    companies.company_name,
    compared.base_form_type,
    compared.fiscal_year,
    compared.fiscal_period,
    compared.period_end_date,
    compared.original_filing_id,
    compared.amended_filing_id,
    compared.amended_form_type,
    compared.original_filed_date,
    compared.amended_filed_date,
    {{ dbt.datediff('compared.original_filed_date', 'compared.amended_filed_date', 'day') }}
        as days_until_restatement,
    compared.original_revenue,
    compared.restated_revenue,
    compared.revenue_change,
    compared.revenue_change_rate,
    compared.original_net_income,
    compared.restated_net_income,
    compared.net_income_change,
    compared.net_income_change_rate,
    compared.original_total_assets,
    compared.restated_total_assets,
    compared.original_total_equity,
    compared.restated_total_equity,
    coalesce(abs(compared.revenue_change_rate) > {{ var('materiality_threshold') }}, false)
    or coalesce(abs(compared.net_income_change_rate) > {{ var('materiality_threshold') }}, false)
        as is_material_restatement
from compared
left join {{ ref('dim_company') }} as companies
    on compared.company_id = companies.cik
