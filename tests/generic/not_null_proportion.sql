{#
    Fails when the share of non-null values in a column drops below `at_least`.

    Plain not_null is too strict for public filings: many companies legitimately
    report no inventory or no R&D. But a column going from 95% populated to 12%
    is a real incident -- usually a tag-mapping or parsing regression -- and this
    catches it without failing on legitimate sparsity.

    Returns one row (the observed proportion) when the check fails.
#}
{% test not_null_proportion(model, column_name, at_least) %}

with stats as (

    select
        count(*) as total_rows,
        count({{ column_name }}) as non_null_rows
    from {{ model }}

)

select
    total_rows,
    non_null_rows,
    cast(non_null_rows as double) / total_rows as non_null_proportion
from stats
where total_rows > 0
  and cast(non_null_rows as double) / total_rows < {{ at_least }}

{% endtest %}
