import { useRef, useState } from "react"
import { LoaderCircle, Mic, Square } from "lucide-react"
import { Button } from "@/components/ui/button"

/**
 * Record a message in the console instead of typing it.
 *
 * Voice worked on Telegram and nowhere else, which is backwards - the console
 * is where someone tries it first.
 *
 * The transcript lands in the composer rather than sending straight away. A
 * chat UI can show you what it heard and let you fix it before committing;
 * a messaging app cannot, which is why Telegram sends immediately and this
 * does not.
 */
export function VoiceRecorder({
  onTranscript,
  onError,
  disabled,
}: {
  onTranscript: (text: string) => void
  onError: (message: string) => void
  disabled?: boolean
}) {
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle")
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  async function start() {
    // getUserMedia needs a secure context; over plain HTTP on a non-loopback
    // host the API is simply absent, which is worth saying rather than
    // failing with "undefined is not a function".
    if (!navigator.mediaDevices?.getUserMedia) {
      onError("Recording needs a secure connection (https or localhost).")
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      onError("I couldn't get to the microphone. Check the browser's permission for this page.")
      return
    }

    const recorder = new MediaRecorder(stream)
    chunksRef.current = []
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data)
    }
    recorder.onstop = async () => {
      // Release the mic immediately - leaving the track live keeps the
      // browser's recording indicator on, which reads as "still listening".
      stream.getTracks().forEach((track) => track.stop())
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" })
      if (blob.size === 0) {
        setState("idle")
        onError("That recording came out empty.")
        return
      }
      setState("transcribing")
      try {
        const form = new FormData()
        form.append("file", blob, "recording.webm")
        const token = sessionStorage.getItem("ybm-admin-token")
        const response = await fetch("/admin/api/chat/transcribe", {
          method: "POST",
          headers: token ? { "X-Agent-Control-Admin-Token": token } : undefined,
          body: form,
        })
        const payload = (await response.json().catch(() => ({}))) as { text?: string; detail?: string }
        if (!response.ok) {
          onError(payload.detail ?? "I couldn't turn that recording into text.")
          return
        }
        if (payload.text) onTranscript(payload.text)
      } catch {
        onError("I couldn't reach the server to transcribe that.")
      } finally {
        setState("idle")
      }
    }
    recorder.start()
    recorderRef.current = recorder
    setState("recording")
  }

  function stop() {
    recorderRef.current?.stop()
    recorderRef.current = null
  }

  const busy = state === "transcribing"
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      disabled={disabled || busy}
      aria-label={state === "recording" ? "Stop recording" : "Record a message"}
      title={state === "recording" ? "Stop recording" : "Record a message"}
      className={`size-9 shrink-0 rounded-xl ${
        state === "recording" ? "text-destructive" : "text-muted-foreground"
      }`}
      onClick={() => (state === "recording" ? stop() : void start())}
    >
      {busy ? (
        <LoaderCircle className="size-4 animate-spin" />
      ) : state === "recording" ? (
        // A filled square is the universal "stop", and the pulse makes it
        // obvious the mic is still open.
        <Square className="size-4 animate-pulse fill-current" />
      ) : (
        <Mic className="size-4" />
      )}
    </Button>
  )
}
