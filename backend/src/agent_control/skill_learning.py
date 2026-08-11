"""Turn a task that worked into a reusable procedure - after review.

Skills are now visible to the Operator, but the catalog only ever contains
what a human wrote by hand. Everything needed to grow it is already recorded:
`operator_history` holds each tool, its input, and (since the progress work)
the model's own reason for choosing it. That is a procedure, written down, by
the run that succeeded.

Same discipline as persona learning, for the same reason: a skill is
instructions the Operator will follow on future tasks, so a system that
writes its own skills unattended can change how it behaves tomorrow with no
one having agreed to it. Proposals queue; a human installs them.

Deliberately conservative about what is even worth proposing - a catalog full
of near-duplicate machine-written skills is worse than a small hand-written
one, because the index goes into every Operator prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from agent_control.config import SkillsAdapterConfig
from agent_control.schemas import TaskRecord, new_id, utc_now
from agent_control.tools.skills import _load_skills, write_skill_file


# Below this, the task was too trivial to be a procedure worth writing down.
MIN_STEPS_FOR_A_SKILL = 3

SKILL_SYSTEM_PROMPT = """You write short reusable procedures from a task that already succeeded.

Return ONLY compact JSON:
{"name": "...", "description": "...", "body": "..."}
or NONE.

- name: 3-5 words, the KIND of job, never this one instance
  (good "Invoice Total Extraction" / bad "Extract total from march-invoice.pdf")
- description: one line on when to use it
- body: numbered steps, naming the tools and operations that worked, written
  so they apply to the next job of this kind - no paths, dates or values from
  this specific run
- NONE when this was a one-off, trivial, or too specific to generalise.
  Prefer NONE: a catalog of near-duplicates is worse than a small one."""


@dataclass
class SkillSuggestion:
    id: str
    name: str
    description: str
    body: str
    task_id: str | None
    objective: str
    created_at: str
    status: str = "pending"  # pending | accepted | rejected
    decided_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillSuggestionStore:
    path: Path
    suggestions: list[SkillSuggestion] = field(default_factory=list)

    @classmethod
    def load(cls, config: SkillsAdapterConfig) -> "SkillSuggestionStore":
        path = Path(config.root_dir).expanduser() / "_suggestions.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=path, suggestions=[])
        return cls(path=path, suggestions=[SkillSuggestion(**item) for item in raw if isinstance(item, dict)])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([s.to_dict() for s in self.suggestions], indent=2), encoding="utf-8")

    def pending(self) -> list[SkillSuggestion]:
        return [s for s in self.suggestions if s.status == "pending"]

    def add(self, name: str, description: str, body: str, task: TaskRecord) -> SkillSuggestion | None:
        normalized = name.strip().lower()
        for existing in self.suggestions:
            if existing.status in ("pending", "accepted") and existing.name.strip().lower() == normalized:
                return None
        suggestion = SkillSuggestion(
            id=new_id("skill_sugg"),
            name=name.strip(),
            description=description.strip(),
            body=body.strip(),
            task_id=task.id,
            objective=task.objective[:300],
            created_at=utc_now().isoformat(),
        )
        self.suggestions.append(suggestion)
        self.save()
        return suggestion

    def decide(self, suggestion_id: str, accept: bool) -> SkillSuggestion | None:
        for suggestion in self.suggestions:
            if suggestion.id == suggestion_id and suggestion.status == "pending":
                suggestion.status = "accepted" if accept else "rejected"
                suggestion.decided_at = utc_now().isoformat()
                self.save()
                return suggestion
        return None


def install(config: SkillsAdapterConfig, suggestion: SkillSuggestion) -> dict[str, Any]:
    return write_skill_file(
        config.root_dir,
        suggestion.name,
        suggestion.description,
        f"{suggestion.body.strip()}\n\n_Learned from a completed task on "
        f"{suggestion.created_at[:10]}._",
    )


def _successful_steps(task: TaskRecord) -> list[dict[str, Any]]:
    history = task.metadata.get("operator_history")
    if not isinstance(history, list):
        return []
    return [
        step for step in history
        if isinstance(step, dict)
        and step.get("status") == "succeeded"
        and not str(step.get("tool_name") or "").startswith("_")
    ]


def worth_proposing(task: TaskRecord, config: SkillsAdapterConfig) -> bool:
    """Cheap checks before spending an LLM call.

    A two-step task is not a procedure, and a job the catalog already covers
    does not need a second entry - the index is injected into every Operator
    prompt, so every skill has a permanent cost.
    """
    if not config.enabled or not config.learning_enabled:
        return False
    steps = _successful_steps(task)
    if len(steps) < MIN_STEPS_FOR_A_SKILL:
        return False
    return len(_load_skills(config.root_dir)) < config.max_skills_listed


async def propose_from_task(provider: Any, config: SkillsAdapterConfig, task: TaskRecord) -> SkillSuggestion | None:
    if provider is None or not worth_proposing(task, config):
        return None
    steps = _successful_steps(task)
    rendered = "\n".join(
        f"{index}. {step.get('tool_name')} "
        f"{(step.get('input') or {}).get('operation', '')} - {step.get('reasoning') or ''}".strip()
        for index, step in enumerate(steps, 1)
    )
    prompt = f"Objective: {task.objective}\n\nSteps that worked:\n{rendered}"
    try:
        raw = await provider.generate_text(SKILL_SYSTEM_PROMPT, prompt[:4000])
    except Exception:  # noqa: BLE001 - never fail a finished task over this
        return None
    text = (raw or "").strip()
    if not text or text.upper().startswith("NONE"):
        return None
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        payload = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not (name and description and body):
        return None
    return SkillSuggestionStore.load(config).add(name, description, body, task)
