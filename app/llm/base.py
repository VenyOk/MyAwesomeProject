from __future__ import annotations

from typing import Iterator, Protocol


class LLMProvider(Protocol):
    """Minimal interface used by the application layer.

    Providers may run the model in-process or connect to a separate inference
    server. The rest of the application must not depend on that choice.
    """

    @property
    def thinking(self) -> bool: ...

    @thinking.setter
    def thinking(self, value: bool) -> None: ...

    def is_loaded(self) -> bool: ...

    def generate(
        self,
        messages: list[dict],
        max_new_tokens: int | None = None,
        thinking: bool | None = None,
    ) -> Iterator[str]: ...
