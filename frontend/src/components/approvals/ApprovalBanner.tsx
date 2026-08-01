import { useState } from "react"
import { ShieldAlert } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"
import { toast } from "sonner"
import { ApiError } from "@/lib/api"
import { useDecideApproval, usePendingApprovals } from "@/lib/queries"
import { EvidencePack } from "@/components/approvals/EvidencePack"

/**
 * Persistent, unmissable, on every route (docs/UI_REWRITE_PLAN.md §11.1) -
 * mounted once in AppShell above the router outlet. Renders nothing when
 * there is nothing pending; that's the common case and it should be silent.
 */
export function ApprovalBanner() {
  const { data } = usePendingApprovals()
  const [open, setOpen] = useState(false)
  const decide = useDecideApproval()

  const items = data?.approvals ?? []
  if (items.length === 0) return null

  function handleDecide(approvalId: string, decision: "approve" | "reject") {
    decide.mutate(
      { approvalId, decision },
      {
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Could not record the decision.")
        },
      },
    )
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full items-center justify-between gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2 text-sm text-foreground transition-colors hover:bg-warning/15"
      >
        <span className="flex items-center gap-2 font-medium">
          <ShieldAlert className="size-4 text-warning" />
          {items.length} pending approval{items.length === 1 ? "" : "s"}
        </span>
        <span className="underline underline-offset-2">Review</span>
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Pending approvals</DialogTitle>
            <DialogDescription>
              Full Access and every other preset does not bypass these - a runtime-owned gate
              that only you can clear.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-6">
            {items.map((item, index) => (
              <div key={item.approval.id}>
                {index > 0 && <Separator className="mb-6" />}
                <EvidencePack
                  item={item}
                  deciding={decide.isPending}
                  onApprove={() => handleDecide(item.approval.id, "approve")}
                  onDeny={() => handleDecide(item.approval.id, "reject")}
                />
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
