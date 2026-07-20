from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp import types
from pydantic import ValidationError

from market_lens.mcp.client import (
    build_docker_stdio_parameters,
    resolve_header_environment,
)
from market_lens.mcp.gateway import McpGateway, McpGatewayError
from market_lens.mcp.models import (
    McpGatewayConfig,
    McpServerConfig,
    McpToolPolicy,
    McpTransport,
)
from market_lens.tools.executor import ToolExecutor
from market_lens.tools.models import ToolRisk, ToolStatus
from market_lens.tools.policy import ToolPolicy
from market_lens.tools.registry import ToolRegistry


class FakeMcpClient:
    def __init__(
        self,
        tools: list[types.Tool],
        result: types.CallToolResult | None = None,
    ) -> None:
        self.tools = tools
        self.result = result or types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")]
        )
        self.calls: list[dict[str, Any]] = []

    async def list_tools(self, server: McpServerConfig) -> list[types.Tool]:
        del server
        return self.tools

    async def call_tool(
        self,
        server: McpServerConfig,
        name: str,
        arguments: dict[str, object],
    ) -> types.CallToolResult:
        self.calls.append({"server": server.name, "name": name, "arguments": arguments})
        return self.result


class RecoveringMcpClient(FakeMcpClient):
    def __init__(self, tools: list[types.Tool]) -> None:
        super().__init__(tools)
        self.discovery_attempts = 0

    async def list_tools(self, server: McpServerConfig) -> list[types.Tool]:
        self.discovery_attempts += 1
        if self.discovery_attempts == 1:
            raise RuntimeError("temporary failure")
        return await super().list_tools(server)


def remote_tool(name: str = "lookup") -> types.Tool:
    return types.Tool(
        name=name,
        description="Look up a value",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def http_server(
    *,
    risk: ToolRisk = ToolRisk.READ,
    tools: dict[str, McpToolPolicy] | None = None,
    max_response_bytes: int = 1_000_000,
) -> McpServerConfig:
    return McpServerConfig(
        name="research",
        enabled=True,
        transport=McpTransport.STREAMABLE_HTTP,
        url="https://mcp.example.com/mcp",
        tools=tools
        or {
            "lookup": McpToolPolicy(
                description="Look up reviewed research data",
                risk=risk,
            )
        },
        max_response_bytes=max_response_bytes,
    )


def test_http_transport_rejects_insecure_or_credentialed_urls() -> None:
    with pytest.raises(ValidationError, match="requires HTTPS"):
        McpGatewayConfig(
            servers=[
                McpServerConfig(
                    name="remote",
                    transport=McpTransport.STREAMABLE_HTTP,
                    url="http://mcp.example.com/mcp",
                )
            ]
        )

    with pytest.raises(ValidationError, match="credentials"):
        McpGatewayConfig(
            servers=[
                McpServerConfig(
                    name="remote",
                    transport=McpTransport.STREAMABLE_HTTP,
                    url="https://user:pass@mcp.example.com/mcp",
                )
            ]
        )


def test_local_http_requires_explicit_gateway_opt_in() -> None:
    config = McpGatewayConfig(
        allow_insecure_local_http=True,
        servers=[
            McpServerConfig(
                name="local",
                transport=McpTransport.STREAMABLE_HTTP,
                url="http://127.0.0.1:9000/mcp",
            )
        ],
    )

    assert config.servers[0].url == "http://127.0.0.1:9000/mcp"


def test_stdio_transport_is_always_a_hardened_docker_process(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOKEN", "secret-value")
    server = McpServerConfig(
        name="local",
        transport=McpTransport.STDIO,
        image=f"registry.example/mcp-server@sha256:{'a' * 64}",
        command=["python", "-m", "example_server"],
        env_from_host=["MCP_TOKEN"],
    )

    params = build_docker_stdio_parameters(server)

    assert params.command == "docker"
    assert "--pull=never" in params.args
    assert "--network=none" in params.args
    assert "--read-only" in params.args
    assert "--cap-drop=ALL" in params.args
    assert "--env" in params.args
    assert "MCP_TOKEN" in params.args
    assert "secret-value" not in params.args
    assert params.env == {"MCP_TOKEN": "secret-value"}
    assert not any(argument.startswith("--volume") for argument in params.args)


def test_http_credentials_are_resolved_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MCP_AUTH", "Bearer secret")
    server = http_server().model_copy(
        update={"headers_from_env": {"Authorization": "RESEARCH_MCP_AUTH"}}
    )

    assert resolve_header_environment(server) == {"Authorization": "Bearer secret"}
    assert server.model_dump()["headers_from_env"] == {
        "Authorization": "RESEARCH_MCP_AUTH"
    }


def test_gateway_registers_only_explicitly_allowlisted_tools() -> None:
    client = FakeMcpClient([remote_tool(), remote_tool("unapproved")])
    gateway = McpGateway(McpGatewayConfig(servers=[http_server()]), client)
    gateway.start()
    registry = ToolRegistry()

    gateway.register_tools(registry)

    assert [spec.name for spec in registry.list()] == ["mcp.research.lookup"]
    schema = registry.schemas()[0]
    assert schema["input_schema"]["required"] == ["query"]
    assert schema["risk"] == "read"


def test_gateway_routes_allowlisted_tool_through_executor_policy() -> None:
    client = FakeMcpClient([remote_tool()])
    gateway = McpGateway(McpGatewayConfig(servers=[http_server()]), client)
    gateway.start()
    registry = ToolRegistry()
    gateway.register_tools(registry)
    executor = ToolExecutor(registry, ToolPolicy(remote_mcp_available=True))

    result = executor.execute("mcp.research.lookup", {"query": "market"})

    assert result.status is ToolStatus.SUCCESS
    assert result.data == {
        "server": "research",
        "remote_tool": "lookup",
        "untrusted_content": True,
        "content": [{"type": "text", "text": "ok"}],
        "structured_content": None,
    }
    assert client.calls == [
        {"server": "research", "name": "lookup", "arguments": {"query": "market"}}
    ]


def test_gateway_rejects_arguments_against_remote_json_schema() -> None:
    client = FakeMcpClient([remote_tool()])
    gateway = McpGateway(McpGatewayConfig(servers=[http_server()]), client)
    gateway.start()
    registry = ToolRegistry()
    gateway.register_tools(registry)
    executor = ToolExecutor(registry, ToolPolicy(remote_mcp_available=True))

    result = executor.execute("mcp.research.lookup", {"query": ""})

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "invalid_input"
    assert client.calls == []


def test_write_mcp_tool_stops_at_confirmation_boundary() -> None:
    client = FakeMcpClient([remote_tool()])
    gateway = McpGateway(
        McpGatewayConfig(servers=[http_server(risk=ToolRisk.WRITE)]),
        client,
    )
    gateway.start()
    registry = ToolRegistry()
    gateway.register_tools(registry)
    executor = ToolExecutor(registry, ToolPolicy(remote_mcp_available=True))

    result = executor.execute("mcp.research.lookup", {"query": "market"})

    assert result.status is ToolStatus.CONFIRMATION_REQUIRED
    assert client.calls == []


def test_missing_allowlisted_tool_disables_server_fail_closed() -> None:
    gateway = McpGateway(
        McpGatewayConfig(servers=[http_server()]),
        FakeMcpClient([remote_tool("different")]),
    )

    gateway.start()

    assert gateway.is_available() is False
    assert "research" in gateway.startup_errors


def test_strict_startup_rejects_mismatched_server_catalog() -> None:
    gateway = McpGateway(
        McpGatewayConfig(servers=[http_server()], startup_strict=True),
        FakeMcpClient([remote_tool("different")]),
    )

    with pytest.raises(McpGatewayError, match="startup failed"):
        gateway.start()


def test_gateway_rejects_oversized_tool_responses() -> None:
    client = FakeMcpClient(
        [remote_tool()],
        types.CallToolResult(
            content=[types.TextContent(type="text", text="x" * 2000)]
        ),
    )
    gateway = McpGateway(
        McpGatewayConfig(servers=[http_server(max_response_bytes=1024)]),
        client,
    )
    gateway.start()
    registry = ToolRegistry()
    gateway.register_tools(registry)
    executor = ToolExecutor(registry, ToolPolicy(remote_mcp_available=True))

    result = executor.execute("mcp.research.lookup", {"query": "market"})

    assert result.status is ToolStatus.ERROR
    assert result.error_code == "mcp_response_too_large"


def test_gateway_refresh_recovers_failed_discovery() -> None:
    client = RecoveringMcpClient([remote_tool()])
    gateway = McpGateway(McpGatewayConfig(servers=[http_server()]), client)

    gateway.start()
    assert gateway.has_started() is True
    assert gateway.is_available() is False

    anyio.run(gateway.arefresh)

    assert gateway.is_available() is True
    assert gateway.startup_errors == {}
