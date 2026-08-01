import { useState } from "react"
import { Plus, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SkillCard } from "@/components/skills/SkillCard"
import { SkillInstallForm } from "@/components/skills/SkillInstallForm"
import { useSkills } from "@/lib/queries"

/**
 * Skill catalog (docs/UI_UX_AUDIT.md Phase 5): install/uninstall entirely
 * from the console, with inferred permission labels shown up front -
 * replacing "drop a markdown file into adapters.skills.root_dir by hand".
 */
export function SkillsPage() {
  const { data, isPending, isError, error } = useSkills()
  const [installing, setInstalling] = useState(false)

  const skills = data?.skills ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 p-4 sm:p-6 lg:p-8 [&>*]:shrink-0">
        <PageHeader
          eyebrow="Capability packs"
          title="Skills"
          description="Instructions YBM can read when they're relevant to a task - a runbook, a house style guide, anything that's expertise rather than an action. A skill can't execute anything on its own."
          actions={
            !installing && (
              <Button size="sm" onClick={() => setInstalling(true)}>
                <Plus className="size-4" />
                Install a skill
              </Button>
            )
          }
        />

        {installing && <SkillInstallForm onDone={() => setInstalling(false)} />}

        {isPending && (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {isError && (
          <Alert variant="destructive">
            <AlertTitle>Couldn&apos;t load skills</AlertTitle>
            <AlertDescription>{error?.message ?? "Unknown error"}</AlertDescription>
          </Alert>
        )}

        {!isPending && !isError && skills.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
              <span className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Sparkles className="size-5" />
              </span>
              <p className="text-sm font-medium">No skills installed yet.</p>
              <p className="max-w-sm text-xs text-muted-foreground">
                Install one above, or drop a markdown file into {data?.root_dir ?? "the skills directory"} directly -
                either way it's available on the worker's next call.
              </p>
            </CardContent>
          </Card>
        )}

        {skills.length > 0 && (
          <div className="flex flex-col gap-3">
            {skills.map((skill) => (
              <SkillCard key={skill.name} skill={skill} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
