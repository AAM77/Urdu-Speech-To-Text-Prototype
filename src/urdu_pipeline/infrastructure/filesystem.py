"""Filesystem adapters for the current local run-directory layout."""

from __future__ import annotations

import shutil
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from pydantic import BaseModel

from urdu_pipeline.application.ports import CacheEntry, CacheScope
from urdu_pipeline.artifacts.store import ArtifactStore, RunPaths
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.config.settings import Settings

_SAFE_CACHE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


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


def _safe_cache_segment(field: str, value: str) -> str:
    if not isinstance(value, str) or not _SAFE_CACHE_SEGMENT_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be a non-empty cache segment containing only "
            "letters, numbers, underscores, and hyphens."
        )
    return value


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


class FilesystemCacheStore:
    """CacheStore adapter backed by the existing ArtifactCache JSON files."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        settings: Settings | None = None,
        cache: ArtifactCache | None = None,
    ) -> None:
        if cache is not None and (root is not None or settings is not None):
            raise ValueError("pass either cache or root/settings, not both.")
        self._cache = cache or ArtifactCache(root=root, settings=settings)

    @classmethod
    def from_cache(cls, cache: ArtifactCache) -> "FilesystemCacheStore":
        return cls(cache=cache)

    def get(self, scope: CacheScope, key: str) -> CacheEntry | None:
        scope_name, safe_key = self._safe_scope_and_key(scope, key)
        lookup = self._cache.lookup(self._artifact_cache_key(scope, scope_name, safe_key))
        if not lookup.hit or lookup.payload is None:
            return None
        return CacheEntry(
            scope=CacheScope(user_id=scope.user_id, name=scope_name),
            key=safe_key,
            payload=dict(lookup.payload),
        )

    def put(
        self,
        scope: CacheScope,
        key: str,
        payload: Mapping[str, Any],
    ) -> CacheEntry:
        scope_name, safe_key = self._safe_scope_and_key(scope, key)
        entry = CacheEntry(
            scope=CacheScope(user_id=scope.user_id, name=scope_name),
            key=safe_key,
            payload=dict(payload),
        )
        self._cache.store(
            self._artifact_cache_key(scope, scope_name, safe_key),
            dict(payload),
        )
        return entry

    def delete(self, scope: CacheScope, key: str) -> bool:
        scope_name, safe_key = self._safe_scope_and_key(scope, key)
        path = self._cache._path_for(self._artifact_cache_key(scope, scope_name, safe_key))
        if not path.exists():
            return False
        path.unlink()
        return True

    def _safe_scope_and_key(self, scope: CacheScope, key: str) -> tuple[str, str]:
        return (
            _safe_cache_segment("scope", scope.name),
            _safe_cache_segment("cache_key", key),
        )

    def _artifact_cache_key(
        self,
        scope: CacheScope,
        scope_name: str,
        key: str,
    ) -> str:
        return f"users/{scope.user_id}/{scope_name}/{key}"


__all__ = [
    "FilesystemArtifactSink",
    "FilesystemCacheStore",
    "FilesystemRunWorkspace",
]
