from __future__ import annotations

from io import BytesIO
from typing import Protocol

from agent_control.config import DesktopAdapterConfig
from agent_control.observation.artifacts import ArtifactService
from agent_control.schemas import Artifact, ArtifactType


class ScreenshotAdapter(Protocol):
    def capture_png(self) -> bytes:
        ...


class DisabledScreenshotAdapter:
    def capture_png(self) -> bytes:
        raise RuntimeError("desktop.screenshot is disabled")


class PillowScreenshotAdapter:
    def capture_png(self) -> bytes:
        from PIL import ImageGrab

        image = ImageGrab.grab()
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class ScreenshotService:
    def __init__(
        self,
        config: DesktopAdapterConfig,
        artifact_service: ArtifactService,
        adapter: ScreenshotAdapter | None = None,
    ) -> None:
        self.config = config
        self.artifact_service = artifact_service
        self.adapter = adapter or PillowScreenshotAdapter()

    def capture(self, task_id: str | None = None) -> Artifact:
        if not self.config.screenshot_enabled:
            raise RuntimeError("desktop.screenshot is disabled")
        data = self.adapter.capture_png()
        return self.artifact_service.write_bytes(
            ArtifactType.SCREENSHOT,
            data,
            "png",
            task_id=task_id,
            metadata={"format": self.config.screenshot_format},
            content_preview="desktop screenshot",
        )
