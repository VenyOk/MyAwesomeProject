from __future__ import annotations

from app.db.migrations import run_migrations
from app.memory.store import MemoryStore


def _enable_fts(store: MemoryStore) -> None:
    run_migrations(store._conn, store.db_path)


def test_lexical_search_indexes_existing_and_updated_memory_fields(tmp_path):
    store = MemoryStore(tmp_path / "brain.db")
    existing = store.add(
        content="initial launch note",
        summary="quarterly roadmap",
        tags=["planning"],
    )
    _enable_fts(store)

    summary_hits = store.search_lexical("roadmap")
    assert [memory.id for memory, _ in summary_hits] == [existing.id]

    updated = store.update(existing.id, content="revised launch note", tags=["release"])
    assert updated is not None
    assert store.search_lexical("initial") == []
    assert [memory.id for memory, _ in store.search_lexical("release")] == [existing.id]

    symbols = store.add(content='C++ "quoted" implementation note')
    assert [memory.id for memory, _ in store.search_lexical('C++ "quoted"')] == [symbols.id]
    store.close()


def test_lexical_search_only_returns_recallable_memories(tmp_path):
    store = MemoryStore(tmp_path / "brain.db")
    _enable_fts(store)
    active = store.add(content="hybrid retrieval reference")
    candidate = store.add(content="hybrid retrieval candidate", status="candidate")
    pending = store.add(content="hybrid retrieval pending")
    deleted = store.add(content="hybrid retrieval deleted")
    store._conn.execute(
        "UPDATE memories SET embedding_status = 'pending' WHERE id = ?", (pending.id,)
    )
    store._conn.commit()
    store.delete(deleted.id)

    hits = store.search_lexical("hybrid retrieval")
    assert [memory.id for memory, _ in hits] == [active.id]
    assert all(0 < score <= 1 for _, score in hits)
    assert candidate.id not in [memory.id for memory, _ in hits]
    store.close()


def test_lexical_search_falls_back_without_migration_and_accepts_punctuation(tmp_path):
    store = MemoryStore(tmp_path / "brain.db")
    memory = store.add(content='C++ "quoted" note')

    hits = store.search_lexical('C++ "quoted"')
    assert [hit.id for hit, _ in hits] == [memory.id]
    assert store.search_lexical("!!!") == []
    store.close()
