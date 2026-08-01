import { Link } from "react-router-dom"
import { CheckCircle2, ExternalLink, Globe, HardDrive, LoaderCircle } from "lucide-react"
import { useTaskReceipt } from "@/lib/queries"
import { cn } from "@/lib/utils"

function formatDuration(seconds: number): string {
  if (seconds < 1) return "under a second"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`
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
        <Link to={`/tasks/${taskId}`} className="flex items-center gap-1 hover:text-foreground">
          Full trace <ExternalLink className="size-3" />
        </Link>
      </div>
    </div>
  )
}
