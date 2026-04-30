"""Artifact store + validator tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from urdu_pipeline.artifacts.exporter import export_run_zip
from urdu_pipeline.artifacts.store import (
    ArtifactStore,
    compute_text_checksum,
    new_run_id,
)
from urdu_pipeline.artifacts.validators import (
    ArtifactValidationError,
    load_and_validate_artifact,
    require_artifact_type,
)


def test_new_run_id_is_unique_and_well_formed():
    a = new_run_id("Lecture One")
    b = new_run_id("Lecture One")
    assert a != b
    assert "_" in a
    # No path separators or unsafe chars.
    assert "/" not in a and "\\" not in a


def test_artifact_store_creates_layout(tmp_path, monkeypatch, make_chunk_manifest_payload):
    store = ArtifactStore.for_new_run("sample")
    assert store.paths.input.is_dir()
    assert store.paths.chunks.is_dir()
    assert store.paths.artifacts.is_dir()
    assert store.paths.exports.is_dir()


def test_load_and_validate_chunk_manifest(tmp_path, make_chunk_manifest_payload):
    payload = make_chunk_manifest_payload(num_chunks=3)
    p = tmp_path / "chunk_manifest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    artifact = load_and_validate_artifact(p)
    assert artifact.artifact_type == "chunk_manifest"
    assert len(artifact.chunks) == 3


def test_require_artifact_type_rejects_wrong_stage(tmp_path, make_chunk_manifest_payload):
    payload = make_chunk_manifest_payload()
    p = tmp_path / "chunk_manifest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        require_artifact_type(p, "english_translation")


def test_invalid_artifact_type_rejected(tmp_path):
    p = tmp_path / "weird.json"
    p.write_text(json.dumps({"artifact_type": "not_a_real_thing"}), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        load_and_validate_artifact(p)


def test_missing_artifact_type_rejected(tmp_path):
    p = tmp_path / "weird.json"
    p.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        load_and_validate_artifact(p)


def test_export_zip_includes_artifacts_only_by_default(tmp_path):
    store = ArtifactStore.for_new_run("export-test")
    (store.paths.artifacts / "x.json").write_text("{}", encoding="utf-8")
    (store.paths.chunks / "c.bin").write_bytes(b"audio")
    target = export_run_zip(store.paths)
    import zipfile

    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    assert any(n.endswith("x.json") for n in names)
    assert not any(n.endswith("c.bin") for n in names)


def test_export_zip_can_include_chunks(tmp_path):
    store = ArtifactStore.for_new_run("export-test-chunks")
    (store.paths.artifacts / "x.json").write_text("{}", encoding="utf-8")
    (store.paths.chunks / "c.bin").write_bytes(b"audio")
    target = export_run_zip(store.paths, include_chunks=True)
    import zipfile

    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    assert any(n.endswith("c.bin") for n in names)


def test_text_checksum_is_stable():
    assert compute_text_checksum("hello") == compute_text_checksum("hello")
    assert compute_text_checksum("hello") != compute_text_checksum("world")
