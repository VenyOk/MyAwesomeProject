"""Chat orchestrator: the model <-> tools loop (plan §5, §12).

Given the conversation and a tool registry, the orchestrator drives the cycle:
  1. Ask the model to generate (with tools advertised).
  2. If the output contains tool calls (native OpenAI format OR Hermes XML for
     transformers-based providers), run each tool through the registry (subject
     to policy), and feed the results back as ``role=tool`` messages.
  3. Repeat until the model answers without a tool call, or the iteration cap
     is hit.

The orchestrator yields events so the API layer can stream tokens and tool
activity to the UI in real time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from app.agent.execution import execute_tool
from app.agent.parser import ParsedToolCall, parse_tool_calls, strip_tool_calls


@dataclass
class ToolEvent:
    """A structured event emitted during the orchestration loop."""

    kind: str  # "text" | "tool_started" | "tool_finished" | "tool_error" | "confirmation_required"
    payload: dict = field(default_factory=dict)


def _run_native_turn(
    llm: Any, working: list[dict], tools_schema: list[dict], max_new_tokens: int
) -> tuple[str, list[ParsedToolCall]]:
    """Run one model turn via generate_with_tools() (native OpenAI tool_calls).

    Returns (prose, tool_calls).
    """
    prose_parts: list[str] = []
    calls: list[ParsedToolCall] = []
    for chunk in llm.generate_with_tools(working, tools_schema, max_new_tokens=max_new_tokens):
        if chunk.kind == "text" and chunk.content:
            prose_parts.append(chunk.content)
        elif chunk.kind == "tool_call":
            for tc in chunk.tool_calls:
                calls.append(ParsedToolCall(name=tc.name, arguments=tc.arguments))
    return "".join(prose_parts), calls


def _run_hermes_turn(
    llm: Any, working: list[dict], tools_schema: list[dict], max_new_tokens: int
) -> tuple[str, list[ParsedToolCall]]:
    """Run one model turn via generate() + Hermes XML parsing (transformers path).

    Returns (prose, tool_calls).
    """
    chunks: list[str] = []
    for chunk in llm.generate(working, max_new_tokens=max_new_tokens, tools=tools_schema):
        chunks.append(chunk)
    raw = "".join(chunks)
    return strip_tool_calls(raw), parse_tool_calls(raw)


def run_turn(
    messages: list[dict],
    llm: Any,
    registry: Any,
    services: Any,
    *,
    chat_id: int | None = None,
    max_new_tokens: int = 1024,
    max_iterations: int = 3,
) -> Iterator[ToolEvent]:
    """Run one user turn, possibly invoking tools, yielding events."""
    tools_schema = registry.openai_schema()
    # qwen_server exposes the same HTTP endpoint as Ollama but streams
    # text/XML, so method presence alone is not a reliable capability check.
    use_native = bool(getattr(llm, "supports_native_tool_calls", False)) and callable(
        getattr(llm, "generate_with_tools", None)
    )
    turn_fn = _run_native_turn if use_native else _run_hermes_turn
    working = list(messages)

    for _iteration in range(max_iterations):
        prose, calls = turn_fn(llm, working, tools_schema, max_new_tokens)

        if prose:
            yield ToolEvent(kind="text", payload={"content": prose})

        if not calls:
            working.append({"role": "assistant", "content": prose})
            return

        # There are tool calls: record the assistant turn, then execute each.
        if use_native:
            # Native path: reconstruct an OpenAI-style assistant message with
            # tool_calls so the chat template renders the right control tokens.
            working.append(
                {
                    "role": "assistant",
                    "content": prose or "",
                    "tool_calls": [
                        {"id": f"call_{i}", "type": "function",
                         "function": {"name": c.name,
                                      "arguments": _stringify(c.arguments)}}
                        for i, c in enumerate(calls)
                    ],
                }
            )
        else:
            working.append({"role": "assistant", "content": prose + _serialize_hermes(calls) if prose else _serialize_hermes(calls)})

        for call in calls:
            result: dict | None = None
            stop = False
            for event in execute_tool(
                registry,
                services,
                call.name,
                call.arguments,
                chat_id=chat_id,
            ):
                yield ToolEvent(kind=event.kind, payload=event.payload)
                if event.kind == "confirmation_required":
                    yield ToolEvent(
                        kind="text",
                        payload={"content": "Нужно ваше подтверждение для этого действия."},
                    )
                if event.kind in ("tool_finished", "tool_error"):
                    result = event.payload["result"]
                stop = stop or event.stop

            if stop:
                return
            if result is not None:
                working.append(
                    {
                        "role": "tool",
                        "name": call.name,
                        "content": _stringify(result),
                    }
                )
        # Loop again so the model can produce the final answer from the results.

    yield ToolEvent(kind="text", payload={"content": ""})


def _stringify(result: Any) -> str:
    import json

    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def _serialize_hermes(calls: list[ParsedToolCall]) -> str:
    """Render calls as Hermes-style <tool_call> blocks for transformers history."""
    blocks = []
    for c in calls:
        params = "".join(
            f"<parameter={k}>\n{_stringify(v)}\n</parameter>\n" for k, v in c.arguments.items()
        )
        blocks.append(f"<tool_call>\n<function={c.name}>\n{params}</function>\n</tool_call>")
    return "\n".join(blocks)
