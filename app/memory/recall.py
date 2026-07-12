from __future__ import annotations

import re
from datetime import datetime, timezone
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
    def __init__(
        self,
        store: MemoryStore,
        embedder: EmbedderLike,
        index: IndexLike,
        *,
        context_token_budget: int = 1200,
    ):
        self.store = store
        self.embedder = embedder
        self.index = index
        self.context_token_budget = max(0, context_token_budget)

    def ensure_ready(self) -> None:
        loaded = self.index.load()
        if not loaded or self.index.size != self.store.recallable_count():
            self.rebuild_from_store()

    def embed_query(self, text: str):
        embed_query = getattr(self.embedder, "embed_query", None)
        return embed_query(text) if callable(embed_query) else self.embedder.embed(text)

    def embed_document(self, text: str):
        embed_document = getattr(self.embedder, "embed_document", None)
        return embed_document(text) if callable(embed_document) else self.embedder.embed(text)

    def add_memory(self, memory: Memory, persist: bool = True) -> None:
        if memory.status != "active" or memory.embedding_status != "ready" or memory.deleted_at:
            return
        vector = self.embed_document(memory.content)
        self.index.add(memory.id, vector)
        if persist:
            self.index.save()

    def recall(self, query: str, k: int = 5) -> list[tuple[Memory, float]]:
        if self.store.recallable_count() == 0 or k <= 0:
            return []
        candidate_limit = self._candidate_limit(query, k)
        semantic_hits = self.index.search(
            self.embed_query(query), k=candidate_limit
        )
        lexical_hits = self.store.search_lexical(query, limit=candidate_limit)

        candidates: dict[int, tuple[Memory, float, float]] = {}
        for memory_id, score in semantic_hits:
            memory = self.store.get(memory_id)
            if self._is_recallable(memory):
                candidates[memory_id] = (memory, max(0.0, min(1.0, score)), 0.0)
        for memory, score in lexical_hits:
            if not self._is_recallable(memory):
                continue
            previous = candidates.get(memory.id)
            semantic = previous[1] if previous is not None else 0.0
            candidates[memory.id] = (
                memory,
                semantic,
                max(0.0, min(1.0, score)),
            )

        ranked = [
            (memory, self._score(memory, query, semantic, lexical))
            for memory, semantic, lexical in candidates.values()
        ]
        ranked.sort(
            key=lambda item: (item[1], item[0].importance, item[0].created_at, item[0].id),
            reverse=True,
        )
        return ranked[:k]

    def build_context(
        self, query: str, k: int = 5, token_budget: int | None = None
    ) -> str:
        hits = self.recall(query, k=k)
        if not hits:
            return ""
        budget = self.context_token_budget if token_budget is None else max(0, token_budget)
        max_characters = budget * 4
        if max_characters < len(SYSTEM_BLOCK_HEADER) + len(SYSTEM_BLOCK_FOOTER) + 1:
            return ""
        lines = [SYSTEM_BLOCK_HEADER]
        footer = SYSTEM_BLOCK_FOOTER
        for i, (memory, score) in enumerate(hits, start=1):
            tags = f"[{', '.join(memory.tags)}]" if memory.tags else ""
            stamp = memory.created_at[:10] if memory.created_at else ""
            source = f"memory_id={memory.id}"
            if memory.source_message_id is not None:
                source += f", source_message_id={memory.source_message_id}"
            preview = memory.content.strip().replace("\n", " ")
            if len(preview) > 500:
                preview = preview[:497] + "..."
            prefix = f"{i}. {stamp} {tags} [{source}] (match {score:.2f}): "
            reserved = len("\n".join([*lines, footer])) + 1
            available = max_characters - reserved
            if available <= len(prefix):
                break
            if len(preview) > available - len(prefix):
                preview = preview[: max(0, available - len(prefix) - 3)].rstrip() + "..."
            lines.append(prefix + preview)
        lines.append(footer)
        return "\n".join(lines)

    def rebuild_from_store(self) -> None:
        memories = self.store.recallable()
        embed_documents = getattr(self.embedder, "embed_documents", None)
        if callable(embed_documents):
            vectors = embed_documents([memory.content for memory in memories])
            if len(vectors) != len(memories):
                raise ValueError("Embedder returned an unexpected document vector count")
            items = [(memory.id, vector) for memory, vector in zip(memories, vectors)]
        else:
            items = [(memory.id, self.embed_document(memory.content)) for memory in memories]
        self.index.rebuild(items)
        self.index.save()

    @staticmethod
    def _is_recallable(memory: Memory | None) -> bool:
        return bool(
            memory
            and memory.status == "active"
            and memory.embedding_status == "ready"
            and memory.deleted_at is None
        )

    @staticmethod
    def _candidate_limit(query: str, k: int) -> int:
        """Use a wider lexical/semantic pool for exact-looking questions."""
        base = max(k * 2, 10)
        exact_query = bool(re.search(r"[\d\"'«»#@]", query))
        return max(base, k * 4) if exact_query else base

    @staticmethod
    def _recency(memory: Memory) -> float:
        try:
            created_at = datetime.fromisoformat(memory.created_at.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds() / 86400)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, 1.0 - age_days / 365.0)

    @staticmethod
    def _metadata_match(memory: Memory, query: str) -> float:
        """Tags carry the current project's/folder's label in the MVP schema."""
        query_terms = set(re.findall(r"\w+", query.casefold(), flags=re.UNICODE))
        metadata_terms = {memory.kind.casefold(), *(tag.casefold() for tag in memory.tags)}
        return 1.0 if query_terms & metadata_terms else 0.0

    def _score(self, memory: Memory, query: str, semantic: float, lexical: float) -> float:
        # Exact metadata matches (kind/tags, including project tags) improve the
        # lexical part without bypassing the documented initial weighting.
        lexical = max(lexical, self._metadata_match(memory, query) * 0.5)
        importance = max(0.0, min(1.0, memory.importance))
        return (
            0.55 * semantic
            + 0.25 * lexical
            + 0.10 * importance
            + 0.10 * self._recency(memory)
        )
