from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Protocol

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from market_lens.mcp.models import McpServerConfig, McpTransport


class McpClientError(RuntimeError):
    pass


class McpClient(Protocol):
    async def list_tools(self, server: McpServerConfig) -> list[types.Tool]: ...

    async def call_tool(
        self,
        server: McpServerConfig,
        name: str,
        arguments: dict[str, object],
    ) -> types.CallToolResult: ...


class OfficialMcpClient:
    def __init__(self, http_proxy: str | None = None) -> None:
        self.http_proxy = http_proxy

    async def list_tools(self, server: McpServerConfig) -> list[types.Tool]:
        tools: list[types.Tool] = []
        cursor: str | None = None
        async with self._session(server) as session:
            while True:
                result = await session.list_tools(cursor=cursor)
                tools.extend(result.tools)
                if len(tools) > 1000:
                    raise McpClientError("MCP server exposed more than 1000 tools")
                cursor = result.nextCursor
                if not cursor:
                    return tools

    async def call_tool(
        self,
        server: McpServerConfig,
        name: str,
        arguments: dict[str, object],
    ) -> types.CallToolResult:
        async with self._session(server) as session:
            return await session.call_tool(
                name,
                arguments,
                read_timeout_seconds=timedelta(seconds=server.timeout_seconds),
            )

    @asynccontextmanager
    async def _session(self, server: McpServerConfig) -> AsyncIterator[ClientSession]:
        try:
            with anyio.fail_after(server.timeout_seconds):
                if server.transport is McpTransport.STDIO:
                    params = build_docker_stdio_parameters(server)
                    async with stdio_client(params) as streams:
                        async with ClientSession(
                            *streams,
                            read_timeout_seconds=timedelta(seconds=server.timeout_seconds),
                        ) as session:
                            await session.initialize()
                            yield session
                    return

                headers = resolve_header_environment(server)
                timeout = httpx.Timeout(server.timeout_seconds)
                async with httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                    proxy=self.http_proxy,
                    trust_env=False,
                ) as http_client:
                    async with streamable_http_client(
                        server.url or "",
                        http_client=http_client,
                    ) as streams:
                        async with ClientSession(
                            streams[0],
                            streams[1],
                            read_timeout_seconds=timedelta(seconds=server.timeout_seconds),
                        ) as session:
                            await session.initialize()
                            yield session
        except McpClientError:
            raise
        except TimeoutError as exc:
            raise McpClientError(f"MCP server '{server.name}' timed out") from exc
        except Exception as exc:
            raise McpClientError(f"MCP server '{server.name}' is unavailable") from exc


def build_docker_stdio_parameters(server: McpServerConfig) -> StdioServerParameters:
    if server.transport is not McpTransport.STDIO:
        raise McpClientError("Docker stdio parameters require the stdio transport")

    injected_env = resolve_process_environment(server)
    args = [
        "run",
        "--rm",
        "-i",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={server.pids_limit}",
        f"--memory={server.memory_mb}m",
        f"--cpus={server.cpu_count}",
        "--user=65532:65532",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
    ]
    for name in server.env_from_host:
        args.extend(["--env", name])
    args.extend([server.image or "", *server.command])
    return StdioServerParameters(command="docker", args=args, env=injected_env)


def resolve_process_environment(server: McpServerConfig) -> dict[str, str]:
    return {name: _required_environment(name, server.name) for name in server.env_from_host}


def resolve_header_environment(server: McpServerConfig) -> dict[str, str]:
    return {
        header: _required_environment(env_name, server.name)
        for header, env_name in server.headers_from_env.items()
    }


def _required_environment(name: str, server_name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise McpClientError(
            f"MCP server '{server_name}' requires missing environment variable '{name}'"
        )
    return value
