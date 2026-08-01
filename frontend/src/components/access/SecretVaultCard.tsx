import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/access/ConfirmDialog"
import { ApiError } from "@/lib/api"
import { useDeleteSecret, useSecrets, useSetSecret } from "@/lib/queries"

/**
 * Secret vault (docs/UI_REWRITE_PLAN.md §13) - list `service.key`, never
 * values (storage/secrets.py never returns them either), add, delete.
 * Ports Streamlit's admin_streamlit.py `_render_secrets_config`.
 */
export function SecretVaultCard() {
  const { data, isPending, isError, error } = useSecrets()
  const setSecret = useSetSecret()
  const deleteSecret = useDeleteSecret()
  const [service, setService] = useState("")
  const [key, setKey] = useState("")
  const [value, setValue] = useState("")
  const [pendingDelete, setPendingDelete] = useState<{ service: string; key: string } | null>(null)

  function handleDelete() {
    if (!pendingDelete) return
    const target = pendingDelete
    deleteSecret.mutate(target, {
      onSuccess: () => toast.success(`Deleted ${target.service}.${target.key}.`),
      onError: (err) => {
        toast.error(err instanceof ApiError ? err.message : "Could not delete the secret.")
      },
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Secret vault</CardTitle>
        <CardDescription>
          Store credentials here so http.request can inject them by reference — the value never
          appears in an LLM prompt or in this page&apos;s traffic.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {isPending && <Skeleton className="h-16 w-full" />}

        {isError && (
          <Alert variant="destructive">
            <AlertTitle>Couldn&apos;t load secrets</AlertTitle>
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        )}

        {data && !data.available && (
          <Alert variant="destructive">
            <AlertTitle>Vault not initialized</AlertTitle>
            <AlertDescription>
              <span className="font-mono">{data.key_env}</span> is not set — run{" "}
              <span className="font-mono">ybm setup</span> to generate it, then restart the backend.
            </AlertDescription>
          </Alert>
        )}

        {data?.available && (
          <>
            {Object.keys(data.services).length === 0 && (
              <p className="text-sm text-muted-foreground">No secrets stored yet.</p>
            )}
            <div className="flex flex-col gap-1">
              {Object.entries(data.services).flatMap(([svc, keys]) =>
                keys.map((k) => (
                  <div key={`${svc}.${k}`} className="flex items-center justify-between gap-2 text-sm">
                    <span className="font-mono">
                      {svc}.{k}
                    </span>
                    <Button variant="outline" size="sm" onClick={() => setPendingDelete({ service: svc, key: k })}>
                      Delete
                    </Button>
                  </div>
                )),
              )}
            </div>

            <form
              className="flex flex-col gap-2 border-t border-border pt-3"
              onSubmit={(event) => {
                event.preventDefault()
                const svc = service.trim()
                const k = key.trim()
                if (!svc || !k || !value.trim()) return
                setSecret.mutate(
                  { service: svc, key: k, value },
                  {
                    onSuccess: () => {
                      toast.success(`Saved ${svc}.${k}.`)
                      setService("")
                      setKey("")
                      setValue("")
                    },
                    onError: (err) => {
                      toast.error(err instanceof ApiError ? err.message : "Could not save the secret.")
                    },
                  },
                )
              }}
            >
              <p className="text-xs font-medium text-muted-foreground">Add / replace a secret</p>
              <div className="flex gap-2">
                <Input placeholder="Service (e.g. openai)" value={service} onChange={(e) => setService(e.target.value)} />
                <Input placeholder="Key (e.g. api_key)" value={key} onChange={(e) => setKey(e.target.value)} />
              </div>
              <Input
                type="password"
                placeholder="Value"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
              <Button
                type="submit"
                size="sm"
                className="self-start"
                disabled={setSecret.isPending || !service.trim() || !key.trim() || !value.trim()}
              >
                Save secret
              </Button>
            </form>
          </>
        )}
      </CardContent>

      <ConfirmDialog
        open={pendingDelete != null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null)
        }}
        title={pendingDelete ? `Delete ${pendingDelete.service}.${pendingDelete.key}?` : "Delete secret?"}
        description="Any tool configured to use this credential by reference will fail until it's re-added."
        confirmLabel="Delete"
        pending={deleteSecret.isPending}
        onConfirm={handleDelete}
      />
    </Card>
  )
}
