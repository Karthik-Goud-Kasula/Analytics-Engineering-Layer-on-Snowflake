/*
    One row per SEC submission (filing). Renamed and typed; no filtering, joins
    or business logic.

    Dates arrive as YYYYMMDD strings and timestamps as text; every cast is a
    "try" cast so one malformed value becomes NULL (and fails a test) rather than
    failing the whole build.
*/

with source as (

    select * from {{ source('sec', 'sub') }}

),

renamed as (

    select
        adsh as filing_id,
        try_cast(cik as bigint) as company_id,
        trim(name) as company_name,
        try_cast(sic as integer) as sic_code,
        countryba as business_country_code,
        stprba as business_state_code,
        cityba as business_city,
        countryinc as incorporation_country_code,
        stprinc as incorporation_state_code,
        former as former_company_name,
        {{ parse_yyyymmdd('changed') }} as name_changed_date,
        afs as filer_status_code,
        wksi = '1' as is_well_known_seasoned_issuer,
        fye as fiscal_year_end_mmdd,
        form as form_type,
        replace(form, '/A', '') as base_form_type,
        form like '%/A' as is_amendment,
        {{ parse_yyyymmdd('period') }} as period_end_date,
        try_cast(fy as integer) as fiscal_year,
        fp as fiscal_period,
        {{ parse_yyyymmdd('filed') }} as filed_date,
        -- EDGAR acceptance time, US Eastern, published without a zone offset.
        try_cast(accepted as timestamp) as accepted_at,
        prevrpt = '1' as is_subsequently_amended,
        detail = '1' as has_detailed_tagging,
        instance as xbrl_instance_name,
        try_cast(nciks as integer) as registrant_count,
        aciks as additional_ciks,
        _source_quarter as source_quarter,
        _loaded_at as loaded_at

    from source

)

select * from renamed
