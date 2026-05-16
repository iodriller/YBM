from __future__ import annotations

from agent_control.config import DesktopAdapterConfig, StorageConfig
from agent_control.observation import ArtifactService, ScreenshotService
from agent_control.schemas import ArtifactType
from agent_control.storage import Database, Repositories


class FakeScreenshotAdapter:
    def capture_png(self) -> bytes:
        return b"png-bytes"


def test_screenshot_service_writes_artifact(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    artifact_service = ArtifactService(
        StorageConfig(artifact_dir=str(tmp_path / "artifacts")),
        repos.artifacts,
    )
    service = ScreenshotService(
        DesktopAdapterConfig(screenshot_enabled=True),
        artifact_service,
        adapter=FakeScreenshotAdapter(),
    )

    artifact = service.capture()
    loaded = repos.artifacts.get(artifact.id)

    assert loaded is not None
    assert loaded.type == ArtifactType.SCREENSHOT
    assert loaded.uri is not None
    assert (tmp_path / "artifacts" / artifact.id / "artifact.png").read_bytes() == b"png-bytes"

