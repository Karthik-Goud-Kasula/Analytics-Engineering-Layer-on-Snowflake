/*
    One row per numeric fact, at the SEC's native eight-column grain:

        adsh, tag, version, ddate, qtrs, uom, segments, coreg

    `fact_key` hashes all eight so the grain is tested, not assumed. Dropping any
    one of them silently merges facts that must stay separate:

        ddate    -> prior-year comparatives merge into the current year
        qtrs     -> annual and quarterly figures collapse together
        segments -> segment breakdowns (added to the files in Dec 2024) are
                    summed into the consolidated total
        coreg    -> a subsidiary's figures merge into the parent's

    The raw value is kept next to the parsed one so unparseable values can be
    reported (assert_numeric_values_parse) instead of silently disappearing.
*/

with source as (

    select * from {{ source('sec', 'num') }}

),

renamed as (

    select
        {{ dbt_utils.generate_surrogate_key([
            'adsh', 'tag', 'version', 'ddate', 'qtrs', 'uom', 'segments', 'coreg'
        ]) }} as fact_key,
        adsh as filing_id,
        tag,
        version as tag_version,
        {{ parse_yyyymmdd('ddate') }} as period_end_date,
        try_cast(qtrs as integer) as duration_quarters,
        uom as unit_of_measure,
        segments,
        coreg as coregistrant,
        segments is null and coreg is null as is_consolidated,
        try_cast(value as decimal(28, 4)) as fact_value,
        value as fact_value_raw,
        footnote,
        _source_quarter as source_quarter,
        _loaded_at as loaded_at

    from source

)

select * from renamed
