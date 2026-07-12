"""Parse Qwen/Hermes-style ``<tool_call>`` blocks from model output.

The model emits (after optional reasoning text):

    <tool_call>
    <function=memory_search>
    <parameter=query>питон</parameter>
    </function>
    </tool_call>

This module extracts ``(name, arguments)`` pairs from one or more complete
blocks. It does not handle partial blocks — the orchestrator accumulates the
full assistant turn before parsing, so partial handling is unnecessary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict


_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_FUNCTION_RE = re.compile(r"<function=([^>]+)>(.*?)</function>", re.DOTALL)
_PARAMETER_RE = re.compile(r"<parameter=([^>]+)>(.*?)</parameter>", re.DOTALL)


def strip_tool_calls(text: str) -> str:
    """Return ``text`` with all ``<tool_call>`` blocks removed (the prose part)."""
    return _TOOL_CALL_RE.sub("", text).strip()


def parse_tool_calls(text: str) -> list[ParsedToolCall]:
    """Extract all tool calls from ``text``. Returns an empty list if none."""
    calls: list[ParsedToolCall] = []
    for tc_match in _TOOL_CALL_RE.finditer(text):
        body = tc_match.group(1)
        fn_match = _FUNCTION_RE.search(body)
        if not fn_match:
            continue
        name = fn_match.group(1).strip()
        fn_body = fn_match.group(2)
        arguments: dict = {}
        for p_match in _PARAMETER_RE.finditer(fn_body):
            key = p_match.group(1).strip()
            val = p_match.group(2).strip()
            arguments[key] = val
        calls.append(ParsedToolCall(name=name, arguments=arguments))
    return calls


def has_tool_call(text: str) -> bool:
    """Cheap check whether any ``<tool_call>`` block starts in ``text``."""
    return "<tool_call>" in text
