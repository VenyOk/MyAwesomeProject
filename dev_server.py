"""Dev server: run the Second Brain web UI without heavy retrieval dependencies.

No GPU, no model download, no faiss/sentence-transformers required. Intended for
UI preview and Playwright driving. By default answers are canned. Set
``DEV_USE_REAL_LLM=1`` to connect it to the configured inference server while
keeping the lightweight fake embedder/index.

Run:  python dev_server.py   ->  http://127.0.0.1:8000
"""
from __future__ import annotations

import tempfile
import os
from pathlib import Path

import uvicorn

from app.chat.commands import CommandContext
from app.chat.session import ChatSession
from app.chat.store import ChatStore
from app.config import settings
from app.main import Services, create_app
from app.llm.openai_compatible import OpenAICompatibleLLM
from app.memory.recall import RecallService
from app.memory.store import MemoryStore
from tests.conftest import BruteForceIndex, FakeEmbedder, FakeLLM


def build_fake_services() -> Services:
    db_path = Path(tempfile.gettempdir()) / "second_brain_dev.db"
    store = MemoryStore(db_path)
    chat_store = ChatStore(Path(tempfile.gettempdir()) / "second_brain_dev_chats.db")
    # keep the dev db on the same versioned schema as the real one
    from app.db.migrations import run_migrations

    run_migrations(chat_store._conn, db_path)
    embedder = FakeEmbedder(dim=16)
    index = BruteForceIndex(dim=16)
    recall = RecallService(store, embedder, index)
    session = ChatSession()
    llm = (
        OpenAICompatibleLLM(settings)
        if os.getenv("DEV_USE_REAL_LLM") == "1"
        else FakeLLM()
    )
    ctx = CommandContext(
        store=store, recall=recall, session=session, llm=llm, settings=settings,
        chat_store=chat_store,
    )
    return Services(
        settings=settings, store=store, recall=recall, session=session, llm=llm,
        ctx=ctx, chat_store=chat_store,
    )


app = create_app(build_fake_services())


if __name__ == "__main__":
    uvicorn.run("dev_server:app", host="127.0.0.1", port=8000, log_level="info")
