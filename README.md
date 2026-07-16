# Market Lens

Market Lens is a local agent-service scaffold for investment research workflows. The first
module focuses on A-share and fund analysis using Eastmoney/Tiantian Fund web data endpoints.

The project name follows the current directory. The Python package is `market_lens`, matching the
repository name while keeping Python import syntax valid.

## What is included

- FastAPI service for stock/fund data queries and valuation analysis.
- Eastmoney data client:
  - stock K-line data from `push2his.eastmoney.com`
  - stock valuation history from Eastmoney data center
  - fund NAV history from Tiantian Fund `F10DataApi`
- Deterministic analysis engine for valuation percentile, return, drawdown, and basic signals.
- SQLite-backed HTTP cache to reduce repeated calls to public web endpoints.
- Independent React frontend for the research workspace.
- Supabase authentication plus per-user analysis and chat history.
- CLI for quick local checks.
- A protocol-neutral sandbox boundary with a locked-down local Docker backend and a reserved
  Daytona backend.
- Codex integration placeholders with explicit boundaries.

Public Eastmoney/Tiantian Fund endpoints are web interfaces, not official SLA-backed APIs. Use this
project for personal research and local analysis. Do not treat generated output as investment advice.

## Quick start

```powershell
uv python install 3.12
uv sync --dev
copy .env.example .env
```

The checked-in `.python-version` pins local development to CPython 3.12. This avoids SSL trust-store
incompatibilities observed with some Conda Python/OpenSSL builds when importing the Daytona SDK.

Configure the hosted Supabase project in the root `.env`:

```dotenv
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_PUBLISHABLE_KEY=your-publishable-key
```

Configure the same public application connection in `frontend/.env.local`:

```dotenv
VITE_SUPABASE_URL=https://your-project-ref.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=your-publishable-key
```

Never put a Supabase secret/service-role key in the frontend or in `.env.example`.

For local Supabase development, start Docker Desktop and run:

```powershell
npx supabase@latest start
```

Keep hosted values in `.env` and `frontend/.env.local`. Put the local API URL and publishable key
reported by the CLI in ignored local override files:

```dotenv
# .env.local
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_PUBLISHABLE_KEY=<local-publishable-key>

# frontend/.env.development.local
VITE_SUPABASE_URL=http://127.0.0.1:54321
VITE_SUPABASE_PUBLISHABLE_KEY=<local-publishable-key>
```

The backend loads `.env.local` after `.env`, and Vite applies `.env.development.local` only in
development mode. `run.ps1` checks and starts local Supabase automatically; use
`./run.ps1 -SkipSupabaseStart` only when managing that local service separately. To use hosted
Supabase, remove or rename the two local override files before starting the application.

Apply the checked-in database migration before using authenticated analysis:

```powershell
npx supabase@latest login
npx supabase@latest link --project-ref <project-ref>
npx supabase@latest db push
```

Run the API:

```powershell
uv run uvicorn market_lens.api.app:app --reload --host 127.0.0.1 --port 8000
```

Run the frontend:

```powershell
cd frontend
pnpm install
pnpm dev
```

Try endpoints:

```text
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/api/search?keyword=贵州茅台
GET http://127.0.0.1:8000/api/stocks/600519/history?start=2015-01-01
GET http://127.0.0.1:8000/api/stocks/600519/valuation
GET http://127.0.0.1:8000/api/funds/161725/nav?start=2015-01-01
POST http://127.0.0.1:8000/api/analyze
```

Analysis, chat, and history endpoints require the Supabase access token in the request header:

```text
Authorization: Bearer <access-token>
```

Frontend:

```text
http://127.0.0.1:5173
```

Example analysis body:

```json
{
  "asset_type": "stock",
  "code": "600519",
  "start": "2018-01-01",
  "end": "2026-07-02"
}
```

CLI examples:

```powershell
uv run market-lens analyze stock 600519 --start 2018-01-01
uv run market-lens analyze fund 161725 --start 2015-01-01
```

## Architecture

```text
market_lens/
  agent/          Business orchestration for user-facing analysis
  capabilities/   Domain capability packs exposed through the common tool layer
  api/            FastAPI app and schemas
  data/           Eastmoney/Tiantian Fund data adapters
  storage/        SQLite cache and Supabase persistence adapter
  tools/          Tool registry, schemas, policy, execution, and audit boundaries
  valuation/      Metrics and valuation signal logic
  sandbox/        Isolated execution models and Docker/Daytona backend boundaries
  engineering/    Codex engineering-agent boundary placeholder
frontend/
  src/
    components/ui/  shadcn/ui-style source-owned components
    lib/            API client, query client, formatting helpers
    pages/          Workspace pages
supabase/
  migrations/       PostgreSQL schema, indexes, grants, and RLS policies
```

The runtime product path is:

```text
User/API
  -> Supabase Auth
  -> market_lens.agent
  -> ToolRegistry and ToolPolicy
  -> capability tools
  -> market_lens.data adapters
  -> local SQLite cache
  -> market_lens.valuation
  -> JSON report and Supabase history
```

Codex should remain an engineering assistant for changing this codebase. Generated analysis scripts
must use the sandbox boundary once that capability is exposed; they must not execute inside the
FastAPI process.

## Sandbox execution

Sandbox execution is disabled by default and is not currently exposed as an LLM-callable tool. The
current Docker backend establishes the execution boundary needed before MCP or generated-code tools
are added.

Prepare the configured image explicitly. Market Lens never pulls sandbox images automatically:

```powershell
docker pull python:3.11-slim
docker image inspect python:3.11-slim
```

Then opt in through the backend environment file:

```dotenv
MARKET_LENS_SANDBOX_BACKEND=docker
MARKET_LENS_DOCKER_SANDBOX_IMAGE=python:3.11-slim
MARKET_LENS_DOCKER_SANDBOX_TEMP_ROOT=.tmp/sandboxes
```

The Docker runner uses a fixed local image, disabled networking, a read-only root filesystem, a
non-root user, dropped Linux capabilities, `no-new-privileges`, and CPU, memory, process, output,
artifact, and wall-clock limits. It mounts only a temporary read-only input directory and a temporary
output directory. The repository, environment files, host shell, and Docker socket are not mounted.

For production remote execution, create an API key in the Daytona dashboard and configure the
Daytona backend:

```dotenv
MARKET_LENS_SANDBOX_BACKEND=daytona
DAYTONA_API_KEY=<daytona-api-key>
DAYTONA_API_URL=https://app.daytona.io/api
DAYTONA_TARGET=
MARKET_LENS_DAYTONA_SANDBOX_IMAGE=python:3.11-slim
MARKET_LENS_DAYTONA_SNAPSHOT=
MARKET_LENS_DAYTONA_CREATE_TIMEOUT=90
MARKET_LENS_DAYTONA_DELETE_TIMEOUT=60
MARKET_LENS_DAYTONA_DISK_GB=3
```

The Daytona runner creates a private ephemeral sandbox for each request, maps CPU and memory limits
to Daytona resources, uploads only declared input files, limits downloaded output and artifacts,
and waits for remote deletion on every completion or failure path. Network access is blocked by
default. Requests using the allowlist policy pass only validated domain names to Daytona's domain
allowlist.

Image-based creation applies per-request resource limits. For lower startup latency in production,
configure a prebuilt `MARKET_LENS_DAYTONA_SNAPSHOT`; the snapshot's provisioned resources then define
the runtime limits. Prefer an immutable image digest or a controlled snapshot over a mutable image
tag.

The real remote integration test is opt-in because it creates billable external resources:

```powershell
$env:MARKET_LENS_RUN_DAYTONA_TESTS="true"
uv run pytest tests/test_daytona_sandbox_integration.py -v
```

MCP integration is a later transport layer and does not bypass the tool policy or sandbox boundary.

## Development

```powershell
uv add <package>
uv add --dev <package>
uv run pytest
uv run ruff check .
```
