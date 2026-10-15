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

The files are not quoted, with one exception seen in real data: a value that
contains a tab is enclosed in double quotes. In 2026q1, num.txt has two rows whose
segments value is the typed dimension "InvestmentIdentifier=Unfunded equity<TAB>
securities;". Read without quoting, such a row has one field too many and every
value after it lands in the wrong column. check_rows() repairs these rows, sets
aside anything else malformed in a rejects file, and fails the load if there are
too many.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileLayout:
    """Expected columns of one SEC file and the raw table it loads into."""

    file_name: str
    table_name: str
    columns: tuple[str, ...]
    optional_columns: frozenset[str] = frozenset()
    # (column, regex) pairs a repaired row must satisfy: proof that every value
    # ended up in its own column. Only applied to repaired rows; ordinary rows
    # are validated in dbt staging.
    repair_checks: tuple[tuple[str, str], ...] = ()

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
    repair_checks=(("cik", r"\d+"), ("filed", r"\d{8}")),
)  # fmt: skip

NUMERIC = r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?"

NUM = FileLayout(
    file_name="num.txt",
    table_name="num",
    columns=(
        "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg",
        "value", "footnote",
    ),
    optional_columns=frozenset({"segments"}),
    repair_checks=(("ddate", r"\d{8}"), ("qtrs", r"\d+"), ("value", f"({NUMERIC})?")),
)  # fmt: skip

TAG = FileLayout(
    file_name="tag.txt",
    table_name="tag",
    columns=(
        "tag", "version", "custom", "abstract", "datatype", "iord", "crdr",
        "tlabel", "doc",
    ),
    repair_checks=(("custom", "[01]"), ("abstract", "[01]")),
)  # fmt: skip

PRE = FileLayout(
    file_name="pre.txt",
    table_name="pre",
    columns=(
        "adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version",
        "plabel", "negating",
    ),
    optional_columns=frozenset({"negating"}),
    repair_checks=(("report", r"\d+"), ("line", r"\d+")),
)  # fmt: skip

LAYOUTS: tuple[FileLayout, ...] = (SUB, NUM, TAG, PRE)

DELIMITER = "\t"
QUOTE = '"'
ENCODING = "utf-8"
LINE_ENDINGS = "\r\n"


class LayoutError(ValueError):
    """A source file does not match the documented SEC layout."""


def read_header(path: Path) -> list[str]:
    """Return the header row of a tab-delimited SEC file."""
    with path.open("r", encoding=ENCODING, newline="") as handle:
        first_line = handle.readline()
    if not first_line:
        raise LayoutError(f"{path}: file is empty (expected a header row)")
    return first_line.rstrip(LINE_ENDINGS).split(DELIMITER)


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


DEFAULT_MAX_REJECTED_ROWS = 100


@dataclass(frozen=True)
class RowCheck:
    """Result of checking the rows of one file."""

    path: Path  # the file to load: the original, or a cleaned copy
    rows: int  # data rows in `path`, i.e. what the warehouse must report loading
    repaired: int = 0  # rows fixed by reading their double-quoted values
    rejected: int = 0  # malformed rows left out of `path`
    rejects_path: Path | None = None  # where the rejected rows were written


def _repair(layout: FileLayout, header: list[str], line: str) -> list[str] | None:
    """Re-read a malformed row honouring double-quoted values; None if that doesn't fix it.

    Quotes are honoured ONLY for rows that are malformed without them. In a
    well-formed row a leading quote is part of the value (company names such as
    "K" LINE), so ordinary rows are never re-read this way.
    """
    try:
        reader = csv.reader([line.rstrip(LINE_ENDINGS)], delimiter=DELIMITER, quotechar=QUOTE, strict=True)
        fields = next(reader)
    except csv.Error:
        return None
    if len(fields) != len(header):
        return None
    normalized = [h.strip().lower() for h in header]
    for column, pattern in layout.repair_checks:
        if column in normalized and not re.fullmatch(pattern, fields[normalized.index(column)]):
            return None
    # The cleaned file is tab-delimited and unquoted, so a tab inside a value
    # becomes a space.
    return [field.replace(DELIMITER, " ") for field in fields]


def _classify(layout: FileLayout, header: list[str], line: str) -> tuple[str, str]:
    """Return ("ok" | "repaired" | "rejected", line to write)."""
    if line.count(DELIMITER) == len(header) - 1:
        return "ok", line  # fast path: almost every row
    repaired = _repair(layout, header, line)
    if repaired is not None:
        return "repaired", DELIMITER.join(repaired) + "\n"
    return "rejected", line


def check_rows(
    path: Path,
    layout: FileLayout,
    header: list[str],
    work_dir: Path,
    max_rejected_rows: int = DEFAULT_MAX_REJECTED_ROWS,
) -> RowCheck:
    """Count data rows and deal with rows whose field count differs from the header.

    * A row whose extra field comes from a double-quoted value containing a tab
      is repaired: quotes removed, the tab replaced by a space. Only when the
      re-read row has exactly the header's field count and passes the layout's
      repair_checks (e.g. num.value is still numeric).
    * Any other malformed row is rejected: left out of the load and written,
      with its line number, to `<work_dir>/<file>.rejected.txt` for inspection.
    * More than `max_rejected_rows` rejects fails the file: that many broken rows
      means a format change or a corrupt download, not a few bad filings.

    Clean files (the normal case) are loaded as they are. Otherwise a cleaned
    copy is written to `work_dir` and that copy is loaded. The returned row
    count is what the warehouse must report loading, so reconciliation still
    holds either way.
    """
    rows = repaired = rejected = 0
    first_rejects: list[int] = []
    with path.open("r", encoding=ENCODING, newline="") as handle:
        handle.readline()  # header
        for line_number, line in enumerate(handle, start=2):
            kind, _ = _classify(layout, header, line)
            if kind == "rejected":
                rejected += 1
                if len(first_rejects) < 5:
                    first_rejects.append(line_number)
            else:
                rows += 1
                repaired += kind == "repaired"

    if rejected > max_rejected_rows:
        lines = ", ".join(str(n) for n in first_rejects)
        raise LayoutError(
            f"{path}: {rejected} row(s) do not have {len(header)} tab-separated fields "
            f"(lines {lines}{', ...' if rejected > len(first_rejects) else ''}); the limit is "
            f"{max_rejected_rows}. An embedded tab or line break shifts values into the wrong "
            "columns, so these rows cannot be loaded as they are. Inspect those lines; if "
            "they are genuine one-off defects, raise the limit with --max-rejected-rows."
        )
    clean_path = work_dir / path.name
    rejects_path = work_dir / f"{path.stem}.rejected.txt"
    rejects_path.unlink(missing_ok=True)  # never leave a stale file from an earlier run
    if not repaired and not rejected:
        return RowCheck(path=path, rows=rows)
    if clean_path.resolve() == path.resolve():
        raise ValueError(f"work_dir must differ from the directory of {path}")

    work_dir.mkdir(parents=True, exist_ok=True)
    rejected_lines: list[str] = []
    with (
        path.open("r", encoding=ENCODING, newline="") as src,
        clean_path.open("w", encoding=ENCODING, newline="") as clean,
    ):
        header_line = src.readline()
        clean.write(header_line)
        for line_number, line in enumerate(src, start=2):
            kind, output = _classify(layout, header, line)
            if kind == "rejected":
                rejected_lines.append(f"{line_number}{DELIMITER}{line.rstrip(LINE_ENDINGS)}\n")
            else:
                clean.write(output if output.endswith("\n") else output + "\n")
    if rejected_lines:
        with rejects_path.open("w", encoding=ENCODING, newline="") as rejects:
            rejects.write("line_number" + DELIMITER + header_line)
            rejects.writelines(rejected_lines)
    return RowCheck(
        path=clean_path,
        rows=rows,
        repaired=repaired,
        rejected=rejected,
        rejects_path=rejects_path if rejected else None,
    )
