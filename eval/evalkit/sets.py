"""Loaders for the kit scenarios and the eval sets (JSONL)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evalkit.paths import SETS_DIR, SIIS_PATH


@dataclass(frozen=True)
class KitRow:
    row_id: str
    query: str  # original_query exactly as the kit ships it (numbering and quotes included)
    siis: dict | str | None


def load_kit(path: Path = SIIS_PATH) -> list[KitRow]:
    data = json.loads(path.read_text())
    return [KitRow(r["id"], r["original_query"], r.get("siis_response")) for r in data["responses"]]


def read_jsonl(path: Path) -> list[dict]:
    """Blank lines are skipped; a bad line raises with its line number."""
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{n}: invalid JSON ({e.msg})") from e
    return rows


def load_set(name: str) -> list[dict]:
    """name is one of paraphrases, near_miss, unseen, adversarial."""
    return read_jsonl(SETS_DIR / f"{name}.jsonl")
