{{
    config(
        severity='warn',
        store_failures=true
    )
}}

/*
    Numeric values that arrived but could not be parsed as decimals.

    The loader keeps every row as text, and staging uses try_cast, so a
    malformed value becomes NULL instead of failing the load. This test is
    what stops that NULL from going unnoticed. Warn-level: one bad value
    should not block a build, but it must be visible (and is stored for
    inspection).
*/

select
    fact_key,
    filing_id,
    tag,
    period_end_date,
    fact_value_raw,
    source_quarter
from {{ ref('stg_sec__numeric_facts') }}
where
    fact_value_raw is not null
    and fact_value is null
