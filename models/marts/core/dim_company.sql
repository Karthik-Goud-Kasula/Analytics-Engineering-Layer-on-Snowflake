/*
    Company dimension: current attributes (Type 1 view of dim_company_history).

    Grain: one row per company (CIK). For attributes as they were at a past
    date, join dim_company_history instead.
*/

with current_version as (

    select * from {{ ref('dim_company_history') }}
    where is_current

),

filing_stats as (

    select
        cik,
        min(first_filed_date) as first_filed_date,
        max(last_filed_date) as latest_filed_date,
        sum(filing_count) as filing_count,
        count(*) as attribute_version_count
    from {{ ref('dim_company_history') }}
    group by cik

)

select
    current_version.company_key,
    current_version.cik,
    lpad(cast(current_version.cik as varchar), 10, '0') as cik_padded,
    current_version.company_name,
    current_version.sic_code,
    current_version.sector,
    current_version.industry_group,
    current_version.incorporation_state_code,
    current_version.incorporation_country_code,
    current_version.fiscal_year_end_mmdd,
    filing_stats.first_filed_date,
    filing_stats.latest_filed_date,
    filing_stats.filing_count,
    filing_stats.attribute_version_count
from current_version
inner join filing_stats
    on current_version.cik = filing_stats.cik
