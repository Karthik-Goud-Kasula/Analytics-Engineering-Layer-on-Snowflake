/*
    Filing dimension: every submission, including amendments and forms outside
    the financial marts' scope (20-F, 40-F, ...).

    Grain: one row per filing (accession number).

    is_authoritative marks the filing int_filings__deduplicated chose for its
    company-period. days_to_file measures reporting lag from period end.
*/

with submissions as (

    select * from {{ ref('stg_sec__submissions') }}

),

statement_filings as (

    select distinct filing_id
    from {{ ref('stg_sec__presentation') }}
    where
        statement_code in {{ primary_statement_codes() }}
        and not is_parenthetical

),

authoritative as (

    select filing_id from {{ ref('int_filings__deduplicated') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['submissions.filing_id']) }} as filing_key,
    {{ dbt_utils.generate_surrogate_key(['submissions.company_id']) }} as company_key,
    submissions.filing_id,
    submissions.company_id as cik,
    submissions.form_type,
    submissions.base_form_type,
    submissions.is_amendment,
    submissions.period_end_date,
    submissions.fiscal_year,
    submissions.fiscal_period,
    submissions.filed_date,
    submissions.accepted_at,
    {{ dbt.datediff(
        'submissions.period_end_date',
        'submissions.filed_date',
        'day'
    ) }} as days_to_file,
    statement_filings.filing_id is not null as has_financial_statements,
    authoritative.filing_id is not null as is_authoritative,
    submissions.is_subsequently_amended,
    submissions.source_quarter
from submissions
left join statement_filings
    on submissions.filing_id = statement_filings.filing_id
left join authoritative
    on submissions.filing_id = authoritative.filing_id
