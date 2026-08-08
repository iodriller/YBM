import { useId, cloneElement } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type SettingsSummary } from "@/lib/api"
import { useServerForm } from "@/lib/use-server-form"
import { useSettingsSummary, useUpdateWorkspaceConfig } from "@/lib/queries"

type Draft = { enabled: boolean; rootDir: string; webHost: string; webPortStart: string; openBrowser: boolean }

function deriveDraft(data: SettingsSummary): Draft {
  const workspace = data.config.adapters.workspace
  return {
    enabled: workspace.enabled,
    rootDir: workspace.root_dir,
    webHost: workspace.web_host,
    webPortStart: String(workspace.web_port_start),
    openBrowser: workspace.open_browser,
  }
}

/** Level 2/Advanced. Ports Streamlit's `_render_workspace_config`. */
export function WorkspaceSettingsCard() {
  const { data, isPending } = useSettingsSummary()
  const [draft, setDraft, resetDraft] = useServerForm(data, deriveDraft)
  const updateWorkspace = useUpdateWorkspaceConfig()

  if (isPending || !draft) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Workspace</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Workspace</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            updateWorkspace.mutate(
              {
                enabled: draft.enabled,
                root_dir: draft.rootDir,
                web_host: draft.webHost,
                web_port_start: Number(draft.webPortStart) || 8890,
                open_browser: draft.openBrowser,
              },
              {
                onSuccess: () => {
                  toast.success("Workspace config saved.")
                  resetDraft()
                },
                onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not save workspace config."),
              },
            )
          }}
        >
          <div className="flex items-center gap-2">
            <Switch checked={draft.enabled} onCheckedChange={(v) => setDraft({ ...draft, enabled: v })} />
            <Label className="text-sm">Enabled</Label>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Root directory">
              <Input value={draft.rootDir} onChange={(e) => setDraft({ ...draft, rootDir: e.target.value })} />
            </Field>
            <Field label="Web host">
              <Input value={draft.webHost} onChange={(e) => setDraft({ ...draft, webHost: e.target.value })} />
            </Field>
            <Field label="Web port start">
              <Input type="number" value={draft.webPortStart} onChange={(e) => setDraft({ ...draft, webPortStart: e.target.value })} />
            </Field>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={draft.openBrowser} onCheckedChange={(v) => setDraft({ ...draft, openBrowser: v })} />
            <Label className="text-sm">Open browser automatically</Label>
          </div>
          <div className="flex justify-end">
            <Button type="submit" size="sm" disabled={updateWorkspace.isPending}>
              Save workspace config
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
