import re
from pathlib import Path

import pytest

from ingestion.sec_layout import (
    LAYOUTS,
    NUM,
    PRE,
    SUB,
    TAG,
    LayoutError,
    check_rows,
    column_positions,
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


NUM_HEADER = "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote"
GOOD_NUM_ROW = "0001-24-000001\tRevenues\tus-gaap/2023\t20231231\t4\tUSD\t\t\t480000000\t"


def check(path, layout, max_rejected_rows=100):
    return check_rows(path, layout, read_header(path), path.parent / "_clean", max_rejected_rows)


def write_num(tmp_path, *rows):
    path = tmp_path / "num.txt"
    path.write_text("\n".join([NUM_HEADER, GOOD_NUM_ROW, *rows]) + "\n", encoding="utf-8")
    return path


def test_clean_file_is_loaded_as_is(sample_quarter):
    path = sample_quarter / "num.txt"
    result = check(path, NUM)
    assert result.path == path and result.repaired == result.rejected == 0
    assert result.rows == len(path.read_text(encoding="utf-8").splitlines()) - 1
    assert not (sample_quarter / "_clean").exists()


# Exactly as in the SEC's 2026q1 num.txt (line 2451014): the segments value
# contains a tab, so the SEC encloses it in double quotes.
REAL_QUOTED_ROW = (
    "0001628280-26-020991\tInvestmentCompanyFinancialCommitmentToInvesteeFutureAmount\t"
    'us-gaap/2025\t20241231\t0\tUSD\t"InvestmentIdentifier=Unfunded equity\tsecurities;"\t\t'
    "11431000.0000\t"
)


def test_quoted_value_containing_a_tab_is_repaired(tmp_path):
    path = write_num(tmp_path, REAL_QUOTED_ROW)
    result = check(path, NUM)
    assert (result.rows, result.repaired, result.rejected) == (2, 1, 0)
    assert result.path == tmp_path / "_clean" / "num.txt"
    repaired = result.path.read_text(encoding="utf-8").splitlines()[2].split("\t")
    assert repaired[3:] == [
        "20241231", "0", "USD", "InvestmentIdentifier=Unfunded equity securities;", "", "11431000.0000", "",
    ]  # fmt: skip


def test_quotes_in_a_well_formed_row_are_kept_as_data(tmp_path):
    """Quotes are only honoured to repair a malformed row, never in ordinary ones."""
    row = GOOD_NUM_ROW.replace("USD\t\t", 'USD\t"Axis=Member"\t')
    path = write_num(tmp_path, row)
    result = check(path, NUM)
    assert result.path == path and result.repaired == 0
    assert '"Axis=Member"' in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "broken_row",
    [
        # An unquoted tab: no way to know which column it belongs to.
        GOOD_NUM_ROW.replace("USD\t\t\t", "USD\tAxis=A\tB\t\t"),
        GOOD_NUM_ROW.replace("480000000\t", "480000000\tSee\tnote"),
        # Quoted, but the re-read row fails a check (value is not numeric).
        GOOD_NUM_ROW.replace("USD\t\t\t480000000", 'USD\t"a\tb"\t\tn/a'),
        # Broken quoting.
        GOOD_NUM_ROW.replace("USD\t\t\t", 'USD\t"a\tb"c\t\t'),
        # Too few fields (a line break inside a value) is never repaired.
        "0001-24-000001\tRevenues\tus-gaap/2023",
    ],
)
def test_other_malformed_rows_are_rejected_not_repaired(tmp_path, broken_row):
    path = write_num(tmp_path, broken_row)
    result = check(path, NUM)
    assert (result.rows, result.repaired, result.rejected) == (1, 0, 1)
    assert result.path.read_text(encoding="utf-8").splitlines() == [NUM_HEADER, GOOD_NUM_ROW]
    rejects = result.rejects_path.read_text(encoding="utf-8").splitlines()
    assert rejects == ["line_number\t" + NUM_HEADER, "3\t" + broken_row]


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.file_name)
def test_every_layout_repairs_a_quoted_tab_in_a_text_column(tmp_path, sample_quarter, layout):
    """Each layout's repair_checks accept a real row once its quoted value is re-read."""
    lines = (sample_quarter / layout.file_name).read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    fields = lines[1].split("\t")
    text_column = {"sub": "name", "num": "footnote", "tag": "tlabel", "pre": "plabel"}[layout.table_name]
    fields[header.index(text_column)] = '"Part one\tpart two"'
    path = tmp_path / layout.file_name
    path.write_text("\n".join([lines[0], "\t".join(fields)]) + "\n", encoding="utf-8")
    result = check(path, layout)
    assert (result.rows, result.repaired, result.rejected) == (1, 1, 0)
    assert "Part one part two" in result.path.read_text(encoding="utf-8")


def test_too_many_rejected_rows_fail_the_file(tmp_path):
    path = tmp_path / "tag.txt"
    path.write_text("tag\tversion\nA\tv1\nB\tv1\textra\n", encoding="utf-8")
    with pytest.raises(LayoutError, match=r"tag.txt: 1 row\(s\) do not have 2 .*\(lines 3\); the limit is 0"):
        check(path, TAG, max_rejected_rows=0)
    assert not (tmp_path / "_clean").exists()


def test_stale_rejects_file_is_removed_when_the_file_is_clean(sample_quarter):
    stale = sample_quarter / "_clean" / "num.rejected.txt"
    stale.parent.mkdir()
    stale.write_text("old", encoding="utf-8")
    check(sample_quarter / "num.txt", NUM)
    assert not stale.exists()


def test_cleaned_copy_never_overwrites_the_source(tmp_path):
    path = write_num(tmp_path, REAL_QUOTED_ROW)
    with pytest.raises(ValueError, match="work_dir must differ"):
        check_rows(path, NUM, read_header(path), tmp_path)


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
