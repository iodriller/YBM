import { LLMSettingsCard } from "@/components/settings/LLMSettingsCard"
import { TelegramSettingsCard } from "@/components/settings/TelegramSettingsCard"
import { VSCodeSettingsCard } from "@/components/settings/VSCodeSettingsCard"
import { WorkspaceSettingsCard } from "@/components/settings/WorkspaceSettingsCard"
import { ComputerUseSettingsCard } from "@/components/settings/ComputerUseSettingsCard"
import { MCPServersCard } from "@/components/settings/MCPServersCard"
import { DiagnosticsCard } from "@/components/settings/DiagnosticsCard"
import { AuditViewerCard } from "@/components/settings/AuditViewerCard"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { useAdvancedMode } from "@/lib/advanced-mode"

/**
 * docs/UI_REWRITE_PLAN.md §14. Level 1: LLM + Telegram. Level 2/Advanced
 * adds every adapter field, MCP servers (read-only), diagnostics, and the
 * audit viewer. **A1/A2/A3 (per-role model, per-role prompt override,
 * delegate presets) and D4 (OpenTelemetry export) are not built** - see
 * the plan doc for why (real new backend machinery, same reasoning that
 * scoped D2 out of Phase 2).
 */
export function SettingsPage({ onRerunWizard }: { onRerunWizard: () => void }) {
  const { advanced } = useAdvancedMode()

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 [&>*]:shrink-0">
      <div>
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">LLM, channels, and adapters this instance uses.</p>
      </div>

      <LLMSettingsCard />
      <TelegramSettingsCard />

      {advanced && (
        <>
          <VSCodeSettingsCard />
          <WorkspaceSettingsCard />
          <ComputerUseSettingsCard />
          <MCPServersCard />
          <DiagnosticsCard />
          <AuditViewerCard />
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Setup wizard</CardTitle>
          <CardDescription>Re-run the first-run wizard (pick a brain, pick a face).</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" size="sm" onClick={onRerunWizard}>
            Re-run setup wizard
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
