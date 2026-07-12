from __future__ import annotations

from threading import Thread
from typing import Iterator

from app.config import Settings
from app.llm.response import clean_response


class GemmaLLM:
    """Gemma 4 12B loaded locally via transformers with 4-bit quantization."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._processor = None
        self._model = None
        self._thinking = settings.thinking_enabled

    @property
    def thinking(self) -> bool:
        return self._thinking

    @thinking.setter
    def thinking(self, value: bool) -> None:
        self._thinking = bool(value)

    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.is_loaded():
            return
        import torch
        from transformers import (
            AutoProcessor,
            AutoModelForMultimodalLM,
            BitsAndBytesConfig,
        )

        dtype = getattr(torch, self.settings.bnb_4bit_compute_dtype, torch.bfloat16)
        quantization_config = None
        if self.settings.load_in_4bit:
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=dtype,
                bnb_4bit_quant_type=self.settings.bnb_4bit_quant_type,
                bnb_4bit_use_double_quant=self.settings.use_double_quant,
            )

        self._processor = AutoProcessor.from_pretrained(self.settings.model_id)
        self._model = AutoModelForMultimodalLM.from_pretrained(
            self.settings.model_id,
            quantization_config=quantization_config,
            device_map="auto",
            torch_dtype=dtype,
        )

    def generate(
        self,
        messages: list[dict],
        max_new_tokens: int | None = None,
        thinking: bool | None = None,
        tools: list[dict] | None = None,
    ) -> Iterator[str]:
        self.load()
        from transformers import TextIteratorStreamer

        think = self._thinking if thinking is None else thinking
        inputs = self._processor.apply_chat_template(
            messages,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            add_generation_prompt=True,
            enable_thinking=think,
        ).to(self._model.device)

        streamer = TextIteratorStreamer(
            self._processor,
            skip_prompt=True,
            skip_special_tokens=False,
            timeout=60.0,
        )
        gen_kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=max_new_tokens or self.settings.max_new_tokens,
            temperature=self.settings.temperature,
            top_p=self.settings.top_p,
            top_k=self.settings.top_k,
            do_sample=True,
        )
        thread = Thread(target=self._model.generate, kwargs=gen_kwargs)
        thread.start()
        try:
            for chunk in streamer:
                if chunk:
                    yield chunk
        finally:
            thread.join()
