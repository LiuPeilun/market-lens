from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from market_lens.config import settings
from market_lens.mcp.client import OfficialMcpClient
from market_lens.mcp.gateway import McpGateway, McpGatewayError
from market_lens.mcp.models import McpGatewayConfig


def build_mcp_gateway() -> McpGateway:
    if settings.mcp_servers_file is None:
        return McpGateway()
    config = load_mcp_config(
        settings.mcp_servers_file,
        allow_insecure_local_http=settings.mcp_allow_insecure_local_http,
        startup_strict=settings.mcp_startup_strict,
    )
    return McpGateway(config, client=OfficialMcpClient(http_proxy=settings.mcp_http_proxy))


def load_mcp_config(
    path: Path,
    *,
    allow_insecure_local_http: bool = False,
    startup_strict: bool = False,
) -> McpGatewayConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("MCP configuration root must be an object")
        payload["allow_insecure_local_http"] = allow_insecure_local_http
        payload["startup_strict"] = startup_strict
        return McpGatewayConfig.model_validate(payload)
    except FileNotFoundError as exc:
        raise McpGatewayError(f"MCP configuration file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise McpGatewayError(f"MCP configuration is invalid: {path}") from exc
