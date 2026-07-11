from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.llm.openai_compatible import OpenAICompatibleLLM
from app.llm.response import StreamCleaner, clean_response


def test_openai_compatible_provider_streams_qwen_tokens():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen3.5-4B"}]})
        captured.update(json.loads(request.content))
        body = (
            'data: {"choices":[{"delta":{"content":"Привет "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"от Qwen"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    settings = Settings(
        model_id="Qwen/Qwen3.5-4B",
        llm_base_url="http://inference.test/v1",
        thinking_enabled=False,
    )
    provider = OpenAICompatibleLLM(settings, transport=httpx.MockTransport(handler))

    assert provider.is_loaded() is True
    assert "".join(provider.generate([{"role": "user", "content": "Привет"}])) == (
        "Привет от Qwen"
    )
    assert captured["model"] == "Qwen/Qwen3.5-4B"
    assert captured["stream"] is True
    assert captured["chat_template_kwargs"]["enable_thinking"] is False


def test_clean_response_removes_qwen_thinking_block():
    raw = (
        "<think>скрытое рассуждение</think>\n\n"
        "Итоговый ответ<|im_end|><|endoftext|>"
    )
    assert clean_response(raw) == "Итоговый ответ"


def test_stream_cleaner_removes_special_tokens_in_one_chunk():
    cleaner = StreamCleaner()
    out = cleaner.feed("Ответ<|im_end|><|endoftext|>")
    out += cleaner.flush()
    assert out == "Ответ"


def test_stream_cleaner_handles_token_split_across_chunks():
    """The Qwen inference stream sometimes splits a special token like
    ``<|im_end|>`` across several SSE chunks. The cleaner must buffer the
    incomplete tail instead of leaking fragments to the UI."""
    cleaner = StreamCleaner()
    parts = ["Ответ ", "<|im_", "end", "|>", "<|endoftext|>"]
    out = "".join(cleaner.feed(p) for p in parts)
    out += cleaner.flush()
    assert out == "Ответ "


def test_stream_cleaner_releases_partial_lookalike_on_flush():
    """Text that merely *starts* like a token but is ordinary text must be
    emitted (on flush if buffered), never dropped."""
    cleaner = StreamCleaner()
    out = cleaner.feed("цена 50<|")
    out += cleaner.flush()
    assert out == "цена 50<|"


def test_stream_cleaner_passes_plain_text_through():
    cleaner = StreamCleaner()
    out = cleaner.feed("Привет от Qwen3.5")
    out += cleaner.flush()
    assert out == "Привет от Qwen3.5"


def test_chat_endpoint_strips_artifacts_from_stream(client):
    """End-to-end: artifacts emitted by the model must not reach the SSE
    stream the UI consumes, nor the persisted assistant message."""
    import app.chat.commands as cmd_mod
    from app.api.routes import router

    # Inject a fake LLM into the running services that emits the exact
    # artifacts the user reported.
    class LeakyLLM:
        thinking = False

        def is_loaded(self) -> bool:
            return True

        def generate(self, messages, max_new_tokens=None):
            yield "Привет"
            yield "<|im_end|>"
            yield "<|endoftext|>"

    client.get("/api/health")  # warm up app state
    services = client.app.state.services  # type: ignore[attr-defined]
    original_llm = services.llm
    services.llm = LeakyLLM()  # type: ignore[assignment]
    services.ctx.llm = services.llm
    try:
        chat = client.post("/api/chats", json={"title": None}).json()
        body = client.post(
            "/api/chat", json={"chat_id": chat["id"], "message": "привет"}
        ).text
    finally:
        services.llm = original_llm
        services.ctx.llm = original_llm

    # No artifact should appear anywhere in the streamed tokens.
    assert "<|im_end|>" not in body
    assert "<|endoftext|>" not in body
    # The persisted assistant message must be clean too.
    msgs = client.get(f"/api/chats/{chat['id']}/messages").json()["messages"]
    assert msgs[-1]["content"].strip() == "Привет"
    _ = cmd_mod, router  # silence unused imports
