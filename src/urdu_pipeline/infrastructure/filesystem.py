"""Filesystem adapters for the current local run-directory layout."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel

from urdu_pipeline.artifacts.store import ArtifactStore, RunPaths
from urdu_pipeline.config.settings import Settings


def _safe_workspace_relative_path(relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("workspace path must be a non-empty relative path.")
    if relative_path == ".":
        raise ValueError("workspace path must not resolve to a workspace directory.")
    if "\\" in relative_path or "//" in relative_path:
        raise ValueError("workspace path must use safe relative POSIX separators.")

    windows_path = PureWindowsPath(relative_path)
    if windows_path.drive or windows_path.is_absolute():
        raise ValueError("workspace path must be relative.")

    path = Path(relative_path)
    if path.is_absolute():
        raise ValueError("workspace path must be relative.")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("workspace path must not contain traversal segments.")
    return path


def _resolve_workspace_path(root: Path, directory: str, relative_path: str) -> Path:
    safe_relative_path = _safe_workspace_relative_path(relative_path)
    base = root / directory
    candidate = base / safe_relative_path

    resolved_base = base.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError("workspace path must stay within the run directory.") from exc
    return candidate


@dataclass
class FilesystemRunWorkspace:
    """RunWorkspace backed by the existing per-run filesystem directories."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    @classmethod
    def from_store(cls, store: ArtifactStore) -> "FilesystemRunWorkspace":
        return cls(store.paths.root)

    def ensure(self) -> None:
        RunPaths(root=self.root).ensure()
        (self.root / "scratch").mkdir(parents=True, exist_ok=True)

    def input_path(self, relative_path: str) -> Path:
        return _resolve_workspace_path(self.root, "input", relative_path)

    def chunk_path(self, relative_path: str) -> Path:
        return _resolve_workspace_path(self.root, "chunks", relative_path)

    def scratch_path(self, relative_path: str) -> Path:
        return _resolve_workspace_path(self.root, "scratch", relative_path)

    def cleanup(self) -> None:
        # Compatibility mode keeps input/chunk/artifact outputs under the run dir.
        shutil.rmtree(self.root / "scratch", ignore_errors=True)


@dataclass
class FilesystemArtifactSink:
    """ArtifactSink that delegates writes to the existing ArtifactStore."""

    store: ArtifactStore

    @classmethod
    def for_existing_run(cls, run_dir: Path | str) -> "FilesystemArtifactSink":
        return cls(ArtifactStore.for_existing_run(run_dir))

    @classmethod
    def for_new_run(
        cls,
        seed: str | None = None,
        *,
        settings: Settings | None = None,
    ) -> "FilesystemArtifactSink":
        return cls(ArtifactStore.for_new_run(seed, settings=settings))

    def write_artifact(self, model: BaseModel, filename: str) -> Path:
        return self.store.write_artifact(model, filename)

    def write_markdown(self, text: str, filename: str) -> Path:
        return self.store.write_markdown(text, filename)


__all__ = [
    "FilesystemArtifactSink",
    "FilesystemRunWorkspace",
]
