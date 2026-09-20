"""Offline: clean catalog -> data/build/screengraph.json.

Run from api/:  python scripts/build_screengraph.py
The API loads this file at boot instead of re-clustering the catalog.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.screengraph import load_graph, save_graph

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data" / "kit" / "deeplinks.json"
OUT = ROOT / "data" / "build" / "screengraph.json"


def main() -> None:
    nodes = load_graph(CATALOG)
    save_graph(nodes, OUT)
    buttons = sum(len(ids) for node in nodes for ids in node.entries_by_polarity.values())
    print(f"{buttons} catalog entries -> {len(nodes)} screens")
    print(f"written to {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
