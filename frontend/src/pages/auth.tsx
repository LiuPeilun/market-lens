import { Activity, LockKeyhole } from 'lucide-react'
import { useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/lib/auth-context'

type AuthMode = 'sign-in' | 'sign-up'

export function AuthPage() {
  const { signIn, signUp } = useAuth()
  const [mode, setMode] = useState<AuthMode>('sign-in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isBusy, setIsBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setMessage(null)
    setIsBusy(true)
    try {
      if (mode === 'sign-in') {
        await signIn(email.trim(), password)
      } else {
        const result = await signUp(email.trim(), password)
        if (result.confirmationRequired) {
          setMessage('注册成功，请在邮箱中确认账号后登录。')
          setMode('sign-in')
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '认证请求失败。')
    } finally {
      setIsBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Activity className="size-5" />
          </div>
          <div>
            <div className="text-xl font-semibold">Market Lens</div>
            <div className="text-sm text-muted-foreground">投研工作台</div>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <LockKeyhole className="size-4" />
              {mode === 'sign-in' ? '登录' : '创建账号'}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-5 grid grid-cols-2 rounded-md bg-muted p-1">
              <Button
                onClick={() => setMode('sign-in')}
                size="sm"
                type="button"
                variant={mode === 'sign-in' ? 'secondary' : 'ghost'}
              >
                登录
              </Button>
              <Button
                onClick={() => setMode('sign-up')}
                size="sm"
                type="button"
                variant={mode === 'sign-up' ? 'secondary' : 'ghost'}
              >
                注册
              </Button>
            </div>

            {error ? (
              <Alert className="mb-4 border-destructive/30">
                <AlertTitle>认证失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            {message ? (
              <Alert className="mb-4">
                <AlertTitle>检查邮箱</AlertTitle>
                <AlertDescription>{message}</AlertDescription>
              </Alert>
            ) : null}

            <form className="grid gap-4" onSubmit={submit}>
              <div className="grid gap-2">
                <Label htmlFor="auth-email">邮箱</Label>
                <Input
                  autoComplete="email"
                  id="auth-email"
                  onChange={(event) => setEmail(event.target.value)}
                  required
                  type="email"
                  value={email}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="auth-password">密码</Label>
                <Input
                  autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
                  id="auth-password"
                  minLength={6}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </div>
              <Button disabled={isBusy} type="submit">
                {isBusy ? '处理中' : mode === 'sign-in' ? '登录' : '注册'}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  )
}
