import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { flexRender, getCoreRowModel, useReactTable, createColumnHelper } from "@tanstack/react-table"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { StatusBadge } from "@/components/tasks/StatusBadge"
import { ActiveTasksPanel } from "@/components/tasks/ActiveTasksPanel"
import { useTasks } from "@/lib/queries"
import type { TaskRecord, TaskStatus } from "@/lib/api"

const STATUS_OPTIONS: (TaskStatus | "all")[] = [
  "all", "received", "interpreting", "clarifying", "planned", "awaiting_approval",
  "awaiting_external", "running", "paused", "retrying", "blocked", "completed",
  "cancelled", "failed",
]

const columnHelper = createColumnHelper<TaskRecord>()

export function TasksPage() {
  const { data, isPending, isError, error } = useTasks(100)
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<TaskStatus | "all">("all")
  const [search, setSearch] = useState("")

  // Client-side filter/search: the backend's /api/tasks has no status/query
  // params today (docs/UI_REWRITE_PLAN.md §12.1), and a personal-scale task
  // history (dozens to low hundreds) makes filtering the fetched page
  // cheap enough that adding server-side filtering would be solving a
  // problem that doesn't exist yet - the same reasoning knowledge_base.py
  // already applies to its own re-index-on-every-call design.
  const filtered = useMemo(() => {
    const tasks = data?.tasks ?? []
    const bySearch = search.trim()
      ? tasks.filter((t) => t.objective.toLowerCase().includes(search.trim().toLowerCase()))
      : tasks
    return statusFilter === "all" ? bySearch : bySearch.filter((t) => t.status === statusFilter)
  }, [data, statusFilter, search])

  const columns = useMemo(
    () => [
      columnHelper.accessor("objective", {
        header: "Objective",
        cell: (info) => <span className="line-clamp-1">{info.getValue()}</span>,
      }),
      columnHelper.accessor("status", {
        header: "Status",
        cell: (info) => <StatusBadge status={info.getValue()} />,
      }),
      columnHelper.accessor("created_at", {
        header: "Created",
        cell: (info) => new Date(info.getValue()).toLocaleString(),
      }),
    ],
    [],
  )

  const table = useReactTable({
    data: filtered,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 [&>*]:shrink-0">
      <ActiveTasksPanel />

      <div className="flex items-center gap-2">
        <Input
          placeholder="Search objectives..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-sm"
        />
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as TaskStatus | "all")}>
          <SelectTrigger className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((s) => (
              <SelectItem key={s} value={s}>
                {s === "all" ? "All statuses" : s.replace(/_/g, " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>Couldn't load tasks</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {isPending && <Skeleton className="h-64 w-full" />}

      {!isPending && !isError && filtered.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {data?.tasks.length ? "No tasks match this filter." : "No tasks yet."}
        </p>
      )}

      {!isPending && filtered.length > 0 && (
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id}>
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                className="cursor-pointer"
                onClick={() => navigate(`/tasks/${row.original.id}`)}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
