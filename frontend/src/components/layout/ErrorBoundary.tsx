import { Component, type ErrorInfo, type ReactNode } from "react"
import { AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

/**
 * React render errors have no boundary anywhere in this app - one uncaught
 * exception in a page component (a malformed API payload, a bad access into
 * an array TanStack Table or React Flow doesn't expect) blanks the entire
 * console to white, including ApprovalBanner, which is the only in-app way
 * to clear a pending approval. Two boundaries, not one:
 *
 * - `variant="page"` wraps each routed page's content inside AppShell's
 *   `<Outlet />` (see AppShell.tsx) - a crash there loses only that page's
 *   content area. Nav, the health indicator, and ApprovalBanner (mounted
 *   above `<Outlet />`, outside this boundary) stay alive and usable.
 * - `variant="root"` wraps the whole app in main.tsx as a last-resort catch
 *   for anything above AppShell.
 *
 * A class component because getDerivedStateFromError/componentDidCatch have
 * no hook equivalent (as of React 19) - this is the one place in the app
 * that has to be one.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; variant?: "root" | "page" },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // No telemetry in this app (README: "no telemetry") - console only,
    // same as every other error path here.
    console.error("YBM console crashed:", error, info.componentStack)
  }

  private reset = () => this.setState({ error: null })

  render() {
    if (!this.state.error) return this.props.children

    const full = (this.props.variant ?? "page") === "root"
    return (
      <div className={full ? "flex min-h-svh items-center justify-center p-6" : "p-6"}>
        <Alert variant="destructive" className="max-w-lg">
          <AlertTriangle />
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription>
            <p>
              {full ? "The console hit an error it couldn't recover from." : "This page hit an error."}{" "}
              Nothing on your machine was changed by this - it's a display problem, not an action.
            </p>
            <p className="mt-2 font-mono text-xs break-all">{this.state.error.message}</p>
            <div className="mt-3 flex gap-2">
              <Button size="sm" variant="outline" onClick={this.reset}>
                Try again
              </Button>
              <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
                Reload
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      </div>
    )
  }
}
