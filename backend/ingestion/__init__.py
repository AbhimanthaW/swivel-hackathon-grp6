"""Muthuwella Works Dispatch - CSV ingestion and normalization layer.

    external CSV export
        -> file identification      (validation.detect_source_type)
        -> schema inspection        (reader.read_table)
        -> field mapping            (mappings + validation.resolve_columns)
        -> validation               (validation)
        -> normalization            (normalizers)
        -> canonical domain objects (models)
        -> sanitized LLM input      (sanitize)

Nothing above this package should ever see a raw council column name.
"""

from .assets import build_road_directory, ingest_assets
from .jobs import coverage_window, ingest_jobs
from .mappings import (
    ASSETS_SCHEMA,
    JOBS_SCHEMA,
    REPORTS_SCHEMA,
    FieldSpec,
    Requirement,
    SourceSchema,
    get_schema,
)
from .models import (
    AssetType,
    CanonicalRecord,
    ContactKind,
    Coordinates,
    CouncilAsset,
    CoverageWindow,
    DataQualityReport,
    IngestionResult,
    Issue,
    IssueCode,
    RawLocation,
    ReportChannel,
    ReporterContact,
    ReportStatus,
    ResidentReport,
    RoadClass,
    RoadContext,
    RoadRecord,
    Severity,
    SourceMetadata,
    SourceType,
    UrgencyHint,
    WorkJob,
)
from .reports import ingest_reports
from .sanitize import (
    ReportAnalysisInput,
    find_pii_leaks,
    redact_text,
    to_analysis_input,
    to_analysis_inputs,
)
from .service import IngestedBundle, IngestionService
from .validation import DetectionResult, detect_source_type, resolve_columns

__all__ = [
    # service
    "IngestionService",
    "IngestedBundle",
    "ingest_reports",
    "ingest_assets",
    "ingest_jobs",
    # models
    "SourceType",
    "ResidentReport",
    "CouncilAsset",
    "RoadRecord",
    "RoadContext",
    "WorkJob",
    "CanonicalRecord",
    "IngestionResult",
    "DataQualityReport",
    "CoverageWindow",
    "Issue",
    "IssueCode",
    "Severity",
    "Coordinates",
    "RawLocation",
    "ReporterContact",
    "SourceMetadata",
    "ReportChannel",
    "ReportStatus",
    "UrgencyHint",
    "AssetType",
    "RoadClass",
    "ContactKind",
    # llm boundary
    "ReportAnalysisInput",
    "to_analysis_input",
    "to_analysis_inputs",
    "redact_text",
    "find_pii_leaks",
    # schema / detection
    "SourceSchema",
    "FieldSpec",
    "Requirement",
    "get_schema",
    "REPORTS_SCHEMA",
    "ASSETS_SCHEMA",
    "JOBS_SCHEMA",
    "detect_source_type",
    "resolve_columns",
    "DetectionResult",
    # derived views
    "build_road_directory",
    "coverage_window",
]
