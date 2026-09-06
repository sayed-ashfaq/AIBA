"""Text -> vectors, and top-k cosine search over a vector matrix.

Wraps fastembed's TextEmbedding (BAAI/bge-small-en-v1.5, 384-dim, runs on CPU).
The model is a lazy module-level singleton: first use loads ~130MB of weights
(downloaded once, then cached by fastembed under ~/.cache/fastembed), so a caller
on the event loop should offload the first call to a worker thread.

BGE is an instruction-tuned retrieval model — a *query* is embedded with a
different prefix than the *documents* it searches against, otherwise the
similarity scores are skewed. Hence two entry points: embed_documents() for the
schema side (one row per table), embed_query() for the question side. The
prototype used one shared .embed() for both; this is the corrected version.

Pure: no database. pgvector persistence is a later concern — for now the matrix
lives in SchemaGraph.embeddings as a plain numpy array.
"""

import numpy as np
from fastembed import TextEmbedding

from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384  # bge-small-en-v1.5 output dimensionality

_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        with log_duration(f"Load embedding model {MODEL_NAME}"):
            _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed_documents(texts: list[str]) -> np.ndarray:
    """Embed schema-side text — one string per table. Returns shape (len(texts), DIM).
    Empty input returns a well-shaped (0, DIM) array so callers can matrix-multiply
    without a special case."""
    if not texts:
        return np.empty((0, DIM), dtype=np.float32)
    with log_duration(f"Embed {len(texts)} schema documents"):
        vectors = list(_get_model().passage_embed(texts))
    return np.asarray(vectors, dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    """Embed one question-side string, with BGE's query instruction applied.
    Returns shape (DIM,)."""
    vector = next(iter(_get_model().query_embed([text])))
    return np.asarray(vector, dtype=np.float32)


def cosine_topk(query: np.ndarray, matrix: np.ndarray, k: int = 3) -> list[tuple[int, float]]:
    """Row indices + cosine scores of the k rows of `matrix` closest to `query`,
    best first.

    `query` is (DIM,), `matrix` is (n, DIM) — row i is expected to line up with
    SchemaGraph.table_names[i]. Returns [] for an empty matrix; clamps k to n.

    bge vectors already come out L2-normalised, but we normalise again here so the
    function is correct for any input, not just this model's.
    """
    if matrix.size == 0:
        return []
    q = query / (np.linalg.norm(query) + 1e-12)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    scores = m @ q  # (n,)

    k = min(k, scores.shape[0])
    top = np.argpartition(-scores, k - 1)[:k]        # k largest, unordered
    top = top[np.argsort(-scores[top])]             # order those k, best first
    return [(int(i), float(scores[i])) for i in top]
