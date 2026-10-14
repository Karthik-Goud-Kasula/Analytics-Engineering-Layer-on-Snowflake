{#
    Parse a YYYYMMDD string (the SEC's date format) into a DATE.
    Returns NULL instead of failing on a malformed value; staging tests then
    report how many dates did not parse.

    Dispatched because the two warehouses spell "try to parse" differently.
#}
{% macro parse_yyyymmdd(column) -%}
    {{ return(adapter.dispatch('parse_yyyymmdd', 'sec_analytics')(column)) }}
{%- endmacro %}

{% macro snowflake__parse_yyyymmdd(column) -%}
    try_to_date({{ column }}, 'YYYYMMDD')
{%- endmacro %}

{% macro duckdb__parse_yyyymmdd(column) -%}
    cast(try_strptime({{ column }}, '%Y%m%d') as date)
{%- endmacro %}

{% macro default__parse_yyyymmdd(column) -%}
    {{ exceptions.raise_compiler_error(
        "parse_yyyymmdd is not implemented for adapter '" ~ adapter.type() ~ "'"
    ) }}
{%- endmacro %}
