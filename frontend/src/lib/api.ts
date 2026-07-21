import { getAccessToken } from '@/lib/supabase'

export type AssetType = 'stock' | 'fund'

export interface AnalyzeRequest {
  asset_type: AssetType
  code: string
  start: string
  end?: string
}

export interface ChatAssetContext {
  asset_type: AssetType
  code: string
  name?: string | null
}

export interface ChatRequest {
  message: string
  context?: ChatAssetContext | null
  start: string
  end?: string
  session_id?: string | null
}

export interface ChatResponse {
  answer: string
  intent: string
  asset: ChatAssetContext | null
  analysis: AnalysisResult | null
  candidates: AssetSearchResult[]
  citations: string[]
  session_id: string | null
}

export interface ToolApproval {
  id: string
  tool_name: string
  risk_level: 'read' | 'compute' | 'write' | 'external_side_effect' | 'destructive'
  execution_target: 'trusted_local' | 'sandbox_required' | 'remote_mcp'
  reason: string
  input_summary: Record<string, unknown>
  status: 'pending' | 'approved' | 'denied' | 'executed' | 'failed' | 'expired'
  expires_at: string
}

export type ChatStreamEvent =
  | {
      type: 'meta'
      intent: string
      asset: ChatAssetContext | null
      analysis: AnalysisResult | null
      candidates: AssetSearchResult[]
      citations: string[]
      session_id?: string
    }
  | { type: 'token'; delta: string }
  | { type: 'citations'; citations: string[] }
  | {
      type: 'approval_required'
      approval: ToolApproval
      citations: string[]
      session_id: string
    }
  | { type: 'done'; session_id?: string }
  | { type: 'error'; message: string }

export interface AssetSearchResult {
  asset_type: AssetType
  code: string
  name: string
  market: string | null
  quote_id: string | null
  source_type: string | null
}

export interface StockBar {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
  amount: number
  amplitude_pct: number | null
  change_pct: number | null
  change_amount: number | null
  turnover_pct: number | null
}

export interface StockValuationPoint {
  date: string
  code: string
  name: string | null
  close: number | null
  market_cap: number | null
  pe_ttm: number | null
  pe_static: number | null
  pb: number | null
  ps_ttm: number | null
  pcf_ocf_ttm: number | null
  peg: number | null
}

export interface FundNavPoint {
  date: string
  unit_nav: number | null
  cumulative_nav: number | null
  daily_growth_pct: number | null
  subscribe_status: string | null
  redeem_status: string | null
}

export interface FundHoldingsRouteInfo {
  source: string
  scope: string
  as_of: string | null
  coverage: number
  fallback_reasons: string[]
  fund_type: string | null
  tracked_index_code: string | null
  tracked_index_name: string | null
  target_etf_code: string | null
  target_etf_name: string | null
}

export type AssessmentDimensionCategory = 'valuation' | 'quality' | 'product'

export interface AssessmentFactor {
  key: string | null
  name: string | null
  category: AssessmentDimensionCategory | null
  value: unknown
  unit: string | null
  source_as_of: string | null
  score: number | null
  direction: string | null
  normalization: string | null
  weight: number | null
  effective_weight: number | null
  sample_size: number | null
  coverage: number | null
  source: string | null
  status: string | null
  reason?: string | null
}

export interface AssessmentDimension {
  model: string
  score: number | null
  level: string
  level_zh: string
  confidence: number
  factors: AssessmentFactor[]
  weight_coverage: number
  data_coverage: number
  sample_adequacy: number
  warnings: string[]
}

export interface ValuationAssessment {
  schema_version: string
  model_version: string
  profile: string
  analysis_as_of: string | null
  dimensions: {
    valuation: AssessmentDimension
    quality: AssessmentDimension
    product: AssessmentDimension | null
  }
  overall_confidence: number
  attractiveness: number | null
  confidence_detail: {
    components?: Record<string, number>
    caps?: Array<Record<string, unknown>>
    reasons?: string[]
    dimensions?: Record<string, unknown>
  }
  data_quality: {
    sources: Array<Record<string, unknown>>
    warnings: string[]
    source_as_of: string | null
    retrieved_at: string | null
  }
  routing?: Record<string, unknown> | null
}

export interface AnalysisResult {
  asset_type: AssetType
  code: string
  name: string | null
  as_of: string | null
  latest_price?: number | null
  latest_unit_nav?: number | null
  latest_cumulative_nav?: number | null
  assessment?: ValuationAssessment | null
  valuation: {
    as_of?: string | null
    pe_ttm?: number | null
    pb?: number | null
    pe_ttm_percentile?: number | null
    pb_percentile?: number | null
    pe_ttm_label?: string
    pb_label?: string
    score?: number | null
    level?: string
    level_zh?: string
    confidence?: number
    confidence_label?: string
    profile?: string
    profile_name?: string
    factor_coverage?: number
    holding_factor_coverage?: number
    missing_factors?: string[]
    required_future_data?: string[]
    method?: string
    status?: string
    holdings_route?: FundHoldingsRouteInfo
    portfolio?: {
      metrics?: Record<string, { value: number | null; coverage: number }>
      industry_weights?: Array<{ industry: string; weight_pct: number }>
    }
    holdings?: {
      report_date: string | null
      report_age_days: number | null
      count: number
      analyzed_count: number
      top_holdings_weight: number
      analyzed_holdings_weight: number
      items: Array<{
        rank: number
        code: string
        name: string
        weight_pct: number | null
        shares_10k: number | null
        market_value_10k: number | null
        analysis_available: boolean
        industry: string | null
        pe_ttm: number | null
        pb: number | null
        roe_weighted: number | null
        parent_netprofit_growth_pct: number | null
        dividend_yield: number | null
      }>
    }
  }
  holdings_route?: FundHoldingsRouteInfo
  performance: {
    sample_size: number
    total_return: number | null
    annualized_return: number | null
    max_drawdown: number | null
    total_return_text: string | null
    annualized_return_text: string | null
    max_drawdown_text: string | null
  }
  notes: string[]
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const accessToken = await getAccessToken()
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    ...init,
  })

  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const message = body?.detail ?? `Request failed with ${response.status}`
    throw new Error(message)
  }

  return response.json() as Promise<T>
}

export function analyzeAsset(payload: AnalyzeRequest) {
  return requestJson<{ result: AnalysisResult }>('/api/analyze', {
    body: JSON.stringify(payload),
    method: 'POST',
  }).then((data) => data.result)
}

export function chatWithAgent(payload: ChatRequest) {
  return requestJson<ChatResponse>('/api/chat', {
    body: JSON.stringify(payload),
    method: 'POST',
  })
}

export async function streamChatWithAgent(
  payload: ChatRequest,
  onEvent: (event: ChatStreamEvent) => void,
) {
  const accessToken = await getAccessToken()
  const response = await fetch('/api/chat/stream', {
    body: JSON.stringify(payload),
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    method: 'POST',
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Request failed with ${response.status}`)
  }

  await consumeChatStream(response, onEvent)
}

export async function resumeToolApproval(
  approvalId: string,
  decision: 'approve' | 'deny',
  onEvent: (event: ChatStreamEvent) => void,
) {
  const accessToken = await getAccessToken()
  const response = await fetch(`/api/tool-approvals/${approvalId}/stream`, {
    body: JSON.stringify({ decision }),
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    method: 'POST',
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Request failed with ${response.status}`)
  }

  await consumeChatStream(response, onEvent)
}

async function consumeChatStream(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void,
) {
  if (!response.body) throw new Error('Streaming response body is unavailable')
  const decoder = new TextDecoder()
  const reader = response.body.getReader()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const data = chunk
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trim())
        .join('\n')
      if (!data) continue
      onEvent(JSON.parse(data) as ChatStreamEvent)
    }
  }
  if (buffer.trim()) {
    const data = buffer
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('\n')
    if (data) onEvent(JSON.parse(data) as ChatStreamEvent)
  }
}

export function searchAssets(keyword: string, assetType?: AssetType, limit = 10) {
  const params = new URLSearchParams({ keyword, limit: String(limit) })
  if (assetType) params.set('asset_type', assetType)
  return requestJson<{ keyword: string; count: number; items: AssetSearchResult[] }>(
    `/api/search?${params.toString()}`,
  )
}

export function getStockHistory(symbol: string, start: string, end?: string) {
  const params = new URLSearchParams({ start })
  if (end) params.set('end', end)
  return requestJson<{ symbol: string; name: string | null; count: number; items: StockBar[] }>(
    `/api/stocks/${symbol}/history?${params.toString()}`,
  )
}

export function getStockValuation(symbol: string) {
  return requestJson<{ symbol: string; name: string | null; count: number; items: StockValuationPoint[] }>(
    `/api/stocks/${symbol}/valuation`,
  )
}

export function getFundNav(code: string, start: string, end?: string) {
  const params = new URLSearchParams({ start })
  if (end) params.set('end', end)
  return requestJson<{
    code: string
    name: string | null
    data_source?: string
    count: number
    items: FundNavPoint[]
  }>(
    `/api/funds/${code}/nav?${params.toString()}`,
  )
}

export function getHealth() {
  return requestJson<{ status: string; version: string; supabase_configured: boolean }>('/health')
}

export interface AnalysisHistoryItem {
  id: string
  asset_type: AssetType
  asset_code: string
  asset_name: string | null
  request_params: Record<string, unknown>
  result: AnalysisResult
  created_at: string
}

export interface ChatSessionHistoryItem {
  id: string
  title: string
  asset_type: AssetType | null
  asset_code: string | null
  asset_name: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessageHistoryItem {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: string[]
  analysis_run_id: string | null
  created_at: string
}

export function getAnalysisHistory(limit = 30) {
  return requestJson<{ count: number; items: AnalysisHistoryItem[] }>(
    `/api/history/analyses?limit=${limit}`,
  )
}

export function getChatSessionHistory(limit = 30) {
  return requestJson<{ count: number; items: ChatSessionHistoryItem[] }>(
    `/api/history/chat-sessions?limit=${limit}`,
  )
}

export function getChatMessageHistory(sessionId: string) {
  return requestJson<{ count: number; items: ChatMessageHistoryItem[] }>(
    `/api/history/chat-sessions/${sessionId}/messages`,
  )
}
