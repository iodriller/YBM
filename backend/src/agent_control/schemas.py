from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,
    )


class ChannelType(StrEnum):
    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    WEB = "web"
    CLI = "cli"


class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    CALLBACK = "callback"
    SYSTEM = "system"


class TaskStatus(StrEnum):
    RECEIVED = "received"
    INTERPRETING = "interpreting"
    CLARIFYING = "clarifying"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_EXTERNAL = "awaiting_external"
    RUNNING = "running"
    PAUSED = "paused"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ScheduleStatus(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"
    DELETED = "deleted"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Capability(StrEnum):
    TELEGRAM_RECEIVE = "telegram.receive"
    TELEGRAM_SEND = "telegram.send"
    LLM_GENERATE = "llm.generate"
    STT_TRANSCRIBE = "stt.transcribe"
    TTS_SYNTHESIZE = "tts.synthesize"
    VSCODE_READ_STATE = "vscode.read_state"
    VSCODE_WRITE_FILES = "vscode.write_files"
    TERMINAL_RUN = "terminal.run"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    DESKTOP_SCREENSHOT = "desktop.screenshot"
    DESKTOP_CONTROL = "desktop.control"
    BROWSER_OPEN = "browser.open"
    BROWSER_CONTROL = "browser.control"
    NETWORK_HTTP = "network.http"
    SCHEDULE_MANAGE = "schedule.manage"
    GITHUB_READ = "github.read"
    GITHUB_PUSH = "github.push"
    DEPENDENCIES_INSTALL = "dependencies.install"


class TaskType(StrEnum):
    DEVELOPMENT = "development"
    CONFIGURATION = "configuration"
    ADMIN_CONTROL = "admin_control"
    DESKTOP_OBSERVATION = "desktop_observation"
    QUESTION = "question"
    STATUS_REQUEST = "status_request"
    OTHER = "other"


class IntentRoute(StrEnum):
    CONVERSATION = "conversation"
    STATUS = "status"
    DESKTOP_OBSERVE = "desktop.observe"
    COMPUTER_USE = "computer.use"
    BROWSER_OPEN = "browser.open"
    BROWSER_CONTROL = "browser.control"
    FILESYSTEM_MANAGE = "filesystem.manage"
    DOCUMENT_MANAGE = "document.manage"
    ARTIFACT_DELIVERY = "artifact.deliver"
    CODE_INTERPRETER = "code.interpreter"
    CODING_AGENT = "coding.agent"
    SCHEDULE_MANAGE = "schedule.manage"
    ADAPTER_FACTORY = "adapter.factory"
    WORKSPACE_MANAGE = "workspace.manage"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class DeliveryKind(StrEnum):
    NONE = "none"
    LATEST = "latest"
    FILE = "file"
    SCREENSHOT = "screenshot"


class CapabilityAccessMode(StrEnum):
    OFF = "off"
    READ_ONLY = "read_only"
    WRITE_ACCESS = "write_access"
    FULL_ACCESS = "full_access"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DENIED = "denied"
    NEEDS_APPROVAL = "needs_approval"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"


class ArtifactType(StrEnum):
    TEXT_LOG = "text_log"
    JSON = "json"
    SCREENSHOT = "screenshot"
    VOICE = "voice"
    TRANSCRIPT = "transcript"
    GENERATED_FILE = "generated_file"
    DOCUMENT = "document"
    EXTERNAL_LINK = "external_link"


class PostconditionType(StrEnum):
    WORKSPACE_DIR = "workspace_dir"
    PREVIEW_URL = "preview_url"
    ADAPTER_PROPOSAL = "adapter_proposal"
    ARTIFACT_DELIVERED = "artifact_delivered"
    DOCUMENT_SUMMARY = "document_summary"
    PRESENTATION_FILE = "presentation_file"
    CODING_AGENT_STEP = "coding_agent_step"
    SCHEDULE_CREATED = "schedule_created"
    BROWSER_STATE = "browser_state"
    DESKTOP_OBSERVATION = "desktop_observation"
    FILE_ORGANIZATION = "file_organization"
    TASK_STATUS = "task_status"
    GITHUB_PR = "github_pr"
    EXTERNAL_COMMAND = "external_command"


class AuditEventType(StrEnum):
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"
    CONFIG_UPDATED = "config_updated"
    TELEGRAM_ACCESS_DECISION = "telegram_access_decision"
    MESSAGE_CLASSIFIED = "message_classified"
    TASK_SPAWN_FAILED = "task_spawn_failed"
    TASK_CREATED = "task_created"
    TASK_STATE_CHANGED = "task_state_changed"
    PLAN_CREATED = "plan_created"
    POLICY_DECISION = "policy_decision"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    TOOL_REQUESTED = "tool_requested"
    TOOL_COMPLETED = "tool_completed"
    ARTIFACT_CREATED = "artifact_created"
    ERROR = "error"


class ErrorClass(StrEnum):
    TRANSIENT = "transient"
    POLICY_DENIED = "policy_denied"
    APPROVAL_TIMEOUT = "approval_timeout"
    VALIDATION_FAILED = "validation_failed"
    ADAPTER_FAILED = "adapter_failed"
    RATE_LIMITED = "rate_limited"
    USAGE_LIMITED = "usage_limited"
    FATAL = "fatal"


class Attachment(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("att"))
    kind: MessageKind
    file_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceAttachment(Attachment):
    kind: MessageKind = MessageKind.VOICE
    duration_seconds: int | None = Field(default=None, ge=0)
    transcript: str | None = None


class InboundMessage(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    channel: ChannelType
    kind: MessageKind
    sender_id: str
    chat_id: str
    text: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=utc_now)
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))
    raw: dict[str, Any] | None = None


class OutboundMessage(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("out"))
    channel: ChannelType
    chat_id: str
    text: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    reply_to_message_id: str | None = None
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))


class CommandEnvelope(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("cmd"))
    type: str
    source: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))


class OrchestrationIntent(StrictBaseModel):
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,
    )

    route: IntentRoute
    operation: str | None = None
    objective: str | None = None
    reasoning: str
    tool_name: str | None = None
    url: str | None = None
    path: str | None = None
    folder_path: str | None = None
    file_path: str | None = None
    query: str | None = None
    provider: str | None = None
    cadence: str | None = None
    schedule_id: str | None = None
    scheduled_objective: str | None = None
    delivery: DeliveryKind = DeliveryKind.NONE
    artifact_type: str | None = None
    page_limit: int | None = Field(default=None, ge=1, le=50)
    form_fields: dict[str, str] = Field(default_factory=dict)
    submit: bool = False
    open_first_result: bool = False
    needs_plan_first: bool = False
    use_external_agent: bool = False
    allow_deletion: bool = False
    allow_overwrite: bool = False

    @field_validator("route", mode="before")
    @classmethod
    def route_accepts_aliases(cls, value: Any) -> Any:
        if isinstance(value, IntentRoute):
            return value
        if value is None:
            return value
        aliases = {
            "browser.research": IntentRoute.BROWSER_OPEN,
            "browser.search": IntentRoute.BROWSER_OPEN,
            "browser.screenshot": IntentRoute.BROWSER_OPEN,
            "browser.summarize": IntentRoute.BROWSER_OPEN,
            "browser.navigate": IntentRoute.BROWSER_CONTROL,
            "browser.fill_form": IntentRoute.BROWSER_CONTROL,
            "browser.form": IntentRoute.BROWSER_CONTROL,
            "filesystem.inspect": IntentRoute.FILESYSTEM_MANAGE,
            "filesystem.search": IntentRoute.FILESYSTEM_MANAGE,
            "filesystem.organize": IntentRoute.FILESYSTEM_MANAGE,
            "filesystem.rename": IntentRoute.FILESYSTEM_MANAGE,
            "filesystem.describe": IntentRoute.FILESYSTEM_MANAGE,
            "document.pdf": IntentRoute.DOCUMENT_MANAGE,
            "document.presentation": IntentRoute.DOCUMENT_MANAGE,
            "desktop.screenshot": IntentRoute.DESKTOP_OBSERVE,
            "python": IntentRoute.CODE_INTERPRETER,
            "python.run": IntentRoute.CODE_INTERPRETER,
            "code.run": IntentRoute.CODE_INTERPRETER,
            "code_interpreter": IntentRoute.CODE_INTERPRETER,
            "coding.codex": IntentRoute.CODING_AGENT,
            "coding.copilot": IntentRoute.CODING_AGENT,
        }
        return aliases.get(str(value).strip().lower(), value)

    @field_validator("operation")
    @classmethod
    def operation_is_simple(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("operation must be a simple operation id")
        return cleaned


class MessageClassification(StrictBaseModel):
    is_task: bool
    task_type: TaskType = TaskType.OTHER
    normalized_objective: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str
    intent: OrchestrationIntent | None = None
    # Populated only when is_task=False (see prompts/base/concierge_system.md) -
    # the Concierge composes the chat reply in the same call it classifies, so
    # a non-task message doesn't need a second LLM round trip. None/empty falls
    # back to TelegramIntakeService's separate `responder` if one is configured
    # (see channels/responder.py) - kept for classifiers that don't populate this.
    reply: str | None = None

    @field_validator("task_type", mode="before")
    @classmethod
    def task_type_accepts_route_aliases(cls, value: Any) -> Any:
        if isinstance(value, TaskType):
            return value
        if value is None:
            return TaskType.OTHER
        route_aliases = {
            "conversation": TaskType.QUESTION,
            "status": TaskType.STATUS_REQUEST,
            "desktop.observe": TaskType.DESKTOP_OBSERVATION,
            "computer.use": TaskType.OTHER,
            "browser.open": TaskType.OTHER,
            "browser.control": TaskType.OTHER,
            "browser.research": TaskType.OTHER,
            "browser.search": TaskType.OTHER,
            "browser.screenshot": TaskType.OTHER,
            "browser.navigate": TaskType.OTHER,
            "browser.fill_form": TaskType.OTHER,
            "filesystem.manage": TaskType.OTHER,
            "filesystem.inspect": TaskType.OTHER,
            "filesystem.search": TaskType.OTHER,
            "filesystem.organize": TaskType.OTHER,
            "filesystem.rename": TaskType.OTHER,
            "filesystem.describe": TaskType.OTHER,
            "document.manage": TaskType.OTHER,
            "document.pdf": TaskType.OTHER,
            "document.presentation": TaskType.OTHER,
            "artifact.deliver": TaskType.OTHER,
            "code.interpreter": TaskType.OTHER,
            "code_interpreter": TaskType.OTHER,
            "python": TaskType.OTHER,
            "python.run": TaskType.OTHER,
            "coding.agent": TaskType.DEVELOPMENT,
            "coding.codex": TaskType.DEVELOPMENT,
            "coding.copilot": TaskType.DEVELOPMENT,
            "schedule.manage": TaskType.OTHER,
            "adapter.factory": TaskType.DEVELOPMENT,
            "workspace.manage": TaskType.DEVELOPMENT,
            "configuration": TaskType.CONFIGURATION,
            "unknown": TaskType.OTHER,
        }
        return route_aliases.get(str(value).strip().lower(), value)


class CapabilityAccessSummary(StrictBaseModel):
    name: str
    label: str | None = None
    mode: CapabilityAccessMode
    capabilities: list[Capability] = Field(default_factory=list)
    options: list[dict[str, str]] = Field(default_factory=list)
    requires_approval: bool = True


class FormattedAuditEvent(StrictBaseModel):
    id: str
    type: AuditEventType
    category: str
    formatted_time: str
    actor: str
    task_id: str | None = None
    title: str
    summary: str
    decision: str | None = None
    reason: str | None = None
    task_type: str | None = None
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ApprovalGate(StrictBaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, validate_assignment=True, use_enum_values=False)
    capability: Capability
    risk_level: RiskLevel
    summary: str
    required_before_step: str | None = None

    @field_validator("capability", mode="before")
    @classmethod
    def map_capability_alias(cls, value: Any) -> Any:
        # Same alias table as PlanStep.required_capabilities — the LLM
        # routinely confuses tool names ("artifact.deliver") with capabilities
        # ("telegram.send"). Without this, the plan is rejected outright and
        # the planner replans the same mistake.
        return _normalize_capability_value(value)


class PlanPostcondition(StrictBaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, validate_assignment=True, use_enum_values=False)
    type: PostconditionType
    description: str
    required: bool = True


# Map common LLM mistakes (tool names / intent routes) to the actual Capability they need.
# This is intentionally generous — the LLM frequently confuses a TOOL it's calling with the
# CAPABILITY that tool requires (e.g. tool "artifact.deliver" needs capability "telegram.send").
# Without this mapping, perfectly correct plans get rejected for a string-level confusion.
_CAPABILITY_ALIASES: dict[str, str] = {
    # Tool-name confusions (tool name → underlying capability)
    "artifact.deliver":       "telegram.send",
    "filesystem.manage":      "filesystem.read",   # write ops should explicitly list filesystem.write
    "document.manage":        "filesystem.read",
    "code.interpreter":       "terminal.run",
    "computer.use":           "desktop.control",
    "task.status":            "llm.generate",
    "coding.agent":           "terminal.run",
    "mcp.client":             "terminal.run",
    "workspace.manage":       "filesystem.write",
    "adapter.factory":        "llm.generate",
    # IntentRoute sub-types → primary capability
    "browser.research":       "browser.open",
    "browser.search":         "browser.open",
    "browser.screenshot":     "browser.open",
    "browser.summarize":      "browser.open",
    "browser.navigate":       "browser.control",
    "browser.fill_form":      "browser.control",
    "browser.form":           "browser.control",
    "http.request":           "network.http",
    "http":                   "network.http",
    "rest":                   "network.http",
    # Common informal names
    "telegram":               "telegram.send",
    "filesystem":             "filesystem.read",
    "browser":                "browser.open",
    "network":                "network.http",
    "desktop":                "desktop.control",
    "code":                   "terminal.run",
    "python":                 "terminal.run",
    "python.run":             "terminal.run",
    "code.run":               "terminal.run",
    "screenshot":             "desktop.screenshot",
}


def _normalize_capability_value(value: Any) -> Any:
    """Translate a single LLM-produced capability string through the alias table.

    Pass-through for already-Capability values or unknown strings (Pydantic will
    then validate or reject downstream). The point is to catch the common
    tool-name-mistaken-for-capability error ("artifact.deliver" → "telegram.send")
    BEFORE Pydantic rejects it.
    """
    if isinstance(value, Capability) or value is None:
        return value
    key = str(value).strip().lower()
    return _CAPABILITY_ALIASES.get(key, value)


def _normalize_capabilities(value: Any) -> Any:
    """Translate common LLM mistakes in required_capabilities to valid enum values."""
    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, Capability):
            normalized.append(item)
            seen.add(item.value)
            continue
        if item is None:
            continue
        mapped = _normalize_capability_value(item)
        # dedupe after mapping (multiple aliases can collapse to same capability)
        marker = mapped.value if isinstance(mapped, Capability) else str(mapped).strip().lower()
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(mapped)
    return normalized


class PlanStep(StrictBaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, validate_assignment=True, use_enum_values=False)
    id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    description: str
    required_capabilities: list[Capability] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    tool_name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    expected_output: str | None = None

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def map_capability_aliases(cls, value: Any) -> Any:
        return _normalize_capabilities(value)


class PlanModel(StrictBaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, validate_assignment=True, use_enum_values=False)
    id: str = Field(default_factory=lambda: new_id("plan"))
    objective: str
    assumptions: list[str] = Field(default_factory=list)
    required_capabilities: list[Capability] = Field(default_factory=list)
    approval_gates: list[ApprovalGate] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    postconditions: list[PlanPostcondition] = Field(default_factory=list)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def map_capability_aliases(cls, value: Any) -> Any:
        return _normalize_capabilities(value)

    @field_validator("postconditions", mode="before")
    @classmethod
    def drop_invalid_postconditions(cls, value: Any) -> Any:
        """Postconditions are optional and inferred by the fulfillment validator.

        Rather than reject the whole plan because the LLM invented a postcondition
        type (e.g. ``"screenshot"`` instead of ``"desktop_observation"``), silently
        drop entries that don't validate. The plan steps still execute correctly.
        """
        if not isinstance(value, list):
            return value
        kept: list[Any] = []
        for item in value:
            if isinstance(item, PlanPostcondition):
                kept.append(item)
                continue
            if not isinstance(item, dict):
                continue
            try:
                kept.append(PlanPostcondition.model_validate(item))
            except Exception:
                # Drop silently — leaving postconditions empty lets the fulfillment
                # validator infer them from objective + step tool names.
                continue
        return kept

    @field_validator("steps")
    @classmethod
    def plan_requires_steps(cls, value: list[PlanStep]) -> list[PlanStep]:
        if not value:
            raise ValueError("plan must contain at least one step")
        return value


class OperatorAction(StrEnum):
    """What the Operator loop's decide() call chose to do next.

    See docs/ROADMAP.md P3 / orchestration/operator.py - the additive
    observe/decide/act alternative to plan-once-then-replan.
    """
    CALL_TOOL = "call_tool"
    DONE = "done"
    ASK_USER = "ask_user"
    BLOCKED = "blocked"


class OperatorDecision(StrictBaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, validate_assignment=True, use_enum_values=False)
    action: OperatorAction
    reasoning: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    # ToolDefinition carries no static risk level - like PlanStep, the model
    # declares it per call (low for reads, high for writes/browser control,
    # critical for desktop UI control) and PolicyEngine.evaluate() gates on
    # it against the capability's max_risk_level.
    risk_level: RiskLevel = RiskLevel.LOW
    final_answer: str | None = None
    question: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def call_tool_requires_tool_name(self) -> "OperatorDecision":
        if self.action == OperatorAction.CALL_TOOL and not self.tool_name:
            raise ValueError("action=call_tool requires tool_name")
        return self


class TaskRecord(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    objective: str
    status: TaskStatus = TaskStatus.RECEIVED
    conversation_id: str | None = None
    plan_id: str | None = None
    current_step_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduleRecord(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("schedule"))
    source_channel: ChannelType = ChannelType.TELEGRAM
    source_chat_id: str | None = None
    objective: str
    cadence: str
    timezone: str = "America/Chicago"
    status: ScheduleStatus = ScheduleStatus.ENABLED
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SubtaskRecord(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("subtask"))
    task_id: str
    title: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    dependencies: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskSignal(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("sig"))
    task_id: str
    signal: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ToolCallRequest(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("toolreq"))
    task_id: str
    tool_name: str
    capability: Capability
    risk_level: RiskLevel = RiskLevel.LOW
    scope_target: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1)
    idempotency_key: str = Field(default_factory=lambda: new_id("idem"))
    requires_approval: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ToolCallResult(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("toolres"))
    request_id: str
    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error_class: ErrorClass | None = None
    error_message: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=utc_now)


class ToolObservation(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("obs"))
    task_id: str
    tool_name: str
    content: dict[str, Any]
    observed_at: datetime = Field(default_factory=utc_now)


class ApprovalRequest(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    task_id: str
    capability: Capability
    risk_level: RiskLevel
    summary: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalDecision(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("approval_decision"))
    approval_request_id: str
    status: ApprovalStatus
    actor: str
    reason: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)

    @field_validator("status")
    @classmethod
    def decision_must_be_terminal(cls, value: ApprovalStatus) -> ApprovalStatus:
        if value == ApprovalStatus.PENDING:
            raise ValueError("approval decision cannot remain pending")
        return value


class Artifact(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    task_id: str | None = None
    type: ArtifactType
    uri: str | None = None
    content_preview: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("audit"))
    type: AuditEventType
    actor: str
    task_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TranscriptionResult(StrictBaseModel):
    text: str
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
