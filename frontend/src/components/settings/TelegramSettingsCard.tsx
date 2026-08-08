import { useId, useState, cloneElement } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type SettingsSummary, type TelegramOperator } from "@/lib/api"
import { useServerForm } from "@/lib/use-server-form"
import {
  useDetectTelegramOperator,
  useSettingsSummary,
  useTestTelegram,
  useUpdateTelegramConfig,
} from "@/lib/queries"

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

function addId(current: string, id: number): string {
  const ids = parseIds(current)
  return ids.includes(id) ? current : [...ids, id].join(", ")
}

/**
 * Telegram connection panel.
 *
 * Rebuilt around one fact: the two things this connector needs - a bot token
 * and the operator's numeric id - are both invisible to the person setting it
 * up. Telegram never shows a user their own id, and nothing in the product
 * previously said where a token comes from. The old card asked for both as
 * bare text inputs (plus an env var name as the *first* field), and leaving
 * the allowlist empty silently denied every message with a success toast.
 *
 * So the happy path here never asks for a number: verify the token against
 * getMe, then let the operator identify themselves by messaging the bot.
 * The raw fields still exist under Advanced for anyone who wants them.
 */
export function TelegramSettingsCard() {
  const { data, isPending } = useSettingsSummary()
  const [draft, setDraft, resetDraft] = useServerForm(data, deriveDraft)
  const updateTelegram = useUpdateTelegramConfig()
  const testTelegram = useTestTelegram()
  const detectOperator = useDetectTelegramOperator()

  const [showAdvanced, setShowAdvanced] = useState(false)
  const [verifiedBot, setVerifiedBot] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<TelegramOperator[] | null>(null)

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

  const telegram = data.integrations.telegram
  const denials = telegram.recent_denials
  const allowCount = parseIds(draft.allowedUserIds).length + parseIds(draft.allowedChatIds).length
  // The exact state the first-run wizard used to leave behind, and the one
  // that looks identical to a crash from the user's side.
  const denyingEverything = draft.enabled && allowCount === 0
  const connected = telegram.token_present && telegram.enabled && telegram.allowed_user_count > 0

  function save(overrides: Partial<Draft> = {}) {
    const next = { ...draft!, ...overrides }
    updateTelegram.mutate(
      {
        enabled: next.enabled,
        token_env: next.tokenEnv,
        bot_token: next.botToken || null,
        allowed_user_ids: parseIds(next.allowedUserIds),
        allowed_chat_ids: parseIds(next.allowedChatIds),
        polling: next.polling,
      },
      {
        onSuccess: () => {
          // The poller re-reads this config on every loop, so an allowlist
          // change is live within one poll cycle. Only a token swap needs a
          // restart, which is what this says - the old copy told everyone to
          // "restart polling" and gave them no way to do it.
          toast.success(
            next.botToken
              ? "Saved. Restart Telegram polling to pick up the new token."
              : "Saved. The bot picks this up within a few seconds.",
          )
          resetDraft()
        },
        onError: (err) =>
          toast.error(err instanceof ApiError ? err.message : "Could not save Telegram config."),
      },
    )
  }

  function handleVerify() {
    testTelegram.mutate(draft!.botToken || null, {
      onSuccess: (result) => {
        setVerifiedBot(result.bot_username ?? null)
        toast.success(
          result.bot_username ? `Connected to @${result.bot_username}` : "Token is valid.",
        )
      },
      onError: (err) => {
        setVerifiedBot(null)
        toast.error(err instanceof ApiError ? err.message : "Telegram rejected that token.")
      },
    })
  }

  function handleDetect() {
    detectOperator.mutate(draft!.botToken || null, {
      onSuccess: (result) => {
        setCandidates(result.candidates)
        if (result.candidates.length === 0) {
          toast.info("No messages yet - send your bot a message, then try again.")
        }
      },
      onError: (err) =>
        toast.error(err instanceof ApiError ? err.message : "Could not check for messages."),
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Telegram
          {connected && <Badge variant="secondary">Connected</Badge>}
        </CardTitle>
        <CardDescription>
          {connected
            ? `Listening — ${telegram.allowed_user_count} operator${telegram.allowed_user_count === 1 ? "" : "s"} allowed.`
            : "Talk to YBM from your phone. Takes about a minute to set up."}
        </CardDescription>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        {denyingEverything && (
          <Alert variant="destructive">
            <AlertTitle>Nobody can message this bot yet</AlertTitle>
            <AlertDescription>
              Telegram is on, but the allowlist is empty — which refuses every message, including
              yours. Use <b>Find me</b> below, or add an ID under Advanced.
            </AlertDescription>
          </Alert>
        )}

        {denials && denials.count > 0 && denials.latest && (
          <Alert>
            <AlertTitle>
              {denials.count} message{denials.count === 1 ? "" : "s"} refused
            </AlertTitle>
            <AlertDescription className="flex flex-col items-start gap-2">
              <span>
                Most recent: {denials.latest.explanation}
                {denials.latest.user_id !== null && <> (user {denials.latest.user_id})</>}.
              </span>
              {denials.latest.user_id !== null && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    const userId = denials.latest!.user_id!
                    const chatId = denials.latest!.chat_id
                    setDraft({
                      ...draft,
                      allowedUserIds: addId(draft.allowedUserIds, userId),
                      allowedChatIds:
                        chatId !== null ? addId(draft.allowedChatIds, chatId) : draft.allowedChatIds,
                    })
                  }}
                >
                  Allow this person
                </Button>
              )}
            </AlertDescription>
          </Alert>
        )}

        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            save()
          }}
        >
          {/* Step 1 - the token, with the one thing the old card never said:
              where to get one. */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="telegram-bot-token" className="text-sm font-medium">
              Bot token
            </Label>
            <p className="text-xs text-muted-foreground">
              Get one from{" "}
              <a
                href="https://t.me/BotFather"
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
              >
                @BotFather
              </a>{" "}
              — send it <code className="font-mono">/newbot</code>. It looks like{" "}
              <code className="font-mono">8991597588:AAG4…</code>
            </p>
            <div className="flex flex-wrap gap-2">
              <Input
                id="telegram-bot-token"
                type="password"
                className="min-w-48 flex-1"
                placeholder={telegram.token_present ? "Saved — paste a new one to replace" : "Paste your token"}
                value={draft.botToken}
                onChange={(e) => {
                  setVerifiedBot(null)
                  setDraft({ ...draft, botToken: e.target.value })
                }}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={testTelegram.isPending || (!draft.botToken && !telegram.token_present)}
                onClick={handleVerify}
              >
                {testTelegram.isPending ? "Checking…" : "Verify"}
              </Button>
            </div>
            {verifiedBot && (
              <p className="text-xs text-muted-foreground">
                Verified — this token belongs to{" "}
                <a
                  href={`https://t.me/${verifiedBot}`}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono underline underline-offset-2"
                >
                  @{verifiedBot}
                </a>
              </p>
            )}
          </div>

          {/* Step 2 - identify the operator by recognition, not by asking for
              a number Telegram never displays. */}
          <div className="flex flex-col gap-1.5 border-t border-border pt-4">
            <Label className="text-sm font-medium">Who may use it</Label>
            <p className="text-xs text-muted-foreground">
              Message your bot, then press Find me. An empty list refuses everyone.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={detectOperator.isPending}
                onClick={handleDetect}
              >
                {detectOperator.isPending ? "Looking…" : "Find me"}
              </Button>
              {allowCount > 0 && (
                <span className="text-xs text-muted-foreground">
                  {allowCount} entr{allowCount === 1 ? "y" : "ies"} allowed
                </span>
              )}
            </div>

            {candidates && candidates.length > 0 && (
              <ul className="mt-1 flex flex-col gap-1.5">
                {candidates.map((candidate) => {
                  const already = parseIds(draft.allowedUserIds).includes(candidate.user_id)
                  const label = candidate.username
                    ? `@${candidate.username}`
                    : candidate.display_name ?? `User ${candidate.user_id}`
                  return (
                    <li
                      key={candidate.user_id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-2.5 py-1.5"
                    >
                      <span className="text-sm">
                        {label}{" "}
                        <span className="font-mono text-xs text-muted-foreground">
                          {candidate.user_id}
                        </span>
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant={already ? "ghost" : "default"}
                        disabled={already}
                        onClick={() =>
                          setDraft({
                            ...draft,
                            allowedUserIds: addId(draft.allowedUserIds, candidate.user_id),
                            allowedChatIds:
                              candidate.chat_id != null
                                ? addId(draft.allowedChatIds, candidate.chat_id)
                                : draft.allowedChatIds,
                            enabled: true,
                          })
                        }
                      >
                        {already ? "Allowed" : "Allow"}
                      </Button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          <div className="flex items-center gap-2 border-t border-border pt-4">
            <Switch
              id="telegram-enabled"
              checked={draft.enabled}
              onCheckedChange={(v) => setDraft({ ...draft, enabled: v })}
            />
            <Label htmlFor="telegram-enabled" className="text-sm">
              Enabled
            </Label>
          </div>

          {/* Everything below is an implementation detail. "Token env" used to
              be the first field on this card. */}
          <div className="flex flex-col gap-2">
            <button
              type="button"
              className="self-start text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? "Hide advanced" : "Advanced"}
            </button>

            {showAdvanced && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Token env var">
                  <Input
                    value={draft.tokenEnv}
                    onChange={(e) => setDraft({ ...draft, tokenEnv: e.target.value })}
                  />
                </Field>
                <Field label="Allowed user IDs">
                  <Input
                    value={draft.allowedUserIds}
                    onChange={(e) => setDraft({ ...draft, allowedUserIds: e.target.value })}
                  />
                </Field>
                <Field label="Allowed chat IDs">
                  <Input
                    value={draft.allowedChatIds}
                    onChange={(e) => setDraft({ ...draft, allowedChatIds: e.target.value })}
                  />
                </Field>
                <div className="flex items-center gap-2 pt-5">
                  <Switch
                    id="telegram-polling"
                    checked={draft.polling}
                    onCheckedChange={(v) => setDraft({ ...draft, polling: v })}
                  />
                  <Label htmlFor="telegram-polling" className="text-sm">
                    Polling
                  </Label>
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end">
            <Button type="submit" size="sm" disabled={updateTelegram.isPending}>
              {updateTelegram.isPending ? "Saving…" : "Save"}
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
