import { useId, cloneElement } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type LLMConfigInput, type SettingsSummary } from "@/lib/api"
import { useServerForm } from "@/lib/use-server-form"
import { useSelectLLMPreset, useSettingsSummary, useTestLLM, useUpdateLLMConfig } from "@/lib/queries"

type Draft = {
  profileName: string
  defaultProfile: string
  provider: string
  model: string
  baseUrl: string
  apiKeyEnv: string
  apiKeyValue: string
  timeoutSeconds: string
  maxTokens: string
  temperature: string
}

function deriveDraft(data: SettingsSummary): Draft {
  const name = data.config.llm.default_profile
  const profile = data.config.llm.profiles[name]
  return {
    profileName: name,
    defaultProfile: name,
    provider: profile?.provider ?? "openai_compatible",
    model: profile?.model ?? "",
    baseUrl: profile?.base_url ?? "",
    apiKeyEnv: profile?.api_key_env ?? "",
    apiKeyValue: "",
    timeoutSeconds: String(profile?.timeout_seconds ?? 60),
    maxTokens: String(profile?.max_tokens ?? 4096),
    temperature: String(profile?.temperature ?? 0.2),
  }
}

/**
 * Level 1 (docs/UI_REWRITE_PLAN.md §14): model picker via presets + a
 * "Test connection" button, plus the full manual profile form. Ports
 * Streamlit's `_render_llm_config`.
 */
export function LLMSettingsCard() {
  const { data, isPending } = useSettingsSummary()
  const [draft, setDraft, resetDraft] = useServerForm(data, deriveDraft)
  const updateLLM = useUpdateLLMConfig()
  const selectPreset = useSelectLLMPreset()
  const testLLM = useTestLLM()

  const presets = data?.integrations.llm.presets ?? []

  function handlePreset(key: string) {
    selectPreset.mutate(key, {
      onSuccess: () => {
        toast.success("LLM preset saved. Restart long-running processes to pick it up.")
        resetDraft()
      },
      onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not apply the preset."),
    })
  }

  function handleTest() {
    testLLM.mutate(undefined, {
      onSuccess: (result) => toast.success(result.output_preview || "LLM responded."),
      onError: (err) => toast.error(err instanceof ApiError ? err.message : "LLM test failed."),
    })
  }

  if (isPending || !draft || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>LLM</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    )
  }

  const input: LLMConfigInput = {
    profile_name: draft.profileName,
    default_profile: draft.defaultProfile,
    provider: draft.provider,
    model: draft.model,
    base_url: draft.baseUrl || null,
    api_key_env: draft.apiKeyEnv || null,
    timeout_seconds: Number(draft.timeoutSeconds) || 60,
    max_tokens: Number(draft.maxTokens) || 4096,
    temperature: Number(draft.temperature) || 0.2,
    api_key_value: draft.apiKeyValue || null,
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>LLM</CardTitle>
        <CardDescription>The model the Concierge, Operator, and Auditor all currently share.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {presets.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {presets.map((preset) => (
              <Button
                key={preset.key}
                type="button"
                size="sm"
                variant={preset.active ? "default" : "outline"}
                disabled={preset.active || selectPreset.isPending}
                onClick={() => handlePreset(preset.key)}
              >
                {preset.label ?? preset.key}
              </Button>
            ))}
          </div>
        )}

        <Button type="button" variant="outline" size="sm" className="self-start" disabled={testLLM.isPending} onClick={handleTest}>
          {testLLM.isPending ? "Testing..." : "Test active LLM"}
        </Button>

        <form
          className="grid grid-cols-1 gap-3 border-t border-border pt-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            updateLLM.mutate(input, {
              onSuccess: () => {
                toast.success("LLM config saved. Restart long-running processes to pick it up.")
                resetDraft()
              },
              onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not save the LLM config."),
            })
          }}
        >
          <Field label="Profile name">
            <Input value={draft.profileName} onChange={(e) => setDraft({ ...draft, profileName: e.target.value })} />
          </Field>
          <Field label="Default profile">
            <Input value={draft.defaultProfile} onChange={(e) => setDraft({ ...draft, defaultProfile: e.target.value })} />
          </Field>
          <Field label="Provider">
            <Input value={draft.provider} onChange={(e) => setDraft({ ...draft, provider: e.target.value })} />
          </Field>
          <Field label="Model">
            <Input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
          </Field>
          <Field label="Base URL">
            <Input value={draft.baseUrl} onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })} />
          </Field>
          <Field label="API key env">
            <Input value={draft.apiKeyEnv} onChange={(e) => setDraft({ ...draft, apiKeyEnv: e.target.value })} />
          </Field>
          <Field label="Replace API key">
            <Input
              type="password"
              value={draft.apiKeyValue}
              onChange={(e) => setDraft({ ...draft, apiKeyValue: e.target.value })}
              placeholder={data.config.llm.profiles[draft.profileName]?.api_key_present ? "already set" : "not set"}
            />
          </Field>
          <Field label="Timeout (s)">
            <Input
              type="number"
              value={draft.timeoutSeconds}
              onChange={(e) => setDraft({ ...draft, timeoutSeconds: e.target.value })}
            />
          </Field>
          <Field label="Max tokens">
            <Input type="number" value={draft.maxTokens} onChange={(e) => setDraft({ ...draft, maxTokens: e.target.value })} />
          </Field>
          <Field label="Temperature">
            <Input
              type="number"
              step="0.1"
              value={draft.temperature}
              onChange={(e) => setDraft({ ...draft, temperature: e.target.value })}
            />
          </Field>
          <div className="col-span-full flex justify-end">
            <Button type="submit" size="sm" disabled={updateLLM.isPending}>
              Save LLM config
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
