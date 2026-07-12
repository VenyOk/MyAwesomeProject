from __future__ import annotations

import numpy as np

from app.memory.recall import RecallService, SYSTEM_BLOCK_HEADER


def _seed(recall: RecallService, texts):
    for t in texts:
        mem = recall.store.add(content=t, source="manual")
        recall.add_memory(mem, persist=False)


def test_recall_finds_identical(services):
    recall = services.recall
    _seed(recall, ["the quick brown fox", "a totally different topic"])
    hits = recall.recall("the quick brown fox", k=1)
    assert len(hits) == 1
    mem, score = hits[0]
    assert "fox" in mem.content
    assert score > 0.9


def test_build_context_empty(services):
    recall = services.recall
    assert recall.build_context("anything") == ""


def test_build_context_has_header(services):
    recall = services.recall
    _seed(recall, ["remember this fact"])
    context = recall.build_context("remember this fact", k=3)
    assert SYSTEM_BLOCK_HEADER in context
    assert "remember this fact" in context


def test_rebuild_from_store(services):
    recall = services.recall
    mem = recall.store.add(content="rebuild me", source="manual")
    recall.add_memory(mem, persist=False)
    assert recall.index.size == 1
    recall.index.rebuild([])  # simulate a wiped index
    assert recall.index.size == 0
    recall.rebuild_from_store()
    assert recall.index.size == 1
    hits = recall.recall("rebuild me", k=1)
    assert hits and hits[0][0].id == mem.id


class StaticIndex:
    def __init__(self, hits: list[tuple[int, float]]):
        self.hits = hits
        self.requested_k: int | None = None

    @property
    def size(self) -> int:
        return len(self.hits)

    def search(self, _vector, k: int = 5) -> list[tuple[int, float]]:
        self.requested_k = k
        return self.hits[:k]

    def add(self, _memory_id: int, _vector) -> None:
        pass

    def rebuild(self, _items) -> None:
        pass

    def save(self) -> None:
        pass

    def load(self) -> bool:
        return True


def test_hybrid_recall_unions_and_deduplicates_semantic_and_lexical_hits(services):
    recall = services.recall
    semantic_only = recall.store.add(content="A broad note about deploy procedures")
    lexical = recall.store.add(content="Ticket 422 is blocked by the release")
    recall.index = StaticIndex([(semantic_only.id, 0.9), (lexical.id, 0.7)])

    hits = recall.recall("Ticket 422", k=2)

    assert {memory.id for memory, _ in hits} == {semantic_only.id, lexical.id}
    assert len([memory for memory, _ in hits if memory.id == lexical.id]) == 1


def test_exact_query_uses_a_wider_candidate_pool(services):
    recall = services.recall
    memory = recall.store.add(content="project note")
    index = StaticIndex([(memory.id, 0.8)])
    recall.index = index

    recall.recall("project update", k=5)
    generic_pool = index.requested_k
    recall.recall("project #422 update", k=5)

    assert generic_pool == 10
    assert index.requested_k == 20


def test_hybrid_recall_keeps_candidates_out_even_if_the_index_returns_them(services):
    recall = services.recall
    active = recall.store.add(content="active project note")
    candidate = recall.store.add(content="candidate project note", status="candidate")
    recall.index = StaticIndex([(candidate.id, 1.0), (active.id, 0.6)])

    hits = recall.recall("project", k=5)

    assert [memory.id for memory, _ in hits] == [active.id]


def test_build_context_respects_budget_and_includes_memory_source_ids(services):
    recall = services.recall
    memory = recall.store.add(
        content="important memory " * 30,
        source_message_id=42,
    )
    recall.add_memory(memory, persist=False)

    context = recall.build_context("important memory", k=1, token_budget=50)

    assert f"memory_id={memory.id}" in context
    assert "source_message_id=42" in context
    assert len(context) <= 50 * 4


def test_recall_uses_query_and_document_embedding_roles(services):
    class RoleAwareEmbedder:
        def __init__(self):
            self.calls: list[str] = []

        def embed(self, _text: str) -> np.ndarray:
            raise AssertionError("role-specific embedding methods should be used")

        def embed_query(self, _text: str) -> np.ndarray:
            self.calls.append("query")
            return np.ones(16, dtype="float32")

        def embed_document(self, _text: str) -> np.ndarray:
            self.calls.append("document")
            return np.ones(16, dtype="float32")

        def embed_documents(self, texts: list[str]) -> np.ndarray:
            self.calls.append("documents")
            return np.ones((len(texts), 16), dtype="float32")

    recall = services.recall
    embedder = RoleAwareEmbedder()
    recall.embedder = embedder
    memory = recall.store.add(content="role-aware retrieval")

    recall.add_memory(memory, persist=False)
    recall.recall("retrieve", k=1)
    recall.rebuild_from_store()

    assert embedder.calls == ["document", "query", "documents"]
