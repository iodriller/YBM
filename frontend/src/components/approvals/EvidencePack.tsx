import { Link } from "react-router-dom"
import { AlertTriangle, ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import type { PendingApprovalItem } from "@/lib/api"
import { RISK_ORDER } from "@/lib/api"
import { describeCapability } from "@/lib/capability"
import { formatCountdown, useCountdown } from "@/lib/time"

/**
 * The approval decision surface (docs/UI_REWRITE_PLAN.md §11.2), ordered
 * for a sub-15-second decision per the 2026 human-in-the-loop research this
 * plan cites: Why -> What -> Exactly what -> Blast radius -> Reversibility
 * -> Authority -> expiry. Never a bare "Approve?" button.
 */
export function EvidencePack({
  item,
  onApprove,
  onDeny,
  deciding,
}: {
  item: PendingApprovalItem
  onApprove: () => void
  onDeny: () => void
  deciding: boolean
}) {
  const { approval, blast_radius: blastRadius } = item
  const remaining = useCountdown(approval.expires_at)
  const expired = remaining <= 0
  const operation = (approval.action_payload.input as { operation?: string } | undefined)?.operation
  const toolName = (approval.action_payload.tool_name as string | undefined) ?? "unknown tool"
  const exceedsCeiling =
    item.capability_max_risk_level != null &&
    RISK_ORDER[approval.risk_level] > RISK_ORDER[item.capability_max_risk_level]
  const hasBlastRadius =
    blastRadius.files.length > 0 || blastRadius.urls.length > 0 || blastRadius.commands.length > 0

  return (
    <div className="flex flex-col gap-4 text-sm">
      <Field label="Why">
        <p>{approval.summary}</p>
      </Field>

      <Field label="What">
        <p>
          <span className="font-mono text-xs">{toolName}</span>
          {operation && <span className="text-muted-foreground"> · {operation}</span>}
        </p>
      </Field>

      <Field label="Exactly what">
        <pre className="max-h-48 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">
          {JSON.stringify(approval.action_payload.input ?? {}, null, 2)}
        </pre>
      </Field>

      <Field label="Blast radius">
        {hasBlastRadius ? (
          <div className="flex flex-col gap-1">
            {blastRadius.files.map((f) => (
              <BlastRadiusRow key={f} kind="file" value={f} />
            ))}
            {blastRadius.urls.map((u) => (
              <BlastRadiusRow key={u} kind="url" value={u} />
            ))}
            {blastRadius.commands.map((c) => (
              <BlastRadiusRow key={c} kind="command" value={c} />
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground">
            No specific files, URLs, or commands detected in the parameters above.
          </p>
        )}
      </Field>

      <Field label="Reversibility">
        <p>{describeCapability(approval.capability)}</p>
      </Field>

      <Field label="Authority">
        <div className="flex items-center gap-2">
          <Badge variant={exceedsCeiling ? "destructive" : "secondary"}>
            {approval.risk_level}
          </Badge>
          <span className="text-muted-foreground">
            capability: <span className="font-mono">{approval.capability}</span>
            {item.capability_max_risk_level && ` · ceiling: ${item.capability_max_risk_level}`}
          </span>
        </div>
        {exceedsCeiling && (
          <p className="mt-1 flex items-center gap-1 text-xs text-destructive">
            <AlertTriangle className="size-3.5" />
            This risk level exceeds the configured ceiling for this capability.
          </p>
        )}
      </Field>

      <Separator />

      <div className="flex items-center justify-between">
        <span className={expired ? "text-destructive" : "text-muted-foreground"}>
          {expired ? "This approval has expired." : `Expires in ${formatCountdown(remaining)}`}
        </span>
        <Link
          to={`/tasks/${approval.task_id}`}
          className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
        >
          Open task trace <ExternalLink className="size-3.5" />
        </Link>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onDeny} disabled={deciding || expired}>
          Deny
        </Button>
        <Button onClick={onApprove} disabled={deciding || expired}>
          Approve
        </Button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  )
}

function BlastRadiusRow({ kind, value }: { kind: "file" | "url" | "command"; value: string }) {
  return (
    <div className="flex items-center gap-2 font-mono text-xs">
      <Badge variant="outline" className="shrink-0 font-sans text-[10px]">
        {kind}
      </Badge>
      <span className="truncate">{value}</span>
    </div>
  )
}
