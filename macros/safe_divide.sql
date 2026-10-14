{#
    Divide two SQL expressions, returning NULL when the denominator is zero or NULL.

    Snowflake raises an error on x / 0. A ratio with a zero denominator is
    undefined, and NULL is the honest representation of "undefined" -- a huge
    number or a zero would both be lies that end up on a dashboard.

    Both sides are cast to double: these are ratios, so floating point is the
    right type, and it keeps results identical on Snowflake and DuckDB.
#}
{% macro safe_divide(numerator, denominator) -%}
    (cast({{ numerator }} as double) / nullif(cast({{ denominator }} as double), 0))
{%- endmacro %}
