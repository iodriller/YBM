import { useState } from "react"
import { toast } from "sonner"
import { ChevronDown, ChevronUp, Fingerprint, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ConfirmDialog } from "@/components/access/ConfirmDialog"
import { ApiError, type Skill } from "@/lib/api"
import { useUninstallSkill } from "@/lib/queries"
import { formatRelativeTime } from "@/lib/time"

/**
 * One installed skill (docs/UI_UX_AUDIT.md Phase 5): what it's for, which
 * tools its instructions reference (permission labels - inferred unless
 * the manifest declared them explicitly), and uninstall. A skill has no
 * execution capability of its own (tools/skills.py) - these labels are
 * informational, not an enforced permission grant.
 */
export function SkillCard({ skill }: { skill: Skill }) {
  const [expanded, setExpanded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const uninstall = useUninstallSkill()

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-card p-3 shadow-sm transition-colors hover:border-primary/30">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="truncate font-semibold">{skill.name}</h3>
            <Badge variant="outline">v{skill.version}</Badge>
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{skill.description}</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label={`Uninstall ${skill.name}`} onClick={() => setConfirmDelete(true)}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>

      {skill.tools.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {skill.tools_declared ? "Declared tools:" : "References:"}
          </span>
          {skill.tools.map((tool) => (
            <Badge key={tool} variant="secondary" className="font-mono text-[10px]">
              {tool}
            </Badge>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1 self-start text-xs font-medium text-primary hover:underline"
      >
        {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        {expanded ? "Hide instructions" : "Show instructions"}
      </button>
      {expanded && (
        <pre className="max-h-64 overflow-auto rounded-lg bg-muted p-2.5 font-mono text-xs whitespace-pre-wrap">
          {skill.body}
        </pre>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {skill.modified_at != null && <span>Updated {formatRelativeTime(new Date(skill.modified_at * 1000).toISOString())}</span>}
        <span className="inline-flex items-center gap-1">
          <Fingerprint className="size-3" />
          {skill.content_hash}
        </span>
        <span>{skill.size_bytes} bytes</span>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Uninstall ${skill.name}?`}
        description="Removes its manifest file. YBM can no longer read this skill's instructions until it's reinstalled."
        confirmLabel="Uninstall"
        pending={uninstall.isPending}
        onConfirm={() => {
          uninstall.mutate(skill.name, {
            onSuccess: () => toast.success(`Uninstalled ${skill.name}.`),
            onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not uninstall the skill."),
          })
        }}
      />
    </div>
  )
}
