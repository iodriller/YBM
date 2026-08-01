import { CircleCheck, CircleDashed, CirclePause, CircleX, Clock3, ShieldAlert } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { TaskStatus } from "@/lib/api"
import { cn } from "@/lib/utils"

const STATE: Record<TaskStatus, { className: string; icon: typeof CircleDashed }> = {
  received: { className: "bg-info/10 text-info", icon: Clock3 },
  interpreting: { className: "bg-info/10 text-info", icon: CircleDashed },
  clarifying: { className: "bg-warning/10 text-warning", icon: Clock3 },
  planned: { className: "bg-info/10 text-info", icon: CircleDashed },
  awaiting_approval: { className: "bg-warning/10 text-warning", icon: ShieldAlert },
  awaiting_external: { className: "bg-warning/10 text-warning", icon: Clock3 },
  running: { className: "bg-info/10 text-info", icon: CircleDashed },
  paused: { className: "bg-muted text-muted-foreground", icon: CirclePause },
  retrying: { className: "bg-warning/10 text-warning", icon: CircleDashed },
  blocked: { className: "bg-destructive/10 text-destructive", icon: ShieldAlert },
  completed: { className: "bg-success/10 text-success", icon: CircleCheck },
  cancelled: { className: "bg-muted text-muted-foreground", icon: CircleX },
  failed: { className: "bg-destructive/10 text-destructive", icon: CircleX },
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  const state = STATE[status]
  const Icon = state.icon
  return (
    <Badge variant="outline" className={cn("gap-1 border-transparent capitalize", state.className)}>
      <Icon className={cn("size-3", ["running", "interpreting", "retrying"].includes(status) && "animate-pulse")} />
      {status.replace(/_/g, " ")}
    </Badge>
  )
}
