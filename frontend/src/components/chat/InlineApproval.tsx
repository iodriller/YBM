import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { EvidencePack } from "@/components/approvals/EvidencePack"
import { ApiError, type PendingApprovalItem } from "@/lib/api"
import { useDecideApproval } from "@/lib/queries"

/**
 * Approvals directly in Chat instead of only behind the Review dialog
 * (docs/UI_UX_AUDIT.md Phase 1): most decisions should take under ten
 * seconds - Deny / Allow for this task / Approve once, right where the
 * task already is. "Review details" expands the same EvidencePack used in
 * the full dialog for the rare case that needs it; nothing about the
 * decision logic is duplicated.
 *
 * No "Always allow" here on purpose - that needs a revocation list to be
 * safe, which doesn't exist yet (docs/UI_UX_AUDIT.md's explicit scope-down).
 */
export function InlineApproval({ item }: { item: PendingApprovalItem }) {
  const [expanded, setExpanded] = useState(false)
  const decide = useDecideApproval()

  function handleDecide(decision: "approve" | "approve_for_task" | "reject") {
    decide.mutate(
      { approvalId: item.approval.id, decision },
      {
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Could not record the decision.")
        },
      },
    )
  }

  if (expanded) {
    return (
      <div className="mt-2.5 rounded-lg border border-warning/30 bg-warning/5 p-3">
        <EvidencePack
          item={item}
          deciding={decide.isPending}
          onApprove={() => handleDecide("approve")}
          onApproveForTask={() => handleDecide("approve_for_task")}
          onDeny={() => handleDecide("reject")}
        />
      </div>
    )
  }

  const toolName = (item.approval.action_payload.tool_name as string | undefined) ?? "unknown tool"

  return (
    <div className="mt-2.5 flex flex-col gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3">
      <p className="text-xs">{item.approval.summary}</p>
      <p className="font-mono text-[11px] text-muted-foreground">{toolName}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 px-2.5 text-xs"
          disabled={decide.isPending}
          onClick={() => handleDecide("reject")}
        >
          Deny
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 px-2.5 text-xs"
          disabled={decide.isPending}
          onClick={() => handleDecide("approve_for_task")}
        >
          Allow for this task
        </Button>
        <Button
          type="button"
          size="sm"
          className="h-7 px-2.5 text-xs"
          disabled={decide.isPending}
          onClick={() => handleDecide("approve")}
        >
          Approve once
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs text-muted-foreground"
          onClick={() => setExpanded(true)}
        >
          Review details
        </Button>
      </div>
    </div>
  )
}
