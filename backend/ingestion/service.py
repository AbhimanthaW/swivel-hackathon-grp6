"""Public entry point for the ingestion subsystem.

The rest of the backend should import from here (or from ``ingestion``) and
nothing else. Nothing above this layer needs to know that the sources are CSV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .assets import ASSETS_PARSER, build_road_directory
from .jobs import JOBS_PARSER, coverage_window
from .models import (
    CouncilAsset,
    CoverageWindow,
    IngestionResult,
    Issue,
    IssueCode,
    ResidentReport,
    RoadRecord,
    Severity,
    SourceType,
    WorkJob,
)
from .pipeline import SourceParser, ingest_source, ingest_table
from .reader import RawTable, TableSource, read_table
from .reports import REPORTS_PARSER
from .sanitize import ReportAnalysisInput, to_analysis_inputs
from .validation import DetectionResult, detect_source_type

_PARSERS: Mapping[SourceType, SourceParser[Any]] = {
    SourceType.REPORTS: REPORTS_PARSER,
    SourceType.ASSETS: ASSETS_PARSER,
    SourceType.JOBS: JOBS_PARSER,
}


@dataclass(frozen=True)
class IngestedBundle:
    """The three exports ingested together, plus cheap derived views."""

    reports: IngestionResult[ResidentReport]
    assets: IngestionResult[CouncilAsset]
    jobs: IngestionResult[WorkJob]

    @property
    def roads(self) -> list[RoadRecord]:
        """De-duplicated road directory built from the asset export."""
        return build_road_directory(self.assets.records)

    @property
    def jobs_coverage(self) -> CoverageWindow:
        """Observed date span of the jobs export - measured, not assumed."""
        return coverage_window(self.jobs.records)

    @property
    def issues(self) -> list[Issue]:
        return [*self.reports.issues, *self.assets.issues, *self.jobs.issues]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in (self.reports, self.assets, self.jobs))

    def analysis_inputs(self, *, require_content: bool = True) -> list[ReportAnalysisInput]:
        """PII-safe projection of the reports, ready for the LLM stage."""
        return to_analysis_inputs(self.reports.records, require_content=require_content)

    def summary(self) -> dict[str, Any]:
        """Compact status payload for the upload/processing UI."""
        return {
            "ok": self.ok,
            "sources": {
                result.source_type.value: {
                    "source_name": result.source_name,
                    "rows": result.row_count,
                    "accepted": result.accepted_count,
                    "rejected": result.rejected_count,
                    "errors": len(result.errors),
                    "warnings": len(result.warnings),
                    "quality": result.quality.model_dump(),
                }
                for result in (self.reports, self.assets, self.jobs)
            },
            "derived": {
                "distinct_roads": len(self.roads),
                "jobs_coverage_days": self.jobs_coverage.span_days,
            },
        }


class IngestionService:
    """Ingests council CSV exports into canonical domain objects."""

    def __init__(self, *, verify_source_type: bool = True) -> None:
        self.verify_source_type = verify_source_type

    # -- single source ----------------------------------------------------

    def ingest(
        self,
        source: TableSource,
        source_type: SourceType,
        *,
        source_name: str | None = None,
        ingested_at: datetime | None = None,
    ) -> IngestionResult[Any]:
        """Ingest one export of a known type.

        This is the primary API: the caller (the upload endpoint) knows which
        slot the file came from and should always say so.
        """
        return ingest_source(
            source,
            _PARSERS[source_type],
            source_name=source_name,
            verify_source_type=self.verify_source_type,
            ingested_at=ingested_at,
        )

    def ingest_path(
        self,
        path: str | Path,
        source_type: SourceType,
        *,
        ingested_at: datetime | None = None,
    ) -> IngestionResult[Any]:
        path = Path(path)
        return self.ingest(
            path, source_type, source_name=path.name, ingested_at=ingested_at
        )

    def ingest_auto(
        self,
        source: TableSource,
        *,
        source_name: str | None = None,
        ingested_at: datetime | None = None,
    ) -> IngestionResult[Any]:
        """Ingest a file whose type is inferred from its columns.

        A convenience for CLI/exploration. Filenames are never consulted. If
        the type cannot be determined confidently, an empty failed result is
        returned rather than a guess.
        """
        table = read_table(source)
        detection = detect_source_type(table.headers)
        if detection.source_type is None:
            return _undetected_result(table, source_name, detection)
        return ingest_table(
            table,
            _PARSERS[detection.source_type],
            source_name=source_name,
            verify_source_type=False,
            ingested_at=ingested_at,
        )

    def identify(self, source: TableSource) -> DetectionResult:
        """Inspect a file's columns and report what it looks like."""
        return detect_source_type(read_table(source).headers)

    # -- all three --------------------------------------------------------

    def ingest_bundle(
        self,
        *,
        reports: TableSource,
        assets: TableSource,
        jobs: TableSource,
        reports_name: str | None = None,
        assets_name: str | None = None,
        jobs_name: str | None = None,
    ) -> IngestedBundle:
        """Ingest the three uploads that the UI collects."""
        ingested_at = datetime.now(timezone.utc)
        return IngestedBundle(
            reports=self.ingest(
                reports,
                SourceType.REPORTS,
                source_name=reports_name,
                ingested_at=ingested_at,
            ),
            assets=self.ingest(
                assets,
                SourceType.ASSETS,
                source_name=assets_name,
                ingested_at=ingested_at,
            ),
            jobs=self.ingest(
                jobs, SourceType.JOBS, source_name=jobs_name, ingested_at=ingested_at
            ),
        )

    def ingest_directory(self, directory: str | Path) -> IngestedBundle:
        """Ingest a directory by identifying each CSV from its columns.

        Convenience for local development and tests only; the API should use
        :meth:`ingest_bundle` with explicit slots.
        """
        directory = Path(directory)
        found: dict[SourceType, Path] = {}
        for path in sorted(directory.glob("*.csv")):
            detection = detect_source_type(read_table(path).headers)
            if detection.source_type and detection.source_type not in found:
                found[detection.source_type] = path

        missing = [t.value for t in SourceType if t not in found]
        if missing:
            raise FileNotFoundError(
                f"Could not identify a {', '.join(missing)} export in {directory}"
            )
        return self.ingest_bundle(
            reports=found[SourceType.REPORTS],
            assets=found[SourceType.ASSETS],
            jobs=found[SourceType.JOBS],
            reports_name=found[SourceType.REPORTS].name,
            assets_name=found[SourceType.ASSETS].name,
            jobs_name=found[SourceType.JOBS].name,
        )


def _undetected_result(
    table: RawTable, source_name: str | None, detection: DetectionResult
) -> IngestionResult[Any]:
    candidates = ", ".join(
        f"{c.source_type.value}={c.score:.2f}" for c in detection.candidates
    )
    return IngestionResult[Any](
        source_type=SourceType.REPORTS,
        source_name=source_name,
        row_count=len(table.rows),
        detected_columns=list(table.headers),
        rejected_count=len(table.rows),
        issues=[
            Issue(
                code=IssueCode.SOURCE_TYPE_UNDETECTED,
                severity=Severity.ERROR,
                message=(
                    f"Could not identify this file: {detection.reason}. "
                    f"Scores: {candidates or 'none'}. "
                    "Supply source_type explicitly."
                ),
            )
        ],
    )
