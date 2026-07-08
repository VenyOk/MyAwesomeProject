from __future__ import annotations

import hashlib

import numpy as np
import pytest

from app.chat.commands import CommandContext
from app.chat.session import ChatSession
from app.config import Settings
from app.main import Services, create_app
from app.memory.recall import RecallService
from app.memory.store import MemoryStore


class FakeEmbedder:
    def __init__(self, dim: int = 16):
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype="float32")
        for byte in text.encode("utf-8"):
            vec[byte % self.dim] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec


class BruteForceIndex:
    def __init__(self, dim: int = 16):
        self.dim = dim
        self._ids: list[int] = []
        self._vecs: list[np.ndarray] = []

    @property
    def size(self) -> int:
        return len(self._ids)

    def add(self, memory_id: int, vector: np.ndarray) -> None:
        self._ids.append(memory_id)
        self._vecs.append(np.asarray(vector, dtype="float32"))

    def rebuild(self, items) -> None:
        self._ids = []
        self._vecs = []
        for memory_id, vector in items:
            self.add(memory_id, vector)

    def search(self, vector, k: int = 5) -> list[tuple[int, float]]:
        if not self._ids:
            return []
        q = np.asarray(vector, dtype="float32")
        q = q / (np.linalg.norm(q) + 1e-9)
        mat = np.asarray(self._vecs, dtype="float32")
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        sims = mat @ q
        order = np.argsort(-sims)[:k]
        return [(self._ids[i], float(sims[i])) for i in order]

    def save(self) -> None:
        pass

    def load(self) -> bool:
        return False


class FakeLLM:
    def __init__(self):
        self.thinking = False

    def is_loaded(self) -> bool:
        return True

    def generate(self, messages, max_new_tokens=None):
        yield "Ответ "
        yield "от FakeLLM."


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "brain.db")


@pytest.fixture
def services(tmp_path):
    settings = Settings()
    store = MemoryStore(tmp_path / "brain.db")
    embedder = FakeEmbedder(dim=16)
    index = BruteForceIndex(dim=16)
    recall = RecallService(store, embedder, index)
    session = ChatSession()
    llm = FakeLLM()
    ctx = CommandContext(
        store=store, recall=recall, session=session, llm=llm, settings=settings
    )
    return Services(
        settings=settings,
        store=store,
        recall=recall,
        session=session,
        llm=llm,
        ctx=ctx,
    )


@pytest.fixture
def client(services):
    app = create_app(services)
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def ctx(services):
    return services.ctx
