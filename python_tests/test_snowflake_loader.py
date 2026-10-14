"""SnowflakeWarehouse logic, tested offline with a fake connection.

These tests verify the statements the loader sends and their order (the
transaction boundaries, FORCE, the column mapping, rollback on a count
mismatch). They do NOT prove the SQL runs on Snowflake; that needs a real
account -- see README > Snowflake setup.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ingestion.load_sec_data import plan_files
from ingestion.warehouses import LoadReconciliationError, SnowflakeWarehouse


class FakeCursor:
    def __init__(self, log, rows_loaded_override=None):
        self.log = log
        self.description = None
        self._rows = []
        self.rows_loaded_override = rows_loaded_override

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))
        self._rows = []
        self.description = None
        if sql.startswith("copy into"):
            table = sql.split()[2].split(".")[-1]
            rows = self.rows_loaded_override.get(table) if self.rows_loaded_override else None
            self.description = [("file",), ("status",), ("rows_parsed",), ("rows_loaded",)]
            self._rows = [("f.gz", "LOADED", rows, rows)]

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows_loaded):
        self.log: list[str] = []
        self.rows_loaded = rows_loaded

    def cursor(self):
        return FakeCursor(self.log, self.rows_loaded)


def make_warehouse(rows_loaded):
    warehouse = SnowflakeWarehouse.__new__(SnowflakeWarehouse)  # skip the real connect
    warehouse.schema = "RAW_SEC"
    warehouse.con = FakeConnection(rows_loaded)
    return warehouse


def test_load_sequence_and_transaction_boundaries(sample_quarter):
    plans = plan_files(sample_quarter)
    warehouse = make_warehouse({p.layout.table_name: p.expected_rows for p in plans})
    loaded = warehouse.load_quarter("2024q1", plans)

    log = warehouse.con.log
    begin, commit = log.index("begin transaction"), log.index("commit")
    puts = [i for i, s in enumerate(log) if s.startswith("put ")]
    copies = [i for i, s in enumerate(log) if s.startswith("copy into")]
    deletes = [i for i, s in enumerate(log) if s.startswith("delete from")]

    assert len(puts) == len(copies) == len(deletes) == 4
    assert max(puts) < begin  # PUT is a file transfer: outside the transaction
    assert all(begin < i < commit for i in copies + deletes)
    assert log[-1] == "remove @RAW_SEC.sec_stage/2024q1/"
    assert all("force = true" in log[i] for i in copies)
    assert loaded == {p.layout.table_name: p.expected_rows for p in plans}


def test_count_mismatch_rolls_back(sample_quarter):
    plans = plan_files(sample_quarter)
    rows = {p.layout.table_name: p.expected_rows for p in plans}
    rows["num"] -= 1
    warehouse = make_warehouse(rows)
    with pytest.raises(LoadReconciliationError):
        warehouse.load_quarter("2024q1", plans)
    assert "rollback" in warehouse.con.log and "commit" not in warehouse.con.log
    assert warehouse.con.log[-1].startswith("remove")  # stage cleaned even on failure


def test_copy_maps_columns_by_header_and_nulls_missing_optional_columns(sample_quarter):
    plan = next(p for p in plan_files(sample_quarter) if p.layout.table_name == "num")
    legacy = replace(plan, positions={**plan.positions, "segments": None})
    sql = make_warehouse({}).copy_statement("2024q1", legacy)
    assert "(adsh, tag, version, ddate, qtrs, uom, segments, coreg, value, footnote," in sql
    assert "$6, null, $8" in sql  # uom, segments (absent), coreg
    assert "'2024q1', metadata$filename, current_timestamp()" in sql


def test_quarter_is_validated_before_being_inlined(sample_quarter):
    plan = plan_files(sample_quarter)[0]
    with pytest.raises(ValueError):
        make_warehouse({}).copy_statement("2024q1'; drop table x; --", plan)
