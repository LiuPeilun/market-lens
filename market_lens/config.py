from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("MARKET_LENS_ENV", "development")
    host: str = os.getenv("MARKET_LENS_HOST", "127.0.0.1")
    port: int = int(os.getenv("MARKET_LENS_PORT", "8000"))
    http_timeout: float = float(os.getenv("MARKET_LENS_HTTP_TIMEOUT", "15"))
    http_retries: int = int(os.getenv("MARKET_LENS_HTTP_RETRIES", "2"))
    db_path: Path = Path(os.getenv("MARKET_LENS_DB_PATH", ".data/market_lens.sqlite3"))
    llm_base_url: str = os.getenv("MARKET_LENS_LLM_BASE_URL", "http://218.106.157.54:13006/v1")
    llm_model: str = os.getenv("MARKET_LENS_LLM_MODEL", "qwen3.5-27b")
    llm_api_key: str | None = os.getenv("MARKET_LENS_LLM_API_KEY") or None
    llm_timeout: float = float(os.getenv("MARKET_LENS_LLM_TIMEOUT", "60"))
    llm_enabled: bool = os.getenv("MARKET_LENS_LLM_ENABLED", "true").lower() == "true"


settings = Settings()
