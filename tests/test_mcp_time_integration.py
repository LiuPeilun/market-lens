from __future__ import annotations

import os
from typing import Any

import pytest

from market_lens.mcp.gateway import McpGateway
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


class AuditRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, spec, context, result, input_summary) -> None:
        self.records.append(
            {
                "tool": spec.name,
                "target": spec.execution_target.value,
                "decision": result.policy_decision.value,
                "status": result.status.value,
                "input": input_summary,
            }
        )


@pytest.mark.skipif(
    os.getenv("MARKET_LENS_RUN_MCP_TIME_TESTS", "false").lower() != "true",
    reason="real MCP time integration test is opt-in",
)
def test_time_server_discovery_call_and_audit() -> None:
    image = os.environ["MARKET_LENS_MCP_TIME_IMAGE"]
    server = McpServerConfig(
        name="time_reference",
        enabled=True,
        transport=McpTransport.STDIO,
        image=image,
        command=["--local-timezone", "Asia/Shanghai"],
        tools={
            "get_current_time": McpToolPolicy(
                description="Get current time for an IANA timezone",
                risk=ToolRisk.READ,
                capability="utility",
                timeout_seconds=15,
            ),
            "convert_time": McpToolPolicy(
                description="Convert time between IANA timezones",
                risk=ToolRisk.COMPUTE,
                capability="utility",
                timeout_seconds=15,
            ),
        },
        timeout_seconds=15,
        max_response_bytes=65536,
    )
    gateway = McpGateway(McpGatewayConfig(servers=[server], startup_strict=True))
    gateway.start()
    registry = ToolRegistry()
    gateway.register_tools(registry)
    recorder = AuditRecorder()
    executor = ToolExecutor(
        registry,
        ToolPolicy(remote_mcp_available=True),
        recorder,
    )

    assert [spec.name for spec in registry.list()] == [
        "mcp.time_reference.convert_time",
        "mcp.time_reference.get_current_time",
    ]

    current = executor.execute(
        "mcp.time_reference.get_current_time",
        {"timezone": "Asia/Shanghai"},
    )
    converted = executor.execute(
        "mcp.time_reference.convert_time",
        {
            "source_timezone": "Asia/Shanghai",
            "time": "09:30",
            "target_timezone": "Europe/London",
        },
    )

    assert current.status is ToolStatus.SUCCESS
    assert "Asia/Shanghai" in str(current.data)
    assert converted.status is ToolStatus.SUCCESS
    assert "Europe/London" in str(converted.data)
    assert [record["target"] for record in recorder.records] == [
        "remote_mcp",
        "remote_mcp",
    ]
    assert all(record["decision"] == "allow" for record in recorder.records)
