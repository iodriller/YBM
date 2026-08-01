import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useSettingsSummary } from "@/lib/queries"

/**
 * Level 2/Advanced, read-only. There is no admin write endpoint for MCP
 * server config today (config.py's MCPConfig has no admin.py route at
 * all) - editing would be genuinely new backend surface, so this pass
 * only surfaces what's already configured in config.yaml. Server `env`
 * values are never sent to this client in the first place (see the
 * backend fix in this same phase) - only the env var *names* are shown,
 * same "list the key, never the value" invariant as the secret vault.
 */
export function MCPServersCard() {
  const { data, isPending } = useSettingsSummary()

  if (isPending || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>MCP servers</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    )
  }

  const mcp = data.config.mcp
  const servers = Object.entries(mcp.servers)

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP servers</CardTitle>
        <CardDescription>
          {mcp.enabled ? "Enabled" : "Disabled"} · read-only here — edit config.yaml to change.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {servers.length === 0 && <p className="text-sm text-muted-foreground">No MCP servers configured.</p>}
        {servers.map(([name, server]) => (
          <div key={name} className="flex flex-col gap-1 rounded-md border border-border p-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-medium">{name}</span>
              <Badge variant={server.enabled ? "secondary" : "outline"}>{server.enabled ? "enabled" : "disabled"}</Badge>
              <Badge variant="outline">{server.risk_level}</Badge>
            </div>
            <p className="font-mono text-xs text-muted-foreground">
              {server.command} {server.args.join(" ")}
            </p>
            <p className="text-xs text-muted-foreground">
              capability: {server.capability}
              {server.env_keys.length > 0 && ` · env: ${server.env_keys.join(", ")}`}
              {server.disabled_tools.length > 0 && ` · disabled tools: ${server.disabled_tools.join(", ")}`}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
