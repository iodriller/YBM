import { useEffect, useRef, useState } from "react"
import { Bot, LoaderCircle, Send, ShieldCheck, Sparkles, User } from "lucide-react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { chatAnswerText, isTerminal } from "@/lib/chat"
import { useChatMessages, useSendChatMessage } from "@/lib/queries"
import { cn } from "@/lib/utils"
import type { TaskRecord } from "@/lib/api"

// One of these deliberately routes through an approval, so a first-time
// user meets the approval gate in their first minute rather than
// discovering it later (docs/UI_REWRITE_PLAN.md §10).
const STARTER_PROMPTS = [
  "What's the current status?",
  "Summarize a PDF on my desktop",
  "Use the local code interpreter to compute the 20th Fibonacci number",
]

export function ChatPage() {
  const { data, isPending, isError, error } = useChatMessages()
  const sendMessage = useSendChatMessage()
  const [draft, setDraft] = useState("")
  const scrollAnchorRef = useRef<HTMLDivElement>(null)

  const tasks = data?.tasks ?? []

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ block: "end" })
  }, [tasks.length])

  function handleSend(text: string) {
    const trimmed = text.trim()
    if (!trimmed || sendMessage.isPending) return
    sendMessage.mutate(trimmed)
    setDraft("")
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <header className="shrink-0 border-b border-border/70 bg-card/60 px-4 py-3 backdrop-blur sm:px-6">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
          <div>
            <h1 className="text-sm font-semibold">Local chat</h1>
            <p className="text-xs text-muted-foreground">Ask, approve, and inspect from one conversation.</p>
          </div>
          <div className="flex items-center gap-1.5 rounded-full bg-success/10 px-2.5 py-1 text-xs font-medium text-success">
            <ShieldCheck className="size-3.5" />
            Policy protected
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 sm:py-7">
        <div className="mx-auto flex min-w-0 max-w-3xl flex-col gap-5">
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
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-input bg-card p-2 shadow-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/20">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                handleSend(draft)
              }
            }}
            placeholder="Ask YBM to do something..."
            disabled={sendMessage.isPending}
            autoFocus
            rows={1}
            aria-label="Message YBM"
            className="max-h-36 min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-5 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
          />
          <Button
            type="submit"
            size="icon"
            className="size-9 rounded-xl"
            aria-label="Send message"
            disabled={sendMessage.isPending || !draft.trim()}
          >
            {sendMessage.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}
          </Button>
        </div>
        <p className="mx-auto mt-1.5 hidden max-w-3xl px-2 text-[11px] text-muted-foreground sm:block">
          Enter to send · Shift + Enter for a new line
        </p>
        {sendMessage.isError && (
          <p className="mx-auto mt-2 max-w-3xl text-xs text-destructive">
            {sendMessage.error.message}
          </p>
        )}
      </form>
    </div>
  )
}

function ChatExchange({ task }: { task: TaskRecord }) {
  const settled = isTerminal(task.status)
  return (
    <div className="flex flex-col gap-3">
      <Bubble role="user" text={task.objective} />
      <Bubble
        role="assistant"
        text={chatAnswerText(task)}
        pending={!settled}
        status={task.status}
      />
    </div>
  )
}

function Bubble({
  role,
  text,
  pending,
  status,
}: {
  role: "user" | "assistant"
  text: string
  pending?: boolean
  status?: string
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
          "min-w-0 max-w-[84%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[0.925rem] leading-6 [overflow-wrap:anywhere] sm:max-w-[78%]",
          isUser
            ? "rounded-tr-sm bg-primary text-primary-foreground shadow-sm shadow-primary/10"
            : "rounded-tl-sm border border-border/80 bg-card text-card-foreground shadow-sm",
          failed && !isUser && "border-destructive/25 bg-destructive/5",
        )}
      >
        {text}
        {pending && status && (
          <span className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
            <LoaderCircle className="size-3 animate-spin" />
            {status.replace(/_/g, " ")}
          </span>
        )}
      </div>
    </div>
  )
}
