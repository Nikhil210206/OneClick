"""Offline: BM25 + vector index over the deeplink catalog.

Run from api/:  python scripts/build_index.py
Writes data/build/catalog_vectors.npz so the API starts without embedding 577 entries (~12 s).
BM25 is rebuilt at boot: it costs milliseconds and keeps the artefact small.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import retrieval
from app.config import settings
from app.retrieval import dense

DATA = Path(settings.data_dir)
CATALOG = DATA / "kit" / "deeplinks.json"
OUT = DATA / "build" / "catalog_vectors.npz"


def main() -> None:
    start = time.time()
    docs = retrieval.load_catalog_docs(CATALOG)
    retrieval.build(docs)
    dense.save(OUT)
    print(f"embedded {len(docs)} catalog entries in {time.time() - start:.1f}s")
    print(f"written to {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
