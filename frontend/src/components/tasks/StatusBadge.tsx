import { Badge } from "@/components/ui/badge"
import type { TaskStatus } from "@/lib/api"

const VARIANT_BY_STATUS: Record<TaskStatus, "default" | "secondary" | "destructive" | "outline"> = {
  received: "outline",
  interpreting: "secondary",
  clarifying: "secondary",
  planned: "secondary",
  awaiting_approval: "secondary",
  awaiting_external: "secondary",
  running: "secondary",
  paused: "outline",
  retrying: "secondary",
  blocked: "destructive",
  completed: "default",
  cancelled: "outline",
  failed: "destructive",
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  return <Badge variant={VARIANT_BY_STATUS[status]}>{status.replace(/_/g, " ")}</Badge>
}
