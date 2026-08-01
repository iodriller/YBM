import { Button } from "@/components/ui/button"

/**
 * The three decision buttons, extracted so the Chat inline card, the
 * expanded Evidence Pack, and the review dialog's sticky footer all render
 * the same choices in the same order without duplicating decision logic
 * (docs/UI_UX_AUDIT.md Phase 8).
 *
 * Still no "Always allow" - that needs a revocation list to be safe, which
 * doesn't exist yet (the original scope-down in Phase 1 still holds).
 */
export function ApprovalActions({
  onApprove,
  onApproveForTask,
  onDeny,
  deciding,
  expired,
  size = "default",
}: {
  onApprove: () => void
  onApproveForTask: () => void
  onDeny: () => void
  deciding: boolean
  expired?: boolean
  size?: "default" | "sm"
}) {
  const disabled = deciding || expired
  const compact = size === "sm"
  const buttonSize = compact ? "sm" : "default"
  const extra = compact ? "h-7 px-2.5 text-xs" : ""

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button type="button" variant="outline" size={buttonSize} className={extra} disabled={disabled} onClick={onDeny}>
        Deny
      </Button>
      <Button
        type="button"
        variant="outline"
        size={buttonSize}
        className={extra}
        disabled={disabled}
        onClick={onApproveForTask}
      >
        Allow for this task
      </Button>
      <Button type="button" size={buttonSize} className={extra} disabled={disabled} onClick={onApprove}>
        Approve once
      </Button>
    </div>
  )
}
