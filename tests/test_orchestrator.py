from __future__ import annotations

from app.agent.orchestrator import run_turn
from app.agent.tool_registry import ToolDefinition, ToolRegistry
from app.llm.base import LLMChunk, ToolCallPart


class _NativeToolLLM:
    """Fake LLM with generate_with_tools: emits a tool_call, then a final answer."""

    def __init__(self):
        self.thinking = False
        self.supports_native_tool_calls = True
        self._calls = 0

    def is_loaded(self) -> bool:
        return True

    def generate(self, messages, max_new_tokens=None, tools=None):
        yield "fallback"

    def generate_with_tools(self, messages, tools, *, max_new_tokens=None):
        if self._calls == 0:
            self._calls += 1
            yield LLMChunk(
                kind="tool_call",
                tool_calls=[ToolCallPart(name="memory.search", arguments={"query": "питон"}, id="c1")],
            )
        else:
            yield LLMChunk(kind="text", content="Найдено: пользователь любит Python.")


class _Services:
    def __init__(self, store=None, recall=None):
        self.store = store
        self.recall = recall
        self.agent_store = None


def _registry_with_search():
    from app.agent.schemas import MemorySearchArgs
    from pydantic import BaseModel

    class _Args(MemorySearchArgs):
        pass

    def handler(services, args):
        return {"results": [{"content": "любит Python"}], "count": 1}

    reg = ToolRegistry()
    reg.register(ToolDefinition("memory.search", "search", _Args, handler))
    return reg


def test_orchestrator_native_tool_call_executes_and_finalizes():
    """Native path: tool_call chunk -> dispatch -> tool result -> final text."""
    llm = _NativeToolLLM()
    registry = _registry_with_search()
    services = _Services()
    events = list(run_turn([{"role": "user", "content": "find python"}], llm, registry, services))
    kinds = [e.kind for e in events]
    assert "tool_started" in kinds
    assert "tool_finished" in kinds
    text = "".join(e.payload.get("content", "") for e in events if e.kind == "text")
    assert "Python" in text


def test_orchestrator_no_tool_call_just_text():
    class _TextOnlyLLM:
        thinking = False
        supports_native_tool_calls = True

        def is_loaded(self):
            return True

        def generate(self, messages, max_new_tokens=None, tools=None):
            yield "Просто ответ."

        def generate_with_tools(self, messages, tools, *, max_new_tokens=None):
            yield LLMChunk(kind="text", content="Просто ответ.")

    llm = _TextOnlyLLM()
    registry = _registry_with_search()
    events = list(run_turn([{"role": "user", "content": "hi"}], llm, registry, _Services()))
    assert all(e.kind == "text" for e in events)
    assert events[0].payload["content"] == "Просто ответ."
