"""Local ONNX embeddings (ADR-005) + in-memory vector index. Shared by retrieval, cache and grounding."""

from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from app.config import settings

_model: TextEmbedding | None = None

# Module-level index, built once at startup (or in tests) via build().
_ids: list[str] = []
_matrix: np.ndarray | None = None  # shape (n_docs, dim), L2-normalized rows


def _get_model() -> TextEmbedding:
    """Load the ONNX embedding model once; downloads to the fastembed cache on first use."""
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=settings.embed_model)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts as L2-normalized vectors. Shared with cache (C2) and grounding (C6)."""
    if not texts:
        return []
    vectors = np.asarray(list(_get_model().embed(texts)), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)
    return vectors.tolist()


def _doc_text(fields: dict[str, str]) -> str:
    """One string per document; the embedder reads meaning, so no field weighting here."""
    return ". ".join(value for value in fields.values() if value)


def build(docs: list[dict]) -> None:
    """Build the vector index from documents shaped {"id": str, "fields": {field: text}}."""
    global _ids, _matrix
    _ids = [d["id"] for d in docs]
    _matrix = np.asarray(embed([_doc_text(d["fields"]) for d in docs]), dtype=np.float32)


def save(path: str | Path) -> None:
    """Write the vectors to disk so a container starts without re-embedding the catalog."""
    if _matrix is None:
        raise RuntimeError("dense.build() must be called before save()")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, ids=np.array(_ids, dtype=object), matrix=_matrix)


def load(path: str | Path) -> bool:
    """Load vectors written by save(). False when the file is absent or was built differently."""
    global _ids, _matrix
    path = Path(path)
    if not path.exists():
        return False
    payload = np.load(path, allow_pickle=True)
    _ids = [str(doc_id) for doc_id in payload["ids"]]
    _matrix = payload["matrix"].astype(np.float32)
    return True


def is_loaded() -> bool:
    return _matrix is not None


def search(query: str, k: int = 20) -> list[tuple[str, float]]:
    """Top-k (doc id, cosine similarity) for the query, best first."""
    if _matrix is None:
        raise RuntimeError("dense.build() must be called before search()")
    query_vector = np.asarray(embed([query])[0], dtype=np.float32)
    scores = _matrix @ query_vector  # rows are normalized, so this is cosine similarity
    top = np.argsort(-scores)[:k]
    return [(_ids[i], float(scores[i])) for i in top]
