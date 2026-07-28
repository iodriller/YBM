from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from agent_control.config import AppSettings, TelegramConfig
from agent_control.config_sync import read_env_value
from agent_control.channels.responder import TelegramResponder, gateway_context
from agent_control.channels.memory import ConversationMemoryService, memory_context
from agent_control.llm.classifier import MessageClassifier, classification_trace
from agent_control.orchestration.signals import apply_task_signal
from agent_control.schemas import (
    ApprovalStatus,
    AuditEventType,
    Artifact,
    ArtifactType,
    Capability,
    ChannelType,
    CommandEnvelope,
    InboundMessage,
    IntentRoute,
    MessageClassification,
    MessageKind,
    OutboundMessage,
    TaskRecord,
    TaskSignal,
    TaskStatus,
    TaskType,
    VoiceAttachment,
)
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories
from agent_control.observation.screenshot import ScreenshotService
from agent_control.tools.coding_agent import (
    PROVIDERS as CODING_PROVIDERS,
    format_session_status,
    latest_session,
    load_sessions,
    read_log_tail,
    stop_session_process,
)
from agent_control.tools.stt import STTAdapter


def _preview(value: str | None, limit: int = 240) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


@dataclass(frozen=True)
class TelegramUpdateResult:
    authorized: bool
    inbound_message: InboundMessage | None = None
    command: CommandEnvelope | None = None
    classification: MessageClassification | None = None
    signal: TaskSignal | None = None
    task: TaskRecord | None = None
    outbound_message: OutboundMessage | None = None
    denial_reason: str | None = None


def load_telegram_token(config: TelegramConfig) -> str:
    if config.token:
        return config.token.get_secret_value()
    token = read_env_value(config.token_env)
    if not token:
        raise RuntimeError(f"Telegram token not found in {config.token_env}")
    return token


class TelegramBotApi:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.telegram.org",
        audit: AuditLogger | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.audit = audit

    async def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = await self._post("getUpdates", payload)
        return list(data.get("result", []))

    async def send_message(self, chat_id: str | int, text: str) -> dict[str, Any]:
        data = await self._post("sendMessage", {"chat_id": chat_id, "text": text})
        self._log_sent(chat_id, kind="text", text=text, response=data)
        return data

    async def send_photo_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict[str, Any]:
        payload = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption
        url = f"{self.base_url}/bot{self.token}/sendPhoto"
        with open(path, "rb") as photo:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, data=payload, files={"photo": photo})
                response.raise_for_status()
                data = response.json()
        if not data.get("ok"):
            raise RuntimeError("Telegram Bot API call failed: sendPhoto")
        self._log_sent(chat_id, kind="photo", caption=caption, path=path, response=data)
        return data

    async def send_document_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict[str, Any]:
        payload = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption
        url = f"{self.base_url}/bot{self.token}/sendDocument"
        with open(path, "rb") as document:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, data=payload, files={"document": document})
                response.raise_for_status()
                data = response.json()
        if not data.get("ok"):
            raise RuntimeError("Telegram Bot API call failed: sendDocument")
        self._log_sent(chat_id, kind="document", caption=caption, path=path, response=data)
        return data

    def _log_sent(
        self,
        chat_id: str | int,
        *,
        kind: str,
        response: dict[str, Any],
        text: str | None = None,
        caption: str | None = None,
        path: str | None = None,
    ) -> None:
        # Durable local record of what YBM actually sent — the E2E runner's
        # truth source, independent of re-reading Telegram's live chat state.
        if self.audit is None:
            return
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "kind": kind,
            "telegram_message_id": result.get("message_id"),
        }
        if text is not None:
            payload["text"] = text
        if caption is not None:
            payload["caption"] = caption
        if path is not None:
            payload["path"] = path
        try:
            self.audit.append(AuditEventType.MESSAGE_SENT, actor="telegram_bot_api", payload=payload)
        except Exception:
            pass

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
            update_id = update.get("update_id")
            try:
                result = await self.intake.handle_update_async(update)
                results.append(result)
                if result.outbound_message and result.outbound_message.text:
                    await self.client.send_message(result.outbound_message.chat_id, result.outbound_message.text)
                if result.outbound_message and result.outbound_message.artifact_ids:
                    await self._send_artifacts(result.outbound_message)
            except Exception as exc:
                if self.intake.audit:
                    self.intake.audit.append(
                        AuditEventType.ERROR,
                        actor="telegram_polling",
                        payload={
                            "error": "update_processing_failed",
                            "update_id": update_id,
                            "reason": str(exc),
                        },
                    )
            finally:
                if isinstance(update_id, int):
                    next_offset = update_id + 1
        return next_offset, results

    async def _send_artifacts(self, outbound_message: "OutboundMessage") -> None:
        for artifact_id in outbound_message.artifact_ids:
            artifact = self.intake.repositories.artifacts.get(artifact_id)
            if artifact and artifact.type == ArtifactType.SCREENSHOT and artifact.uri:
                await self.client.send_photo_file(outbound_message.chat_id, artifact.uri, artifact.content_preview)
            elif artifact and artifact.uri:
                await self.client.send_document_file(outbound_message.chat_id, artifact.uri, artifact.content_preview)


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

        text = self._message_text(message)
        allowed, reason = self._authorization_decision(user_id, chat_id)
        if not allowed:
            self._audit_telegram_access(False, reason, user_id, chat_id, text, message)
            return TelegramUpdateResult(authorized=False, denial_reason="unauthorized")

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
                actor=f"telegram:user:{user_id}",
                correlation_id=inbound.correlation_id,
                payload={
                    "message_id": inbound.id,
                    "kind": inbound.kind.value,
                    "sender_id": inbound.sender_id,
                    "chat_id": inbound.chat_id,
                    "text": text,
                    "text_preview": _preview(text),
                    "has_forward_origin": "forward_origin" in message or "forward_from" in message,
                    "forward_origin": message.get("forward_origin") or message.get("forward_from"),
                },
            )
        return TelegramUpdateResult(authorized=True, inbound_message=inbound)

    def _normalize_callback(self, callback: dict[str, Any], raw_update: dict[str, Any]) -> TelegramUpdateResult:
        sender = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        user_id = sender.get("id")
        chat_id = chat.get("id")

        allowed, reason = self._authorization_decision(user_id, chat_id)
        if not allowed:
            self._audit_telegram_access(False, reason, user_id, chat_id, None, callback)
            return TelegramUpdateResult(authorized=False, denial_reason="unauthorized")

        data = callback.get("data") or ""
        payload = self._parse_callback_data(data)
        command = CommandEnvelope(
            type="telegram.callback",
            source=f"telegram:{user_id}",
            payload={**payload, "callback_query_id": callback.get("id"), "raw": raw_update},
        )
        return TelegramUpdateResult(authorized=True, command=command)

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str | None:
        value = message.get("text") or message.get("caption")
        return str(value) if value is not None else None

    def _authorization_decision(self, user_id: int | None, chat_id: int | None) -> tuple[bool, str]:
        if not self.config.enabled:
            return False, "telegram_disabled"
        has_user_allowlist = bool(self.config.allowed_user_ids)
        has_chat_allowlist = bool(self.config.allowed_chat_ids)
        if not has_user_allowlist and not has_chat_allowlist:
            return False, "allowlist_empty"
        if has_user_allowlist and user_id not in self.config.allowed_user_ids:
            return False, "user_not_allowed"
        if has_chat_allowlist and chat_id not in self.config.allowed_chat_ids:
            return False, "chat_not_allowed"
        return True, "allowed"

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

    def _audit_telegram_access(
        self,
        allowed: bool,
        reason: str,
        user_id: int | None,
        chat_id: int | None,
        text: str | None,
        raw: dict[str, Any],
    ) -> None:
        if self.audit:
            self.audit.append(
                AuditEventType.TELEGRAM_ACCESS_DECISION,
                actor="telegram",
                payload={
                    "allowed": allowed,
                    "reason": reason,
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "telegram_enabled": self.config.enabled,
                    "allowed_user_ids": self.config.allowed_user_ids,
                    "allowed_chat_ids": self.config.allowed_chat_ids,
                    "text_preview": _preview(text),
                    "message_id": raw.get("message_id") or raw.get("id"),
                },
            )


class TelegramIntakeService:
    def __init__(
        self,
        adapter: TelegramAdapter,
        repositories: Repositories,
        audit: AuditLogger,
        settings: AppSettings | None = None,
        bot_api: TelegramBotApi | None = None,
        stt: STTAdapter | None = None,
        screenshot_service: ScreenshotService | None = None,
        classifier: MessageClassifier | None = None,
        responder: TelegramResponder | None = None,
        memory_service: ConversationMemoryService | None = None,
    ) -> None:
        self.adapter = adapter
        self.repositories = repositories
        self.audit = audit
        self.settings = settings
        self.bot_api = bot_api
        self.stt = stt
        self.screenshot_service = screenshot_service
        self.classifier = classifier
        self.responder = responder
        self.memory_service = memory_service or ConversationMemoryService(repositories)

    def handle_update(self, update: dict[str, Any]) -> TelegramUpdateResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.handle_update_async(update))
        raise RuntimeError("handle_update cannot run inside an active event loop; use handle_update_async")

    async def handle_update_async(self, update: dict[str, Any]) -> TelegramUpdateResult:
        result = self.adapter.normalize_update(update)
        if not result.authorized:
            return result

        if result.inbound_message:
            conversation_id = self.repositories.conversations.get_or_create(
                result.inbound_message.channel,
                result.inbound_message.chat_id,
            )
            inbound = result.inbound_message
            if inbound.kind == MessageKind.VOICE and not inbound.text:
                try:
                    inbound = await self._transcribe_voice(inbound)
                except Exception as exc:
                    self.audit.append(
                        AuditEventType.ERROR,
                        actor=f"telegram:user:{inbound.sender_id}",
                        correlation_id=inbound.correlation_id,
                        payload={
                            "error": "voice_transcription_failed",
                            "message_id": inbound.id,
                            "reason": str(exc),
                        },
                    )
                    return TelegramUpdateResult(
                        authorized=True,
                        inbound_message=inbound,
                        outbound_message=self._out(inbound.chat_id, f"Voice transcription failed: {exc}"),
                    )
            if not self.repositories.messages.try_create(inbound, conversation_id):
                return TelegramUpdateResult(authorized=True, inbound_message=inbound)
            if inbound.text:
                await self._update_conversation_memory(conversation_id, inbound.text)

            if result.command is None:
                plain_response = self._plain_text_command_response(inbound)
                if plain_response is not None:
                    return TelegramUpdateResult(
                        authorized=True,
                        inbound_message=inbound,
                        outbound_message=plain_response,
                    )
                clarify_response = self._resume_clarifying_task(inbound, conversation_id)
                if clarify_response is not None:
                    return TelegramUpdateResult(
                        authorized=True,
                        inbound_message=inbound,
                        outbound_message=clarify_response,
                    )
                return await self._classify_and_spawn(inbound, conversation_id)

        if result.command:
            signal = self._apply_command(result.command)
            outbound = self._command_response(result.command, signal)
            return TelegramUpdateResult(
                authorized=True,
                inbound_message=result.inbound_message,
                command=result.command,
                signal=signal,
                outbound_message=outbound,
            )

        return result

    async def _classify_and_spawn(self, inbound: InboundMessage, conversation_id: str) -> TelegramUpdateResult:
        actor = f"telegram:user:{inbound.sender_id}"
        if not inbound.text:
            return self._spawn_failed(inbound, "message has no text content", actor)
        if self.classifier is None:
            return self._spawn_failed(inbound, "message classifier is not configured", actor)

        await self._send_progress(inbound.chat_id, "Got your message, figuring out what to do…")
        try:
            # The Concierge prompt does double duty (classify + compose a chat
            # reply in one call, see prompts/base/concierge_system.md) - a chat
            # reply needs the same runtime context the old separate responder
            # call used (capabilities, recent tasks), not just conversation
            # memory. Falls back to the narrower memory-only context when no
            # settings are available (e.g. a caller that never wired them).
            classification_context = (
                gateway_context(self.settings, self.repositories, conversation_id)
                if self.settings is not None
                else memory_context(
                    self.repositories.conversation_memory.get(conversation_id),
                    recent_turns=3,
                    max_chars=900,
                )
            )
            try:
                classification = await self.classifier.classify(inbound, context=classification_context)
            except TypeError:
                classification = await self.classifier.classify(inbound)
        except Exception as exc:
            return self._spawn_failed(inbound, f"classification failed: {exc}", actor)

        self.audit.append(
            AuditEventType.MESSAGE_CLASSIFIED,
            actor=actor,
            correlation_id=inbound.correlation_id,
            payload={
                "message_id": inbound.id,
                "chat_id": inbound.chat_id,
                "sender_id": inbound.sender_id,
                "text": inbound.text,
                "is_task": classification.is_task,
                "task_type": classification.task_type.value,
                "normalized_objective": classification.normalized_objective,
                "confidence": classification.confidence,
                "reason": classification.reason,
                "intent": classification.intent.model_dump(mode="json") if classification.intent else None,
                "llm": classification_trace(inbound, context=classification_context),
            },
        )

        # Decide chat-only vs spawn-task. Prefer the intent.route enum because
        # the LLM picks it more consistently than the is_task bool. The bool
        # was flipping wrong for observation/check requests in production
        # (e.g. "tell me what is on my desktop" → is_task=False AND
        # intent.route=desktop.observe; the bool was wrong, the route was right).
        #
        # Rules:
        #  - intent.route is CONVERSATION  → chat-only (model explicitly said chat)
        #  - intent.route is any other     → spawn task (override is_task=False
        #                                    so observation tasks aren't dropped)
        #  - intent missing entirely       → fall back to is_task (legacy behavior)
        # STATUS_REQUEST task_type still bypasses the gate as before.
        intent_route = (
            classification.intent.route
            if classification.intent is not None
            else None
        )
        if intent_route is None:
            is_chat_only = (
                not classification.is_task
                and classification.task_type != TaskType.STATUS_REQUEST
            )
        else:
            is_chat_only = (
                intent_route == IntentRoute.CONVERSATION
                and classification.task_type != TaskType.STATUS_REQUEST
            )
        if is_chat_only:
            outbound = await self._non_task_response(inbound, classification, conversation_id)
            if outbound is not None:
                return TelegramUpdateResult(
                    authorized=True,
                    inbound_message=inbound,
                    classification=classification,
                    outbound_message=outbound,
                )
            return self._spawn_failed(inbound, classification.reason, actor, classification)

        objective = (classification.normalized_objective or inbound.text).strip()
        voice_attachment = next((attachment for attachment in inbound.attachments if isinstance(attachment, VoiceAttachment)), None)
        voice_metadata = (
            {
                "voice_file_id": voice_attachment.file_id,
                "voice_transcript": voice_attachment.transcript,
            }
            if voice_attachment is not None
            else {}
        )
        # Snapshot conversation memory so the planner has prior context available
        _mem_ctx = memory_context(
            self.repositories.conversation_memory.get(conversation_id),
            recent_turns=5,
            max_chars=1600,
        )
        task = self.repositories.tasks.create(
            objective,
            conversation_id=conversation_id,
            metadata={
                "source_message_id": inbound.id,
                "source_channel": inbound.channel.value,
                "source_chat_id": inbound.chat_id,
                "source_sender_id": inbound.sender_id,
                "task_type": classification.task_type.value,
                "classification_confidence": classification.confidence,
                "classification_reason": classification.reason,
                "orchestration_intent": classification.intent.model_dump(mode="json") if classification.intent else None,
                "original_message_text": inbound.text,
                "memory_context": _mem_ctx,
                **voice_metadata,
            },
        )
        self.audit.append(
            AuditEventType.TASK_CREATED,
            actor=actor,
            task_id=task.id,
            correlation_id=inbound.correlation_id,
            payload={
                "objective": task.objective,
                "conversation_id": conversation_id,
                "task_type": classification.task_type.value,
                "source_message_id": inbound.id,
                "classification_confidence": classification.confidence,
                "classification_reason": classification.reason,
                "orchestration_intent": classification.intent.model_dump(mode="json") if classification.intent else None,
            },
        )
        await self._send_progress(inbound.chat_id, "On it — I'll send the result here when it's done.")
        return TelegramUpdateResult(
            authorized=True,
            inbound_message=inbound,
            classification=classification,
            task=task,
            outbound_message=None,
        )

    def _resume_clarifying_task(self, inbound: InboundMessage, conversation_id: str) -> OutboundMessage | None:
        """Route a reply to the task waiting on a question instead of spawning a new task."""
        text = (inbound.text or "").strip()
        if not text:
            return None
        task = next(
            (
                candidate
                for candidate in self.repositories.tasks.list_by_statuses([TaskStatus.CLARIFYING], limit=20)
                if candidate.conversation_id == conversation_id
                or str(candidate.metadata.get("source_chat_id")) == str(inbound.chat_id)
            ),
            None,
        )
        if task is None:
            return None
        actor = f"telegram:user:{inbound.sender_id}"
        if text.lower() in {"cancel", "stop", "drop it", "never mind", "nevermind", "forget it", "no"}:
            self.repositories.tasks.update_status(task.id, TaskStatus.CANCELLED)
            self.audit.append(
                AuditEventType.TASK_STATE_CHANGED,
                actor=actor,
                task_id=task.id,
                correlation_id=inbound.correlation_id,
                payload={"reason": "clarification_cancelled", "status": TaskStatus.CANCELLED.value},
            )
            return self._out(inbound.chat_id, f"Cancelled: {task.objective[:200]}")

        # Fold the answer into the objective so replanning sees it, and reset
        # the attempt counters — the user's input makes this a fresh attempt.
        objective = f"{task.objective}\n[User clarification: {text}]"
        self.repositories.tasks.update_objective(task.id, objective)
        answers = list(task.metadata.get("clarification_answers") or [])
        answers.append(
            {
                "question": task.metadata.get("clarifying_question"),
                "answer": text,
                "message_id": inbound.id,
                "created_at": inbound.received_at.isoformat(),
            }
        )
        metadata = {
            **task.metadata,
            "clarification_answer": text,
            "clarification_answers": answers,
            "answered_clarifying_question": task.metadata.get("clarifying_question"),
            "retry_count": 0,
            "replan_count": 0,
            "evaluator_repair_count": 0,
            "fulfillment_retry_count": 0,
        }
        metadata.pop("clarifying_question", None)
        self.repositories.tasks.update_metadata(task.id, metadata, TaskStatus.RECEIVED)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor=actor,
            task_id=task.id,
            correlation_id=inbound.correlation_id,
            payload={"reason": "clarification_answered", "answer": text[:400], "status": TaskStatus.RECEIVED.value},
        )
        return self._out(inbound.chat_id, "Got it — resuming the task with your answer.")

    async def _non_task_response(
        self,
        inbound: InboundMessage,
        classification: MessageClassification,
        conversation_id: str,
    ) -> OutboundMessage | None:
        if classification.task_type == TaskType.STATUS_REQUEST:
            return self._out(inbound.chat_id, self._status_summary())
        # The Concierge composes the chat reply in the same call it
        # classifies (see prompts/base/concierge_system.md) - no second LLM
        # round trip needed. Falls back to the separate `responder` (if
        # configured) for a classifier that doesn't populate `.reply`.
        reply = (classification.reply or "").strip()
        if reply:
            return self._out(inbound.chat_id, reply[:3900])
        if self.responder is None:
            return None
        try:
            answer = await self.responder.answer(inbound, conversation_id)
        except Exception as exc:
            return self._spawn_failed(inbound, f"response generation failed: {exc}", f"telegram:user:{inbound.sender_id}", classification).outbound_message
        return self._out(inbound.chat_id, answer[:3900])

    async def _update_conversation_memory(self, conversation_id: str, text: str) -> None:
        await self.memory_service.update_from_user_message(conversation_id, text)

    async def _transcribe_voice(self, inbound: InboundMessage) -> InboundMessage:
        if self.bot_api is None or self.stt is None:
            raise RuntimeError("voice transcription is not configured")
        voice = next((attachment for attachment in inbound.attachments if isinstance(attachment, VoiceAttachment)), None)
        if voice is None or not voice.file_id:
            raise RuntimeError("voice file id is missing")

        file_info = await self.bot_api.get_file(voice.file_id)
        file_path = file_info.get("file_path")
        if not file_path:
            raise RuntimeError("Telegram did not return a voice file path")

        audio = await self.bot_api.download_file(file_path)
        transcript = await self.stt.transcribe(
            audio,
            file_name=file_path.rsplit("/", 1)[-1],
            mime_type=voice.mime_type,
        )
        text = transcript.text.strip()
        if not text:
            raise RuntimeError("voice transcript was empty")

        updated_attachments = [
            attachment.model_copy(update={"transcript": text}) if isinstance(attachment, VoiceAttachment) else attachment
            for attachment in inbound.attachments
        ]
        self.audit.append(
            AuditEventType.MESSAGE_RECEIVED,
            actor=f"telegram:user:{inbound.sender_id}",
            correlation_id=inbound.correlation_id,
            payload={
                "message_id": inbound.id,
                "kind": MessageKind.VOICE.value,
                "sender_id": inbound.sender_id,
                "chat_id": inbound.chat_id,
                "text": text,
                "text_preview": _preview(text),
                "voice_file_id": voice.file_id,
                "voice_transcribed": True,
                "transcription": transcript.model_dump(mode="json"),
            },
        )
        return inbound.model_copy(update={"text": text, "attachments": updated_attachments})

    def _spawn_failed(
        self,
        inbound: InboundMessage,
        reason: str,
        actor: str,
        classification: MessageClassification | None = None,
    ) -> TelegramUpdateResult:
        self.audit.append(
            AuditEventType.TASK_SPAWN_FAILED,
            actor=actor,
            correlation_id=inbound.correlation_id,
            payload={
                "message_id": inbound.id,
                "chat_id": inbound.chat_id,
                "sender_id": inbound.sender_id,
                "text": inbound.text,
                "reason": reason,
                "classification": classification.model_dump(mode="json") if classification else None,
            },
        )
        return TelegramUpdateResult(
            authorized=True,
            inbound_message=inbound,
            classification=classification,
            outbound_message=self._out(inbound.chat_id, f"I could not start this request: {reason}"),
        )

    async def _send_progress(self, chat_id: str, text: str) -> None:
        if self.bot_api is None:
            return
        try:
            await self.bot_api.send_message(chat_id, text)
        except Exception as exc:
            self.audit.append(
                AuditEventType.ERROR,
                actor="telegram_progress",
                payload={"error": "progress_send_failed", "reason": str(exc), "chat_id": chat_id},
            )

    def _apply_command(self, command: CommandEnvelope) -> TaskSignal | None:
        payload = command.payload
        if command.type == "telegram.callback":
            return self._apply_callback(payload, command.source)

        name = payload.get("command")
        args = payload.get("args") or []
        if name in {"pause", "resume", "cancel"} and args:
            return self._create_task_signal(args[0], name, command.source, payload)
        return None

    def _command_response(self, command: CommandEnvelope, signal: TaskSignal | None) -> OutboundMessage | None:
        if command.type != "telegram.command":
            return None
        payload = command.payload
        name = payload.get("command")
        chat_id = str(payload.get("chat_id"))
        args = payload.get("args") or []

        if name == "status":
            return self._out(chat_id, self._status_summary())
        if name == "agents":
            return self._out(chat_id, self._agents_summary())
        if name == "tasks":
            tasks = self.repositories.tasks.list_recent(10)
            if not tasks:
                return self._out(chat_id, "No tasks found.")
            lines = [f"{task.id} | {task.status.value} | {task.objective[:80]}" for task in tasks]
            return self._out(chat_id, "\n".join(lines))
        if name == "task" and args:
            task = self.repositories.tasks.get(args[0])
            if not task:
                return self._out(chat_id, f"Task not found: {args[0]}")
            return self._out(chat_id, f"{task.id}\nstatus: {task.status.value}\nobjective: {task.objective}")
        if name == "logs" and args:
            events = self.repositories.audit.list_for_task(args[0])[-10:]
            if not events:
                return self._out(chat_id, f"No logs found for {args[0]}.")
            lines = [f"{event.created_at.isoformat()} | {event.type.value} | {event.actor}" for event in events]
            return self._out(chat_id, "\n".join(lines))
        if name == "screenshot":
            policy = self.settings.capabilities.get(Capability.DESKTOP_SCREENSHOT) if self.settings else None
            enabled = bool(policy and policy.enabled)
            if not enabled:
                return self._out(chat_id, "desktop.screenshot is disabled.")
            if not self.screenshot_service:
                return self._out(chat_id, "desktop.screenshot is enabled, but screenshot capture is not configured.")
            try:
                artifact = self.screenshot_service.capture()
            except Exception as exc:
                return self._out(chat_id, f"Screenshot capture failed: {exc}")
            return OutboundMessage(
                channel=ChannelType.TELEGRAM,
                chat_id=chat_id,
                text="Screenshot captured.",
                artifact_ids=[artifact.id],
            )
        if name in {"pause", "resume", "cancel"}:
            if signal:
                return self._out(chat_id, f"{name} signal recorded for {signal.task_id}.")
            return self._out(chat_id, f"Usage: /{name} <task_id>")
        return None

    def _plain_text_command_response(self, inbound: InboundMessage) -> OutboundMessage | None:
        text = (inbound.text or "").strip().lower()
        if text in {"approve", "approved", "approve it", "yes approve", "yes, approve"}:
            return self._approve_latest_pending(inbound)
        if text in {"status", "task status", "tasks status", "what is the status"}:
            return self._out(inbound.chat_id, self._status_summary())
        if text in {"tasks", "list tasks", "show tasks"}:
            tasks = self.repositories.tasks.list_recent(10)
            if not tasks:
                return self._out(inbound.chat_id, "No tasks found.")
            lines = [f"{task.id} | {task.status.value} | {task.objective[:80]}" for task in tasks]
            return self._out(inbound.chat_id, "\n".join(lines))
        agent_reply = self._coding_agent_fast_reply(text)
        if agent_reply is not None:
            return self._out(inbound.chat_id, agent_reply)
        return None

    # Deterministic coding-agent queries answered straight from session files,
    # so "what is codex doing" works instantly even when every LLM is down.
    _CODING_ALIASES = {"codex": "codex", "claude": "claude_code", "copilot": "github_copilot"}
    _CODING_STATUS_MARKERS = (
        "status", "doing", "done", "finished", "finish", "progress", "update",
        "up to", "how is", "how's", "going", "say", "output", "working",
    )
    _CODING_STOP_MARKERS = ("stop", "kill", "abort", "terminate")

    def _coding_agent_fast_reply(self, text: str) -> str | None:
        if not text or len(text) > 120 or self.settings is None:
            return None
        provider = next(
            (canonical for alias, canonical in self._CODING_ALIASES.items() if alias in text),
            None,
        )
        if provider is None:
            return None
        session_root = self.settings.adapters.coding_agent.session_root
        if any(marker in text for marker in self._CODING_STOP_MARKERS):
            session = latest_session(session_root, provider=provider)
            if session is None or session.get("status") != "running":
                return f"No running {provider} session to stop."
            stop_session_process(session)
            return f"Stop requested for {provider} session {session.get('session_id')}. A final report follows once it exits."
        if any(marker in text for marker in self._CODING_STATUS_MARKERS) or text.strip() == provider:
            session = latest_session(session_root, provider=provider)
            if session is None:
                return f"No {provider} sessions have run yet."
            log_tail = read_log_tail(str(session.get("log_path") or ""))
            return format_session_status(session, log_tail=log_tail)[:3900]
        return None

    def _agents_summary(self) -> str:
        if self.settings is None:
            return "Coding agent sessions are not configured."
        sessions = load_sessions(self.settings.adapters.coding_agent.session_root, limit=8)
        if not sessions:
            return "No coding agent sessions yet. Providers: " + ", ".join(CODING_PROVIDERS)
        lines = ["Recent coding sessions:"]
        for session in sessions:
            lines.append(
                f"- {session.get('provider')} | {session.get('status')} | {session.get('session_id')} | "
                f"{str(session.get('prompt') or '')[:60]}"
            )
        return "\n".join(lines)

    def _approve_latest_pending(self, inbound: InboundMessage) -> OutboundMessage:
        chat_id = str(inbound.chat_id)
        for task in self.repositories.tasks.list_recent(50):
            if task.conversation_id != f"conv_telegram_{chat_id}" and str(task.metadata.get("source_chat_id")) != chat_id:
                continue
            pending = [
                approval
                for approval in self.repositories.approvals.list_for_task(task.id)
                if approval.status == ApprovalStatus.PENDING
            ]
            if not pending:
                continue
            for approval in pending:
                self.repositories.approvals.set_status(approval.id, ApprovalStatus.APPROVED)
                self.audit.append(
                    AuditEventType.APPROVAL_DECIDED,
                    actor=f"telegram:user:{inbound.sender_id}",
                    task_id=task.id,
                    correlation_id=inbound.correlation_id,
                    payload={"approval_id": approval.id, "decision": "approve", "source": "plain_text"},
                )
            return self._out(chat_id, f"Approved {len(pending)} pending approval(s) for {task.id}.")
        return self._out(chat_id, "No pending approval found. Full-access modes run without approval.")

    def _status_summary(self) -> str:
        recent = self.repositories.tasks.list_recent(20)
        active_statuses = {TaskStatus.RECEIVED, TaskStatus.INTERPRETING, TaskStatus.PLANNED, TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.RETRYING}
        active = [task for task in recent if task.status in active_statuses]
        lines = [f"{len(recent)} recent task(s), {len(active)} active."]
        for task in recent[:5]:
            lines.append(f"- {task.status.value}: {task.objective[:120]}")
        return "\n".join(lines)

    @staticmethod
    def _out(chat_id: str, text: str) -> OutboundMessage:
        return OutboundMessage(channel=ChannelType.TELEGRAM, chat_id=chat_id, text=text)

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
        signal, _, _ = apply_task_signal(self.repositories, self.audit, task_id, action, actor, payload)
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
        if not self.repositories.messages.try_create(result.inbound_message, conversation_id):
            return result

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
                "source_channel": result.inbound_message.channel.value,
                "source_chat_id": result.inbound_message.chat_id,
                "source_sender_id": result.inbound_message.sender_id,
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
            actor=f"telegram:user:{result.inbound_message.sender_id}",
            task_id=task.id,
            correlation_id=result.inbound_message.correlation_id,
            payload={
                "objective": task.objective,
                "conversation_id": conversation_id,
                "task_type": "voice",
                "source_message_id": result.inbound_message.id,
            },
        )

        return TelegramUpdateResult(authorized=True, inbound_message=result.inbound_message, task=task)
