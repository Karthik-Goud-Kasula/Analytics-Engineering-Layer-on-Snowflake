"""DAG integrity test for the reference Airflow DAG.

Skipped unless Apache Airflow 3 is installed (it is heavy and not needed to run
the project). CI installs it in a separate job; locally:

    pip install "apache-airflow==3.3.2" apache-airflow-providers-standard \
      --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

pytest.importorskip("airflow.sdk", reason="Apache Airflow 3 is not installed")

DAG_FOLDER = Path(__file__).resolve().parent.parent / "orchestration" / "airflow" / "dags"
DAG_FILE = DAG_FOLDER / "sec_financials_pipeline.py"


@pytest.fixture(scope="module")
def dagbag():
    from airflow.dag_processing.dagbag import DagBag

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bag = DagBag(dag_folder=str(DAG_FOLDER))
    bag.caught_warnings = [w for w in caught if str(DAG_FILE) in str(w.filename)]
    return bag


def test_dag_imports_without_errors(dagbag):
    assert dagbag.import_errors == {}
    assert "sec_financials_pipeline" in dagbag.dags


def test_dag_uses_no_deprecated_apis(dagbag):
    messages = [str(w.message) for w in dagbag.caught_warnings]
    assert messages == []


def test_no_variable_access_at_import_time():
    source = DAG_FILE.read_text(encoding="utf-8")
    assert "Variable.get" not in source  # Variables are read via Jinja at run time


def test_task_graph(dagbag):
    dag = dagbag.dags["sec_financials_pipeline"]
    assert set(dag.task_ids) == {
        "start", "load_latest_quarter", "dbt_transform.dbt_seed",
        "dbt_transform.dbt_build_staging", "dbt_transform.dbt_build_intermediate",
        "dbt_transform.dbt_build_marts", "dbt_docs_generate", "dbt_source_freshness", "end",
    }  # fmt: skip
    assert dag.get_task("dbt_transform.dbt_seed").upstream_task_ids == {"load_latest_quarter"}
    assert dag.get_task("dbt_transform.dbt_build_marts").upstream_task_ids == {
        "dbt_transform.dbt_build_intermediate"
    }
    assert dag.get_task("dbt_source_freshness").trigger_rule == "all_done"
    assert dag.catchup is False and dag.max_active_runs == 1


def test_load_skips_rather_than_fails_when_nothing_is_new(dagbag):
    task = dagbag.dags["sec_financials_pipeline"].get_task("load_latest_quarter")
    assert 99 in task.skip_on_exit_code  # matches ingestion.load_sec_data.EXIT_NOTHING_TO_DO
    assert "--quarters latest --skip-if-loaded" in task.bash_command
