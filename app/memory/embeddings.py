from __future__ import annotations

import json
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
        if dim <= 0:
            raise ValueError("Embedding dimension must be positive")
        self.model_name = model_name
        self.dim = dim
        self._model = None

    @property
    def uses_e5_prefixes(self) -> bool:
        return "e5" in self.model_name.lower()

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        model = self._ensure()
        vecs = np.asarray(
            model.encode(texts, convert_to_numpy=True, normalize_embeddings=True),
            dtype="float32",
        )
        if vecs.ndim == 1 and len(texts) == 1:
            vecs = vecs.reshape(1, -1)
        if vecs.ndim != 2 or vecs.shape != (len(texts), self.dim):
            raise ValueError(
                f"Embedding model {self.model_name!r} returned shape {vecs.shape}; "
                f"expected ({len(texts)}, {self.dim})"
            )
        return vecs

    def _with_prefix(self, text: str, prefix: str) -> str:
        if not self.uses_e5_prefixes or text.startswith(prefix):
            return text
        return f"{prefix}{text}"

    def embed(self, text: str) -> np.ndarray:
        """Legacy generic embedding API; it deliberately does not add E5 prefixes."""
        return self._encode([text])[0]

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Legacy batch embedding API; it deliberately does not add E5 prefixes."""
        return self._encode(texts)

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a retrieval query, adding the E5 ``query:`` prefix when needed."""
        return self._encode([self._with_prefix(text, "query: ")])[0]

    def embed_document(self, text: str) -> np.ndarray:
        """Embed one retrieval document, adding the E5 ``passage:`` prefix when needed."""
        return self._encode([self._with_prefix(text, "passage: ")])[0]

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed retrieval documents, adding the E5 ``passage:`` prefix when needed."""
        return self._encode([self._with_prefix(text, "passage: ") for text in texts])


class FaissIndex:
    """Flat inner-product index over L2-normalized vectors (== cosine)."""

    CACHE_VERSION = 1

    def __init__(
        self,
        dim: int,
        faiss_path: Path | str,
        ids_path: Path | str,
        *,
        model_name: str,
    ):
        if dim <= 0:
            raise ValueError("FAISS dimension must be positive")
        self.dim = dim
        self.model_name = model_name
        self.faiss_path = Path(faiss_path)
        self.ids_path = Path(ids_path)
        self.metadata_path = self.faiss_path.with_suffix(
            f"{self.faiss_path.suffix}.meta.json"
        )
        self._index = faiss.IndexFlatIP(dim)
        self._ids: list[int] = []

    @property
    def size(self) -> int:
        return self._index.ntotal

    def _metadata(self) -> dict[str, int | str]:
        return {
            "version": self.CACHE_VERSION,
            "model_name": self.model_name,
            "dimension": self.dim,
        }

    def _matches_metadata(self, metadata: object) -> bool:
        return isinstance(metadata, dict) and all(
            metadata.get(key) == value for key, value in self._metadata().items()
        )

    def _reset(self) -> None:
        self._index = faiss.IndexFlatIP(self.dim)
        self._ids = []

    def _vector(self, vector: np.ndarray, operation: str) -> np.ndarray:
        vec = np.asarray(vector, dtype="float32")
        if vec.ndim == 1:
            vec = vec.reshape(1, -1)
        if vec.ndim != 2 or vec.shape != (1, self.dim):
            raise ValueError(
                f"Cannot {operation}: expected one vector with dimension {self.dim}, "
                f"got shape {vec.shape}"
            )
        return vec

    def add(self, memory_id: int, vector: np.ndarray) -> None:
        self._index.add(_normalize(self._vector(vector, "add")))
        self._ids.append(memory_id)

    def rebuild(self, items: Iterable[tuple[int, np.ndarray]]) -> None:
        ids = []
        vectors = []
        for memory_id, vector in items:
            ids.append(memory_id)
            vectors.append(self._vector(vector, "rebuild")[0])
        self._ids = ids
        if vectors:
            mat = _normalize(np.asarray(vectors, dtype="float32"))
        else:
            mat = np.zeros((0, self.dim), dtype="float32")
        self._index = faiss.IndexFlatIP(self.dim)
        if mat.shape[0] > 0:
            self._index.add(mat)

    def search(self, vector: np.ndarray, k: int = 5) -> list[tuple[int, float]]:
        vec = self._vector(vector, "search")
        if self._index.ntotal == 0:
            return []
        k = min(k, self._index.ntotal)
        scores, indices = self._index.search(_normalize(vec), k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            results.append((self._ids[int(idx)], float(score)))
        return results

    def save(self) -> None:
        faiss.write_index(self._index, str(self.faiss_path))
        np.save(self.ids_path, np.asarray(self._ids, dtype="int64"))
        self.metadata_path.write_text(
            json.dumps(self._metadata(), sort_keys=True), encoding="utf-8"
        )

    def load(self) -> bool:
        if not (
            self.faiss_path.exists()
            and self.ids_path.exists()
            and self.metadata_path.exists()
        ):
            self._reset()
            return False
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if not self._matches_metadata(metadata):
                self._reset()
                return False
            index = faiss.read_index(str(self.faiss_path))
            ids = np.load(self.ids_path, allow_pickle=False)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            self._reset()
            return False
        if (
            index.d != self.dim
            or ids.ndim != 1
            or not np.issubdtype(ids.dtype, np.integer)
            or index.ntotal != len(ids)
        ):
            self._reset()
            return False
        self._index = index
        self._ids = [int(x) for x in ids]
        return True
