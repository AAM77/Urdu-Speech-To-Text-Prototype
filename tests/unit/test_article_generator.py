"""Article generation stage tests."""

from __future__ import annotations

import json

import pytest

from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.artifacts.validators import (
    ArtifactValidationError,
    require_artifact_type,
)
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.translations import (
    EnglishTranslationArtifact,
    EnglishTranslationSegment,
)
from urdu_pipeline.stages.article_generator import (
    _parse_article_payload,
    run_article_stage,
)


def _translation() -> EnglishTranslationArtifact:
    seg = EnglishTranslationSegment(
        segment_id="es_0001",
        source_segment_id="seg_0001",
        text_english="In the name of Allah, the Most Gracious, the Most Merciful.",
        preserved_uncertainty=False,
    )
    manifest = ArtifactManifest(
        artifact_id="tr_test",
        stage_name="translator",
        artifact_type="english_translation",
    )
    return EnglishTranslationArtifact(
        reconciled_transcript_artifact_id="rec_test",
        segments=[seg],
        full_text_english="A short test translation. Sincerity (*ikhlāṣ*).",
        manifest=manifest,
    )


def test_article_stage_writes_outputs():
    store = ArtifactStore.for_new_run("article-test")
    artifact = run_article_stage(translation=_translation(), store=store)
    assert (store.paths.artifacts / "final_article.json").exists()
    assert (store.paths.artifacts / "final_article.md").exists()
    assert artifact.article.title
    assert artifact.article.body_markdown


def test_article_rejects_wrong_artifact_via_validator(tmp_path):
    payload = {
        "artifact_type": "raw_urdu_transcript",
        "schema_version": "1.0",
        "created_at": "2026-04-27T00:00:00Z",
        "source_audio_hash": "h",
        "chunk_manifest_artifact_id": "cm",
        "chunks": [],
        "manifest": {
            "artifact_id": "raw",
            "schema_version": "1.0",
            "stage_name": "transcriber",
            "artifact_type": "raw_urdu_transcript",
            "created_at": "2026-04-27T00:00:00Z",
            "source_input_hash": "h",
            "upstream_artifact_ids": [],
            "model_provider": None,
            "model_id": None,
            "prompt_id": None,
            "prompt_version": None,
            "chunk_length_seconds": 300,
            "overlap_seconds": 60,
            "context_mode": None,
            "estimated_cost_usd": None,
            "actual_usage": None,
            "cache_hit": False,
            "checksum": "",
            "warnings": [],
            "human_review_status": "unreviewed",
        },
    }
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        require_artifact_type(p, "english_translation")


def test_parse_article_payload_falls_back_when_not_json():
    article = _parse_article_payload("# A Title\n\nSome body text.")
    assert article.title.startswith("A Title")
    assert "Some body" in article.body_markdown
    assert "model_did_not_return_json" in article.warnings


def test_parse_article_payload_handles_fenced_json():
    raw = "```json\n{\"title\": \"T\", \"subtitle\": null, \"body_markdown\": \"hi\"}\n```"
    article = _parse_article_payload(raw)
    assert article.title == "T"
    assert article.body_markdown == "hi"
    assert article.subtitle is None
