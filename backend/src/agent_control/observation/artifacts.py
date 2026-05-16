from __future__ import annotations

from pathlib import Path

from agent_control.config import StorageConfig
from agent_control.schemas import Artifact, ArtifactType
from agent_control.storage.repositories import ArtifactRepository


class ArtifactService:
    def __init__(self, config: StorageConfig, repository: ArtifactRepository) -> None:
        self.config = config
        self.repository = repository

    def write_bytes(
        self,
        artifact_type: ArtifactType,
        data: bytes,
        extension: str,
        task_id: str | None = None,
        metadata: dict | None = None,
        content_preview: str | None = None,
    ) -> Artifact:
        artifact = Artifact(
            task_id=task_id,
            type=artifact_type,
            content_preview=content_preview,
            metadata=metadata or {},
        )
        path = Path(self.config.artifact_dir) / artifact.id
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"artifact.{extension.lstrip('.')}"
        file_path.write_bytes(data)
        return self.repository.create(artifact.model_copy(update={"uri": str(file_path)}))

