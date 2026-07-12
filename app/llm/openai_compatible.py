from __future__ import annotations

import json
from typing import Iterator

import httpx

from app.config import Settings
from app.llm.base import LLMChunk, ToolCallPart


class OpenAICompatibleLLM:
    """Streaming client for a separate local OpenAI-compatible LLM server."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        *,
        supports_native_tool_calls: bool = True,
    ):
        self.settings = settings
        self._thinking = settings.thinking_enabled
        self._transport = transport
        self._supports_native_tool_calls = supports_native_tool_calls

    @property
    def thinking(self) -> bool:
        return self._thinking

    @thinking.setter
    def thinking(self, value: bool) -> None:
        self._thinking = bool(value)

    @property
    def supports_native_tool_calls(self) -> bool:
        return self._supports_native_tool_calls

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        return headers

    def is_loaded(self) -> bool:
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=0.75,
                headers=self._headers,
                trust_env=False,
            ) as client:
                response = client.get(
                    f"{self.settings.llm_base_url.rstrip('/')}/models"
                )
                return response.is_success
        except httpx.HTTPError:
            return False

    def generate(
        self,
        messages: list[dict],
        max_new_tokens: int | None = None,
        thinking: bool | None = None,
        tools: list[dict] | None = None,
    ) -> Iterator[str]:
        think = self._thinking if thinking is None else thinking
        payload = {
            "model": self.settings.model_id,
            "messages": messages,
            "stream": True,
            "max_tokens": max_new_tokens or self.settings.max_new_tokens,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "top_k": self.settings.top_k,
            "chat_template_kwargs": {"enable_thinking": bool(think)},
        }
        if tools:
            payload["tools"] = tools
        timeout = httpx.Timeout(
            connect=5.0,
            read=self.settings.llm_request_timeout,
            write=30.0,
            pool=5.0,
        )

        with httpx.Client(
            transport=self._transport,
            timeout=timeout,
            headers=self._headers,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_new_tokens: int | None = None,
    ) -> Iterator[LLMChunk]:
        """Stream a turn and surface native tool calls as structured chunks.

        Ollama (and any OpenAI-compatible server) delivers tool calls via
        ``delta.tool_calls`` while ``delta.content`` is empty. Arguments arrive
        as partial JSON text split across many chunks and must be concatenated
        per ``index`` before parsing. This method accumulates them and emits a
        single ``tool_call`` chunk once each call is complete.
        """
        payload = {
            "model": self.settings.model_id,
            "messages": messages,
            "stream": True,
            "max_tokens": max_new_tokens or self.settings.max_new_tokens,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "top_k": self.settings.top_k,
            "chat_template_kwargs": {"enable_thinking": bool(self._thinking)},
            "tools": tools,
        }
        timeout = httpx.Timeout(
            connect=5.0,
            read=self.settings.llm_request_timeout,
            write=30.0,
            pool=5.0,
        )

        # Per-index accumulator: {"id":..., "name":..., "arguments":"<concat>"}
        pending: dict[int, dict] = {}

        def _flush_index(idx: int) -> ToolCallPart | None:
            entry = pending.pop(idx, None)
            if not entry:
                return None
            raw_args = entry.get("arguments", "")
            try:
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                arguments = {"_raw": raw_args}
            return ToolCallPart(
                name=entry.get("name", ""),
                arguments=arguments,
                id=entry.get("id", ""),
            )

        with httpx.Client(
            transport=self._transport,
            timeout=timeout,
            headers=self._headers,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    # Text deltas -> text chunks.
                    content = delta.get("content")
                    if content:
                        yield LLMChunk(kind="text", content=content)
                    # Tool-call deltas -> accumulate per index.
                    tc_list = delta.get("tool_calls") or []
                    seen_indexes: list[int] = []
                    for tc in tc_list:
                        idx = tc.get("index", 0)
                        seen_indexes.append(idx)
                        slot = pending.setdefault(idx, {"arguments": ""})
                        fn = tc.get("function") or {}
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        frag = fn.get("arguments")
                        if frag:
                            slot["arguments"] += frag
                    # When the turn finishes with tool_calls, emit any pending.
                    finish = choice.get("finish_reason")
                    if finish == "tool_calls":
                        parts: list[ToolCallPart] = []
                        for idx in sorted(pending.keys()):
                            part = _flush_index(idx)
                            if part is not None:
                                parts.append(part)
                        if parts:
                            yield LLMChunk(kind="tool_call", tool_calls=parts)
