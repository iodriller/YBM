import { useMemo } from "react"
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import type { ToolInvocation } from "@/lib/api"

/**
 * Level 2/Advanced trace view (docs/UI_REWRITE_PLAN.md §7/§12.2) - the
 * actual reason a node UI belongs in this app: `call_tools_parallel` and
 * `delegate` made execution non-linear, and a flat timeline cannot show
 * "these ran at once" or "this spawned a sub-agent." Grounded in the
 * origin/parent_step_id correlation added in Phase 0.6 specifically so
 * this graph would be a faithful reconstruction, not an inferred guess.
 *
 * Deliberately a *lane* layout, not a full precision tree: each distinct
 * origin gets its own vertical lane, ordered left-to-right by when that
 * group first started, with nodes chained chronologically within a lane.
 * What it does NOT do (disclosed, not silently missing): draw an edge from
 * a parallel/subagent lane back to the exact parent step that spawned it -
 * that step lives in operator_history, not tool_invocations, and wiring
 * the two together is left for a future pass rather than guessed at here.
 */
const LANE_WIDTH = 260
const ROW_HEIGHT = 90

interface GroupInfo {
  key: string
  label: string
  invocations: ToolInvocation[]
}

function classifyOrigin(origin: string | undefined): { key: string; label: string } {
  if (!origin || origin === "operator") return { key: "operator", label: "Main sequence" }
  if (origin.startsWith("subagent:")) {
    return {
      key: origin,
      label: origin.includes("/parallel_batch:") ? "Sub-task — parallel batch" : "Delegated sub-task",
    }
  }
  if (origin.startsWith("parallel_batch:")) return { key: origin, label: "Parallel batch" }
  return { key: origin, label: origin }
}

function buildGraph(invocations: ToolInvocation[]): { nodes: Node[]; edges: Edge[] } {
  const groups = new Map<string, GroupInfo>()
  for (const invocation of invocations) {
    const origin = (invocation.request?.origin as string | undefined) ?? undefined
    const { key, label } = classifyOrigin(origin)
    const group = groups.get(key) ?? { key, label, invocations: [] }
    group.invocations.push(invocation)
    groups.set(key, group)
  }

  const orderedGroups = [...groups.values()].sort((a, b) => {
    const aFirst = a.invocations[0]?.created_at ?? ""
    const bFirst = b.invocations[0]?.created_at ?? ""
    // "operator" always leads even if timestamps tie, since it's the spine
    // every other lane branches from.
    if (a.key === "operator") return -1
    if (b.key === "operator") return 1
    return aFirst.localeCompare(bFirst)
  })

  const nodes: Node[] = []
  const edges: Edge[] = []

  orderedGroups.forEach((group, laneIndex) => {
    const sorted = [...group.invocations].sort((a, b) => a.created_at.localeCompare(b.created_at))
    sorted.forEach((invocation, rowIndex) => {
      const status = invocation.status
      const failed = status === "failed" || status === "denied" || status === "timeout"
      nodes.push({
        id: invocation.id,
        position: { x: laneIndex * LANE_WIDTH, y: rowIndex * ROW_HEIGHT + (laneIndex === 0 ? 0 : 40) },
        data: {
          label: (
            <div className="flex flex-col gap-0.5 text-left">
              {rowIndex === 0 && (
                <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {group.label}
                </span>
              )}
              <span className="font-mono text-xs">{invocation.tool_name}</span>
              <span className={failed ? "text-[10px] text-destructive" : "text-[10px] text-muted-foreground"}>
                {status}
              </span>
            </div>
          ),
        },
        style: {
          border: failed ? "1px solid var(--destructive)" : "1px solid var(--border)",
          background: "var(--card)",
          borderRadius: 8,
          padding: 8,
          width: LANE_WIDTH - 40,
        },
      })
      if (rowIndex > 0) {
        edges.push({
          id: `${sorted[rowIndex - 1].id}->${invocation.id}`,
          source: sorted[rowIndex - 1].id,
          target: invocation.id,
        })
      }
    })
  })

  return { nodes, edges }
}

export function TraceGraph({ invocations }: { invocations: ToolInvocation[] }) {
  const { nodes, edges } = useMemo(() => buildGraph(invocations), [invocations])

  if (invocations.length === 0) {
    return <p className="text-sm text-muted-foreground">No tool calls recorded for this task.</p>
  }

  return (
    <div className="h-96 w-full overflow-hidden rounded-md border border-border">
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
