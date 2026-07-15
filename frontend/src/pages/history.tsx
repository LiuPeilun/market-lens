import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowLeft, LogOut, MessageCircle, Search } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/lib/auth-context'
import {
  getAnalysisHistory,
  getChatMessageHistory,
  getChatSessionHistory,
} from '@/lib/api'
import { formatNumber, formatPercent } from '@/lib/format'

export function HistoryPage() {
  const { signOut, user } = useAuth()
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const analyses = useQuery({ queryFn: () => getAnalysisHistory(), queryKey: ['history-analyses'] })
  const sessions = useQuery({
    queryFn: () => getChatSessionHistory(),
    queryKey: ['history-chat-sessions'],
  })
  const messages = useQuery({
    enabled: Boolean(selectedSessionId),
    queryFn: () => getChatMessageHistory(selectedSessionId!),
    queryKey: ['history-chat-messages', selectedSessionId],
  })
  const error = analyses.error ?? sessions.error ?? messages.error

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-[1500px] flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Activity className="size-4" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">历史记录</h1>
            <div className="text-xs text-muted-foreground">{user?.email}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline">
            <Link to="/">
              <ArrowLeft className="size-4" />
              返回工作台
            </Link>
          </Button>
          <Button onClick={() => void signOut()} size="icon" title="退出登录" variant="ghost">
            <LogOut className="size-4" />
          </Button>
        </div>
      </header>

      {error ? (
        <Alert className="border-destructive/30">
          <AlertTitle>历史记录加载失败</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      ) : null}

      <Tabs defaultValue="analyses">
        <TabsList>
          <TabsTrigger value="analyses">分析记录</TabsTrigger>
          <TabsTrigger value="chats">对话记录</TabsTrigger>
        </TabsList>
        <TabsContent value="analyses">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Search className="size-4" />
                最近分析
              </CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              {analyses.isLoading ? (
                <Skeleton className="h-48 w-full" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>时间</TableHead>
                      <TableHead>标的</TableHead>
                      <TableHead>类型</TableHead>
                      <TableHead>估值</TableHead>
                      <TableHead>估值分</TableHead>
                      <TableHead>置信度</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(analyses.data?.items ?? []).map((item) => (
                      <TableRow key={item.id}>
                        <TableCell>{formatDateTime(item.created_at)}</TableCell>
                        <TableCell>
                          <div className="font-medium">{item.asset_name ?? item.asset_code}</div>
                          <div className="text-xs text-muted-foreground">{item.asset_code}</div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{item.asset_type === 'stock' ? '股票' : '基金'}</Badge>
                        </TableCell>
                        <TableCell>{item.result.valuation.level_zh ?? '—'}</TableCell>
                        <TableCell>{formatNumber(item.result.valuation.score, 1)}</TableCell>
                        <TableCell>{formatPercent(item.result.valuation.confidence, 0)}</TableCell>
                      </TableRow>
                    ))}
                    {!analyses.data?.items.length ? (
                      <TableRow>
                        <TableCell className="h-24 text-center text-muted-foreground" colSpan={6}>
                          还没有分析记录。
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="chats">
          <div className="grid gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <MessageCircle className="size-4" />
                  会话
                </CardTitle>
              </CardHeader>
              <CardContent className="grid max-h-[600px] gap-2 overflow-auto">
                {sessions.isLoading ? <Skeleton className="h-48 w-full" /> : null}
                {(sessions.data?.items ?? []).map((session) => (
                  <button
                    className={`w-full rounded-md border p-3 text-left hover:bg-muted ${
                      selectedSessionId === session.id ? 'border-primary bg-muted' : ''
                    }`}
                    key={session.id}
                    onClick={() => setSelectedSessionId(session.id)}
                    type="button"
                  >
                    <div className="truncate font-medium">{session.title}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {session.asset_name ?? session.asset_code ?? '未指定标的'} · {formatDateTime(session.updated_at)}
                    </div>
                  </button>
                ))}
                {!sessions.isLoading && !sessions.data?.items.length ? (
                  <div className="py-10 text-center text-sm text-muted-foreground">还没有对话记录。</div>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>消息</CardTitle>
              </CardHeader>
              <CardContent className="grid max-h-[600px] min-h-64 gap-3 overflow-auto">
                {!selectedSessionId ? (
                  <div className="grid place-items-center text-sm text-muted-foreground">选择左侧会话查看内容。</div>
                ) : null}
                {messages.isLoading ? <Skeleton className="h-48 w-full" /> : null}
                {(messages.data?.items ?? []).map((message) => (
                  <div
                    className={
                      message.role === 'user'
                        ? 'ml-8 rounded-md bg-primary px-3 py-2 text-primary-foreground'
                        : 'mr-8 rounded-md border bg-background px-3 py-2'
                    }
                    key={message.id}
                  >
                    <div className="whitespace-pre-wrap leading-relaxed">{message.content}</div>
                    <div className="mt-2 text-xs opacity-70">{formatDateTime(message.created_at)}</div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </main>
  )
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}
