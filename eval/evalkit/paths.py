"""Repo paths used by every eval script. Scripts run from the repo root: `python eval/<script>.py`."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
DATA_DIR = REPO_ROOT / "data"
KIT_DIR = DATA_DIR / "kit"
GOLD_PATH = DATA_DIR / "gold" / "deeplink_gold.jsonl"
SETS_DIR = REPO_ROOT / "eval" / "sets"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
RESULTS_JSONL = REPO_ROOT / "results.jsonl"

CATALOG_PATH = KIT_DIR / "deeplinks.json"
SIIS_PATH = KIT_DIR / "siis_responses.json"
SAMPLE_OUTPUT_PATH = KIT_DIR / "sample_output.json"
DEPENDENCIES_PATH = DATA_DIR / "dependencies.json"


def use_api_package() -> None:
    """Make `app.schema` importable. Only the official schema is used, never engine code."""
    if str(API_DIR) not in sys.path:
        sys.path.insert(0, str(API_DIR))
