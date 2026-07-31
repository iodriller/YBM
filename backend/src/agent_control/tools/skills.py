"""User-droppable capability packs (docs/HISTORY.md Part 4 T1.3).

A skill is one markdown file with YAML frontmatter (`name`, `description`)
and a body of instructions - a runbook, a house style guide, "how our
invoices are shaped and where the total is", anything that's expertise
rather than an action. Adding one is copying a file into
`adapters.skills.root_dir`; there is no code change and no adapter to write.

Progressive disclosure, same idea as Claude Code's Skills: `list` (always
cheap - name + one-line description only, so having 30 skills installed
doesn't bloat every single prompt) and `read` (loads one skill's full body,
called only when the Operator has already decided that skill is relevant to
the current objective).

This is deliberately NOT a way to run code - `code.interpreter` already does
that, safely, with sandboxing and approval gates. A skill is text the model
reads, then acts on with its normal tools; it has no execution capability of
its own, so there is nothing here for a malicious or malformed skill file to
directly exploit beyond what any other text in the prompt could do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_control.config import SkillsAdapterConfig
from agent_control.schemas import Capability, ToolCallRequest, ToolCallResult, ToolResultStatus
from agent_control.tools.contracts import SkillsInput, SkillsOutput
from agent_control.tools.spec import (
    Adapters,
    Definitions,
    RegistryDeps,
    ToolDefinition,
    capability_enabled,
    failed_result,
    same_output_schema,
)


class SkillsAdapter:
    def __init__(self, config: SkillsAdapterConfig) -> None:
        self.config = config

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        operation = str(request.input.get("operation") or "list")
        try:
            if operation == "list":
                output = self._list()
            elif operation == "read":
                output = self._read(request)
            else:
                return failed_result(request, f"unsupported skills operation: {operation}")
        except Exception as exc:
            return failed_result(request, f"skills operation failed: {exc}")
        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _list(self) -> dict[str, Any]:
        skills = _load_skills(self.config.root_dir)[: self.config.max_skills_listed]
        entries = [{"name": s["name"], "description": s["description"]} for s in skills]
        summary = (
            f"{len(entries)} skill(s) available."
            if entries
            else f"No skills found in {self.config.root_dir}."
        )
        return {"summary": summary, "skills": entries}

    def _read(self, request: ToolCallRequest) -> dict[str, Any]:
        name = str(request.input["name"]).strip()
        skills = _load_skills(self.config.root_dir)
        match = next((s for s in skills if s["name"] == name), None)
        if match is None:
            available = ", ".join(sorted(s["name"] for s in skills)) or "(none)"
            raise ValueError(f"no skill named {name!r} - available: {available}")
        return {
            "summary": f"Loaded skill {name!r} ({len(match['body'])} chars).",
            "skills": [match],
        }


def _load_skills(root_dir: str) -> list[dict[str, Any]]:
    """Every valid skill file under root_dir, sorted by name.

    Reads fresh each call rather than caching - skill files are small,
    local, and edited by hand; a worker process should see a newly dropped
    or edited file on its very next call, not after a restart.
    """
    root = Path(root_dir).expanduser()
    if not root.is_dir():
        return []
    skills = []
    for path in sorted(root.glob("*.md")):
        parsed = _parse_skill_file(path)
        if parsed is not None:
            skills.append(parsed)
    return sorted(skills, key=lambda s: s["name"])


def _parse_skill_file(path: Path) -> dict[str, Any] | None:
    """None on anything malformed (missing frontmatter, invalid YAML, no
    name) - a broken skill file must not crash tool registration or hide
    every OTHER valid skill in the directory, just itself."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    _blank, frontmatter_text, body = parts
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    name = str(frontmatter.get("name") or "").strip()
    if not name:
        return None
    description = str(frontmatter.get("description") or "").strip() or "(no description)"
    return {"name": name, "description": description, "body": body.strip(), "path": str(path)}


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    lines = [output.get("summary") or f"skills.use {operation} completed."]
    for skill in output.get("skills") or []:
        if skill.get("body"):
            lines.append(f"\n--- {skill['name']} ---\n{skill['body']}")
        else:
            lines.append(f"- {skill['name']}: {skill.get('description', '')}")
    return {
        "instance_id": "local-worker",
        "terminal_id": "skills",
        "content": "\n".join(lines),
        "is_final": True,
        "exit_code": 0,
        "source": "skills",
    }


def register(deps: RegistryDeps, definitions: Definitions, adapters: Adapters) -> None:
    settings = deps.settings
    # Reuses TELEGRAM_RECEIVE, same as task.status: reading local
    # instructional markdown has no side effects and no meaningful risk
    # beyond "the bot works at all" - see this module's docstring. A skill's
    # CONTENT can only influence what the model reads, never act on its own;
    # any action it prompts the model toward still goes through that tool's
    # own capability gate.
    enabled = capability_enabled(settings, Capability.TELEGRAM_RECEIVE) and settings.adapters.skills.enabled
    definitions.append(
        ToolDefinition(
            name="skills.use",
            capability=Capability.TELEGRAM_RECEIVE,
            enabled=enabled,
            description=(
                "list available user-defined skills (name + one-line description), or read one "
                "skill's full instructions by name"
            ),
            operations=("list", "read"),
            input_schema=SkillsInput,
            output_schema=SkillsOutput,
            operation_output_schemas=same_output_schema(("list", "read"), SkillsOutput),
            default_operation="list",
            examples=(
                {"operation": "list"},
                {"operation": "read", "name": "{{skill_name}}"},
            ),
        )
    )
    if enabled:
        adapters["skills.use"] = SkillsAdapter(settings.adapters.skills)
