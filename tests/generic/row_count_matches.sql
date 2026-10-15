{#
    Fails when `model` and `compare_model` have different row counts.

    Used as a reconciliation (nothing dropped, duplicated or fanned out between
    two layers) in place of dbt_utils.equal_rowcount. That test groups each
    count by a constant, and a GROUP BY over an empty table returns no row, so:
      * both sides empty  -> the test errors ("None is not of type 'integer'");
      * one side empty    -> the difference is NULL and the test PASSES.
    The second case is exactly a failure a reconciliation exists to catch.
    Plain aggregates always return one row, so this has neither problem.

    Returns one row with both counts when the check fails.
#}
{% test row_count_matches(model, compare_model) %}

with model_count as (
    select count(*) as row_count from {{ model }}
),

compare_model_count as (
    select count(*) as row_count from {{ compare_model }}
)

select
    model_count.row_count as model_rows,
    compare_model_count.row_count as compare_model_rows
from model_count
cross join compare_model_count
where model_count.row_count <> compare_model_count.row_count

{% endtest %}
