"""Backend-owned configuration: crew roster and LLM model. Not derived from
any uploaded export, per docs/BACKEND-HANDOFF.md.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("DISPATCH_MODEL", "claude-haiku-4-5-20251001")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024

CREWS = [
    {
        "id": "crew-drainage",
        "name": "Drainage",
        "capabilities": ["Drainage"],
        "description": "Blocked drains, flooding, culverts and stormwater faults.",
    },
    {
        "id": "crew-road-surface",
        "name": "Road Surface",
        "capabilities": ["Road Surface"],
        "description": "Potholes, road collapse and surface defects.",
    },
    {
        "id": "crew-lighting",
        "name": "Lighting & Street Furniture",
        "capabilities": ["Lighting & Street Furniture"],
        "description": "Street lighting, poles, signage and public furniture faults.",
    },
]

CREW_BY_CAPABILITY = {c["capabilities"][0]: c["id"] for c in CREWS}
CREW_IDS = {c["id"] for c in CREWS}


def crew_id_for_work_type(work_type: str):
    return CREW_BY_CAPABILITY.get(work_type)
