import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import {
  ApiError,
  awaitFirstTelegramMessage,
  fetchChannels,
  type ChannelSpec,
  verifyTelegramToken,
  type TelegramFirstMessage,
  type TelegramVerifyResult,
} from "@/lib/api"
import {
  useSelectLLMPreset,
  useSettingsSummary,
  useSetupDetect,
  useUpdateLLMConfig,
  useUpdateTelegramConfig,
} from "@/lib/queries"
import { ThemeToggle } from "@/components/layout/ThemeToggle"
import { ProviderPicker } from "@/components/onboarding/ProviderPicker"
import { ChannelGrid } from "@/components/onboarding/ChannelGrid"

type Step = "brain" | "face" | "done"

/**
 * First-run wizard (docs/UI_REWRITE_PLAN.md §14) - shown when
 * `bootstrap.onboarding_complete` is false (backend: no LLM profile is
 * actually configured yet - `ybm setup` always creates config.yaml now, so
 * that alone can't be the signal), or when re-triggered manually from
 * Settings. Skippable at every step; every mutating step reuses an
 * existing config endpoint.
 *
 * Runs on real detection (`GET /api/setup/detect`, mirroring
 * bootstrap.py's own _prompt_llm_choice/_prompt_telegram_choice CLI
 * logic) rather than blind prompts: a real Ollama model list to click,
 * and "already configured" states for LocalDeploy/a cloud key/Telegram
 * that skip the question entirely instead of re-asking for something
 * that's already sitting in .env.
 */
export function OnboardingWizard({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>("brain")
  const { data: detect, isPending: detectPending, refetch: refetchDetect } = useSetupDetect(true)
  const { data: settingsData } = useSettingsSummary()
  const selectPreset = useSelectLLMPreset()
  const updateLLM = useUpdateLLMConfig()
  const updateTelegram = useUpdateTelegramConfig()

  const [channels, setChannels] = useState<ChannelSpec[]>([])
  const [telegramEnabled, setTelegramEnabled] = useState(false)
  const [telegramToken, setTelegramToken] = useState("")
  // The guided Telegram sequence. Enabling used to save a token and nothing
  // else, which produced a bot that ignored every message: the allowlist is
  // what makes it answer, and _authorization_decision fails closed on an empty
  // one. `linked` is the id learned from the user's own first message, so
  // nobody has to go and find a numeric id.
  const [botIdentity, setBotIdentity] = useState<TelegramVerifyResult | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [listening, setListening] = useState(false)
  const [linked, setLinked] = useState<TelegramFirstMessage | null>(null)
  const [telegramError, setTelegramError] = useState<string | null>(null)

  async function verifyToken() {
    setTelegramError(null)
    setVerifying(true)
    try {
      setBotIdentity(await verifyTelegramToken(telegramToken.trim() || null))
    } catch (err) {
      setBotIdentity(null)
      setTelegramError(err instanceof ApiError ? err.message : "Could not check that token.")
    } finally {
      setVerifying(false)
    }
  }

  async function listenForFirstMessage() {
    setTelegramError(null)
    setListening(true)
    try {
      const result = await awaitFirstTelegramMessage(telegramToken.trim() || null)
      if (result.found) setLinked(result)
      else setTelegramError("No message arrived yet. Send your bot any message, then try again.")
    } catch (err) {
      setTelegramError(err instanceof ApiError ? err.message : "Could not check for a message.")
    } finally {
      setListening(false)
    }
  }

  useEffect(() => {
    fetchChannels()
      .then((data) => setChannels(data.channels))
      // The grid is additive: if the catalog cannot be read the step still
      // works, it just shows no cards.
      .catch(() => setChannels([]))
  }, [])

  const presets = settingsData?.integrations.llm.presets ?? []

  return (
    <div className="relative flex h-svh w-full items-center justify-center bg-background p-6">
      <div className="absolute top-4 right-4"><ThemeToggle /></div>
      <Card className="w-full max-w-xl shadow-lg">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-sm font-semibold text-primary-foreground">
              Y
            </div>
            <div>
              <CardTitle>Welcome to YBM Control</CardTitle>
              <CardDescription>
                Two quick questions. Skippable, and re-runnable later from Settings.
              </CardDescription>
            </div>
          </div>
          {/* A dot per step beats "Step 1 of 2" buried in the body text. */}
          {step !== "done" && (
            <div className="mt-3 flex items-center gap-1.5" aria-hidden>
              <span className="h-1.5 w-10 rounded-full bg-primary" />
              <span
                className={`h-1.5 w-10 rounded-full ${step === "face" ? "bg-primary" : "bg-border"}`}
              />
            </div>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {detectPending && <Skeleton className="h-32 w-full" />}

          {!detectPending && detect && step === "brain" && (
            <>
              <p className="text-sm font-medium">Step 1 of 2 &middot; Pick a brain</p>

              {/* Say what was found before offering choices. The list used to
                  be presented with equal confidence whether or not anything
                  behind it was reachable, and in a container none of the
                  loopback presets can work at all. */}
              {!detect.llm_configured && (
                <p className="text-xs text-muted-foreground">
                  {detect.ollama.available
                    ? `Found Ollama on this machine with ${detect.ollama.models.length} model(s) installed.`
                    : detect.ollama.reachable
                      ? "Ollama is running here but has no models installed yet."
                      : "No local model server found on this machine. A cloud API key is the quickest way to start."}
                </p>
              )}

              {detect.llm_configured ? (
                <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">Already configured</Badge>
                    <span className="text-sm font-mono">{detect.current_llm_profile}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    This model is reachable and ready to use as-is.
                  </p>
                </div>
              ) : (
                <>
                  {detect.ollama.available && (
                    <div className="flex flex-col gap-2">
                      <p className="text-xs text-muted-foreground">
                        {detect.ollama.recommended
                          ? "Found a local Ollama server. The recommended model is first - pick it to continue."
                          : `Found a local Ollama server with ${detect.ollama.models.length} model(s):`}
                      </p>
                      {[...detect.ollama.models]
                        .sort((a, b) =>
                          a === detect.ollama.recommended ? -1 : b === detect.ollama.recommended ? 1 : 0,
                        )
                        .slice(0, 8)
                        .map((model) => (
                        <Button
                          key={model}
                          variant={model === detect.ollama.recommended ? "default" : "outline"}
                          className="justify-start font-mono"
                          disabled={updateLLM.isPending}
                          onClick={() => {
                            updateLLM.mutate(
                              {
                                profile_name: "onboard",
                                default_profile: "onboard",
                                provider: "openai_compatible",
                                model,
                                base_url: "http://127.0.0.1:11434/v1",
                                api_key_env: null,
                                timeout_seconds: 120,
                                max_tokens: 4096,
                                temperature: 0.2,
                                api_key_value: null,
                              },
                              {
                                onSuccess: () => {
                                  toast.success(`${model} selected.`)
                                  setStep("face")
                                },
                                onError: (err) => {
                                  toast.error(err instanceof ApiError ? err.message : "Could not select this model.")
                                },
                              },
                            )
                          }}
                        >
                          {model}
                          {model === detect.ollama.recommended && (
                            <span className="ml-2 text-xs opacity-80">recommended</span>
                          )}
                        </Button>
                      ))}
                    </div>
                  )}

                  {/* Ollama is running but nothing is pulled. Without this the
                      wizard looked identical to "no Ollama at all", and the
                      user had to leave, find a model name, and come back - the
                      only such point in onboarding. */}
                  {!detect.ollama.available && detect.ollama.reachable && (
                    <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
                      <Badge variant="secondary" className="w-fit">
                        Ollama running, no models yet
                      </Badge>
                      <p className="text-xs text-muted-foreground">
                        Pull one and reload this page - about 5 GB, a few minutes:
                      </p>
                      <code className="rounded bg-background px-2 py-1 font-mono text-xs">
                        ollama pull qwen3:8b
                      </code>
                      <Button size="sm" variant="outline" className="self-start" onClick={() => refetchDetect()}>
                        I've pulled a model - check again
                      </Button>
                    </div>
                  )}

                  {!detect.ollama.available && !detect.ollama.reachable && detect.localdeploy_root_present && (
                    <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
                      <Badge variant="secondary" className="w-fit">
                        LocalDeploy detected
                      </Badge>
                      <p className="text-xs text-muted-foreground">
                        YBM_LOCALDEPLOY_ROOT is set - keeping the shipped LocalDeploy profile.
                      </p>
                      <Button size="sm" className="self-start" onClick={() => setStep("face")}>
                        Continue
                      </Button>
                    </div>
                  )}

                  {!detect.ollama.available && !detect.localdeploy_root_present && detect.openai_key_present && (
                    <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
                      <Badge variant="secondary" className="w-fit">
                        API key found
                      </Badge>
                      <p className="text-xs text-muted-foreground">
                        OPENAI_API_KEY is already set - keeping the shipped cloud profile that uses it.
                      </p>
                      <Button size="sm" className="self-start" onClick={() => setStep("face")}>
                        Continue
                      </Button>
                    </div>
                  )}

                  {presets.length > 0 && (
                    <div className="flex flex-col gap-2">
                      {presets.map((preset) => (
                        <Button
                          key={preset.key}
                          variant="outline"
                          className="justify-start"
                          disabled={selectPreset.isPending}
                          onClick={() => {
                            selectPreset.mutate(preset.key, {
                              onSuccess: () => {
                                toast.success(`${preset.label ?? preset.key} selected.`)
                                setStep("face")
                              },
                              onError: (err) => {
                                toast.error(err instanceof ApiError ? err.message : "Could not select this preset.")
                              },
                            })
                          }}
                        >
                          {preset.label ?? preset.key}
                        </Button>
                      ))}
                    </div>
                  )}

                  {/* Bring your own key. The old version of this block sent
                      base_url: null, so it could never actually reach a cloud
                      provider - and it offered no provider choice at all.
                      ProviderPicker verifies the key and asks the provider
                      what models it has before saving anything. */}
                  <details className="text-sm" open={!detect.ollama.available}>
                    <summary className="cursor-pointer text-muted-foreground">
                      Or use an API key (Anthropic, OpenAI, and others)
                    </summary>
                    <div className="mt-2">
                      <ProviderPicker onConfigured={() => setStep("face")} />
                    </div>
                  </details>
                </>
              )}

              {!detect.llm_configured && (
                <p className="text-xs text-muted-foreground">
                  Skipping is fine - you can pick a model later in Settings. Until you do, YBM has nothing to think
                  with and cannot answer.
                </p>
              )}
              <div className="flex justify-between">
                <Button variant="ghost" size="sm" onClick={() => setStep("face")}>
                  Skip
                </Button>
                {detect.llm_configured && (
                  <Button size="sm" onClick={() => setStep("face")}>
                    Continue
                  </Button>
                )}
              </div>
            </>
          )}

          {!detectPending && detect && step === "face" && (
            <>
              <p className="text-sm font-medium">Step 2 of 2 &middot; Where can you reach it?</p>
              <p className="text-sm text-muted-foreground">
                Web chat already works. Connect a messaging app to reach YBM from your phone.
              </p>

              {/* The catalog, not a hardcoded toggle - a single "Also enable
                  Telegram" switch implied Telegram was the only thing that
                  would ever exist. */}
              {channels.length > 0 && (
                <ChannelGrid
                  channels={channels}
                  onConnect={(key) => {
                    if (key === "telegram") setTelegramEnabled(true)
                  }}
                />
              )}

              {detect.telegram_token_present ? (
                <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">Token found</Badge>
                    <span className="text-xs text-muted-foreground">TELEGRAM_BOT_TOKEN is already set</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch checked={telegramEnabled} onCheckedChange={setTelegramEnabled} />
                    <Label className="text-sm">Use it for Telegram</Label>
                  </div>
                </div>
              ) : (
                <>
                  {telegramEnabled && (
                    <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3">
                      {/* Three steps, each confirmed before the next. A bare
                          token field asked for something most people have never
                          made, then failed silently later if the allowlist was
                          empty - which it always was, because nothing collected
                          it. */}
                      <div className="flex flex-col gap-1">
                        <p className="text-xs font-medium">1. Create a bot</p>
                        <p className="text-xs text-muted-foreground">
                          Message{" "}
                          <a
                            href="https://t.me/BotFather"
                            target="_blank"
                            rel="noreferrer"
                            className="underline underline-offset-2"
                          >
                            @BotFather
                          </a>{" "}
                          on Telegram and send <code className="rounded bg-background px-1">/newbot</code>. He replies
                          with a token.
                        </p>
                      </div>

                      <div className="flex flex-col gap-1">
                        <p className="text-xs font-medium">2. Paste the token</p>
                        <div className="flex gap-2">
                          <Input
                            type="password"
                            placeholder="123456789:AA..."
                            value={telegramToken}
                            onChange={(e) => {
                              setTelegramToken(e.target.value)
                              setBotIdentity(null)
                              setLinked(null)
                            }}
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={verifying || !telegramToken.trim()}
                            onClick={verifyToken}
                          >
                            {verifying ? "Checking..." : "Check"}
                          </Button>
                        </div>
                        {botIdentity?.username && (
                          <p className="text-xs text-success">Connected to @{botIdentity.username}.</p>
                        )}
                      </div>

                      {botIdentity?.username && (
                        <div className="flex flex-col gap-1">
                          <p className="text-xs font-medium">3. Say hello to your bot</p>
                          <p className="text-xs text-muted-foreground">
                            Open{" "}
                            <a
                              href={botIdentity.link ?? "https://telegram.org"}
                              target="_blank"
                              rel="noreferrer"
                              className="underline underline-offset-2"
                            >
                              @{botIdentity.username}
                            </a>{" "}
                            and send it any message. That is how YBM learns it is you - only you will be able to talk
                            to it.
                          </p>
                          {linked ? (
                            <p className="text-xs text-success">
                              Linked to {linked.username ? `@${linked.username}` : linked.first_name ?? "you"}.
                            </p>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              className="self-start"
                              disabled={listening}
                              onClick={listenForFirstMessage}
                            >
                              {listening ? "Waiting for your message..." : "I've sent a message"}
                            </Button>
                          )}
                        </div>
                      )}

                      {telegramError && <p className="text-xs text-destructive">{telegramError}</p>}
                    </div>
                  )}
                </>
              )}

              <div className="flex justify-between">
                <Button variant="ghost" size="sm" onClick={() => setStep("done")}>
                  Skip
                </Button>
                <Button
                  size="sm"
                  disabled={
                    updateTelegram.isPending ||
                    // Requiring the link is the point: enabling Telegram
                    // without an allowlist produces a bot that silently
                    // ignores its owner.
                    (telegramEnabled && !linked)
                  }
                  onClick={() => {
                    if (!telegramEnabled) {
                      setStep("done")
                      return
                    }
                    updateTelegram.mutate(
                      {
                        enabled: true,
                        token_env: "TELEGRAM_BOT_TOKEN",
                        bot_token: detect.telegram_token_present ? null : telegramToken,
                        // Without these the bot ignores every message:
                        // _authorization_decision fails closed on an empty
                        // allowlist. Learned from the user's own first message
                        // rather than asking them to find a numeric id.
                        ...(linked?.user_id ? { allowed_user_ids: [linked.user_id] } : {}),
                        ...(linked?.chat_id ? { allowed_chat_ids: [linked.chat_id] } : {}),
                      },
                      {
                        onSuccess: () => {
                          toast.success("Telegram enabled.")
                          setStep("done")
                        },
                        onError: (err) => {
                          toast.error(err instanceof ApiError ? err.message : "Could not save Telegram config.")
                        },
                      },
                    )
                  }}
                >
                  Continue
                </Button>
              </div>
            </>
          )}

          {step === "done" && (
            <>
              <p className="text-sm font-medium">You&apos;re set.</p>
              <p className="text-sm text-muted-foreground">
                Everything dangerous is off by default - YBM will ask before it touches anything. You can turn
                capabilities on in Access whenever you need them.
              </p>
              {/* Ending on an empty chat box leaves the user to invent a first
                  request. Suggesting one makes the first act a working round
                  trip instead. */}
              <p className="text-xs text-muted-foreground">Try asking it something like:</p>
              <code className="rounded bg-muted px-2 py-1 text-xs">What can you do?</code>
              <Button size="sm" onClick={onDone}>
                Start chatting
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
