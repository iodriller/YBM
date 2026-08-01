import { useState } from "react"
import { NavLink, Outlet } from "react-router-dom"
import { Bot, BrainCircuit, ListTree, MessageSquare, Settings, ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"
import { HealthIndicator } from "@/components/layout/HealthIndicator"
import { ApprovalBanner } from "@/components/approvals/ApprovalBanner"
import { SafetyTourBanner } from "@/components/layout/SafetyTourBanner"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { AdvancedModeContext, readAdvancedMode, writeAdvancedMode } from "@/lib/advanced-mode"
import { ThemeToggle } from "@/components/layout/ThemeToggle"

// Matches docs/UI_REWRITE_PLAN.md §10's table, plus Memory
// (docs/UI_UX_AUDIT.md Phase 4) - Chat is the landing page/first route on
// purpose (plan: "a first-time user must get an answer without visiting
// any other screen").
const NAV_ITEMS = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/tasks", label: "Tasks", icon: ListTree, end: false },
  { to: "/access", label: "Access", icon: ShieldCheck, end: false },
  { to: "/memory", label: "Memory", icon: BrainCircuit, end: false },
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
      <div className="flex h-svh w-full flex-col overflow-hidden bg-background text-foreground">
        {/* Full-width, above both nav and content - "persistent, unmissable,
            on every route" (docs/UI_REWRITE_PLAN.md §11.1). Renders nothing
            when there are no pending approvals. */}
        <ApprovalBanner />
        <SafetyTourBanner />
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-sidebar-border bg-sidebar px-4 md:hidden">
          <Brand />
          <ThemeToggle />
        </header>
        <div className="flex min-h-0 flex-1">
          <nav className="hidden w-64 shrink-0 flex-col justify-between border-r border-sidebar-border bg-sidebar p-4 md:flex">
            <div>
              <div className="mb-7 px-2">
                <Brand />
              </div>
              <ul className="space-y-1.5">
                {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      end={end}
                      className={({ isActive }) =>
                        cn(
                          "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
                          isActive
                            ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm"
                            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
                        )
                      }
                    >
                      <Icon className="size-4.5 transition-transform group-hover:scale-105" />
                      {label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-col gap-3">
              {/* One switch, reveals Level 2 everywhere (docs/UI_REWRITE_PLAN.md
                  §6) - not a per-panel disclosure to hunt for on each screen. */}
              <div className="flex items-center justify-between gap-2 rounded-xl px-2 py-1">
                <Label htmlFor="advanced-mode" className="text-xs text-muted-foreground">
                  Advanced mode
                </Label>
                <Switch id="advanced-mode" checked={advanced} onCheckedChange={setAdvanced} />
              </div>
              <HealthIndicator />
              <div className="flex items-center justify-between border-t border-sidebar-border pt-2 pl-2">
                <span className="text-xs text-muted-foreground">Appearance</span>
                <ThemeToggle />
              </div>
            </div>
          </nav>
          <main className="min-w-0 flex-1 overflow-hidden pb-17 md:pb-0">
            <Outlet />
          </main>
        </div>
        <nav className="fixed inset-x-0 bottom-0 z-40 grid h-17 grid-cols-5 border-t border-sidebar-border bg-sidebar/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex min-w-0 flex-col items-center justify-center gap-1 rounded-lg text-[11px] font-medium transition-colors",
                  isActive ? "text-primary" : "text-muted-foreground hover:text-foreground",
                )
              }
            >
              <Icon className="size-5" />
              <span className="truncate">{label}</span>
            </NavLink>
          ))}
        </nav>
      </div>
    </AdvancedModeContext.Provider>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex size-8 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm shadow-primary/25">
        <Bot className="size-4.5" />
      </span>
      <span className="text-base font-semibold tracking-tight text-sidebar-foreground">YBM Control</span>
    </div>
  )
}
