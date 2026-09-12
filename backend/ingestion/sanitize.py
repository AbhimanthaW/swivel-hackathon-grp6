"""PII sanitization and the LLM analysis boundary.

This is the only supported way to get resident data out of ingestion and into
a model prompt:

    CSV -> ResidentReport -> ReportAnalysisInput -> LLM

Dropping the ``reporter`` object is necessary but NOT sufficient: the sample
export contains email sign-off blocks that repeat the resident's name inside
``description``, and residents mention phone numbers and relatives in free
text. So both structural removal and text redaction are applied.

No model or API call happens here. This module has no network dependencies.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import Coordinates, ReportChannel, ResidentReport, UrgencyHint
from .normalizers import EMAIL_PATTERN, NIC_PATTERN, PHONE_PATTERN, clean_text

EMAIL_PLACEHOLDER = "[email]"
PHONE_PLACEHOLDER = "[phone]"
NIC_PLACEHOLDER = "[id-number]"
NAME_PLACEHOLDER = "[name]"

#: Sign-off lines. Everything from the sign-off to the end of the text is
#: dropped, because that block is where names and contact details live.
_SIGNOFF_PATTERN = re.compile(
    r"\n\s*(?:kind\s+regards|best\s+regards|many\s+thanks|regards|thanks|thank\s+you"
    r"|sincerely|yours\s+(?:faithfully|sincerely)|cheers)\b[\s\S]*$",
    re.IGNORECASE,
)
#: Inline salutations that leak the sender's name.
_SENT_FROM_PATTERN = re.compile(r"\n\s*sent\s+from\s+my\b.*$", re.IGNORECASE | re.MULTILINE)


class ReportAnalysisInput(BaseModel):
    """The PII-safe projection of a report, ready for a model prompt.

    Deliberately excludes reporter name, email, phone, raw contact and the
    photo filename (which frequently encodes device/user information).
    """

    model_config = ConfigDict(frozen=True)

    report_id: str = Field(description="Internal ingest id - stable and non-personal.")
    source_report_id: str | None = None
    description: str | None = Field(
        default=None, description="Resident text with PII patterns redacted."
    )
    location_text: str | None = Field(
        default=None, description="Location text with PII patterns redacted."
    )
    location_hint: str | None = None
    coordinates: Coordinates | None = None
    reported_at: datetime | None = None
    channel: ReportChannel = ReportChannel.UNKNOWN
    category_hint: str | None = None
    urgency_hint: UrgencyHint | None = None
    has_photo: bool = False
    redactions_applied: tuple[str, ...] = ()

    def to_prompt_dict(self) -> dict[str, object]:
        """Minimal dict for prompt construction. Drops internal bookkeeping."""
        return {
            "report_id": self.report_id,
            "description": self.description,
            "location_text": self.location_text,
            "reported_at": self.reported_at.isoformat() if self.reported_at else None,
            "channel": self.channel.value,
            "category_hint": self.category_hint,
            "urgency_hint": self.urgency_hint.value if self.urgency_hint else None,
            "has_photo": self.has_photo,
        }


def _name_patterns(name: str | None) -> list[re.Pattern[str]]:
    """Patterns matching a known reporter name and its individual parts.

    Parts shorter than 4 characters are skipped so that common Sri Lankan
    name fragments do not blank out ordinary words.
    """
    if not name:
        return []
    parts = [p for p in re.split(r"\s+", name.strip()) if len(p) >= 4]
    candidates = [name.strip(), *parts]
    return [
        re.compile(rf"\b{re.escape(c)}\b", re.IGNORECASE)
        for c in dict.fromkeys(candidates)
        if c
    ]


def redact_text(text: str | None, known_name: str | None = None) -> tuple[str | None, list[str]]:
    """Remove PII patterns from free text.

    Returns the redacted text and the list of redaction kinds applied, so the
    data-quality report can show that sanitization actually did something.
    """
    if not text:
        return text, []

    applied: list[str] = []
    redacted = text

    stripped = _SIGNOFF_PATTERN.sub("", redacted)
    if stripped != redacted:
        applied.append("signoff_block")
        redacted = stripped

    stripped = _SENT_FROM_PATTERN.sub("", redacted)
    if stripped != redacted:
        applied.append("mail_footer")
        redacted = stripped

    for pattern, placeholder, label in (
        (EMAIL_PATTERN, EMAIL_PLACEHOLDER, "email"),
        (NIC_PATTERN, NIC_PLACEHOLDER, "id_number"),
        (PHONE_PATTERN, PHONE_PLACEHOLDER, "phone"),
    ):
        stripped = pattern.sub(placeholder, redacted)
        if stripped != redacted:
            applied.append(label)
            redacted = stripped

    for pattern in _name_patterns(known_name):
        stripped = pattern.sub(NAME_PLACEHOLDER, redacted)
        if stripped != redacted:
            if "name" not in applied:
                applied.append("name")
            redacted = stripped

    return clean_text(redacted), applied


def to_analysis_input(report: ResidentReport) -> ReportAnalysisInput:
    """Project one report into its PII-safe analysis form."""
    known_name = report.reporter.name
    description, description_redactions = redact_text(report.description, known_name)
    location_text, location_redactions = redact_text(report.location.text, known_name)

    return ReportAnalysisInput(
        report_id=report.ingest_id,
        source_report_id=report.source_report_id,
        description=description,
        location_text=location_text,
        location_hint=report.location.normalized_text,
        coordinates=report.location.coordinates,
        reported_at=report.reported_at,
        channel=report.channel,
        category_hint=report.category_raw,
        urgency_hint=report.urgency,
        has_photo=report.has_photo,
        redactions_applied=tuple(
            dict.fromkeys(description_redactions + location_redactions)
        ),
    )


def to_analysis_inputs(
    reports: Iterable[ResidentReport], *, require_content: bool = False
) -> list[ReportAnalysisInput]:
    """Project many reports.

    ``require_content=True`` drops reports with neither description nor
    location text, which are useless as model input and only cost tokens.
    """
    projected = (to_analysis_input(r) for r in reports)
    if not require_content:
        return list(projected)
    return [p for p in projected if p.description or p.location_text]


# --------------------------------------------------------------------------
# Test / audit helper
# --------------------------------------------------------------------------

_PII_FIELD_NAMES = frozenset(
    {"reporter", "reporter_name", "name", "email", "phone", "raw_contact", "contact"}
)


def find_pii_leaks(
    payload: object, forbidden_values: Sequence[str] = ()
) -> list[str]:
    """Audit a payload for PII field names, patterns or known literal values.

    Used by the tests, and available as a runtime assertion before any prompt
    is sent. Returns a list of human-readable findings (empty means clean).
    """
    findings: list[str] = []

    def _walk(node: object, path: str) -> None:
        if isinstance(node, BaseModel):
            _walk(node.model_dump(), path)
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and key.lower() in _PII_FIELD_NAMES:
                    findings.append(f"{path}.{key}: PII field name present")
                _walk(value, f"{path}.{key}")
            return
        if isinstance(node, (list, tuple, set)):
            for index, value in enumerate(node):
                _walk(value, f"{path}[{index}]")
            return
        if isinstance(node, str):
            if EMAIL_PATTERN.search(node):
                findings.append(f"{path}: email address present")
            if PHONE_PATTERN.search(node):
                findings.append(f"{path}: phone number present")
            if NIC_PATTERN.search(node):
                findings.append(f"{path}: id number present")
            lowered = node.lower()
            for value in forbidden_values:
                if value and value.lower() in lowered:
                    findings.append(f"{path}: contains forbidden value {value!r}")

    _walk(payload, "$")
    return findings
