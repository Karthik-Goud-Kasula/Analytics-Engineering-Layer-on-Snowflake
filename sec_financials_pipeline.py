"""Quarterly SEC ingest and dbt transformation.

Schedule: monthly, not quarterly. The SEC publishes roughly six weeks after each
quarter closes but does not commit to a date, so a monthly poll that finds
nothing new is far cheaper than a quarterly schedule that misses by a week and
leaves the warehouse stale for a month.

Task shape:

    check_sec_availability
             |
        download_and_load          (skipped if the quarter is not published yet)
             |
        dbt_seed -> dbt_run_staging -> dbt_test_staging
                            |
                    dbt_run_intermediate
                            |
                        dbt_run_marts
                            |
                    dbt_test_marts -> dbt_snapshot -> dbt_docs_generate
                            |
                    freshness_check

Staging is tested before the intermediate layer runs. Building marts on top of
data that already failed its source tests wastes warehouse credits and produces
a plausible-looking wrong answer, which is worse than no answer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.decorators import task
from airflow.exceptions import AirflowSkipException
from airflow.models import Variable
from airflow.models.dag import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.task_group import TaskGroup

LOG = logging.getLogger(__name__)

DBT_PROJECT_DIR = "/opt/airflow/dbt/sec_analytics"
DBT_PROFILES_DIR = "/opt/airflow/dbt/.dbt"
DBT_TARGET = Variable.get("dbt_target", default_var="prod")

DBT_ENV = {
    "DBT_PROFILES_DIR": DBT_PROFILES_DIR,
    "SNOWFLAKE_ACCOUNT": "{{ var.value.snowflake_account }}",
    "SNOWFLAKE_USER": "{{ var.value.snowflake_user }}",
    "SNOWFLAKE_PRIVATE_KEY": "{{ var.value.snowflake_private_key }}",
}

DEFAULT_ARGS = {
    "owner": "analytics-engineering",
    "depends_on_past": False,
    "email_on_failure": True,
    "email": ["analytics-alerts@example.com"],
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(hours=1),
}


def dbt_command(task_id: str, command: str, **kwargs) -> BashOperator:
    """BashOperator running a dbt command with consistent env and flags."""
    return BashOperator(
        task_id=task_id,
        bash_command=(
            f"cd {DBT_PROJECT_DIR} && "
            f"dbt {command} --target {DBT_TARGET} --profiles-dir {DBT_PROFILES_DIR}"
        ),
        env=DBT_ENV,
        append_env=True,
        **kwargs,
    )


with DAG(
    dag_id="sec_financials_pipeline",
    description="Ingest SEC Financial Statement Data Sets and build dbt models",
    start_date=datetime(2024, 1, 1),
    schedule="0 6 1 * *",          # 06:00 UTC on the 1st of each month
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["sec", "dbt", "snowflake", "finance"],
    doc_md=__doc__,
) as dag:

    start = EmptyOperator(task_id="start")

    @task(task_id="resolve_target_quarter")
    def resolve_target_quarter(data_interval_end=None) -> str:
        """Work out which SEC quarter should be available by now.

        The SEC publishes ~6 weeks after quarter end, so at any given moment the
        most recent *published* quarter is roughly two quarters back. Resolving
        this in code rather than hardcoding means the DAG keeps working without
        edits year after year.
        """
        run_date = data_interval_end or datetime.utcnow()
        month = run_date.month
        year = run_date.year

        # Step back two quarters from the current one.
        current_q = (month - 1) // 3 + 1
        target_q = current_q - 2
        target_year = year
        while target_q < 1:
            target_q += 4
            target_year -= 1

        quarter = f"{target_year}q{target_q}"
        LOG.info("Target quarter resolved to %s", quarter)
        return quarter

    @task(task_id="check_sec_availability")
    def check_sec_availability(quarter: str) -> str:
        """HEAD the zip before spending a task slot downloading it.

        Raises AirflowSkipException rather than failing when the quarter is not
        published. A missing quarter is the expected state for most runs of a
        monthly schedule -- treating it as a failure would mean a red DAG most
        months and an alerting channel everyone learns to ignore.
        """
        import os
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        url = (
            "https://www.sec.gov/files/dera/data/"
            f"financial-statement-data-sets/{quarter}.zip"
        )
        user_agent = os.environ.get("SEC_USER_AGENT", "")
        if not user_agent:
            raise ValueError("SEC_USER_AGENT env var is required by SEC policy")

        try:
            request = Request(url, method="HEAD", headers={"User-Agent": user_agent})
            with urlopen(request, timeout=30) as response:
                size_mb = int(response.headers.get("Content-Length", 0)) / 1e6
                LOG.info("%s is available (%.1f MB)", quarter, size_mb)
                return quarter
        except (HTTPError, URLError) as exc:
            raise AirflowSkipException(
                f"{quarter} not published yet ({exc}); will retry next month"
            ) from exc

    target_quarter = resolve_target_quarter()
    available_quarter = check_sec_availability(target_quarter)

    download_and_load = BashOperator(
        task_id="download_and_load",
        bash_command=(
            "cd /opt/airflow/dbt/sec_analytics && "
            "python scripts/load_sec_data.py "
            "--quarters {{ ti.xcom_pull(task_ids='check_sec_availability') }}"
        ),
        env={
            "SEC_USER_AGENT": "{{ var.value.sec_user_agent }}",
            "SNOWFLAKE_ACCOUNT": "{{ var.value.snowflake_account }}",
            "SNOWFLAKE_USER": "{{ var.value.snowflake_user }}",
            "SNOWFLAKE_PRIVATE_KEY_PATH": "{{ var.value.snowflake_key_path }}",
            "SNOWFLAKE_ROLE": "LOADER",
        },
        append_env=True,
        execution_timeout=timedelta(hours=2),
    )

    with TaskGroup(group_id="dbt_transform") as dbt_transform:
        seed = dbt_command("dbt_seed", "seed")

        run_staging = dbt_command("dbt_run_staging", "run --select staging")
        test_staging = dbt_command(
            "dbt_test_staging",
            "test --select staging",
            # Source freshness and accepted_values warnings should not stop the
            # build; genuine errors still fail because dbt exits non-zero.
            retries=0,
        )

        run_intermediate = dbt_command(
            "dbt_run_intermediate", "run --select intermediate"
        )
        run_marts = dbt_command("dbt_run_marts", "run --select marts")
        test_marts = dbt_command("dbt_test_marts", "test --select marts", retries=0)

        snapshot = dbt_command("dbt_snapshot", "snapshot")

        (
            seed
            >> run_staging
            >> test_staging
            >> run_intermediate
            >> run_marts
            >> test_marts
            >> snapshot
        )

    source_freshness = dbt_command(
        "dbt_source_freshness",
        "source freshness",
        # Freshness is informational on a quarterly source -- a warn is normal
        # for most of the quarter, so it must not fail the DAG.
        trigger_rule="all_done",
        retries=0,
    )

    generate_docs = dbt_command(
        "dbt_docs_generate",
        "docs generate",
        trigger_rule="all_success",
    )

    end = EmptyOperator(task_id="end", trigger_rule="none_failed_min_one_success")

    (
        start
        >> target_quarter
        >> available_quarter
        >> download_and_load
        >> dbt_transform
        >> [source_freshness, generate_docs]
        >> end
    )
