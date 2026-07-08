from __future__ import annotations

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
    assert score > 0.99


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
