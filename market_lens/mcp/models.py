from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from market_lens.tools.models import ToolRisk


class McpTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class McpToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1, max_length=500)
    risk: ToolRisk
    capability: str = Field(default="mcp", pattern=r"^[a-z][a-z0-9_]*$")
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    idempotent: bool = True
    requires_network: bool = False


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    enabled: bool = False
    transport: McpTransport
    tools: dict[str, McpToolPolicy] = Field(default_factory=dict)
    url: str | None = None
    headers_from_env: dict[str, str] = Field(default_factory=dict)
    image: str | None = None
    command: list[str] = Field(default_factory=list, max_length=64)
    env_from_host: list[str] = Field(default_factory=list, max_length=32)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_response_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    memory_mb: int = Field(default=256, ge=64, le=4096)
    cpu_count: float = Field(default=0.5, gt=0, le=4)
    pids_limit: int = Field(default=64, ge=16, le=512)

    @field_validator("tools")
    @classmethod
    def validate_tool_names(cls, value: dict[str, McpToolPolicy]) -> dict[str, McpToolPolicy]:
        if any(not name or len(name) > 128 for name in value):
            raise ValueError("MCP tool names must be non-empty and at most 128 characters")
        return value

    @field_validator("headers_from_env")
    @classmethod
    def validate_header_mappings(cls, value: dict[str, str]) -> dict[str, str]:
        header_pattern = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
        for header, env_name in value.items():
            if not header_pattern.fullmatch(header) or not _is_env_name(env_name):
                raise ValueError("MCP headers must map valid header names to environment names")
        return value

    @field_validator("env_from_host")
    @classmethod
    def validate_env_names(cls, value: list[str]) -> list[str]:
        if any(not _is_env_name(name) for name in value):
            raise ValueError("MCP environment entries must be environment variable names")
        if len(value) != len(set(value)):
            raise ValueError("MCP environment entries must be unique")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if any(not part or len(part) > 512 or "\x00" in part for part in value):
            raise ValueError("MCP command arguments must be non-empty and at most 512 characters")
        return value

    @model_validator(mode="after")
    def validate_transport(self) -> McpServerConfig:
        if self.transport is McpTransport.STREAMABLE_HTTP:
            if not self.url or self.image or self.command or self.env_from_host:
                raise ValueError("HTTP MCP servers require only a URL and optional header mappings")
        elif not self.image or not self.command or self.url or self.headers_from_env:
            raise ValueError(
                "stdio MCP servers require a container image and command, without URL headers"
            )
        immutable_image = r"(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})"
        if self.image and not re.fullmatch(immutable_image, self.image):
            raise ValueError("MCP stdio container image must use an immutable image ID or digest")
        if self.transport is McpTransport.STDIO and any(
            policy.requires_network for policy in self.tools.values()
        ):
            raise ValueError("stdio MCP tools cannot require network access")
        return self


class McpGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: list[McpServerConfig] = Field(default_factory=list)
    allow_insecure_local_http: bool = False
    startup_strict: bool = False

    @model_validator(mode="after")
    def validate_servers(self) -> McpGatewayConfig:
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")
        for server in self.servers:
            if server.url:
                validate_mcp_url(server.url, self.allow_insecure_local_http)
        return self


def validate_mcp_url(url: str, allow_insecure_local_http: bool) -> None:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ValueError("MCP URL cannot contain credentials or fragments")
    if parsed.scheme == "https":
        return
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if not (
        allow_insecure_local_http
        and parsed.scheme == "http"
        and parsed.hostname.lower() in local_hosts
    ):
        raise ValueError("MCP HTTP transport requires HTTPS; only explicit local HTTP is allowed")


def _is_env_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]*", value))
