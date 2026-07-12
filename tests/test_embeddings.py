from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import Settings


def _install_faiss_fake() -> None:
    class IndexFlatIP:
        def __init__(self, dim: int):
            self.d = dim
            self._vectors = np.zeros((0, dim), dtype="float32")

        @property
        def ntotal(self) -> int:
            return len(self._vectors)

        def add(self, vectors: np.ndarray) -> None:
            self._vectors = np.vstack((self._vectors, vectors))

        def search(self, queries: np.ndarray, k: int):
            scores = queries @ self._vectors.T
            order = np.argsort(-scores, axis=1)[:, :k]
            return np.take_along_axis(scores, order, axis=1), order

    def normalize_l2(vectors: np.ndarray) -> None:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        np.divide(vectors, norms, out=vectors, where=norms != 0)

    def write_index(index: IndexFlatIP, path: str) -> None:
        with Path(path).open("wb") as handle:
            np.savez(handle, dim=index.d, vectors=index._vectors)

    def read_index(path: str) -> IndexFlatIP:
        with np.load(path) as saved:
            index = IndexFlatIP(int(saved["dim"]))
            index._vectors = saved["vectors"]
        return index

    sys.modules["faiss"] = SimpleNamespace(
        IndexFlatIP=IndexFlatIP,
        normalize_L2=normalize_l2,
        write_index=write_index,
        read_index=read_index,
    )


try:
    import faiss  # noqa: F401
except ModuleNotFoundError:
    _install_faiss_fake()

from app.memory.embeddings import Embedder, FaissIndex


class EncodeSpy:
    def __init__(self, dim: int):
        self.dim = dim
        self.calls: list[list[str]] = []

    def encode(self, texts, **_kwargs) -> np.ndarray:
        self.calls.append(list(texts))
        return np.ones((len(texts), self.dim), dtype="float32")


def test_embedding_settings_read_environment(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    monkeypatch.setenv("EMBEDDING_DIM", "384")

    configured = Settings()

    assert configured.embedding_model == "intfloat/multilingual-e5-small"
    assert configured.embedding_dim == 384


def test_e5_methods_apply_explicit_query_and_document_prefixes():
    embedder = Embedder("intfloat/multilingual-e5-small", dim=3)
    spy = EncodeSpy(dim=3)
    embedder._model = spy

    embedder.embed("legacy input")
    embedder.embed_query("find the note")
    embedder.embed_document("the note")
    embedder.embed_documents(["first", "second"])

    assert spy.calls == [
        ["legacy input"],
        ["query: find the note"],
        ["passage: the note"],
        ["passage: first", "passage: second"],
    ]


def test_embedder_rejects_wrong_model_dimension():
    embedder = Embedder("test-model", dim=3)
    embedder._model = EncodeSpy(dim=2)

    with pytest.raises(ValueError, match=r"expected \(1, 3\)"):
        embedder.embed("bad vector")


def test_faiss_cache_requires_matching_embedding_identity(tmp_path):
    faiss_path = tmp_path / "faiss.index"
    ids_path = tmp_path / "faiss.ids.npy"
    original = FaissIndex(2, faiss_path, ids_path, model_name="model-a")
    original.add(7, np.array([1.0, 0.0], dtype="float32"))
    original.save()

    same_model = FaissIndex(2, faiss_path, ids_path, model_name="model-a")
    assert same_model.load() is True
    assert same_model.search(np.array([1.0, 0.0], dtype="float32")) == [(7, 1.0)]

    assert FaissIndex(2, faiss_path, ids_path, model_name="model-b").load() is False
    assert FaissIndex(3, faiss_path, ids_path, model_name="model-a").load() is False
    original.metadata_path.unlink()
    assert FaissIndex(2, faiss_path, ids_path, model_name="model-a").load() is False


def test_faiss_index_rejects_wrong_vector_dimension(tmp_path):
    index = FaissIndex(
        2, tmp_path / "faiss.index", tmp_path / "faiss.ids.npy", model_name="model-a"
    )

    with pytest.raises(ValueError, match="dimension 2"):
        index.add(1, np.array([1.0, 0.0, 0.0], dtype="float32"))
    with pytest.raises(ValueError, match="dimension 2"):
        index.search(np.array([1.0, 0.0, 0.0], dtype="float32"))
