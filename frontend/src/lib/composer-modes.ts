import { Globe, Telescope, TerminalSquare } from "lucide-react"

export type ComposerMode = {
  key: string
  label: string
  hint: string
  icon: typeof Globe
  capability: string
  instruction: string
}

export const COMPOSER_MODES: ComposerMode[] = [
  {
    key: "web_search",
    label: "Search the web",
    hint: "Look things up online before answering",
    icon: Globe,
    capability: "browser.open",
    instruction: "Search the web before answering.",
  },
  {
    key: "deep_research",
    label: "Deep research",
    hint: "Reads many sources and writes up findings — takes several minutes",
    icon: Telescope,
    capability: "browser.open",
    instruction:
      "Research this thoroughly across multiple sources using browser research_pages, then write up what you found with links.",
  },
  {
    key: "code",
    label: "Run code",
    hint: "Compute, transform data, or check a result by running it",
    icon: TerminalSquare,
    capability: "code.execute",
    instruction: "Work this out by running code rather than answering from memory.",
  },
]
