import { Button } from "@/components/ui/button"

/**
 * Shown on every page while no model is configured.
 *
 * Skipping the wizard used to be impossible - `showWizard` was driven purely
 * by `onboarding_complete`, so a skip re-rendered the wizard forever. Now a
 * skip really skips, which means the console can be reached in a state where
 * nothing can answer. This is what keeps that from being a silent dead end:
 * it states the consequence and offers the way back, on every page, and is
 * deliberately not dismissible because the condition it reports is not
 * cosmetic - the product cannot do its job until it is resolved.
 */
export function SetupIncompleteBanner({ onResume }: { onResume: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2">
      <p className="text-sm">
        <span className="font-medium">No model configured yet.</span>{" "}
        <span className="text-muted-foreground">
          YBM has nothing to think with, so it cannot answer messages.
        </span>
      </p>
      <Button size="sm" variant="outline" onClick={onResume}>
        Finish setup
      </Button>
    </div>
  )
}
