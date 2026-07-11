from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.chat.commands import CommandContext
from app.chat.session import ChatSession
from app.chat.store import ChatStore
from app.config import Settings, settings as default_settings
from app.llm.base import LLMProvider
from app.llm.gemma import GemmaLLM
from app.llm.openai_compatible import OpenAICompatibleLLM
from app.memory.recall import RecallService
from app.memory.store import MemoryStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


@dataclass
class Services:
    settings: Settings
    store: MemoryStore
    recall: RecallService
    session: ChatSession
    llm: LLMProvider
    ctx: CommandContext
    chat_store: ChatStore


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or default_settings
    settings.ensure_dirs()

    store = MemoryStore(settings.db_path)
    chat_store = ChatStore(settings.db_path)

    # Apply versioned migrations after the stores have created their baseline
    # schema. Enriches the schema (workspace_id, soft delete, curated memory
    # columns, tasks/reminders/...) on top of the existing CREATE IF NOT EXISTS.
    from app.db.migrations import run_migrations

    run_migrations(chat_store._conn, settings.db_path)

    from app.memory.embeddings import Embedder, FaissIndex

    embedder = Embedder(settings.embedding_model, settings.embedding_dim)
    index = FaissIndex(
        settings.embedding_dim, settings.faiss_path, settings.faiss_ids_path
    )
    recall = RecallService(store, embedder, index)

    session = ChatSession()
    if settings.llm_provider == "openai_compatible":
        llm: LLMProvider = OpenAICompatibleLLM(settings)
    elif settings.llm_provider == "legacy_gemma":
        llm = GemmaLLM(settings)
    else:
        raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
    ctx = CommandContext(
        store=store,
        recall=recall,
        session=session,
        llm=llm,
        settings=settings,
        chat_store=chat_store,
    )
    return Services(
        settings=settings,
        store=store,
        recall=recall,
        session=session,
        llm=llm,
        ctx=ctx,
        chat_store=chat_store,
    )


def create_app(services: Services | None = None) -> FastAPI:
    owns_services = services is None
    services = services or build_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_services:
            try:
                services.recall.ensure_ready()
            except Exception as exc:  # noqa: BLE001 - don't crash on startup over embeddings
                print(f"[second-brain] recall init skipped: {exc}")
        app.state.services = services
        yield
        if owns_services:
            services.store.close()
            services.chat_store.close()

    app = FastAPI(title="Second Brain", lifespan=lifespan)
    app.include_router(api_router, prefix="/api")

    static_dir = FRONTEND_DIST_DIR if FRONTEND_DIST_DIR.exists() else WEB_DIR
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="web")

    return app


def get_app() -> FastAPI:
    return create_app()
