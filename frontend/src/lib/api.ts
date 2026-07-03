export type AssetType = 'stock' | 'fund'

export interface AnalyzeRequest {
  asset_type: AssetType
  code: string
  start: string
  end?: string
}

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
  return requestJson<{ code: string; name: string | null; count: number; items: FundNavPoint[] }>(
    `/api/funds/${code}/nav?${params.toString()}`,
  )
}

export function getHealth() {
  return requestJson<{ status: string; version: string }>('/health')
}
