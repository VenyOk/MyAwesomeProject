from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class ToolCallPart:
    """A single tool call parsed from a native OpenAI-style tool_calls delta."""

    name: str
    arguments: dict
    id: str = ""


@dataclass
class LLMChunk:
    """A structured piece of a model turn.

    kind == "text": ``content`` carries assistant prose.
    kind == "tool_call": ``tool_calls`` carries one or more parsed tool calls.
    """

    kind: str  # "text" | "tool_call"
    content: str = ""
    tool_calls: list[ToolCallPart] = field(default_factory=list)


class LLMProvider(Protocol):
    """Minimal interface used by the application layer.

    Providers may run the model in-process or connect to a separate inference
    server. The rest of the application must not depend on that choice.
    """

    @property
    def thinking(self) -> bool: ...

    @thinking.setter
    def thinking(self, value: bool) -> None: ...

    def is_loaded(self) -> bool: ...

    def generate(
        self,
        messages: list[dict],
        max_new_tokens: int | None = None,
        thinking: bool | None = None,
        tools: list[dict] | None = None,
    ) -> Iterator[str]: ...

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_new_tokens: int | None = None,
    ) -> Iterator[LLMChunk]:
        """Stream a turn that may contain native tool calls.

        Returns structured chunks so callers (the orchestrator) get tool calls
        as parsed dicts instead of having to regex-parse provider-specific text.
        Not all providers implement this; callers should check via hasattr().
        """
        ...
