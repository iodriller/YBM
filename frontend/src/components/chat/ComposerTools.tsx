import { useState } from "react"
import { Plus, X } from "lucide-react"
import { Button } from "@/components/ui/button"

/**
 * A tools menu for the composer, following the pattern ChatGPT settled on: one
 * "+" that reveals modes, each inserting a removable chip so the user can see
 * the scope of a request *before* sending it.
 *
 * These force a capability; they do not enable one. The default is unchanged
 * and is the right default - the agent decides which tool a request needs. A
 * chip is for the case where the user already knows they want the web searched,
 * or wants to pay for the slow thorough version on purpose.
 *
 * Deep research states its cost up front. A mode that silently runs for minutes
 * reads as a hang, so the runtime is part of the label rather than a surprise.
 */

export type ComposerMode = {
  key: string
  label: string
  hint: string
  /** The capability this needs, so a disabled one can say where to enable it. */
  capability: string
}

export const COMPOSER_MODES: ComposerMode[] = [
  {
    key: "web_search",
    label: "Search the web",
    hint: "Look things up online before answering",
    capability: "browser.open",
  },
  {
    key: "deep_research",
    label: "Deep research",
    hint: "Reads many sources and writes up findings — takes several minutes",
    capability: "browser.open",
  },
  {
    key: "code",
    label: "Run code",
    hint: "Compute, transform data, or check a result by running it",
    capability: "code.execute",
  },
]

export function ComposerTools({
  selected,
  onToggle,
  disabledCapabilities,
}: {
  selected: string[]
  onToggle: (key: string) => void
  /** Capabilities policy currently forbids, so chips can explain rather than lie. */
  disabledCapabilities?: string[]
}) {
  const [open, setOpen] = useState(false)
  const blocked = new Set(disabledCapabilities ?? [])

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label="Tools"
        title="Tools"
        className="h-8 w-8"
        onClick={() => setOpen((v) => !v)}
      >
        <Plus className="h-4 w-4" />
      </Button>

      {/* Chips show scope before send, and can be taken back off. */}
      {selected.map((key) => {
        const mode = COMPOSER_MODES.find((m) => m.key === key)
        if (!mode) return null
        return (
          <button
            key={key}
            type="button"
            onClick={() => onToggle(key)}
            className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-xs"
            title={`${mode.hint} — click to remove`}
          >
            {mode.label}
            <X className="h-3 w-3" />
          </button>
        )
      })}

      {open && (
        <div className="mt-1 flex w-full flex-col gap-1 rounded-md border border-border bg-popover p-1.5 shadow-md">
          {COMPOSER_MODES.map((mode) => {
            const isBlocked = blocked.has(mode.capability)
            const isOn = selected.includes(mode.key)
            return (
              <button
                key={mode.key}
                type="button"
                disabled={isBlocked}
                onClick={() => {
                  onToggle(mode.key)
                  setOpen(false)
                }}
                className={`flex flex-col items-start rounded px-2 py-1.5 text-left transition-colors ${
                  isBlocked
                    ? "cursor-not-allowed opacity-60"
                    : isOn
                      ? "bg-primary/10"
                      : "hover:bg-muted"
                }`}
              >
                <span className="text-sm">{mode.label}</span>
                <span className="text-xs text-muted-foreground">
                  {/* The composer must not become a way around the policy
                      engine, so a blocked mode points at Access like the
                      Tools page does. */}
                  {isBlocked ? `Turn on ${mode.capability} in Access to use this` : mode.hint}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
