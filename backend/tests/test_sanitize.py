"""The PII boundary between ingestion and the LLM stage."""

from __future__ import annotations

from ingestion import (
    ReportAnalysisInput,
    find_pii_leaks,
    ingest_reports,
    redact_text,
    to_analysis_input,
    to_analysis_inputs,
)


def test_analysis_input_has_no_reporter_fields(reports_csv: str) -> None:
    report = ingest_reports(reports_csv).records[0]
    analysis = to_analysis_input(report)

    payload = analysis.model_dump()
    assert "reporter" not in payload
    assert "reporter_name" not in payload
    assert "photo_reference" not in payload, "filenames can encode device/user info"
    assert find_pii_leaks(analysis, ["Alex Fernando", "0114425687"]) == []


def test_structural_pii_is_dropped_for_every_report(reports_csv: str) -> None:
    result = ingest_reports(reports_csv)
    forbidden = [
        value
        for record in result.records
        for value in (record.reporter.name, record.reporter.raw_contact)
        if value
    ]
    inputs = to_analysis_inputs(result.records)

    assert find_pii_leaks(inputs, forbidden) == []


def test_signature_block_is_removed_from_description(reports_csv: str) -> None:
    """Dropping the reporter column is not enough - the name is in the text."""
    report = next(
        r for r in ingest_reports(reports_csv).records if r.source_report_id == "MR-000003"
    )
    assert "Cara Perera" in report.description

    analysis = to_analysis_input(report)
    assert "Cara Perera" not in analysis.description
    assert "Regards" not in analysis.description
    assert analysis.description == "There is a pothole in front of the pharmacy."
    assert "signoff_block" in analysis.redactions_applied


def test_analysis_input_keeps_the_information_the_model_needs(reports_csv: str) -> None:
    report = ingest_reports(reports_csv).records[1]
    analysis = to_analysis_input(report)

    assert analysis.report_id == report.ingest_id
    assert analysis.source_report_id == "MR-000002"
    assert analysis.description
    assert analysis.location_text == "Temple Ln"
    assert analysis.location_hint == "temple lane"
    assert analysis.coordinates is not None
    assert analysis.reported_at is not None
    assert analysis.category_hint == "Street lighting"
    assert analysis.has_photo is True


def test_prompt_dict_is_minimal(reports_csv: str) -> None:
    analysis = to_analysis_input(ingest_reports(reports_csv).records[1])
    prompt = analysis.to_prompt_dict()

    assert set(prompt) == {
        "report_id",
        "description",
        "location_text",
        "reported_at",
        "channel",
        "category_hint",
        "urgency_hint",
        "has_photo",
    }
    assert find_pii_leaks(prompt, ["Bea Silva", "bea.silva@example.com"]) == []


def test_redacts_inline_email_and_phone() -> None:
    text = "Call me on 0771234567 or write to resident@example.com about the drain."
    redacted, applied = redact_text(text)

    assert "0771234567" not in redacted
    assert "resident@example.com" not in redacted
    assert "[phone]" in redacted and "[email]" in redacted
    assert set(applied) == {"phone", "email"}


def test_redacts_national_id_numbers() -> None:
    redacted, applied = redact_text("My NIC is 912345678V, please respond.")

    assert "912345678V" not in redacted
    assert "[id-number]" in redacted
    assert "id_number" in applied


def test_redacts_a_known_reporter_name_mentioned_inline() -> None:
    redacted, applied = redact_text(
        "Nilanthi Perera here, the light on our lane is out.", "Nilanthi Perera"
    )

    assert "Nilanthi" not in redacted
    assert "Perera" not in redacted
    assert "name" in applied


def test_short_name_fragments_do_not_blank_ordinary_words() -> None:
    redacted, _ = redact_text(
        "The road by the bus halt is flooded near the bin.", "Ann Roy"
    )

    assert redacted == "The road by the bus halt is flooded near the bin."


def test_house_numbers_are_not_mistaken_for_phone_numbers() -> None:
    redacted, applied = redact_text("Pothole outside 39 Kirula Road, near number 113.")

    assert redacted == "Pothole outside 39 Kirula Road, near number 113."
    assert applied == []


def test_mail_footer_is_removed() -> None:
    redacted, applied = redact_text("Drain is blocked.\nSent from my iPhone")

    assert redacted == "Drain is blocked."
    assert "mail_footer" in applied


def test_redaction_of_empty_text_is_a_no_op() -> None:
    assert redact_text(None) == (None, [])
    assert redact_text("") == ("", [])


def test_require_content_drops_useless_reports() -> None:
    csv = (
        "report_id,received_at,location_text,description\n"
        "MR-1,2026-09-08,Park Road,Blocked drain\n"
        "MR-2,2026-09-08,,\n"
    )
    records = ingest_reports(csv).records

    assert len(to_analysis_inputs(records)) == 2
    assert len(to_analysis_inputs(records, require_content=True)) == 1


def test_find_pii_leaks_actually_detects_leaks() -> None:
    """Guard the guard: a permissive auditor would make every test above pass."""
    leaky = {"description": "email me at someone@example.com or call 0771234567"}

    findings = find_pii_leaks(leaky)
    assert any("email" in f for f in findings)
    assert any("phone" in f for f in findings)
    assert find_pii_leaks({"reporter": {"name": "Ann"}})


def test_analysis_input_is_immutable(reports_csv: str) -> None:
    analysis = to_analysis_input(ingest_reports(reports_csv).records[0])

    assert isinstance(analysis, ReportAnalysisInput)
    assert analysis.model_config["frozen"] is True
