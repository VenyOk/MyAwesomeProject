from __future__ import annotations

from datetime import datetime, timezone, timedelta

# Moscow timezone for date resolution ("вчера", "завтра").
_MSK = timezone(timedelta(hours=3))


def _today_msk() -> str:
    return datetime.now(_MSK).strftime("%Y-%m-%d (%A)")


DEFAULT_PERSONA = (
    "You are the user's second brain. You remember what they explicitly ask you to "
    "remember and help them think, recall, and connect ideas. When the user asks about "
    "something they have mentioned before, use the provided relevant memories. Be "
    "concise, direct, and useful. Answer in the user's language.\n\n"
    "IMPORTANT RULES:\n"
    "- Never invent facts, dates, names or numbers that the user did not state. "
    "If you are unsure, say so or ask.\n"
    "- Resolve relative dates like «вчера»/«завтра»/«на следующей неделе» using the "
    "current date provided below, and state the resolved date only if it matters.\n"
    "- Do not claim you have remembered or saved something unless a memory tool "
    "actually ran. For casual messages, just reply naturally.\n"
    "- Quote names exactly as the user wrote them."
)


class ChatSession:
    """In-memory conversation history for the current session."""

    def __init__(self, system_prompt: str = DEFAULT_PERSONA):
        self._system = system_prompt
        self._messages: list[dict] = []

    def add(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})

    def history(self) -> list[dict]:
        return list(self._messages)

    def with_system(self, system_override: str | None = None) -> list[dict]:
        messages: list[dict] = []
        system = system_override if system_override is not None else self._system
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(self._messages)
        return messages

    def clear(self) -> None:
        self._messages = []

    def last_user_message(self) -> str | None:
        for msg in reversed(self._messages):
            if msg["role"] == "user":
                return msg["content"]
        return None

    @property
    def system_prompt(self) -> str:
        return self._system

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system = value
