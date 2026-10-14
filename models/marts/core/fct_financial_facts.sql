{{
    config(
        materialized='incremental',
        unique_key='fact_key',
        incremental_strategy=('merge' if target.type == 'snowflake' else 'delete+insert'),
        on_schema_change='append_new_columns'
    )
}}

/*
    Atomic fact table: every numeric fact ever loaded, at the SEC's native
    grain, including custom tags, segment rows and co-registrant rows.

    Grain: one row per fact_key.

    The only incremental model in the project, because it is the only one where
    a full rebuild is genuinely expensive (tens of millions of rows across the
    full SEC history).

    Watermark: LOAD time (loaded_at), not filed_date. The SEC files are loaded
    a quarter at a time, and a backfill of an older quarter loads filings whose
    filed_date is years older than anything already in the table -- a filed_date
    filter would skip every one of them. loaded_at always moves forward. A
    lookback window (incremental_lookback_hours) absorbs loads that commit out
    of order; reprocessing that overlap is a no-op because of unique_key.

    Strategy: merge on Snowflake; delete+insert on DuckDB (local/CI). Both
    upsert on fact_key, so a reloaded quarter replaces its rows.

    Hard deletes: if a reloaded quarter no longer contains a fact, the old row
    stays here. The equal_rowcount test against stg_sec__numeric_facts detects
    that; the fix is `dbt build --full-refresh --select fct_financial_facts`.

    Only immutable attributes are stored. Mutable ones (is the filing still
    authoritative? the company's current name?) live in the dimensions, so a
    later amendment can never leave a stale flag in an old incremental row.
*/

with facts as (

    select * from {{ ref('stg_sec__numeric_facts') }}
    {% if is_incremental() %}
    where
        loaded_at > (
            select {{ dbt.dateadd('hour', -1 * var('incremental_lookback_hours'), 'max(loaded_at)') }}
            from {{ this }}
        )
    {% endif %}

),

filings as (

    select
        filing_id,
        company_id
    from {{ ref('stg_sec__submissions') }}

)

select
    facts.fact_key,
    {{ dbt_utils.generate_surrogate_key(['facts.filing_id']) }} as filing_key,
    {{ dbt_utils.generate_surrogate_key(['filings.company_id']) }} as company_key,
    facts.filing_id,
    filings.company_id as cik,
    facts.tag,
    facts.tag_version,
    facts.period_end_date,
    facts.duration_quarters,
    facts.unit_of_measure,
    facts.segments,
    facts.coregistrant,
    facts.is_consolidated,
    facts.fact_value,
    facts.source_quarter,
    facts.loaded_at
from facts
inner join filings
    on facts.filing_id = filings.filing_id
