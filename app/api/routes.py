from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.chat.commands import REGISTRY
from app.llm.gemma import clean_response

router = APIRouter()


class ChatRequest(BaseModel):
    chat_id: int
    message: str


class CommandRequest(BaseModel):
    input: str
    chat_id: int | None = None


class CreateChatRequest(BaseModel):
    title: str | None = None


class RenameChatRequest(BaseModel):
    title: str


def _services(request: Request):
    return request.app.state.services


@router.get("/health")
def health(request: Request):
    services = _services(request)
    return {
        "status": "ok",
        "model": services.settings.model_id,
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
    return chat.to_dict()


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, request: Request):
    services = _services(request)
    chat = services.chat_store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat.to_dict()


@router.patch("/chats/{chat_id}")
def rename_chat(chat_id: int, payload: RenameChatRequest, request: Request):
    services = _services(request)
    chat = services.chat_store.rename(chat_id, payload.title)
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
    if context:
        system = system + "\n\n" + context

    # persist user turn + auto-save a global memory
    user_msg = services.chat_store.add_message(chat.id, "user", message)
    services.chat_store.maybe_title_from_first_message(chat.id, message)
    if services.settings.auto_save:
        mem = services.store.add(content=message, source="chat")
        services.recall.add_memory(mem)

    history = [{"role": m.role, "content": m.content} for m in services.chat_store.list_messages(chat.id)]
    messages = [{"role": "system", "content": system}] + history

    def event_stream():
        accumulated: list[str] = []
        for chunk in services.llm.generate(
            messages, max_new_tokens=services.settings.max_new_tokens
        ):
            accumulated.append(chunk)
            yield f"data: {json.dumps({'token': chunk}, ensure_ascii=False)}\n\n"
        full = clean_response("".join(accumulated))
        assistant_msg = services.chat_store.add_message(chat.id, "assistant", full)
        title = services.chat_store.get(chat.id).title
        yield f"data: {json.dumps({'done': True, 'user_message': user_msg.to_dict(), 'assistant_message': assistant_msg.to_dict(), 'title': title}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------- commands ----------------------------


@router.post("/command")
def command(payload: CommandRequest, request: Request):
    services = _services(request)
    services.ctx.current_chat_id = payload.chat_id
    result = REGISTRY.dispatch(payload.input, services.ctx)
    if result is None:
        return {"is_command": False}
    return {"is_command": True, "text": result.text, "error": result.error}


# ---------------------------- memories ----------------------------


@router.get("/memories")
def list_memories(request: Request, q: str | None = None, limit: int = 50):
    services = _services(request)
    if q:
        memories = services.store.search_text(q, limit=limit)
    else:
        memories = services.store.list_recent(limit)
    return {"memories": [m.to_dict() for m in memories], "count": len(memories)}


@router.get("/memories/{memory_id}")
def get_memory(memory_id: int, request: Request):
    services = _services(request)
    memory = services.store.get(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
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
