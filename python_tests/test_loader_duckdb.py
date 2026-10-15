"""Loader behaviour against a real (temporary) DuckDB warehouse."""

from dataclasses import replace

import pytest

from ingestion.load_sec_data import EXIT_NOTHING_TO_DO, EXIT_OK, main, plan_files
from ingestion.warehouses import DuckDBWarehouse, LoadReconciliationError


def counts(warehouse, quarter):
    return {
        table: warehouse.con.execute(
            f"select count(*) from raw_sec.{table} where _source_quarter = ?", [quarter]
        ).fetchone()[0]
        for table in ("sub", "num", "tag", "pre")
    }


def test_load_matches_file_row_counts(tmp_path, sample_quarter):
    warehouse = DuckDBWarehouse(tmp_path / "w.duckdb")
    plans = plan_files(sample_quarter)
    loaded = warehouse.load_quarter("2024q1", plans)
    assert loaded == {p.layout.table_name: p.expected_rows for p in plans}
    assert counts(warehouse, "2024q1") == loaded


def test_reloading_a_quarter_replaces_it_instead_of_duplicating(tmp_path, sample_quarter):
    warehouse = DuckDBWarehouse(tmp_path / "w.duckdb")
    plans = plan_files(sample_quarter)
    first = warehouse.load_quarter("2024q1", plans)
    second = warehouse.load_quarter("2024q1", plans)
    assert first == second == counts(warehouse, "2024q1")


def test_empty_fields_load_as_null_not_empty_string(tmp_path, sample_quarter):
    warehouse = DuckDBWarehouse(tmp_path / "w.duckdb")
    warehouse.load_quarter("2024q1", plan_files(sample_quarter))
    empty, null = warehouse.con.execute(
        "select count(*) filter (where segments = ''), count(*) filter (where segments is null) "
        "from raw_sec.num"
    ).fetchone()
    assert empty == 0 and null > 0


def test_failed_reconciliation_rolls_back_and_keeps_previous_load(tmp_path, sample_quarter):
    warehouse = DuckDBWarehouse(tmp_path / "w.duckdb")
    plans = plan_files(sample_quarter)
    before = warehouse.load_quarter("2024q1", plans)

    # Pretend num.txt had one more row than the warehouse ends up loading.
    broken = [replace(p, expected_rows=p.expected_rows + 1) if p.layout.table_name == "num" else p
              for p in plans]  # fmt: skip
    with pytest.raises(LoadReconciliationError, match="num.txt"):
        warehouse.load_quarter("2024q1", broken)
    assert counts(warehouse, "2024q1") == before


def test_legacy_layout_loads_with_null_optional_columns(tmp_path, sample_quarter):
    """A num.txt without `segments` (pre-December-2024 layout) still loads."""
    num = sample_quarter / "num.txt"
    lines = num.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    keep = [i for i, name in enumerate(header) if name != "segments"]
    num.write_text(
        "\n".join("\t".join(line.split("\t")[i] for i in keep) for line in lines) + "\n",
        encoding="utf-8",
    )
    warehouse = DuckDBWarehouse(tmp_path / "w.duckdb")
    warehouse.load_quarter("2024q1", plan_files(sample_quarter))
    rows, with_segments = warehouse.con.execute(
        "select count(*), count(segments) from raw_sec.num"
    ).fetchone()
    assert rows == len(lines) - 1 and with_segments == 0


def test_cli_exit_codes(tmp_path):
    db = str(tmp_path / "w.duckdb")
    base = ["--target", "duckdb", "--source", "sample", "--duckdb-path", db]
    assert main([*base, "--quarters", "2024q1"]) == EXIT_OK
    # Already loaded + --skip-if-loaded -> nothing to do (Airflow treats 99 as "skipped").
    assert main([*base, "--quarters", "2024q1", "--skip-if-loaded"]) == EXIT_NOTHING_TO_DO
    # A quarter that is not in the sample is an error, not a silent skip.
    assert main([*base, "--quarters", "2019q1"]) == 1
    # 'all' only makes sense for the bundled sample.
    assert main(["--target", "duckdb", "--source", "sec", "--quarters", "all", "--duckdb-path", db]) == 1


def _load_sample_cli(tmp_path, sample_quarter, *extra):
    db = tmp_path / "w.duckdb"
    code = main(["--target", "duckdb", "--source", "sample", "--sample-dir", str(sample_quarter.parent),
                 "--download-dir", str(tmp_path / "dl"), "--duckdb-path", str(db),
                 "--quarters", "2024q1", *extra])  # fmt: skip
    return code, DuckDBWarehouse(db)


def test_malformed_rows_over_the_limit_fail_before_anything_is_loaded(tmp_path, sample_quarter):
    pre = sample_quarter / "pre.txt"
    pre.write_text(pre.read_text(encoding="utf-8") + "only\ttwo\n", encoding="utf-8")
    code, warehouse = _load_sample_cli(tmp_path, sample_quarter, "--max-rejected-rows", "0")
    assert code == 1
    assert warehouse.loaded_quarters() == set()


def test_malformed_rows_are_repaired_or_left_out_and_the_rest_loads(tmp_path, sample_quarter):
    num = sample_quarter / "num.txt"
    lines = num.read_text(encoding="utf-8").splitlines()
    fields = lines[1].split("\t")
    fields[6] = '"InvestmentIdentifier=Unfunded equity\tsecurities;"'  # quoted tab: repaired
    lines[1] = "\t".join(fields)
    pre = sample_quarter / "pre.txt"
    num.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pre.write_text(pre.read_text(encoding="utf-8") + "only\ttwo\n", encoding="utf-8")  # left out

    code, warehouse = _load_sample_cli(tmp_path, sample_quarter)
    assert code == EXIT_OK
    assert counts(warehouse, "2024q1")["num"] == len(lines) - 1
    value, segments = warehouse.con.execute(
        "select value, segments from raw_sec.num where segments like 'InvestmentIdentifier%'"
    ).fetchone()
    assert (value, segments) == (fields[8], "InvestmentIdentifier=Unfunded equity securities;")
    assert (tmp_path / "dl" / "_clean" / "2024q1" / "pre.rejected.txt").is_file()
