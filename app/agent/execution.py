"""Shared policy-aware execution lifecycle for registered tools.

Both the LLM orchestrator and explicit slash commands must pass through this
module.  It records the same audit trail, applies the same policy decision,
and creates the same durable confirmation for actions that cannot run
automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from app.agent.policies import decide


@dataclass
class ToolExecutionEvent:
    """One lifecycle event emitted while a registered tool is handled."""

    kind: str  # tool_started | tool_finished | tool_error | confirmation_required
    payload: dict = field(default_factory=dict)
    stop: bool = False


def execute_tool(
    registry: Any,
    services: Any,
    tool_name: str,
    arguments: dict,
    *,
    chat_id: int | None = None,
) -> Iterator[ToolExecutionEvent]:
    """Run one allowlisted tool through policy, audit, and confirmation flow."""
    decision = decide(tool_name)
    agent_store = getattr(services, "agent_store", None)
    tool_run_id = None
    if agent_store is not None:
        tool_run_id = agent_store.start_tool_run(
            tool_name,
            arguments,
            chat_id=chat_id,
            policy_decision=decision.risk,
        )

    yield ToolExecutionEvent(
        kind="tool_started",
        payload={
            "name": tool_name,
            "arguments": arguments,
            "risk": decision.risk,
            "needs_confirmation": decision.needs_confirmation,
            "tool_run_id": tool_run_id,
        },
    )

    if not decision.auto_execute:
        if agent_store is None or tool_run_id is None:
            result = {"error": "confirmation storage is unavailable"}
            yield ToolExecutionEvent(
                kind="tool_error",
                payload={
                    "name": tool_name,
                    "result": result,
                    "tool_run_id": tool_run_id,
                },
                stop=True,
            )
            return

        confirmation = agent_store.create_confirmation(
            tool_run_id=tool_run_id,
            tool_name=tool_name,
            arguments=arguments,
            risk=decision.risk,
            chat_id=chat_id,
        )
        agent_store.finish_tool_run(
            tool_run_id,
            "pending_confirmation",
            {"confirmation_id": confirmation.id},
        )
        yield ToolExecutionEvent(
            kind="confirmation_required",
            payload={
                "confirmation": confirmation.to_dict(),
                "tool_run_id": tool_run_id,
            },
            stop=True,
        )
        return

    result = registry.dispatch(tool_name, arguments, services)
    if agent_store is not None and tool_run_id is not None:
        agent_store.finish_tool_run(
            tool_run_id,
            "failed" if "error" in result else "succeeded",
            result,
        )
    yield ToolExecutionEvent(
        kind="tool_finished" if "error" not in result else "tool_error",
        payload={
            "name": tool_name,
            "result": result,
            "tool_run_id": tool_run_id,
        },
    )


def execute_confirmed_tool(registry: Any, services: Any, confirmation: Any) -> dict:
    """Execute a previously approved confirmation exactly once."""
    result = registry.dispatch(confirmation.tool_name, confirmation.arguments, services)
    agent_store = getattr(services, "agent_store", None)
    if agent_store is not None:
        agent_store.finish_tool_run(
            confirmation.tool_run_id,
            "failed" if "error" in result else "succeeded",
            result,
        )
    return result
