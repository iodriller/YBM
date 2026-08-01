import type { TaskStatus } from "@/lib/api"

/** Mirrors admin_streamlit.py's `_action_disabled` gating (also enforced
 * server-side by apply_task_signal - these sets only avoid a pointless
 * click, not a security boundary). Shared between the Trace page's action
 * buttons and the Tasks page's Live Activity panel. */
export const PAUSABLE = new Set<TaskStatus>(["running", "retrying"])
export const RESUMABLE = new Set<TaskStatus>(["paused"])
export const CANCELLABLE = new Set<TaskStatus>([
  "received", "interpreting", "clarifying", "planned",
  "awaiting_approval", "awaiting_external", "running", "paused", "retrying",
])
