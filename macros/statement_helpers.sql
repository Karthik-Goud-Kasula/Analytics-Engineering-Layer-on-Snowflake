{#
    Helpers shared by the statement pivots and filing models, so the definition
    of "primary financial statement" and the pivot pattern live in one place.
#}

{# Statement codes that make a filing count as containing financial statements. #}
{% macro primary_statement_codes() -%}
    ('BS', 'IS', 'CF')
{%- endmacro %}


{#
    One column per concept from (concept, fact_value, tag) rows that have
    already been reduced to one row per concept, plus a <concept>_tag column for
    selected concepts recording which XBRL tag supplied the value (lineage that
    reconciliation tests and analysts can check).
#}
{% macro pivot_concepts(concepts, with_source_tag=[]) -%}
    {%- for concept in concepts %}
        max(case when concept = '{{ concept }}' then fact_value end) as {{ concept }}
        {%- if not loop.last or with_source_tag %},{% endif %}
    {%- endfor %}
    {%- for concept in with_source_tag %}
        max(case when concept = '{{ concept }}' then tag end) as {{ concept }}_tag
        {%- if not loop.last %},{% endif %}
    {%- endfor %}
{%- endmacro %}
