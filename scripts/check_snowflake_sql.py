#!/usr/bin/env python3
"""Offline Snowflake SQL check: compile every model for Snowflake and parse it.

No Snowflake account is needed, and none is contacted. The script:

1. copies the dbt project to a temporary directory;
2. compiles it with the real dbt-snowflake adapter against a profile whose
   account does not exist, with cache population off -- any model that tried
   to query the warehouse would fail loudly here;
3. compiles twice, with is_incremental() stubbed to true and to false, because
   asking whether the incremental table exists is the one thing that would
   otherwise need a connection -- so both branches of fct_financial_facts are
   checked;
4. parses every compiled model, test and analysis, plus the hand-written
   scripts in snowflake/, with sqlfluff's Snowflake grammar, and the compiled
   SQL also with sqlglot's.

What this proves: the SQL dbt would send to Snowflake is syntactically valid.
What it does NOT prove: that it runs (functions, types, privileges, data).
That needs a real account: see README > Snowflake setup.

    python scripts/check_snowflake_sql.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import sqlglot
from sqlglot.errors import ParseError

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_FILES = ["dbt_project.yml", "packages.yml", "package-lock.yml"]
PROJECT_DIRS = ["models", "macros", "tests", "seeds", "analyses", "dbt_packages"]

OFFLINE_PROFILE = """\
sec_analytics:
  target: snowflake_offline
  outputs:
    snowflake_offline:
      type: snowflake
      account: offline-compile-check.invalid
      user: offline
      password: not-a-credential
      role: SEC_TRANSFORMER
      database: SEC_ANALYTICS_DEV
      warehouse: SEC_TRANSFORM_WH
      schema: dbt_offline
      threads: 4
      connect_timeout: 5
      connect_retries: 0
"""


def compile_project(workdir: Path, incremental: bool) -> Path:
    stub = workdir / "macros" / "zz_offline_is_incremental.sql"
    stub.write_text(
        "{% macro is_incremental() %}{{ return(" + str(incremental) + ") }}{% endmacro %}\n",
        encoding="utf-8",
    )
    target = workdir / f"target_incremental_{str(incremental).lower()}"
    # dbt reads DBT_* environment variables as CLI flags (DBT_TARGET acts as
    # --target), so strip any inherited ones and pass everything explicitly.
    env = {key: value for key, value in os.environ.items() if not key.startswith("DBT_")}
    result = subprocess.run(
        [
            "dbt",
            "compile",
            "--profiles-dir",
            str(workdir),
            "--target",
            "snowflake_offline",
            "--target-path",
            str(target),
            "--no-populate-cache",
            "--exclude",
            "test_type:unit",
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-3000:], result.stderr[-3000:], sep="\n")
        raise SystemExit(f"dbt compile for Snowflake failed (is_incremental={incremental})")
    print(f"  compiled for Snowflake with is_incremental() = {incremental}")
    return target / "compiled"


def sqlfluff_parse_errors(files: list[Path]) -> list[str]:
    result = subprocess.run(
        [
            "sqlfluff",
            "lint",
            "--dialect",
            "snowflake",
            "--templater",
            "raw",
            "--rules",
            "LT01",
            "--nofail",
            "--format",
            "json",
            *map(str, files),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    errors = []
    for item in json.loads(result.stdout or "[]"):
        for violation in item["violations"]:
            if violation["code"] == "PRS":
                errors.append(
                    f"{item['filepath']}:{violation['start_line_no']}: {violation['description'][:160]}"
                )
                break
    return errors


def sqlglot_parse_errors(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        try:
            sqlglot.parse(path.read_text(encoding="utf-8"), read="snowflake")
        except ParseError as exc:
            errors.append(f"{path}: {str(exc).splitlines()[0][:160]}")
    return errors


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sec_snowflake_check_") as tmp:
        workdir = Path(tmp)
        for name in PROJECT_FILES:
            shutil.copy2(REPO_ROOT / name, workdir / name)
        for name in PROJECT_DIRS:
            if not (REPO_ROOT / name).exists():
                raise SystemExit(f"{name}/ not found (run `dbt deps` first for dbt_packages)")
            shutil.copytree(REPO_ROOT / name, workdir / name)
        (workdir / "profiles.yml").write_text(OFFLINE_PROFILE, encoding="utf-8")

        compiled_dirs = [compile_project(workdir, True), compile_project(workdir, False)]
        compiled = sorted(
            {p for d in compiled_dirs for p in (d / "sec_analytics").rglob("*.sql")}
        )  # fmt: skip
        scripts = sorted((REPO_ROOT / "snowflake").rglob("*.sql"))

        fluff_errors = sqlfluff_parse_errors(compiled + scripts)
        glot_errors = sqlglot_parse_errors(compiled)

    print(f"  sqlfluff (Snowflake grammar): {len(compiled)} compiled files + {len(scripts)} setup scripts")
    print(f"  sqlglot  (Snowflake grammar): {len(compiled)} compiled files")
    for error in fluff_errors + glot_errors:
        print(f"    {error}")
    if fluff_errors or glot_errors:
        print("FAILED: Snowflake SQL does not parse")
        return 1
    print("PASSED: all compiled SQL and setup scripts parse as Snowflake SQL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
