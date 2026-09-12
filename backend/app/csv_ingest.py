"""CSV ingestion for the three mandatory import roles. Column names are not
prescribed (docs/BACKEND-HANDOFF.md #1) so headers are matched against a set
of accepted aliases, case/whitespace-insensitively.
"""
import io
import pandas as pd

CSV_MAX_ROWS = 20000

ALIASES = {
    "reports": {
        "report_id": ["report_id", "id", "ref", "reference"],
        "location_text": ["location_text", "location", "address", "site"],
        "category": ["category", "type", "issue_type"],
        "urgency": ["urgency", "severity"],
        "received_at": ["received_at", "date", "timestamp", "reported_at", "created_at"],
        "description": ["description", "details", "notes", "comment", "complaint"],
        "channel": ["channel", "source", "via"],
    },
    "assets": {
        "asset_id": ["asset_id", "id"],
        "asset_type": ["asset_type", "type"],
        "road_name": ["road_name", "road", "street", "street_name"],
        "road_class": ["road_class", "class", "classification"],
        "ward": ["ward", "zone", "area"],
        "nearest_facility": ["nearest_facility", "facility", "landmark"],
    },
    "jobs_history": {
        "job_id": ["job_id", "id"],
        "completed_at": ["completed_at", "date", "finished_at"],
        "crew_name": ["crew_name", "crew"],
        "work_type": ["work_type", "type", "category"],
        "notes": ["notes", "details", "description"],
        "location": ["location", "road_name", "road", "site", "address"],
    },
}


class CsvValidationError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def _normalize_columns(df, role):
    lookup = {str(c).strip().lower(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for canonical, aliases in ALIASES[role].items():
        found = next((lookup[a] for a in aliases if a in lookup), None)
        out[canonical] = df[found] if found is not None else None
    return out


def parse_csv(role: str, raw: bytes):
    """Parses and validates one uploaded file for `role`.

    Returns (normalized_dataframe, row_count, warnings). Raises
    CsvValidationError on structural problems the caller should surface as a
    422/invalid file state rather than a 500.
    """
    if not raw:
        raise CsvValidationError("EMPTY_FILE", "The uploaded file is empty.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception as exc:
            raise CsvValidationError("BAD_ENCODING", "Could not decode the file as text.") from exc
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=True)
    except Exception as exc:
        raise CsvValidationError("BAD_CSV", f"The file could not be parsed as CSV: {exc}") from exc
    if df.empty:
        raise CsvValidationError("NO_ROWS", "The CSV has no data rows.")
    if len(df) > CSV_MAX_ROWS:
        raise CsvValidationError("TOO_MANY_ROWS", f"The CSV has more than {CSV_MAX_ROWS} rows.")

    normalized = _normalize_columns(df, role)
    warnings = []
    if role == "reports":
        if normalized["location_text"].isna().all() and normalized["description"].isna().all():
            raise CsvValidationError(
                "NO_USABLE_COLUMNS",
                "No location or description column could be identified in this reports file.",
            )
        if "report_id" not in df.columns and normalized["report_id"].isna().all():
            normalized["report_id"] = [f"row-{i}" for i in range(len(normalized))]
        else:
            normalized["report_id"] = normalized["report_id"].fillna("")
            blank = normalized["report_id"] == ""
            if blank.any():
                normalized.loc[blank, "report_id"] = [f"row-{i}" for i in normalized.index[blank]]
        if normalized["report_id"].duplicated().any():
            warnings.append("Duplicate report_id values were found; rows are still tracked individually.")
    if role == "assets" and normalized["road_name"].isna().all():
        warnings.append("No road_name-like column found; location/asset context will be limited.")
    if role == "jobs_history" and normalized["location"].isna().all():
        warnings.append("No location-like column found in jobs history; recurrence context will be limited.")

    return normalized, len(df), warnings
