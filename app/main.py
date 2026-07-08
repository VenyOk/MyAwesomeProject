from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.chat.commands import CommandContext
from app.chat.session import ChatSession
from app.config import Settings, settings as default_settings
from app.llm.gemma import GemmaLLM
from app.memory.recall import RecallService
from app.memory.store import MemoryStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@dataclass
class Services:
    settings: Settings
    store: MemoryStore
    recall: RecallService
    session: ChatSession
    llm: GemmaLLM
    ctx: CommandContext


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or default_settings
    settings.ensure_dirs()

    store = MemoryStore(settings.db_path)

    from app.memory.embeddings import Embedder, FaissIndex

    embedder = Embedder(settings.embedding_model, settings.embedding_dim)
    index = FaissIndex(
        settings.embedding_dim, settings.faiss_path, settings.faiss_ids_path
    )
    recall = RecallService(store, embedder, index)

    session = ChatSession()
    llm = GemmaLLM(settings)
    ctx = CommandContext(
        store=store,
        recall=recall,
        session=session,
        llm=llm,
        settings=settings,
    )
    return Services(settings=settings, store=store, recall=recall, session=session, llm=llm, ctx=ctx)


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

    app = FastAPI(title="Second Brain", lifespan=lifespan)
    app.include_router(api_router, prefix="/api")

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


def get_app() -> FastAPI:
    return create_app()
