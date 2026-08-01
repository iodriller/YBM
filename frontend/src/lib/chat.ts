import type { TaskRecord } from "@/lib/api"

/**
 * Ports admin_streamlit.py's _extract_last_output / _chat_answer_text
 * exactly, so the web console's chat behaves identically to the Streamlit
 * one it replaces. Keep these two in sync with the Python originals until
 * Streamlit is retired (docs/UI_REWRITE_PLAN.md §19).
 */

interface LastToolResult {
  output?: {
    stdout?: string
    summary?: string
    response?: string
    content?: string
    text?: string
    result?: string
  }
  error_message?: string
}

export function extractLastOutput(task: TaskRecord): string | null {
  const meta = task.metadata
  const result = (meta.last_tool_result ?? {}) as LastToolResult
  const out = result.output ?? {}
  const text =
    out.stdout ?? out.summary ?? out.response ?? out.content ?? out.text ?? out.result
  if (text) return String(text)
  if (result.error_message) return `Error: ${result.error_message}`
  if (typeof meta.last_worker_error === "string") return `Error: ${meta.last_worker_error}`
  return null
}

export function chatAnswerText(task: TaskRecord): string {
  const meta = task.metadata
  if (typeof meta.synthesized_answer === "string" && meta.synthesized_answer) {
    return meta.synthesized_answer
  }
  const lastOutput = extractLastOutput(task)
  switch (task.status) {
    case "completed":
      return lastOutput ?? "Done."
    case "failed":
      return lastOutput ?? "Failed."
    case "blocked":
      return lastOutput ?? "Blocked."
    case "awaiting_approval":
      return "Waiting for approval - see Pending Approvals above."
    case "clarifying":
      return typeof meta.clarifying_question === "string"
        ? meta.clarifying_question
        : "Needs more information."
    default:
      return lastOutput ?? `Working... (${task.status || "received"})`
  }
}

export function isTerminal(status: TaskRecord["status"]): boolean {
  return status === "completed" || status === "failed" || status === "cancelled"
}
