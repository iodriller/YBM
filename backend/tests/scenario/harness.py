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
from agent_control.llm.planner import PlannerService
from agent_control.llm.synthesizer import ResponseSynthesizer
from agent_control.llm.validator import AnswerValidator
from agent_control.orchestration.default_plans import build_default_task_plan, build_evaluator_recovery_plan
from agent_control.orchestration.executor import ToolExecutor
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
) -> Scenario:
    """Build a scenario. Pass ``record_with=<a live LLMProvider>`` to record a
    fresh fixture instead of replaying one - e.g.
    ``record_with=OpenAICompatibleProvider(settings.llm.profiles["openai_saved"])``.
    Every call the worker makes during that run is persisted to
    ``fixtures/<fixture_name>.json``, ready to commit and replay from after.
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

    registry = build_tool_registry(
        settings,
        "http://127.0.0.1:8765",
        provider=provider,
        should_continue=lambda task_id: True,
        artifact_repository=repositories.artifacts,
        task_repository=repositories.tasks,
        repositories=repositories,
        audit_logger=audit,
        telegram_client=None,
    )
    policy = PolicyEngine(settings, audit)
    executor = ToolExecutor(
        policy, repositories, audit,
        adapters=registry.adapters,
        tool_definitions=registry.definition_index,
    )
    planner = PlannerService(provider, repositories, audit, plan_validator=registry.validate_plan)
    synthesizer = ResponseSynthesizer(provider)
    validator = AnswerValidator(provider)

    worker = TaskWorker(
        repositories,
        audit,
        planner=planner,
        executor=executor,
        retry_policy=RetryPolicy(settings.limits),
        config_context=registry.context(),
        default_plan_factory=lambda task: build_default_task_plan(settings, task),
        recovery_plan_factory=lambda task, reason: build_evaluator_recovery_plan(settings, task, reason),
        synthesizer=synthesizer,
        validator=validator,
    )

    return Scenario(settings=settings, repositories=repositories, audit=audit, worker=worker, provider=provider)


async def run_task_to_completion(
    scenario: Scenario,
    objective: str,
    *,
    metadata: dict | None = None,
    max_ticks: int = 10,
) -> TaskRecord:
    """Creates a task and drives worker.process_task() until a terminal
    status. Raises (not hangs) if the task doesn't settle within max_ticks -
    a stuck scenario test should fail fast and loud, not time out silently."""
    task = scenario.repositories.tasks.create(objective=objective, metadata=metadata or {})
    for _ in range(max_ticks):
        task = await scenario.worker.process_task(task.id)
        if task.status in TERMINAL_STATUSES:
            return task
    raise AssertionError(
        f"task {task.id} did not reach a terminal status within {max_ticks} ticks "
        f"(stuck at {task.status}); either the fixture is missing a replan-cycle "
        f"response or this is a real non-terminating loop"
    )
