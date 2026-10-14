"""Warehouse writers. Both implement the same contract for one quarter:

1. Delete any rows previously loaded for the quarter, then load the four files,
   in ONE transaction. Re-running a quarter replaces it instead of duplicating it,
   and a failure leaves the previous load intact.
2. Reconcile: the rows the warehouse reports as loaded must equal the rows the
   loader counted in each file, or the transaction is rolled back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ingestion.quarters import parse_quarter
from ingestion.sec_layout import LAYOUTS, METADATA_COLUMNS, FileLayout

LOG = logging.getLogger(__name__)


class LoadReconciliationError(RuntimeError):
    """The warehouse loaded a different number of rows than the file contains."""


@dataclass(frozen=True)
class FilePlan:
    """A validated source file, ready to load."""

    layout: FileLayout
    path: Path
    positions: dict[str, int | None]  # canonical column -> 1-based position, None if absent
    header: list[str]
    expected_rows: int


def _raw_table_columns_ddl(layout: FileLayout, timestamp_type: str) -> str:
    columns = [f"{column} varchar" for column in layout.columns]
    columns += [
        "_source_quarter varchar",
        "_source_file varchar",
        f"_loaded_at {timestamp_type}",
    ]
    return ", ".join(columns)


class DuckDBWarehouse:
    """Local warehouse used for offline development and CI."""

    def __init__(self, path: Path, schema: str = "raw_sec") -> None:
        import duckdb

        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.schema = schema
        self.con = duckdb.connect(str(path))
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        self.con.execute(f"create schema if not exists {self.schema}")
        for layout in LAYOUTS:
            self.con.execute(
                f"create table if not exists {self.schema}.{layout.table_name} "
                f"({_raw_table_columns_ddl(layout, 'timestamp with time zone')})"
            )

    def loaded_quarters(self) -> set[str]:
        rows = self.con.execute(f"select distinct _source_quarter from {self.schema}.sub").fetchall()
        return {row[0] for row in rows}

    def load_quarter(self, quarter: str, plans: list[FilePlan]) -> dict[str, int]:
        parse_quarter(quarter)
        loaded: dict[str, int] = {}
        self.con.execute("begin transaction")
        try:
            for plan in plans:
                table = f"{self.schema}.{plan.layout.table_name}"
                self.con.execute(f"delete from {table} where _source_quarter = ?", [quarter])
                # Every column is read as VARCHAR with quoting disabled: the SEC
                # files are not quoted, so a literal " in a company name must
                # stay a character, not start a quoted field.
                file_columns = ", ".join(f"'{name}': 'VARCHAR'" for name in plan.header)
                select_list = ", ".join(
                    f'"{column}"' if plan.positions[column] else f"null as {column}"
                    for column in plan.layout.columns
                )
                self.con.execute(
                    f"insert into {table} "
                    f"select {select_list}, ?, ?, current_timestamp "
                    "from read_csv(?, delim = '\t', header = true, quote = '', "
                    f"escape = '', auto_detect = false, columns = {{{file_columns}}})",
                    [quarter, plan.path.name, str(plan.path)],
                )
                count = self.con.execute(
                    f"select count(*) from {table} where _source_quarter = ?", [quarter]
                ).fetchone()[0]
                if count != plan.expected_rows:
                    raise LoadReconciliationError(
                        f"{quarter} {plan.layout.file_name}: file has {plan.expected_rows} "
                        f"rows but {count} were loaded"
                    )
                loaded[plan.layout.table_name] = count
            self.con.execute("commit")
        except Exception:
            self.con.execute("rollback")
            raise
        return loaded

    def close(self) -> None:
        self.con.close()


class SnowflakeWarehouse:
    """Loads into Snowflake through an internal stage: PUT, then COPY INTO.

    Raw tables, the stage and the file format are created by
    snowflake/setup/02_raw_tables.sql, not here, so the loader's role needs no
    CREATE privileges.
    """

    stage = "sec_stage"
    file_format = "sec_tsv"

    def __init__(
        self,
        *,
        account: str,
        user: str,
        private_key_path: str,
        private_key_passphrase: str | None,
        role: str,
        warehouse: str,
        database: str,
        schema: str = "RAW_SEC",
    ) -> None:
        import snowflake.connector

        self.schema = schema
        self.con = snowflake.connector.connect(
            account=account,
            user=user,
            private_key_file=str(Path(private_key_path).expanduser()),
            private_key_file_pwd=private_key_passphrase or None,
            role=role,
            warehouse=warehouse,
            database=database,
            schema=schema,
            session_parameters={"QUERY_TAG": "sec_analytics_loader"},
        )
        self._check_objects_exist()

    def _check_objects_exist(self) -> None:
        cursor = self.con.cursor()
        cursor.execute(f"show tables in schema {self.schema}")
        name_index = [col[0].lower() for col in cursor.description].index("name")
        existing = {row[name_index].lower() for row in cursor.fetchall()}
        missing = [layout.table_name for layout in LAYOUTS if layout.table_name not in existing]
        if missing:
            raise RuntimeError(
                f"raw tables {missing} not found in {self.schema}. Run "
                "snowflake/setup/02_raw_tables.sql as SEC_LOADER first."
            )

    def loaded_quarters(self) -> set[str]:
        cursor = self.con.cursor()
        cursor.execute(f"select distinct _source_quarter from {self.schema}.sub")
        return {row[0] for row in cursor.fetchall()}

    def copy_statement(self, quarter: str, plan: FilePlan) -> str:
        """COPY INTO with an explicit column mapping built from the file header."""
        parse_quarter(quarter)  # validated, so safe to inline as a literal
        target_columns = ", ".join(plan.layout.columns + METADATA_COLUMNS)
        select_list = ", ".join(
            f"${plan.positions[column]}" if plan.positions[column] else "null"
            for column in plan.layout.columns
        )
        return (
            f"copy into {self.schema}.{plan.layout.table_name} ({target_columns}) "
            f"from (select {select_list}, '{quarter}', metadata$filename, current_timestamp() "
            f"from @{self.schema}.{self.stage}/{quarter}/{plan.path.name}.gz) "
            f"file_format = (format_name = '{self.schema}.{self.file_format}') "
            # FORCE: Snowflake skips files it loaded in the last 64 days. We just
            # deleted the quarter's rows, so the reload must not be skipped.
            "force = true on_error = 'abort_statement'"
        )

    def load_quarter(self, quarter: str, plans: list[FilePlan]) -> dict[str, int]:
        parse_quarter(quarter)
        cursor = self.con.cursor()
        stage_path = f"@{self.schema}.{self.stage}/{quarter}/"
        for plan in plans:
            # PUT is a file transfer, not DML, so it runs before the transaction.
            local = plan.path.resolve().as_posix()
            cursor.execute(f"put 'file://{local}' {stage_path} auto_compress = true overwrite = true")

        loaded: dict[str, int] = {}
        cursor.execute("begin transaction")
        try:
            for plan in plans:
                table = f"{self.schema}.{plan.layout.table_name}"
                cursor.execute(f"delete from {table} where _source_quarter = %s", (quarter,))
                cursor.execute(self.copy_statement(quarter, plan))
                columns = [col[0].lower() for col in cursor.description]
                results = cursor.fetchall()
                # A header-only file returns a single status column, no rows_loaded.
                rows_loaded = (
                    sum(row[columns.index("rows_loaded")] for row in results)
                    if "rows_loaded" in columns
                    else 0
                )
                if rows_loaded != plan.expected_rows:
                    raise LoadReconciliationError(
                        f"{quarter} {plan.layout.file_name}: file has {plan.expected_rows} "
                        f"rows but COPY loaded {rows_loaded}"
                    )
                loaded[plan.layout.table_name] = rows_loaded
            cursor.execute("commit")
        except Exception:
            cursor.execute("rollback")
            raise
        finally:
            cursor.execute(f"remove {stage_path}")
        return loaded

    def close(self) -> None:
        self.con.close()
