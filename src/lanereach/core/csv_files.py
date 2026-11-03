"""CSV in and out: tolerant reading of spreadsheets exported by people, safe writing for Excel."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator, Sequence

from lanereach.core.errors import BusinessRuleViolationError

# Cells starting with these are formulas to Excel; prefixing a quote keeps them as text
# (CSV/formula injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def read_csv_rows(
    content: bytes, required: Sequence[str], *, max_rows: int
) -> Iterator[tuple[int, dict[str, str]]]:
    """Rows of an uploaded CSV with lower-cased, trimmed column names and values.

    Accepts UTF-8 (with or without BOM) separated by commas or semicolons (Excel in many
    locales uses semicolons).

    Args:
        content: File bytes.
        required: Columns that must exist.
        max_rows: Rows beyond this are not read.

    Yields:
        (spreadsheet row number, row) pairs; the header is row 1.

    Raises:
        BusinessRuleViolationError: For non-UTF-8 files, missing columns or too many rows.
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BusinessRuleViolationError("Save the file as UTF-8 CSV.") from exc
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text.splitlines()[0] if text else ",", delimiters=",;"
        )
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = {(name or "").strip().lower() for name in reader.fieldnames or []}
    missing = [column for column in required if column not in columns]
    if missing:
        raise BusinessRuleViolationError("Missing CSV columns: " + ", ".join(missing))
    for number, raw in enumerate(reader, start=2):
        if number > max_rows + 1:
            raise BusinessRuleViolationError(
                f"The file has more than {max_rows} rows; split it into smaller files."
            )
        yield (
            number,
            {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            },
        )


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def write_csv(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    """A CSV file (UTF-8 with BOM so Excel shows accents and Arabic correctly).

    Args:
        columns: Header.
        rows: Values per row.

    Returns:
        The file bytes.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")
