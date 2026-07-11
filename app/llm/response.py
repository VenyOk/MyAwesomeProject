from __future__ import annotations

import re


_QWEN_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_GEMMA_THOUGHT_BLOCK = re.compile(
    r"<\|channel\|>thought.*?<\|channel\|>", re.DOTALL
)
# Special tokens that engines sometimes leak into the stream even when
# skip_special_tokens=True. Ordered longest-first so prefixes don't shadow
# longer tokens during streaming.
_SPECIAL_TOKENS = tuple(
    sorted(
        [
            "<|think|>",
            "<|channel|>thought",
            "<|channel|>",
            "<|end|>",
            "<|start|>",
            "<end_of_turn>",
            "<start_of_turn>",
            "<|im_end|>",
            "<|endoftext|>",
        ],
        key=len,
        reverse=True,
    )
)
# A token is a possible special-token prefix if a stream chunk equals the
# start of one. Such tail fragments must be buffered until they are completed
# by the next chunk or proven to be ordinary text.
_TOKEN_STARTS = tuple({tok[: i + 1] for tok in _SPECIAL_TOKENS for i in range(len(tok))})


class StreamCleaner:
    """Incremental filter that strips special/reasoning tokens from a token stream.

    Special tokens can be split across chunks (e.g. ``"<|im_"`` then ``"end|>"``),
    so a naive per-chunk ``str.replace`` leaves fragments behind. This class
    buffers a possible incomplete token at the tail of each chunk and emits the
    safe text only. Call :meth:`flush` at end of stream to release any leftover
    buffer that turned out to be ordinary text.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        """Process ``chunk`` and return the text safe to emit right now."""
        text = self._buffer + chunk

        # Strip full special tokens and reasoning blocks that are complete here.
        text = _QWEN_THINK_BLOCK.sub("", text)
        text = _GEMMA_THOUGHT_BLOCK.sub("", text)
        for token in _SPECIAL_TOKENS:
            text = text.replace(token, "")

        # Hold back any tail that could be the start of a special token; it will
        # be resolved by the next chunk (or flush).
        self._buffer = ""
        max_token_len = len(_SPECIAL_TOKENS[0])
        cut = len(text)
        for i in range(1, min(max_token_len, len(text)) + 1):
            if text[-i:] in _TOKEN_STARTS:
                cut = len(text) - i
        safe, tail = text[:cut], text[cut:]
        self._buffer = tail
        return safe

    def flush(self) -> str:
        """Release any buffered text at the end of the stream."""
        leftover = self._buffer
        self._buffer = ""
        return leftover


def clean_response(text: str) -> str:
    """Remove provider-specific reasoning wrappers from persisted answers."""

    text = _QWEN_THINK_BLOCK.sub("", text)
    text = _GEMMA_THOUGHT_BLOCK.sub("", text)
    for token in _SPECIAL_TOKENS:
        text = text.replace(token, "")
    return text.strip()
