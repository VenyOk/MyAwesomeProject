from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Load a local .env if present so the app picks up ollama/model settings
# without requiring shell env vars. No-op in production/CI without a .env.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


@dataclass
class Settings:
    model_id: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "Qwen/Qwen3.5-4B")
    )
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "openai_compatible")
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "http://127.0.0.1:8081/v1")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "local")
    )
    llm_request_timeout: float = field(
        default_factory=lambda: float(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
    )
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    load_in_4bit: bool = True
    bnb_4bit_compute_dtype: str = "bfloat16"
    bnb_4bit_quant_type: str = "nf4"
    use_double_quant: bool = True

    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 64
    max_new_tokens: int = 1024

    thinking_enabled: bool = False

    recall_top_k: int = 5
    recent_default: int = 10
    auto_save: bool = True

    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def data_dir(self) -> Path:
        return DATA_DIR

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "brain.db"

    @property
    def faiss_path(self) -> Path:
        return DATA_DIR / "faiss.index"

    @property
    def faiss_ids_path(self) -> Path:
        return DATA_DIR / "faiss.ids.npy"

    def ensure_dirs(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
