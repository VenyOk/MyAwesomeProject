from __future__ import annotations

DEFAULT_PERSONA = (
    "You are the user's second brain. You remember everything they share with you "
    "and help them think, recall, and connect ideas. When the user asks about something "
    "they have mentioned before, use the provided relevant memories. Be concise, direct, "
    "and useful. Answer in the user's language."
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
