import { lazy, Suspense, useState } from "react"
import { Route, Routes } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { ChatPage } from "@/pages/ChatPage"
import { useBootstrap } from "@/lib/queries"

// Chat is the landing page and should stay light (docs/UI_REWRITE_PLAN.md
// §10: "a first-time user must get an answer without visiting any other
// screen") - lazy-loading Tasks/Trace/Access/Settings/the wizard keeps
// TanStack Table and React Flow (the two heaviest deps this rewrite
// added, and the reason the bundle crossed Vite's 500kB warning
// threshold) out of that first paint.
const TasksPage = lazy(() => import("@/pages/TasksPage").then((m) => ({ default: m.TasksPage })))
const TaskTracePage = lazy(() =>
  import("@/pages/TaskTracePage").then((m) => ({ default: m.TaskTracePage })),
)
const AccessPage = lazy(() => import("@/pages/AccessPage").then((m) => ({ default: m.AccessPage })))
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
)
const OnboardingWizard = lazy(() =>
  import("@/components/onboarding/OnboardingWizard").then((m) => ({ default: m.OnboardingWizard })),
)

// Route table matches docs/UI_REWRITE_PLAN.md §10's page table exactly.
function App() {
  const { data: bootstrap, isPending } = useBootstrap()
  const [manualWizard, setManualWizard] = useState(false)

  // Bootstrap is the one request that must resolve before any real paint
  // (docs/UI_REWRITE_PLAN.md §9 Phase 0.3) - it decides wizard vs console.
  if (isPending) return null

  const showWizard = manualWizard || (bootstrap ? !bootstrap.onboarding_complete : false)
  if (showWizard) {
    return (
      <Suspense fallback={<PageFallback />}>
        <OnboardingWizard onDone={() => setManualWizard(false)} />
      </Suspense>
    )
  }

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<ChatPage />} />
        <Route
          path="tasks"
          element={
            <Suspense fallback={<PageFallback />}>
              <TasksPage />
            </Suspense>
          }
        />
        <Route
          path="tasks/:taskId"
          element={
            <Suspense fallback={<PageFallback />}>
              <TaskTracePage />
            </Suspense>
          }
        />
        <Route
          path="access"
          element={
            <Suspense fallback={<PageFallback />}>
              <AccessPage />
            </Suspense>
          }
        />
        <Route
          path="settings"
          element={
            <Suspense fallback={<PageFallback />}>
              <SettingsPage onRerunWizard={() => setManualWizard(true)} />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  )
}

function PageFallback() {
  return <div className="p-6 text-sm text-muted-foreground">Loading...</div>
}

export default App
