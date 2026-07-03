import { useEffect, useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'
import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import { Activity, Database, Search, Server, TrendingDown, TrendingUp } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  type AssetType,
  type AssetSearchResult,
  type AnalysisResult,
  analyzeAsset,
  getFundNav,
  getHealth,
  getStockHistory,
  getStockValuation,
  searchAssets,
} from '@/lib/api'
import { formatLabel, formatNumber, formatPercent, formatRatioPercentile } from '@/lib/format'

interface SubmittedQuery {
  assetType: AssetType
  code: string
  name?: string
  start: string
  end?: string
}

const defaultQuery: SubmittedQuery = {
  assetType: 'stock',
  code: '600519',
  start: '2018-01-01',
}

export function DashboardPage() {
  const [assetType, setAssetType] = useState<AssetType>(defaultQuery.assetType)
  const [code, setCode] = useState(defaultQuery.code)
  const [start, setStart] = useState(defaultQuery.start)
  const [end, setEnd] = useState('')
  const [submitted, setSubmitted] = useState<SubmittedQuery>(defaultQuery)
  const [selectedAssetName, setSelectedAssetName] = useState<string | null>(null)
  const [debouncedKeyword, setDebouncedKeyword] = useState(defaultQuery.code)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isResolving, setIsResolving] = useState(false)
  const trimmedCode = code.trim()

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedKeyword(trimmedCode), 250)
    return () => window.clearTimeout(timer)
  }, [trimmedCode])

  const healthQuery = useQuery({
    queryFn: getHealth,
    queryKey: ['health'],
  })

  const searchQuery = useQuery({
    enabled: shouldSearchByKeyword(debouncedKeyword),
    queryFn: () => searchAssets(debouncedKeyword, undefined, 8),
    queryKey: ['asset-search', debouncedKeyword],
  })

  const analysisQuery = useQuery({
    enabled: Boolean(submitted.code && submitted.start),
    queryFn: () =>
      analyzeAsset({
        asset_type: submitted.assetType,
        code: submitted.code,
        end: submitted.end,
        start: submitted.start,
      }),
    queryKey: ['analysis', submitted],
  })

  const stockHistoryQuery = useQuery({
    enabled: submitted.assetType === 'stock',
    queryFn: () => getStockHistory(submitted.code, submitted.start, submitted.end),
    queryKey: ['stock-history', submitted],
  })

  const stockValuationQuery = useQuery({
    enabled: submitted.assetType === 'stock',
    queryFn: () => getStockValuation(submitted.code),
    queryKey: ['stock-valuation', submitted.code],
  })

  const fundNavQuery = useQuery({
    enabled: submitted.assetType === 'fund',
    queryFn: () => getFundNav(submitted.code, submitted.start, submitted.end),
    queryKey: ['fund-nav', submitted],
  })

  const chartOption = useMemo<EChartsOption>(() => {
    const rows =
      submitted.assetType === 'stock'
        ? (stockHistoryQuery.data?.items ?? []).map((item) => [item.date, item.close])
        : (fundNavQuery.data?.items ?? []).map((item) => [item.date, item.unit_nav])

    return {
      animationDuration: 300,
      color: ['#0f766e'],
      grid: { bottom: 42, left: 48, right: 24, top: 24 },
      series: [
        {
          areaStyle: { color: 'rgba(15, 118, 110, 0.08)' },
          data: rows,
          name: submitted.assetType === 'stock' ? 'close' : 'unit nav',
          showSymbol: false,
          smooth: true,
          type: 'line',
        },
      ],
      tooltip: {
        trigger: 'axis',
      },
      xAxis: {
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        type: 'time',
      },
      yAxis: {
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        scale: true,
        type: 'value',
      },
    }
  }, [fundNavQuery.data?.items, stockHistoryQuery.data?.items, submitted.assetType])

  const result = analysisQuery.data
  const currentAssetLabel = formatAssetLabel(
    result?.name ?? submitted.name,
    result?.code ?? submitted.code,
  )
  const isBusy =
    isResolving ||
    analysisQuery.isFetching ||
    stockHistoryQuery.isFetching ||
    stockValuationQuery.isFetching ||
    fundNavQuery.isFetching
  const queryError =
    analysisQuery.error ??
    stockHistoryQuery.error ??
    stockValuationQuery.error ??
    fundNavQuery.error
  const errorMessage = submitError ?? queryError?.message
  const searchResults = searchQuery.data?.items ?? []

  async function submitForm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedInput = code.trim()
    if (!normalizedInput) {
      setSubmitError('请输入代码或名称。')
      return
    }

    setSubmitError(null)
    if (/^\d{6}$/.test(normalizedInput)) {
      const submittedAssetType = inferSubmittedAssetType(normalizedInput, assetType)
      setAssetType(submittedAssetType)
      setSelectedAssetName(null)
      setSubmitted({
        assetType: submittedAssetType,
        code: normalizedInput,
        end: end || undefined,
        start,
      })
      return
    }

    setIsResolving(true)
    try {
      const response = await searchAssets(normalizedInput, undefined, 8)
      const candidate = findBestSearchResult(normalizedInput, response.items)
      if (!candidate) {
        setSubmitError(`没有找到“${normalizedInput}”对应的基金或 A 股股票。`)
        return
      }
      selectAsset(candidate, { submit: true })
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '名称查询失败。')
    } finally {
      setIsResolving(false)
    }
  }

  function switchAssetType(value: AssetType) {
    setAssetType(value)
    if (value === 'stock') {
      setCode('600519')
      setStart('2018-01-01')
    } else {
      setCode('161725')
      setStart('2015-01-01')
    }
    setSelectedAssetName(null)
    setSubmitError(null)
    setEnd('')
  }

  function selectAsset(candidate: AssetSearchResult, options?: { submit?: boolean }) {
    setAssetType(candidate.asset_type)
    setCode(candidate.code)
    setSelectedAssetName(candidate.name)
    setSubmitError(null)
    if (options?.submit) {
      setSubmitted({
        assetType: candidate.asset_type,
        code: candidate.code,
        end: end || undefined,
        name: candidate.name,
        start,
      })
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-4 border-b pb-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Activity className="size-4" />
            </div>
            <span className="text-sm font-medium text-muted-foreground">Market Lens</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-normal sm:text-3xl">投研工作台</h1>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={healthQuery.data?.status === 'ok' ? 'secondary' : 'destructive'}>
            <Server className="mr-1 size-3" />
            API {healthQuery.data?.version ?? '—'}
          </Badge>
          <Badge variant="outline">
            <Database className="mr-1 size-3" />
            Eastmoney
          </Badge>
        </div>
      </header>

      <section className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>查询</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="grid gap-4" onSubmit={submitForm}>
              <div className="grid gap-2">
                <Label htmlFor="asset-type">资产类型</Label>
                <Select value={assetType} onValueChange={(value) => switchAssetType(value as AssetType)}>
                  <SelectTrigger id="asset-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="stock">股票</SelectItem>
                    <SelectItem value="fund">基金</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="code">代码或名称</Label>
                <Input
                  id="code"
                  placeholder="例如：600519、019670、贵州茅台"
                  value={code}
                  onChange={(event) => {
                    setCode(event.target.value)
                    setSelectedAssetName(null)
                    setSubmitError(null)
                  }}
                />
                {selectedAssetName ? (
                  <div className="text-xs text-muted-foreground">
                    已选择：{selectedAssetName} ({code})
                  </div>
                ) : null}
                {shouldSearchByKeyword(trimmedCode) ? (
                  <div className="rounded-md border bg-background p-1">
                    {searchQuery.isFetching ? (
                      <div className="px-2 py-2 text-sm text-muted-foreground">搜索中...</div>
                    ) : null}
                    {!searchQuery.isFetching && searchResults.length === 0 ? (
                      <div className="px-2 py-2 text-sm text-muted-foreground">暂无匹配结果</div>
                    ) : null}
                    {searchResults.map((item) => (
                      <button
                        className="flex w-full items-center justify-between gap-3 rounded-sm px-2 py-2 text-left text-sm hover:bg-muted"
                        key={`${item.asset_type}-${item.code}`}
                        onClick={() => selectAsset(item)}
                        type="button"
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium">{item.name}</span>
                          <span className="text-xs text-muted-foreground">{item.code}</span>
                        </span>
                        <Badge variant={item.asset_type === 'stock' ? 'secondary' : 'outline'}>
                          {item.asset_type === 'stock' ? '股票' : '基金'}
                        </Badge>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-2">
                  <Label htmlFor="start">开始日期</Label>
                  <Input
                    id="start"
                    type="date"
                    value={start}
                    onChange={(event) => setStart(event.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="end">结束日期</Label>
                  <Input
                    id="end"
                    type="date"
                    value={end}
                    onChange={(event) => setEnd(event.target.value)}
                  />
                </div>
              </div>

              <Button className="w-full" disabled={isBusy} type="submit">
                <Search className="size-4" />
                {isResolving ? '解析中' : '分析'}
              </Button>
            </form>
          </CardContent>
        </Card>

        <div className="grid gap-6">
          {errorMessage ? (
            <Alert className="border-destructive/30">
              <AlertTitle>请求失败</AlertTitle>
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          ) : null}

          <MetricGrid isBusy={isBusy} result={result} />

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>走势</CardTitle>
              <Badge variant="outline">{currentAssetLabel}</Badge>
            </CardHeader>
            <CardContent>
              <div className="h-[320px]">
                {isBusy ? (
                  <Skeleton className="h-full w-full" />
                ) : (
                  <ReactECharts option={chartOption} style={{ height: '100%', width: '100%' }} />
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </section>

      <Tabs defaultValue="analysis">
        <TabsList>
          <TabsTrigger value="analysis">分析</TabsTrigger>
          <TabsTrigger value="prices">行情</TabsTrigger>
          <TabsTrigger value="valuation">估值</TabsTrigger>
          <TabsTrigger value="nav">净值</TabsTrigger>
        </TabsList>
        <TabsContent value="analysis">
          <Card>
            <CardContent className="pt-5">
              <pre className="max-h-[360px] overflow-auto rounded-md bg-muted p-4 text-xs leading-relaxed">
                {JSON.stringify(result ?? {}, null, 2)}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="prices">
          <StockHistoryTable rows={stockHistoryQuery.data?.items ?? []} />
        </TabsContent>
        <TabsContent value="valuation">
          <StockValuationTable rows={stockValuationQuery.data?.items ?? []} />
        </TabsContent>
        <TabsContent value="nav">
          <FundNavTable rows={fundNavQuery.data?.items ?? []} />
        </TabsContent>
      </Tabs>
    </main>
  )
}

function inferSubmittedAssetType(code: string, selectedAssetType: AssetType): AssetType {
  if (selectedAssetType !== 'stock' || !/^\d{6}$/.test(code)) {
    return selectedAssetType
  }
  const looksLikeAshare = /^(000|001|002|003|300|301|600|601|603|605|688|689)/.test(code)
  return looksLikeAshare ? 'stock' : 'fund'
}

function shouldSearchByKeyword(keyword: string) {
  const value = keyword.trim()
  return value.length >= 2 && !/^\d{6}$/.test(value)
}

function findBestSearchResult(keyword: string, items: AssetSearchResult[]) {
  const value = keyword.trim()
  return items.find((item) => item.code === value || item.name === value) ?? items[0]
}

function MetricGrid({ isBusy, result }: { isBusy: boolean; result: AnalysisResult | undefined }) {
  const price = result?.asset_type === 'stock' ? result.latest_price : result?.latest_unit_nav
  const valuation = result?.valuation
  const assetLabel = formatAssetLabel(result?.name, result?.code)
  const metrics = [
    {
      icon: <Activity className="size-4 text-primary" />,
      label: '资产',
      value: assetLabel,
    },
    {
      icon: <TrendingUp className="size-4 text-primary" />,
      label: result?.asset_type === 'stock' ? '最新价格' : '单位净值',
      value: formatNumber(price),
    },
    {
      icon: <Activity className="size-4 text-primary" />,
      label: '综合估值',
      value: valuation?.level_zh ?? '—',
    },
    {
      icon: <Activity className="size-4 text-primary" />,
      label: '估值分',
      value: formatNumber(valuation?.score, 1),
    },
    {
      icon: <Activity className="size-4 text-primary" />,
      label: '置信度',
      value: formatPercent(valuation?.confidence, 0),
    },
    {
      icon: <TrendingUp className="size-4 text-primary" />,
      label: '总收益',
      value: formatPercent(result?.performance.total_return),
    },
    {
      icon: <TrendingUp className="size-4 text-primary" />,
      label: '年化收益',
      value: formatPercent(result?.performance.annualized_return),
    },
    {
      icon: <TrendingDown className="size-4 text-destructive" />,
      label: '最大回撤',
      value: formatPercent(result?.performance.max_drawdown),
    },
    {
      icon: <Activity className="size-4 text-accent" />,
      label: 'PE 分位',
      value: formatRatioPercentile(valuation?.pe_ttm_percentile),
      visible: result?.asset_type !== 'fund',
    },
    {
      icon: <Activity className="size-4 text-accent" />,
      label: 'PB 分位',
      value: formatRatioPercentile(valuation?.pb_percentile),
      visible: result?.asset_type !== 'fund',
    },
  ].filter((item) => item.visible !== false)

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {metrics.map((item) => (
        <Card key={item.label}>
          <CardContent className="flex min-h-24 items-center gap-4 p-4">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
              {item.icon}
            </div>
            <div className="min-w-0">
              <div className="text-xs text-muted-foreground">{item.label}</div>
              {isBusy ? (
                <Skeleton className="mt-2 h-7 w-24" />
              ) : (
                <div className="mt-1 text-xl font-semibold sm:text-2xl">{item.value}</div>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
      <Card>
        <CardContent className="flex min-h-24 items-center gap-4 p-4">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
            <Activity className="size-4 text-primary" />
          </div>
          <div className="min-w-0">
            <div className="text-xs text-muted-foreground">估值策略</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <Badge variant="warning">{result?.valuation.profile_name ?? '—'}</Badge>
              <Badge variant="secondary">{formatLabel(result?.valuation.confidence_label)}</Badge>
              {result?.asset_type === 'stock' ? (
                <>
                  <Badge variant="outline">PE {formatLabel(result?.valuation.pe_ttm_label)}</Badge>
                  <Badge variant="outline">PB {formatLabel(result?.valuation.pb_label)}</Badge>
                </>
              ) : null}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function formatAssetLabel(name: string | null | undefined, code: string | null | undefined) {
  if (name && code) return `${name} (${code})`
  return code ?? '—'
}

function StockHistoryTable({ rows }: { rows: Array<{ date: string; close: number; volume: number }> }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>日期</TableHead>
              <TableHead>收盘</TableHead>
              <TableHead>成交量</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows
              .slice(-20)
              .reverse()
              .map((row) => (
                <TableRow key={row.date}>
                  <TableCell>{row.date}</TableCell>
                  <TableCell>{formatNumber(row.close)}</TableCell>
                  <TableCell>{formatNumber(row.volume, 0)}</TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function StockValuationTable({
  rows,
}: {
  rows: Array<{ date: string; pe_ttm: number | null; pb: number | null; ps_ttm: number | null }>
}) {
  return (
    <Card>
      <CardContent className="pt-5">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>日期</TableHead>
              <TableHead>PE TTM</TableHead>
              <TableHead>PB</TableHead>
              <TableHead>PS TTM</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows
              .slice(-20)
              .reverse()
              .map((row) => (
                <TableRow key={row.date}>
                  <TableCell>{row.date}</TableCell>
                  <TableCell>{formatNumber(row.pe_ttm)}</TableCell>
                  <TableCell>{formatNumber(row.pb)}</TableCell>
                  <TableCell>{formatNumber(row.ps_ttm)}</TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function FundNavTable({
  rows,
}: {
  rows: Array<{ date: string; unit_nav: number | null; cumulative_nav: number | null }>
}) {
  return (
    <Card>
      <CardContent className="pt-5">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>日期</TableHead>
              <TableHead>单位净值</TableHead>
              <TableHead>累计净值</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows
              .slice(-20)
              .reverse()
              .map((row) => (
                <TableRow key={row.date}>
                  <TableCell>{row.date}</TableCell>
                  <TableCell>{formatNumber(row.unit_nav, 4)}</TableCell>
                  <TableCell>{formatNumber(row.cumulative_nav, 4)}</TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
