"""LLM-based extraction of memory candidates from a user message.

Per the implementation plan §10.2, not every message becomes a memory. The
extractor asks the model to return strictly typed candidates and the chat
pipeline persists only those — explicit ones as ``active``, inferred ones as
``candidate`` pending confirmation.

The extractor never performs external actions and always returns a list
(possibly empty) so a failed/empty generation never breaks the chat.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from pydantic import BaseModel, Field, ValidationError

from app.llm.response import clean_response


VALID_KINDS = ("fact", "preference", "decision", "idea", "person", "project")


class MemoryCandidate(BaseModel):
    kind: str = Field(default="fact", description="one of: " + ", ".join(VALID_KINDS))
    content: str
    # Range clamping is applied in _coerce; kept loose here so out-of-range
    # model output is normalized instead of rejecting the whole extraction.
    importance: float = Field(default=0.5)
    confidence: float = Field(default=0.5)
    sensitivity: str = Field(default="normal")
    explicit: bool = Field(default=False)


class ExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


EXTRACTOR_SYSTEM_PROMPT = (
    "Ты — модуль извлечения памяти персонального ассистента. Проанализируй "
    "сообщение пользователя и выдели только то, что стоит запомнить надолго: "
    "факты о пользователе, предпочтения, решения, идеи, информацию о людях и проектах.\n\n"
    "ПРАВИЛА:\n"
    "- Игнорируй приветствия («привет», «как дела»), уточнения формата, "
    "одноразовые вопросы и команды к ассистенту.\n"
    "- Если пользователь прямо просит запомнить («запомни», «учти», «всегда», "
    "«имей в виду») — ставь explicit=true и confidence выше 0.85.\n"
    "- Если факт предположительный (ты догадался сам) — explicit=false и lower confidence.\n"
    "- Чувствительные данные (здоровье, финансы, пароли, личное) — sensitivity=«private».\n"
    "- Формулируй content как утверждение о пользователе, от третьего лица, "
    "коротко и точно.\n"
    "- Если запоминать нечего — верни пустой список candidates.\n\n"
    "Верни СТРОГО JSON в формате:\n"
    '{"candidates": [{"kind": "preference", "content": "...", "importance": 0.9, '
    '"confidence": 0.95, "sensitivity": "normal", "explicit": true}]}\n'
    "Без markdown, без пояснений, только JSON."
)


# Robust JSON extraction: models sometimes wrap JSON in ```json fences or add
# stray text. Find the outermost object and parse that.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> ExtractionResult:
    """Parse the model output into an ExtractionResult, tolerating noise.

    Returns an empty result if nothing valid can be parsed.
    """
    cleaned = clean_response(text).strip()
    if not cleaned:
        return ExtractionResult(candidates=[])

    # Try direct parse first (fast path for well-behaved output).
    try:
        return ExtractionResult.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError):
        pass

    # Fallback: extract the outermost {...} block.
    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        try:
            return ExtractionResult.model_validate(json.loads(match.group(0)))
        except (json.JSONDecodeError, ValidationError):
            pass
    return ExtractionResult(candidates=[])


def _coerce(candidate: MemoryCandidate) -> MemoryCandidate:
    """Clamp free-form model output into the allowed enum values."""
    if candidate.kind not in VALID_KINDS:
        candidate.kind = "fact"
    if candidate.sensitivity not in ("normal", "private", "secret"):
        candidate.sensitivity = "normal"
    candidate.importance = max(0.0, min(1.0, candidate.importance))
    candidate.confidence = max(0.0, min(1.0, candidate.confidence))
    return candidate


def extract_candidates(
    user_message: str,
    llm,
    *,
    max_new_tokens: int = 512,
) -> list[MemoryCandidate]:
    """Return memory candidates extracted from ``user_message``.

    ``llm`` is any LLMProvider-like object exposing ``generate(messages)``.
    Never raises: on any failure returns an empty list.
    """
    if not user_message.strip():
        return []
    messages = [
        {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    try:
        chunks: list[str] = []
        for chunk in llm.generate(messages, max_new_tokens=max_new_tokens):
            chunks.append(chunk)
        raw = "".join(chunks)
    except Exception:  # noqa: BLE001 - extraction must never break the chat
        return []

    result = _parse_json(raw)
    coerced = [_coerce(c) for c in result.candidates if c.content.strip()]
    return coerced


def candidate_to_memory_kwargs(c: MemoryCandidate) -> dict:
    """Map an extracted candidate to MemoryStore.add(...) keyword arguments.

    Explicit candidates become ``active`` immediately; inferred ones become
    ``candidate`` and wait for user confirmation (plan §10.2)."""
    return dict(
        content=c.content.strip(),
        kind=c.kind,
        importance=c.importance,
        confidence=c.confidence,
        sensitivity=c.sensitivity,
        status="active" if c.explicit else "candidate",
    )


# Iterable alias used by type-checkers; kept lightweight to avoid hard deps.
def iter_candidates(result: ExtractionResult) -> Iterable[MemoryCandidate]:
    return iter(result.candidates)
