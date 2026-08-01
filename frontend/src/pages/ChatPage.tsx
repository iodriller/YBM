import { useEffect, useRef, useState } from "react"
import { Send } from "lucide-react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
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
            <div className="flex flex-col items-center gap-4 py-16 text-center">
              <p className="text-sm text-muted-foreground">
                No messages yet — say something below, or try:
              </p>
              <div className="flex flex-col gap-2 sm:flex-row">
                {STARTER_PROMPTS.map((prompt) => (
                  <Button
                    key={prompt}
                    variant="outline"
                    size="sm"
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
        className="border-t border-border p-4"
        onSubmit={(event) => {
          event.preventDefault()
          handleSend(draft)
        }}
      >
        <div className="mx-auto flex max-w-2xl gap-2">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Message the agent..."
            disabled={sendMessage.isPending}
            autoFocus
          />
          <Button type="submit" disabled={sendMessage.isPending || !draft.trim()}>
            <Send className="size-4" />
          </Button>
        </div>
        {sendMessage.isError && (
          <p className="mx-auto mt-2 max-w-2xl text-xs text-destructive">
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
  return (
    <div className={cn("flex items-start gap-3", isUser && "flex-row-reverse")}>
      <Avatar className="size-8 shrink-0">
        <AvatarFallback className="text-xs">{isUser ? "You" : "Y"}</AvatarFallback>
      </Avatar>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground",
          pending && "animate-pulse",
        )}
      >
        {text}
        {pending && status && (
          <span className="ml-1 text-xs opacity-70">({status})</span>
        )}
      </div>
    </div>
  )
}
