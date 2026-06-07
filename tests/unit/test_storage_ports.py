"""Storage and workspace port tests."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

from pydantic import BaseModel

from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType, RunId, UserId


class SmallArtifact(BaseModel):
    value: str


def test_storage_port_module_imports_without_optional_adapter_dependencies():
    blocked_roots = [
        "boto3",
        "botocore",
        "fastapi",
        "minio",
        "openai",
        "redis",
        "sqlalchemy",
        "streamlit",
        "typer",
        "uvicorn",
    ]
    script = f"""
import importlib
import importlib.abc
import sys

blocked_roots = {blocked_roots!r}


class OptionalDependencyBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in blocked_roots:
            raise AssertionError(f"optional dependency imported during port import: {{fullname}}")
        return None


sys.meta_path.insert(0, OptionalDependencyBlocker())
importlib.import_module("urdu_pipeline.application.ports.storage")
importlib.import_module("urdu_pipeline.application.ports")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr


def test_object_store_protocol_covers_streaming_signed_metadata_prefix_and_multipart():
    from urdu_pipeline.application.ports.storage import (
        MultipartPart,
        MultipartUpload,
        ObjectInfo,
        ObjectMetadata,
        ObjectStore,
        SignedUrl,
    )

    class FakeObjectStore:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}

        def put_stream(
            self,
            key: str,
            body: BinaryIO,
            *,
            metadata: ObjectMetadata | None = None,
        ) -> ObjectInfo:
            payload = body.read()
            self.objects[key] = payload
            return ObjectInfo(
                key=key,
                size_bytes=len(payload),
                content_type=metadata.content_type if metadata else None,
                checksum_sha256=metadata.checksum_sha256 if metadata else None,
                user_metadata=metadata.user_metadata if metadata else {},
            )

        def get_stream(self, key: str) -> BinaryIO:
            return io.BytesIO(self.objects[key])

        def head_object(self, key: str) -> ObjectInfo:
            return ObjectInfo(key=key, size_bytes=len(self.objects[key]))

        def create_signed_upload_url(
            self,
            key: str,
            *,
            expires_in: timedelta,
            metadata: ObjectMetadata | None = None,
        ) -> SignedUrl:
            return SignedUrl(
                url=f"https://objects.local/{key}",
                method="PUT",
                expires_at=datetime.now(tz=timezone.utc) + expires_in,
            )

        def create_signed_download_url(
            self,
            key: str,
            *,
            expires_in: timedelta,
            filename: str | None = None,
        ) -> SignedUrl:
            return SignedUrl(
                url=f"https://objects.local/{key}",
                method="GET",
                expires_at=datetime.now(tz=timezone.utc) + expires_in,
            )

        def delete_object(self, key: str) -> None:
            self.objects.pop(key, None)

        def list_prefix(self, prefix: str) -> Sequence[ObjectInfo]:
            return [
                ObjectInfo(key=key, size_bytes=len(value))
                for key, value in self.objects.items()
                if key.startswith(prefix)
            ]

        def delete_prefix(self, prefix: str) -> int:
            keys = [key for key in self.objects if key.startswith(prefix)]
            for key in keys:
                del self.objects[key]
            return len(keys)

        def create_multipart_upload(
            self,
            key: str,
            *,
            metadata: ObjectMetadata | None = None,
        ) -> MultipartUpload:
            return MultipartUpload(key=key, upload_id="upload-1")

        def create_signed_part_upload_url(
            self,
            upload: MultipartUpload,
            *,
            part_number: int,
            expires_in: timedelta,
        ) -> SignedUrl:
            return SignedUrl(
                url=f"https://objects.local/{upload.key}?partNumber={part_number}",
                method="PUT",
                expires_at=datetime.now(tz=timezone.utc) + expires_in,
            )

        def complete_multipart_upload(
            self,
            upload: MultipartUpload,
            parts: Sequence[MultipartPart],
        ) -> ObjectInfo:
            self.objects[upload.key] = b"".join(
                f"{part.part_number}:{part.etag}".encode("utf-8") for part in parts
            )
            return self.head_object(upload.key)

        def abort_multipart_upload(self, upload: MultipartUpload) -> None:
            self.objects.pop(upload.key, None)

    store = FakeObjectStore()
    metadata = ObjectMetadata(
        content_type="audio/mpeg",
        checksum_sha256="a" * 64,
        user_metadata={"purpose": "test"},
    )

    assert isinstance(store, ObjectStore)
    info = store.put_stream("tmp/users/usr/uploads/upl/source", io.BytesIO(b"audio"), metadata=metadata)
    assert info.size_bytes == 5
    assert store.get_stream(info.key).read() == b"audio"
    assert store.create_signed_upload_url(info.key, expires_in=timedelta(minutes=5)).method == "PUT"
    assert store.create_signed_download_url(info.key, expires_in=timedelta(minutes=5)).method == "GET"
    upload = store.create_multipart_upload("tmp/users/usr/uploads/upl/multipart", metadata=metadata)
    part_url = store.create_signed_part_upload_url(
        upload,
        part_number=1,
        expires_in=timedelta(minutes=5),
    )
    assert "partNumber=1" in part_url.url
    completed = store.complete_multipart_upload(
        upload,
        [MultipartPart(part_number=1, etag="etag-1", size_bytes=5)],
    )
    assert completed.key == upload.key
    assert store.delete_prefix("tmp/users/") == 2


def test_run_workspace_artifact_sink_and_repository_protocols_are_structural(tmp_path: Path):
    from urdu_pipeline.application.ports.storage import (
        ArtifactFormat,
        ArtifactReference,
        ArtifactRepository,
        ArtifactSink,
        RunWorkspace,
    )

    class FakeRunWorkspace:
        root = tmp_path

        def ensure(self) -> None:
            self.root.mkdir(parents=True, exist_ok=True)

        def input_path(self, relative_path: str) -> Path:
            return self.root / "input" / relative_path

        def chunk_path(self, relative_path: str) -> Path:
            return self.root / "chunks" / relative_path

        def scratch_path(self, relative_path: str) -> Path:
            return self.root / "scratch" / relative_path

        def cleanup(self) -> None:
            return None

    class FakeArtifactSink:
        def write_artifact(self, model: BaseModel, filename: str) -> Path:
            target = tmp_path / "artifacts" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(model.model_dump_json(), encoding="utf-8")
            return target

        def write_markdown(self, text: str, filename: str) -> Path:
            target = tmp_path / "artifacts" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            return target

    class FakeArtifactRepository:
        def __init__(self) -> None:
            self.payloads: dict[ArtifactId, Mapping[str, Any]] = {}

        def save_artifact(
            self,
            *,
            user_id: UserId,
            run_id: RunId,
            stage: ArtifactStage,
            artifact_type: ArtifactType,
            artifact_id: ArtifactId,
            payload: Mapping[str, Any],
            markdown: str | None = None,
        ) -> ArtifactReference:
            self.payloads[artifact_id] = payload
            return ArtifactReference(
                user_id=user_id,
                run_id=run_id,
                stage=stage,
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                has_markdown=markdown is not None,
            )

        def get_artifact_metadata(
            self,
            *,
            user_id: UserId,
            artifact_id: ArtifactId,
        ) -> ArtifactReference:
            return ArtifactReference(
                user_id=user_id,
                run_id=RunId.new(),
                stage=ArtifactStage.CHUNKER,
                artifact_type=ArtifactType.CHUNK_MANIFEST,
                artifact_id=artifact_id,
                has_markdown=False,
            )

        def load_artifact(
            self,
            *,
            user_id: UserId,
            artifact_id: ArtifactId,
            artifact_format: ArtifactFormat,
        ) -> Mapping[str, Any] | str:
            return self.payloads[artifact_id]

        def list_run_artifacts(
            self,
            *,
            user_id: UserId,
            run_id: RunId,
        ) -> Sequence[ArtifactReference]:
            return []

    workspace = FakeRunWorkspace()
    sink = FakeArtifactSink()
    repository = FakeArtifactRepository()

    assert isinstance(workspace, RunWorkspace)
    assert isinstance(sink, ArtifactSink)
    assert isinstance(repository, ArtifactRepository)
    assert workspace.input_path("source.mp3") == tmp_path / "input" / "source.mp3"
    artifact_path = sink.write_artifact(SmallArtifact(value="ok"), "sample.json")
    assert artifact_path.exists()

    reference = repository.save_artifact(
        user_id=UserId.new(),
        run_id=RunId.new(),
        stage=ArtifactStage.CHUNKER,
        artifact_type=ArtifactType.CHUNK_MANIFEST,
        artifact_id=ArtifactId.new(),
        payload={"value": "ok"},
        markdown="# ok",
    )
    assert asdict(reference)["has_markdown"] is True
