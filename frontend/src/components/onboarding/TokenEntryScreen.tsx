import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { ApiError, getSummary, setAdminToken } from "@/lib/api"
import { ThemeToggle } from "@/components/layout/ThemeToggle"

/**
 * Shown when `bootstrap.token_required` is true and no admin token is
 * available yet (not carried in the URL by a same-machine `ybm start`
 * auto-open, and not left over in sessionStorage from an earlier visit).
 *
 * A real, previously-missing gap: `run_setup()` always auto-generates
 * AGENT_ADMIN_TOKEN, `require_admin()` enforces it on every /api/* route
 * except bootstrap, and nothing anywhere in this app ever called
 * `setAdminToken()` - meaning every fresh install with a real token would
 * 401 on every page with no way to recover from the browser. This screen,
 * plus the ?token= URL capture in App.tsx for the auto-opened first launch,
 * closes that.
 */
export function TokenEntryScreen({ onVerified }: { onVerified: () => void }) {
  const [token, setToken] = useState("")
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = token.trim()
    if (!trimmed) return
    setChecking(true)
    setError(null)
    setAdminToken(trimmed)
    try {
      await getSummary(1)
      onVerified()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That token isn't right." : "Could not reach the backend.")
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="relative flex h-svh w-full items-center justify-center bg-background p-6">
      <div className="absolute top-4 right-4"><ThemeToggle /></div>
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Admin token required</CardTitle>
          <CardDescription>
            This instance has an admin token set for defense in depth. Find it in the repo's{" "}
            <code className="rounded bg-muted px-1 py-0.5">.env</code> file as{" "}
            <code className="rounded bg-muted px-1 py-0.5">AGENT_ADMIN_TOKEN</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
            <div className="flex flex-col gap-1">
              <Label htmlFor="admin-token" className="text-xs text-muted-foreground">
                Admin token
              </Label>
              <Input
                id="admin-token"
                type="password"
                autoFocus
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertTitle>Couldn&apos;t verify that token</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" disabled={checking || !token.trim()}>
              {checking ? "Checking..." : "Continue"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
