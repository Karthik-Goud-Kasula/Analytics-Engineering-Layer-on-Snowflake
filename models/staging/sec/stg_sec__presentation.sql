/*
    One row per line of each rendered financial statement: where (which
    statement, which line) a filer presented each tag.

    This is what tells us a tag is on the income statement rather than in a
    footnote -- the tag name alone does not.
*/

with source as (

    select * from {{ source('sec', 'pre') }}

),

renamed as (

    select
        adsh as filing_id,
        try_cast(report as integer) as report_number,
        try_cast(line as integer) as line_number,
        stmt as statement_code,
        inpth = '1' as is_parenthetical,
        rfile as rendered_file_type,
        tag,
        version as tag_version,
        plabel as presented_label,
        negating = '1' as is_negated,
        _source_quarter as source_quarter,
        _loaded_at as loaded_at

    from source

)

select * from renamed
