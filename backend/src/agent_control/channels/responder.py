from __future__ import annotations

from typing import Protocol

from agent_control.channels.memory import memory_context
from agent_control.config import AppSettings
from agent_control.llm.providers import LLMProvider
from agent_control.prompts import render_prompt
from agent_control.schemas import Capability, InboundMessage, TaskStatus
from agent_control.storage.repositories import Repositories


class TelegramResponder(Protocol):
    async def answer(self, message: InboundMessage, conversation_id: str | None = None) -> str:
        ...


class LLMTelegramResponder:
    def __init__(self, provider: LLMProvider, settings: AppSettings, repositories: Repositories) -> None:
        self.provider = provider
        self.settings = settings
        self.repositories = repositories

    async def answer(self, message: InboundMessage, conversation_id: str | None = None) -> str:
        return await self.provider.generate_text(
            _system_prompt(),
            _user_prompt(message, gateway_context(self.settings, self.repositories, conversation_id)),
        )


class StaticTelegramResponder:
    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.messages: list[InboundMessage] = []

    async def answer(self, message: InboundMessage, conversation_id: str | None = None) -> str:
        self.messages.append(message)
        return self.response


def _system_prompt() -> str:
    return render_prompt("base/telegram_gateway_system.md")


def _user_prompt(message: InboundMessage, context: str) -> str:
    return render_prompt("tasks/telegram_gateway_user.md", context=context, message_text=message.text or "")


def gateway_context(settings: AppSettings, repositories: Repositories, conversation_id: str | None = None) -> str:
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
    workspace_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    workspace_enabled = bool(settings.adapters.workspace.enabled and workspace_policy and workspace_policy.enabled)
    workspace_approval = "approval-free" if workspace_policy and not workspace_policy.requires_approval else "approval-gated"
    adapter_factory_enabled = bool(settings.adapters.adapter_factory.enabled and workspace_policy and workspace_policy.enabled)
    memory_record = repositories.conversation_memory.get(conversation_id) if conversation_id else None
    memory = memory_context(memory_record, remembered_facts=repositories.memory_facts.list_all())

    return f"""LLM profile: {settings.llm.default_profile}
Telegram receive/send: enabled
VS Code/GitHub Copilot terminal route: {'enabled' if vscode_enabled else 'disabled'} ({vscode_approval})
Local workspaces and localhost previews: {'enabled' if workspace_enabled else 'disabled'} ({workspace_approval}); root={settings.adapters.workspace.root_dir}
Generated adapter proposal cache: {'enabled' if adapter_factory_enabled else 'disabled'}; root={settings.adapters.adapter_factory.root_dir}
Terminal command route: {'enabled' if terminal_enabled else 'disabled'} ({terminal_approval})
Desktop screenshots: {'enabled' if screenshot_enabled else 'disabled'}
Conversation memory:
{memory}
Recent tasks: {len(tasks)}
Active tasks: {active_count}
Recent task list:
{recent}"""
