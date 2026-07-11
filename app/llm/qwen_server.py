from __future__ import annotations

import json
import threading
from collections.abc import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings


class ChatCompletionRequest(BaseModel):
    model: str = settings.model_id
    messages: list[dict]
    stream: bool = True
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    top_p: float = Field(default=0.95, gt=0.0, le=1.0)
    top_k: int = Field(default=20, ge=1, le=200)
    chat_template_kwargs: dict = Field(default_factory=dict)


class QwenRuntime:
    """Lazy Qwen3.5-4B runtime owned by the inference process only."""

    def __init__(self) -> None:
        self.processor = None
        self.model = None
        self._load_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        if self.loaded:
            return
        with self._load_lock:
            if self.loaded:
                return

            import torch
            from transformers import (
                AutoModelForMultimodalLM,
                AutoProcessor,
                BitsAndBytesConfig,
            )

            quantization = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
            self.processor = AutoProcessor.from_pretrained(settings.model_id)
            self.model = AutoModelForMultimodalLM.from_pretrained(
                settings.model_id,
                quantization_config=quantization,
                device_map="auto",
                dtype=torch.bfloat16,
            )

    def stream(self, request: ChatCompletionRequest) -> Iterator[str]:
        self.load()

        from transformers import TextIteratorStreamer

        thinking = bool(request.chat_template_kwargs.get("enable_thinking", False))
        prompt = self.processor.apply_chat_template(
            request.messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=thinking,
        )
        inputs = self.processor(
            text=prompt,
            return_tensors="pt",
        ).to(self.model.device)
        streamer = TextIteratorStreamer(
            self.processor,
            skip_prompt=True,
            skip_special_tokens=True,
            timeout=settings.llm_request_timeout,
        )
        generation = {
            **inputs,
            "streamer": streamer,
            "max_new_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "top_k": request.top_k,
            "do_sample": request.temperature > 0,
        }
        thread = threading.Thread(target=self.model.generate, kwargs=generation)
        thread.start()
        try:
            yield from streamer
        finally:
            thread.join()


runtime = QwenRuntime()
app = FastAPI(title="Second Brain Qwen Inference")


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": settings.model_id,
                "object": "model",
                "owned_by": "local",
                "loaded": runtime.loaded,
            }
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok", "model": settings.model_id, "loaded": runtime.loaded}


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest):
    if request.model != settings.model_id:
        raise HTTPException(status_code=404, detail="Model not found")

    if not request.stream:
        text = "".join(runtime.stream(request))
        return {
            "id": "local-completion",
            "object": "chat.completion",
            "model": settings.model_id,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": text}}
            ],
        }

    def events():
        for token in runtime.stream(request):
            event = {
                "id": "local-completion",
                "object": "chat.completion.chunk",
                "model": settings.model_id,
                "choices": [{"index": 0, "delta": {"content": token}}],
            }
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
