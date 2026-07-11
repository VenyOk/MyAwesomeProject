from __future__ import annotations

from app.memory.extractor import (
    MemoryCandidate,
    _coerce,
    _parse_json,
    candidate_to_memory_kwargs,
    extract_candidates,
)


class _ScriptedLLM:
    """Fake LLM that yields a fixed payload, optionally raising."""

    def __init__(self, payload: str):
        self._payload = payload

    def generate(self, messages, max_new_tokens=None):
        yield from [self._payload]


class _FailingLLM:
    def generate(self, messages, max_new_tokens=None):
        raise RuntimeError("inference down")


def test_parse_clean_json():
    raw = '{"candidates": [{"kind": "preference", "content": "Не ест арахис", "importance": 0.9, "confidence": 0.95, "explicit": true}]}'
    res = _parse_json(raw)
    assert len(res.candidates) == 1
    assert res.candidates[0].kind == "preference"
    assert res.candidates[0].explicit is True


def test_parse_json_wrapped_in_markdown_fence():
    raw = "```json\n{\"candidates\": [{\"kind\": \"fact\", \"content\": \"x\", \"explicit\": false}]}\n```"
    res = _parse_json(raw)
    assert len(res.candidates) == 1


def test_parse_json_with_stray_text():
    raw = 'Вот результат:\n{"candidates": [{"kind": "idea", "content": "y"}]}\nНадеюсь помог.'
    res = _parse_json(raw)
    assert len(res.candidates) == 1


def test_parse_empty_candidates():
    res = _parse_json('{"candidates": []}')
    assert res.candidates == []


def test_parse_invalid_returns_empty():
    assert _parse_json("не json вообще").candidates == []
    assert _parse_json("").candidates == []


def test_coerce_clamps_invalid_enum_and_range():
    c = MemoryCandidate(kind="nonsense", content="x", importance=5.0, confidence=-1.0, sensitivity="weird")
    c = _coerce(c)
    assert c.kind == "fact"
    assert c.sensitivity == "normal"
    assert c.importance == 1.0
    assert c.confidence == 0.0


def test_extract_candidates_with_valid_payload():
    payload = '{"candidates": [{"kind": "preference", "content": "Не пьёт кофе после 18:00", "importance": 0.9, "confidence": 0.9, "explicit": true}]}'
    out = extract_candidates("Запомни, что я не пью кофе после 18:00", _ScriptedLLM(payload))
    assert len(out) == 1
    assert out[0].explicit is True


def test_extract_candidates_empty_message_returns_empty():
    assert extract_candidates("   ", _ScriptedLLM('{"candidates":[]}')) == []


def test_extract_candidates_llm_failure_returns_empty():
    assert extract_candidates("hi", _FailingLLM()) == []


def test_candidate_to_memory_kwargs_status_mapping():
    explicit = MemoryCandidate(content="x", explicit=True)
    assert candidate_to_memory_kwargs(explicit)["status"] == "active"

    inferred = MemoryCandidate(content="y", explicit=False)
    assert candidate_to_memory_kwargs(inferred)["status"] == "candidate"
