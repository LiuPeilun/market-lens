# MCP Gateway boundary

Market Lens treats MCP as a transport, not as an authorization mechanism. An MCP server cannot
register or execute a tool unless the server and tool are explicitly configured in the local
allowlist.

## Execution path

```text
Agent or application
  -> ToolRegistry
  -> ToolPolicy (allow / confirmation required / deny)
  -> ToolExecutor and invocation audit
  -> MCP Gateway
  -> request-scoped MCP client session
  -> reviewed MCP server
```

Discovery does not grant access. The gateway intersects the server's discovered catalog with the
configured `tools` map. A configured tool that disappears from the server fails closed, and a newly
discovered tool remains unavailable until it is reviewed and added to the map.
Tool descriptions are local reviewed configuration and are never accepted from the remote catalog.

## Transports and isolation

- `streamable_http` accepts HTTPS only. Plain HTTP can be enabled only for `localhost`, `127.0.0.1`,
  or `::1` through the explicit development switch.
- `stdio` never launches an arbitrary server command on the host. It launches a reviewed container
  image pinned by immutable sha256 digest through Docker with no network, no mounts, a read-only
  root filesystem, a non-root user,
  dropped capabilities, `no-new-privileges`, process/memory/CPU limits, and `--pull=never`.
- MCP sessions are request scoped. The application does not retain a third-party session between
  calls.
- MCP sampling, elicitation, roots, prompts, and resources are not exposed in this phase. Only
  allowlisted tools are mapped into `ToolRegistry`.
- Tool results are size bounded and marked `untrusted_content`; later LLM orchestration must treat
  them as data rather than instructions.

The stdio implementation currently supports Docker isolation. Daytona remains the production
sandbox for batch execution, but it is not yet used as a long-lived bidirectional MCP transport.

## Credentials

Configuration contains environment variable names, never secret values. HTTP headers use
`headers_from_env`; container variables use `env_from_host`. Missing variables fail before a
connection is attempted. The real `mcp.servers.json` and `.env` files are ignored by Git.

## Risk and approval

Every allowlisted tool must declare one of the existing risk levels: `read`, `compute`, `write`,
`external_side_effect`, or `destructive`. MCP tools are registered with the `remote_mcp` execution
target and use the same policy and Supabase audit path as native tools.

- Read and compute tools can run when the gateway has discovered them.
- Write and external-side-effect tools stop with `confirmation_required` before the MCP call.
- Destructive tools are denied by default.

The policy boundary is connected, but the API/UI approval token and resume flow are still pending.
Until that flow exists, confirmation-required MCP calls do not execute.

## Enabling a reviewed server

1. Copy `mcp.servers.example.json` to the ignored `mcp.servers.json`.
2. Review the server URL or immutable container image digest.
3. Add only approved remote tool names to the server's `tools` map and classify each risk.
4. Put required secrets in `.env` using the referenced environment variable names.
5. Configure `MARKET_LENS_MCP_SERVERS_FILE=mcp.servers.json` and restart the API.
6. Check `/health`; `mcp_available` becomes `true` only after at least one approved tool is
   successfully discovered.

Set `MARKET_LENS_MCP_STARTUP_STRICT=true` when production startup must fail if any enabled server
cannot be verified. The default records the failed server and keeps non-MCP features available.
