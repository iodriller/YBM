import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { ApiError } from "@/lib/api"
import { useSelectLLMPreset, useSettingsSummary, useUpdateLLMConfig, useUpdateTelegramConfig } from "@/lib/queries"

type Step = "brain" | "face" | "done"

/**
 * First-run wizard (docs/UI_REWRITE_PLAN.md §14) - shown when
 * `bootstrap.onboarding_complete` is false (backend: CONFIG_FILE_PATH
 * doesn't exist yet), or when re-triggered manually from Settings.
 * Skippable at every step; each mutating step reuses an existing config
 * endpoint, so there is nothing new on the backend here. One real, honest
 * rough edge: skipping both steps never writes config.yaml, so
 * onboarding_complete stays false and the wizard reappears next load -
 * which is arguably correct (nothing was actually configured) rather
 * than a bug, but is worth knowing about.
 */
export function OnboardingWizard({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>("brain")
  const { data } = useSettingsSummary()
  const selectPreset = useSelectLLMPreset()
  const updateLLM = useUpdateLLMConfig()
  const updateTelegram = useUpdateTelegramConfig()

  const [customModel, setCustomModel] = useState({ apiKeyEnv: "OPENAI_API_KEY", apiKeyValue: "", model: "gpt-4.1" })
  const [telegramEnabled, setTelegramEnabled] = useState(false)
  const [telegramToken, setTelegramToken] = useState("")

  const presets = data?.integrations.llm.presets ?? []

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
          {step === "brain" && (
            <>
              <p className="text-sm font-medium">1. Pick a brain</p>
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
                          profile_name: "default",
                          default_profile: "default",
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
              <Button variant="ghost" size="sm" className="self-start" onClick={() => setStep("face")}>
                Skip
              </Button>
            </>
          )}

          {step === "face" && (
            <>
              <p className="text-sm font-medium">2. Pick a face</p>
              <p className="text-sm text-muted-foreground">
                Web chat works with zero setup — you&apos;re using it right now.
              </p>
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
              <div className="flex justify-between">
                <Button variant="ghost" size="sm" onClick={() => setStep("done")}>
                  Skip
                </Button>
                <Button
                  size="sm"
                  disabled={updateTelegram.isPending || (telegramEnabled && !telegramToken.trim())}
                  onClick={() => {
                    if (!telegramEnabled) {
                      setStep("done")
                      return
                    }
                    updateTelegram.mutate(
                      { enabled: true, token_env: "TELEGRAM_BOT_TOKEN", bot_token: telegramToken },
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
