/*
    One row per XBRL tag per taxonomy version.

    The single exception to "staging does not deduplicate": the SEC republishes
    every standard tag it uses in EACH quarterly tag.txt, so the raw table holds
    the same (tag, version) once per loaded quarter. That is a property of the
    file format, not of the data, so it is collapsed here -- keeping the most
    recently loaded copy -- and every consumer can rely on a unique key.
*/

with source as (

    select * from {{ source('sec', 'tag') }}

),

renamed as (

    select
        tag,
        version as tag_version,
        custom = '1' as is_custom,
        abstract = '1' as is_abstract,
        datatype as data_type,
        iord as instant_or_duration,
        crdr as natural_balance,
        tlabel as tag_label,
        doc as tag_documentation,
        _source_quarter as source_quarter,
        _loaded_at as loaded_at

    from source

)

select * from renamed
qualify row_number() over (
    partition by tag, tag_version
    order by loaded_at desc, source_quarter desc
) = 1
