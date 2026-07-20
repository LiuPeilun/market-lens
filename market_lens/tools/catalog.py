from __future__ import annotations

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.capabilities.finance.tools import register_finance_tools
from market_lens.data.eastmoney import EastmoneyClient
from market_lens.mcp.gateway import McpGateway
from market_lens.sandbox.runner import SandboxRunner
from market_lens.tools.executor import ToolAuditRecorder, ToolExecutor
from market_lens.tools.policy import ToolPolicy
from market_lens.tools.registry import ToolRegistry


def build_default_registry(
    data_client: EastmoneyClient | None = None,
    analysis_agent: MarketAnalysisAgent | None = None,
    mcp_gateway: McpGateway | None = None,
) -> ToolRegistry:
    client = data_client or EastmoneyClient()
    agent = analysis_agent or MarketAnalysisAgent(client)
    registry = ToolRegistry()
    register_finance_tools(registry, client, agent)
    if mcp_gateway is not None:
        mcp_gateway.register_tools(registry)
    return registry


def build_default_executor(
    data_client: EastmoneyClient | None = None,
    analysis_agent: MarketAnalysisAgent | None = None,
    policy: ToolPolicy | None = None,
    audit_recorder: ToolAuditRecorder | None = None,
    sandbox_runner: SandboxRunner | None = None,
    mcp_gateway: McpGateway | None = None,
) -> ToolExecutor:
    registry = build_default_registry(data_client, analysis_agent, mcp_gateway)
    effective_policy = policy or ToolPolicy(
        sandbox_available=sandbox_runner.is_available() if sandbox_runner else False,
        remote_mcp_available=mcp_gateway.is_available() if mcp_gateway else False,
    )
    return ToolExecutor(
        registry=registry,
        policy=effective_policy,
        audit_recorder=audit_recorder,
    )
