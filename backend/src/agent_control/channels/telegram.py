from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx

from agent_control.config import TelegramConfig
from agent_control.schemas import (
    ApprovalStatus,
    AuditEventType,
    Artifact,
    ArtifactType,
    ChannelType,
    CommandEnvelope,
    InboundMessage,
    MessageKind,
    TaskRecord,
    TaskSignal,
    TaskStatus,
    VoiceAttachment,
)
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories
from agent_control.tools.stt import STTAdapter


@dataclass(frozen=True)
class TelegramUpdateResult:
    authorized: bool
    inbound_message: InboundMessage | None = None
    command: CommandEnvelope | None = None
    signal: TaskSignal | None = None
    task: TaskRecord | None = None
    denial_reason: str | None = None


def load_telegram_token(config: TelegramConfig) -> str:
    if config.token:
        return config.token.get_secret_value()
    token = os.getenv(config.token_env)
    if not token:
        raise RuntimeError(f"Telegram token not found in {config.token_env}")
    return token


class TelegramBotApi:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    async def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = await self._post("getUpdates", payload)
        return list(data.get("result", []))

    async def send_message(self, chat_id: str | int, text: str) -> dict[str, Any]:
        return await self._post("sendMessage", {"chat_id": chat_id, "text": text})

    async def get_file(self, file_id: str) -> dict[str, Any]:
        data = await self._post("getFile", {"file_id": file_id})
        return dict(data.get("result", {}))

    async def download_file(self, file_path: str) -> bytes:
        url = f"{self.base_url}/file/bot{self.token}/{file_path}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return await self._post("answerCallbackQuery", payload)

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/bot{self.token}/{method}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram Bot API call failed: {method}")
        return data


class TelegramPollingRunner:
    def __init__(self, client: TelegramBotApi, intake: "TelegramIntakeService") -> None:
        self.client = client
        self.intake = intake

    async def poll_once(self, offset: int | None = None, timeout: int = 30) -> tuple[int | None, list[TelegramUpdateResult]]:
        updates = await self.client.get_updates(offset=offset, timeout=timeout)
        results: list[TelegramUpdateResult] = []
        next_offset = offset
        for update in updates:
            results.append(self.intake.handle_update(update))
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = update_id + 1
        return next_offset, results


class TelegramAdapter:
    def __init__(self, config: TelegramConfig, audit: AuditLogger | None = None) -> None:
        self.config = config
        self.audit = audit

    def normalize_update(self, update: dict[str, Any]) -> TelegramUpdateResult:
        if "callback_query" in update:
            return self._normalize_callback(update["callback_query"], update)
        if "message" in update:
            return self._normalize_message(update["message"], update)
        return TelegramUpdateResult(authorized=False, denial_reason="unsupported_update")

    def _normalize_message(self, message: dict[str, Any], raw_update: dict[str, Any]) -> TelegramUpdateResult:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = sender.get("id")
        chat_id = chat.get("id")

        if not self._is_authorized(user_id, chat_id):
            self._audit_policy("telegram", "unauthorized_message", user_id, chat_id)
            return TelegramUpdateResult(authorized=False, denial_reason="unauthorized")

        text = message.get("text")
        voice = message.get("voice")
        kind = MessageKind.VOICE if voice else MessageKind.TEXT
        attachments = []
        if voice:
            attachments.append(
                VoiceAttachment(
                    file_id=voice.get("file_id"),
                    duration_seconds=voice.get("duration"),
                    mime_type=voice.get("mime_type"),
                    size_bytes=voice.get("file_size"),
                    metadata={"file_unique_id": voice.get("file_unique_id")},
                )
            )

        inbound = InboundMessage(
            id=f"telegram_{message.get('message_id')}",
            channel=ChannelType.TELEGRAM,
            kind=kind,
            sender_id=str(user_id),
            chat_id=str(chat_id),
            text=text,
            attachments=attachments,
            raw=raw_update,
        )

        if text and text.startswith("/"):
            command = self._command_from_text(text, inbound)
            return TelegramUpdateResult(authorized=True, inbound_message=inbound, command=command)

        if self.audit:
            self.audit.append(
                AuditEventType.MESSAGE_RECEIVED,
                actor=f"telegram:{user_id}",
                correlation_id=inbound.correlation_id,
                payload={"message_id": inbound.id, "kind": inbound.kind.value, "chat_id": inbound.chat_id},
            )
        return TelegramUpdateResult(authorized=True, inbound_message=inbound)

    def _normalize_callback(self, callback: dict[str, Any], raw_update: dict[str, Any]) -> TelegramUpdateResult:
        sender = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        user_id = sender.get("id")
        chat_id = chat.get("id")

        if not self._is_authorized(user_id, chat_id):
            self._audit_policy("telegram", "unauthorized_callback", user_id, chat_id)
            return TelegramUpdateResult(authorized=False, denial_reason="unauthorized")

        data = callback.get("data") or ""
        payload = self._parse_callback_data(data)
        command = CommandEnvelope(
            type="telegram.callback",
            source=f"telegram:{user_id}",
            payload={**payload, "callback_query_id": callback.get("id"), "raw": raw_update},
        )
        return TelegramUpdateResult(authorized=True, command=command)

    def _is_authorized(self, user_id: int | None, chat_id: int | None) -> bool:
        if not self.config.enabled:
            return False
        has_user_allowlist = bool(self.config.allowed_user_ids)
        has_chat_allowlist = bool(self.config.allowed_chat_ids)
        if not has_user_allowlist and not has_chat_allowlist:
            return False
        if has_user_allowlist and user_id not in self.config.allowed_user_ids:
            return False
        if has_chat_allowlist and chat_id not in self.config.allowed_chat_ids:
            return False
        return True

    def _command_from_text(self, text: str, inbound: InboundMessage) -> CommandEnvelope:
        parts = text.strip().split()
        command = parts[0].removeprefix("/").split("@", 1)[0].lower()
        return CommandEnvelope(
            type="telegram.command",
            source=f"telegram:{inbound.sender_id}",
            correlation_id=inbound.correlation_id,
            payload={
                "command": command,
                "args": parts[1:],
                "chat_id": inbound.chat_id,
                "message_id": inbound.id,
            },
        )

    @staticmethod
    def _parse_callback_data(data: str) -> dict[str, Any]:
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "approval":
            return {"kind": "approval", "approval_id": parts[1], "decision": parts[2]}
        if len(parts) == 3 and parts[0] == "task":
            return {"kind": "task", "task_id": parts[1], "action": parts[2]}
        return {"kind": "unknown", "data": data}

    def _audit_policy(self, actor: str, reason: str, user_id: int | None, chat_id: int | None) -> None:
        if self.audit:
            self.audit.append(
                AuditEventType.POLICY_DECISION,
                actor=actor,
                payload={
                    "allowed": False,
                    "reason": reason,
                    "user_id": user_id,
                    "chat_id": chat_id,
                },
            )


class TelegramIntakeService:
    def __init__(self, adapter: TelegramAdapter, repositories: Repositories, audit: AuditLogger) -> None:
        self.adapter = adapter
        self.repositories = repositories
        self.audit = audit

    def handle_update(self, update: dict[str, Any]) -> TelegramUpdateResult:
        result = self.adapter.normalize_update(update)
        if not result.authorized:
            return result

        if result.inbound_message:
            conversation_id = self.repositories.conversations.get_or_create(
                result.inbound_message.channel,
                result.inbound_message.chat_id,
            )
            self.repositories.messages.create(result.inbound_message, conversation_id)

            if result.command is None and result.inbound_message.text:
                task = self.repositories.tasks.create(
                    result.inbound_message.text,
                    conversation_id=conversation_id,
                    metadata={"source_message_id": result.inbound_message.id},
                )
                self.audit.append(
                    AuditEventType.TASK_CREATED,
                    actor=f"telegram:{result.inbound_message.sender_id}",
                    task_id=task.id,
                    correlation_id=result.inbound_message.correlation_id,
                    payload={"objective": task.objective, "conversation_id": conversation_id},
                )
                return TelegramUpdateResult(
                    authorized=True,
                    inbound_message=result.inbound_message,
                    task=task,
                )

        if result.command:
            signal = self._apply_command(result.command)
            return TelegramUpdateResult(
                authorized=True,
                inbound_message=result.inbound_message,
                command=result.command,
                signal=signal,
            )

        return result

    def _apply_command(self, command: CommandEnvelope) -> TaskSignal | None:
        payload = command.payload
        if command.type == "telegram.callback":
            return self._apply_callback(payload, command.source)

        name = payload.get("command")
        args = payload.get("args") or []
        if name in {"pause", "resume", "cancel"} and args:
            return self._create_task_signal(args[0], name, command.source, payload)
        return None

    def _apply_callback(self, payload: dict[str, Any], actor: str) -> TaskSignal | None:
        if payload.get("kind") == "approval":
            decision = payload.get("decision")
            if decision == "approve":
                self.repositories.approvals.set_status(payload["approval_id"], ApprovalStatus.APPROVED)
            elif decision == "reject":
                self.repositories.approvals.set_status(payload["approval_id"], ApprovalStatus.REJECTED)
            self.audit.append(
                AuditEventType.APPROVAL_DECIDED,
                actor=actor,
                payload={"approval_id": payload.get("approval_id"), "decision": decision},
            )
            return None

        if payload.get("kind") == "task" and payload.get("action") in {"pause", "resume", "cancel"}:
            return self._create_task_signal(payload["task_id"], payload["action"], actor, payload)
        return None

    def _create_task_signal(
        self,
        task_id: str,
        action: str,
        actor: str,
        payload: dict[str, Any],
    ) -> TaskSignal:
        signal = TaskSignal(task_id=task_id, signal=action, actor=actor, payload=payload)
        self.repositories.task_signals.create(signal)

        target_status = {
            "pause": TaskStatus.PAUSED,
            "resume": TaskStatus.RECEIVED,
            "cancel": TaskStatus.CANCELLED,
        }[action]
        existing = self.repositories.tasks.get(task_id)
        if existing:
            updated = self.repositories.tasks.update_status(task_id, target_status)
            self.audit.task_state_changed(
                actor=actor,
                task_id=task_id,
                old_status=existing.status,
                new_status=updated.status,
            )
        return signal


class TelegramVoiceIntakeService:
    def __init__(
        self,
        adapter: TelegramAdapter,
        bot_api: TelegramBotApi,
        stt: STTAdapter,
        repositories: Repositories,
        audit: AuditLogger,
    ) -> None:
        self.adapter = adapter
        self.bot_api = bot_api
        self.stt = stt
        self.repositories = repositories
        self.audit = audit

    async def handle_update(self, update: dict[str, Any]) -> TelegramUpdateResult:
        result = self.adapter.normalize_update(update)
        if not result.authorized or not result.inbound_message:
            return result
        if result.inbound_message.kind != MessageKind.VOICE:
            return result

        conversation_id = self.repositories.conversations.get_or_create(
            result.inbound_message.channel,
            result.inbound_message.chat_id,
        )
        self.repositories.messages.create(result.inbound_message, conversation_id)

        voice = next(
            (attachment for attachment in result.inbound_message.attachments if isinstance(attachment, VoiceAttachment)),
            None,
        )
        if voice is None or not voice.file_id:
            self.audit.append(
                AuditEventType.ERROR,
                actor=f"telegram:{result.inbound_message.sender_id}",
                correlation_id=result.inbound_message.correlation_id,
                payload={"error": "voice_file_id_missing", "message_id": result.inbound_message.id},
            )
            return result

        file_info = await self.bot_api.get_file(voice.file_id)
        file_path = file_info.get("file_path")
        if not file_path:
            self.audit.append(
                AuditEventType.ERROR,
                actor=f"telegram:{result.inbound_message.sender_id}",
                correlation_id=result.inbound_message.correlation_id,
                payload={"error": "telegram_file_path_missing", "file_id": voice.file_id},
            )
            return result

        audio = await self.bot_api.download_file(file_path)
        transcript = await self.stt.transcribe(
            audio,
            file_name=file_path.rsplit("/", 1)[-1],
            mime_type=voice.mime_type,
        )
        if not transcript.text.strip():
            self.audit.append(
                AuditEventType.ERROR,
                actor=f"telegram:{result.inbound_message.sender_id}",
                correlation_id=result.inbound_message.correlation_id,
                payload={"error": "empty_transcript", "file_id": voice.file_id},
            )
            return result

        artifact = Artifact(
            type=ArtifactType.TRANSCRIPT,
            content_preview=transcript.text,
            metadata=transcript.model_dump(mode="json"),
        )
        task = self.repositories.tasks.create(
            transcript.text,
            conversation_id=conversation_id,
            metadata={
                "source_message_id": result.inbound_message.id,
                "transcript_artifact_id": artifact.id,
                "voice_file_id": voice.file_id,
            },
        )
        artifact = self.repositories.artifacts.create(artifact.model_copy(update={"task_id": task.id}))

        self.audit.append(
            AuditEventType.ARTIFACT_CREATED,
            actor=f"telegram:{result.inbound_message.sender_id}",
            task_id=task.id,
            correlation_id=result.inbound_message.correlation_id,
            payload={"artifact_id": artifact.id, "type": ArtifactType.TRANSCRIPT.value},
        )
        self.audit.append(
            AuditEventType.TASK_CREATED,
            actor=f"telegram:{result.inbound_message.sender_id}",
            task_id=task.id,
            correlation_id=result.inbound_message.correlation_id,
            payload={"objective": task.objective, "conversation_id": conversation_id},
        )

        return TelegramUpdateResult(authorized=True, inbound_message=result.inbound_message, task=task)
