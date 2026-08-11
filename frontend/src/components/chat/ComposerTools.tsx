import { useEffect, useRef, useState } from "react"
import { Plus, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { COMPOSER_MODES } from "@/lib/composer-modes"

/**
 * A tools menu for the composer, following the pattern ChatGPT settled on: one
 * "+" that reveals modes, each inserting a removable chip so the user can see
 * the scope of a request *before* sending it.
 *
 * These force a capability; they do not enable one. The default is unchanged
 * and is the right default - the agent decides which tool a request needs. A
 * chip is for the case where the user already knows they want the web
 * searched, or wants the slow thorough version on purpose.
 *
 * Layout note: the menu is absolutely positioned *above* the composer. The
 * first version let it sit in normal flow, which pushed the "+" onto its own
 * row and dropped the panel on top of the textarea and the send button.
 */

/** The chips, rendered above the input row so they never overlap it. */
export function ComposerModeChips({
  selected,
  onToggle,
}: {
  selected: string[]
  onToggle: (key: string) => void
}) {
  if (selected.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-1 pb-1.5">
      {selected.map((key) => {
        const mode = COMPOSER_MODES.find((m) => m.key === key)
        if (!mode) return null
        const Icon = mode.icon
        return (
          <button
            key={key}
            type="button"
            onClick={() => onToggle(key)}
            title={`${mode.hint} — click to remove`}
            className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 py-1 pl-2.5 pr-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
          >
            <Icon className="size-3.5" />
            {mode.label}
            <X className="size-3.5 opacity-70" />
          </button>
        )
      })}
    </div>
  )
}

export function ComposerTools({
  selected,
  onToggle,
  disabledCapabilities,
}: {
  selected: string[]
  onToggle: (key: string) => void
  /** Capabilities policy currently forbids, so the menu explains rather than lies. */
  disabledCapabilities?: string[]
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const blocked = new Set(disabledCapabilities ?? [])

  // Click-away and Escape, so the panel behaves like a menu rather than a
  // block of markup that happens to be visible.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label="Tools"
        aria-expanded={open}
        title="Tools"
        className="size-9 rounded-xl text-muted-foreground"
        onClick={() => setOpen((v) => !v)}
      >
        <Plus className={`size-4 transition-transform ${open ? "rotate-45" : ""}`} />
      </Button>

      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-2 w-72 overflow-hidden rounded-xl border border-border bg-popover p-1 shadow-lg">
          {COMPOSER_MODES.map((mode) => {
            const isBlocked = blocked.has(mode.capability)
            const isOn = selected.includes(mode.key)
            const Icon = mode.icon
            return (
              <button
                key={mode.key}
                type="button"
                disabled={isBlocked}
                onClick={() => {
                  onToggle(mode.key)
                  setOpen(false)
                }}
                className={`flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                  isBlocked
                    ? "cursor-not-allowed opacity-50"
                    : isOn
                      ? "bg-primary/10"
                      : "hover:bg-muted"
                }`}
              >
                <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <span className="flex min-w-0 flex-col">
                  <span className="text-sm font-medium leading-tight">{mode.label}</span>
                  <span className="mt-0.5 text-xs leading-snug text-muted-foreground">
                    {/* The composer must not become a way around the policy
                        engine, so a blocked mode points at Access the way the
                        Tools page does. */}
                    {isBlocked ? `Turn on ${mode.capability} in Access` : mode.hint}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
