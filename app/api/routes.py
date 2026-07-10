from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
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
        saved = False
        try:
            for chunk in services.llm.generate(
                messages, max_new_tokens=services.settings.max_new_tokens
            ):
                accumulated.append(chunk)
                yield f"data: {json.dumps({'token': chunk}, ensure_ascii=False)}\n\n"
            full = clean_response("".join(accumulated))
            assistant_msg = services.chat_store.add_message(chat.id, "assistant", full)
            saved = True
            title = services.chat_store.get(chat.id).title
            yield f"data: {json.dumps({'done': True, 'user_message': user_msg.to_dict(), 'assistant_message': assistant_msg.to_dict(), 'title': title}, ensure_ascii=False)}\n\n"
        finally:
            # client disconnected (e.g. pressed Stop) — persist the partial answer
            if not saved and accumulated:
                services.chat_store.add_message(
                    chat.id, "assistant", clean_response("".join(accumulated))
                )

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
