import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { ApiError, fetchVoiceConfig, updateVoiceConfig, type VoiceConfig } from "@/lib/api"

/**
 * Turn speech-to-text on or off.
 *
 * This exists because the reply to a failed voice message tells the user to
 * "turn on voice under Settings" - advice the console could not honour, since
 * there was nothing here to turn on.
 *
 * The distinction the copy has to keep straight: this is transcription, not a
 * model capability. Local models do not take audio; the recording becomes text
 * before the model sees anything.
 */
export function VoiceSettingsCard() {
  const [config, setConfig] = useState<VoiceConfig | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetchVoiceConfig()
      .then(setConfig)
      // Hiding the card on error was the same silent-failure shape this whole
      // area exists to fix: the setting appears not to exist rather than
      // appearing to be unreachable.
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Could not load voice settings."))
  }, [])

  async function toggle(enabled: boolean) {
    setSaving(true)
    try {
      await updateVoiceConfig(enabled)
      setConfig((prev) => (prev ? { ...prev, enabled } : prev))
      toast.success(enabled ? "Voice messages turned on." : "Voice messages turned off.")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not change that.")
    } finally {
      setSaving(false)
    }
  }

  if (loadError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Voice messages</CardTitle>
          <CardDescription>{loadError}</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  if (!config) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Voice messages</CardTitle>
        <CardDescription>
          Turns recordings into text before YBM reads them - in this console and on Telegram. The
          model itself never hears audio.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Switch
            id="voice-enabled"
            checked={config.enabled}
            disabled={saving || !config.available}
            onCheckedChange={toggle}
          />
          <Label htmlFor="voice-enabled" className="text-sm">Understand voice messages</Label>
        </div>
        {!config.available ? (
          // Flipping the switch without the package fails at the first
          // recording, so the reason is stated before it can be flipped.
          <p className="text-xs text-muted-foreground">
            Needs the voice extra installed first - run <code>{config.install_hint}</code> and
            restart.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Using {config.provider} ({config.model}). Everything stays on this machine.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
