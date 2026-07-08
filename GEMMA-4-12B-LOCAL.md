# Gemma 4 12B — Local Deployment Reference (RTX 5070 Ti / Blackwell / Windows)

> Target: **NVIDIA RTX 5070 Ti** (Blackwell, compute capability **sm_120**), **16 GB VRAM**, 32 GB RAM, Windows.
> Use case: local "second brain" chat assistant (text first; images/audio later).
> Model: `google/gemma-4-12B` (base) / `google/gemma-4-12B-it` (instruction-tuned), a.k.a. **"Gemma 4 12B Unified"** — encoder-free multimodal (text + image + audio + video), ~11.95 B params, **Apache-2.0**, **256K** context. Released **2026-06-03**.
>
> Sources are cited inline. Items I could not fully verify are marked **[UNVERIFIED]**.

---

## 0. Model at a glance (verified)

| Property | Value | Source |
|---|---|---|
| HF IDs | `google/gemma-4-12B`, `google/gemma-4-12B-it` | [model card](https://huggingface.co/google/gemma-4-12B-it) |
| Params | ~11.95 B (dense, "Unified") | [VentureBeat](https://venturebeat.com/technology/googles-new-open-source-gemma-4-12b-analyzes-audio-video-and-runs-entirely-locally-on-a-typical-16gb-enterprise-laptop) |
| Architecture | Encoder-free: vision/audio project **directly** into the LLM backbone (no separate ViT/audio encoder) | [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b) |
| Modalities | In: text, image, video, **audio** (audio only on E2B/E4B/12B). Out: text | [model card](https://huggingface.co/google/gemma-4-12B-it) |
| Context | **256K** tokens (E2B/E4B are 128K) | [model card 4](https://ai.google.dev/gemma/docs/core/model_card_4) |
| License | Apache-2.0 | model card |
| Extra | Multi-Token Prediction (MTP) drafters for speculative decoding; hybrid local+global sliding-window attention with unified KV + p-RoPE | [model card 4](https://ai.google.dev/gemma/docs/core/model_card_4) |

---

## 1. Loading options for this exact hardware

### 1a. transformers + bitsandbytes 4-bit (NF4)

**Requirements (verified):**
- `transformers >= 5.5.0` — Gemma 4 (`model_type: gemma4`) was introduced in 5.5.0. Recommend the latest (5.12–5.13+ to also get the multimodal-4bit dtype fix, see §5). ([llm-compressor #2562](https://github.com/vllm-project/llm-compressor/issues/2562))
- `bitsandbytes == 0.49.2` — the `win_amd64` wheel **does** include `sm_120` kernels when paired with a **CUDA 12.8 (cu128)** PyTorch. ([bitsandbytes PyPI](https://pypi.org/project/bitsandbytes), [HF bnb install table](https://huggingface.co/docs/bitsandbytes/en/installation): "Windows x86-64, CUDA 12.8–12.9 … sm_120")
- `torch >= 2.7.1+cu128` — **must** be the cu128 build for Blackwell/sm_120. ([PyTorch forums](https://discuss.pytorch.org/t/nvidia-geforce-rtx-5070-ti-with-cuda-capability-sm-120/221509))
- Load class: **`AutoModelForMultimodalLM`** + `AutoProcessor` (it is a multimodal model, not `AutoModelForCausalLM`). ([HF Gemma4 blog](https://huggingface.co/blog/gemma4))

**Smoke-test that Blackwell is actually wired up before loading the model:**
```powershell
python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.get_arch_list())"
# Expect: 2.7.1+cu128 12.8  [..., 'sm_120', ...]
```

**Install (Windows, cu128):**
```powershell
pip install -U "torch==2.7.1+cu128" --index-url https://download.pytorch.org/whl/cu128
pip install -U "transformers>=5.12.0" bitsandbytes==0.49.2 accelerate torchvision librosa
```

**Load + 4-bit config** (canonical config from HF issue #46899 reproducer):
```python
import torch
from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig

MODEL_ID = "google/gemma-4-12B-it"
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,
)
processor = AutoProcessor.from_pretrained(MODEL_ID)
model = AutoModelForMultimodalLM.from_pretrained(
    MODEL_ID,
    quantization_config=bnb_config,
    device_map="cuda",
    dtype=torch.bfloat16,
)
```
Footprint at 4-bit NF4: **~7–8 GB** weights → fits 16 GB with room for KV cache + a large context. ⚠️ See §5 for the (now-fixed) multimodal+4-bit dtype bug — use a transformers build that includes the fix for [#46899](https://github.com/huggingface/transformers/issues/46899)/#46904.

### 1b. GGUF quantization (llama.cpp / llama-server) — **recommended for inference**

Yes — multiple verified community quants exist:

| Repo | Notes |
|---|---|
| `ggml-org/gemma-4-12B-it-GGUF` | official ggml-org mirror; `llama-server -hf ggml-org/gemma-4-12B-it-GGUF` |
| `bartowski/gemma-4-12B-it-GGUF` | full quant ladder with exact sizes |
| `unsloth/gemma-4-12b-it-GGUF` | "omni" GGUF (text+image+audio in one file); `-hf unsloth/gemma-4-12b-it-GGUF:UD-Q4_K_XL --jinja` |
| `lmstudio-community/gemma-4-12B-it-GGUF` | LM Studio default |

**Official Google QAT** (best-in-class 4-bit, from the `gemma-4-qat-q4-0` collection):
`google/gemma-4-12B-it-qat-q4_0-gguf` (Q4_0) — plus `-w4a16-ct` compressed-tensors for vLLM/SGLang. ([QAT collection](https://huggingface.co/collections/google/gemma-4-qat-q4-0))

**Quant sizes that fit 16 GB VRAM** (bartowski, verified):
| Quant | File size | Fits 16 GB? | Use |
|---|---|---|---|
| Q3_K_M | 6.30 GB | yes | low quality, not recommended |
| Q4_0 / QAT Q4_0 | 7.13 GB | yes | fast, ARM/AVX repack |
| **Q4_K_M** | **7.66 GB** | **yes (best default)** | recommended |
| Q5_K_M | ~8.5 GB | yes | better quality daily driver |
| Q6_K | ~10 GB | yes (smaller ctx) | high quality |
| Q8_0 | ~13 GB | tight | near-lossless, small ctx only |

Source: [bartowski/gemma-4-12B-it-GGUF](https://huggingface.co/bartowski/gemma-4-12B-it-GGUF).

**Run on Blackwell (Windows):** llama.cpp supports Gemma 4 + Blackwell via its cu128 CUDA builds. Grab a recent stock build and:
```powershell
llama-server -hf bartowski/gemma-4-12B-it-GGUF:Q4_K_M --jinja -c 8192 -ngl 999
# then open http://localhost:8080  (OpenAI-compatible API)
```
`-ngl 999` offloads all layers to the GPU. The mmproj (multimodal projector) is pulled automatically with `-hf`. ([unsloth card](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF))

### 1c. Ollama

- Official library: [`ollama.com/library/gemma4`](https://ollama.com/library/gemma4) — but the **`:12b` tag has had availability issues** (Reddit reports it was scrubbed/missing at times). **[UNVERIFIED current status — check `ollama pull gemma4:12b` on your machine.]** ([r/ollama](https://www.reddit.com/r/ollama/comments/1twgzkz/wheres_gemma412b))
- Working community mirror: `batiai/gemma4-12b` — `iq4` ≈ 6.2 GB (recommended for 16 GB), `q4` also fine. ([batiai/gemma4-12b](https://ollama.com/batiai/gemma4-12b))
```powershell
ollama pull batiai/gemma4-12b:iq4
ollama run   batiai/gemma4-12b:iq4
```

### 1d. Hosted/remote inference

- HuggingFace Inference endpoints, Google AI Edge Gallery, Google Cloud (Model Garden / Cloud Run / GKE), vLLM, SGLang all listed as supported. ([Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b)) No always-free public API known — **[UNVERIFIED]**.

---

## 2. Thinking mode (verified from [model card 4](https://ai.google.dev/gemma/docs/core/model_card_4) + [HF card](https://huggingface.co/google/gemma-4-12B-it))

**Control tokens:**
- `<|think|>` — placed at the **start of the system prompt** to enable thinking.
- `<|channel>thought\n` … `<channel|>` — wraps the internal reasoning block.

**Standard output structure when thinking is ON:**
```
<|channel>thought\n
[internal reasoning]
<channel|>
[final answer]
```

**Behavior when disabled:**
- For **12B** (and 26B/31B): thinking disabled → the model **still emits an empty thought block** then the answer:
  `<|channel>thought\n<channel|>[final answer]`
- For E2B/E4B only: no thought block at all when disabled.

**Enabling/disabling via the chat template:**
```python
inputs = processor.apply_chat_template(
    messages, tokenize=True, return_dict=True, return_tensors="pt",
    add_generation_prompt=True,
    enable_thinking=False,   # True to reason, False for direct answers
).to(model.device)
...
response = processor.decode(outputs[input_len:], skip_special_tokens=False)
processor.parse_response(response)   # splits reasoning vs final answer for you
```
`enable_thinking` is the param the built-in template reads; `processor.parse_response()` separates the `<|channel>thought` block from the final answer. Roles are standard `system` / `user` / `assistant` (unlike Gemma 3's start/turn tokens).

---

## 3. Sampling, context, multimodal ordering

**Sampling (official, "standardized across all use cases"):**
- `temperature = 1.0`
- `top_p = 0.95`
- `top_k = 64`
Source: [model card 4 §Best Practices](https://ai.google.dev/gemma/docs/core/model_card_4).

**Context length:** **256K** tokens for 12B. (Caveat: full 256K KV cache is large; for local chat use a smaller `-c`, e.g. 8K–32K.)

**Multimodal input ordering:**
- **Image before text — CONFIRMED** in the model-card code: `# Prompt - add image before text`, with the `{"type":"image"}` content item listed before the `{"type":"text"}` item. ([HF card](https://huggingface.co/google/gemma-4-12B-it))
- **Audio placement — [PARTIALLY UNVERIFIED]:** the card's audio snippet passes audio as a content item alongside text; the dedicated "Modality order" section in the card did not extract cleanly. Treat "audio after the text instruction" as a reasonable default but **confirm against the live card** before relying on it. Images use a fixed budget of **280 soft tokens** by default (configurable). ([transformers Gemma4 docs](https://huggingface.co/docs/transformers/en/model_doc/gemma4))

```python
messages = [{"role": "user", "content": [
    {"type": "image", "url": "<img url>"},          # media first
    {"type": "text", "text": "What is shown here?"}, # text after
]}]
```

---

## 4. Gating / access

**Gated model — yes.** Like all Gemma releases, you must:
1. Have a HuggingFace account.
2. Open `https://huggingface.co/google/gemma-4-12B-it` and **accept the license terms** on the page (Google approves quickly, usually minutes).
3. Supply a token: `huggingface-cli login` (paste a read-scoped token) **or** set `HF_TOKEN` env var.

Source: gated-model login pattern noted across Gemma guides; the `gemma4` family follows the same gate as Gemma 1–3. **[Status as of 2026-07 confirmed by the download/login workflow shown on the card.]**

---

## 5. Known issues / gotchas (Windows + Blackwell)

1. **"CUDA error: no kernel image is available"** = your torch is **not** the cu128 build, or bitsandbytes wheel is too old. Fix: `torch==2.7.1+cu128` (cu128 index) + `bitsandbytes==0.49.2`. The original gap is tracked in [bitsandbytes #1937](https://github.com/bitsandbytes-foundation/bitsandbytes/issues/1937) and is resolved by the current Windows wheels per the [install table](https://huggingface.co/docs/bitsandbytes/en/installation).
2. **transformers too old → `gemma4` model type unknown.** Need `transformers >= 5.5.0`; many tools (vLLM, llm-compressor) still pin ≤4.57 and will reject it. ([llm-compressor #2562](https://github.com/vllm-project/llm-compressor/issues/2562))
3. **4-bit + audio/vision broken (now fixed).** transformers [#46899](https://github.com/huggingface/transformers/issues/46899): "Audio (and vision) processing broken with 4-bit quantization due to unsafe dtype cast in multimodal embedders" — `RuntimeError` dtype mismatch. Fixed via #46904. **Use a transformers version that includes #46904** (≈5.12.1+). Until then, multimodal + 4-bit fails; text-only 4-bit is fine.
4. **Load with `AutoModelForMultimodalLM`, not `AutoModelForCausalLM`** — the latter won't apply the processor/embedders and silently breaks image/audio.
5. **Don't call `.cuda()` on a 4-bit model** — it re-materializes tensors on GPU and causes OOM. Use `device_map=` and let bnb place layers. (Common StackOverflow footgun.)
6. **256K context is memory-heavy.** Default chat `-c` to 8K–32K locally; full 256K needs CPU-offloaded KV or much more VRAM.
7. **PyPI `bitsandbytes-windows` (the old 2023 community wheel) is obsolete** — use the official `bitsandbytes` PyPI package which now ships native Windows wheels.

---

## 6. Integration recommendation

**For this hardware + goal, run it via llama.cpp (`llama-server`) with a GGUF Q4_K_M or Q6_K — or Ollama if you want zero-config.**

| Option | Verdict on RTX 5070 Ti / 16 GB / Windows | When to pick |
|---|---|---|
| **llama.cpp / llama-server + GGUF** ✅ **Best** | Fastest inference on consumer Blackwell, cu128 CUDA kernels, OpenAI-compatible API, omni-GGUF does text+image+audio, smallest footprint. Q4_K_M (7.66 GB) leaves ~8 GB for context. | Chat assistant, API integration, multimodal later |
| **Ollama** ✅ Easy | Same engine under the hood, one-command UX; but official `gemma4:12b` tag is intermittently missing → use `batiai/gemma4-12b:iq4`. | Quickest start, GUI/simple CLI |
| **transformers + bnb 4-bit** ⚠️ | Works (NF4 ≈ 7–8 GB), but most friction: cu128 torch + transformers ≥5.12 (for the multimodal fix) + bnb 0.49.2, and slower per-token than llama.cpp. | **Only if you need fine-tuning / QLoRA** |

**Concrete starting command (recommended):**
```powershell
llama-server -hf bartowski/gemma-4-12B-it-GGUF:Q4_K_M --jinja -c 16384 -ngl 999 \
  --temp 1.0 --top-p 0.95 --top-k 64
```
Point any OpenAI-compatible client at `http://localhost:8080`. Add thinking by sending `<|think|>` at the start of the system message, or use `enable_thinking=true` through your client's template options.

---

## Key source URLs
- Model card (IT): https://huggingface.co/google/gemma-4-12B-it
- Model card (Google docs): https://ai.google.dev/gemma/docs/core/model_card_4
- Launch blog: https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b
- HF Welcome-Gemma-4 blog: https://huggingface.co/blog/gemma4
- transformers Gemma4 docs: https://huggingface.co/docs/transformers/en/model_doc/gemma4
- GGUF (bartowski): https://huggingface.co/bartowski/gemma-4-12B-it-GGUF
- GGUF (unsloth, omni): https://huggingface.co/unsloth/gemma-4-12b-it-GGUF
- Official QAT collection: https://huggingface.co/collections/google/gemma-4-qat-q4-0
- bitsandbytes install (sm_120): https://huggingface.co/docs/bitsandbytes/en/installation
- 4-bit multimodal bug/fix: https://github.com/huggingface/transformers/issues/46899
- Blackwell torch thread: https://discuss.pytorch.org/t/nvidia-geforce-rtx-5070-ti-with-cuda-capability-sm-120/221509

*Compiled 2026-07-08. Items marked **[UNVERIFIED]** could not be fully confirmed from primary sources — re-check before relying on them.*
