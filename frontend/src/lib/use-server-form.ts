import { useEffect, useState } from "react"

/**
 * Seeds local edit-form state from server data exactly once, the first
 * time that data becomes available - used by every Settings form (LLM,
 * Telegram, VS Code, Workspace, Computer Use), each of which edits a
 * local draft rather than the live query data directly (typing in a field
 * shouldn't fight a background refetch). Call `reset` after a successful
 * save to re-seed from the now-current server value.
 */
export function useServerForm<TSource, TForm>(
  source: TSource | undefined,
  derive: (source: TSource) => TForm,
): [TForm | null, (form: TForm | null) => void, () => void] {
  const [form, setForm] = useState<TForm | null>(null)

  useEffect(() => {
    if (form == null && source != null) setForm(derive(source))
  }, [form, source, derive])

  return [form, setForm, () => setForm(null)]
}
