"""Reading an income statement out of a delimited text file.

The hard part of CSV is not the parsing, it is the bytes. Korean financial
exports are routinely CP949 (the Microsoft superset of EUC-KR) rather than
UTF-8, and Excel writes UTF-8 with a byte-order mark. Decoding CP949 bytes as
UTF-8 raises; decoding UTF-8 bytes as CP949 usually does **not** — it silently
produces mojibake. So the codecs are tried strict-UTF-8 first, and the order is
deliberate rather than incidental.

Once decoded, the file becomes a :class:`~app.adapters.ingest.grid.Grid` and
everything downstream is shared with the XLSX adapter.
"""

from __future__ import annotations

import csv as csv_module
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.adapters.ingest.grid import (
    ExtractOptions,
    Grid,
    GridCell,
    StatementNotFoundError,
    extract_statement,
)
from app.domain.extraction import ExtractedStatement

#: Tried in order. UTF-8 is strict, so a CP949 file fails it and falls through;
#: the reverse is not true, which is why UTF-8 must be tried first.
CANDIDATE_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig",  # Excel's UTF-8 export, with BOM
    "utf-8",
    "cp949",  # Microsoft Korean; a superset of euc-kr
    "euc-kr",
)

_DELIMITERS = ",;\t|"

#: Rows of preamble scanned for the presentation unit, e.g. ``(단위: 백만원)``.
_PREAMBLE_ROWS = 12

#: Spreadsheet-style column labels, so CSV provenance reads like XLSX
#: provenance and an auditor sees one convention rather than two.
_COLUMN_LABELS = [chr(ord("A") + i) for i in range(26)]


class EncodingDetectionError(ValueError):
    """The file could not be decoded with any supported encoding."""


@dataclass(frozen=True, slots=True)
class DecodedFile:
    text: str
    encoding: str


def decode_bytes(raw: bytes) -> DecodedFile:
    """Decode with the first candidate encoding that accepts the bytes."""
    for encoding in CANDIDATE_ENCODINGS:
        try:
            return DecodedFile(raw.decode(encoding), encoding)
        except UnicodeDecodeError:
            continue
    raise EncodingDetectionError(
        f"could not decode the file as any of {', '.join(CANDIDATE_ENCODINGS)}"
    )


def sniff_delimiter(text: str) -> str:
    """Pick the delimiter, preferring the one that yields consistent columns.

    ``csv.Sniffer`` is tried first but is unreliable on files with a title row
    and ragged preamble, which is exactly what a statement export looks like.
    The fallback counts candidates per line and takes the one producing the most
    columns consistently.
    """
    sample = "\n".join(text.splitlines()[:30])
    try:
        return csv_module.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv_module.Error:
        pass

    best, best_score = ",", 0
    for delimiter in _DELIMITERS:
        counts = [line.count(delimiter) for line in sample.splitlines() if line.strip()]
        if not counts:
            continue
        # Reward a delimiter that appears the same number of times on most lines.
        modal = max(set(counts), key=counts.count)
        score = modal * counts.count(modal)
        if modal > 0 and score > best_score:
            best, best_score = delimiter, score
    return best


def _indent_of(text: str) -> int:
    """CSV keeps no alignment metadata, so indentation is leading whitespace."""
    stripped = text.lstrip(" 　\t")
    return (len(text) - len(stripped)) // 2


def rows_to_grid(
    rows: Sequence[Sequence[str]],
    source_file: str,
    *,
    sheet: str | None = None,
) -> Grid:
    grid_rows: list[tuple[GridCell, ...]] = []
    for row_index, row in enumerate(rows, start=1):
        cells = tuple(
            GridCell(
                value=value if value.strip() else None,
                row=row_index,
                column=column_index,
                column_label=(
                    _COLUMN_LABELS[column_index - 1]
                    if column_index <= len(_COLUMN_LABELS)
                    else None
                ),
                indent=_indent_of(value),
            )
            for column_index, value in enumerate(row, start=1)
        )
        if cells:
            grid_rows.append(cells)

    preamble = tuple(
        value for row in rows[:_PREAMBLE_ROWS] for value in row if value and value.strip()
    )

    return Grid(
        source_file=source_file,
        rows=tuple(grid_rows),
        sheet=sheet,
        preamble=preamble,
    )


def read_income_statement(
    path: Path | str,
    *,
    options: ExtractOptions | None = None,
    encoding: str | None = None,
) -> ExtractedStatement:
    """Extract an income statement from a delimited text file.

    ``encoding`` overrides detection when the caller already knows it.
    """
    options = options or ExtractOptions()
    path = Path(path)
    raw = path.read_bytes()

    if encoding is not None:
        try:
            decoded = DecodedFile(raw.decode(encoding), encoding)
        except UnicodeDecodeError as exc:
            raise EncodingDetectionError(f"file is not valid {encoding}") from exc
    else:
        decoded = decode_bytes(raw)

    text = decoded.text
    if not text.strip():
        raise StatementNotFoundError("the file is empty")

    delimiter = sniff_delimiter(text)
    rows = list(csv_module.reader(text.splitlines(), delimiter=delimiter))
    if not rows:
        raise StatementNotFoundError("the file has no rows")

    return extract_statement(rows_to_grid(rows, path.name), options)


def looks_like_korean_encoding_damage(text: str) -> bool:
    """Heuristic for mojibake, used to explain a failure rather than to decide.

    CP949 bytes decoded as latin-1 produce runs of Latin-1 supplement
    characters where Hangul should be. Never used to pick an encoding — only to
    tell a user *why* their file did not parse.
    """
    return bool(re.search(r"[À-ÿ]{3,}", text)) and "가" not in text
