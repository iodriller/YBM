import { useState } from "react"
import { toast } from "sonner"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/access/ConfirmDialog"
import { AUDIT_CATEGORIES, ApiError } from "@/lib/api"
import { useAudit, useClearAudit } from "@/lib/queries"

/** Level 2/Advanced. Ports Streamlit's `_render_audit`. */
export function AuditViewerCard() {
  const [category, setCategory] = useState<string>("all")
  const [q, setQ] = useState("")
  const [confirmClear, setConfirmClear] = useState(false)
  const { data, isPending, isError, error } = useAudit({
    category: category === "all" ? undefined : category,
    q: q.trim() || undefined,
    limit: 50,
  })
  const clearAudit = useClearAudit()

  const events = data?.events ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit</CardTitle>
        <CardDescription>Every policy decision, config change, and tool call this instance has recorded.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={category} onValueChange={(v) => v && setCategory(v)}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              {AUDIT_CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {c.replace(/_/g, " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
          <span className="text-xs text-muted-foreground">Showing {events.length} events</span>
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto"
            disabled={events.length === 0 && !data}
            onClick={() => setConfirmClear(true)}
          >
            Clear audit
          </Button>
        </div>

        {isPending && <Skeleton className="h-32 w-full" />}
        {isError && <p className="text-sm text-destructive">{error.message}</p>}
        {!isPending && !isError && events.length === 0 && (
          <p className="text-sm text-muted-foreground">No audit events found.</p>
        )}

        <div className="flex max-h-96 flex-col gap-1 overflow-y-auto">
          {events.map((event) => (
            <details key={event.id} className="rounded-md border border-border p-2 text-sm">
              <summary className="cursor-pointer">
                <span className="text-xs text-muted-foreground">{event.formatted_time}</span>{" "}
                <span className="font-medium">{event.title}</span>
              </summary>
              <p className="mt-1 text-xs text-muted-foreground">{event.summary}</p>
              <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 font-mono text-xs">
                {JSON.stringify(event.details, null, 2)}
              </pre>
            </details>
          ))}
        </div>
      </CardContent>

      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear all audit history?"
        description="Permanently deletes every recorded audit event for this instance. This cannot be undone."
        confirmLabel="Clear audit"
        pending={clearAudit.isPending}
        onConfirm={() => {
          clearAudit.mutate(undefined, {
            onSuccess: (result) => toast.success(`Deleted ${result.deleted_audit_events} audit events.`),
            onError: (err) => toast.error(err instanceof ApiError ? err.message : "Could not clear audit history."),
          })
        }}
      />
    </Card>
  )
}
