from __future__ import annotations

from app.agent.orchestrator import run_turn
from app.agent.tool_registry import ToolDefinition, ToolRegistry
from app.config import Settings
from app.llm.openai_compatible import OpenAICompatibleLLM


class _Services:
    agent_store = None


def _registry() -> ToolRegistry:
    from app.agent.schemas import MemorySearchArgs

    def search(_services, args):
        return {"query": args.query, "count": 1}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("memory.search", "Search memory", MemorySearchArgs, search)
    )
    return registry


def test_openai_compatible_provider_can_disable_native_tool_calls():
    provider = OpenAICompatibleLLM(
        Settings(model_id="Qwen/Qwen3.5-4B"),
        supports_native_tool_calls=False,
    )

    assert provider.supports_native_tool_calls is False


def test_orchestrator_uses_hermes_fallback_when_native_calls_are_unsupported():
    class _QwenServerLLM:
        thinking = False
        supports_native_tool_calls = False

        def __init__(self):
            self._calls = 0

        def is_loaded(self):
            return True

        def generate(self, messages, max_new_tokens=None, tools=None):
            if self._calls == 0:
                self._calls += 1
                yield (
                    '<tool_call>\n<function=memory.search>\n'
                    '<parameter=query>\npython\n</parameter>\n'
                    '</function>\n</tool_call>'
                )
            else:
                yield "Found it."

        def generate_with_tools(self, messages, tools, *, max_new_tokens=None):
            raise AssertionError("qwen_server must use Hermes XML fallback")

    events = list(
        run_turn(
            [{"role": "user", "content": "find python"}],
            _QwenServerLLM(),
            _registry(),
            _Services(),
        )
    )

    assert [event.kind for event in events] == [
        "tool_started",
        "tool_finished",
        "text",
    ]
    assert events[-1].payload["content"] == "Found it."
