from __future__ import annotations

import json
import logging
import re
from threading import RLock
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from jsonschema.exceptions import (
    SchemaError,
)
from jsonschema.exceptions import (
    ValidationError as JsonSchemaValidationError,
)
from mcp import types
from pydantic import BaseModel, ConfigDict, Field

from market_lens.mcp.client import McpClient, McpClientError, OfficialMcpClient
from market_lens.mcp.models import McpGatewayConfig, McpServerConfig, McpToolPolicy
from market_lens.tools.executor import ToolPublicError
from market_lens.tools.models import (
    ExecutionTarget,
    ToolContext,
    ToolInput,
    ToolOutput,
    ToolSpec,
)
from market_lens.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class McpToolInput(ToolInput):
    model_config = ConfigDict(extra="allow")


class McpToolOutput(ToolOutput):
    server: str
    remote_tool: str
    untrusted_content: bool = True
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured_content: dict[str, Any] | None = None


class McpGatewayError(RuntimeError):
    pass


class McpGateway:
    def __init__(
        self,
        config: McpGatewayConfig | None = None,
        client: McpClient | None = None,
    ) -> None:
        self.config = config or McpGatewayConfig()
        self.client = client or OfficialMcpClient()
        self._tools: dict[str, tuple[McpServerConfig, types.Tool, McpToolPolicy]] = {}
        self._startup_errors: dict[str, str] = {}
        self._started = False
        self._lock = RLock()

    @property
    def startup_errors(self) -> dict[str, str]:
        with self._lock:
            return dict(self._startup_errors)

    def is_available(self) -> bool:
        with self._lock:
            return bool(self._tools)

    def has_started(self) -> bool:
        with self._lock:
            return self._started

    def start(self) -> None:
        anyio.run(self.astart)

    async def astart(self) -> None:
        with self._lock:
            if self._started:
                return
        await self.arefresh()

    async def arefresh(self) -> None:
        discovered: dict[str, tuple[McpServerConfig, types.Tool, McpToolPolicy]] = {}
        errors: dict[str, str] = {}
        for server in self.config.servers:
            if not server.enabled:
                continue
            try:
                remote_tools = await self.client.list_tools(server)
                self._bind_server_tools(server, remote_tools, discovered)
            except Exception as exc:
                if isinstance(exc, McpClientError):
                    logger.warning("MCP server discovery failed: %s: %s", server.name, exc)
                else:
                    logger.exception("MCP server discovery failed: %s", server.name)
                errors[server.name] = str(exc)
                if self.config.startup_strict:
                    raise McpGatewayError(
                        f"MCP startup failed for configured server '{server.name}'"
                    ) from exc

        with self._lock:
            self._tools = discovered
            self._startup_errors = errors
            self._started = True

    async def aclose(self) -> None:
        # Sessions are intentionally request-scoped; no third-party connection is retained.
        return None

    def register_tools(self, registry: ToolRegistry) -> None:
        with self._lock:
            bindings = list(self._tools.items())
        for public_name, (_server, remote_tool, policy) in bindings:
            registry.register(
                ToolSpec(
                    name=public_name,
                    capability=policy.capability,
                    description=policy.description,
                    input_model=McpToolInput,
                    output_model=McpToolOutput,
                    handler=self._make_handler(public_name),
                    risk=policy.risk,
                    execution_target=ExecutionTarget.REMOTE_MCP,
                    timeout_seconds=policy.timeout_seconds,
                    idempotent=policy.idempotent,
                    requires_network=policy.requires_network,
                    input_schema_override=remote_tool.inputSchema,
                )
            )

    def call_tool(self, public_name: str, arguments: dict[str, Any]) -> McpToolOutput:
        with self._lock:
            binding = self._tools.get(public_name)
        if binding is None:
            raise McpGatewayError("MCP tool is not registered")
        server, remote_tool, _ = binding

        try:
            Draft202012Validator(remote_tool.inputSchema).validate(arguments)
        except (SchemaError, JsonSchemaValidationError) as exc:
            raise ToolPublicError("invalid_input", "MCP tool input validation failed") from exc

        try:
            result = anyio.run(
                self.client.call_tool,
                server,
                remote_tool.name,
                arguments,
            )
        except McpClientError as exc:
            raise ToolPublicError("mcp_unavailable", str(exc)) from exc
        except Exception as exc:
            logger.exception("MCP tool call failed: %s", public_name)
            raise ToolPublicError("mcp_tool_failed", "MCP tool execution failed") from exc

        if result.isError:
            raise ToolPublicError("mcp_tool_error", "MCP tool returned an error")
        output = McpToolOutput(
            server=server.name,
            remote_tool=remote_tool.name,
            content=[_serialize_content(item) for item in result.content],
            structured_content=result.structuredContent,
        )
        encoded = json.dumps(output.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > server.max_response_bytes:
            raise ToolPublicError("mcp_response_too_large", "MCP tool response exceeded its limit")
        return output

    def _bind_server_tools(
        self,
        server: McpServerConfig,
        remote_tools: list[types.Tool],
        discovered: dict[str, tuple[McpServerConfig, types.Tool, McpToolPolicy]],
    ) -> None:
        by_name = {tool.name: tool for tool in remote_tools}
        for remote_name, policy in server.tools.items():
            remote_tool = by_name.get(remote_name)
            if remote_tool is None:
                raise McpGatewayError(
                    f"Configured tool '{remote_name}' was not exposed by MCP server '{server.name}'"
                )
            _validate_input_schema(server, remote_tool)
            public_name = _public_tool_name(server.name, remote_name)
            if public_name in discovered:
                raise McpGatewayError(f"MCP tool name collision: {public_name}")
            discovered[public_name] = (server, remote_tool, policy)

    def _make_handler(self, public_name: str):
        def handler(raw_input: BaseModel, context: ToolContext) -> McpToolOutput:
            del context
            return self.call_tool(
                public_name,
                raw_input.model_dump(mode="python", exclude_none=True),
            )

        return handler


def _validate_input_schema(server: McpServerConfig, tool: types.Tool) -> None:
    schema = tool.inputSchema
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise McpGatewayError(
            f"MCP tool '{server.name}.{tool.name}' must expose an object input schema"
        )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise McpGatewayError(
            f"MCP tool '{server.name}.{tool.name}' exposed an invalid input schema"
        ) from exc


def _public_tool_name(server_name: str, remote_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", remote_name.lower()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"tool_{normalized}"
    return f"mcp.{server_name}.{normalized[:64]}"


def _serialize_content(item: types.ContentBlock) -> dict[str, Any]:
    return item.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        exclude={"meta", "annotations"},
    )
