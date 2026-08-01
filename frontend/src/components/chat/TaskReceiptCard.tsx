import { Link } from "react-router-dom"
import { Download, ExternalLink, Globe, HardDrive, LoaderCircle } from "lucide-react"
import type { TaskReceipt, TaskStatus } from "@/lib/api"
import { useTaskReceipt } from "@/lib/queries"
import { cn } from "@/lib/utils"
import { StatusBadge } from "@/components/tasks/StatusBadge"
import { formatDuration } from "@/lib/time"

/** Plain-text export (docs/UI_UX_AUDIT.md Phase 2) - human-readable, not a
 * raw JSON dump, so it reads the same way the card does. Client-side only:
 * every field here already came from the receipt the card already fetched,
 * so there's nothing new to ask the backend for. */
function formatReceiptAsText(receipt: TaskReceipt): string {
  const lines: string[] = []
  lines.push(`YBM Control - Task Receipt`)
  lines.push(`Task: ${receipt.task_id}`)
  lines.push(`Objective: ${receipt.objective}`)
  lines.push(`Status: ${receipt.status}`)
  lines.push(`Started: ${receipt.created_at}`)
  lines.push(`Finished: ${receipt.updated_at}`)
  lines.push(`Duration: ${formatDuration(receipt.duration_seconds)}`)
  lines.push("")
  if (receipt.result_summary) {
    lines.push("Result:", receipt.result_summary, "")
  }
  const touched = [...receipt.changes.files, ...receipt.changes.commands, ...receipt.changes.urls]
  if (touched.length > 0) {
    // Not "Changed": this list merges tool inputs and outputs, so it mixes
    // files merely read or searched with ones actually written, and
    // commands merely requested with ones actually run. Real per-item
    // classification is planned (docs/UI_UX_AUDIT.md Phase 12); until then
    // this label doesn't claim more precision than the data has.
    lines.push("Touched during this task:")
    for (const item of touched) lines.push(`- ${item.value}`)
    lines.push("")
  }
  if (receipt.tools_used.length > 0) {
    lines.push("Tools used:")
    for (const t of receipt.tools_used) lines.push(`- ${t.tool_name} (${t.succeeded} succeeded, ${t.failed} failed)`)
    lines.push("")
  }
  lines.push(
    receipt.data_left_machine
      ? `Data left this computer: yes - ${receipt.services_contacted.map((s) => s.host).join(", ") || "cloud model"}`
      // Not "no": only http.request calls record_egress today, so a false
      // here means no transfer was RECORDED, not that none happened.
      : "No external transfer was recorded",
  )
  if (receipt.approvals.length > 0) {
    lines.push("", "Approvals:")
    for (const a of receipt.approvals) lines.push(`- ${a.summary} -> ${a.status}`)
  }
  if (receipt.uncertainties.length > 0) {
    lines.push("", "Uncertainty:")
    for (const u of receipt.uncertainties) lines.push(`- ${u}`)
  }
  if (receipt.token_usage.total_tokens) {
    lines.push("", `Tokens used: ${receipt.token_usage.total_tokens} (model: ${receipt.token_usage.last_model ?? "unknown"})`)
  }
  return lines.join("\n")
}

function downloadReceipt(receipt: TaskReceipt) {
  const blob = new Blob([formatReceiptAsText(receipt)], { type: "text/plain;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = `ybm-receipt-${receipt.task_id}.txt`
  link.click()
  URL.revokeObjectURL(url)
}

const OUTCOME_STYLE: Partial<Record<TaskStatus, { border: string; bg: string; text: string }>> = {
  completed: { border: "border-success/25", bg: "bg-success/5", text: "text-success" },
  failed: { border: "border-destructive/25", bg: "bg-destructive/5", text: "text-destructive" },
  blocked: { border: "border-destructive/25", bg: "bg-destructive/5", text: "text-destructive" },
  cancelled: { border: "border-border", bg: "bg-muted/40", text: "text-muted-foreground" },
}
const DEFAULT_OUTCOME_STYLE = { border: "border-success/25", bg: "bg-success/5", text: "text-success" }

/**
 * The "Done" result format (docs/UI_UX_AUDIT.md Phase 2), extended in
 * Phase 8 to every terminal state, not only "completed" - a task that
 * modified files, contacted a service, or spent an approval before
 * failing is exactly when the user most needs to see what happened. The
 * header now reflects the real outcome (StatusBadge's own colors/icons)
 * instead of always showing a green checkmark regardless of status.
 */
export function TaskReceiptCard({ taskId }: { taskId: string }) {
  const { data: receipt, isPending } = useTaskReceipt(taskId)

  if (isPending || !receipt) {
    return (
      <div className="mt-2.5 flex items-center gap-1.5 text-xs text-muted-foreground">
        <LoaderCircle className="size-3 animate-spin" />
        Loading receipt...
      </div>
    )
  }

  const changedItems = [...receipt.changes.files, ...receipt.changes.commands]
  const usedTools = receipt.tools_used.map((t) => t.tool_name)
  const contactedHosts = [...new Set(receipt.services_contacted.map((s) => s.host).filter(Boolean))] as string[]
  const style = OUTCOME_STYLE[receipt.status] ?? DEFAULT_OUTCOME_STYLE
  const partial = receipt.status !== "completed" && (changedItems.length > 0 || receipt.tools_used.length > 0)

  return (
    <div className={cn("mt-2.5 flex flex-col gap-2 rounded-lg border p-3 text-xs", style.border, style.bg)}>
      <div className={cn("flex items-center gap-1.5 font-medium", style.text)}>
        <StatusBadge status={receipt.status} />
        Receipt
      </div>
      {partial && (
        <p className="text-muted-foreground">
          Work happened before this task {receipt.status} - shown below, exactly as recorded.
        </p>
      )}

      {changedItems.length > 0 && (
        <div>
          {/* Not "Changed" - the backend merges tool inputs and outputs, so
              this genuinely mixes reads/searches with writes and
              merely-requested commands with executed ones (real
              classification: docs/UI_UX_AUDIT.md Phase 12). */}
          <p className="font-medium text-muted-foreground">Touched during this task</p>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
            {changedItems.slice(0, 8).map((item) => (
              <li key={item.value} className="[overflow-wrap:anywhere]">
                {item.value}
              </li>
            ))}
          </ul>
        </div>
      )}

      {usedTools.length > 0 && (
        <p className="text-muted-foreground">
          <span className="font-medium text-foreground">Used: </span>
          {usedTools.join(", ")}
        </p>
      )}

      <div
        className={cn(
          "flex items-center gap-1.5",
          receipt.data_left_machine ? "text-warning" : "text-muted-foreground",
        )}
      >
        {receipt.data_left_machine ? <Globe className="size-3.5 shrink-0" /> : <HardDrive className="size-3.5 shrink-0" />}
        {receipt.data_left_machine
          ? `Contacted: ${contactedHosts.length > 0 ? contactedHosts.join(", ") : "a cloud model"}`
          : // Not an absolute "nothing left" claim: only http.request calls
            // record_egress today, so this reflects what was recorded, not
            // a guarantee that nothing else contacted anywhere.
            "No external transfer was recorded"}
      </div>

      <div className="flex items-center justify-between border-t border-current/15 pt-2 text-[11px] text-muted-foreground">
        <span>Time: {formatDuration(receipt.duration_seconds)}</span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => downloadReceipt(receipt)}
            className="flex items-center gap-1 hover:text-foreground"
          >
            Download <Download className="size-3" />
          </button>
          <Link to={`/tasks/${taskId}`} className="flex items-center gap-1 hover:text-foreground">
            Full trace <ExternalLink className="size-3" />
          </Link>
        </div>
      </div>
    </div>
  )
}
