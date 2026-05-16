from __future__ import annotations

from typing import Protocol

from agent_control.schemas import TranscriptionResult


class STTAdapter(Protocol):
    async def transcribe(
        self,
        audio: bytes,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> TranscriptionResult:
        ...


class DisabledSTTAdapter:
    async def transcribe(
        self,
        audio: bytes,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> TranscriptionResult:
        raise RuntimeError("STT adapter is disabled")


class StaticSTTAdapter:
    def __init__(self, text: str) -> None:
        self.text = text

    async def transcribe(
        self,
        audio: bytes,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text=self.text,
            metadata={"file_name": file_name, "mime_type": mime_type, "bytes": len(audio)},
        )
