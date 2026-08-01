import { z } from "zod"

/**
 * Client for the existing FastAPI admin API (`/admin/api/*`,
 * backend/src/agent_control/admin.py). The backend returns untyped Python
 * dicts, so every response is parsed with a Zod schema at the boundary -
 * see docs/UI_REWRITE_PLAN.md §15.4 ("Contract tests - the drift guard"):
 * a backend shape change should fail loudly here, not render `undefined`.
 *
 * Same-origin only, by design (docs/UI_REWRITE_PLAN.md §4) - this app is
 * always served from the same origin as the backend (Vite proxy in dev,
 * static mount in prod), so requests are plain relative fetches with no
 * base URL to configure.
 */

const ADMIN_TOKEN_STORAGE_KEY = "ybm-admin-token"

// In-memory only, never localStorage - limits XSS blast radius (plan §4).
// Seeded synchronously at module load (before any component renders or
// query fires), checking two sources in order:
//  1. A `?token=` URL param - how `ybm start`'s auto-opened browser tab
//     carries the auto-generated AGENT_ADMIN_TOKEN on a fresh install, so
//     the one-click flow never hits TokenEntryScreen at all. Stripped from
//     the URL immediately (history.replaceState) so it never lingers in
//     browser history or gets shared via a copied link.
//  2. sessionStorage - so a page refresh during the same tab session
//     doesn't force re-entering the token; still clears on tab close,
//     unlike localStorage.
let adminToken: string | null = (() => {
  try {
    const url = new URL(window.location.href)
    const urlToken = url.searchParams.get("token")
    if (urlToken) {
      url.searchParams.delete("token")
      window.history.replaceState({}, "", url.pathname + url.search + url.hash)
      sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, urlToken)
      return urlToken
    }
    return sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
})()

export function setAdminToken(token: string): void {
  adminToken = token
  try {
    sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token)
  } catch {
    // sessionStorage unavailable (private mode, etc.) - token still works
    // for this page load, just won't survive a refresh.
  }
}

export function getAdminToken(): string | null {
  return adminToken
}

export class ApiError extends Error {
  status: number

  // Explicit field + assignment, not a constructor parameter property -
  // tsconfig's erasableSyntaxOnly (Node's native type-stripping mode)
  // disallows parameter properties since they emit real runtime code, not
  // just erasable type annotations.
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = "ApiError"
  }
}

async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers)
  if (adminToken) {
    headers.set("X-Agent-Control-Admin-Token", adminToken)
  }
  // FormData (file uploads) must keep the browser-generated multipart
  // boundary in its own Content-Type - forcing application/json here would
  // send the file as an unparseable body.
  if (init?.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json")
  }

  const response = await fetch(`/admin${path}`, { ...init, headers })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      // response body wasn't JSON - keep statusText
    }
    throw new ApiError(response.status, detail)
  }

  const data: unknown = await response.json()
  const parsed = schema.safeParse(data)
  if (!parsed.success) {
    // A contract violation: the backend returned a shape this client
    // doesn't understand. Surfacing this loudly (not silently rendering
    // `undefined`) is the whole point of parsing at the boundary.
    console.error(`API contract mismatch for ${path}:`, parsed.error)
    throw new ApiError(response.status, `Unexpected response shape from ${path}`)
  }
  return parsed.data
}

// ---- Task ------------------------------------------------------------

export const TaskStatusSchema = z.enum([
  "received",
  "interpreting",
  "clarifying",
  "planned",
  "awaiting_approval",
  "awaiting_external",
  "running",
  "paused",
  "retrying",
  "blocked",
  "completed",
  "cancelled",
  "failed",
])
export type TaskStatus = z.infer<typeof TaskStatusSchema>

export const ArtifactSchema = z.object({
  id: z.string(),
  task_id: z.string().nullable(),
  type: z.string(),
  uri: z.string().nullable(),
  content_preview: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
})
export type Artifact = z.infer<typeof ArtifactSchema>

/**
 * A plain <a href>/window.open navigation can't attach the
 * X-Agent-Control-Admin-Token header apiFetch normally sends - the admin
 * token has to ride in the URL instead, which require_admin's own
 * query_params.get("token") fallback already accepts (docs/UI_UX_AUDIT.md
 * Phase 8: artifact download).
 */
export function artifactDownloadUrl(artifactId: string, options: { inline?: boolean } = {}): string {
  const params = new URLSearchParams()
  const token = getAdminToken()
  if (token) params.set("token", token)
  if (options.inline) params.set("inline", "true")
  const query = params.toString()
  return `/admin/api/artifacts/${encodeURIComponent(artifactId)}/download${query ? `?${query}` : ""}`
}

export const TaskRecordSchema = z.object({
  id: z.string(),
  objective: z.string(),
  status: TaskStatusSchema,
  conversation_id: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  metadata: z.record(z.string(), z.unknown()),
  // Only populated by the chat endpoints (docs/UI_UX_AUDIT.md Phase 1's
  // artifact cards) - optional so /api/tasks and /api/tasks/{id}/trace,
  // which don't send this key, still parse.
  artifacts: z.array(ArtifactSchema).optional(),
})
export type TaskRecord = z.infer<typeof TaskRecordSchema>

// ---- Chat --------------------------------------------------------------

const ChatMessagesResponseSchema = z.object({
  conversation_id: z.string(),
  tasks: z.array(TaskRecordSchema),
})

const ChatSendResponseSchema = z.object({
  conversation_id: z.string(),
  task: TaskRecordSchema,
})

export function listChatMessages(limit = 50) {
  return apiFetch(`/api/chat/messages?limit=${limit}`, ChatMessagesResponseSchema)
}

export function sendChatMessage(text: string, attachmentIds: string[] = []) {
  return apiFetch("/api/chat/messages", ChatSendResponseSchema, {
    method: "POST",
    body: JSON.stringify({ text, attachment_ids: attachmentIds }),
  })
}

const ChatAttachmentUploadResponseSchema = z.object({
  artifact_id: z.string(),
  file_name: z.string().nullable(),
  size_bytes: z.number(),
})
export type ChatAttachmentUpload = z.infer<typeof ChatAttachmentUploadResponseSchema>

export function uploadChatAttachment(file: File) {
  const form = new FormData()
  form.append("file", file)
  return apiFetch("/api/chat/attachments", ChatAttachmentUploadResponseSchema, {
    method: "POST",
    body: form,
  })
}

// ---- Bootstrap -----------------------------------------------------------

export const BootstrapResponseSchema = z.object({
  token_required: z.boolean(),
  onboarding_complete: z.boolean(),
  llm_reachable: z.boolean(),
  version: z.string(),
})
export type Bootstrap = z.infer<typeof BootstrapResponseSchema>

export function getBootstrap() {
  return apiFetch("/api/bootstrap", BootstrapResponseSchema)
}

// ---- Setup detection (docs/UI_REWRITE_PLAN.md's first-run wizard) --------
//
// Real detection so the wizard asks real questions instead of blind ones -
// an actual Ollama model list, and "already configured" booleans (never
// values) for LocalDeploy/a cloud key/Telegram, matching bootstrap.py's
// _prompt_llm_choice/_prompt_telegram_choice CLI logic exactly so the
// browser wizard and the (still-available, headless-friendly) `ybm onboard`
// CLI wizard make the same decisions from the same signals.

export const SetupDetectResponseSchema = z.object({
  ollama: z.object({ available: z.boolean(), models: z.array(z.string()) }),
  localdeploy_root_present: z.boolean(),
  openai_key_present: z.boolean(),
  telegram_token_present: z.boolean(),
  current_llm_profile: z.string(),
  llm_configured: z.boolean(),
  telegram_enabled: z.boolean(),
})
export type SetupDetect = z.infer<typeof SetupDetectResponseSchema>

export function getSetupDetect() {
  return apiFetch("/api/setup/detect", SetupDetectResponseSchema)
}

// ---- VS Code bridge status -------------------------------------------

// admin.py's _vscode_summary() - tools/vscode_bridge.py's VSCodeHeartbeat/
// VSCodeWorkspaceState. terminal_outputs is deliberately left untyped
// (passthrough) - this client never reads it.
const VSCodeHeartbeatSchema = z.object({
  instance_id: z.string(),
  workspace_folders: z.array(z.string()),
  active_file: z.string().nullable(),
  diagnostics_count: z.number().int(),
  observed_at: z.string(),
})

const VSCodeWorkspaceStateSchema = z.object({
  instance_id: z.string(),
  workspace_folders: z.array(z.string()),
  active_file: z.string().nullable(),
  open_files: z.array(z.string()),
  diagnostics_count: z.number().int(),
  observed_at: z.string(),
}).passthrough()

export const VSCodeStatusSchema = z.object({
  connected: z.boolean(),
  status: z.enum(["connected", "stale", "waiting"]),
  last_seen_at: z.string().nullable(),
  last_seen_age_seconds: z.number().int().nullable(),
  heartbeat: VSCodeHeartbeatSchema.nullable(),
  state: VSCodeWorkspaceStateSchema.nullable(),
  pending_terminal_commands: z.number().int(),
}).passthrough()
export type VSCodeStatus = z.infer<typeof VSCodeStatusSchema>

// ---- Summary (health indicator) ------------------------------------------

// /api/summary returns a much larger payload (config, audit, schedules,
// tool registry, ...) - only the fields this client actually reads are
// declared, via .passthrough(), so an unrelated field being added upstream
// doesn't break this schema. Fields it *does* read are still fully validated.
const SummarySchema = z.object({
  status: z.string(),
  tasks: z.array(TaskRecordSchema),
  task_pagination: z.object({ total: z.number().int() }),
  vscode: VSCodeStatusSchema,
  warnings: z.array(z.string()),
  config: z.object({
    llm: z.object({ default_profile: z.string() }).passthrough(),
    adapters: z.object({
      workspace: z.object({ enabled: z.boolean(), root_dir: z.string() }).passthrough(),
    }).passthrough(),
  }).passthrough(),
  database: z.object({ database_url: z.string(), path: z.string() }).passthrough(),
  integrations: z.object({
    telegram: z.object({ enabled: z.boolean(), token_present: z.boolean() }).passthrough(),
    llm: z.object({ default_profile_configured: z.boolean() }).passthrough(),
  }).passthrough(),
}).passthrough()
export type Summary = z.infer<typeof SummarySchema>

// ACTIVE_STATUSES mirrors admin_streamlit.py's own constant of the same
// name - kept in sync manually since the backend doesn't expose "active"
// as a first-class concept, only individual task statuses.
export const ACTIVE_STATUSES = new Set<TaskStatus>([
  "received", "interpreting", "planned", "running",
  "retrying", "awaiting_approval", "awaiting_external",
])

export function getSummary(taskLimit = 5) {
  return apiFetch(`/api/summary?task_limit=${taskLimit}`, SummarySchema)
}

export function countActive(tasks: TaskRecord[]): number {
  return tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length
}

// ---- Approvals (Evidence Pack, docs/UI_REWRITE_PLAN.md §11) --------------

export const RiskLevelSchema = z.enum(["low", "medium", "high", "critical"])
export type RiskLevel = z.infer<typeof RiskLevelSchema>

const ApprovalStatusSchema = z.enum([
  "pending", "approved", "consumed", "rejected", "expired", "cancelled",
])

const ApprovalRequestSchema = z.object({
  id: z.string(),
  task_id: z.string(),
  // Capability is a large, evolving enum (~150 values) - a plain string
  // avoids this client needing to be kept in lockstep with every capability
  // the backend adds; RiskLevel and ApprovalStatus are small and stable
  // enough to validate exactly.
  capability: z.string(),
  risk_level: RiskLevelSchema,
  summary: z.string(),
  action_payload: z.record(z.string(), z.unknown()),
  status: ApprovalStatusSchema,
  expires_at: z.string(),
  created_at: z.string(),
})

const BlastRadiusSchema = z.object({
  files: z.array(z.string()),
  urls: z.array(z.string()),
  commands: z.array(z.string()),
})

const PendingApprovalItemSchema = z.object({
  approval: ApprovalRequestSchema,
  task_objective: z.string().nullable(),
  task_status: TaskStatusSchema.nullable(),
  capability_max_risk_level: RiskLevelSchema.nullable(),
  blast_radius: BlastRadiusSchema,
})
export type PendingApprovalItem = z.infer<typeof PendingApprovalItemSchema>

const PendingApprovalsResponseSchema = z.object({
  approvals: z.array(PendingApprovalItemSchema),
})

const ApprovalGrantSchema = z.object({
  id: z.string(),
  task_id: z.string(),
  tool_name: z.string(),
  capability: z.string(),
  granted_from_approval_id: z.string(),
  created_at: z.string(),
  expires_at: z.string(),
})

const DecideApprovalResponseSchema = z.object({
  approval: ApprovalRequestSchema.nullable(),
  grant: ApprovalGrantSchema.nullable(),
})

export function listPendingApprovals() {
  return apiFetch("/api/approvals", PendingApprovalsResponseSchema)
}

export type ApprovalDecision = "approve" | "reject" | "approve_for_task"

export function decideApproval(approvalId: string, decision: ApprovalDecision) {
  return apiFetch(`/api/approvals/${approvalId}/decide`, DecideApprovalResponseSchema, {
    method: "POST",
    body: JSON.stringify({ decision }),
  })
}

// Risk ordering for a plain-English "exceeds your configured ceiling"
// comparison in the Evidence Pack's Authority field - mirrors
// policy/engine.py's RISK_ORDER.
export const RISK_ORDER: Record<RiskLevel, number> = { low: 1, medium: 2, high: 3, critical: 4 }

// ---- Tasks + Trace (docs/UI_REWRITE_PLAN.md §12) --------------------------

const TaskListResponseSchema = z.object({
  tasks: z.array(TaskRecordSchema),
  pagination: z.object({
    limit: z.number().int(),
    offset: z.number().int(),
    total: z.number().int(),
    has_more: z.boolean(),
  }),
})

export function listTasks(limit = 50, offset = 0) {
  return apiFetch(`/api/tasks?limit=${limit}&offset=${offset}`, TaskListResponseSchema)
}

const ClearTaskHistoryResponseSchema = z.object({ deleted_tasks: z.number().int(), include_active: z.boolean() })

export function clearTaskHistory(includeActive: boolean) {
  return apiFetch(`/api/tasks?include_active=${includeActive}`, ClearTaskHistoryResponseSchema, {
    method: "DELETE",
  })
}

// One tool_invocations row (storage/repositories.py's ToolInvocationRepository.
// list_for_task) - request/result are the full serialized ToolCallRequest/
// ToolCallResult, deliberately loose (z.record) rather than exhaustively
// typed: every tool's input/output shape differs, and the trace view only
// ever reads a handful of well-known keys out of them (origin, tool_name,
// input, output, error_message, ...) via optional chaining, not the whole
// shape.
const ToolInvocationSchema = z.object({
  id: z.string(),
  task_id: z.string(),
  tool_name: z.string(),
  capability: z.string(),
  request: z.record(z.string(), z.unknown()).nullable(),
  result: z.record(z.string(), z.unknown()).nullable(),
  status: z.string(),
  created_at: z.string(),
  completed_at: z.string().nullable(),
})
export type ToolInvocation = z.infer<typeof ToolInvocationSchema>

// One operator_history entry (orchestration/worker.py) - the step-by-step
// record of what the Operator loop decided and did. output_summary/error/
// origin/parallel are all conditionally present depending on the step kind
// (see worker.py's various history.append() call sites), hence .optional()
// rather than .nullable() throughout - the key is sometimes absent, not
// present-with-null.
const OperatorHistoryEntrySchema = z.object({
  tool_name: z.string().nullable(),
  input: z.unknown().optional(),
  status: z.string(),
  output_summary: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  origin: z.string().optional(),
  parallel: z.boolean().optional(),
})
export type OperatorHistoryEntry = z.infer<typeof OperatorHistoryEntrySchema>

const TokenUsageSourceSchema = z.object({
  calls: z.number().int(),
  prompt_tokens: z.number().int().optional(),
  completion_tokens: z.number().int().optional(),
  total_tokens: z.number().int().optional(),
})

const TokenUsageSchema = z.object({
  calls: z.number().int(),
  prompt_tokens: z.number().int().optional(),
  completion_tokens: z.number().int().optional(),
  total_tokens: z.number().int().optional(),
  by_source: z.record(z.string(), TokenUsageSourceSchema).optional(),
  last_model: z.string().optional(),
})
export type TokenUsage = z.infer<typeof TokenUsageSchema>

const EvidenceItemSchema = z.object({
  value: z.string(),
  tool_name: z.string().nullable(),
  at: z.string().nullable(),
})

// build_task_trace's _trace_timeline() (admin.py): audit events and tool
// invocations merged and time-sorted - the full "what happened, in order"
// record, richer than operator_history (policy decisions, approvals,
// classification are audit events with no operator_history entry of
// their own).
export const TimelineItemSchema = z.object({
  at: z.string().nullable(),
  kind: z.enum(["audit", "tool"]),
  title: z.string().nullable(),
  summary: z.string().nullable(),
  actor: z.string().nullable(),
  details: z.record(z.string(), z.unknown()).nullable(),
})
export type TimelineItem = z.infer<typeof TimelineItemSchema>

const TaskTraceSchema = z.object({
  task: TaskRecordSchema,
  context: z.record(z.string(), z.unknown()),
  operator_history: z.array(OperatorHistoryEntrySchema),
  timeline: z.array(TimelineItemSchema),
  tool_invocations: z.array(ToolInvocationSchema),
  evidence: z.object({
    files: z.array(EvidenceItemSchema),
    urls: z.array(EvidenceItemSchema),
    commands: z.array(EvidenceItemSchema),
  }),
  approvals: z.array(ApprovalRequestSchema),
  artifacts: z.array(z.record(z.string(), z.unknown())),
  signals: z.array(z.record(z.string(), z.unknown())),
  audit: z.array(z.record(z.string(), z.unknown())),
})
export type TaskTrace = z.infer<typeof TaskTraceSchema>

export function getTaskTrace(taskId: string) {
  return apiFetch(`/api/tasks/${taskId}/trace`, TaskTraceSchema)
}

const ServiceContactedSchema = z.object({
  host: z.string().nullable(),
  tool_name: z.string().nullable(),
  at: z.string(),
})

const ToolUsageSummarySchema = z.object({
  tool_name: z.string(),
  calls: z.number().int(),
  succeeded: z.number().int(),
  failed: z.number().int(),
})

const ReceiptApprovalSchema = z.object({
  id: z.string(),
  capability: z.string(),
  risk_level: z.string(),
  status: z.string(),
  summary: z.string(),
})

export const TaskReceiptSchema = z.object({
  task_id: z.string(),
  objective: z.string(),
  status: TaskStatusSchema,
  result_summary: z.string().nullable(),
  changes: z.object({
    files: z.array(EvidenceItemSchema),
    urls: z.array(EvidenceItemSchema),
    commands: z.array(EvidenceItemSchema),
  }),
  tools_used: z.array(ToolUsageSummarySchema),
  services_contacted: z.array(ServiceContactedSchema),
  data_left_machine: z.boolean(),
  llm_left_machine: z.boolean(),
  approvals: z.array(ReceiptApprovalSchema),
  artifacts: z.array(ArtifactSchema),
  token_usage: TokenUsageSchema.partial(),
  duration_seconds: z.number(),
  uncertainties: z.array(z.string()),
  created_at: z.string(),
  updated_at: z.string(),
})
export type TaskReceipt = z.infer<typeof TaskReceiptSchema>

export function getTaskReceipt(taskId: string) {
  return apiFetch(`/api/tasks/${taskId}/receipt`, TaskReceiptSchema)
}

const TaskSignalResponseSchema = z.object({
  signal: z.record(z.string(), z.unknown()),
  task: TaskRecordSchema,
})

export function sendTaskSignal(taskId: string, signal: "pause" | "resume" | "cancel") {
  return apiFetch(`/api/tasks/${taskId}/signals`, TaskSignalResponseSchema, {
    method: "POST",
    body: JSON.stringify({ signal }),
  })
}

function tokenUsageOf(task: TaskRecord): TokenUsage | null {
  const parsed = TokenUsageSchema.safeParse(task.metadata.token_usage)
  return parsed.success ? parsed.data : null
}

export { tokenUsageOf }

// ---- Access (docs/UI_REWRITE_PLAN.md §13) ---------------------------------

export const CapabilityAccessModeSchema = z.enum([
  "off", "read_only", "write_access", "full_access",
])
export type CapabilityAccessMode = z.infer<typeof CapabilityAccessModeSchema>

const AccessModeOptionSchema = z.object({ value: z.string(), label: z.string() })

const CapabilityAccessSummarySchema = z.object({
  name: z.string(),
  label: z.string().nullable(),
  mode: CapabilityAccessModeSchema,
  capabilities: z.array(z.string()),
  options: z.array(AccessModeOptionSchema),
  requires_approval: z.boolean(),
})
export type CapabilityAccessSummary = z.infer<typeof CapabilityAccessSummarySchema>

// The raw per-capability policy (config.py's CapabilityPolicy) - Level 2/
// Advanced-only, read-only display (docs/UI_REWRITE_PLAN.md §13's "risk
// ceilings, scopes, allow/deny patterns"). There is no write endpoint for
// these individual fields, only the coarse access-mode groups below, so
// this client only ever reads them - see the Phase 4 notes in
// docs/UI_REWRITE_PLAN.md for why editing them was left out of this pass.
const CapabilityPolicySchema = z.object({
  enabled: z.boolean(),
  scopes: z.array(z.string()),
  requires_approval: z.boolean(),
  max_risk_level: RiskLevelSchema,
  allow_patterns: z.array(z.string()),
  deny_patterns: z.array(z.string()),
})
export type CapabilityPolicy = z.infer<typeof CapabilityPolicySchema>

const EffectiveConfigResponseSchema = z.object({
  config: z.object({
    capabilities: z.record(z.string(), CapabilityPolicySchema),
  }).passthrough(),
  access_modes: z.record(z.string(), CapabilityAccessSummarySchema),
  warnings: z.array(z.string()),
})
export type EffectiveConfig = z.infer<typeof EffectiveConfigResponseSchema>

export function getEffectiveConfig() {
  return apiFetch("/api/config/effective", EffectiveConfigResponseSchema)
}

const AccessModesUpdateResponseSchema = z.object({
  config_file: z.string(),
  access_modes: z.record(z.string(), CapabilityAccessSummarySchema),
})

export function updateAccessModes(modes: Record<string, CapabilityAccessMode>) {
  return apiFetch("/api/config/access-modes", AccessModesUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify({ modes }),
  })
}

// ---- Secret vault -----------------------------------------------------

const SecretsListResponseSchema = z.object({
  available: z.boolean(),
  key_env: z.string(),
  services: z.record(z.string(), z.array(z.string())),
})
export type SecretsList = z.infer<typeof SecretsListResponseSchema>

export function listSecrets() {
  return apiFetch("/api/secrets", SecretsListResponseSchema)
}

const SecretSetResponseSchema = z.object({ service: z.string(), key: z.string(), set: z.boolean() })

export function setSecret(service: string, key: string, value: string) {
  return apiFetch("/api/secrets", SecretSetResponseSchema, {
    method: "POST",
    body: JSON.stringify({ service, key, value }),
  })
}

const SecretDeleteResponseSchema = z.object({ service: z.string(), key: z.string(), deleted: z.boolean() })

export function deleteSecret(service: string, key: string) {
  return apiFetch(`/api/secrets/${encodeURIComponent(service)}/${encodeURIComponent(key)}`, SecretDeleteResponseSchema, {
    method: "DELETE",
  })
}

const SecretVaultInitResponseSchema = z.object({ key_env: z.string(), generated: z.boolean() })

export function initSecretVault() {
  return apiFetch("/api/secrets/init", SecretVaultInitResponseSchema, { method: "POST" })
}

// ---- Settings (docs/UI_REWRITE_PLAN.md §14) -------------------------------
//
// Reuses /api/summary (already fetched for the health indicator) rather
// than adding a dedicated settings endpoint - the shape it returns already
// carries everything this page reads. The schema below only types the
// fields this client actually consumes; everything else stays under a
// .passthrough() at each level, same convention as SummarySchema above.

const LLMProfileConfigSchema = z.object({
  provider: z.string(),
  model: z.string(),
  base_url: z.string().nullable(),
  api_key_env: z.string().nullable(),
  api_key: z.string().nullable(),
  api_key_present: z.boolean(),
  timeout_seconds: z.number().int(),
  max_tokens: z.number().int(),
  temperature: z.number(),
})

const TelegramConfigSchema = z.object({
  enabled: z.boolean(),
  token_env: z.string(),
  token: z.string().nullable(),
  token_present: z.boolean(),
  allowed_user_ids: z.array(z.number().int()),
  allowed_chat_ids: z.array(z.number().int()),
  allowed_user_count: z.number().int(),
  allowed_chat_count: z.number().int(),
  polling: z.boolean(),
})

const VSCodeConfigSchema = z.object({
  enabled: z.boolean(),
  bridge_host: z.string(),
  bridge_port: z.number().int(),
  auth_token_env: z.string(),
})

const WorkspaceConfigSchema = z.object({
  enabled: z.boolean(),
  root_dir: z.string(),
  web_host: z.string(),
  web_port_start: z.number().int(),
  open_browser: z.boolean(),
})

const ComputerUseConfigSchema = z.object({
  enabled: z.boolean(),
  max_steps: z.number().int(),
  step_delay_seconds: z.number(),
  screenshot_dir: z.string(),
  allowed_apps: z.array(z.string()),
  allowed_roots: z.array(z.string()),
  require_session_approval: z.boolean(),
  max_ui_elements: z.number().int(),
})

// config.mcp.servers[*].env is deliberately absent server-side (config.py's
// safe_summary strips values, keeping only env_keys) - see the backend
// fix in this same phase for why (MCP servers routinely carry secrets in env).
const MCPServerConfigSchema = z.object({
  enabled: z.boolean(),
  command: z.string(),
  args: z.array(z.string()),
  cwd: z.string().nullable(),
  timeout_seconds: z.number().int(),
  capability: z.string(),
  risk_level: RiskLevelSchema,
  disabled_tools: z.array(z.string()),
  max_output_chars: z.number().int(),
  env_keys: z.array(z.string()),
})

const MCPConfigSchema = z.object({
  enabled: z.boolean(),
  cache_ttl_seconds: z.number().int(),
  catalog_path: z.string(),
  servers: z.record(z.string(), MCPServerConfigSchema),
})

const ServiceItemSchema = z.object({
  name: z.string(),
  expected: z.boolean(),
  ok: z.boolean(),
  status: z.string(),
  updated_at: z.string().nullable(),
  age_seconds: z.number().nullable(),
  supervisor_pid: z.number().nullable(),
  child_pid: z.number().nullable(),
  restart_count: z.number().nullable(),
  last_exit_code: z.number().nullable(),
  message: z.string().nullable(),
})

const DatabaseSummarySchema = z.object({
  database_url: z.string(),
  path: z.string(),
  table_counts: z.record(z.string(), z.number().int()),
  last_task_at: z.string().nullable(),
  last_audit_at: z.string().nullable(),
})

const LLMPresetSchema = z.object({
  key: z.string(),
  label: z.string().optional(),
  active: z.boolean(),
}).passthrough()

const SettingsSummarySchema = z.object({
  config: z.object({
    identity: z.object({ instance_name: z.string(), owner_label: z.string() }).passthrough(),
    channels: z.object({ telegram: TelegramConfigSchema }).passthrough(),
    llm: z.object({
      default_profile: z.string(),
      profiles: z.record(z.string(), LLMProfileConfigSchema),
    }),
    adapters: z.object({
      vscode: VSCodeConfigSchema,
      workspace: WorkspaceConfigSchema,
      computer_use: ComputerUseConfigSchema,
    }).passthrough(),
    mcp: MCPConfigSchema,
  }).passthrough(),
  warnings: z.array(z.string()),
  vscode: VSCodeStatusSchema,
  database: DatabaseSummarySchema,
  services: z.object({
    ready: z.boolean(),
    stale_after_seconds: z.number(),
    items: z.array(ServiceItemSchema),
  }),
  admin: z.object({
    enabled: z.boolean(),
    token_required: z.boolean(),
    config_file: z.string(),
  }),
  integrations: z.object({
    telegram: z.object({
      enabled: z.boolean(),
      token_present: z.boolean(),
      allowed_user_count: z.number().int(),
      allowed_chat_count: z.number().int(),
    }).passthrough(),
    llm: z.object({
      default_profile: z.string(),
      profile_count: z.number().int(),
      default_profile_configured: z.boolean(),
      presets: z.array(LLMPresetSchema),
    }),
  }),
}).passthrough()
export type SettingsSummary = z.infer<typeof SettingsSummarySchema>
export type LLMProfileConfig = z.infer<typeof LLMProfileConfigSchema>

export function getSettingsSummary() {
  return apiFetch("/api/summary?task_limit=1", SettingsSummarySchema)
}

const DoctorCheckSchema = z.object({
  name: z.string(),
  status: z.enum(["ok", "warn", "fail"]),
  detail: z.string(),
})
export type DoctorCheck = z.infer<typeof DoctorCheckSchema>

const DoctorResponseSchema = z.object({ checks: z.array(DoctorCheckSchema), ok: z.boolean() })

/** Runs the same checks `ybm doctor` runs (docs/UI_UX_AUDIT.md Phase 9) -
 * takes a couple of seconds, so this is operator-triggered, never polled. */
export function runDoctor() {
  return apiFetch("/api/doctor", DoctorResponseSchema)
}

const ServiceLogResponseSchema = z.object({
  service: z.string(),
  log_path: z.string().nullable(),
  lines: z.array(z.string()),
})

export function getServiceLog(service: string, lines = 200) {
  return apiFetch(`/api/logs/${encodeURIComponent(service)}?lines=${lines}`, ServiceLogResponseSchema)
}

// ---- Settings mutations -------------------------------------------------

export type LLMConfigInput = {
  profile_name: string
  default_profile: string
  provider: string
  model: string
  base_url: string | null
  api_key_env: string | null
  timeout_seconds: number
  max_tokens: number
  temperature: number
  api_key_value: string | null
}

const ConfigUpdateResponseSchema = z.object({ config_file: z.string() }).passthrough()

export function updateLLMConfig(input: LLMConfigInput) {
  return apiFetch("/api/config/llm", ConfigUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify(input),
  })
}

export function selectLLMPreset(preset: string) {
  return apiFetch("/api/config/llm/preset", ConfigUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify({ preset }),
  })
}

const LLMTestResponseSchema = z.object({ profile: z.string(), output_preview: z.string() })

// Makes a real LLM call - never invoke from an automated check, only from
// an explicit user click on the "Test connection" button.
export function testLLM() {
  return apiFetch("/api/llm/test", LLMTestResponseSchema, { method: "POST" })
}

export type TelegramConfigInput = Partial<{
  enabled: boolean
  token_env: string
  allowed_user_ids: number[]
  allowed_chat_ids: number[]
  polling: boolean
  bot_token: string | null
}>

export function updateTelegramConfig(input: TelegramConfigInput) {
  return apiFetch("/api/config/telegram", ConfigUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify(input),
  })
}

export type VSCodeConfigInput = Partial<{
  enabled: boolean
  bridge_host: string
  bridge_port: number
  auth_token_env: string
  bridge_token: string | null
}>

export function updateVSCodeConfig(input: VSCodeConfigInput) {
  return apiFetch("/api/config/vscode", ConfigUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify(input),
  })
}

export type WorkspaceConfigInput = Partial<{
  enabled: boolean
  root_dir: string
  web_host: string
  web_port_start: number
  open_browser: boolean
}>

export function updateWorkspaceConfig(input: WorkspaceConfigInput) {
  return apiFetch("/api/config/workspace", ConfigUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify(input),
  })
}

export type ComputerUseConfigInput = Partial<{
  enabled: boolean
  max_steps: number
  step_delay_seconds: number
  screenshot_dir: string
  allowed_apps: string[]
  allowed_roots: string[]
  require_session_approval: boolean
  max_ui_elements: number
}>

export function updateComputerUseConfig(input: ComputerUseConfigInput) {
  return apiFetch("/api/config/computer-use", ConfigUpdateResponseSchema, {
    method: "POST",
    body: JSON.stringify(input),
  })
}

// ---- Audit (docs/UI_REWRITE_PLAN.md §14 Level 2) --------------------------

const AuditEventTypeSchema = z.enum([
  "message_received", "message_sent", "config_updated", "telegram_access_decision",
  "message_classified", "task_spawn_failed", "task_created", "task_state_changed",
  "plan_created", "policy_decision", "approval_requested", "approval_decided",
  "tool_requested", "tool_completed", "artifact_created", "error",
])

// Mirrors storage/audit_view.py's CATEGORY_BY_TYPE value set - a small,
// derived grouping (event type -> coarser category), not a schema of its own.
export const AUDIT_CATEGORIES = [
  "raw_telegram", "telegram_access", "classification", "failed_classification",
  "spawned_task", "policy", "config", "tool", "approval", "error", "system",
] as const

const FormattedAuditEventSchema = z.object({
  id: z.string(),
  type: AuditEventTypeSchema,
  category: z.string(),
  formatted_time: z.string(),
  actor: z.string(),
  task_id: z.string().nullable(),
  title: z.string(),
  summary: z.string(),
  decision: z.string().nullable(),
  reason: z.string().nullable(),
  task_type: z.string().nullable(),
  source: z.string().nullable(),
  details: z.record(z.string(), z.unknown()),
})
export type FormattedAuditEvent = z.infer<typeof FormattedAuditEventSchema>

const AuditListResponseSchema = z.object({ events: z.array(FormattedAuditEventSchema) })

export function listAudit(params: { category?: string; q?: string; limit?: number }) {
  const search = new URLSearchParams()
  if (params.category) search.set("category", params.category)
  if (params.q) search.set("q", params.q)
  search.set("limit", String(params.limit ?? 50))
  return apiFetch(`/api/audit?${search.toString()}`, AuditListResponseSchema)
}

const AuditClearResponseSchema = z.object({ deleted_audit_events: z.number().int() })

export function clearAudit() {
  return apiFetch("/api/audit", AuditClearResponseSchema, { method: "DELETE" })
}

// ---- Memory (docs/UI_UX_AUDIT.md Phase 4) ------------------------------

export const MemorySourceSchema = z.enum(["user_stated", "task_derived", "operator_admin"])
export type MemorySource = z.infer<typeof MemorySourceSchema>

export const MemoryFactSchema = z.object({
  id: z.string(),
  category: z.string(),
  content: z.string(),
  source: MemorySourceSchema,
  confidence: z.number(),
  task_id: z.string().nullable(),
  supersedes_id: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
})
export type MemoryFact = z.infer<typeof MemoryFactSchema>

const MemoryListResponseSchema = z.object({ facts: z.array(MemoryFactSchema) })

export function listMemoryFacts(params: { q?: string; category?: string } = {}) {
  const search = new URLSearchParams()
  if (params.q) search.set("q", params.q)
  if (params.category) search.set("category", params.category)
  const query = search.toString()
  return apiFetch(`/api/memory${query ? `?${query}` : ""}`, MemoryListResponseSchema)
}

const MemoryFactResponseSchema = z.object({ fact: MemoryFactSchema })

export function createMemoryFact(category: string, content: string) {
  return apiFetch("/api/memory", MemoryFactResponseSchema, {
    method: "POST",
    body: JSON.stringify({ category, content }),
  })
}

export function updateMemoryFact(factId: string, category: string, content: string) {
  return apiFetch(`/api/memory/${encodeURIComponent(factId)}`, MemoryFactResponseSchema, {
    method: "PATCH",
    body: JSON.stringify({ category, content }),
  })
}

const MemoryDeleteResponseSchema = z.object({ fact_id: z.string(), deleted: z.boolean() })

export function deleteMemoryFact(factId: string) {
  return apiFetch(`/api/memory/${encodeURIComponent(factId)}`, MemoryDeleteResponseSchema, {
    method: "DELETE",
  })
}

// ---- Skills (docs/UI_UX_AUDIT.md Phase 5) -------------------------------

export const SkillSchema = z.object({
  name: z.string(),
  description: z.string(),
  version: z.string(),
  tools: z.array(z.string()),
  tools_declared: z.boolean(),
  body: z.string(),
  path: z.string(),
  content_hash: z.string(),
  size_bytes: z.number(),
  modified_at: z.number().nullable(),
})
export type Skill = z.infer<typeof SkillSchema>

const SkillsListResponseSchema = z.object({ root_dir: z.string(), skills: z.array(SkillSchema) })

export function listSkills() {
  return apiFetch("/api/skills", SkillsListResponseSchema)
}

const SkillsCatalogResponseSchema = z.object({ skills: z.array(SkillSchema) })

/** The bundled skills/starter/ catalog (docs/UI_UX_AUDIT.md Phase 11) -
 * read-only browsing, distinct from listSkills() above (what's actually
 * installed). Installing a catalog entry reuses installSkill() with its
 * own fields - one install code path, not a separate one for the catalog. */
export function listSkillsCatalog() {
  return apiFetch("/api/skills/catalog", SkillsCatalogResponseSchema)
}

const SkillInstallResponseSchema = z.object({ skill: SkillSchema })

export type SkillInstallInput = { name: string; description: string; body: string; version?: string; tools?: string[] }

export function installSkill(input: SkillInstallInput) {
  return apiFetch("/api/skills", SkillInstallResponseSchema, {
    method: "POST",
    body: JSON.stringify(input),
  })
}

const SkillUninstallResponseSchema = z.object({ name: z.string(), deleted: z.boolean() })

export function uninstallSkill(name: string) {
  return apiFetch(`/api/skills/${encodeURIComponent(name)}`, SkillUninstallResponseSchema, {
    method: "DELETE",
  })
}
