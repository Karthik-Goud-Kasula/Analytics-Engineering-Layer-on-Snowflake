"""Load SEC Financial Statement Data Sets into Snowflake or a local DuckDB file.

Examples:

    # Synthetic sample data into the local DuckDB warehouse (no credentials needed)
    python -m ingestion.load_sec_data --target duckdb --source sample --quarters all

    # The same sample data into Snowflake (uses SNOWFLAKE_* variables from .env)
    python -m ingestion.load_sec_data --target snowflake --source sample --quarters all

    # Real SEC data: specific quarters, or an inclusive range
    python -m ingestion.load_sec_data --target snowflake --source sec --quarters 2024q1 2024q2
    python -m ingestion.load_sec_data --target snowflake --source sec --quarters 2023q1:2024q4

    # Scheduled use: newest completed quarter, only if not already loaded
    python -m ingestion.load_sec_data --target snowflake --source sec \
        --quarters latest --skip-if-loaded

Exit codes: 0 = at least one quarter loaded; 99 = nothing to do (quarter not yet
published, or already loaded with --skip-if-loaded); 1 = error. 99 is the exit
code Airflow's BashOperator treats as "skipped" by default.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ingestion.quarters import expand_quarters
from ingestion.sec_layout import LAYOUTS, LayoutError, column_positions, count_and_check_rows, read_header
from ingestion.sources import (
    QuarterNotPublished,
    download_quarter,
    sample_quarter_dir,
    sample_quarters,
)
from ingestion.warehouses import DuckDBWarehouse, FilePlan, SnowflakeWarehouse

LOG = logging.getLogger("ingestion.load_sec_data")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLE_DIR = REPO_ROOT / "data" / "sample"
DEFAULT_DOWNLOAD_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_DUCKDB_PATH = REPO_ROOT / "data" / "warehouse" / "sec_analytics.duckdb"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING_TO_DO = 99


def plan_files(quarter_dir: Path) -> list[FilePlan]:
    """Validate the four files of one quarter. Raises LayoutError on any mismatch."""
    plans = []
    for layout in LAYOUTS:
        path = quarter_dir / layout.file_name
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing")
        header = read_header(path)
        positions = column_positions(layout, header, path)
        rows = count_and_check_rows(path, expected_fields=len(header))
        plans.append(FilePlan(layout, path, positions, header, rows))
    return plans


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"error: environment variable {name} is not set (see .env.example)")
    return value


def connect(target: str, duckdb_path: Path, raw_schema: str):
    if target == "duckdb":
        return DuckDBWarehouse(duckdb_path, schema=raw_schema)
    return SnowflakeWarehouse(
        account=_require_env("SNOWFLAKE_ACCOUNT"),
        user=_require_env("SNOWFLAKE_USER"),
        private_key_path=_require_env("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_passphrase=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        role=os.environ.get("SNOWFLAKE_LOADER_ROLE", "SEC_LOADER"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "SEC_TRANSFORM_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "SEC_ANALYTICS_DEV"),
        schema=raw_schema.upper(),
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load SEC Financial Statement Data Sets into a warehouse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--target", choices=["duckdb", "snowflake"], default="duckdb")
    parser.add_argument("--source", choices=["sample", "sec"], default="sample")
    parser.add_argument(
        "--quarters",
        nargs="+",
        required=True,
        help="Quarters (2024q1), ranges (2023q1:2024q2), 'latest', or 'all' (sample only)",
    )
    parser.add_argument(
        "--skip-if-loaded",
        action="store_true",
        help="Skip quarters already present in the warehouse instead of reloading them",
    )
    parser.add_argument("--raw-schema", default="raw_sec")
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=Path(os.environ.get("DUCKDB_PATH", DEFAULT_DUCKDB_PATH)),
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    if args.quarters == ["all"]:
        if args.source != "sample":
            LOG.error("'all' is only valid with --source sample")
            return EXIT_ERROR
        quarters = sample_quarters(args.sample_dir)
    else:
        quarters = expand_quarters(args.quarters)
    if not quarters:
        LOG.error("no quarters to load")
        return EXIT_ERROR

    warehouse = connect(args.target, args.duckdb_path, args.raw_schema)
    try:
        already_loaded = warehouse.loaded_quarters() if args.skip_if_loaded else set()
        loaded_any = False
        for quarter in quarters:
            if quarter in already_loaded:
                LOG.info("%s already loaded; skipping (--skip-if-loaded)", quarter)
                continue
            try:
                if args.source == "sample":
                    quarter_dir = sample_quarter_dir(args.sample_dir, quarter)
                else:
                    quarter_dir = download_quarter(
                        quarter, args.download_dir, os.environ.get("SEC_USER_AGENT", "")
                    )
            except QuarterNotPublished as exc:
                LOG.warning("%s", exc)
                continue
            plans = plan_files(quarter_dir)
            counts = warehouse.load_quarter(quarter, plans)
            loaded_any = True
            LOG.info(
                "Loaded %s into %s: %s",
                quarter,
                args.target,
                ", ".join(f"{table}={rows}" for table, rows in counts.items()),
            )
    finally:
        warehouse.close()

    if not loaded_any:
        LOG.info("Nothing new to load")
        return EXIT_NOTHING_TO_DO
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import find_dotenv, load_dotenv

        # Same lookup as dbt: the nearest .env from the working directory up.
        load_dotenv(find_dotenv(usecwd=True), override=False)
    except ImportError:  # python-dotenv is optional for the loader
        pass
    args = parse_args(argv)
    try:
        return run(args)
    except (LayoutError, FileNotFoundError, PermissionError, ValueError) as exc:
        LOG.error("%s", exc)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
