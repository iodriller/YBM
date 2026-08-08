import { useId, cloneElement } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type SettingsSummary, type TaskRecord } from "@/lib/api"
import { useServerForm } from "@/lib/use-server-form"
import { CANCELLABLE } from "@/lib/task-signals"
import { useSettingsSummary, useTaskSignal, useTasks, useUpdateComputerUseConfig } from "@/lib/queries"

function findActiveComputerUseTask(tasks: TaskRecord[]): TaskRecord | undefined {
  return tasks.find(
    (task) => task.metadata.last_tool_name === "computer.use" || Boolean(task.metadata.desktop_observation),
  )
}

type Draft = {
  enabled: boolean
  maxSteps: string
  stepDelaySeconds: string
  screenshotDir: string
  allowedApps: string
  allowedRoots: string
  requireSessionApproval: boolean
  maxUiElements: string
}

function deriveDraft(data: SettingsSummary): Draft {
  const cu = data.config.adapters.computer_use
  return {
    enabled: cu.enabled,
    maxSteps: String(cu.max_steps),
    stepDelaySeconds: String(cu.step_delay_seconds),
    screenshotDir: cu.screenshot_dir,
    allowedApps: cu.allowed_apps.join(", "),
    allowedRoots: cu.allowed_roots.join(", "),
    requireSessionApproval: cu.require_session_approval,
    maxUiElements: String(cu.max_ui_elements),
  }
}

function parseList(value: string): string[] {
  return value.split(",").map((part) => part.trim()).filter(Boolean)
}

/** Level 2/Advanced. Ports Streamlit's `_render_computer_use_config`,
 * including its live "currently running computer-use task" monitor and
 * stop button - not just the static config form. */
export function ComputerUseSettingsCard() {
  const { data, isPending } = useSettingsSummary()
  const [draft, setDraft, resetDraft] = useServerForm(data, deriveDraft)
  const updateComputerUse = useUpdateComputerUseConfig()
  const { data: taskList } = useTasks(25)
  const signal = useTaskSignal()

  const activeSession = findActiveComputerUseTask(taskList?.tasks ?? [])
  const actionsCount = Array.isArray(activeSession?.metadata.computer_use_actions)
    ? activeSession.metadata.computer_use_actions.length
    : 0
  const screenshotPath =
    typeof activeSession?.metadata.screenshot_path === "string" ? activeSession.metadata.screenshot_path : null

  if (isPending || !draft) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Computer use</CardTitle>
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
        <CardTitle>Computer use</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {activeSession && (
          <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">Last computer-use task</span>
              <Badge variant="secondary">{activeSession.status}</Badge>
            </div>
            <p className="text-xs text-muted-foreground">{activeSession.objective}</p>
            {screenshotPath && <p className="font-mono text-xs text-muted-foreground">{screenshotPath}</p>}
            {actionsCount > 0 && <p className="text-xs text-muted-foreground">Actions recorded: {actionsCount}</p>}
            {CANCELLABLE.has(activeSession.status) && (
              <Button
                variant="destructive"
                size="sm"
                className="self-start"
                disabled={signal.isPending}
                onClick={() => {
                  signal.mutate(
                    { taskId: activeSession.id, signal: "cancel" },
                    {
                      onSuccess: () => toast.success("Computer-use stop signal sent."),
                      onError: (err) => {
                        toast.error(err instanceof ApiError ? err.message : "Could not stop the task.")
                      },
                    },
                  )
                }}
              >
                Stop active computer-use task
              </Button>
            )}
          </div>
        )}

        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            updateComputerUse.mutate(
              {
                enabled: draft.enabled,
                max_steps: Number(draft.maxSteps) || 8,
                step_delay_seconds: Number(draft.stepDelaySeconds) || 0.4,
                screenshot_dir: draft.screenshotDir,
                allowed_apps: parseList(draft.allowedApps),
                allowed_roots: parseList(draft.allowedRoots),
                require_session_approval: draft.requireSessionApproval,
                max_ui_elements: Number(draft.maxUiElements) || 80,
              },
              {
                onSuccess: () => {
                  toast.success("Computer use config saved.")
                  resetDraft()
                },
                onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not save computer use config."),
              },
            )
          }}
        >
          <div className="flex items-center gap-2">
            <Switch checked={draft.enabled} onCheckedChange={(v) => setDraft({ ...draft, enabled: v })} />
            <Label className="text-sm">Enabled</Label>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Max steps">
              <Input type="number" value={draft.maxSteps} onChange={(e) => setDraft({ ...draft, maxSteps: e.target.value })} />
            </Field>
            <Field label="Step delay (s)">
              <Input
                type="number"
                step="0.1"
                value={draft.stepDelaySeconds}
                onChange={(e) => setDraft({ ...draft, stepDelaySeconds: e.target.value })}
              />
            </Field>
            <Field label="Screenshot dir">
              <Input value={draft.screenshotDir} onChange={(e) => setDraft({ ...draft, screenshotDir: e.target.value })} />
            </Field>
            <Field label="Max UI elements">
              <Input
                type="number"
                value={draft.maxUiElements}
                onChange={(e) => setDraft({ ...draft, maxUiElements: e.target.value })}
              />
            </Field>
            <Field label="Allowed apps (comma-separated)">
              <Input value={draft.allowedApps} onChange={(e) => setDraft({ ...draft, allowedApps: e.target.value })} />
            </Field>
            <Field label="Allowed roots (comma-separated)">
              <Input value={draft.allowedRoots} onChange={(e) => setDraft({ ...draft, allowedRoots: e.target.value })} />
            </Field>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={draft.requireSessionApproval}
              onCheckedChange={(v) => setDraft({ ...draft, requireSessionApproval: v })}
            />
            <Label className="text-sm">Require session approval</Label>
          </div>
          <div className="flex justify-end">
            <Button type="submit" size="sm" disabled={updateComputerUse.isPending}>
              Save computer use config
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
