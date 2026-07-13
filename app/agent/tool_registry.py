"""Typed tool registry (plan §12.1).

Each tool has a unique name, a Pydantic argument schema, a handler bound to
the application services, and a risk level resolved through the policy engine.
The registry produces the OpenAI-style ``tools`` JSON advertised to the model
and dispatches validated arguments to handlers.

The model never supplies Python code or shell commands: arguments are parsed
through Pydantic and only allowlisted handlers run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from app.agent.policies import decide


@dataclass
class ToolDefinition:
    name: str
    description: str
    args_schema: Type[BaseModel]
    handler: Callable[[Any, BaseModel], dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def openai_schema(self) -> list[dict]:
        """Produce the ``tools`` array for the OpenAI-compatible request."""
        out = []
        for name in sorted(self._tools):
            t = self._tools[name]
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": t.description,
                        "parameters": t.args_schema.model_json_schema(),
                    },
                }
            )
        return out

    def dispatch(self, name: str, arguments: dict, services: Any) -> dict:
        """Validate arguments and run the handler. Returns a result dict.

        Raises KeyError for unknown tools (caller decides how to tell the model)
        and stores validation errors in the returned dict so the model can
        self-correct on the next turn.
        """
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            args = tool.args_schema.model_validate(arguments)
        except ValidationError as exc:
            return {"error": "invalid arguments", "detail": exc.errors()}
        try:
            return tool.handler(services, args)
        except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
            return {"error": f"tool execution failed: {exc}"}


# ---------------------------- handlers ----------------------------


def _handle_memory_search(services: Any, args) -> dict:
    hits = services.recall.recall(args.query, k=services.settings.recall_top_k)
    return {
        "results": [
            {
                "id": m.id,
                "kind": m.kind,
                "content": m.content,
                "tags": m.tags,
                "score": round(s, 3),
            }
            for m, s in hits
        ],
        "count": len(hits),
    }


def _handle_memory_create(services: Any, args) -> dict:
    source_type = "manual" if args.source == "manual" else "chat"
    mem = services.store.add(
        content=args.content,
        source=args.source,
        source_type=source_type,
        kind=args.kind,
        status="active",
    )
    services.recall.add_memory(mem)
    return {"id": mem.id, "status": "created", "content": mem.content}


def _handle_memory_update(services: Any, args) -> dict:
    memory = services.store.update(args.id, content=args.content, kind=args.kind)
    if memory is None:
        return {"error": "memory not found"}
    services.recall.rebuild_from_store()
    return {"id": memory.id, "status": "updated", "content": memory.content}


def _handle_memory_delete(services: Any, args) -> dict:
    if not services.store.delete(args.id):
        return {"error": "memory not found"}
    services.recall.rebuild_from_store()
    return {"id": args.id, "status": "deleted"}


def _handle_task_create(services: Any, args) -> dict:
    task = services.task_store.create(
        title=args.title,
        description=args.description,
        due_at=args.due_at,
    )
    return {"id": task.id, "status": task.status, "title": task.title}


def _handle_task_list(services: Any, args) -> dict:
    status = args.status.strip() or None
    tasks = services.task_store.list(status=status)
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks),
    }


def _handle_task_complete(services: Any, args) -> dict:
    task = services.task_store.complete(args.id)
    if task is None:
        return {"error": "open task not found"}
    task_reminder_service = getattr(services, "task_reminder_service", None)
    if task_reminder_service is not None:
        task_reminder_service.cancel_linked_reminders(task.id)
    return {"id": task.id, "status": task.status, "title": task.title}


def _handle_task_cancel(services: Any, args) -> dict:
    task = services.task_store.cancel(args.id)
    if task is None:
        return {"error": "open task not found"}
    task_reminder_service = getattr(services, "task_reminder_service", None)
    if task_reminder_service is not None:
        task_reminder_service.cancel_linked_reminders(task.id)
    return {"id": task.id, "status": task.status, "title": task.title}


def _get_reminder_store(services: Any):
    store = getattr(services, "reminder_store", None)
    if store is None:
        raise RuntimeError("reminder storage is unavailable")
    return store


def _handle_reminder_create(services: Any, args) -> dict:
    if args.task_id is not None and services.task_store.get(args.task_id) is None:
        return {"error": "task not found"}
    timezone_name = args.timezone or services.settings.timezone
    reminder = _get_reminder_store(services).create(
        args.title,
        args.scheduled_at,
        task_id=args.task_id,
        timezone_name=timezone_name,
    )
    scheduler = getattr(services, "reminder_scheduler", None)
    if scheduler is not None:
        scheduler.tick()
        reminder = _get_reminder_store(services).get(reminder.id) or reminder
    return {"reminder": reminder.to_dict()}


def _handle_reminder_list(services: Any, args) -> dict:
    status = args.status.strip() or None
    reminders = _get_reminder_store(services).list(status=status)
    return {"reminders": [reminder.to_dict() for reminder in reminders], "count": len(reminders)}


def _handle_reminder_cancel(services: Any, args) -> dict:
    reminder = _get_reminder_store(services).cancel(args.id)
    if reminder is None:
        return {"error": "scheduled reminder not found"}
    return {"id": reminder.id, "status": reminder.status, "title": reminder.title}


def _handle_task_create_with_reminder(services: Any, args) -> dict:
    timezone_name = args.timezone or services.settings.timezone
    task_reminder_service = getattr(services, "task_reminder_service", None)
    if task_reminder_service is not None:
        task, reminder = task_reminder_service.create_with_reminder(
            title=args.title,
            description=args.description,
            scheduled_at=args.scheduled_at,
            timezone_name=timezone_name,
        )
    else:
        task = services.task_store.create(
            title=args.title,
            description=args.description,
            due_at=args.scheduled_at,
        )
        reminder = _get_reminder_store(services).create(
            args.title,
            args.scheduled_at,
            task_id=task.id,
            timezone_name=timezone_name,
        )
    scheduler = getattr(services, "reminder_scheduler", None)
    if scheduler is not None:
        scheduler.tick()
        reminder = _get_reminder_store(services).get(reminder.id) or reminder
    return {"task": task.to_dict(), "reminder": reminder.to_dict()}


def build_default_registry() -> ToolRegistry:
    """Construct the allowlisted MVP tool registry."""
    from app.agent.schemas import (
        MemoryCreateArgs,
        MemoryDeleteArgs,
        MemorySearchArgs,
        MemoryUpdateArgs,
        TaskCreateArgs,
        TaskIdArgs,
        TaskListArgs,
        ReminderCreateArgs,
        ReminderIdArgs,
        ReminderListArgs,
        TaskWithReminderArgs,
    )

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="memory.search",
            description="Найти сохранённые воспоминания/факты о пользователе по теме или фразе.",
            args_schema=MemorySearchArgs,
            handler=_handle_memory_search,
        )
    )
    reg.register(
        ToolDefinition(
            name="task.create_with_reminder",
            description=(
                "Создать связанную задачу и одноразовое локальное напоминание. "
                "Используй, когда пользователь пишет «напомни» и указывает дату/время."
            ),
            args_schema=TaskWithReminderArgs,
            handler=_handle_task_create_with_reminder,
        )
    )
    reg.register(
        ToolDefinition(
            name="memory.update",
            description="Изменить текст или тип существующего воспоминания по его ID.",
            args_schema=MemoryUpdateArgs,
            handler=_handle_memory_update,
        )
    )
    reg.register(
        ToolDefinition(
            name="memory.delete",
            description="Удалить воспоминание по ID.",
            args_schema=MemoryDeleteArgs,
            handler=_handle_memory_delete,
        )
    )
    reg.register(
        ToolDefinition(
            name="memory.create",
            description="Сохранить новый факт или предпочтение о пользователе.",
            args_schema=MemoryCreateArgs,
            handler=_handle_memory_create,
        )
    )
    reg.register(
        ToolDefinition(
            name="task.create",
            description="Создать задачу для пользователя.",
            args_schema=TaskCreateArgs,
            handler=_handle_task_create,
        )
    )
    reg.register(
        ToolDefinition(
            name="task.list",
            description="Показать задачи пользователя.",
            args_schema=TaskListArgs,
            handler=_handle_task_list,
        )
    )
    reg.register(
        ToolDefinition(
            name="task.complete",
            description="Отметить открытую задачу выполненной по её ID.",
            args_schema=TaskIdArgs,
            handler=_handle_task_complete,
        )
    )
    reg.register(
        ToolDefinition(
            name="task.cancel",
            description="Отменить открытую задачу по её ID.",
            args_schema=TaskIdArgs,
            handler=_handle_task_cancel,
        )
    )
    reg.register(
        ToolDefinition(
            name="reminder.create",
            description="Создать одноразовое локальное напоминание, при необходимости связав его с существующей задачей.",
            args_schema=ReminderCreateArgs,
            handler=_handle_reminder_create,
        )
    )
    reg.register(
        ToolDefinition(
            name="reminder.list",
            description="Показать запланированные, сработавшие или отменённые напоминания пользователя.",
            args_schema=ReminderListArgs,
            handler=_handle_reminder_list,
        )
    )
    reg.register(
        ToolDefinition(
            name="reminder.cancel",
            description="Отменить запланированное напоминание по его ID.",
            args_schema=ReminderIdArgs,
            handler=_handle_reminder_cancel,
        )
    )
    return reg
