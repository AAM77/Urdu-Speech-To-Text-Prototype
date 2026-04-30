"""Filesystem layout, artifact persistence, hashing, and run management."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from urdu_pipeline.config.settings import Settings, get_settings


# -----------------------------------------------------------------------------
# Filename + path safety
# -----------------------------------------------------------------------------
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(name: str) -> str:
    """Return a filename safe for cross-platform use.

    Strips path separators, collapses unsafe characters into `_`, and removes
    any leading dots so the result cannot become a hidden file or escape its
    directory.
    """
    base = Path(name).name  # drop any directory components
    cleaned = _UNSAFE_CHARS.sub("_", base).strip("._")
    return cleaned or "file"


# -----------------------------------------------------------------------------
# Hashing helpers
# -----------------------------------------------------------------------------
def compute_file_hash(path: Path | str, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    p = Path(path)
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_text_checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_json_checksum(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return compute_text_checksum(payload)


# -----------------------------------------------------------------------------
# Run identifiers + paths
# -----------------------------------------------------------------------------
def new_run_id(seed: str | None = None) -> str:
    """Return a `<YYYY-MM-DD>_<slug>_<short-uuid>` identifier."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    slug_source = seed or "run"
    slug = sanitize_filename(slug_source)[:40] or "run"
    short = uuid.uuid4().hex[:8]
    return f"{today}_{slug}_{short}"


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def input(self) -> Path:
        return self.root / "input"

    @property
    def chunks(self) -> Path:
        return self.root / "chunks"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    def ensure(self) -> "RunPaths":
        for d in (self.input, self.chunks, self.artifacts, self.exports):
            d.mkdir(parents=True, exist_ok=True)
        return self


class ArtifactStore:
    """Per-run filesystem layout helper."""

    def __init__(self, run_paths: RunPaths) -> None:
        self.paths = run_paths
        self.paths.ensure()

    @classmethod
    def for_new_run(
        cls,
        seed: str | None = None,
        *,
        settings: Settings | None = None,
    ) -> "ArtifactStore":
        s = settings or get_settings()
        run_id = new_run_id(seed)
        root = s.output_root_path / run_id
        return cls(RunPaths(root=root))

    @classmethod
    def for_existing_run(cls, run_dir: Path | str) -> "ArtifactStore":
        return cls(RunPaths(root=Path(run_dir)))

    @property
    def run_id(self) -> str:
        return self.paths.root.name

    # ------------------------------------------------------------------
    # Read / write artifacts
    # ------------------------------------------------------------------
    def write_artifact(self, model: BaseModel, filename: str) -> Path:
        path = self.paths.artifacts / sanitize_filename(filename)
        payload = model.model_dump(mode="json")
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def write_markdown(self, text: str, filename: str) -> Path:
        path = self.paths.artifacts / sanitize_filename(filename)
        path.write_text(text, encoding="utf-8")
        return path

    def read_json(self, path: Path | str) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def copy_input_audio(self, source: Path) -> Path:
        target = self.paths.input / sanitize_filename(source.name)
        target.write_bytes(Path(source).read_bytes())
        return target
