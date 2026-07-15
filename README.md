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
- Daytona and Codex integration placeholders with explicit boundaries.

Public Eastmoney/Tiantian Fund endpoints are web interfaces, not official SLA-backed APIs. Use this
project for personal research and local analysis. Do not treat generated output as investment advice.

## Quick start

```powershell
uv sync --dev
copy .env.example .env
```

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
  api/            FastAPI app and schemas
  data/           Eastmoney/Tiantian Fund data adapters
  storage/        SQLite cache and Supabase persistence adapter
  valuation/      Metrics and valuation signal logic
  sandbox/        Daytona execution boundary placeholder
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
  -> market_lens.data tools
  -> local SQLite cache
  -> market_lens.valuation
  -> JSON report and Supabase history
```

Codex should remain an engineering assistant for changing this codebase. Daytona should be used for
isolated execution of generated analysis scripts once that integration is added.

## Development

```powershell
uv add <package>
uv add --dev <package>
uv run pytest
uv run ruff check .
```
