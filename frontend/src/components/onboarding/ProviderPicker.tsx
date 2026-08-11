import { useEffect, useId, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  ApiError,
  fetchLLMProviders,
  testLLMModel,
  verifyLLMProvider,
  type LLMProviderSpec,
  type LLMTestResult,
  type LLMVerifyResult,
} from "@/lib/api"
import { useUpdateLLMConfig } from "@/lib/queries"

/**
 * Bring-your-own-key model setup, shared by the first-run wizard and Settings.
 *
 * Three deliberate choices:
 *
 * - The provider list comes from the backend catalog, so adding a provider is
 *   a table row rather than a UI change.
 * - The key is verified before it is saved, and the provider is asked what
 *   models it has. A pasted key that is silently accepted fails much later,
 *   somewhere the user will not connect back to this screen - the same failure
 *   shape as the Telegram step that saved a token and produced a bot that
 *   ignored its owner.
 * - The model list is whatever the provider just reported, never a hardcoded
 *   one. Endpoints are stable; model names rot.
 */
export function ProviderPicker({ onConfigured }: { onConfigured: () => void }) {
  const fieldId = useId()
  const [providers, setProviders] = useState<LLMProviderSpec[] | null>(null)
  const [selectedKey, setSelectedKey] = useState<string>("anthropic")
  const [apiKey, setApiKey] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [verifying, setVerifying] = useState(false)
  const [verified, setVerified] = useState<LLMVerifyResult | null>(null)
  const [model, setModel] = useState("")
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<LLMTestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const updateLLM = useUpdateLLMConfig()

  useEffect(() => {
    fetchLLMProviders()
      .then((data) => setProviders(data.providers))
      .catch(() => setError("Could not load the provider list."))
  }, [])

  const spec = providers?.find((p) => p.key === selectedKey) ?? null

  function selectProvider(key: string) {
    setSelectedKey(key)
    // Everything downstream belongs to the old provider.
    setVerified(null)
    setTested(null)
    setModel("")
    setError(null)
    setBaseUrl("")
  }

  async function verify() {
    if (!spec) return
    setError(null)
    setVerifying(true)
    try {
      const result = await verifyLLMProvider({
        provider: spec.key,
        api_key: apiKey.trim() || null,
        base_url: baseUrl.trim() || null,
      })
      setVerified(result)
      // Prefer the provider's suggested default when it actually offers it.
      const suggested = result.models.find((m) => m.id === result.default_model)
      setModel(suggested?.id ?? result.models[0]?.id ?? result.default_model)
    } catch (err) {
      setVerified(null)
      setError(err instanceof ApiError ? err.message : "Could not reach that provider.")
    } finally {
      setVerifying(false)
    }
  }

  /**
   * One real completion before anything is written. A key that lists models
   * can still fail on the chosen model, on provider routing, or on parsing -
   * and each of those surfaces later, somewhere the user will not connect back
   * to this screen. Showing the reply makes "it works" something they can see.
   */
  async function testModel() {
    if (!spec || !model.trim()) return
    setError(null)
    setTesting(true)
    try {
      setTested(await testLLMModel({
        provider: spec.key,
        model: model.trim(),
        api_key: apiKey.trim() || null,
        base_url: baseUrl.trim() || null,
      }))
    } catch (err) {
      setTested(null)
      setError(err instanceof ApiError ? err.message : "That model did not answer.")
    } finally {
      setTesting(false)
    }
  }

  function save() {
    if (!spec || !model.trim()) return
    updateLLM.mutate(
      {
        profile_name: "onboard",
        default_profile: "onboard",
        // The catalog's `kind` is what the backend routes on: "anthropic"
        // goes to the native provider, everything else to the OpenAI shape.
        provider: spec.kind,
        model: model.trim(),
        base_url: baseUrl.trim() || spec.base_url,
        api_key_env: spec.api_key_env,
        timeout_seconds: 120,
        max_tokens: 4096,
        temperature: 0.2,
        api_key_value: apiKey.trim() || null,
      },
      {
        onSuccess: () => {
          toast.success(`${spec.label} configured.`)
          onConfigured()
        },
        onError: (err) => {
          setError(err instanceof ApiError ? err.message : "Could not save this model.")
        },
      },
    )
  }

  if (!providers) {
    return <p className="text-xs text-muted-foreground">Loading providers…</p>
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${fieldId}-provider`} className="text-xs text-muted-foreground">Provider</Label>
        <select
          id={`${fieldId}-provider`}
          className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
          value={selectedKey}
          onChange={(e) => selectProvider(e.target.value)}
        >
          {providers.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
        {spec?.notes && <p className="text-xs text-muted-foreground">{spec.notes}</p>}
      </div>

      {spec?.needs_key && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <Label htmlFor={`${fieldId}-api-key`} className="text-xs text-muted-foreground">API key</Label>
            {spec.keys_url && (
              <a
                href={spec.keys_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs underline underline-offset-2"
              >
                Get a key
              </a>
            )}
          </div>
          <Input
            id={`${fieldId}-api-key`}
            type="password"
            placeholder="sk-…"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value)
              setVerified(null)
            }}
          />
        </div>
      )}

      {/* Only a "custom" OpenAI-compatible endpoint needs one from the user.
          Anthropic also has no catalog base_url, but that is because its SDK
          supplies the default - asking for it there would be a trap. */}
      {spec && spec.kind !== "anthropic" && !spec.base_url && (
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${fieldId}-base-url`} className="text-xs text-muted-foreground">Base URL</Label>
          <Input
            id={`${fieldId}-base-url`}
            placeholder="https://example.com/v1"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value)
              setVerified(null)
            }}
          />
        </div>
      )}

      {!verified && (
        <Button
          size="sm"
          variant="outline"
          className="self-start"
          disabled={verifying || (spec?.needs_key === true && !apiKey.trim())}
          onClick={verify}
        >
          {verifying ? "Checking…" : "Check"}
        </Button>
      )}

      {verified && (
        <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3">
          <div className="flex items-center gap-2">
            <Badge variant="secondary">Connected</Badge>
            <span className="text-xs text-muted-foreground">
              {verified.models.length > 0
                ? `${verified.label} — ${verified.models.length} models available`
                : `${verified.label} — enter a model name`}
            </span>
          </div>
          <Label htmlFor={`${fieldId}-model`} className="text-xs text-muted-foreground">Model</Label>
          {verified.models.length > 0 ? (
            <select
              id={`${fieldId}-model`}
              className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
              value={model}
              onChange={(e) => {
                setModel(e.target.value)
                setTested(null)
              }}
            >
              {verified.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          ) : (
            <Input id={`${fieldId}-model`} value={model} onChange={(e) => setModel(e.target.value)} />
          )}
          {tested ? (
            <div className="flex flex-col gap-2 rounded-md border border-success/40 bg-success/5 p-2">
              <p className="text-xs text-success">
                It answered in {tested.latency_ms} ms:
              </p>
              <p className="text-xs italic text-muted-foreground">&ldquo;{tested.reply}&rdquo;</p>
              <Button
                size="sm"
                className="self-start"
                disabled={updateLLM.isPending}
                onClick={save}
              >
                Use this model
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              className="self-start"
              disabled={testing || !model.trim()}
              onClick={testModel}
            >
              {testing ? "Sending a test message…" : "Test this model"}
            </Button>
          )}
        </div>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
