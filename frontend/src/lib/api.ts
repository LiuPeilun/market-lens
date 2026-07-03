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
}

export interface ChatResponse {
  answer: string
  intent: string
  asset: ChatAssetContext | null
  analysis: AnalysisResult | null
  candidates: AssetSearchResult[]
  citations: string[]
}

export type ChatStreamEvent =
  | {
      type: 'meta'
      intent: string
      asset: ChatAssetContext | null
      analysis: AnalysisResult | null
      candidates: AssetSearchResult[]
      citations: string[]
    }
  | { type: 'token'; delta: string }
  | { type: 'done' }
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

export interface AnalysisResult {
  asset_type: AssetType
  code: string
  name: string | null
  as_of: string | null
  latest_price?: number | null
  latest_unit_nav?: number | null
  latest_cumulative_nav?: number | null
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
    missing_factors?: string[]
    required_future_data?: string[]
    method?: string
    status?: string
  }
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
  const response = await fetch(url, {
    headers: {
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
  const response = await fetch('/api/chat/stream', {
    body: JSON.stringify(payload),
    headers: {
      'Content-Type': 'application/json',
    },
    method: 'POST',
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Request failed with ${response.status}`)
  }

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
  return requestJson<{ status: string; version: string }>('/health')
}
