import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { Bot, LoaderCircle, Maximize2, Minimize2, Paperclip, Send, ShieldCheck, Sparkles, Square, Columns3, User, X } from "lucide-react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { ArtifactCard } from "@/components/chat/ArtifactCard"
import { ChatMarkdown } from "@/components/chat/ChatMarkdown"
import { ComposerModeChips, ComposerTools, COMPOSER_MODES } from "@/components/chat/ComposerTools"
import { VoiceRecorder } from "@/components/chat/VoiceRecorder"
import { FolderPicker } from "@/components/chat/FolderPicker"
import { InlineApproval } from "@/components/chat/InlineApproval"
import { TaskReceiptCard } from "@/components/chat/TaskReceiptCard"
import { chatAnswerText, displayedObjective, isTerminal } from "@/lib/chat"
import { chatWidthClass, readChatWidth, writeChatWidth, type ChatWidth } from "@/lib/chat-width"
import {
  useChatMessages,
  usePendingApprovals,
  useSendChatMessage,
  useTaskSignal,
  useUploadChatAttachment,
} from "@/lib/queries"
import { cn } from "@/lib/utils"
import { ApiError, type TaskRecord } from "@/lib/api"

const WIDTH_OPTIONS: { value: ChatWidth; label: string; icon: typeof Minimize2 }[] = [
  { value: "comfortable", label: "Comfortable", icon: Minimize2 },
  { value: "wide", label: "Wide", icon: Columns3 },
  { value: "full", label: "Full width", icon: Maximize2 },
]

interface PendingAttachment {
  key: string
  fileName: string
  artifactId?: string
  uploading: boolean
}

// Suggestions describe an outcome, never a tool. "Use the local code
// interpreter to compute..." taught the user to name the tool themselves,
// which implies YBM cannot work out that arithmetic needs code - if that were
// true it would be a routing bug to fix, not a prompt to ship. Deciding which
// capability to reach for is the agent's job.
//
// One of these deliberately routes through an approval, so a first-time user
// meets the approval gate in their first minute rather than discovering it
// later (docs/UI_REWRITE_PLAN.md §10).
const STARTER_PROMPTS = [
  "What can you help me with?",
  "Summarize the PDFs on my desktop",
  "What's the 20th Fibonacci number?",
]

export function ChatPage() {
  const { data, isPending, isError, error } = useChatMessages()
  const sendMessage = useSendChatMessage()
  const uploadAttachment = useUploadChatAttachment()
  const [draft, setDraft] = useState("")
  const [modes, setModes] = useState<string[]>([])
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [width, setWidth] = useState<ChatWidth>(readChatWidth)
  const scrollAnchorRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const widthClass = chatWidthClass(width)

  function handleWidthChange(next: ChatWidth) {
    setWidth(next)
    writeChatWidth(next)
  }

  const tasks = data?.tasks ?? []
  const attachmentsUploading = attachments.some((a) => a.uploading)

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ block: "end" })
  }, [tasks.length])

  function handleFilesSelected(files: FileList | null) {
    if (!files) return
    for (const file of Array.from(files)) {
      const key = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`
      setAttachments((prev) => [...prev, { key, fileName: file.name, uploading: true }])
      uploadAttachment.mutate(file, {
        onSuccess: (result) => {
          setAttachments((prev) => prev.map((a) => (a.key === key ? { ...a, artifactId: result.artifact_id, uploading: false } : a)))
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : `Could not upload ${file.name}.`)
          setAttachments((prev) => prev.filter((a) => a.key !== key))
        },
      })
    }
  }

  function removeAttachment(key: string) {
    setAttachments((prev) => prev.filter((a) => a.key !== key))
  }

  function handleFolderSelect(path: string) {
    setDraft((prev) => (prev.trim() ? `${prev.trimEnd()} ${path} ` : `${path} `))
  }

  function handleSend(text: string) {
    const trimmed = text.trim()
    if (!trimmed || sendMessage.isPending || attachmentsUploading) return
    const attachmentIds = attachments.map((a) => a.artifactId).filter((id): id is string => Boolean(id))
    // Selected modes ride along as an explicit instruction. The default is no
    // modes, which is the right default - the agent picks its own tools. A
    // chip is for when the user has already decided.
    const instructions = modes
      .map((key) => COMPOSER_MODES.find((m) => m.key === key)?.instruction)
      .filter(Boolean)
      .join(" ")
    const body = instructions ? `${trimmed}

${instructions}` : trimmed
    sendMessage.mutate({ text: body, attachmentIds })
    setDraft("")
    setAttachments([])
    setModes([])
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <header className="shrink-0 border-b border-border/70 bg-card/60 px-4 py-3 backdrop-blur sm:px-6">
        <div className={cn("mx-auto flex items-center justify-between gap-4", widthClass)}>
          <div>
            <h1 className="text-sm font-semibold">Local chat</h1>
            <p className="text-xs text-muted-foreground">Ask, approve, and inspect from one conversation.</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-0.5 rounded-full border border-border bg-muted/40 p-0.5 sm:flex">
              {WIDTH_OPTIONS.map(({ value, label, icon: Icon }) => (
                <Button
                  key={value}
                  type="button"
                  variant={width === value ? "secondary" : "ghost"}
                  size="icon-sm"
                  className="rounded-full"
                  aria-label={`${label} chat width`}
                  aria-pressed={width === value}
                  title={label}
                  onClick={() => handleWidthChange(value)}
                >
                  <Icon className="size-3.5" />
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 rounded-full bg-success/10 px-2.5 py-1 text-xs font-medium text-success">
              <ShieldCheck className="size-3.5" />
              Policy protected
            </div>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 sm:py-7">
        <div className={cn("mx-auto flex min-w-0 flex-col gap-5", widthClass)}>
          {isError && (
            <Alert variant="destructive">
              <AlertTitle>Couldn't load chat</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          )}

          {isPending && (
            <div className="space-y-3">
              <Skeleton className="h-16 w-2/3" />
              <Skeleton className="ml-auto h-10 w-1/2" />
            </div>
          )}

          {!isPending && !isError && tasks.length === 0 && (
            <div className="flex flex-col items-center py-10 text-center sm:py-20">
              <span className="mb-5 flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
                <Sparkles className="size-5" />
              </span>
              <h2 className="text-xl font-semibold tracking-tight">What can I help you do?</h2>
              <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                YBM can answer questions or carry out policy-gated work on this machine.
              </p>
              <div className="mt-7 grid w-full gap-2 sm:grid-cols-3">
                {STARTER_PROMPTS.map((prompt) => (
                  <Button
                    key={prompt}
                    variant="outline"
                    className="h-auto min-h-14 justify-start whitespace-normal px-3 py-2.5 text-left text-xs leading-5"
                    onClick={() => handleSend(prompt)}
                  >
                    {prompt}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {tasks.map((task) => (
            <ChatExchange key={task.id} task={task} />
          ))}
          <div ref={scrollAnchorRef} />
        </div>
      </div>

      <form
        className="shrink-0 border-t border-border/70 bg-background/90 px-4 py-3 backdrop-blur sm:px-6 sm:py-4"
        onSubmit={(event) => {
          event.preventDefault()
          handleSend(draft)
        }}
      >
        <div className={cn("mx-auto flex flex-col gap-2", widthClass)}>
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {attachments.map((attachment) => (
                <span
                  key={attachment.key}
                  className="flex items-center gap-1.5 rounded-full border border-border bg-muted/60 py-1 pl-2.5 pr-1.5 text-xs"
                >
                  {attachment.uploading ? (
                    <LoaderCircle className="size-3 animate-spin text-muted-foreground" />
                  ) : (
                    <Paperclip className="size-3 text-muted-foreground" />
                  )}
                  <span className="max-w-40 truncate">{attachment.fileName}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${attachment.fileName}`}
                    onClick={() => removeAttachment(attachment.key)}
                    className="flex size-4 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
          <ComposerModeChips
            selected={modes}
            onToggle={(key) =>
              setModes((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
            }
          />
          <div className="flex items-end gap-2 rounded-2xl border border-input bg-card p-2 shadow-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/20">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => {
                handleFilesSelected(event.target.files)
                event.target.value = ""
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 rounded-xl text-muted-foreground"
              aria-label="Attach a file"
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip className="size-4" />
            </Button>
            <FolderPicker onSelect={handleFolderSelect} />
            <VoiceRecorder
              disabled={sendMessage.isPending}
              // Into the composer, not straight out - the user gets to read
              // what was heard and fix it before sending.
              onTranscript={(text) => setDraft((prev) => (prev ? `${prev} ${text}` : text))}
              onError={(message) => toast.error(message)}
            />
            <ComposerTools
              selected={modes}
              onToggle={(key) =>
                setModes((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
              }
            />
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault()
                  handleSend(draft)
                }
              }}
              placeholder="Ask YBM anything..."
              // At 390px the composer buttons leave the textarea narrow
              // enough to wrap the placeholder, and rows={1} clipped it.
              style={{ minHeight: "2.75rem" }}
              disabled={sendMessage.isPending}
              autoFocus
              rows={1}
              aria-label="Message YBM"
              className="max-h-36 min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-5 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
            />
            <Button
              type="submit"
              size="icon"
              className="size-9 shrink-0 rounded-xl"
              aria-label="Send message"
              disabled={sendMessage.isPending || !draft.trim() || attachmentsUploading}
            >
              {sendMessage.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}
            </Button>
          </div>
        </div>
        <p className={cn("mx-auto mt-1.5 hidden px-2 text-[11px] text-muted-foreground sm:block", widthClass)}>
          Enter to send · Shift + Enter for a new line
        </p>
        {sendMessage.isError && (
          <p className={cn("mx-auto mt-2 text-xs text-destructive", widthClass)}>
            {sendMessage.error.message}
          </p>
        )}
      </form>
    </div>
  )
}

function ChatExchange({ task }: { task: TaskRecord }) {
  const settled = isTerminal(task.status)
  const taskSignal = useTaskSignal()
  const sendMessage = useSendChatMessage()
  const [clarifyDraft, setClarifyDraft] = useState("")
  const clarifying = task.status === "clarifying"
  const { data: approvalsData } = usePendingApprovals()
  const pendingApproval =
    task.status === "awaiting_approval"
      ? approvalsData?.approvals.find((item) => item.approval.task_id === task.id)
      : undefined

  function handleClarifySubmit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = clarifyDraft.trim()
    if (!trimmed || sendMessage.isPending) return
    sendMessage.mutate({ text: trimmed })
    setClarifyDraft("")
  }

  const clarificationAnswers = Array.isArray(task.metadata.clarification_answers)
    ? (task.metadata.clarification_answers as { question?: string; answer?: string }[])
    : []

  return (
    <div className="flex flex-col gap-3">
      <Bubble role="user" text={displayedObjective(task.objective)} />
      {clarificationAnswers.map((entry, index) => (
        <div key={index} className="flex flex-col gap-2">
          {entry.question && <Bubble role="assistant" text={entry.question} muted />}
          {entry.answer && <Bubble role="user" text={displayedObjective(entry.answer)} />}
        </div>
      ))}
      <Bubble role="assistant" text={chatAnswerText(task)} pending={!settled} status={task.status}>
        {/* Every terminal state gets a receipt, not just completed - a task
            that modified files then failed is exactly when the user most
            needs to see what happened (docs/UI_UX_AUDIT.md Phase 8). */}
        {settled && <TaskReceiptCard taskId={task.id} />}
        {!settled && !clarifying && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2 h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-destructive"
            disabled={taskSignal.isPending}
            onClick={() => taskSignal.mutate({ taskId: task.id, signal: "cancel" })}
          >
            <Square className="size-3" />
            Stop
          </Button>
        )}
        {clarifying && (
          <form onSubmit={handleClarifySubmit} className="mt-2.5 flex items-center gap-1.5">
            <input
              value={clarifyDraft}
              onChange={(event) => setClarifyDraft(event.target.value)}
              placeholder="Type your answer..."
              autoFocus
              className="h-8 min-w-0 flex-1 rounded-lg border border-input bg-background px-2.5 text-xs outline-none focus:border-ring focus:ring-2 focus:ring-ring/20"
            />
            <Button type="submit" size="sm" className="h-8 px-2.5 text-xs" disabled={!clarifyDraft.trim() || sendMessage.isPending}>
              Reply
            </Button>
          </form>
        )}
        {pendingApproval && <InlineApproval item={pendingApproval} />}
        {task.artifacts != null && task.artifacts.length > 0 && (
          <div className="mt-2.5 flex flex-col gap-1.5">
            {task.artifacts.map((artifact) => (
              <ArtifactCard key={artifact.id} artifact={artifact} />
            ))}
          </div>
        )}
      </Bubble>
    </div>
  )
}

function Bubble({
  role,
  text,
  pending,
  status,
  muted,
  children,
}: {
  role: "user" | "assistant"
  text: string
  pending?: boolean
  status?: string
  muted?: boolean
  children?: React.ReactNode
}) {
  const isUser = role === "user"
  const failed = status === "failed" || status === "blocked"
  return (
    <div className={cn("flex min-w-0 items-start gap-3", isUser && "flex-row-reverse")}>
      <Avatar className="size-8 shrink-0 border border-border shadow-sm">
        <AvatarFallback className={cn("text-xs", isUser ? "bg-muted" : "bg-primary/10 text-primary")}>
          {isUser ? <User className="size-3.5" /> : <Bot className="size-3.5" />}
        </AvatarFallback>
      </Avatar>
      <div
        className={cn(
          "min-w-0 max-w-[84%] rounded-2xl px-4 py-2.5 text-[0.925rem] leading-6 sm:max-w-[78%]",
          isUser
            ? "rounded-tr-sm bg-primary text-primary-foreground shadow-sm shadow-primary/10 whitespace-pre-wrap [overflow-wrap:anywhere]"
            : "rounded-tl-sm border border-border/80 bg-card text-card-foreground shadow-sm",
          failed && !isUser && "border-destructive/25 bg-destructive/5",
          muted && !isUser && "text-muted-foreground",
        )}
      >
        {isUser ? text : <ChatMarkdown text={text} />}
        {pending && status && (
          <span className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
            <LoaderCircle className="size-3 animate-spin" />
            {status.replace(/_/g, " ")}
          </span>
        )}
        {children}
      </div>
    </div>
  )
}
