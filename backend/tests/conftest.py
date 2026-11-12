"""Synthetic fixtures.

Deliberately NOT derived from the council's sample exports: the parser must
work independently of today's exact rows. A single opt-in test exercises the
real files if they happen to be present.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion import IngestionService  # noqa: E402

REPORTS_CSV = """report_id,channel,received_at,reporter_name,reporter_contact,location_text,latitude,longitude,description,category,urgency,photo,status
MR-000001,phone,2026-09-08T08:38:07,Alex Fernando,0114425687,"Havelock Road, by the bus halt",,,Surface water is not draining away.,,,,new
MR-000002,web_form,08/09/2026 09:28,Bea Silva,bea.silva@example.com,Temple Ln,6.884565,79.866178,"The lighting on our lane has been out for weeks.",Street lighting,High,IMG_0001.jpg,triaged
MR-000003,email,2026-09-08T17:12:50,Cara Perera,cara.perera@example.com,Kirula Road,,,"There is a pothole in front of the pharmacy.

Regards,
Cara Perera",,,,new
MR-000004,mobile_app,2026-09-08T18:45:06,,,,,,drain blocked,Drains,,,open
"""

ASSETS_CSV = """road_name,also_known_as,road_class,ward,asset_type,asset_id,nearest_facility,facility_distance_m
Temple Lane,the road by the temple,residential,Havelock Town,streetlight,MMC-STR-0001,,
Temple Lane,the road by the temple,residential,Havelock Town,drain,MMC-DRA-0001,,
Havelock Road,,main road,Havelock Town,footpath,MMC-FOO-0001,Central College,180
Kirula Road,,residential,Thimbirigasyaya,bus shelter,MMC-BUS-0001,,
"""

JOBS_CSV = """job_id,completed_date,crew,work_type,road_name,notes
JOB-0001,2026-08-25,Lighting & Street Furniture,Streetlight column repair,Temple Lane,Completed.
JOB-0002,2026-09-01,Drainage,Drain clearing,Kirula Road,Closed.
JOB-0003,2026-09-07,Road Surface,Pothole patching,Havelock Road,"Partial, returned next day."
"""


@pytest.fixture
def service() -> IngestionService:
    return IngestionService()


@pytest.fixture
def reports_csv() -> str:
    return REPORTS_CSV


@pytest.fixture
def assets_csv() -> str:
    return ASSETS_CSV


@pytest.fixture
def jobs_csv() -> str:
    return JOBS_CSV


@pytest.fixture(scope="session")
def council_dataset_dir() -> Path:
    """The real council export, if the developer has it locally."""
    path = Path.home() / "Downloads" / "Challenge03-Dataset"
    if not (path / "reports.csv").exists():
        pytest.skip("council sample dataset not present")
    return path
