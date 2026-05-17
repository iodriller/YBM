from __future__ import annotations

from typing import Protocol

from agent_control.config import AppSettings
from agent_control.llm.providers import LLMProvider
from agent_control.schemas import Capability, InboundMessage, TaskStatus
from agent_control.storage.repositories import Repositories


class TelegramResponder(Protocol):
    async def answer(self, message: InboundMessage) -> str:
        ...


class LLMTelegramResponder:
    def __init__(self, provider: LLMProvider, settings: AppSettings, repositories: Repositories) -> None:
        self.provider = provider
        self.settings = settings
        self.repositories = repositories

    async def answer(self, message: InboundMessage) -> str:
        return await self.provider.generate_text(
            _system_prompt(),
            _user_prompt(message, _gateway_context(self.settings, self.repositories)),
        )


class StaticTelegramResponder:
    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.messages: list[InboundMessage] = []

    async def answer(self, message: InboundMessage) -> str:
        self.messages.append(message)
        return self.response


def _system_prompt() -> str:
    return """You are the Telegram gateway for a local agent-control system.
Answer direct questions concisely.
Use the provided runtime context for current capabilities and task state.
Do not claim a capability is enabled unless the context says it is enabled.
If the user asks for work that should be executed by tools, say it should be sent as a task and mention the relevant enabled route."""


def _user_prompt(message: InboundMessage, context: str) -> str:
    return f"""Runtime context:
{context}

Telegram message:
{message.text or ""}

Reply in plain text suitable for Telegram."""


def _gateway_context(settings: AppSettings, repositories: Repositories) -> str:
    tasks = repositories.tasks.list_recent(5)
    active_statuses = {TaskStatus.RECEIVED, TaskStatus.INTERPRETING, TaskStatus.PLANNED, TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.AWAITING_APPROVAL}
    active_count = len([task for task in tasks if task.status in active_statuses])
    recent = "\n".join(f"- {task.id}: {task.status.value} - {task.objective[:120]}" for task in tasks) or "- none"

    vscode_policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    vscode_enabled = bool(settings.adapters.vscode.enabled and vscode_policy and vscode_policy.enabled)
    vscode_approval = "approval-free" if vscode_policy and not vscode_policy.requires_approval else "approval-gated"

    terminal_policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    terminal_enabled = bool(terminal_policy and terminal_policy.enabled)
    terminal_approval = "approval-free" if terminal_policy and not terminal_policy.requires_approval else "approval-gated"

    screenshot_policy = settings.capabilities.get(Capability.DESKTOP_SCREENSHOT)
    screenshot_enabled = bool(screenshot_policy and screenshot_policy.enabled)

    return f"""LLM profile: {settings.llm.default_profile}
Telegram receive/send: enabled
VS Code/GitHub Copilot terminal route: {'enabled' if vscode_enabled else 'disabled'} ({vscode_approval})
Terminal command route: {'enabled' if terminal_enabled else 'disabled'} ({terminal_approval})
Desktop screenshots: {'enabled' if screenshot_enabled else 'disabled'}
Recent tasks: {len(tasks)}
Active tasks: {active_count}
Recent task list:
{recent}"""
