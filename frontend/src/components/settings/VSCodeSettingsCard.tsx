import { useId, cloneElement } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type SettingsSummary } from "@/lib/api"
import { useServerForm } from "@/lib/use-server-form"
import { formatRelativeTime } from "@/lib/time"
import { useSettingsSummary, useUpdateVSCodeConfig } from "@/lib/queries"

type Draft = { enabled: boolean; bridgeHost: string; bridgePort: string; authTokenEnv: string; bridgeToken: string }

function deriveDraft(data: SettingsSummary): Draft {
  const vscode = data.config.adapters.vscode
  return {
    enabled: vscode.enabled,
    bridgeHost: vscode.bridge_host,
    bridgePort: String(vscode.bridge_port),
    authTokenEnv: vscode.auth_token_env,
    bridgeToken: "",
  }
}

/** Level 2/Advanced. Ports Streamlit's `_render_vscode_config`. */
export function VSCodeSettingsCard() {
  const { data, isPending } = useSettingsSummary()
  const [draft, setDraft, resetDraft] = useServerForm(data, deriveDraft)
  const updateVSCode = useUpdateVSCodeConfig()

  if (isPending || !draft) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>VS Code bridge</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    )
  }

  const vscodeStatus = data?.vscode

  return (
    <Card>
      <CardHeader>
        <CardTitle>VS Code bridge</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {vscodeStatus && (
          <div className="flex flex-col gap-1 rounded-md border border-border bg-muted/30 p-3 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">Connection</span>
              <Badge variant={vscodeStatus.connected ? "secondary" : "outline"}>{vscodeStatus.status}</Badge>
            </div>
            {vscodeStatus.last_seen_at ? (
              <p className="text-xs text-muted-foreground">Last seen {formatRelativeTime(vscodeStatus.last_seen_at)}</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                No heartbeat yet - open VS Code with the YBM Control Bridge extension enabled.
              </p>
            )}
            {vscodeStatus.state?.workspace_folders?.[0] && (
              <p className="truncate font-mono text-xs text-muted-foreground">
                {vscodeStatus.state.workspace_folders[0]}
              </p>
            )}
            {vscodeStatus.state?.active_file && (
              <p className="truncate font-mono text-xs text-muted-foreground">{vscodeStatus.state.active_file}</p>
            )}
          </div>
        )}

        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            updateVSCode.mutate(
              {
                enabled: draft.enabled,
                bridge_host: draft.bridgeHost,
                bridge_port: Number(draft.bridgePort) || 8766,
                auth_token_env: draft.authTokenEnv,
                bridge_token: draft.bridgeToken || null,
              },
              {
                onSuccess: () => {
                  toast.success("VS Code config saved.")
                  resetDraft()
                },
                onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not save VS Code config."),
              },
            )
          }}
        >
          <div className="flex items-center gap-2">
            <Switch checked={draft.enabled} onCheckedChange={(v) => setDraft({ ...draft, enabled: v })} />
            <Label className="text-sm">Enabled</Label>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Bridge host">
              <Input value={draft.bridgeHost} onChange={(e) => setDraft({ ...draft, bridgeHost: e.target.value })} />
            </Field>
            <Field label="Bridge port">
              <Input type="number" value={draft.bridgePort} onChange={(e) => setDraft({ ...draft, bridgePort: e.target.value })} />
            </Field>
            <Field label="Token env">
              <Input value={draft.authTokenEnv} onChange={(e) => setDraft({ ...draft, authTokenEnv: e.target.value })} />
            </Field>
            <Field label="Replace token">
              <Input type="password" value={draft.bridgeToken} onChange={(e) => setDraft({ ...draft, bridgeToken: e.target.value })} />
            </Field>
          </div>
          <div className="flex justify-end">
            <Button type="submit" size="sm" disabled={updateVSCode.isPending}>
              Save VS Code config
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactElement<{ id?: string }> }) {
  // Every call site passes exactly one form control as children - id/htmlFor
  // pairing via useId() associates the visible label with it for screen
  // readers, without changing the sibling Label-then-control DOM structure
  // (cloneElement, not wrapping children inside <label>, so the existing
  // flex-col layout is untouched).
  const id = useId()
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      {cloneElement(children, { id })}
    </div>
  )
}
