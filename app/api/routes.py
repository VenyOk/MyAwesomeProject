from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.chat.commands import REGISTRY
from app.llm.gemma import clean_response

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class CommandRequest(BaseModel):
    input: str


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
    }


@router.post("/chat")
def chat(payload: ChatRequest, request: Request):
    services = _services(request)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    context = services.recall.build_context(message, k=services.settings.recall_top_k)
    system = services.ctx.persona
    if context:
        system = system + "\n\n" + context

    services.session.add("user", message)
    if services.settings.auto_save:
        mem = services.store.add(content=message, source="chat")
        services.recall.add_memory(mem)

    messages = services.session.with_system(system)

    def event_stream():
        accumulated: list[str] = []
        for chunk in services.llm.generate(
            messages, max_new_tokens=services.settings.max_new_tokens
        ):
            accumulated.append(chunk)
            yield f"data: {json.dumps({'token': chunk}, ensure_ascii=False)}\n\n"
        full = clean_response("".join(accumulated))
        services.session.add("assistant", full)
        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/command")
def command(payload: CommandRequest, request: Request):
    services = _services(request)
    result = REGISTRY.dispatch(payload.input, services.ctx)
    if result is None:
        return {"is_command": False}
    return {"is_command": True, "text": result.text, "error": result.error}


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
