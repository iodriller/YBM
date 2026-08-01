import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useSettingsSummary } from "@/lib/queries"

/**
 * Level 2/Advanced, read-only. A deliberately smaller replacement for
 * Streamlit's Diagnostics tab (`_render_diagnostics`): that tab also
 * dumped the full tool registry as a table and the entire raw summary
 * JSON. Deep JSON inspection is already covered by the "Effective Config"
 * idea from the Streamlit console - service health, database state, and
 * config warnings are the load-bearing parts, so this stays scoped to
 * those rather than reproducing every panel 1:1.
 */
export function DiagnosticsCard() {
  const { data, isPending } = useSettingsSummary()

  if (isPending || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Diagnostics</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Diagnostics</CardTitle>
        <CardDescription>Config file: {data.admin.config_file}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {data.warnings.length > 0 && (
          <Alert variant="destructive">
            <AlertTitle>Configuration warnings</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {data.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Services</p>
          <div className="flex flex-col gap-1">
            {data.services.items.map((item) => (
              <div key={item.name} className="flex items-center justify-between gap-2 text-sm">
                <span>{item.name.replace(/_/g, " ")}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{item.message}</span>
                  <Badge variant={!item.expected ? "outline" : item.ok ? "secondary" : "destructive"}>
                    {!item.expected ? "not expected" : item.status}
                  </Badge>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Database</p>
          <p className="font-mono text-xs text-muted-foreground">{data.database.database_url}</p>
          <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
            {Object.entries(data.database.table_counts).map(([table, count]) => (
              <span key={table} className="text-muted-foreground">
                {table}: <span className="font-medium text-foreground">{count}</span>
              </span>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
