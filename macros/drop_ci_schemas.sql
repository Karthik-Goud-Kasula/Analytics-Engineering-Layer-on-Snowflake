{#
    Drop the schemas one CI run created, e.g. ci_1234567_staging, ci_1234567_marts.

        dbt run-operation drop_ci_schemas --target ci

    Destructive, so it refuses to run unless ALL of these hold:
      * the target is named 'ci';
      * the target schema starts with 'ci_' (set from GITHUB_RUN_ID in profiles.yml);
    and it only drops schemas whose names start with that run's own prefix.
    It can never touch the raw schema, a developer schema, or another run.
#}
{% macro drop_ci_schemas() %}
    {% if target.name != 'ci' %}
        {{ exceptions.raise_compiler_error("drop_ci_schemas only runs against the 'ci' target") }}
    {% endif %}
    {% set prefix = target.schema | lower %}
    {% if not prefix.startswith('ci_') or prefix | length < 4 %}
        {{ exceptions.raise_compiler_error("refusing to drop schemas: target schema '" ~ prefix ~ "' is not a ci_<run id> schema") }}
    {% endif %}

    {% set schemas_query %}
        select schema_name from {{ target.database }}.information_schema.schemata
    {% endset %}
    {# Exact prefix match in Jinja: in SQL LIKE, the "_" in "ci_" is a wildcard. #}
    {% set schemas = [] %}
    {% for schema in run_query(schemas_query).columns[0].values() %}
        {% if schema | lower == prefix or (schema | lower).startswith(prefix ~ '_') %}
            {% do schemas.append(schema) %}
        {% endif %}
    {% endfor %}

    {% for schema in schemas %}
        {% do log("Dropping schema " ~ target.database ~ "." ~ schema, info=true) %}
        {% do run_query("drop schema if exists " ~ target.database ~ "." ~ schema ~ " cascade") %}
    {% endfor %}
    {% if schemas | length == 0 %}
        {% do log("No schemas with prefix " ~ prefix ~ " to drop", info=true) %}
    {% endif %}
{% endmacro %}
