import { useState } from "react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ConfirmDialog } from "@/components/access/ConfirmDialog"
import { describeCapability } from "@/lib/capability"
import type { CapabilityAccessMode, CapabilityAccessSummary, CapabilityPolicy } from "@/lib/api"

/**
 * One access-mode group (docs/UI_REWRITE_PLAN.md §13 Level 1) - a Select
 * bound directly to the group's own `options` (never a hardcoded mode
 * list, since not every group supports every mode: desktop_screenshot has
 * no write_access/full_access at all). Selecting Full access always drops
 * `requires_approval` to false server-side (admin.py's _apply_group), so
 * that one transition gets the "confirm dialog naming the concrete
 * consequence" the plan calls for; every other transition applies
 * immediately, matching the Streamlit console's own behavior.
 */
export function AccessGroupCard({
  group,
  rawPolicies,
  advanced,
  onChange,
  pending,
}: {
  group: CapabilityAccessSummary
  rawPolicies: Record<string, CapabilityPolicy>
  advanced: boolean
  onChange: (mode: CapabilityAccessMode) => void
  pending: boolean
}) {
  const [confirmMode, setConfirmMode] = useState<CapabilityAccessMode | null>(null)

  function handleSelect(value: string | null) {
    if (value == null) return
    const mode = value as CapabilityAccessMode
    if (mode === group.mode) return
    if (mode === "full_access") {
      setConfirmMode(mode)
    } else {
      onChange(mode)
    }
  }

  const currentLabel = group.options.find((o) => o.value === group.mode)?.label ?? group.mode

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <div>
          <CardTitle>{group.label ?? group.name}</CardTitle>
          <p className="text-xs text-muted-foreground">{group.capabilities.join(", ")}</p>
        </div>
        <Badge variant={group.mode === "off" ? "outline" : group.requires_approval ? "secondary" : "destructive"}>
          {currentLabel}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Select value={group.mode} onValueChange={handleSelect}>
          <SelectTrigger className="w-56" disabled={pending}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {group.options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {advanced && (
          <div className="flex flex-col gap-2 rounded-md border border-border/60 bg-muted/30 p-2 text-xs">
            <p className="font-medium text-muted-foreground">
              Risk ceilings & patterns (read-only — edit config.yaml to change)
            </p>
            {group.capabilities.map((capability) => {
              const policy = rawPolicies[capability]
              if (!policy) return null
              return (
                <div key={capability} className="flex flex-col gap-0.5">
                  <span className="font-mono">{capability}</span>
                  <span className="text-muted-foreground">
                    {describeCapability(capability)} · ceiling: {policy.max_risk_level}
                    {policy.scopes.length > 0 && ` · scopes: ${policy.scopes.join(", ")}`}
                    {policy.allow_patterns.length > 0 && ` · allow: ${policy.allow_patterns.join(", ")}`}
                    {policy.deny_patterns.length > 0 && ` · deny: ${policy.deny_patterns.join(", ")}`}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmMode != null}
        onOpenChange={(open) => {
          if (!open) setConfirmMode(null)
        }}
        title={`Set ${group.label ?? group.name} to full access?`}
        description={`This removes the approval prompt for ${group.capabilities.join(", ")}. Matching actions run immediately, without you reviewing them first.`}
        confirmLabel="Set to full access"
        pending={pending}
        onConfirm={() => {
          if (confirmMode) onChange(confirmMode)
        }}
      />
    </Card>
  )
}
