import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { BrowserRouter } from "react-router"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import { ThemeProvider } from "next-themes"
import { ErrorBoundary } from "@/components/layout/ErrorBoundary"
import "./index.css"
import App from "./App.tsx"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Fail fast and visibly rather than retrying a broken connection
      // silently for a local, single-operator console.
      retry: 1,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        {/* Base UI's prop is "delay", not Radix's "delayDuration" - this
            shadcn generation is built on @base-ui/react, not Radix. */}
        <TooltipProvider delay={200}>
          {/* Vite's base="/admin/" (vite.config.ts) only rewrites asset URLs,
            not the router - basename must be passed explicitly or route
            matching breaks against the real mounted URL. import.meta.env.BASE_URL
            is Vite's own runtime reflection of that same base config, so the
            two can never drift out of sync - but it carries Vite's trailing
            slash, which React Router's basename must NOT have: it matches by
            exact string prefix, so basename="/admin/" fails to match the
            literal URL "/admin" (no trailing slash - the exact address every
            banner/README/doctor output in this repo prints) and silently
            renders nothing. Confirmed live via Playwright: that mismatch was
            a real blank-page bug on first load, not a hypothetical. */}
          <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
            <ErrorBoundary variant="root">
              <App />
            </ErrorBoundary>
          </BrowserRouter>
        </TooltipProvider>
        <Toaster richColors closeButton />
        {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
)
