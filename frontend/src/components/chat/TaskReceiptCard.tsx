import { Link } from "react-router-dom"
import { CheckCircle2, Download, ExternalLink, Globe, HardDrive, LoaderCircle } from "lucide-react"
import type { TaskReceipt } from "@/lib/api"
import { useTaskReceipt } from "@/lib/queries"
import { cn } from "@/lib/utils"

function formatDuration(seconds: number): string {
  if (seconds < 1) return "under a second"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`
}

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
  const changed = [...receipt.changes.files, ...receipt.changes.commands, ...receipt.changes.urls]
  if (changed.length > 0) {
    lines.push("Changed:")
    for (const item of changed) lines.push(`- ${item.value}`)
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
      : "Data left this computer: no",
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

/**
 * The "Done" result format (docs/UI_UX_AUDIT.md Phase 2) - what a
 * completed task actually did, in plain language, instead of asking the
 * user to open the technical trace to find out. Deliberately only shown
 * for status "completed": failed/blocked tasks already have their error
 * text in the bubble above this, and a receipt about a failure it never
 * finished would be more confusing than useful.
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

  return (
    <div className="mt-2.5 flex flex-col gap-2 rounded-lg border border-success/25 bg-success/5 p-3 text-xs">
      <div className="flex items-center gap-1.5 font-medium text-success">
        <CheckCircle2 className="size-3.5" />
        Receipt
      </div>

      {changedItems.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">Changed</p>
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
          : "Nothing left this computer"}
      </div>

      <div className="flex items-center justify-between border-t border-success/15 pt-2 text-[11px] text-muted-foreground">
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
