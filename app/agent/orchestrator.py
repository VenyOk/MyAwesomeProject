"""Chat orchestrator: the model <-> tools loop (plan §5, §12).

Given the conversation and a tool registry, the orchestrator drives the cycle:
  1. Ask the model to generate (with tools advertised).
  2. If the output contains ``<tool_call>`` blocks, parse them, run each tool
     through the registry (subject to policy), and feed the results back as
     ``role=tool`` messages.
  3. Repeat until the model answers without a tool call, or the iteration cap
     is hit.

The orchestrator yields events so the API layer can stream tokens and tool
activity to the UI in real time. ``text`` events carry assistant tokens; the
tool_call/tool_result events carry structured payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from app.agent.parser import parse_tool_calls, strip_tool_calls
from app.agent.policies import decide
from app.agent.tool_registry import ToolRegistry


@dataclass
class ToolEvent:
    """A structured event emitted during the orchestration loop."""

    kind: str  # "text" | "tool_started" | "tool_finished" | "tool_error" | "confirmation_required"
    payload: dict = field(default_factory=dict)


def run_turn(
    messages: list[dict],
    llm: Any,
    registry: ToolRegistry,
    services: Any,
    *,
    chat_id: int | None = None,
    max_new_tokens: int = 1024,
    max_iterations: int = 3,
) -> Iterator[ToolEvent]:
    """Run one user turn, possibly invoking tools, yielding events.

    ``messages`` is mutated in place: assistant and tool messages are appended
    so the caller can persist the full final history.
    """
    tools_schema = registry.openai_schema()
    working = list(messages)

    for _iteration in range(max_iterations):
        # Generate the model turn, collecting the full text (it may contain a
        # tool_call block that must be parsed whole).
        chunks: list[str] = []
        for chunk in llm.generate(working, max_new_tokens=max_new_tokens, tools=tools_schema):
            chunks.append(chunk)
        raw = "".join(chunks)

        calls = parse_tool_calls(raw)
        prose = strip_tool_calls(raw)

        # Emit the prose portion (if any) as text for the UI.
        if prose:
            yield ToolEvent(kind="text", payload={"content": prose})

        if not calls:
            # No tool call -> this is the final answer. Record it and stop.
            working.append({"role": "assistant", "content": prose or raw})
            return

        # There are tool calls: record the assistant turn with the tool_call,
        # then execute each and append tool results.
        working.append({"role": "assistant", "content": raw})

        for call in calls:
            decision = decide(call.name)
            agent_store = getattr(services, "agent_store", None)
            tool_run_id = None
            if agent_store is not None:
                tool_run_id = agent_store.start_tool_run(
                    call.name,
                    call.arguments,
                    chat_id=chat_id,
                    policy_decision=decision.risk,
                )
            yield ToolEvent(
                kind="tool_started",
                payload={
                    "name": call.name,
                    "arguments": call.arguments,
                    "risk": decision.risk,
                    "needs_confirmation": decision.needs_confirmation,
                    "tool_run_id": tool_run_id,
                },
            )
            if not decision.auto_execute:
                if agent_store is None or tool_run_id is None:
                    result = {"error": "confirmation storage is unavailable"}
                    yield ToolEvent(kind="tool_error", payload={"name": call.name, "result": result})
                    return
                confirmation = agent_store.create_confirmation(
                    tool_run_id=tool_run_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    risk=decision.risk,
                    chat_id=chat_id,
                )
                agent_store.finish_tool_run(
                    tool_run_id,
                    "pending_confirmation",
                    {"confirmation_id": confirmation.id},
                )
                yield ToolEvent(
                    kind="confirmation_required",
                    payload={"confirmation": confirmation.to_dict()},
                )
                yield ToolEvent(
                    kind="text",
                    payload={"content": "Нужно ваше подтверждение для этого действия."},
                )
                return

            result = registry.dispatch(call.name, call.arguments, services)
            if agent_store is not None and tool_run_id is not None:
                agent_store.finish_tool_run(
                    tool_run_id,
                    "failed" if "error" in result else "succeeded",
                    result,
                )
            yield ToolEvent(
                kind="tool_finished" if "error" not in result else "tool_error",
                payload={"name": call.name, "result": result},
            )
            working.append(
                {
                    "role": "tool",
                    "name": call.name,
                    "content": _stringify_result(result),
                }
            )
        # Loop again so the model can produce the final answer from the results.

    # Iteration cap reached: stop and let the caller persist what we have.
    yield ToolEvent(kind="text", payload={"content": ""})


def _stringify_result(result: dict) -> str:
    import json

    return json.dumps(result, ensure_ascii=False)
