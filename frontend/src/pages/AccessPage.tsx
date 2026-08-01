import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/access/ConfirmDialog"
import { AccessGroupCard } from "@/components/access/AccessGroupCard"
import { SecretVaultCard } from "@/components/access/SecretVaultCard"
import { ACCESS_PRESETS, computePreset, type AccessPresetKey } from "@/lib/access-presets"
import { useAdvancedMode } from "@/lib/advanced-mode"
import { useEffectiveConfig, useUpdateAccessModes } from "@/lib/queries"
import { ApiError, type CapabilityAccessMode } from "@/lib/api"

type PendingAction = {
  modes: Record<string, CapabilityAccessMode>
  title: string
  description: string
  confirmLabel: string
}

/**
 * The security control room (docs/UI_REWRITE_PLAN.md §13). Level 1: kill
 * switch, presets, per-group capability toggles, secret vault. Level 2
 * (Advanced): read-only risk ceiling/scope/allow-deny detail per
 * capability, rendered inside each AccessGroupCard. Active time-boxed
 * grants (D2) are not rendered here - deferred in Phase 2 for the same
 * reason as there, and nothing was ever built to list.
 */
export function AccessPage() {
  const { data, isPending, isError, error } = useEffectiveConfig()
  const update = useUpdateAccessModes()
  const { advanced } = useAdvancedMode()
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)

  function apply(modes: Record<string, CapabilityAccessMode>, successMessage: string) {
    update.mutate(modes, {
      onSuccess: () => toast.success(successMessage),
      onError: (err) => {
        toast.error(err instanceof ApiError ? err.message : "Could not update access.")
      },
    })
  }

  if (isPending) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t load access configuration</AlertTitle>
          <AlertDescription>{error?.message ?? "Unknown error"}</AlertDescription>
        </Alert>
      </div>
    )
  }

  const accessModes = data.access_modes
  const groups = Object.values(accessModes)
  const allOff = groups.length > 0 && groups.every((g) => g.mode === "off")

  function handleKillSwitch() {
    setPendingAction({
      modes: Object.fromEntries(groups.map((g) => [g.name, "off" as CapabilityAccessMode])),
      title: "Disable every capability?",
      description:
        "Sets every access group below to Off in one action. The worker keeps running but every gated capability stops being usable until you turn groups back on.",
      confirmLabel: "Disable everything now",
    })
  }

  function handlePreset(preset: AccessPresetKey) {
    const modes = computePreset(accessModes, preset)
    const info = ACCESS_PRESETS.find((p) => p.key === preset)
    if (!info) return
    if (info.destructive) {
      setPendingAction({
        modes,
        title: `Apply "${info.label}"?`,
        description: `${info.description} Applies to: ${groups.map((g) => g.label ?? g.name).join(", ")}.`,
        confirmLabel: `Apply ${info.label}`,
      })
    } else {
      apply(modes, `${info.label} applied.`)
    }
  }

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 [&>*]:shrink-0">
      <div>
        <h1 className="text-lg font-semibold">Access</h1>
        <p className="text-sm text-muted-foreground">
          What the agent is allowed to do, and whether it needs your approval first.
        </p>
      </div>

      {data.warnings.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>Configuration warnings</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {data.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Kill switch</CardTitle>
          <CardDescription>
            Sets every access group below to Off in one action. The worker keeps running but every
            gated capability stops being usable until you turn groups back on.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="destructive" disabled={allOff || update.isPending} onClick={handleKillSwitch}>
            Disable everything now
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Presets</CardTitle>
          <CardDescription>Apply a preset to every access group at once.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {ACCESS_PRESETS.map((preset) => (
            <div
              key={preset.key}
              className="flex items-center justify-between gap-3 rounded-md border border-border p-2"
            >
              <div>
                <p className="text-sm font-medium">{preset.label}</p>
                <p className="text-xs text-muted-foreground">{preset.description}</p>
              </div>
              <Button
                variant={preset.destructive ? "destructive" : "outline"}
                size="sm"
                disabled={update.isPending}
                onClick={() => handlePreset(preset.key)}
              >
                Apply
              </Button>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {groups.map((group) => (
          <AccessGroupCard
            key={group.name}
            group={group}
            rawPolicies={data.config.capabilities}
            advanced={advanced}
            pending={update.isPending}
            onChange={(mode) =>
              apply(
                { [group.name]: mode },
                `${group.label ?? group.name} set to ${group.options.find((o) => o.value === mode)?.label ?? mode}.`,
              )
            }
          />
        ))}
      </div>

      <SecretVaultCard />

      <ConfirmDialog
        open={pendingAction != null}
        onOpenChange={(open) => {
          if (!open) setPendingAction(null)
        }}
        title={pendingAction?.title ?? ""}
        description={pendingAction?.description ?? ""}
        confirmLabel={pendingAction?.confirmLabel ?? "Confirm"}
        pending={update.isPending}
        onConfirm={() => {
          if (pendingAction) apply(pendingAction.modes, "Access updated.")
        }}
      />
    </div>
  )
}
