"""Integration tests for memory extraction inside the /api/chat pipeline.

Verifies plan §10 acceptance criteria:
- #3: a casual "привет" does NOT create a memory
- #4: "запомни, что я не ем арахис" creates an active, editable memory linked
  to the source message.
"""
from __future__ import annotations

import json

import pytest

from app.chat.commands import CommandContext
from app.chat.session import ChatSession
from app.chat.store import ChatStore
from app.config import Settings
from app.main import Services, create_app
from app.memory.recall import RecallService
from app.memory.store import MemoryStore
from tests.conftest import BruteForceIndex, FakeEmbedder


class _RoutingLLM:
    """Routes by the system prompt: extractor calls get a JSON payload, chat
    calls get a plain answer. This mirrors how the real model behaves
    differently for the two prompt types."""

    def __init__(self):
        self.thinking = False

    def is_loaded(self) -> bool:
        return True

    def generate(self, messages, max_new_tokens=None, tools=None):
        sys_text = messages[0]["content"] if messages else ""
        user_text = messages[-1]["content"] if messages else ""
        if "модуль извлечения памяти" in sys_text:
            # Greeting / casual -> no candidates
            if any(w in user_text.lower() for w in ("привет", "как дела", "пока")):
                yield '{"candidates": []}'
                return
            # Explicit "запомни" -> one active preference
            if "запомни" in user_text.lower() or "учти" in user_text.lower():
                yield json.dumps({"candidates": [{
                    "kind": "preference",
                    "content": "Пользователь не ест арахис",
                    "importance": 0.9,
                    "confidence": 0.95,
                    "explicit": True,
                }]})
                return
            yield '{"candidates": []}'
            return
        # normal chat answer
        yield "Это ответ ассистента."


@pytest.fixture
def chat_client(tmp_path):
    from app.agent.tool_registry import build_default_registry
    from app.agent.store import AgentStore
    from app.tasks.store import TaskStore

    settings = Settings()
    store = MemoryStore(tmp_path / "brain.db")
    chat_store = ChatStore(tmp_path / "chats.db")
    task_store = TaskStore(tmp_path / "brain.db")
    agent_store = AgentStore(tmp_path / "brain.db")
    tool_registry = build_default_registry()
    embedder = FakeEmbedder(dim=16)
    index = BruteForceIndex(dim=16)
    recall = RecallService(store, embedder, index)
    session = ChatSession()
    llm = _RoutingLLM()
    ctx = CommandContext(
        store=store, recall=recall, session=session, llm=llm,
        settings=settings, chat_store=chat_store,
    )
    services = Services(
        settings=settings, store=store, recall=recall, session=session,
        llm=llm, ctx=ctx, chat_store=chat_store, task_store=task_store,
        tool_registry=tool_registry,
        agent_store=agent_store,
    )
    from fastapi.testclient import TestClient
    with TestClient(create_app(services)) as c:
        yield c


def _stream_collect(client, chat_id, message):
    """POST /api/chat and collect SSE events into (tokens, memories, done)."""
    res = client.post("/api/chat", json={"chat_id": chat_id, "message": message})
    assert res.status_code == 200
    tokens, memories, done = [], None, None
    for part in res.text.split("\n\n"):
        line = part.strip()
        if not line.startswith("data:"):
            continue
        payload = json.loads(line[5:].strip())
        if "token" in payload:
            tokens.append(payload["token"])
        if "memory_created" in payload:
            memories = payload["memory_created"]
        if "done" in payload:
            done = payload
    return "".join(tokens), memories, done


def test_casual_message_creates_no_memory(chat_client):
    chat = chat_client.post("/api/chats", json={"title": None}).json()
    before = chat_client.get("/api/memories").json()["count"]
    _, memories, done = _stream_collect(chat_client, chat["id"], "привет!")
    assert done is not None
    # no memory_created event and count unchanged
    assert memories is None
    after = chat_client.get("/api/memories").json()["count"]
    assert after == before


def test_explicit_remember_creates_active_linked_memory(chat_client):
    chat = chat_client.post("/api/chats", json={"title": None}).json()
    _, memories, done = _stream_collect(
        chat_client, chat["id"], "Запомни, что я не ем арахис"
    )
    assert done is not None
    assert memories is not None and len(memories) == 1
    mem = memories[0]
    assert mem["status"] == "active"
    assert mem["kind"] == "preference"
    # the memory exists in the store and is linked to the source user message
    got = chat_client.get(f"/api/memories/{mem['id']}").json()
    assert got["source_message_id"] == done["user_message"]["id"]
    assert got["status"] == "active"
