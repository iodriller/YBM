import { ExternalLink, File, FileJson, FileText, Image, Mic } from "lucide-react"
import type { Artifact } from "@/lib/api"

const TYPE_ICON: Record<string, typeof File> = {
  text_log: FileText,
  json: FileJson,
  screenshot: Image,
  voice: Mic,
  transcript: FileText,
  generated_file: File,
  document: FileText,
  external_link: ExternalLink,
}

const TYPE_LABEL: Record<string, string> = {
  text_log: "Log",
  json: "JSON",
  screenshot: "Screenshot",
  voice: "Voice",
  transcript: "Transcript",
  generated_file: "Generated file",
  document: "Document",
  external_link: "Link",
}

function basename(uri: string): string {
  const cleaned = uri.split(/[?#]/)[0]
  const parts = cleaned.split(/[/\\]/)
  return parts[parts.length - 1] || uri
}

/**
 * A file/output a task produced (docs/UI_UX_AUDIT.md Phase 1) - previously
 * only visible by opening the task's trace and reading raw JSON.
 * `uri` is a local filesystem path for most artifact types (there's no
 * download/serve endpoint yet - that's Phase 2 Receipts' "export" scope),
 * so only an actual http(s) URL is rendered as a clickable link; everything
 * else shows the path as informational text.
 */
export function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const Icon = TYPE_ICON[artifact.type] ?? File
  const label = TYPE_LABEL[artifact.type] ?? artifact.type
  const isWebLink = artifact.uri != null && /^https?:\/\//.test(artifact.uri)
  const name = artifact.uri ? basename(artifact.uri) : label

  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-3 py-2">
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-background text-muted-foreground">
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        {isWebLink ? (
          <a
            href={artifact.uri!}
            target="_blank"
            rel="noopener noreferrer"
            className="block truncate text-xs font-medium text-primary underline underline-offset-2 hover:no-underline"
          >
            {name}
          </a>
        ) : (
          <p className="truncate text-xs font-medium" title={artifact.uri ?? undefined}>
            {name}
          </p>
        )}
        <p className="text-[11px] text-muted-foreground">
          {label}
          {artifact.content_preview && ` · ${artifact.content_preview}`}
        </p>
      </div>
    </div>
  )
}
