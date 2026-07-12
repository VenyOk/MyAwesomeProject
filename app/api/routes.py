from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app.chat.commands import REGISTRY
from app.llm.response import clean_response

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


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    due_at: str | None = None
    priority: int | None = None


def _services(request: Request):
    return request.app.state.services


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
    system = services.ctx.persona
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


@router.post("/confirmations/{confirmation_id}/approve")
def approve_confirmation(confirmation_id: int, request: Request):
    services = _services(request)
    store = _agent_store(request)
    confirmation = store.resolve_confirmation(confirmation_id, approved=True)
    if confirmation is None:
        raise HTTPException(status_code=409, detail="Confirmation is no longer pending")
    result = services.tool_registry.dispatch(
        confirmation.tool_name, confirmation.arguments, services
    )
    store.finish_tool_run(
        confirmation.tool_run_id,
        "failed" if "error" in result else "succeeded",
        result,
    )
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
    task = _services(request).task_store.complete(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Open task not found")
    return task.to_dict()


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, request: Request):
    task = _services(request).task_store.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Open task not found")
    return task.to_dict()


# ---------------------------- commands ----------------------------


@router.post("/command")
def command(payload: CommandRequest, request: Request):
    services = _services(request)
    services.ctx.current_chat_id = payload.chat_id
    result = REGISTRY.dispatch(payload.input, services.ctx)
    if result is None:
        return {"is_command": False}
    return {"is_command": True, "text": result.text, "error": result.error}


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
    return updated.to_dict()


@router.delete("/chats/{chat_id}/messages/{message_id}")
def delete_message(chat_id: int, message_id: int, request: Request):
    services = _services(request)
    msg = services.chat_store.get_message(message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Message not found")
    services.chat_store.delete_message(message_id)
    return {"deleted": message_id}


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
