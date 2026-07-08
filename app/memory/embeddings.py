from __future__ import annotations

from pathlib import Path
from typing import Iterable

import faiss
import numpy as np


def _normalize(vecs: np.ndarray) -> np.ndarray:
    vecs = np.asarray(vecs, dtype="float32")
    if vecs.ndim == 1:
        vecs = vecs.reshape(1, -1)
    faiss.normalize_L2(vecs)
    return vecs


class Embedder:
    """Sentence-transformers embedder. Loads the model lazily on first use."""

    def __init__(self, model_name: str, dim: int):
        self.model_name = model_name
        self.dim = dim
        self._model = None

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> np.ndarray:
        model = self._ensure()
        vec = model.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        return np.asarray(vec[0], dtype="float32")

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        model = self._ensure()
        vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return np.asarray(vecs, dtype="float32")


class FaissIndex:
    """Flat inner-product index over L2-normalized vectors (== cosine)."""

    def __init__(self, dim: int, faiss_path: Path | str, ids_path: Path | str):
        self.dim = dim
        self.faiss_path = Path(faiss_path)
        self.ids_path = Path(ids_path)
        self._index = faiss.IndexFlatIP(dim)
        self._ids: list[int] = []

    @property
    def size(self) -> int:
        return self._index.ntotal

    def add(self, memory_id: int, vector: np.ndarray) -> None:
        vec = _normalize(vector)
        self._index.add(vec)
        self._ids.append(memory_id)

    def rebuild(self, items: Iterable[tuple[int, np.ndarray]]) -> None:
        ids = []
        vectors = []
        for memory_id, vector in items:
            ids.append(memory_id)
            vectors.append(np.asarray(vector, dtype="float32"))
        self._ids = ids
        if vectors:
            mat = _normalize(np.asarray(vectors, dtype="float32"))
        else:
            mat = np.zeros((0, self.dim), dtype="float32")
        self._index = faiss.IndexFlatIP(self.dim)
        if mat.shape[0] > 0:
            self._index.add(mat)

    def search(self, vector: np.ndarray, k: int = 5) -> list[tuple[int, float]]:
        if self._index.ntotal == 0:
            return []
        k = min(k, self._index.ntotal)
        vec = _normalize(vector)
        scores, indices = self._index.search(vec, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            results.append((self._ids[int(idx)], float(score)))
        return results

    def save(self) -> None:
        faiss.write_index(self._index, str(self.faiss_path))
        np.save(self.ids_path, np.asarray(self._ids, dtype="int64"))

    def load(self) -> bool:
        if not (self.faiss_path.exists() and self.ids_path.exists()):
            return False
        self._index = faiss.read_index(str(self.faiss_path))
        ids = np.load(self.ids_path)
        self._ids = [int(x) for x in ids]
        return True
