"""Value-level normalization primitives.

Everything here is deliberately small, pure and side-effect free. Parsers call
these; nothing here knows about CSV headers or the domain models.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Final

# Tokens that different council systems use to mean "no value".
NULL_TOKENS: Final[frozenset[str]] = frozenset(
    {"", "-", "--", "n/a", "na", "none", "null", "nil", "unknown", "unspecified", "?"}
)

# Accepted datetime formats, most specific first. Slash formats are day-first:
# the sample export uses DD/MM/YYYY (see docs/ASSUMPTIONS.md).
DATETIME_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
)

_WS_RUN = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Expanded only to build a *hint*; the original text is always preserved.
_ROAD_ABBREVIATIONS: Final[dict[str, str]] = {
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "av": "avenue",
    "ln": "lane",
    "pl": "place",
    "cres": "crescent",
    "cr": "crescent",
    "dr": "drive",
    "gdns": "gardens",
    "mw": "mawatha",
    "mawata": "mawatha",
    "mw.": "mawatha",
    "sq": "square",
    "ct": "court",
    "ter": "terrace",
    "pde": "parade",
    "hwy": "highway",
}


def is_null_token(value: str | None) -> bool:
    """True when a raw cell should be treated as absent."""
    if value is None:
        return True
    return value.strip().lower() in NULL_TOKENS


def clean_text(value: str | None) -> str | None:
    """Trim and normalize whitespace while preserving paragraph structure.

    Used for free text (descriptions) where line breaks carry meaning.
    """
    if is_null_token(value):
        return None
    assert value is not None
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_WS_RUN.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text).strip()
    return text or None


def clean_line(value: str | None) -> str | None:
    """Trim and collapse to a single line. Used for names, roads, labels."""
    text = clean_text(value)
    if text is None:
        return None
    return _WS_RUN.sub(" ", text.replace("\n", " ")).strip() or None


def slugify(value: str | None) -> str | None:
    """Stable lowercase key for free-form taxonomy values (crews, work types).

    ``&`` is expanded so that "Roads & Pavements" and "Roads and Pavements"
    produce the same key.
    """
    text = clean_line(value)
    if text is None:
        return None
    slug = _NON_ALNUM.sub("_", text.lower().replace("&", " and ")).strip("_")
    return slug or None


def normalize_location_hint(value: str | None) -> str | None:
    """Conservative, reversible normalization of a location string.

    Lowercases, collapses punctuation and expands common road-type
    abbreviations so that later matching has something stable to work with.
    Deliberately does NOT correct spelling or drop qualifiers such as
    "near number 13" - collapsing locations is the semantic stage's job.
    """
    text = clean_line(value)
    if text is None:
        return None
    text = text.lower().replace("&", " and ")
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    if not tokens:
        return None
    expanded = [_ROAD_ABBREVIATIONS.get(t, t) for t in tokens]
    return " ".join(expanded)


def parse_datetime(value: str | None) -> tuple[datetime | None, str | None]:
    """Parse a timestamp. Returns ``(value, error_message)``."""
    text = clean_line(value)
    if text is None:
        return None, None
    candidate = text.replace("Z", "").strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(candidate, fmt), None
        except ValueError:
            continue
    try:  # last resort: full ISO 8601 including offsets
        return datetime.fromisoformat(candidate), None
    except ValueError:
        return None, f"unrecognised date/time format: {text!r}"


def parse_date(value: str | None) -> tuple[date | None, str | None]:
    """Parse a calendar date. Returns ``(value, error_message)``."""
    parsed, error = parse_datetime(value)
    return (parsed.date() if parsed else None), error


def parse_float(value: str | None) -> tuple[float | None, str | None]:
    text = clean_line(value)
    if text is None:
        return None, None
    try:
        return float(text.replace(",", "")), None
    except ValueError:
        return None, f"expected a number, got {text!r}"


def parse_int(value: str | None) -> tuple[int | None, str | None]:
    parsed, error = parse_float(value)
    if parsed is None:
        return None, error
    return int(round(parsed)), None


def parse_bool(value: str | None) -> bool | None:
    text = clean_line(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"true", "yes", "y", "1"}:
        return True
    if lowered in {"false", "no", "n", "0"}:
        return False
    return None


def normalize_enum_value(value: str | None) -> str | None:
    """Canonical lookup key for enum alias tables."""
    text = clean_line(value)
    if text is None:
        return None
    return _NON_ALNUM.sub("_", text.lower()).strip("_") or None


EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"
)
# Sri Lankan formats: 0XXXXXXXXX, +94XXXXXXXXX, with optional spaces/dashes.
PHONE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])(?:\+94[\s-]?|0)(?:\d[\s-]?){8,9}\d(?![\w.])"
)
# National Identity Card: 9 digits + V/X, or 12 digits.
NIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b(?:\d{9}[VvXx]|\d{12})\b")


def looks_like_email(value: str | None) -> bool:
    text = clean_line(value)
    return bool(text and EMAIL_PATTERN.fullmatch(text))


def looks_like_phone(value: str | None) -> bool:
    text = clean_line(value)
    if not text:
        return False
    digits = re.sub(r"[\s()+-]", "", text)
    return digits.isdigit() and 7 <= len(digits) <= 15
