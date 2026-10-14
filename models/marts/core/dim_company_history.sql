/*
    Company attributes over time: Slowly Changing Dimension Type 2.

    Grain: one row per company per version of its tracked attributes (name, SIC
    code, state and country of incorporation, fiscal year end).

    Why a model and not a dbt snapshot: a snapshot records when WE noticed a
    change, and its history is lost if the snapshot table is ever rebuilt. Here
    the history is already in the source -- every filing records the company's
    attributes as of that filing -- so versions are derived from the data:
    effective dates are the acceptance time of the first filing that showed
    them, the model is fully reproducible, and a backfill of older quarters
    corrects history instead of being appended out of order.

    Gaps-and-islands: a new version starts whenever the attribute set differs
    from the previous filing's. A company that changes A -> B -> A gets three
    versions, not two (grouping by the attributes alone would merge the A's).

    valid_from is inclusive, valid_to exclusive; the current version has
    valid_to NULL and is_current true. Point-in-time join:
        filing.accepted_at >= valid_from
        and (filing.accepted_at < valid_to or valid_to is null)

    Surrogate keys are deterministic for a given set of loaded filings. A
    backfill can move a version's valid_from, so only fully rebuilt tables
    store company_history_key (never the incremental fct_financial_facts).
*/

with filings as (

    select
        company_id,
        filing_id,
        accepted_at,
        filed_date,
        company_name,
        sic_code,
        incorporation_state_code,
        incorporation_country_code,
        fiscal_year_end_mmdd,
        {{ dbt_utils.generate_surrogate_key([
            'company_name', 'sic_code', 'incorporation_state_code',
            'incorporation_country_code', 'fiscal_year_end_mmdd'
        ]) }} as attribute_hash
    from {{ ref('stg_sec__submissions') }}

),

flagged as (

    select
        *,
        case
            when attribute_hash = lag(attribute_hash) over (
                partition by company_id order by accepted_at, filing_id
            ) then 0
            else 1
        end as starts_new_version
    from filings

),

numbered as (

    select
        *,
        sum(starts_new_version) over (
            partition by company_id
            order by accepted_at, filing_id
            rows between unbounded preceding and current row
        ) as version_number
    from flagged

),

versions as (

    -- Attributes are constant within a version by construction, so any
    -- aggregate returns the same value; min() just collapses the rows.
    select
        company_id,
        version_number,
        min(company_name) as company_name,
        min(sic_code) as sic_code,
        min(incorporation_state_code) as incorporation_state_code,
        min(incorporation_country_code) as incorporation_country_code,
        min(fiscal_year_end_mmdd) as fiscal_year_end_mmdd,
        min(accepted_at) as valid_from,
        min(filed_date) as first_filed_date,
        max(filed_date) as last_filed_date,
        count(*) as filing_count
    from numbered
    group by company_id, version_number

),

sectors as (

    select * from {{ ref('sic_sector_mapping') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['versions.company_id', 'versions.valid_from']) }}
        as company_history_key,
    {{ dbt_utils.generate_surrogate_key(['versions.company_id']) }} as company_key,
    versions.company_id as cik,
    versions.version_number,
    versions.company_name,
    versions.sic_code,
    coalesce(sectors.sector, 'Unclassified') as sector,
    coalesce(sectors.industry_group, 'Unclassified') as industry_group,
    versions.incorporation_state_code,
    versions.incorporation_country_code,
    versions.fiscal_year_end_mmdd,
    versions.valid_from,
    lead(versions.valid_from) over (
        partition by versions.company_id order by versions.version_number
    ) as valid_to,
    lead(versions.valid_from) over (
        partition by versions.company_id order by versions.version_number
    ) is null as is_current,
    versions.first_filed_date,
    versions.last_filed_date,
    versions.filing_count
from versions
-- Range join. Safe only because assert_sic_ranges_do_not_overlap guarantees at
-- most one matching range; the unique test on company_history_key backs it up.
left join sectors
    on versions.sic_code between sectors.sic_low and sectors.sic_high
