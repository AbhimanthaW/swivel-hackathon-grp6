"""CSV reading and canonical row access.

Responsibilities stop at "give me rows keyed by canonical field name".
No domain knowledge, no validation policy.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator, Mapping

#: Anything that can be handed to the reader.
TableSource = str | bytes | Path | IO[str] | IO[bytes]

_CANDIDATE_DELIMITERS = ",;\t|"
#: csv defaults to 128 KB; resident emails pasted into a cell can exceed that.
_MAX_FIELD_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class RawRow:
    """One physical CSV record, before any mapping or normalization."""

    row_number: int
    values: Mapping[str, str]
    #: Values past the last header (``csv`` puts these under the ``None`` key).
    overflow: tuple[str, ...] = ()
    #: Headers present but absent from this record.
    missing_fields: tuple[str, ...] = ()

    @property
    def is_ragged(self) -> bool:
        return bool(self.overflow or self.missing_fields)

    @property
    def is_blank(self) -> bool:
        return not any(v.strip() for v in self.values.values())


@dataclass
class RawTable:
    headers: list[str] = field(default_factory=list)
    rows: list[RawRow] = field(default_factory=list)
    #: Headers that appear more than once, after normalization.
    duplicate_headers: list[str] = field(default_factory=list)
    delimiter: str = ","

    @property
    def is_empty(self) -> bool:
        return not self.headers


def _decode(source: TableSource) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8-sig", errors="replace")
    if isinstance(source, bytes):
        return source.decode("utf-8-sig", errors="replace")
    if isinstance(source, str):
        return source
    data = source.read()
    if isinstance(data, bytes):
        return data.decode("utf-8-sig", errors="replace")
    return data


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_CANDIDATE_DELIMITERS).delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most often in the header line.
        header = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {d: header.count(d) for d in _CANDIDATE_DELIMITERS}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


def read_table(source: TableSource) -> RawTable:
    """Read a delimited text file into raw rows.

    Handles BOMs, quoted multi-line fields, delimiter variation and ragged
    rows. Row numbers are 1-based record ordinals, so the first data row is 2
    (matching what a user sees in a spreadsheet for well-formed files).
    """
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(_MAX_FIELD_SIZE)
    try:
        text = _decode(source).lstrip("\ufeff")
        if not text.strip():
            return RawTable()

        delimiter = _sniff_delimiter(text[:8192])
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return RawTable()

        headers = [h.strip().lstrip("\ufeff") for h in raw_headers]
        # Disambiguate blank/repeated headers so no data is silently dropped.
        seen: dict[str, int] = {}
        duplicates: list[str] = []
        unique_headers: list[str] = []
        for index, header in enumerate(headers):
            name = header or f"column_{index + 1}"
            if name in seen:
                duplicates.append(name)
                seen[name] += 1
                name = f"{name}__{seen[name]}"
            else:
                seen[name] = 0
            unique_headers.append(name)

        rows: list[RawRow] = []
        width = len(unique_headers)
        for ordinal, record in enumerate(reader, start=2):
            if not record:
                continue
            values = {h: (record[i] if i < len(record) else "") for i, h in enumerate(unique_headers)}
            overflow = tuple(record[width:]) if len(record) > width else ()
            missing = tuple(unique_headers[len(record):]) if len(record) < width else ()
            rows.append(
                RawRow(
                    row_number=ordinal,
                    values=values,
                    overflow=overflow,
                    missing_fields=missing,
                )
            )

        return RawTable(
            headers=unique_headers,
            rows=rows,
            duplicate_headers=duplicates,
            delimiter=delimiter,
        )
    finally:
        csv.field_size_limit(previous_limit)


class RowView:
    """Canonical-field accessor over a raw row.

    Parsers read ``view["description"]``; they never see a council header.
    """

    __slots__ = ("_row", "_mapping")

    def __init__(self, row: RawRow, mapping: Mapping[str, str]) -> None:
        self._row = row
        self._mapping = mapping

    @property
    def row_number(self) -> int:
        return self._row.row_number

    @property
    def raw(self) -> Mapping[str, str]:
        return self._row.values

    @property
    def is_blank(self) -> bool:
        return self._row.is_blank

    @property
    def is_ragged(self) -> bool:
        return self._row.is_ragged

    @property
    def overflow(self) -> tuple[str, ...]:
        return self._row.overflow

    def has(self, canonical_field: str) -> bool:
        return canonical_field in self._mapping

    def __getitem__(self, canonical_field: str) -> str | None:
        header = self._mapping.get(canonical_field)
        if header is None:
            return None
        value = self._row.values.get(header)
        return value if value is not None else None

    def first(self, *canonical_fields: str) -> str | None:
        """First non-blank value across several canonical fields."""
        for name in canonical_fields:
            value = self[name]
            if value is not None and value.strip():
                return value
        return None


def iter_rows(table: RawTable, mapping: Mapping[str, str]) -> Iterator[RowView]:
    """Yield canonical row views over a table."""
    for row in table.rows:
        yield RowView(row, mapping)
