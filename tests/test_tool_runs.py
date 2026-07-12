from __future__ import annotations

from app.agent.orchestrator import run_turn
from app.agent.schemas import MemorySearchArgs, TaskIdArgs
from app.agent.store import AgentStore
from app.agent.tool_registry import ToolDefinition, ToolRegistry
from app.llm.base import LLMChunk, ToolCallPart


class _OneToolCallLLM:
    thinking = False
    supports_native_tool_calls = True

    def __init__(self, name: str, arguments: dict):
        self._name = name
        self._arguments = arguments
        self._called = False

    def generate_with_tools(self, messages, tools, *, max_new_tokens=None):
        if not self._called:
            self._called = True
            yield LLMChunk(
                kind="tool_call",
                tool_calls=[
                    ToolCallPart(name=self._name, arguments=self._arguments, id="call_1")
                ],
            )
            return
        yield LLMChunk(kind="text", content="Done.")


class _Services:
    def __init__(self, agent_store: AgentStore):
        self.agent_store = agent_store


def _registry(name: str, schema, handler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name, name, schema, handler))
    return registry


def test_tool_runs_endpoint_lists_newest_first_and_filters_by_chat(client):
    store = client.app.state.services.agent_store
    first_id = store.start_tool_run(
        "memory.search",
        {"query": "first"},
        chat_id=11,
        policy_decision="read",
    )
    store.finish_tool_run(first_id, "succeeded", {"count": 1})
    second_id = store.start_tool_run(
        "task.create",
        {"title": "second"},
        chat_id=22,
        policy_decision="low_write",
    )

    response = client.get("/api/tool-runs")

    assert response.status_code == 200
    runs = response.json()["tool_runs"]
    assert [run["id"] for run in runs] == [second_id, first_id]
    assert runs[1]["chat_id"] == 11
    assert runs[1]["message_id"] is None
    assert runs[1]["tool_name"] == "memory.search"
    assert runs[1]["arguments"] == {"query": "first"}
    assert runs[1]["result"] == {"count": 1}
    assert runs[1]["policy_decision"] == "read"
    assert runs[1]["status"] == "succeeded"
    assert runs[1]["created_at"]
    assert runs[1]["finished_at"]

    filtered = client.get("/api/tool-runs?chat_id=11")
    assert filtered.status_code == 200
    assert [run["id"] for run in filtered.json()["tool_runs"]] == [first_id]


def test_completed_tool_event_uses_the_started_tool_run_id(tmp_path):
    store = AgentStore(tmp_path / "brain.db")
    try:
        events = list(
            run_turn(
                [{"role": "user", "content": "find memory"}],
                _OneToolCallLLM("memory.search", {"query": "python"}),
                _registry(
                    "memory.search",
                    MemorySearchArgs,
                    lambda _services, _args: {"count": 1},
                ),
                _Services(store),
                chat_id=5,
            )
        )
    finally:
        store.close()

    started = next(event for event in events if event.kind == "tool_started")
    finished = next(event for event in events if event.kind == "tool_finished")
    assert started.payload["tool_run_id"] is not None
    assert finished.payload["tool_run_id"] == started.payload["tool_run_id"]


def test_error_and_confirmation_events_keep_the_started_tool_run_id(tmp_path):
    error_store = AgentStore(tmp_path / "error.db")
    try:
        error_events = list(
            run_turn(
                [{"role": "user", "content": "find memory"}],
                _OneToolCallLLM("memory.search", {"query": "python"}),
                _registry(
                    "memory.search",
                    MemorySearchArgs,
                    lambda _services, _args: {"error": "not found"},
                ),
                _Services(error_store),
            )
        )
    finally:
        error_store.close()

    error_started = next(event for event in error_events if event.kind == "tool_started")
    error = next(event for event in error_events if event.kind == "tool_error")
    assert error.payload["tool_run_id"] == error_started.payload["tool_run_id"]

    confirmation_store = AgentStore(tmp_path / "confirmation.db")
    try:
        confirmation_events = list(
            run_turn(
                [{"role": "user", "content": "complete task"}],
                _OneToolCallLLM("task.complete", {"id": 1}),
                _registry(
                    "task.complete",
                    TaskIdArgs,
                    lambda _services, _args: {"status": "done"},
                ),
                _Services(confirmation_store),
            )
        )
    finally:
        confirmation_store.close()

    confirmation_started = next(
        event for event in confirmation_events if event.kind == "tool_started"
    )
    confirmation = next(
        event for event in confirmation_events if event.kind == "confirmation_required"
    )
    assert confirmation.payload["tool_run_id"] == confirmation_started.payload["tool_run_id"]
    assert (
        confirmation.payload["confirmation"]["tool_run_id"]
        == confirmation_started.payload["tool_run_id"]
    )
