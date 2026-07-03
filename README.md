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
- CLI for quick local checks.
- Daytona and Codex integration placeholders with explicit boundaries.

Public Eastmoney/Tiantian Fund endpoints are web interfaces, not official SLA-backed APIs. Use this
project for personal research and local analysis. Do not treat generated output as investment advice.

## Quick start

```powershell
uv sync --dev
copy .env.example .env
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
  storage/        SQLite cache
  valuation/      Metrics and valuation signal logic
  sandbox/        Daytona execution boundary placeholder
  engineering/    Codex engineering-agent boundary placeholder
frontend/
  src/
    components/ui/  shadcn/ui-style source-owned components
    lib/            API client, query client, formatting helpers
    pages/          Workspace pages
```

The runtime product path is:

```text
User/API
  -> market_lens.agent
  -> market_lens.data tools
  -> local SQLite cache
  -> market_lens.valuation
  -> JSON report
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
