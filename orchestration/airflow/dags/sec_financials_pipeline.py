"""Scheduled SEC ingest and dbt transformation (Airflow 3 reference DAG).

STATUS: reference orchestration. Import-tested against Apache Airflow 3.3
(python_tests/test_airflow_dag.py); not deployed anywhere as part of this
project. See docs/ARCHITECTURE.md > Orchestration.

Schedule: weekly. The SEC posts each quarterly file shortly after the quarter
closes but commits to no date. A run that finds nothing new costs one HTTP
request and exits as "skipped", so polling weekly is cheap and bounds staleness
at about a week -- a monthly poll could leave a published quarter unloaded for
a month.

Task shape:

    load_latest_quarter          exit 99 -> skipped (not published yet, or already
            |                    loaded); downstream tasks are then skipped too
    dbt_transform:
        dbt_seed -> dbt_build_staging -> dbt_build_intermediate -> dbt_build_marts
            |
    dbt_docs_generate
    dbt_source_freshness         runs even when the load was skipped: it is what
                                 notices a quarter that never arrived

`dbt build` already runs each model's tests before its children and skips the
children of a failing test. Splitting the build by layer adds visibility in the
Airflow UI and lets a failed layer be retried on its own.

Deployment assumptions (all overridable):
* this repository is checked out at $SEC_ANALYTICS_PROJECT_DIR on the workers,
  with requirements.txt installed and `dbt deps` run;
* Airflow Variables hold the Snowflake account, user and key path and the SEC
  User-Agent. They are read through Jinja templates at RUN time -- never at
  import time, which would query the metadata database on every DAG parse.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, TaskGroup

PROJECT_DIR = os.environ.get("SEC_ANALYTICS_PROJECT_DIR", "/opt/airflow/sec-analytics")

SNOWFLAKE_ENV = {
    "DBT_PROFILES_DIR": f"{PROJECT_DIR}/profiles",
    "DBT_TARGET": "{{ var.value.get('sec_dbt_target', 'dev') }}",
    "DBT_SNOWFLAKE_SCHEMA": "{{ var.value.get('sec_dbt_schema', 'analytics') }}",
    "SNOWFLAKE_ACCOUNT": "{{ var.value.snowflake_account }}",
    "SNOWFLAKE_USER": "{{ var.value.snowflake_user }}",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "{{ var.value.snowflake_private_key_path }}",
}

DEFAULT_ARGS = {
    "owner": "analytics-engineering",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
}


def dbt_task(task_id: str, command: str, **kwargs) -> BashOperator:
    """BashOperator running one dbt command from the project directory."""
    return BashOperator(
        task_id=task_id,
        bash_command=f"cd {PROJECT_DIR} && dbt {command}",
        env=SNOWFLAKE_ENV,
        append_env=True,
        **kwargs,
    )


with DAG(
    dag_id="sec_financials_pipeline",
    description="Load the latest SEC Financial Statement Data Set and build the dbt models",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule="0 6 * * 1",  # Mondays 06:00 UTC
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["sec", "dbt", "snowflake"],
    doc_md=__doc__,
) as dag:
    start = EmptyOperator(task_id="start")

    # The loader resolves the latest completed quarter itself, downloads it,
    # validates the files and loads them in one transaction. It exits 99 when
    # the quarter is not published yet (HTTP 404) or is already loaded --
    # BashOperator's default skip_on_exit_code, so the task shows as skipped,
    # not failed. Any other failure (403, bad file layout, row-count mismatch)
    # fails the task.
    load_latest_quarter = BashOperator(
        task_id="load_latest_quarter",
        bash_command=(
            f"cd {PROJECT_DIR} && python -m ingestion.load_sec_data "
            "--target snowflake --source sec --quarters latest --skip-if-loaded"
        ),
        env={
            **SNOWFLAKE_ENV,
            "SNOWFLAKE_LOADER_ROLE": "SEC_LOADER",
            "SEC_USER_AGENT": "{{ var.value.sec_user_agent }}",
        },
        append_env=True,
        skip_on_exit_code=99,
        execution_timeout=timedelta(hours=1),
    )

    with TaskGroup(group_id="dbt_transform") as dbt_transform:
        seed = dbt_task("dbt_seed", "deps && dbt seed")
        build_staging = dbt_task("dbt_build_staging", "build --select staging")
        build_intermediate = dbt_task("dbt_build_intermediate", "build --select intermediate")
        build_marts = dbt_task("dbt_build_marts", "build --select marts")
        seed >> build_staging >> build_intermediate >> build_marts

    generate_docs = dbt_task("dbt_docs_generate", "docs generate")

    # Freshness is a monitor, not a gate: it runs whatever happened upstream,
    # and warns when no quarter has been loaded for 100+ days.
    source_freshness = dbt_task(
        "dbt_source_freshness", "source freshness", trigger_rule="all_done", retries=0
    )

    end = EmptyOperator(task_id="end", trigger_rule="none_failed")

    start >> load_latest_quarter >> dbt_transform >> generate_docs >> end
    load_latest_quarter >> source_freshness >> end
