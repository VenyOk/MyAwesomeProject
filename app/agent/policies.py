"""Policy engine: maps a tool to an execution decision (plan §12.2).

Levels:
  read        — search/list tools; execute automatically
  low_write   — explicit save / create task; execute and show result
  confirm     — reminders, important changes; would require confirmation card
  destructive — delete, bulk changes; always explicit confirmation
  forbidden   — shell, url fetch, file changes; not registered at all

Confirm and destructive operations create a durable confirmation and never run
until the user explicitly approves the exact action in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    risk: str
    auto_execute: bool
    needs_confirmation: bool


def _read() -> PolicyDecision:
    return PolicyDecision(risk="read", auto_execute=True, needs_confirmation=False)


def _low_write() -> PolicyDecision:
    return PolicyDecision(risk="low_write", auto_execute=True, needs_confirmation=False)


def _confirm() -> PolicyDecision:
    return PolicyDecision(risk="confirm", auto_execute=False, needs_confirmation=True)


def _destructive() -> PolicyDecision:
    return PolicyDecision(risk="destructive", auto_execute=False, needs_confirmation=True)


# Default risk per tool name. Tools not listed here default to confirm.
_DEFAULT_RISK: dict[str, str] = {
    "memory.search": "read",
    "memory.create": "low_write",
    "memory.update": "confirm",
    "memory.delete": "destructive",
    "task.create": "low_write",
    "task.list": "read",
    "task.complete": "confirm",
    "task.cancel": "confirm",
}


def decide(tool_name: str) -> PolicyDecision:
    risk = _DEFAULT_RISK.get(tool_name, "confirm")
    return {
        "read": _read,
        "low_write": _low_write,
        "confirm": _confirm,
        "destructive": _destructive,
    }[risk]()
