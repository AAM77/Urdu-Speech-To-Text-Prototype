"""Provider-neutral object-key builder tests."""

from __future__ import annotations

import inspect

import pytest

from urdu_pipeline.artifacts.store import sanitize_filename
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    RunId,
    UploadId,
    UserId,
)


def test_object_key_builder_uses_provider_neutral_scoped_layout():
    from urdu_pipeline.application.object_keys import ObjectKeyBuilder

    user_id = UserId.new()
    upload_id = UploadId.new()
    run_id = RunId.new()
    artifact_id = ArtifactId.new()
    builder = ObjectKeyBuilder()

    assert (
        builder.upload_source(user_id=user_id, upload_id=upload_id)
        == f"tmp/users/{user_id}/uploads/{upload_id}/source"
    )
    assert (
        builder.run_input_source(user_id=user_id, run_id=run_id)
        == f"tmp/users/{user_id}/runs/{run_id}/input/source"
    )
    assert (
        builder.run_chunk(
            user_id=user_id,
            run_id=run_id,
            chunk_id="chk_123abc",
            audio_ext=".MP3",
        )
        == f"tmp/users/{user_id}/runs/{run_id}/chunks/chk_123abc.mp3"
    )
    assert (
        builder.artifact_json(
            user_id=user_id,
            run_id=run_id,
            stage=ArtifactStage.TRANSCRIBER,
            artifact_id=artifact_id,
        )
        == f"artifacts/users/{user_id}/runs/{run_id}/transcriber/{artifact_id}/artifact.json"
    )
    assert (
        builder.artifact_markdown(
            user_id=user_id,
            run_id=run_id,
            stage=ArtifactStage.TRANSCRIBER,
            artifact_id=artifact_id,
        )
        == f"artifacts/users/{user_id}/runs/{run_id}/transcriber/{artifact_id}/artifact.md"
    )
    assert (
        builder.cache_entry(
            user_id=user_id,
            scope="translator",
            cache_key="abc123deadbeef",
        )
        == f"cache/users/{user_id}/translator/abc123deadbeef.json"
    )


def test_object_key_builder_does_not_accept_filename_parameters():
    from urdu_pipeline.application.object_keys import ObjectKeyBuilder

    builder = ObjectKeyBuilder()
    key_methods = [
        builder.upload_source,
        builder.run_input_source,
        builder.run_chunk,
        builder.artifact_json,
        builder.artifact_markdown,
        builder.cache_entry,
    ]

    for method in key_methods:
        assert "filename" not in inspect.signature(method).parameters
        assert "display_name" not in inspect.signature(method).parameters


@pytest.mark.parametrize(
    "raw_filename",
    [
        "../secret khutbah.mp3",
        "..\\secret khutbah.mp3",
        "/tmp/lecture final.MP3",
        "lecture final.mp3",
        "lecture_final.mp3",
        "مولانا bayan.mp3",
        "audio/../../escape.wav",
        "my.upload.name.m4a",
    ],
)
def test_object_keys_never_include_raw_or_sanitized_filenames(raw_filename: str):
    from urdu_pipeline.application.object_keys import ObjectKeyBuilder

    builder = ObjectKeyBuilder()
    user_id = UserId.new()
    upload_id = UploadId.new()
    run_id = RunId.new()
    artifact_id = ArtifactId.new()
    sanitized = sanitize_filename(raw_filename)

    keys = [
        builder.upload_source(user_id=user_id, upload_id=upload_id),
        builder.run_input_source(user_id=user_id, run_id=run_id),
        builder.run_chunk(
            user_id=user_id,
            run_id=run_id,
            chunk_id="chk_123abc",
            audio_ext="mp3",
        ),
        builder.artifact_json(
            user_id=user_id,
            run_id=run_id,
            stage=ArtifactStage.ARTICLE_GENERATOR,
            artifact_id=artifact_id,
        ),
        builder.artifact_markdown(
            user_id=user_id,
            run_id=run_id,
            stage=ArtifactStage.ARTICLE_GENERATOR,
            artifact_id=artifact_id,
        ),
        builder.cache_entry(
            user_id=user_id,
            scope="article_generator",
            cache_key="deadbeef1234",
        ),
    ]

    for key in keys:
        assert raw_filename not in key
        assert sanitized not in key


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("chunk_id", {"chunk_id": "../chunk_0001", "audio_ext": "mp3"}),
        ("chunk_id", {"chunk_id": "chunk.0001", "audio_ext": "mp3"}),
        ("chunk_id", {"chunk_id": "مولانا", "audio_ext": "mp3"}),
        ("audio_ext", {"chunk_id": "chunk_0001", "audio_ext": "../secret.mp3"}),
        ("audio_ext", {"chunk_id": "chunk_0001", "audio_ext": "lecture.mp3"}),
        ("audio_ext", {"chunk_id": "chunk_0001", "audio_ext": ".tar.gz"}),
        ("audio_ext", {"chunk_id": "chunk_0001", "audio_ext": "mp3/evil"}),
        ("scope", {"scope": "../translator", "cache_key": "abc123"}),
        ("scope", {"scope": "translator.v1", "cache_key": "abc123"}),
        ("cache_key", {"scope": "translator", "cache_key": "../abc123"}),
        ("cache_key", {"scope": "translator", "cache_key": "abc123.json"}),
    ],
)
def test_object_key_builder_rejects_traversal_and_weird_segments(field: str, kwargs: dict):
    from urdu_pipeline.application.object_keys import ObjectKeyBuilder

    builder = ObjectKeyBuilder()
    user_id = UserId.new()
    run_id = RunId.new()

    with pytest.raises(ValueError, match=field):
        if field in {"chunk_id", "audio_ext"}:
            builder.run_chunk(user_id=user_id, run_id=run_id, **kwargs)
        else:
            builder.cache_entry(user_id=user_id, **kwargs)


def test_object_key_builder_does_not_provide_shared_cache_keys_in_v1():
    from urdu_pipeline.application.object_keys import ObjectKeyBuilder

    assert not hasattr(ObjectKeyBuilder(), "shared_cache_entry")
