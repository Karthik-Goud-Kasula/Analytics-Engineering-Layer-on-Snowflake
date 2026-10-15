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

    The SEC documents these eight columns as the key, but real files break it:
    in 2026q1 one 10-K reported 17 pairs of different forward-currency contracts
    whose typed-dimension identifier the SEC published as identical text. They
    are different facts with different values, so both rows are kept.
    `grain_ordinal` numbers rows that share all eight columns, and only rows
    after the first get it in their key. Ordinary facts keep the key they always
    had, so an existing incremental fact table needs no rebuild.

    The raw value is kept next to the parsed one so unparseable values can be
    reported (assert_numeric_values_parse) instead of silently disappearing.
*/

{%- set sec_grain = ['adsh', 'tag', 'version', 'ddate', 'qtrs', 'uom', 'segments', 'coreg'] %}

with source as (

    select * from {{ source('sec', 'num') }}

),

numbered as (

    select
        *,
        row_number() over (
            partition by {{ sec_grain | join(', ') }}
            order by value nulls last, footnote nulls last
        ) as grain_ordinal
    from source

),

renamed as (

    select
        case
            when grain_ordinal = 1
                then {{ dbt_utils.generate_surrogate_key(sec_grain) }}
            else {{ dbt_utils.generate_surrogate_key(sec_grain + ['grain_ordinal']) }}
        end as fact_key,
        grain_ordinal,
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

    from numbered

)

select * from renamed
