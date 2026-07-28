"""Scenario-test harness: the real worker/planner/policy/registry/executor
stack wired against a temp DB and temp filesystem, with a ScriptedLLMProvider
standing in for the LLM. Mirrors agent_control.cli.run_worker()'s production
wiring - see docs/ROADMAP.md P2 for why this tier exists (nothing between
mocked unit tests and a live Telegram+LLM+desktop E2E run previously did).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from agent_control.config import AppSettings
from agent_control.orchestration.auditor import AuditorService
from agent_control.orchestration.executor import ToolExecutor
from agent_control.orchestration.operator import OperatorLoopService
from agent_control.orchestration.worker import TaskWorker
from agent_control.policy.engine import PolicyEngine
from agent_control.recovery.retry import RetryPolicy
from agent_control.schemas import TaskRecord, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.testing.scripted_llm import RecordingLLMProvider, ScriptedLLMProvider
from agent_control.tools.registry import build_tool_registry


FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Recorded prompts are keyed on exact text (see testing/scripted_llm.py). Any
# objective that mentions a filesystem path needs that path to be identical
# on every run, so it can't use pytest's tmp_path (randomized per run) -
# use this instead. Stable across repeated runs *on this machine*; not yet
# portable across machines/clone paths (the objective text embeds an
# absolute path) - a real limitation, tracked in docs/ROADMAP.md P2, not one
# to design around today.
SCENARIO_SCRATCH_ROOT = Path(tempfile.gettempdir()) / "ybm_scenario_scratch"


def scenario_scratch_dir(name: str) -> Path:
    """A stable, empty directory at the same absolute path every run."""
    path = SCENARIO_SCRATCH_ROOT / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path

class FakeTelegramClient:
    """Records delivered files instead of calling the real Telegram API.
    Scenario tasks aren't created from a real Telegram update, so
    ``run_task_to_completion`` stamps ``source_chat_id`` into task metadata by
    default (mirroring what the production message handler sets) - without
    it, ``artifact.deliver`` raises "chat_id is required" for any plan that
    delivers a created file, which the planner does routinely (see
    docs/ROADMAP.md P2 - discovered while recording the
    code_interpreter_csv_summary fixture)."""

    def __init__(self) -> None:
        self.photos: list[tuple[str | int, str, str | None]] = []
        self.documents: list[tuple[str | int, str, str | None]] = []

    async def send_photo_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict:
        self.photos.append((chat_id, path, caption))
        return {"ok": True, "method": "sendPhoto"}

    async def send_document_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict:
        self.documents.append((chat_id, path, caption))
        return {"ok": True, "method": "sendDocument"}


SCENARIO_CHAT_ID = "scenario_test_chat"

TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.BLOCKED,
    TaskStatus.CANCELLED,
    TaskStatus.AWAITING_EXTERNAL,
    TaskStatus.CLARIFYING,
}


@dataclass
class Scenario:
    settings: AppSettings
    repositories: Repositories
    audit: AuditLogger
    worker: TaskWorker
    provider: ScriptedLLMProvider | RecordingLLMProvider
    telegram: FakeTelegramClient


def isolated_settings(monkeypatch, tmp_path: Path, **overrides) -> AppSettings:
    """Build AppSettings with zero influence from this repo's real
    config/config.yaml or .env.

    AppSettings(_env_file=None, adapters={...}) is NOT enough on its own:
    pydantic-settings deep-merges a partial ``adapters=`` override with
    whatever config/config.yaml supplies for fields the override doesn't
    mention (confirmed the hard way - a scenario test silently inherited
    this repo's real adapters.computer_use.allowed_roots and passed only by
    accident). chdir'ing to an empty tmp_path first means the relative
    default yaml_file path ("config/config.yaml") resolves to nothing.
    """
    monkeypatch.chdir(tmp_path)
    return AppSettings(_env_file=None, **overrides)


def build_scenario(
    settings: AppSettings,
    *,
    tmp_path: Path,
    fixture_name: str,
    record_with: object | None = None,
    include_auditor: bool = False,
) -> Scenario:
    """Build a scenario. Pass ``record_with=<a live LLMProvider>`` to record a
    fresh fixture instead of replaying one - e.g.
    ``record_with=OpenAICompatibleProvider(settings.llm.profiles["openai_saved"])``.
    Every call the worker makes during that run is persisted to
    ``fixtures/<fixture_name>.json``, ready to commit and replay from after.

    Wires the Operator loop (P3 §2.2), mirroring cli.run_worker()'s
    production wiring - the sole execution path since 2026-07-28.

    ``include_auditor=True`` also wires the Auditor (P3 §2.1) - opt-in, not
    the default, because it adds a `generate_text` call the fixture has to
    have recorded; existing fixtures recorded before the Auditor existed
    don't have it and would fail fixture lookup if this defaulted on.
    """
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repositories = Repositories.for_database(database)
    audit = AuditLogger(repositories.audit)

    fixture_path = FIXTURES_DIR / f"{fixture_name}.json"
    provider: ScriptedLLMProvider | RecordingLLMProvider
    if record_with is not None:
        provider = RecordingLLMProvider(record_with, fixture_path)
    else:
        provider = ScriptedLLMProvider(fixture_path)

    telegram = FakeTelegramClient()
    registry = build_tool_registry(
        settings,
        "http://127.0.0.1:8765",
        provider=provider,
        should_continue=lambda task_id: True,
        artifact_repository=repositories.artifacts,
        task_repository=repositories.tasks,
        repositories=repositories,
        audit_logger=audit,
        telegram_client=telegram,
    )
    policy = PolicyEngine(settings, audit)
    executor = ToolExecutor(
        policy, repositories, audit,
        adapters=registry.adapters,
        tool_definitions=registry.definition_index,
    )
    operator = OperatorLoopService(provider)
    auditor = AuditorService(provider) if include_auditor else None

    worker = TaskWorker(
        repositories,
        audit,
        executor=executor,
        retry_policy=RetryPolicy(settings.limits),
        config_context=registry.context(),
        operator=operator,
        operator_max_steps=settings.operator.max_steps,
        auditor=auditor,
    )

    return Scenario(
        settings=settings, repositories=repositories, audit=audit, worker=worker, provider=provider,
        telegram=telegram,
    )


async def run_task_to_completion(
    scenario: Scenario,
    objective: str,
    *,
    metadata: dict | None = None,
    max_ticks: int = 10,
) -> TaskRecord:
    """Creates a task and drives worker.process_task() until a terminal
    status. Raises (not hangs) if the task doesn't settle within max_ticks -
    a stuck scenario test should fail fast and loud, not time out silently.

    Stamps ``source_chat_id`` by default, mirroring what the production
    Telegram message handler sets on every real task - without it, any plan
    step that delivers a file (``artifact.deliver``) fails with a synthetic
    "chat_id is required" error that would never happen in production."""
    task_metadata = {"source_chat_id": SCENARIO_CHAT_ID, **(metadata or {})}
    task = scenario.repositories.tasks.create(objective=objective, metadata=task_metadata)
    for _ in range(max_ticks):
        task = await scenario.worker.process_task(task.id)
        if task.status in TERMINAL_STATUSES:
            return task
    raise AssertionError(
        f"task {task.id} did not reach a terminal status within {max_ticks} ticks "
        f"(stuck at {task.status}); either the fixture is missing a replan-cycle "
        f"response or this is a real non-terminating loop"
    )
