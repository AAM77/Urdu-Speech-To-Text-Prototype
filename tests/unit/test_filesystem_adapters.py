"""Filesystem adapter contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from urdu_pipeline.application.ports import ArtifactSink, RunWorkspace
from urdu_pipeline.artifacts.store import ArtifactStore


class TinyArtifact(BaseModel):
    value: str


def test_filesystem_run_workspace_preserves_existing_run_directory_layout(tmp_path: Path):
    from urdu_pipeline.infrastructure.filesystem import FilesystemRunWorkspace

    run_dir = tmp_path / "runs" / "run_1"
    workspace = FilesystemRunWorkspace(run_dir)

    assert isinstance(workspace, RunWorkspace)
    assert workspace.root == run_dir

    workspace.ensure()

    assert (run_dir / "input").is_dir()
    assert (run_dir / "chunks").is_dir()
    assert (run_dir / "artifacts").is_dir()
    assert (run_dir / "exports").is_dir()
    assert (run_dir / "scratch").is_dir()

    assert workspace.input_path("source.mp3") == run_dir / "input" / "source.mp3"
    assert workspace.chunk_path("chunk_0001.mp3") == run_dir / "chunks" / "chunk_0001.mp3"
    assert workspace.scratch_path("provider/request.json") == (
        run_dir / "scratch" / "provider" / "request.json"
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        ".",
        "..",
        "../secret.txt",
        "safe/../../secret.txt",
        "/tmp/secret.txt",
        "safe\\secret.txt",
        "safe//secret.txt",
    ],
)
def test_filesystem_run_workspace_rejects_unsafe_relative_paths(
    tmp_path: Path,
    relative_path: str,
):
    from urdu_pipeline.infrastructure.filesystem import FilesystemRunWorkspace

    workspace = FilesystemRunWorkspace(tmp_path / "run_1")

    for resolver in (
        workspace.input_path,
        workspace.chunk_path,
        workspace.scratch_path,
    ):
        with pytest.raises(ValueError, match="workspace path"):
            resolver(relative_path)


def test_filesystem_artifact_sink_delegates_to_existing_artifact_store(tmp_path: Path):
    from urdu_pipeline.infrastructure.filesystem import FilesystemArtifactSink

    store = ArtifactStore.for_existing_run(tmp_path / "run_1")
    sink = FilesystemArtifactSink(store)

    assert isinstance(sink, ArtifactSink)

    json_path = sink.write_artifact(TinyArtifact(value="ok"), "../../artifact.json")
    markdown_path = sink.write_markdown("# ok", "summary.md")

    assert json_path == store.paths.artifacts / "artifact.json"
    assert markdown_path == store.paths.artifacts / "summary.md"
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"value": "ok"}
    assert markdown_path.read_text(encoding="utf-8") == "# ok"
