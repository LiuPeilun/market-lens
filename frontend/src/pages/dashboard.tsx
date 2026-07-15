import { useEffect, useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'
import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import {
  Activity,
  Database,
  History as HistoryIcon,
  LogOut,
  MessageCircle,
  RotateCcw,
  Search,
  Send,
  Server,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { Link } from 'react-router-dom'

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
  streamChatWithAgent,
} from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatLabel, formatNumber, formatPercent, formatRatioPercentile } from '@/lib/format'

interface SubmittedQuery {
  assetType: AssetType
  code: string
  name?: string
  start: string
  end?: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: string[]
}

const defaultQuery: SubmittedQuery = {
  assetType: 'stock',
  code: '600519',
  start: '2018-01-01',
}

export function DashboardPage() {
  const { signOut, user } = useAuth()
  const [assetType, setAssetType] = useState<AssetType>(defaultQuery.assetType)
  const [code, setCode] = useState(defaultQuery.code)
  const [start, setStart] = useState(defaultQuery.start)
  const [end, setEnd] = useState('')
  const [submitted, setSubmitted] = useState<SubmittedQuery | null>(null)
  const [selectedAssetName, setSelectedAssetName] = useState<string | null>(null)
  const [debouncedKeyword, setDebouncedKeyword] = useState(defaultQuery.code)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isResolving, setIsResolving] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: '可以直接问我某只基金或股票的估值、收益、回撤和数据来源。',
    },
  ])
  const [isChatBusy, setIsChatBusy] = useState(false)
  const [chatAnalysis, setChatAnalysis] = useState<AnalysisResult | null>(null)
  const [chatSessionId, setChatSessionId] = useState<string | null>(null)
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
    enabled: Boolean(submitted?.code && submitted.start),
    queryFn: () =>
      analyzeAsset({
        asset_type: submitted!.assetType,
        code: submitted!.code,
        end: submitted!.end,
        start: submitted!.start,
      }),
    queryKey: ['analysis', submitted],
  })

  const stockHistoryQuery = useQuery({
    enabled: submitted?.assetType === 'stock',
    queryFn: () => getStockHistory(submitted!.code, submitted!.start, submitted!.end),
    queryKey: ['stock-history', submitted],
  })

  const stockValuationQuery = useQuery({
    enabled: submitted?.assetType === 'stock',
    queryFn: () => getStockValuation(submitted!.code),
    queryKey: ['stock-valuation', submitted?.code],
  })

  const fundNavQuery = useQuery({
    enabled: submitted?.assetType === 'fund',
    queryFn: () => getFundNav(submitted!.code, submitted!.start, submitted!.end),
    queryKey: ['fund-nav', submitted],
  })

  const chartOption = useMemo<EChartsOption>(() => {
    const submittedAssetType = submitted?.assetType ?? assetType
    const rows =
      submittedAssetType === 'stock'
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
          name: submittedAssetType === 'stock' ? 'close' : 'unit nav',
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
  }, [assetType, fundNavQuery.data?.items, stockHistoryQuery.data?.items, submitted?.assetType])

  const result = analysisQuery.data ?? chatAnalysis ?? undefined
  const currentAssetLabel = formatAssetLabel(
    result?.name ?? submitted?.name ?? selectedAssetName,
    result?.code ?? submitted?.code ?? code,
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
      setChatAnalysis(null)
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
    setChatAnalysis(null)
    setEnd('')
  }

  function selectAsset(candidate: AssetSearchResult, options?: { submit?: boolean }) {
    setAssetType(candidate.asset_type)
    setCode(candidate.code)
    setSelectedAssetName(candidate.name)
    setSubmitError(null)
    setChatAnalysis(null)
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

  async function submitChat(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = chatInput.trim()
    if (!message || isChatBusy) return

    setChatInput('')
    setIsChatBusy(true)
    const assistantMessageId = crypto.randomUUID()
    setChatMessages((items) => [
      ...items,
      { id: crypto.randomUUID(), role: 'user', content: message },
      { id: assistantMessageId, role: 'assistant', content: '' },
    ])
    try {
      await streamChatWithAgent(
        {
          context: result
            ? {
                asset_type: result.asset_type,
                code: result.code,
                name: result.name,
              }
            : submitted
              ? {
                  asset_type: submitted.assetType,
                  code: submitted.code,
                  name: submitted.name,
                }
              : null,
          end: end || undefined,
          message,
          session_id: chatSessionId,
          start,
        },
        (event) => {
          if (event.type === 'meta') {
            if (event.session_id) setChatSessionId(event.session_id)
            setChatMessages((items) =>
              items.map((item) =>
                item.id === assistantMessageId ? { ...item, citations: event.citations } : item,
              ),
            )
            if (event.asset && event.analysis) {
              setAssetType(event.asset.asset_type)
              setCode(event.asset.code)
              setSelectedAssetName(event.asset.name ?? event.analysis.name)
              setChatAnalysis(event.analysis)
              setSubmitted({
                assetType: event.asset.asset_type,
                code: event.asset.code,
                end: end || undefined,
                name: event.asset.name ?? event.analysis.name ?? undefined,
                start,
              })
            }
          } else if (event.type === 'token') {
            setChatMessages((items) =>
              items.map((item) =>
                item.id === assistantMessageId
                  ? { ...item, content: `${item.content}${event.delta}` }
                  : item,
              ),
            )
          } else if (event.type === 'error') {
            setChatMessages((items) =>
              items.map((item) =>
                item.id === assistantMessageId ? { ...item, content: event.message } : item,
              ),
            )
          }
        },
      )
    } catch (error) {
      setChatMessages((items) =>
        items.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                content: error instanceof Error ? error.message : '问答请求失败。',
              }
            : item,
        ),
      )
    } finally {
      setIsChatBusy(false)
    }
  }

  function startNewChat() {
    setChatSessionId(null)
    setChatMessages([
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '已开始新对话，可以询问另一只基金或股票。',
      },
    ])
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-[1760px] flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
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
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={healthQuery.data?.status === 'ok' ? 'secondary' : 'destructive'}>
            <Server className="mr-1 size-3" />
            API {healthQuery.data?.version ?? '—'}
          </Badge>
          <Badge variant="outline">
            <Database className="mr-1 size-3" />
            Eastmoney
          </Badge>
          <Badge variant={healthQuery.data?.supabase_configured ? 'secondary' : 'destructive'}>
            <Database className="mr-1 size-3" />
            Supabase
          </Badge>
          <Button asChild size="sm" variant="outline">
            <Link to="/history">
              <HistoryIcon className="size-4" />
              历史记录
            </Link>
          </Button>
          <span className="max-w-48 truncate text-xs text-muted-foreground">{user?.email}</span>
          <Button onClick={() => void signOut()} size="icon" title="退出登录" variant="ghost">
            <LogOut className="size-4" />
          </Button>
        </div>
      </header>

      <section className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
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
              <div className="h-[420px]">
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

      <ChatPanel
        input={chatInput}
        isBusy={isChatBusy}
        messages={chatMessages}
        onChangeInput={setChatInput}
        onNewChat={startNewChat}
        onSubmit={submitChat}
      />

      <Tabs defaultValue="analysis">
        <TabsList>
          <TabsTrigger value="analysis">分析</TabsTrigger>
          <TabsTrigger value="prices">行情</TabsTrigger>
          <TabsTrigger value="valuation">估值</TabsTrigger>
          <TabsTrigger value="holdings">持仓</TabsTrigger>
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
        <TabsContent value="holdings">
          <FundHoldingsTable result={result} />
        </TabsContent>
        <TabsContent value="nav">
          <FundNavTable rows={fundNavQuery.data?.items ?? []} />
        </TabsContent>
      </Tabs>
    </main>
  )
}

function ChatPanel({
  input,
  isBusy,
  messages,
  onChangeInput,
  onNewChat,
  onSubmit,
}: {
  input: string
  isBusy: boolean
  messages: ChatMessage[]
  onChangeInput: (value: string) => void
  onNewChat: () => void
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <MessageCircle className="size-4" /> 智能问答
        </CardTitle>
        <Button onClick={onNewChat} size="icon" title="开始新对话" variant="ghost">
          <RotateCcw className="size-4" />
        </Button>
      </CardHeader>
      <CardContent className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex max-h-[360px] min-h-[260px] flex-col gap-3 overflow-auto rounded-md border bg-muted/30 p-3">
          {messages.map((message) => (
            <div
              className={
                message.role === 'user'
                  ? 'ml-6 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground'
                  : 'mr-6 rounded-md bg-background px-3 py-2 text-sm shadow-sm'
              }
              key={message.id}
            >
              <div className="whitespace-pre-wrap leading-relaxed">{message.content}</div>
              {message.citations?.length ? (
                <div className="mt-2 grid gap-1 border-t pt-2 text-xs text-muted-foreground">
                  {message.citations.map((citation) => (
                    <div key={citation}>{citation}</div>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
          {isBusy ? <Skeleton className="h-16 w-4/5" /> : null}
        </div>
        <div className="grid content-between gap-4">
          <div className="rounded-md border bg-muted/20 p-4">
            <div className="text-sm font-medium">可直接提问</div>
            <div className="mt-2 grid gap-2 text-sm text-muted-foreground">
              <div>南方红利低波贵不贵？</div>
              <div>贵州茅台现在估值怎么样？</div>
              <div>这只基金最大回撤大吗？</div>
            </div>
          </div>
          <form className="flex gap-2" onSubmit={onSubmit}>
            <Input
              disabled={isBusy}
              placeholder="输入你的问题"
              value={input}
              onChange={(event) => onChangeInput(event.target.value)}
            />
            <Button disabled={isBusy || !input.trim()} size="icon" type="submit">
              <Send className="size-4" />
            </Button>
          </form>
        </div>
      </CardContent>
    </Card>
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
    {
      icon: <Activity className="size-4 text-accent" />,
      label: '持仓加权 PE',
      value: formatNumber(valuation?.portfolio?.metrics?.weighted_pe_ttm?.value),
      visible: result?.asset_type === 'fund',
    },
    {
      icon: <Activity className="size-4 text-accent" />,
      label: '持仓加权 PB',
      value: formatNumber(valuation?.portfolio?.metrics?.weighted_pb?.value),
      visible: result?.asset_type === 'fund',
    },
    {
      icon: <Database className="size-4 text-accent" />,
      label: '已分析持仓',
      value: formatPercent(valuation?.holdings?.analyzed_holdings_weight, 1),
      visible: result?.asset_type === 'fund',
    },
  ].filter((item) => item.visible !== false)

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
      {metrics.map((item) => (
        <Card key={item.label}>
          <CardContent className="flex min-h-24 items-center gap-4 p-4">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
              {item.icon}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs text-muted-foreground">{item.label}</div>
              {isBusy ? (
                <Skeleton className="mt-2 h-7 w-24" />
              ) : (
                <div className="mt-1 break-words text-xl font-semibold leading-tight 2xl:text-2xl">
                  {item.value}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
      <Card className="sm:col-span-2 xl:col-span-2">
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

function FundHoldingsTable({ result }: { result: AnalysisResult | undefined }) {
  const holdings = result?.valuation.holdings
  const rows = holdings?.items ?? []

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3">
        <CardTitle>前十大持仓增强</CardTitle>
        <div className="flex flex-wrap justify-end gap-2">
          <Badge variant="outline">报告期 {holdings?.report_date ?? '—'}</Badge>
          <Badge variant="secondary">
            已分析 {holdings?.analyzed_count ?? 0}/{holdings?.count ?? 0}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>序号</TableHead>
              <TableHead>股票</TableHead>
              <TableHead>权重</TableHead>
              <TableHead>行业</TableHead>
              <TableHead>PE TTM</TableHead>
              <TableHead>PB</TableHead>
              <TableHead>ROE</TableHead>
              <TableHead>利润增速</TableHead>
              <TableHead>股息率</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length ? (
              rows.map((row) => (
                <TableRow key={`${row.rank}-${row.code}`}>
                  <TableCell>{row.rank}</TableCell>
                  <TableCell>
                    <div className="min-w-32 font-medium">{row.name}</div>
                    <div className="text-xs text-muted-foreground">{row.code}</div>
                  </TableCell>
                  <TableCell>{formatPercent(row.weight_pct == null ? null : row.weight_pct / 100)}</TableCell>
                  <TableCell>{row.industry ?? '—'}</TableCell>
                  <TableCell>{formatNumber(row.pe_ttm)}</TableCell>
                  <TableCell>{formatNumber(row.pb)}</TableCell>
                  <TableCell>{formatPercent(row.roe_weighted == null ? null : row.roe_weighted / 100)}</TableCell>
                  <TableCell>
                    {formatPercent(
                      row.parent_netprofit_growth_pct == null
                        ? null
                        : row.parent_netprofit_growth_pct / 100,
                    )}
                  </TableCell>
                  <TableCell>{formatPercent(row.dividend_yield)}</TableCell>
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell className="h-24 text-center text-muted-foreground" colSpan={9}>
                  当前标的没有可用的股票持仓披露。
                </TableCell>
              </TableRow>
            )}
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
