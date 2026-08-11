import { useId, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  ApiError,
  awaitFirstTelegramMessage,
  type TelegramFirstMessage,
  type TelegramVerifyResult,
  verifyTelegramToken,
} from "@/lib/api"

export type TelegramConnection = {
  botToken: string | null
  message: TelegramFirstMessage
}

export function TelegramConnectionGuide({
  tokenPresent,
  onLinked,
}: {
  tokenPresent: boolean
  onLinked: (connection: TelegramConnection) => void
}) {
  const tokenInputId = useId()
  const [telegramToken, setTelegramToken] = useState("")
  const [botIdentity, setBotIdentity] = useState<TelegramVerifyResult | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [listening, setListening] = useState(false)
  const [linked, setLinked] = useState<TelegramFirstMessage | null>(null)
  const [error, setError] = useState<string | null>(null)

  const submittedToken = tokenPresent ? null : telegramToken.trim() || null

  async function verifyToken() {
    setError(null)
    setVerifying(true)
    try {
      setBotIdentity(await verifyTelegramToken(submittedToken))
    } catch (err) {
      setBotIdentity(null)
      setError(err instanceof ApiError ? err.message : "Could not check that token.")
    } finally {
      setVerifying(false)
    }
  }

  async function listenForFirstMessage() {
    setError(null)
    setListening(true)
    try {
      const result = await awaitFirstTelegramMessage(submittedToken)
      if (result.found) {
        setLinked(result)
        onLinked({ botToken: submittedToken, message: result })
      } else {
        setError("No message arrived yet. Send your bot any message, then try again.")
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not check for a message.")
    } finally {
      setListening(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3">
      {!tokenPresent && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium">1. Create a bot</p>
          <p className="text-xs text-muted-foreground">
            Message{" "}
            <a
              href="https://t.me/BotFather"
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              @BotFather
            </a>{" "}
            on Telegram and send <code className="rounded bg-background px-1">/newbot</code>. He replies with a
            token.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-1">
        <Label htmlFor={tokenInputId} className="text-xs font-medium">
          {tokenPresent ? "1. Check the saved bot" : "2. Paste the token"}
        </Label>
        <div className="flex flex-col gap-2 sm:flex-row">
          {!tokenPresent && (
            <Input
              id={tokenInputId}
              type="password"
              autoComplete="off"
              placeholder="123456789:AA..."
              value={telegramToken}
              onChange={(event) => {
                setTelegramToken(event.target.value)
                setBotIdentity(null)
                setLinked(null)
              }}
            />
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shrink-0"
            disabled={verifying || (!tokenPresent && !telegramToken.trim())}
            onClick={verifyToken}
          >
            {verifying ? "Checking..." : tokenPresent ? "Check saved bot" : "Check"}
          </Button>
        </div>
        {botIdentity?.username && (
          <p className="text-xs text-success" aria-live="polite">
            Connected to @{botIdentity.username}.
          </p>
        )}
      </div>

      {botIdentity?.username && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium">{tokenPresent ? "2" : "3"}. Say hello to your bot</p>
          <p className="text-xs text-muted-foreground">
            Open{" "}
            <a
              href={botIdentity.link ?? "https://telegram.org"}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              @{botIdentity.username}
            </a>{" "}
            and send it any message. YBM uses that message to learn your IDs and deny everyone else.
          </p>
          {linked ? (
            <p className="text-xs text-success" aria-live="polite">
              Linked to {linked.username ? `@${linked.username}` : linked.first_name ?? "you"}.
            </p>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="self-start"
              disabled={listening}
              onClick={listenForFirstMessage}
            >
              {listening ? "Waiting for your message..." : "I've sent a message"}
            </Button>
          )}
        </div>
      )}

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
