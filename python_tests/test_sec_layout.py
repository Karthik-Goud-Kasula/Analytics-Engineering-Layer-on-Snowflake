import re
from pathlib import Path

import pytest

from ingestion.sec_layout import (
    LAYOUTS,
    NUM,
    PRE,
    SUB,
    LayoutError,
    column_positions,
    count_and_check_rows,
    read_header,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_current_num_layout_maps_every_column(sample_quarter):
    path = sample_quarter / "num.txt"
    positions = column_positions(NUM, read_header(path), path)
    assert positions["adsh"] == 1
    assert positions["segments"] == 7
    assert all(positions.values())


def test_legacy_num_layout_without_segments_is_accepted(tmp_path):
    """Older copies of num.txt have no segments column and a different order."""
    path = tmp_path / "num.txt"
    path.write_text("adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n", encoding="utf-8")
    positions = column_positions(NUM, read_header(path), path)
    assert positions["segments"] is None
    assert positions["coreg"] == 4 and positions["ddate"] == 5


def test_legacy_pre_layout_without_negating_is_accepted(tmp_path):
    path = tmp_path / "pre.txt"
    header = [c for c in PRE.columns if c != "negating"]
    path.write_text("\t".join(header) + "\n", encoding="utf-8")
    assert column_positions(PRE, read_header(path), path)["negating"] is None


def test_unknown_column_is_rejected_with_actionable_message(tmp_path):
    path = tmp_path / "sub.txt"
    path.write_text("\t".join([*SUB.columns, "newcolumn"]) + "\n", encoding="utf-8")
    with pytest.raises(LayoutError, match=r"Unknown columns: \['newcolumn'\].*update"):
        column_positions(SUB, read_header(path), path)


def test_missing_required_column_is_rejected(tmp_path):
    path = tmp_path / "sub.txt"
    path.write_text("\t".join(c for c in SUB.columns if c != "cik") + "\n", encoding="utf-8")
    with pytest.raises(LayoutError, match=r"Missing required columns: \['cik'\]"):
        column_positions(SUB, read_header(path), path)


def test_row_with_wrong_field_count_is_rejected(tmp_path):
    path = tmp_path / "tag.txt"
    path.write_text("tag\tversion\nA\tv1\nB\tv1\textra\n", encoding="utf-8")
    with pytest.raises(LayoutError, match=r"tag.txt:3: expected 2"):
        count_and_check_rows(path, expected_fields=2)


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "pre.txt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(LayoutError, match="empty"):
        read_header(path)


def test_snowflake_raw_ddl_matches_layouts():
    """snowflake/setup/02_raw_tables.sql and ingestion/sec_layout.py must agree."""
    ddl = (REPO_ROOT / "snowflake" / "setup" / "02_raw_tables.sql").read_text(encoding="utf-8")
    for layout in LAYOUTS:
        match = re.search(rf"create table if not exists {layout.table_name} \((.*?)\n\)", ddl, flags=re.S)
        assert match, f"no CREATE TABLE for {layout.table_name}"
        columns = [line.strip().split()[0] for line in match.group(1).strip().splitlines()]
        assert columns == [*layout.columns, "_source_quarter", "_source_file", "_loaded_at"]


def test_all_in_one_setup_matches_layouts():
    """snowflake/setup/00_setup_all_in_one.sql must define the same raw tables."""
    sql = (REPO_ROOT / "snowflake" / "setup" / "00_setup_all_in_one.sql").read_text(encoding="utf-8")
    for layout in LAYOUTS:
        match = re.search(
            rf"create table if not exists sec_analytics_dev\.raw_sec\.{layout.table_name} \((.*?)\);",
            sql,
            flags=re.S,
        )
        assert match, f"no CREATE TABLE for {layout.table_name}"
        columns = [part.split()[0] for part in " ".join(match.group(1).split()).split(",")]
        assert columns == [*layout.columns, "_source_quarter", "_source_file", "_loaded_at"]
