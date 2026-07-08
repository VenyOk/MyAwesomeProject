from __future__ import annotations

from typing import Protocol

from app.memory.store import Memory, MemoryStore


class EmbedderLike(Protocol):
    def embed(self, text: str): ...


class IndexLike(Protocol):
    def search(self, vector, k: int = 5) -> list[tuple[int, float]]: ...
    def add(self, memory_id: int, vector) -> None: ...
    def rebuild(self, items) -> None: ...
    def save(self) -> None: ...
    def load(self) -> bool: ...
    @property
    def size(self) -> int: ...


SYSTEM_BLOCK_HEADER = "[Relevant memories from your second brain]"
SYSTEM_BLOCK_FOOTER = "[end relevant memories]"


class RecallService:
    def __init__(self, store: MemoryStore, embedder: EmbedderLike, index: IndexLike):
        self.store = store
        self.embedder = embedder
        self.index = index

    def ensure_ready(self) -> None:
        loaded = self.index.load()
        if not loaded or self.index.size != self.store.count():
            self.rebuild_from_store()

    def embed_text(self, text: str):
        return self.embedder.embed(text)

    def add_memory(self, memory: Memory, persist: bool = True) -> None:
        vector = self.embed_text(memory.content)
        self.index.add(memory.id, vector)
        if persist:
            self.index.save()

    def recall(self, query: str, k: int = 5) -> list[tuple[Memory, float]]:
        if self.store.count() == 0:
            return []
        vector = self.embed_text(query)
        hits = self.index.search(vector, k=k)
        results: list[tuple[Memory, float]] = []
        for memory_id, score in hits:
            memory = self.store.get(memory_id)
            if memory is not None:
                results.append((memory, score))
        return results

    def build_context(self, query: str, k: int = 5) -> str:
        hits = self.recall(query, k=k)
        if not hits:
            return ""
        lines = [SYSTEM_BLOCK_HEADER]
        for i, (memory, score) in enumerate(hits, start=1):
            tags = f"[{', '.join(memory.tags)}]" if memory.tags else ""
            stamp = memory.created_at[:10] if memory.created_at else ""
            preview = memory.content.strip().replace("\n", " ")
            if len(preview) > 500:
                preview = preview[:497] + "..."
            lines.append(f"{i}. {stamp} {tags} (match {score:.2f}): {preview}")
        lines.append(SYSTEM_BLOCK_FOOTER)
        return "\n".join(lines)

    def rebuild_from_store(self) -> None:
        memories = self.store.all()
        items = [(m.id, self.embed_text(m.content)) for m in memories]
        self.index.rebuild(items)
        self.index.save()
