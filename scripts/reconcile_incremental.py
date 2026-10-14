#!/usr/bin/env python3
"""Prove the incremental pipeline gives the same answer as a full rebuild.

Builds two local DuckDB warehouses from the sample data:

  A (incremental), loaded the way production data arrives:
      1. initial load 2023q2-2024q1          -> dbt build (first run)
      2. new quarter 2024q2                  -> dbt build (incremental)
      3. re-run with nothing new             -> dbt build (must change nothing)
      4. backfill OLDER quarter 2023q1       -> dbt build (incremental)
      5. re-deliver 2024q1 (same content)    -> dbt build (must not duplicate)

  B (reference): load everything at once -> dbt build --full-refresh

Then compares every mart table in A and B row by row (EXCEPT in both
directions), ignoring only load timestamps. Also checks, after each step, that
fct_financial_facts has exactly one row per staged fact.

Step 4 is the case a filed_date watermark gets wrong: every backfilled filing
is older than the newest filing already loaded. The script reports how many
facts such a watermark would have skipped.

    python scripts/reconcile_incremental.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE_DIR = REPO_ROOT / "data" / "warehouse"
DB_INCREMENTAL = WAREHOUSE_DIR / "reconcile_incremental.duckdb"
DB_FULL = WAREHOUSE_DIR / "reconcile_full_refresh.duckdb"

MART_SCHEMA = "analytics_marts"
# Columns that legitimately differ between the two builds: when rows were loaded.
IGNORED_COLUMNS = {"loaded_at"}


def run(command: list[str], db: Path) -> None:
    env = {**os.environ, "DUCKDB_PATH": str(db), "DBT_TARGET": "local",
           "DBT_PROFILES_DIR": str(REPO_ROOT / "profiles")}  # fmt: skip
    result = subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    summary = [line for line in result.stdout.splitlines() if "Done. PASS=" in line]
    label = " ".join(command[:3]) if command[0] != "dbt" else " ".join(command)
    print(f"    $ {label}" + (f"\n      {summary[-1].split('  ', 1)[-1].strip()}" if summary else ""))
    if result.returncode != 0:
        print(result.stdout[-4000:], result.stderr[-4000:], sep="\n")
        raise SystemExit(f"command failed: {' '.join(command)}")


def load(db: Path, *quarters: str) -> None:
    run([sys.executable, "-m", "ingestion.load_sec_data", "--target", "duckdb",
         "--source", "sample", "--duckdb-path", str(db), "--quarters", *quarters], db)  # fmt: skip


def build(db: Path, *extra: str) -> None:
    run(["dbt", "build", "--target", "local", *extra], db)


def query(db: Path, sql: str):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def assert_facts_reconcile(db: Path, step: str) -> int:
    staged, built, distinct = query(
        db,
        "select (select count(*) from analytics_staging.stg_sec__numeric_facts), "
        "count(*), count(distinct fact_key) from analytics_marts.fct_financial_facts",
    )[0]
    status = "OK" if staged == built == distinct else "MISMATCH"
    print(f"    fct_financial_facts: {built} rows, {distinct} distinct, {staged} staged -> {status}")
    if status != "OK":
        raise SystemExit(f"{step}: incremental fact table does not reconcile with staging")
    return built


def compare_marts() -> list[str]:
    con = duckdb.connect()
    con.execute(f"attach '{DB_INCREMENTAL}' as inc (read_only)")
    con.execute(f"attach '{DB_FULL}' as ref (read_only)")
    tables = [row[0] for row in con.execute(
        "select table_name from information_schema.tables "
        f"where table_catalog = 'inc' and table_schema = '{MART_SCHEMA}' order by table_name"
    ).fetchall()]  # fmt: skip
    failures = []
    for table in tables:
        columns = [row[0] for row in con.execute(
            "select column_name from information_schema.columns where table_catalog = 'inc' "
            f"and table_schema = '{MART_SCHEMA}' and table_name = '{table}' order by ordinal_position"
        ).fetchall() if row[0] not in IGNORED_COLUMNS]  # fmt: skip
        column_list = ", ".join(f'"{c}"' for c in columns)
        a = f"select {column_list} from inc.{MART_SCHEMA}.{table}"
        b = f"select {column_list} from ref.{MART_SCHEMA}.{table}"
        only_a = con.execute(f"select count(*) from ({a} except all {b})").fetchone()[0]
        only_b = con.execute(f"select count(*) from ({b} except all {a})").fetchone()[0]
        rows = con.execute(f"select count(*) from ({a})").fetchone()[0]
        ok = only_a == 0 and only_b == 0
        print(f"    {table:<36} {rows:>6} rows  {'identical' if ok else f'DIFFERS ({only_a} / {only_b})'}")
        if not ok:
            failures.append(table)
    return failures


def main() -> int:
    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    for db in (DB_INCREMENTAL, DB_FULL):
        db.unlink(missing_ok=True)

    print("A. Incremental warehouse")
    print("  1. Initial load (2023q2-2024q1) and first build")
    load(DB_INCREMENTAL, "2023q2:2024q1")
    build(DB_INCREMENTAL)
    first_rows = assert_facts_reconcile(DB_INCREMENTAL, "step 1")
    first_loaded_at = query(DB_INCREMENTAL, "select max(loaded_at) from analytics_marts.fct_financial_facts")[
        0
    ][0]

    print("  2. New quarter 2024q2, incremental build")
    load(DB_INCREMENTAL, "2024q2")
    build(DB_INCREMENTAL)
    assert_facts_reconcile(DB_INCREMENTAL, "step 2")
    untouched = query(
        DB_INCREMENTAL,
        "select count(*) from analytics_marts.fct_financial_facts "
        f"where source_quarter <> '2024q2' and loaded_at <= '{first_loaded_at}'",
    )[0][0]
    print(f"    rows from step 1 left untouched by the incremental run: {untouched} of {first_rows}")
    if untouched != first_rows:
        raise SystemExit("step 2: the incremental run rewrote rows it should not have touched")

    print("  3. Re-run with no new data")
    before = query(DB_INCREMENTAL, "select count(*) from analytics_marts.fct_financial_facts")[0][0]
    build(DB_INCREMENTAL)
    after = assert_facts_reconcile(DB_INCREMENTAL, "step 3")
    if after != before:
        raise SystemExit("step 3: a no-op run changed the fact table")

    print("  4. Backfill an OLDER quarter (2023q1) after newer data")
    newest_filed = query(DB_INCREMENTAL, "select max(filed_date) from analytics_marts.dim_filing")[0][0]
    load(DB_INCREMENTAL, "2023q1")
    build(DB_INCREMENTAL)
    assert_facts_reconcile(DB_INCREMENTAL, "step 4")
    skipped = query(
        DB_INCREMENTAL,
        "select count(*) from analytics_marts.fct_financial_facts f "
        "join analytics_marts.dim_filing d on f.filing_id = d.filing_id "
        f"where f.source_quarter = '2023q1' and d.filed_date <= '{newest_filed}'",
    )[0][0]
    print(f"    backfilled facts a filed_date watermark (> {newest_filed}) would have skipped: {skipped}")

    print("  5. Re-deliver 2024q1 with identical content")
    load(DB_INCREMENTAL, "2024q1")
    build(DB_INCREMENTAL)
    assert_facts_reconcile(DB_INCREMENTAL, "step 5")

    print("B. Reference warehouse: everything at once, --full-refresh")
    load(DB_FULL, "2023q1:2024q2")
    build(DB_FULL, "--full-refresh")
    assert_facts_reconcile(DB_FULL, "reference")

    print("C. Compare every mart table (A vs B)")
    failures = compare_marts()
    if failures:
        print(f"FAILED: {len(failures)} table(s) differ: {', '.join(failures)}")
        return 1
    print("PASSED: incremental and full-refresh builds are identical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
