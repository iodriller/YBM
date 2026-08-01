import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { ApiError } from "@/lib/api"
import {
  useSelectLLMPreset,
  useSettingsSummary,
  useSetupDetect,
  useUpdateLLMConfig,
  useUpdateTelegramConfig,
} from "@/lib/queries"

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
  const { data: detect, isPending: detectPending } = useSetupDetect(true)
  const { data: settingsData } = useSettingsSummary()
  const selectPreset = useSelectLLMPreset()
  const updateLLM = useUpdateLLMConfig()
  const updateTelegram = useUpdateTelegramConfig()

  const [customModel, setCustomModel] = useState({ apiKeyEnv: "OPENAI_API_KEY", apiKeyValue: "", model: "gpt-4.1" })
  const [telegramEnabled, setTelegramEnabled] = useState(false)
  const [telegramToken, setTelegramToken] = useState("")

  const presets = settingsData?.integrations.llm.presets ?? []

  return (
    <div className="flex h-svh w-full items-center justify-center bg-background p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Welcome to YBM Control</CardTitle>
          <CardDescription>
            A few quick choices - skippable at every step, re-runnable later from Settings.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {detectPending && <Skeleton className="h-32 w-full" />}

          {!detectPending && detect && step === "brain" && (
            <>
              <p className="text-sm font-medium">1. Pick a brain</p>

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
                        Found a local Ollama server with {detect.ollama.models.length} model(s):
                      </p>
                      {detect.ollama.models.slice(0, 8).map((model) => (
                        <Button
                          key={model}
                          variant="outline"
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
                        </Button>
                      ))}
                    </div>
                  )}

                  {!detect.ollama.available && detect.localdeploy_root_present && (
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

                  <details className="text-sm">
                    <summary className="cursor-pointer text-muted-foreground">Or paste an API key</summary>
                    <div className="mt-2 flex flex-col gap-2">
                      <Label className="text-xs text-muted-foreground">Model</Label>
                      <Input
                        value={customModel.model}
                        onChange={(e) => setCustomModel({ ...customModel, model: e.target.value })}
                      />
                      <Label className="text-xs text-muted-foreground">API key env var name</Label>
                      <Input
                        value={customModel.apiKeyEnv}
                        onChange={(e) => setCustomModel({ ...customModel, apiKeyEnv: e.target.value })}
                      />
                      <Label className="text-xs text-muted-foreground">API key</Label>
                      <Input
                        type="password"
                        value={customModel.apiKeyValue}
                        onChange={(e) => setCustomModel({ ...customModel, apiKeyValue: e.target.value })}
                      />
                      <Button
                        size="sm"
                        disabled={updateLLM.isPending || !customModel.model.trim() || !customModel.apiKeyValue.trim()}
                        onClick={() => {
                          updateLLM.mutate(
                            {
                              profile_name: "onboard",
                              default_profile: "onboard",
                              provider: "openai_compatible",
                              model: customModel.model.trim(),
                              base_url: null,
                              api_key_env: customModel.apiKeyEnv.trim() || null,
                              timeout_seconds: 60,
                              max_tokens: 4096,
                              temperature: 0.2,
                              api_key_value: customModel.apiKeyValue,
                            },
                            {
                              onSuccess: () => {
                                toast.success("LLM configured.")
                                setStep("face")
                              },
                              onError: (err) => {
                                toast.error(err instanceof ApiError ? err.message : "Could not save the LLM config.")
                              },
                            },
                          )
                        }}
                      >
                        Save and continue
                      </Button>
                    </div>
                  </details>
                </>
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
              <p className="text-sm font-medium">2. Pick a face</p>
              <p className="text-sm text-muted-foreground">
                Web chat works with zero setup — you&apos;re using it right now.
              </p>

              {detect.telegram_token_present ? (
                <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">Token found</Badge>
                    <span className="text-xs text-muted-foreground">TELEGRAM_BOT_TOKEN is already set</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch checked={telegramEnabled} onCheckedChange={setTelegramEnabled} />
                    <Label className="text-sm">Enable Telegram with it</Label>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <Switch checked={telegramEnabled} onCheckedChange={setTelegramEnabled} />
                    <Label className="text-sm">Also enable Telegram</Label>
                  </div>
                  {telegramEnabled && (
                    <div className="flex flex-col gap-1">
                      <Label className="text-xs text-muted-foreground">Bot token</Label>
                      <Input type="password" value={telegramToken} onChange={(e) => setTelegramToken(e.target.value)} />
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
                    (telegramEnabled && !detect.telegram_token_present && !telegramToken.trim())
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
                Everything dangerous is off by default. Enable capabilities in Access when you&apos;re ready.
              </p>
              <Button size="sm" onClick={onDone}>
                Go to Chat
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
