from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.llm.base import LLMChunk
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

        def generate(self, messages, max_new_tokens=None, tools=None):
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


def test_generate_with_tools_accumulates_split_tool_call():
    """Ollama streams tool_calls as partial JSON fragments across chunks;
    arguments must be concatenated per index before parsing."""
    chunks = [
        # first fragment: id + name + empty arguments
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"memory.search","arguments":""}}]}}]}\n\n',
        # argument fragment 1
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"qu"}}]}}]}\n\n',
        # argument fragment 2
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ery\\":\\"питон\\"}"}}]}}]}\n\n',
        # finish
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    body = "".join(chunks)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.5:9b"}]})
        return httpx.Response(200, text=body)

    settings = Settings(model_id="qwen3.5:9b", llm_base_url="http://x/v1")
    provider = OpenAICompatibleLLM(settings, transport=httpx.MockTransport(handler))

    out = list(provider.generate_with_tools(
        [{"role": "user", "content": "find"}],
        [{"type": "function", "function": {"name": "memory.search"}}],
    ))
    # expect exactly one tool_call chunk with parsed arguments
    tc_chunks = [c for c in out if c.kind == "tool_call"]
    assert len(tc_chunks) == 1
    assert len(tc_chunks[0].tool_calls) == 1
    part = tc_chunks[0].tool_calls[0]
    assert part.name == "memory.search"
    assert part.arguments == {"query": "питон"}
    assert part.id == "call_1"


def test_generate_with_tools_yields_text_and_tool_call():
    """A turn may contain both prose (content) and a tool call."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.5:9b"}]})
        body = (
            'data: {"choices":[{"delta":{"content":"Ищу..."}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"task.create","arguments":"{\\"title\\":\\"молоко\\"}"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    settings = Settings(model_id="qwen3.5:9b", llm_base_url="http://x/v1")
    provider = OpenAICompatibleLLM(settings, transport=httpx.MockTransport(handler))
    out = list(provider.generate_with_tools([{"role": "user", "content": "x"}], []))
    text_chunks = [c for c in out if c.kind == "text"]
    tc_chunks = [c for c in out if c.kind == "tool_call"]
    assert text_chunks and text_chunks[0].content == "Ищу..."
    assert len(tc_chunks) == 1
    assert tc_chunks[0].tool_calls[0].arguments == {"title": "молоко"}
