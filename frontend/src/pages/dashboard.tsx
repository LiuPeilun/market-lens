import { useEffect, useMemo, useState } from 'react'

import { useQuery } from '@tanstack/react-query'
import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import {
  Activity,
  Check,
  CheckCircle2,
  Clock3,
  Database,
  Gauge,
  History as HistoryIcon,
  Layers3,
  LogOut,
  MessageCircle,
  PackageCheck,
  LoaderCircle,
  RotateCcw,
  Search,
  Send,
  Server,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  X,
  XCircle,
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
  type AssessmentDimension,
  type AnalysisResult,
  type ChatProgressStep,
  type ChatStreamEvent,
  type ToolApproval,
  analyzeAsset,
  getFundNav,
  getHealth,
  getStockHistory,
  getStockValuation,
  searchAssets,
  resumeToolApproval,
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
  requestId: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: string[]
  approval?: ToolApproval & {
    uiStatus?: 'pending' | 'resolving' | 'approved' | 'denied' | 'failed'
  }
  progress?: ChatProgressStep[]
  streamDone?: boolean
}

const defaultQuery: SubmittedQuery = {
  assetType: 'stock',
  code: '600519',
  requestId: 'default',
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
  const [isComposing, setIsComposing] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isResolving, setIsResolving] = useState(false)
  const [fundChartMode, setFundChartMode] = useState<'performance' | 'nav'>('performance')
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
    if (isComposing) return
    const timer = window.setTimeout(() => setDebouncedKeyword(trimmedCode), 250)
    return () => window.clearTimeout(timer)
  }, [isComposing, trimmedCode])

  const healthQuery = useQuery({
    queryFn: getHealth,
    queryKey: ['health'],
  })

  const searchQuery = useQuery({
    enabled: !isComposing && shouldSearchByKeyword(debouncedKeyword),
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
    queryKey: ['stock-valuation', submitted?.code, submitted?.requestId],
  })

  const fundNavQuery = useQuery({
    enabled: submitted?.assetType === 'fund',
    queryFn: () => getFundNav(submitted!.code, submitted!.start, submitted!.end),
    queryKey: ['fund-nav', submitted],
  })

  const chartOption = useMemo<EChartsOption>(() => {
    const submittedAssetType = submitted?.assetType ?? assetType
    const showFundPerformance =
      submittedAssetType === 'fund' && fundChartMode === 'performance'
    const rows =
      submittedAssetType === 'stock'
        ? (stockHistoryQuery.data?.items ?? []).map((item) => [item.date, item.close])
        : (fundNavQuery.data?.items ?? []).map((item) => [
            item.date,
            showFundPerformance && item.cumulative_return !== null
              ? item.cumulative_return * 100
              : item.unit_nav,
          ])
    const seriesName =
      submittedAssetType === 'stock'
        ? '收盘价'
        : showFundPerformance
          ? '累计收益 (%)'
          : '单位净值'

    return {
      animationDuration: 300,
      color: ['#0f766e'],
      grid: { bottom: 42, left: 48, right: 24, top: 24 },
      series: [
        {
          areaStyle: { color: 'rgba(15, 118, 110, 0.08)' },
          data: rows,
          name: seriesName,
          showSymbol: false,
          smooth: false,
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
        axisLabel: showFundPerformance ? { formatter: '{value}%' } : undefined,
        scale: true,
        type: 'value',
      },
    }
  }, [
    assetType,
    fundChartMode,
    fundNavQuery.data?.items,
    stockHistoryQuery.data?.items,
    submitted?.assetType,
  ])

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
        requestId: crypto.randomUUID(),
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
        requestId: crypto.randomUUID(),
        start,
      })
    }
  }

  function handleChatStreamEvent(event: ChatStreamEvent, assistantMessageId: string) {
    if (event.type === 'progress') {
      const step: ChatProgressStep = {
        id: event.id,
        stage: event.stage,
        status: event.status,
        title: event.title,
        detail: event.detail,
        tool_name: event.tool_name,
      }
      setChatMessages((items) =>
        items.map((item) =>
          item.id === assistantMessageId
            ? { ...item, progress: mergeProgressStep(item.progress, step) }
            : item,
        ),
      )
    } else if (event.type === 'meta') {
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
          requestId: crypto.randomUUID(),
          start,
        })
      }
    } else if (event.type === 'citations') {
      setChatMessages((items) =>
        items.map((item) =>
          item.id === assistantMessageId ? { ...item, citations: event.citations } : item,
        ),
      )
    } else if (event.type === 'approval_required') {
      setChatSessionId(event.session_id)
      setChatMessages((items) =>
        items.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                approval: { ...event.approval, uiStatus: 'pending' },
                citations: event.citations,
              }
            : item,
        ),
      )
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
          item.id === assistantMessageId
            ? {
                ...item,
                content: event.message,
                progress: finishProgressSteps(item.progress, 'failed'),
                streamDone: true,
              }
            : item,
        ),
      )
    } else if (event.type === 'done') {
      if (event.session_id) setChatSessionId(event.session_id)
      setChatMessages((items) =>
        items.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                progress: finishProgressSteps(item.progress, 'completed'),
                streamDone: true,
              }
            : item,
        ),
      )
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
      {
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        progress: [
          {
            id: 'resolve_asset',
            stage: 'resolving',
            status: 'running',
            title: '正在连接智能体',
          },
        ],
        streamDone: false,
      },
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
        (event) => handleChatStreamEvent(event, assistantMessageId),
      )
    } catch (error) {
      setChatMessages((items) =>
        items.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                content: error instanceof Error ? error.message : '问答请求失败。',
                progress: finishProgressSteps(item.progress, 'failed'),
                streamDone: true,
              }
            : item,
        ),
      )
    } finally {
      setIsChatBusy(false)
    }
  }

  async function resolveToolApproval(
    messageId: string,
    approval: ToolApproval,
    decision: 'approve' | 'deny',
  ) {
    if (isChatBusy) return
    setIsChatBusy(true)
    setChatMessages((items) =>
      items.map((item) =>
        item.id === messageId && item.approval?.id === approval.id
          ? { ...item, approval: { ...item.approval, uiStatus: 'resolving' } }
          : item,
      ),
    )
    try {
      let streamFailed = false
      await resumeToolApproval(approval.id, decision, (event) => {
        if (event.type === 'error') streamFailed = true
        handleChatStreamEvent(event, messageId)
      })
      setChatMessages((items) =>
        items.map((item) =>
          item.id === messageId && item.approval?.id === approval.id
            ? {
                ...item,
                approval: {
                  ...item.approval,
                  status: streamFailed ? 'failed' : decision === 'approve' ? 'approved' : 'denied',
                  uiStatus: streamFailed
                    ? 'failed'
                    : decision === 'approve'
                      ? 'approved'
                      : 'denied',
                },
              }
            : item,
        ),
      )
    } catch (error) {
      setChatMessages((items) =>
        items.map((item) =>
          item.id === messageId && item.approval?.id === approval.id
            ? {
                ...item,
                approval: { ...item.approval, uiStatus: 'pending' },
                content: error instanceof Error ? error.message : '审批请求失败。',
                progress: finishProgressSteps(item.progress, 'failed'),
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
                  onCompositionEnd={(event) => {
                    setCode(event.currentTarget.value)
                    setIsComposing(false)
                  }}
                  onCompositionStart={() => setIsComposing(true)}
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
                {!isComposing && shouldSearchByKeyword(trimmedCode) ? (
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
          <AssessmentOverview isBusy={isBusy} result={result} />

          <Card>
            <CardHeader className="flex-row items-center justify-between gap-3 max-sm:flex-col max-sm:items-start">
              <CardTitle>
                {(submitted?.assetType ?? assetType) === 'stock'
                  ? '价格走势'
                  : fundChartMode === 'performance'
                    ? '业绩走势'
                    : '单位净值走势'}
              </CardTitle>
              <div className="flex max-w-full flex-wrap items-center justify-end gap-3 max-sm:justify-start">
                {(submitted?.assetType ?? assetType) === 'fund' ? (
                  <Tabs
                    onValueChange={(value) =>
                      setFundChartMode(value as 'performance' | 'nav')
                    }
                    value={fundChartMode}
                  >
                    <TabsList>
                      <TabsTrigger value="performance">累计收益</TabsTrigger>
                      <TabsTrigger value="nav">单位净值</TabsTrigger>
                    </TabsList>
                  </Tabs>
                ) : null}
                <Badge className="max-w-full truncate" variant="outline">
                  {currentAssetLabel}
                </Badge>
              </div>
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
        onResolveApproval={resolveToolApproval}
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

function mergeProgressStep(
  current: ChatProgressStep[] | undefined,
  next: ChatProgressStep,
): ChatProgressStep[] {
  const steps = current ?? []
  const existingIndex = steps.findIndex((step) => step.id === next.id)
  if (existingIndex < 0) return [...steps, next]
  return steps.map((step, index) => (index === existingIndex ? next : step))
}

function finishProgressSteps(
  current: ChatProgressStep[] | undefined,
  status: 'completed' | 'failed',
): ChatProgressStep[] | undefined {
  return current?.map((step) => (step.status === 'running' ? { ...step, status } : step))
}

function ChatProgress({ steps }: { steps: ChatProgressStep[] }) {
  return (
    <div aria-label="任务处理进度" className="grid gap-2">
      {steps.map((step) => (
        <div className="flex min-w-0 items-start gap-2" key={step.id}>
          <ProgressStatusIcon status={step.status} />
          <div className="min-w-0 flex-1">
            <div
              className={
                step.status === 'running'
                  ? 'font-medium text-foreground'
                  : step.status === 'failed'
                    ? 'font-medium text-destructive'
                    : 'text-muted-foreground'
              }
            >
              {step.title}
            </div>
            {step.detail ? (
              <div className="mt-0.5 break-words text-xs text-muted-foreground">{step.detail}</div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  )
}

function ProgressStatusIcon({ status }: { status: ChatProgressStep['status'] }) {
  if (status === 'running') {
    return <LoaderCircle className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
  }
  if (status === 'completed') {
    return <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
  }
  if (status === 'waiting_approval') {
    return <Clock3 className="mt-0.5 size-4 shrink-0 text-amber-600" />
  }
  return <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
}

function ChatPanel({
  input,
  isBusy,
  messages,
  onChangeInput,
  onNewChat,
  onResolveApproval,
  onSubmit,
}: {
  input: string
  isBusy: boolean
  messages: ChatMessage[]
  onChangeInput: (value: string) => void
  onNewChat: () => void
  onResolveApproval: (
    messageId: string,
    approval: ToolApproval,
    decision: 'approve' | 'deny',
  ) => void
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
              {message.role === 'assistant' && message.progress?.length ? (
                <ChatProgress steps={message.progress} />
              ) : null}
              {message.content ? (
                <div
                  className={
                    message.progress?.length
                      ? 'mt-3 whitespace-pre-wrap border-t pt-3 leading-relaxed'
                      : 'whitespace-pre-wrap leading-relaxed'
                  }
                >
                  {message.content}
                </div>
              ) : null}
              {message.approval ? (
                <div className="mt-3 grid gap-3 border-t pt-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <ShieldAlert className="size-4 text-amber-600" />
                    <span className="font-medium">工具执行审批</span>
                    <Badge variant="outline">{formatRiskLevel(message.approval.risk_level)}</Badge>
                    <Badge variant="secondary">
                      {formatExecutionTarget(message.approval.execution_target)}
                    </Badge>
                  </div>
                  <div className="grid gap-1 text-xs text-muted-foreground">
                    <div className="font-mono text-foreground">{message.approval.tool_name}</div>
                    <div>{message.approval.reason}</div>
                  </div>
                  <pre className="max-h-36 overflow-auto rounded bg-muted p-2 text-xs leading-relaxed">
                    {JSON.stringify(message.approval.input_summary, null, 2)}
                  </pre>
                  {message.approval.uiStatus === 'pending' ? (
                    <div className="flex justify-end gap-2">
                      <Button
                        disabled={isBusy}
                        onClick={() => onResolveApproval(message.id, message.approval!, 'deny')}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        <X className="size-4" /> 拒绝
                      </Button>
                      <Button
                        disabled={isBusy}
                        onClick={() => onResolveApproval(message.id, message.approval!, 'approve')}
                        size="sm"
                        type="button"
                      >
                        <Check className="size-4" /> 批准一次
                      </Button>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      {message.approval.uiStatus === 'resolving'
                        ? '正在处理审批…'
                        : message.approval.uiStatus === 'failed'
                          ? '本次调用执行失败'
                        : message.approval.uiStatus === 'approved'
                          ? '已批准本次调用'
                          : '已拒绝本次调用'}
                    </div>
                  )}
                </div>
              ) : null}
              {message.citations?.length ? (
                <div className="mt-2 grid gap-1 border-t pt-2 text-xs text-muted-foreground">
                  {message.citations.map((citation) => (
                    <div key={citation}>{citation}</div>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
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

function formatRiskLevel(risk: ToolApproval['risk_level']) {
  const labels: Record<ToolApproval['risk_level'], string> = {
    compute: '计算',
    destructive: '破坏性',
    external_side_effect: '外部副作用',
    read: '读取',
    write: '写入/执行',
  }
  return labels[risk]
}

function formatExecutionTarget(target: ToolApproval['execution_target']) {
  const labels: Record<ToolApproval['execution_target'], string> = {
    remote_mcp: '远程 MCP',
    sandbox_required: '隔离沙箱',
    trusted_local: '本地可信进程',
  }
  return labels[target]
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
              <Badge variant="warning">
                {result?.assessment?.profile ?? result?.valuation.profile_name ?? '—'}
              </Badge>
              <Badge variant="secondary">
                {result?.assessment?.model_version ?? formatLabel(result?.valuation.confidence_label)}
              </Badge>
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

function AssessmentOverview({
  isBusy,
  result,
}: {
  isBusy: boolean
  result: AnalysisResult | undefined
}) {
  const assessment = resolveAssessment(result)
  const qualityLabel = result?.asset_type === 'fund' ? '底层资产质量' : '基本面质量'
  const dimensions = [
    {
      dimension: assessment.valuation,
      icon: <Gauge className="size-4 text-primary" />,
      label: '估值位置',
    },
    {
      dimension: assessment.quality,
      icon: <Layers3 className="size-4 text-emerald-600" />,
      label: qualityLabel,
    },
    ...(assessment.product
      ? [
          {
            dimension: assessment.product,
            icon: <PackageCheck className="size-4 text-amber-600" />,
            label: '基金产品质量',
          },
        ]
      : []),
  ]

  return (
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {dimensions.map(({ dimension, icon, label }) => (
        <Card key={label}>
          <CardContent className="min-h-40 p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                {icon}
                {label}
              </div>
              <Badge variant="outline">{dimension?.level_zh ?? '未知'}</Badge>
            </div>
            {isBusy ? (
              <Skeleton className="mt-5 h-10 w-28" />
            ) : (
              <div className="mt-4 text-3xl font-semibold">
                {formatNumber(dimension?.score, 1)}
              </div>
            )}
            <div className="mt-4 grid grid-cols-2 gap-3 text-xs text-muted-foreground">
              <div>
                <div>维度置信度</div>
                <div className="mt-1 text-sm font-medium text-foreground">
                  {formatPercent(dimension?.confidence, 0)}
                </div>
              </div>
              <div>
                <div>因子覆盖</div>
                <div className="mt-1 text-sm font-medium text-foreground">
                  {formatPercent(dimension?.weight_coverage, 0)}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
      <Card>
        <CardContent className="min-h-40 p-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck className="size-4 text-sky-600" />
            总体置信度
          </div>
          {isBusy ? (
            <Skeleton className="mt-5 h-10 w-28" />
          ) : (
            <div className="mt-4 text-3xl font-semibold">
              {formatPercent(assessment.overallConfidence, 0)}
            </div>
          )}
          <div className="mt-4 text-xs leading-relaxed text-muted-foreground">
            按各有效评分维度保守聚合
            {result?.assessment?.analysis_as_of ? ` · ${result.assessment.analysis_as_of}` : ''}
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

function resolveAssessment(result: AnalysisResult | undefined): {
  valuation: AssessmentDimension | null
  quality: AssessmentDimension | null
  product: AssessmentDimension | null
  overallConfidence: number | null
} {
  if (result?.assessment) {
    return {
      ...result.assessment.dimensions,
      overallConfidence: result.assessment.overall_confidence,
    }
  }
  if (!result) {
    return { overallConfidence: null, product: null, quality: null, valuation: null }
  }
  return {
    valuation: {
      confidence: result.valuation.confidence ?? 0,
      data_coverage: result.valuation.factor_coverage ?? 0,
      factors: [],
      level: result.valuation.level ?? 'unknown',
      level_zh: result.valuation.level_zh ?? '未知',
      model: result.valuation.method ?? 'legacy',
      sample_adequacy: 0,
      score: result.valuation.score ?? null,
      warnings: [],
      weight_coverage: result.valuation.factor_coverage ?? 0,
    },
    quality: null,
    product: null,
    overallConfidence: result.valuation.confidence ?? null,
  }
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
  const route = result?.holdings_route ?? result?.valuation.holdings_route
  const rows = holdings?.items ?? []
  const title = holdingsScopeLabel(route?.scope)

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <CardTitle>{title}</CardTitle>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            <Badge variant="outline">来源 {holdingsSourceLabel(route?.source)}</Badge>
            <Badge variant="outline">数据日期 {route?.as_of ?? holdings?.report_date ?? '—'}</Badge>
            <Badge variant="outline">覆盖率 {formatPercent(route?.coverage, 1)}</Badge>
            <Badge variant="secondary">
              已分析 {holdings?.analyzed_count ?? 0}/{holdings?.count ?? 0}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {route?.tracked_index_code ? (
            <span>
              跟踪指数 {route.tracked_index_name ?? '—'} ({route.tracked_index_code})
            </span>
          ) : null}
          {route?.target_etf_code ? (
            <span>
              目标 ETF {route.target_etf_name ?? '—'} ({route.target_etf_code})
            </span>
          ) : null}
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

function holdingsScopeLabel(scope: string | undefined) {
  if (scope === 'tracked_index_top10') return '跟踪指数前十大成分'
  if (scope === 'target_etf_top10') return '目标 ETF 前十大持仓'
  if (scope === 'fund_direct_top10') return '基金直接前十大持仓'
  return '持仓数据'
}

function holdingsSourceLabel(source: string | undefined) {
  if (source === 'csindex_official') return '中证指数官方'
  if (source === 'eastmoney_fund_disclosure') return '东方财富基金披露'
  if (source === 'unavailable') return '不可用'
  return source ?? '—'
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
