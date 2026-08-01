import { useState } from "react"
import { NavLink, Outlet } from "react-router-dom"
import { MessageSquare, ListTree, ShieldCheck, Settings } from "lucide-react"
import { cn } from "@/lib/utils"
import { HealthIndicator } from "@/components/layout/HealthIndicator"
import { ApprovalBanner } from "@/components/approvals/ApprovalBanner"
import { SafetyTourBanner } from "@/components/layout/SafetyTourBanner"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { AdvancedModeContext, readAdvancedMode, writeAdvancedMode } from "@/lib/advanced-mode"

// Four routes, matching docs/UI_REWRITE_PLAN.md §10's table exactly - Chat
// is the landing page/first route on purpose (plan: "a first-time user
// must get an answer without visiting any other screen").
const NAV_ITEMS = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/tasks", label: "Tasks", icon: ListTree, end: false },
  { to: "/access", label: "Access", icon: ShieldCheck, end: false },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
] as const

export function AppShell() {
  const [advanced, setAdvancedState] = useState(readAdvancedMode)
  const setAdvanced = (value: boolean) => {
    setAdvancedState(value)
    writeAdvancedMode(value)
  }

  return (
    <AdvancedModeContext.Provider value={{ advanced, setAdvanced }}>
      <div className="flex h-svh w-full flex-col bg-background text-foreground">
        {/* Full-width, above both nav and content - "persistent, unmissable,
            on every route" (docs/UI_REWRITE_PLAN.md §11.1). Renders nothing
            when there are no pending approvals. */}
        <ApprovalBanner />
        <SafetyTourBanner />
        <div className="flex min-h-0 flex-1">
          <nav className="flex w-56 shrink-0 flex-col justify-between border-r border-border p-4">
            <div>
              <div className="mb-6 px-2">
                <h1 className="text-lg font-semibold tracking-tight">YBM</h1>
                <p className="text-xs text-muted-foreground">Agent Control</p>
              </div>
              <ul className="space-y-1">
                {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      end={end}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                          isActive
                            ? "bg-secondary text-secondary-foreground"
                            : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
                        )
                      }
                    >
                      <Icon className="size-4" />
                      {label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-col gap-3">
              {/* One switch, reveals Level 2 everywhere (docs/UI_REWRITE_PLAN.md
                  §6) - not a per-panel disclosure to hunt for on each screen. */}
              <div className="flex items-center justify-between gap-2 px-2">
                <Label htmlFor="advanced-mode" className="text-xs text-muted-foreground">
                  Advanced mode
                </Label>
                <Switch id="advanced-mode" checked={advanced} onCheckedChange={setAdvanced} />
              </div>
              <HealthIndicator />
            </div>
          </nav>
          <main className="min-w-0 flex-1 overflow-hidden">
            <Outlet />
          </main>
        </div>
      </div>
    </AdvancedModeContext.Provider>
  )
}
