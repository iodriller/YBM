import { chatAnswerText, isTerminal } from "@/lib/chat"
import { formatDuration } from "@/lib/time"
import { tokenUsageOf, type TaskRecord } from "@/lib/api"

/**
 * What actually happened, not just the status badge (docs/UI_UX_AUDIT.md
 * Phase 9 - "the tasks menu should be able to show the outcome as well").
 * StatusBadge already says completed/failed/blocked; this says WHY, reusing
 * chatAnswerText so the same text Chat shows for a task's outcome is what
 * the Tasks list shows too, not a second copy of the same summarization
 * logic. Duration/step count/cost all come from the list response's own
 * metadata - no per-row trace fetch needed.
 */
export function TaskOutcomeCell({ task }: { task: TaskRecord }) {
  if (!isTerminal(task.status)) {
    return <span className="text-muted-foreground">In progress...</span>
  }

  const summary = chatAnswerText(task)
  const durationSeconds = Math.max(
    0,
    (new Date(task.updated_at).getTime() - new Date(task.created_at).getTime()) / 1000,
  )
  const steps = Array.isArray(task.metadata.operator_history) ? task.metadata.operator_history.length : 0
  const usage = tokenUsageOf(task)
  const failed = task.status === "failed" || task.status === "blocked"

  return (
    <div className="flex flex-col gap-0.5">
      <span className={`line-clamp-1 text-sm ${failed ? "text-destructive" : ""}`}>{summary}</span>
      <span className="text-[11px] text-muted-foreground">
        {formatDuration(durationSeconds)}
        {steps > 0 && ` · ${steps} step${steps === 1 ? "" : "s"}`}
        {usage?.total_tokens ? ` · ${usage.total_tokens.toLocaleString()} tokens` : ""}
      </span>
    </div>
  )
}
