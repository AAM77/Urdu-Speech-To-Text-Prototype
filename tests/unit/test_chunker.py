"""Chunker tests (pure planner + filename safety)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.artifacts.store import sanitize_filename
from urdu_pipeline.infrastructure.filesystem import FilesystemRunWorkspace
from urdu_pipeline.stages.chunker import ChunkerStage, plan_chunks, run_chunker_stage


class RecordingArtifactSink:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifacts: list[tuple[BaseModel, str, Path]] = []
        self.markdown: list[tuple[str, str, Path]] = []

    def write_artifact(self, model: BaseModel, filename: str) -> Path:
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(), encoding="utf-8")
        self.artifacts.append((model, filename, path))
        return path

    def write_markdown(self, text: str, filename: str) -> Path:
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.markdown.append((text, filename, path))
        return path


def _fake_media(monkeypatch: pytest.MonkeyPatch, duration_seconds: float = 120.0) -> None:
    def fake_slice_chunk(*, target: Path, **_: object) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"chunk")

    monkeypatch.setattr(
        "urdu_pipeline.stages.chunker.probe_audio_duration_seconds",
        lambda _path: duration_seconds,
    )
    monkeypatch.setattr("urdu_pipeline.stages.chunker._slice_chunk_with_ffmpeg", fake_slice_chunk)


def test_60_minute_audio_produces_15_chunks():
    chunks = plan_chunks(duration_seconds=3600, chunk_length_seconds=300, overlap_seconds=60)
    # step = 240s, total = 3600s. (3600 - 60) / 240 = 14.75 -> 15 chunks.
    assert len(chunks) == 15
    # Each new chunk starts every 240s.
    assert chunks[0].start_ms == 0
    assert chunks[1].start_ms == 240_000
    assert chunks[2].start_ms == 480_000
    assert chunks[3].start_ms == 720_000


def test_full_chunks_are_chunk_length_long_except_possibly_last():
    chunks = plan_chunks(duration_seconds=3600, chunk_length_seconds=300, overlap_seconds=60)
    for c in chunks[:-1]:
        assert c.duration_ms == 300_000
    # last chunk may be shorter
    assert chunks[-1].end_ms == 3_600_000
    assert chunks[-1].duration_ms <= 300_000


def test_short_audio_produces_one_short_chunk():
    chunks = plan_chunks(duration_seconds=120, chunk_length_seconds=300, overlap_seconds=60)
    assert len(chunks) == 1
    assert chunks[0].start_ms == 0
    assert chunks[0].end_ms == 120_000


def test_zero_duration_returns_empty():
    assert plan_chunks(0, 300, 60) == []


def test_invalid_overlap_raises():
    with pytest.raises(ValueError):
        plan_chunks(60, 300, 300)
    with pytest.raises(ValueError):
        plan_chunks(60, 300, -1)


def test_sanitize_filename_strips_path_separators_and_unsafe_chars():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    # Each unsafe character maps to an underscore individually (e.g. "; " -> "__").
    assert sanitize_filename("evil; rm -rf /") == "evil__rm_-rf"
    assert sanitize_filename(".hidden") == "hidden"
    assert sanitize_filename("normal_audio.mp3") == "normal_audio.mp3"
    # Empty / all-bad input falls back to "file".
    assert sanitize_filename("///") == "file"


def test_chunker_writes_chunks_to_run_workspace_and_artifacts_to_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _fake_media(monkeypatch, duration_seconds=60.0)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    workspace = FilesystemRunWorkspace(tmp_path / "workspace")
    sink = RecordingArtifactSink(tmp_path / "sink")

    artifact = ChunkerStage(
        workspace=workspace,
        artifact_sink=sink,
        chunk_length_seconds=60,
        overlap_seconds=0,
    ).run(source)

    assert (workspace.root / "input" / "source.mp3").read_bytes() == b"audio"
    assert (workspace.root / "chunks" / "chunk_0001.mp3").read_bytes() == b"chunk"
    assert artifact.source_audio_path == "input/source.mp3"
    assert artifact.chunks[0].file_path == "chunks/chunk_0001.mp3"
    assert [entry[1] for entry in sink.artifacts] == ["chunk_manifest.json"]
    assert sink.artifacts[0][0] is artifact
    assert [entry[1] for entry in sink.markdown] == ["chunk_summary.md"]


def test_run_chunker_stage_preserves_artifact_store_layout_for_cli_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _fake_media(monkeypatch, duration_seconds=60.0)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    store = ArtifactStore.for_existing_run(tmp_path / "run")

    artifact = run_chunker_stage(
        audio_path=source,
        store=store,
        chunk_length_seconds=60,
        overlap_seconds=0,
    )

    assert (store.paths.input / "source.mp3").exists()
    assert (store.paths.chunks / "chunk_0001.mp3").exists()
    assert (store.paths.artifacts / "chunk_manifest.json").exists()
    assert (store.paths.artifacts / "chunk_summary.md").exists()
    assert artifact.source_audio_path == "input/source.mp3"
