"""File layouts of the SEC Financial Statement Data Sets, and header validation.

Source of truth: https://www.sec.gov/files/financial-statement-data-sets.pdf
Each quarterly zip holds four tab-delimited, UTF-8 text files with a lowercase
header row: sub.txt, num.txt, tag.txt and pre.txt.

The raw tables mirror these files column-for-column, all VARCHAR. Nothing is cast
at load time, so a malformed value can never make the load drop a row; typing and
validation happen in the dbt staging layer, where they are tested.

Columns are mapped by header NAME, not position. Two columns are optional because
older copies of the files do not have them:

* num.segments was added when the SEC reprocessed the data sets in December 2024.
* pre.negating does not appear in older documentation of the layout.

A file missing an optional column loads with NULLs in it. A file with an unknown
column, or missing a required one, is rejected: silently loading a changed layout
into the wrong columns is far worse than a failed load.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileLayout:
    """Expected columns of one SEC file and the raw table it loads into."""

    file_name: str
    table_name: str
    columns: tuple[str, ...]
    optional_columns: frozenset[str] = frozenset()

    @property
    def required_columns(self) -> tuple[str, ...]:
        return tuple(c for c in self.columns if c not in self.optional_columns)


# Metadata columns the loader appends to every raw table.
METADATA_COLUMNS: tuple[str, ...] = ("_source_quarter", "_source_file", "_loaded_at")

SUB = FileLayout(
    file_name="sub.txt",
    table_name="sub",
    columns=(
        "adsh", "cik", "name", "sic", "countryba", "stprba", "cityba", "zipba",
        "bas1", "bas2", "baph", "countryma", "stprma", "cityma", "zipma", "mas1",
        "mas2", "countryinc", "stprinc", "ein", "former", "changed", "afs", "wksi",
        "fye", "form", "period", "fy", "fp", "filed", "accepted", "prevrpt",
        "detail", "instance", "nciks", "aciks",
    ),
)  # fmt: skip

NUM = FileLayout(
    file_name="num.txt",
    table_name="num",
    columns=(
        "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg",
        "value", "footnote",
    ),
    optional_columns=frozenset({"segments"}),
)  # fmt: skip

TAG = FileLayout(
    file_name="tag.txt",
    table_name="tag",
    columns=(
        "tag", "version", "custom", "abstract", "datatype", "iord", "crdr",
        "tlabel", "doc",
    ),
)  # fmt: skip

PRE = FileLayout(
    file_name="pre.txt",
    table_name="pre",
    columns=(
        "adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version",
        "plabel", "negating",
    ),
    optional_columns=frozenset({"negating"}),
)  # fmt: skip

LAYOUTS: tuple[FileLayout, ...] = (SUB, NUM, TAG, PRE)

DELIMITER = "\t"
ENCODING = "utf-8"


class LayoutError(ValueError):
    """A source file does not match the documented SEC layout."""


def read_header(path: Path) -> list[str]:
    """Return the header row of a tab-delimited SEC file."""
    with path.open("r", encoding=ENCODING, newline="") as handle:
        first_line = handle.readline()
    if not first_line:
        raise LayoutError(f"{path}: file is empty (expected a header row)")
    return first_line.rstrip("\r\n").split(DELIMITER)


def column_positions(layout: FileLayout, header: list[str], path: Path) -> dict[str, int | None]:
    """Map each canonical column to its 1-based position in the file (None = absent).

    Raises LayoutError with an actionable message when the header does not match.
    """
    normalized = [h.strip().lower() for h in header]

    duplicates = sorted({h for h in normalized if normalized.count(h) > 1})
    if duplicates:
        raise LayoutError(f"{path}: duplicate columns in header: {duplicates}")

    unknown = [h for h in normalized if h not in layout.columns]
    missing = [c for c in layout.required_columns if c not in normalized]
    if unknown or missing:
        raise LayoutError(
            f"{path}: header does not match the SEC {layout.file_name} layout. "
            f"Unknown columns: {unknown or 'none'}. Missing required columns: "
            f"{missing or 'none'}. If the SEC changed the format, update "
            "ingestion/sec_layout.py and snowflake/setup/02_raw_tables.sql together."
        )

    return {
        column: (normalized.index(column) + 1 if column in normalized else None) for column in layout.columns
    }


def count_and_check_rows(path: Path, expected_fields: int) -> int:
    """Count data rows, failing on any row whose field count differs from the header.

    A field count mismatch means an embedded tab or newline, i.e. the file would
    load shifted values into the wrong columns. The count is also used afterwards
    to reconcile the number of rows the warehouse reports as loaded.
    """
    rows = 0
    with path.open("r", encoding=ENCODING, newline="") as handle:
        handle.readline()  # header
        for line_number, line in enumerate(handle, start=2):
            if line in ("\n", "\r\n", ""):
                continue
            fields = line.rstrip("\r\n").count(DELIMITER) + 1
            if fields != expected_fields:
                raise LayoutError(
                    f"{path}:{line_number}: expected {expected_fields} tab-separated fields, found {fields}"
                )
            rows += 1
    return rows
