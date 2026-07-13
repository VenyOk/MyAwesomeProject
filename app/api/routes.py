from __future__ import annotations

import json
from dataclasses import replace
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app.agent.execution import execute_confirmed_tool, execute_tool
from app.chat.commands import REGISTRY
from app.llm.response import clean_response
from app.tasks.reminders import normalize_scheduled_at

router = APIRouter()


class ChatRequest(BaseModel):
    chat_id: int
    message: str


class CommandRequest(BaseModel):
    input: str
    chat_id: int | None = None


class CreateChatRequest(BaseModel):
    title: str | None = None
    folder_id: int | None = None


class UpdateChatRequest(BaseModel):
    title: str | None = None
    folder_id: int | None = None
    pinned: bool | None = None


class CreateFolderRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateFolderRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class MoveChatRequest(BaseModel):
    folder_id: int | None = None


class UpdateMessageRequest(BaseModel):
    content: str


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    due_at: str | None = None
    priority: int = 0


class CreateTaskWithReminderRequest(BaseModel):
    title: str
    description: str = ""
    scheduled_at: str
    timezone: str | None = None
    priority: int = 0


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    due_at: str | None = None
    priority: int | None = None


class CreateReminderRequest(BaseModel):
    title: str
    scheduled_at: str
    task_id: int | None = None
    timezone: str | None = None
    recurrence_rule: str | None = None


class UpdateReminderRequest(BaseModel):
    title: str | None = None
    scheduled_at: str | None = None
    timezone: str | None = None
    recurrence_rule: str | None = None


class UpdateSettingsRequest(BaseModel):
    timezone: str | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


def _services(request: Request):
    return request.app.state.services


def _reminder_store(request: Request):
    store = getattr(_services(request), "reminder_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Reminder storage is unavailable")
    return store


def _task_reminder_service(request: Request):
    service = getattr(_services(request), "task_reminder_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Task/reminder storage is unavailable")
    return service


def _outbox_store(request: Request):
    store = getattr(_services(request), "outbox_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Notification storage is unavailable")
    return store


def _validate_reminder_time(value: str, timezone_name: str) -> None:
    try:
        normalize_scheduled_at(value, timezone_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=422, detail="timezone must be a valid IANA timezone") from None
    return value


def _normalize_clock(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="quiet hours must use HH:MM") from None
    return parsed.strftime("%H:%M")


@router.get("/health")
def health(request: Request):
    services = _services(request)
    return {
        "status": "ok",
        "model": services.settings.model_id,
        "llm_provider": services.settings.llm_provider,
        "model_loaded": services.llm.is_loaded(),
        "memories": services.store.count(),
        "index_size": services.recall.index.size,
        "chats": len(services.chat_store.list_chats()),
    }


@router.get("/settings")
def get_settings(request: Request):
    services = _services(request)
    return {
        "timezone": services.settings.timezone,
        "quiet_hours_start": services.settings.quiet_hours_start,
        "quiet_hours_end": services.settings.quiet_hours_end,
        "model": services.settings.model_id,
        "scheduler_interval_seconds": services.settings.scheduler_interval_seconds,
    }


@router.patch("/settings")
def update_settings(payload: UpdateSettingsRequest, request: Request):
    services = _services(request)
    current = {
        "timezone": services.settings.timezone,
        "quiet_hours_start": services.settings.quiet_hours_start,
        "quiet_hours_end": services.settings.quiet_hours_end,
    }
    fields = payload.model_dump(exclude_unset=True)
    timezone_name = _validate_timezone(fields.get("timezone", current["timezone"]))
    quiet_start = _normalize_clock(fields.get("quiet_hours_start", current["quiet_hours_start"]))
    quiet_end = _normalize_clock(fields.get("quiet_hours_end", current["quiet_hours_end"]))
    if (quiet_start is None) != (quiet_end is None):
        raise HTTPException(status_code=422, detail="quiet hours require both start and end")

    services.settings.timezone = timezone_name
    services.settings.quiet_hours_start = quiet_start
    services.settings.quiet_hours_end = quiet_end
    settings_store = getattr(services, "settings_store", None)
    if settings_store is not None:
        settings_store.save(timezone_name, quiet_start, quiet_end)
    scheduler = getattr(services, "reminder_scheduler", None)
    if scheduler is not None:
        scheduler.set_quiet_hours(quiet_start, quiet_end)
    return get_settings(request)


# ---------------------------- chats ----------------------------


@router.get("/chats")
def list_chats(request: Request):
    services = _services(request)
    chats = services.chat_store.list_chats()
    return {"chats": [c.to_dict() for c in chats]}


@router.post("/chats")
def create_chat(payload: CreateChatRequest, request: Request):
    services = _services(request)
    chat = services.chat_store.create_chat(title=payload.title)
    if payload.folder_id is not None:
        chat = services.chat_store.move_chat(chat.id, payload.folder_id) or chat
    return chat.to_dict()


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, request: Request):
    services = _services(request)
    chat = services.chat_store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat.to_dict()


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, payload: UpdateChatRequest, request: Request):
    services = _services(request)
    chat = services.chat_store.update_chat(
        chat_id,
        title=payload.title,
        pinned=payload.pinned,
        folder_id=payload.folder_id,
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat.to_dict()


@router.patch("/chats/{chat_id}/move")
def move_chat(chat_id: int, payload: MoveChatRequest, request: Request):
    services = _services(request)
    chat = services.chat_store.move_chat(chat_id, payload.folder_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat.to_dict()


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: int, request: Request):
    services = _services(request)
    if not services.chat_store.delete(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"deleted": chat_id}


@router.get("/chats/{chat_id}/messages")
def list_messages(chat_id: int, request: Request):
    services = _services(request)
    if services.chat_store.get(chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    messages = services.chat_store.list_messages(chat_id)
    return {"messages": [m.to_dict() for m in messages]}


# ---------------------------- chat (LLM) ----------------------------


@router.post("/chat")
def chat(payload: ChatRequest, request: Request):
    services = _services(request)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    chat = services.chat_store.get(payload.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    # global memory recall (RAG) — memories are shared across all chats
    context = services.recall.build_context(message, k=services.settings.recall_top_k)
    from app.chat.session import _today_msk

    system = f"{services.ctx.persona}\n\nТекущая дата (МСК): {_today_msk()}."
    # shared folder context: if the chat belongs to a folder with a description,
    # surface it so all chats in the folder share that context.
    if chat.folder_id:
        folder = services.chat_store.get_folder(chat.folder_id)
        if folder and folder.description.strip():
            system += f"\n\n[Контекст папки «{folder.name}»]\n{folder.description.strip()}"
    if context:
        system = system + "\n\n" + context

    # persist user turn. Memory extraction happens AFTER the answer is generated
    # (plan §9): a casual reply must not be force-saved, and the current message
    # must not feed RAG in the same request.
    user_msg = services.chat_store.add_message(chat.id, "user", message)
    services.chat_store.maybe_title_from_first_message(chat.id, message)

    history = [{"role": m.role, "content": m.content} for m in services.chat_store.list_messages(chat.id)]
    messages = [{"role": "system", "content": system}] + history

    def event_stream():
        from app.llm.response import StreamCleaner

        accumulated: list[str] = []
        saved = False
        try:
            # When tools are available, run the orchestrator loop: the model may
            # call tools and produce a final answer from their results. Otherwise
            # fall back to plain token streaming.
            use_orchestrator = bool(getattr(services, "tool_registry", None))
            if use_orchestrator:
                from app.agent.orchestrator import run_turn

                full_parts: list[str] = []
                cleaner = StreamCleaner()
                for event in run_turn(
                    messages,
                    services.llm,
                    services.tool_registry,
                    services,
                    chat_id=chat.id,
                    max_new_tokens=services.settings.max_new_tokens,
                ):
                    if event.kind == "text" and event.payload.get("content"):
                        text = cleaner.feed(event.payload["content"])
                        if text:
                            full_parts.append(text)
                            yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
                    elif event.kind in ("tool_started", "tool_finished", "tool_error", "confirmation_required"):
                        yield f"data: {json.dumps(event.payload | {'type': event.kind}, ensure_ascii=False)}\n\n"
                tail = cleaner.flush()
                if tail:
                    full_parts.append(tail)
                    yield f"data: {json.dumps({'token': tail}, ensure_ascii=False)}\n\n"
                full = clean_response("".join(full_parts))
            else:
                cleaner = StreamCleaner()
                for chunk in services.llm.generate(
                    messages, max_new_tokens=services.settings.max_new_tokens
                ):
                    accumulated.append(chunk)
                    safe = cleaner.feed(chunk)
                    if safe:
                        yield f"data: {json.dumps({'token': safe}, ensure_ascii=False)}\n\n"
                tail = cleaner.flush()
                if tail:
                    accumulated.append(tail)
                    yield f"data: {json.dumps({'token': tail}, ensure_ascii=False)}\n\n"
                full = clean_response("".join(accumulated))
            if full.strip():
                assistant_msg = services.chat_store.add_message(chat.id, "assistant", full)
            else:
                assistant_msg = services.chat_store.add_message(chat.id, "assistant", "(нет ответа)")
            saved = True
            title = services.chat_store.get(chat.id).title
            yield f"data: {json.dumps({'done': True, 'user_message': user_msg.to_dict(), 'assistant_message': assistant_msg.to_dict(), 'title': title}, ensure_ascii=False)}\n\n"
            # Memory extraction (plan §10). Runs only when auto-save is enabled.
            # Casual messages yield no candidates; explicit "запомни ..." becomes
            # active, inferred facts become candidate pending confirmation.
            if services.settings.auto_save:
                from app.memory.extractor import (
                    candidate_to_memory_kwargs,
                    extract_candidates,
                )

                created: list[dict] = []
                for cand in extract_candidates(
                    message, services.llm, max_new_tokens=512
                ):
                    kwargs = candidate_to_memory_kwargs(cand)
                    mem = services.store.add(
                        source="chat",
                        source_type="chat",
                        source_message_id=user_msg.id,
                        **kwargs,
                    )
                    services.recall.add_memory(mem)
                    created.append({"id": mem.id, "status": mem.status, "kind": mem.kind, "content": mem.content})
                if created:
                    yield f"data: {json.dumps({'memory_created': created}, ensure_ascii=False)}\n\n"
        finally:
            # client disconnected (e.g. pressed Stop) — persist the partial answer
            if not saved and accumulated:
                services.chat_store.add_message(
                    chat.id, "assistant", clean_response("".join(accumulated))
                )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------- confirmations ----------------------------


def _agent_store(request: Request):
    store = _services(request).agent_store
    if store is None:
        raise HTTPException(status_code=503, detail="Confirmation storage is unavailable")
    return store


@router.get("/confirmations")
def list_confirmations(request: Request, chat_id: int | None = None):
    confirmations = _agent_store(request).list_confirmations(chat_id=chat_id)
    return {"confirmations": [confirmation.to_dict() for confirmation in confirmations]}


@router.get("/tool-runs")
def list_tool_runs(request: Request, chat_id: int | None = None):
    tool_runs = _agent_store(request).list_tool_runs(chat_id=chat_id)
    return {"tool_runs": [tool_run.to_dict() for tool_run in tool_runs]}


@router.post("/confirmations/{confirmation_id}/approve")
def approve_confirmation(confirmation_id: int, request: Request):
    services = _services(request)
    store = _agent_store(request)
    confirmation = store.resolve_confirmation(confirmation_id, approved=True)
    if confirmation is None:
        raise HTTPException(status_code=409, detail="Confirmation is no longer pending")
    result = execute_confirmed_tool(services.tool_registry, services, confirmation)
    return {"confirmation": confirmation.to_dict(), "result": result}


@router.post("/confirmations/{confirmation_id}/reject")
def reject_confirmation(confirmation_id: int, request: Request):
    store = _agent_store(request)
    confirmation = store.resolve_confirmation(confirmation_id, approved=False)
    if confirmation is None:
        raise HTTPException(status_code=409, detail="Confirmation is no longer pending")
    result = {"status": "rejected"}
    store.finish_tool_run(confirmation.tool_run_id, "rejected", result)
    return {"confirmation": confirmation.to_dict(), "result": result}


# ---------------------------- tasks ----------------------------


@router.get("/tasks")
def list_tasks(request: Request, status: str | None = "open"):
    services = _services(request)
    return {"tasks": [task.to_dict() for task in services.task_store.list(status=status)]}


@router.post("/tasks")
def create_task(payload: CreateTaskRequest, request: Request):
    task = _services(request).task_store.create(
        payload.title,
        payload.description,
        due_at=payload.due_at,
        priority=payload.priority,
    )
    return task.to_dict()


@router.post("/tasks/with-reminder")
def create_task_with_reminder(payload: CreateTaskWithReminderRequest, request: Request):
    services = _services(request)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    timezone_name = payload.timezone or services.settings.timezone
    _validate_reminder_time(payload.scheduled_at, timezone_name)
    task, reminder = _task_reminder_service(request).create_with_reminder(
        title=title,
        description=payload.description,
        scheduled_at=payload.scheduled_at,
        priority=payload.priority,
        timezone_name=timezone_name,
    )
    scheduler = getattr(services, "reminder_scheduler", None)
    if scheduler is not None:
        scheduler.tick()
        reminder = _reminder_store(request).get(reminder.id) or reminder
    return {"task": task.to_dict(), "reminder": reminder.to_dict()}


@router.get("/tasks/{task_id}")
def get_task(task_id: int, request: Request):
    task = _services(request).task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: UpdateTaskRequest, request: Request):
    fields = payload.model_fields_set
    changes = {
        name: getattr(payload, name)
        for name in fields
        if name == "due_at" or getattr(payload, name) is not None
    }
    task = _services(request).task_store.update(
        task_id,
        **changes,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, request: Request):
    services = _services(request)
    task = services.task_store.complete(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Open task not found")
    task_reminder_service = getattr(services, "task_reminder_service", None)
    if task_reminder_service is not None:
        task_reminder_service.cancel_linked_reminders(task_id)
    return task.to_dict()


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, request: Request):
    services = _services(request)
    task = services.task_store.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Open task not found")
    task_reminder_service = getattr(services, "task_reminder_service", None)
    if task_reminder_service is not None:
        task_reminder_service.cancel_linked_reminders(task_id)
    return task.to_dict()


# ---------------------------- reminders and notifications ----------------------------


@router.get("/reminders")
def list_reminders(request: Request, status: str | None = "scheduled"):
    reminders = _reminder_store(request).list(status=status)
    return {"reminders": [reminder.to_dict() for reminder in reminders]}


@router.post("/reminders")
def create_reminder(payload: CreateReminderRequest, request: Request):
    services = _services(request)
    timezone_name = payload.timezone or services.settings.timezone
    _validate_reminder_time(payload.scheduled_at, timezone_name)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    if payload.task_id is not None and services.task_store.get(payload.task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    reminder = _reminder_store(request).create(
        title,
        payload.scheduled_at,
        task_id=payload.task_id,
        timezone_name=timezone_name,
        recurrence_rule=payload.recurrence_rule,
    )
    # A past time is a missed reminder, so surface it without waiting for the
    # next minute-long background tick. Future reminders remain scheduled.
    scheduler = getattr(services, "reminder_scheduler", None)
    if scheduler is not None:
        scheduler.tick()
    return _reminder_store(request).get(reminder.id).to_dict()


@router.get("/reminders/{reminder_id}")
def get_reminder(reminder_id: int, request: Request):
    reminder = _reminder_store(request).get(reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return reminder.to_dict()


@router.patch("/reminders/{reminder_id}")
def update_reminder(reminder_id: int, payload: UpdateReminderRequest, request: Request):
    store = _reminder_store(request)
    current = store.get(reminder_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    fields = payload.model_fields_set
    timezone_name = payload.timezone if "timezone" in fields else current.timezone
    if timezone_name is None:
        raise HTTPException(status_code=422, detail="timezone must not be null")
    if "scheduled_at" in fields:
        if payload.scheduled_at is None:
            raise HTTPException(status_code=422, detail="scheduled_at must not be null")
        _validate_reminder_time(payload.scheduled_at, timezone_name)
    elif "timezone" in fields:
        _validate_reminder_time(current.scheduled_at, timezone_name)
    changes = {}
    if "title" in fields:
        if payload.title is None or not payload.title.strip():
            raise HTTPException(status_code=422, detail="title must not be empty")
        changes["title"] = payload.title.strip()
    if "scheduled_at" in fields:
        changes["scheduled_at"] = payload.scheduled_at
    if "timezone" in fields:
        changes["timezone_name"] = timezone_name
    if "recurrence_rule" in fields:
        changes["recurrence_rule"] = payload.recurrence_rule
    reminder = store.update(reminder_id, **changes)
    if reminder is None:
        raise HTTPException(status_code=409, detail="Only scheduled reminders can be updated")
    return reminder.to_dict()


def _cancel_reminder(reminder_id: int, request: Request):
    reminder = _reminder_store(request).cancel(reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="Scheduled reminder not found")
    return reminder.to_dict()


@router.post("/reminders/{reminder_id}/cancel")
def cancel_reminder(reminder_id: int, request: Request):
    return _cancel_reminder(reminder_id, request)


@router.delete("/reminders/{reminder_id}")
def delete_reminder(reminder_id: int, request: Request):
    return _cancel_reminder(reminder_id, request)


@router.get("/notifications")
def list_notifications(request: Request):
    notifications = _outbox_store(request).list_available()
    return {"notifications": [notification.to_dict() for notification in notifications]}


@router.post("/notifications/{notification_id}/ack")
def acknowledge_notification(notification_id: int, request: Request):
    store = _outbox_store(request)
    item = store.get(notification_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if item.status == "pending":
        store.mark_sent(notification_id)
        item = store.get(notification_id)
    return item.to_dict()


# ---------------------------- commands ----------------------------


@router.post("/command")
def command(payload: CommandRequest, request: Request):
    services = _services(request)
    command_ctx = replace(
        services.ctx,
        current_chat_id=payload.chat_id,
        tool_executor=lambda tool_name, arguments: list(
            execute_tool(
                services.tool_registry,
                services,
                tool_name,
                arguments,
                chat_id=payload.chat_id,
            )
        ),
    )
    result = REGISTRY.dispatch(payload.input, command_ctx)
    if result is None:
        return {"is_command": False}
    return {
        "is_command": True,
        "text": result.text,
        "error": result.error,
        "tool_run_id": result.tool_run_id,
        "tool_events": result.tool_events,
        "confirmation": result.confirmation,
    }


# ---------------------------- folders ----------------------------


@router.get("/folders")
def list_folders(request: Request):
    services = _services(request)
    return {"folders": [f.to_dict() for f in services.chat_store.list_folders()]}


@router.post("/folders")
def create_folder(payload: CreateFolderRequest, request: Request):
    services = _services(request)
    folder = services.chat_store.create_folder(payload.name, payload.description or "")
    return folder.to_dict()


@router.patch("/folders/{folder_id}")
def update_folder(folder_id: int, payload: UpdateFolderRequest, request: Request):
    services = _services(request)
    folder = services.chat_store.rename_folder(
        folder_id, name=payload.name, description=payload.description
    )
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder.to_dict()


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, request: Request):
    services = _services(request)
    if not services.chat_store.delete_folder(folder_id):
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"deleted": folder_id}


# ---------------------------- messages ----------------------------


@router.patch("/chats/{chat_id}/messages/{message_id}")
def update_message(chat_id: int, message_id: int, payload: UpdateMessageRequest, request: Request):
    services = _services(request)
    msg = services.chat_store.get_message(message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Message not found")
    updated = services.chat_store.update_message(message_id, payload.content)
    affected_memories = services.store.mark_for_reextraction(message_id)
    if affected_memories:
        services.recall.rebuild_from_store()
    response = updated.to_dict()
    response["memory_recheck_count"] = len(affected_memories)
    response["memory_recheck_memory_ids"] = [memory.id for memory in affected_memories]
    return response


@router.get("/chats/{chat_id}/messages/{message_id}/memories")
def list_message_memories(chat_id: int, message_id: int, request: Request):
    services = _services(request)
    msg = services.chat_store.get_message(message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Message not found")
    memories = services.store.list_by_source_message(message_id)
    return {"memories": [memory.to_dict() for memory in memories], "count": len(memories)}


@router.delete("/chats/{chat_id}/messages/{message_id}")
def delete_message(
    chat_id: int,
    message_id: int,
    request: Request,
    derived_memories: str | None = None,
):
    services = _services(request)
    msg = services.chat_store.get_message(message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if derived_memories not in (None, "keep", "delete"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_derived_memories_action",
                "message": "derived_memories must be either 'keep' or 'delete'",
                "allowed_actions": ["keep", "delete"],
            },
        )

    linked_memories = services.store.list_by_source_message(message_id)
    if linked_memories and derived_memories is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "derived_memories_confirmation_required",
                "message": "Choose whether to keep or delete memories derived from this message.",
                "derived_memories": {
                    "count": len(linked_memories),
                    "memories": [memory.to_dict() for memory in linked_memories],
                },
                "allowed_actions": ["keep", "delete"],
            },
        )

    deleted_memories = []
    if linked_memories and derived_memories == "delete":
        deleted_memories = services.store.delete_by_source_message(message_id)
        services.recall.rebuild_from_store()

    services.chat_store.delete_message(message_id)
    if not linked_memories:
        return {"deleted": message_id}
    selected_memories = deleted_memories if derived_memories == "delete" else linked_memories
    return {
        "deleted": message_id,
        "derived_memories": {
            "action": derived_memories,
            "count": len(selected_memories),
            "memories": [memory.to_dict() for memory in selected_memories],
        },
    }


@router.get("/messages/search")
def search_messages(request: Request, q: str, chat_id: int | None = None,
                    folder_id: int | None = None, limit: int = 50):
    services = _services(request)
    results = services.chat_store.search_messages(q, limit=limit)
    # optional filters
    if chat_id is not None:
        results = [m for m in results if m.chat_id == chat_id]
    if folder_id is not None:
        chat_ids = {
            c.id for c in services.chat_store.list_chats() if c.folder_id == folder_id
        }
        results = [m for m in results if m.chat_id in chat_ids]
    return {
        "messages": [m.to_dict() for m in results],
        "count": len(results),
        "query": q,
    }


# ---------------------------- export ----------------------------


@router.get("/chats/{chat_id}/export", response_class=PlainTextResponse)
def export_chat(chat_id: int, request: Request, format: str = "md"):
    services = _services(request)
    chat = services.chat_store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    messages = services.chat_store.list_messages(chat_id)
    lines = [f"# {chat.title}", ""]
    for m in messages:
        who = "Пользователь" if m.role == "user" else ("Ассистент" if m.role == "assistant" else "Система")
        stamp = m.created_at
        lines.append(f"### {who} — {stamp}")
        if m.role == "assistant":
            lines.append("")
            lines.append(m.content)
        else:
            lines.append("")
            lines.append(m.content)
        lines.append("")
    return "\n".join(lines)


# ---------------------------- memories ----------------------------


@router.get("/memories")
def list_memories(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
):
    services = _services(request)
    if status:
        memories = services.store.list_by_status(status, limit=limit)
    elif q:
        memories = services.store.search_text(q, limit=limit)
    else:
        memories = services.store.list_recent(limit)
    if kind:
        memories = [m for m in memories if m.kind == kind]
    return {"memories": [m.to_dict() for m in memories], "count": len(memories)}


@router.get("/memories/{memory_id}")
def get_memory(memory_id: int, request: Request):
    services = _services(request)
    memory = services.store.get(memory_id)
    if memory is None or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory.to_dict()


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    kind: str | None = None
    summary: str | None = None


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: int, payload: UpdateMemoryRequest, request: Request):
    services = _services(request)
    existing = services.store.get(memory_id)
    if existing is None or existing.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")
    updated = services.store.update(
        memory_id,
        content=payload.content,
        kind=payload.kind,
        summary=payload.summary,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    # content changed -> keep the FAISS index in sync
    if payload.content:
        services.recall.rebuild_from_store()
    return updated.to_dict()


@router.post("/memories/{memory_id}/activate")
def activate_memory(memory_id: int, request: Request):
    services = _services(request)
    memory = services.store.activate(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    services.recall.add_memory(memory)
    return memory.to_dict()


@router.post("/memories/{memory_id}/restore")
def restore_memory(memory_id: int, request: Request):
    services = _services(request)
    memory = services.store.restore(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    services.recall.rebuild_from_store()
    return memory.to_dict()


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, request: Request):
    services = _services(request)
    if not services.store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    services.recall.rebuild_from_store()
    return {"deleted": memory_id}


@router.get("/tags")
def list_tags(request: Request):
    services = _services(request)
    return {"tags": services.store.tag_counts()}
