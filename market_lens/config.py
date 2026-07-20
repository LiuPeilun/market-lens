from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
load_dotenv(".env.local", override=True)


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
    supabase_url: str | None = os.getenv("SUPABASE_URL") or None
    supabase_publishable_key: str | None = os.getenv("SUPABASE_PUBLISHABLE_KEY") or None
    sandbox_backend: str = os.getenv("MARKET_LENS_SANDBOX_BACKEND", "disabled")
    docker_sandbox_image: str = os.getenv(
        "MARKET_LENS_DOCKER_SANDBOX_IMAGE",
        "python:3.11-slim",
    )
    docker_sandbox_temp_root: Path = Path(
        os.getenv("MARKET_LENS_DOCKER_SANDBOX_TEMP_ROOT", ".tmp/sandboxes")
    )
    daytona_api_key: str | None = os.getenv("DAYTONA_API_KEY") or None
    daytona_api_url: str | None = os.getenv("DAYTONA_API_URL") or None
    daytona_target: str | None = os.getenv("DAYTONA_TARGET") or None
    daytona_sandbox_image: str = os.getenv(
        "MARKET_LENS_DAYTONA_SANDBOX_IMAGE",
        "python:3.11-slim",
    )
    daytona_snapshot: str | None = os.getenv("MARKET_LENS_DAYTONA_SNAPSHOT") or None
    daytona_create_timeout: float = float(
        os.getenv("MARKET_LENS_DAYTONA_CREATE_TIMEOUT", "90")
    )
    daytona_delete_timeout: float = float(
        os.getenv("MARKET_LENS_DAYTONA_DELETE_TIMEOUT", "60")
    )
    daytona_disk_gb: int = int(os.getenv("MARKET_LENS_DAYTONA_DISK_GB", "3"))
    mcp_servers_file: Path | None = (
        Path(value) if (value := os.getenv("MARKET_LENS_MCP_SERVERS_FILE")) else None
    )
    mcp_allow_insecure_local_http: bool = os.getenv(
        "MARKET_LENS_MCP_ALLOW_INSECURE_LOCAL_HTTP", "false"
    ).lower() == "true"
    mcp_startup_strict: bool = os.getenv(
        "MARKET_LENS_MCP_STARTUP_STRICT", "false"
    ).lower() == "true"

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)


settings = Settings()
