from __future__ import annotations

import json
from typing import Iterator

import httpx

from app.config import Settings


class OpenAICompatibleLLM:
    """Streaming client for a separate local OpenAI-compatible LLM server."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._thinking = settings.thinking_enabled
        self._transport = transport

    @property
    def thinking(self) -> bool:
        return self._thinking

    @thinking.setter
    def thinking(self, value: bool) -> None:
        self._thinking = bool(value)

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
