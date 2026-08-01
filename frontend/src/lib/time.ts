import { useEffect, useState } from "react"

/** Live countdown formatting for approval expiry (docs/UI_REWRITE_PLAN.md §11.3). */
export function secondsUntil(isoTimestamp: string): number {
  const target = new Date(isoTimestamp).getTime()
  return Math.max(0, Math.round((target - Date.now()) / 1000))
}

export function formatCountdown(totalSeconds: number): string {
  if (totalSeconds <= 0) return "expired"
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes === 0) return `${seconds}s`
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`
}

/** Re-renders every second so an expiry countdown ticks live in the UI,
 * independent of the (much slower) approvals poll interval. */
export function useCountdown(isoTimestamp: string): number {
  const [remaining, setRemaining] = useState(() => secondsUntil(isoTimestamp))

  useEffect(() => {
    setRemaining(secondsUntil(isoTimestamp))
    const id = setInterval(() => setRemaining(secondsUntil(isoTimestamp)), 1_000)
    return () => clearInterval(id)
  }, [isoTimestamp])

  return remaining
}

/** How long a task ran/took - shared by the Task Receipt card and the
 * Tasks list outcome column (docs/UI_UX_AUDIT.md Phase 9) rather than
 * each computing their own copy of the same rounding rules. */
export function formatDuration(seconds: number): string {
  if (seconds < 1) return "under a second"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`
}

/** "2m ago" / "just now" - not live-ticking (no per-second re-render), the
 * task/summary polls this reads from already refresh every few seconds. */
export function formatRelativeTime(isoTimestamp: string): string {
  const deltaSeconds = Math.max(0, Math.round((Date.now() - new Date(isoTimestamp).getTime()) / 1000))
  if (deltaSeconds < 5) return "just now"
  if (deltaSeconds < 60) return `${deltaSeconds}s ago`
  const minutes = Math.floor(deltaSeconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
