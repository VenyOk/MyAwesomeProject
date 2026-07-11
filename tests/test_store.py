from __future__ import annotations

from app.memory.store import MemoryStore


def test_add_and_get(store: MemoryStore):
    mem = store.add(content="hello world", source="manual")
    assert mem.id is not None
    assert mem.content == "hello world"
    fetched = store.get(mem.id)
    assert fetched is not None
    assert fetched.content == "hello world"
    assert fetched.source == "manual"
    assert fetched.tags == []


def test_tags_roundtrip(store: MemoryStore):
    mem = store.add(content="tagged note", tags=["python", "idea"])
    assert mem.tags == ["python", "idea"]
    updated = store.add_tag(mem.id, "urgent")
    assert updated is not None
    assert "urgent" in updated.tags
    counts = store.tag_counts()
    assert counts["python"] == 1
    assert counts["urgent"] == 1


def test_list_recent_order(store: MemoryStore):
    a = store.add(content="first")
    b = store.add(content="second")
    c = store.add(content="third")
    recent = store.list_recent(2)
    assert [m.id for m in recent] == [c.id, b.id]


def test_search_text(store: MemoryStore):
    store.add(content="I love python")
    store.add(content="rust is nice too")
    hits = store.search_text("python")
    assert len(hits) == 1
    assert "python" in hits[0].content


def test_delete(store: MemoryStore):
    mem = store.add(content="temp note")
    assert store.delete(mem.id) is True
    # soft delete: row stays for restore/audit but is excluded from listings
    soft = store.get(mem.id)
    assert soft is not None
    assert soft.status == "deleted"
    assert soft.deleted_at is not None
    assert store.count() == 0
    assert store.delete(mem.id) is False  # already deleted
    # restore brings it back
    restored = store.restore(mem.id)
    assert restored is not None
    assert restored.status == "active"
    assert restored.deleted_at is None
    assert store.count() == 1


def test_count(store: MemoryStore):
    assert store.count() == 0
    store.add(content="one")
    store.add(content="two")
    assert store.count() == 2
