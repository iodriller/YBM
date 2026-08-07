import { useId, cloneElement } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type SettingsSummary } from "@/lib/api"
import { useServerForm } from "@/lib/use-server-form"
import { useSettingsSummary, useUpdateTelegramConfig } from "@/lib/queries"

type Draft = {
  enabled: boolean
  tokenEnv: string
  botToken: string
  allowedUserIds: string
  allowedChatIds: string
  polling: boolean
}

function deriveDraft(data: SettingsSummary): Draft {
  const telegram = data.config.channels.telegram
  return {
    enabled: telegram.enabled,
    tokenEnv: telegram.token_env,
    botToken: "",
    allowedUserIds: telegram.allowed_user_ids.join(", "),
    allowedChatIds: telegram.allowed_chat_ids.join(", "),
    polling: telegram.polling,
  }
}

function parseIds(value: string): number[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n))
}

/** Level 1 (docs/UI_REWRITE_PLAN.md §14). Ports Streamlit's `_render_telegram_config`. */
export function TelegramSettingsCard() {
  const { data, isPending } = useSettingsSummary()
  const [draft, setDraft, resetDraft] = useServerForm(data, deriveDraft)
  const updateTelegram = useUpdateTelegramConfig()

  if (isPending || !draft || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Telegram</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Telegram</CardTitle>
        <CardDescription>
          {data.config.channels.telegram.token_present ? "Bot token is set." : "No bot token set yet."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            updateTelegram.mutate(
              {
                enabled: draft.enabled,
                token_env: draft.tokenEnv,
                bot_token: draft.botToken || null,
                allowed_user_ids: parseIds(draft.allowedUserIds),
                allowed_chat_ids: parseIds(draft.allowedChatIds),
                polling: draft.polling,
              },
              {
                onSuccess: () => {
                  toast.success("Telegram config saved. Restart polling to reload it.")
                  resetDraft()
                },
                onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not save Telegram config."),
              },
            )
          }}
        >
          <div className="flex items-center gap-2">
            <Switch checked={draft.enabled} onCheckedChange={(v) => setDraft({ ...draft, enabled: v })} />
            <Label className="text-sm">Enabled</Label>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Token env">
              <Input value={draft.tokenEnv} onChange={(e) => setDraft({ ...draft, tokenEnv: e.target.value })} />
            </Field>
            <Field label="Replace bot token">
              <Input type="password" value={draft.botToken} onChange={(e) => setDraft({ ...draft, botToken: e.target.value })} />
            </Field>
            <Field label="Allowed user IDs">
              <Input value={draft.allowedUserIds} onChange={(e) => setDraft({ ...draft, allowedUserIds: e.target.value })} />
            </Field>
            <Field label="Allowed chat IDs">
              <Input value={draft.allowedChatIds} onChange={(e) => setDraft({ ...draft, allowedChatIds: e.target.value })} />
            </Field>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={draft.polling} onCheckedChange={(v) => setDraft({ ...draft, polling: v })} />
            <Label className="text-sm">Polling</Label>
          </div>
          <div className="flex justify-end">
            <Button type="submit" size="sm" disabled={updateTelegram.isPending}>
              Save Telegram config
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactElement }) {
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
