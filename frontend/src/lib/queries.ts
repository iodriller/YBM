import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  clearAudit,
  decideApproval,
  deleteSecret,
  getBootstrap,
  getEffectiveConfig,
  getSettingsSummary,
  getSetupDetect,
  getSummary,
  getTaskTrace,
  initSecretVault,
  listAudit,
  listChatMessages,
  listPendingApprovals,
  listSecrets,
  listTasks,
  selectLLMPreset,
  sendChatMessage,
  sendTaskSignal,
  setSecret,
  testLLM,
  updateAccessModes,
  updateComputerUseConfig,
  updateLLMConfig,
  updateTelegramConfig,
  updateVSCodeConfig,
  updateWorkspaceConfig,
  type ApprovalDecision,
  type CapabilityAccessMode,
  type ComputerUseConfigInput,
  type LLMConfigInput,
  type TelegramConfigInput,
  type VSCodeConfigInput,
  type WorkspaceConfigInput,
} from "@/lib/api"
import { isTerminal } from "@/lib/chat"

// Polling intervals - deliberately simple (plain TanStack Query polling,
// not SSE) per docs/UI_REWRITE_PLAN.md §9 (Phase 0.4): matches Streamlit's
// prior 3s whole-page rerun behavior with per-query granularity instead,
// and SSE is added later only if this proves visibly laggy in real use.
const CHAT_POLL_MS = 2_000
const SUMMARY_POLL_MS = 5_000
// Approvals are the one thing on this console with a hard deadline
// (expires_at) - polled faster than everything else so the countdown and
// "can I still click this" state stay accurate to within a couple seconds.
const APPROVALS_POLL_MS = 2_000
const TASKS_POLL_MS = 3_000
const TRACE_POLL_MS = 2_000

export function useBootstrap() {
  return useQuery({
    queryKey: ["bootstrap"],
    queryFn: getBootstrap,
    // Onboarding/config state changes rarely; no need to poll continuously.
    staleTime: 30_000,
  })
}

export function useSetupDetect(enabled: boolean) {
  return useQuery({
    queryKey: ["setup", "detect"],
    queryFn: getSetupDetect,
    enabled,
    staleTime: 10_000,
  })
}

export function useSummary() {
  return useQuery({
    queryKey: ["summary"],
    queryFn: () => getSummary(),
    refetchInterval: SUMMARY_POLL_MS,
  })
}

export function useChatMessages() {
  return useQuery({
    queryKey: ["chat", "messages"],
    queryFn: () => listChatMessages(),
    refetchInterval: (query) => {
      // Poll faster while any task is still in flight, back off once every
      // visible task has settled - no point hammering the backend for a
      // transcript that can no longer change until the next send.
      const tasks = query.state.data?.tasks
      const anyActive = tasks?.some((task) => !isTerminal(task.status)) ?? true
      return anyActive ? CHAT_POLL_MS : false
    },
  })
}

export function useSendChatMessage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: sendChatMessage,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["chat", "messages"] })
    },
  })
}

export function usePendingApprovals() {
  return useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: listPendingApprovals,
    refetchInterval: APPROVALS_POLL_MS,
  })
}

export function useDecideApproval() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ approvalId, decision }: { approvalId: string; decision: ApprovalDecision }) =>
      decideApproval(approvalId, decision),
    onSuccess: () => {
      // Also invalidate tasks/chat - approving unblocks a task that may be
      // sitting at awaiting_approval, and its status/answer should update
      // wherever it's currently visible, not just in the approvals list.
      void queryClient.invalidateQueries({ queryKey: ["approvals"] })
      void queryClient.invalidateQueries({ queryKey: ["chat", "messages"] })
      void queryClient.invalidateQueries({ queryKey: ["summary"] })
    },
  })
}

export function useTasks(limit = 50) {
  return useQuery({
    queryKey: ["tasks", "list", limit],
    queryFn: () => listTasks(limit),
    refetchInterval: TASKS_POLL_MS,
  })
}

export function useTaskTrace(taskId: string | undefined) {
  return useQuery({
    queryKey: ["tasks", "trace", taskId],
    queryFn: () => getTaskTrace(taskId!),
    enabled: taskId != null,
    refetchInterval: (query) => {
      // No point polling a trace forever once the task has settled - it
      // cannot change further. Keep polling while active so a trace open
      // during a running task updates live.
      const status = query.state.data?.task.status
      return status && isTerminal(status) ? false : TRACE_POLL_MS
    },
  })
}

export function useTaskSignal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ taskId, signal }: { taskId: string; signal: "pause" | "resume" | "cancel" }) =>
      sendTaskSignal(taskId, signal),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["tasks"] })
      void queryClient.invalidateQueries({ queryKey: ["tasks", "trace", variables.taskId] })
      void queryClient.invalidateQueries({ queryKey: ["chat", "messages"] })
    },
  })
}

// ---- Access (docs/UI_REWRITE_PLAN.md §13) ----------------------------

export function useEffectiveConfig() {
  return useQuery({
    queryKey: ["config", "effective"],
    queryFn: getEffectiveConfig,
    // Config only changes through this same console's own mutations below
    // (each of which invalidates this key) - no external process edits it
    // while the console is open, so continuous polling would just be noise.
    staleTime: 30_000,
  })
}

export function useUpdateAccessModes() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (modes: Record<string, CapabilityAccessMode>) => updateAccessModes(modes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["config", "effective"] })
    },
  })
}

export function useSecrets() {
  return useQuery({
    queryKey: ["secrets"],
    queryFn: listSecrets,
    staleTime: 30_000,
  })
}

export function useSetSecret() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ service, key, value }: { service: string; key: string; value: string }) =>
      setSecret(service, key, value),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["secrets"] })
    },
  })
}

export function useDeleteSecret() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ service, key }: { service: string; key: string }) => deleteSecret(service, key),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["secrets"] })
    },
  })
}

export function useInitSecretVault() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: initSecretVault,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["secrets"] })
    },
  })
}

// ---- Settings (docs/UI_REWRITE_PLAN.md §14) ----------------------------

export function useSettingsSummary() {
  return useQuery({
    queryKey: ["settings", "summary"],
    queryFn: getSettingsSummary,
    // Same reasoning as useEffectiveConfig - only this console's own
    // mutations change it, and each of those invalidates this key.
    staleTime: 30_000,
  })
}

function useSettingsMutation<TInput>(mutationFn: (input: TInput) => Promise<unknown>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "summary"] })
      // LLM/Telegram config writes are also what flips bootstrap's
      // onboarding_complete (admin.py: CONFIG_FILE_PATH.exists()) - the
      // onboarding wizard's own mutations go through this same helper, so
      // its "am I done yet" check needs fresh data too.
      void queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
    },
  })
}

export function useUpdateLLMConfig() {
  return useSettingsMutation((input: LLMConfigInput) => updateLLMConfig(input))
}

export function useSelectLLMPreset() {
  return useSettingsMutation((preset: string) => selectLLMPreset(preset))
}

export function useTestLLM() {
  // Never polled or invalidated automatically - this is a real LLM call,
  // triggered only by an explicit button click (see api.ts's testLLM).
  return useMutation({ mutationFn: testLLM })
}

export function useUpdateTelegramConfig() {
  return useSettingsMutation((input: TelegramConfigInput) => updateTelegramConfig(input))
}

export function useUpdateVSCodeConfig() {
  return useSettingsMutation((input: VSCodeConfigInput) => updateVSCodeConfig(input))
}

export function useUpdateWorkspaceConfig() {
  return useSettingsMutation((input: WorkspaceConfigInput) => updateWorkspaceConfig(input))
}

export function useUpdateComputerUseConfig() {
  return useSettingsMutation((input: ComputerUseConfigInput) => updateComputerUseConfig(input))
}

// ---- Audit (docs/UI_REWRITE_PLAN.md §14 Level 2) ------------------------

export function useAudit(params: { category?: string; q?: string; limit?: number }) {
  return useQuery({
    queryKey: ["audit", params],
    queryFn: () => listAudit(params),
    staleTime: 10_000,
  })
}

export function useClearAudit() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: clearAudit,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["audit"] })
    },
  })
}
