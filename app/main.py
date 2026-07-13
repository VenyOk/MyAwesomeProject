from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.agent.store import AgentStore
from app.chat.commands import CommandContext
from app.chat.session import ChatSession
from app.chat.store import ChatStore
from app.config import Settings, settings as default_settings
from app.llm.base import LLMProvider
from app.llm.gemma import GemmaLLM
from app.llm.openai_compatible import OpenAICompatibleLLM
from app.memory.recall import RecallService
from app.memory.store import MemoryStore
from app.jobs.outbox import OutboxStore
from app.jobs.scheduler import ReminderScheduler, ReminderSchedulerLoop
from app.tasks.reminders import ReminderStore
from app.tasks.service import TaskReminderService
from app.tasks.store import TaskStore
from app.settings.store import SettingsStore

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
    task_store: TaskStore
    tool_registry: object  # ToolRegistry; typed loosely to avoid import cycles
    agent_store: AgentStore | None = None
    reminder_store: ReminderStore | None = None
    task_reminder_service: TaskReminderService | None = None
    outbox_store: OutboxStore | None = None
    reminder_scheduler: ReminderScheduler | None = None
    settings_store: SettingsStore | None = None


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or default_settings
    settings.ensure_dirs()

    store = MemoryStore(settings.db_path)
    chat_store = ChatStore(settings.db_path)
    task_store = TaskStore(settings.db_path)
    reminder_store = ReminderStore(settings.db_path)
    task_reminder_service = TaskReminderService(task_store, reminder_store)
    outbox_store = OutboxStore(settings.db_path)

    # Apply versioned migrations after the stores have created their baseline
    # schema. Enriches the schema (workspace_id, soft delete, curated memory
    # columns, tasks/reminders/...) on top of the existing CREATE IF NOT EXISTS.
    from app.db.migrations import run_migrations

    run_migrations(chat_store._conn, settings.db_path)
    settings_store = SettingsStore(settings.db_path)
    stored_settings = settings_store.get()
    if stored_settings is None:
        settings_store.save(
            settings.timezone,
            settings.quiet_hours_start,
            settings.quiet_hours_end,
        )
    else:
        settings.timezone = stored_settings["timezone"]
        settings.quiet_hours_start = stored_settings["quiet_hours_start"]
        settings.quiet_hours_end = stored_settings["quiet_hours_end"]
    agent_store = AgentStore(settings.db_path)
    reminder_scheduler = ReminderScheduler(
        reminder_store,
        outbox_store,
        quiet_hours_start=settings.quiet_hours_start,
        quiet_hours_end=settings.quiet_hours_end,
    )

    from app.agent.tool_registry import build_default_registry
    from app.memory.embeddings import Embedder, FaissIndex

    embedder = Embedder(settings.embedding_model, settings.embedding_dim)
    index = FaissIndex(
        settings.embedding_dim,
        settings.faiss_path,
        settings.faiss_ids_path,
        model_name=settings.embedding_model,
    )
    recall = RecallService(
        store,
        embedder,
        index,
        context_token_budget=settings.recall_context_token_budget,
    )

    session = ChatSession()
    if settings.llm_provider == "openai_compatible":
        llm: LLMProvider = OpenAICompatibleLLM(settings)
    elif settings.llm_provider == "qwen_server":
        llm = OpenAICompatibleLLM(settings, supports_native_tool_calls=False)
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
    tool_registry = build_default_registry()
    return Services(
        settings=settings,
        store=store,
        recall=recall,
        session=session,
        llm=llm,
        ctx=ctx,
        chat_store=chat_store,
        task_store=task_store,
        tool_registry=tool_registry,
        agent_store=agent_store,
        reminder_store=reminder_store,
        task_reminder_service=task_reminder_service,
        outbox_store=outbox_store,
        reminder_scheduler=reminder_scheduler,
        settings_store=settings_store,
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
        scheduler_loop: ReminderSchedulerLoop | None = None
        if services.reminder_scheduler is not None:
            scheduler_loop = ReminderSchedulerLoop(
                services.reminder_scheduler,
                interval_seconds=services.settings.scheduler_interval_seconds,
            )
            await scheduler_loop.start()
        try:
            yield
        finally:
            if scheduler_loop is not None:
                await scheduler_loop.stop()
            if owns_services:
                services.store.close()
                services.chat_store.close()
                services.task_store.close()
                if services.reminder_store is not None:
                    services.reminder_store.close()
                if services.outbox_store is not None:
                    services.outbox_store.close()
                if services.agent_store is not None:
                    services.agent_store.close()
                if services.settings_store is not None:
                    services.settings_store.close()

    app = FastAPI(title="Second Brain", lifespan=lifespan)
    app.include_router(api_router, prefix="/api")

    static_dir = FRONTEND_DIST_DIR if FRONTEND_DIST_DIR.exists() else WEB_DIR
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="web")

    return app


def get_app() -> FastAPI:
    return create_app()
