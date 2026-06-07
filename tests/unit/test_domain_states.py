"""Domain state enum tests."""

from __future__ import annotations

import json
from enum import Enum
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from urdu_pipeline.domain.states import (
    ArtifactStage,
    ArtifactType,
    CleanupTaskStatus,
    JobAttemptStatus,
    JobStatus,
    ProviderConfigStatus,
    RunStatus,
    ServiceIdentityStatus,
    UploadStatus,
    UserStatus,
)
from urdu_pipeline.schemas.base import ARTIFACT_TYPES, StageName


EXPECTED_VALUES = {
    UploadStatus: [
        "initialized",
        "uploading",
        "completed",
        "failed",
        "cancelled",
        "expired",
    ],
    RunStatus: [
        "pending",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ],
    JobStatus: [
        "queued",
        "claimed",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "dead_lettered",
    ],
    JobAttemptStatus: [
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    ],
    ArtifactStage: [
        "chunker",
        "transcriber",
        "transcript_reconciler",
        "translator",
        "article_generator",
        "english_chunk_transcriber",
    ],
    ArtifactType: [
        "chunk_manifest",
        "raw_urdu_transcript",
        "reconciled_urdu_transcript",
        "english_translation",
        "final_article",
        "raw_am_english_transcript",
    ],
    ProviderConfigStatus: [
        "draft",
        "active",
        "disabled",
        "retired",
    ],
    CleanupTaskStatus: [
        "pending",
        "running",
        "succeeded",
        "failed",
        "retrying",
        "cancelled",
    ],
    UserStatus: [
        "active",
        "disabled",
        "locked",
        "deleted",
    ],
    ServiceIdentityStatus: [
        "active",
        "disabled",
        "revoked",
    ],
}


def test_domain_state_enum_values_are_stable():
    for enum_type, expected in EXPECTED_VALUES.items():
        assert [item.value for item in enum_type] == expected


def test_artifact_stage_and_type_values_match_current_artifact_schemas():
    assert [item.value for item in ArtifactStage] == list(get_args(StageName))
    assert [item.value for item in ArtifactType] == list(ARTIFACT_TYPES.values())


def test_domain_state_enums_are_strings_and_json_serializable():
    payload = json.dumps(
        {
            "upload_status": UploadStatus.COMPLETED,
            "job_status": JobStatus.DEAD_LETTERED,
            "artifact_type": ArtifactType.FINAL_ARTICLE,
        }
    )

    assert json.loads(payload) == {
        "upload_status": "completed",
        "job_status": "dead_lettered",
        "artifact_type": "final_article",
    }


def test_domain_state_enums_reject_unknown_values():
    for enum_type in EXPECTED_VALUES:
        with pytest.raises(ValueError):
            enum_type("not_a_valid_state")


def test_domain_state_enums_validate_in_pydantic_models():
    class JobSnapshot(BaseModel):
        run_status: RunStatus
        job_status: JobStatus

    parsed = JobSnapshot.model_validate(
        {"run_status": "running", "job_status": "claimed"}
    )

    assert parsed.run_status is RunStatus.RUNNING
    assert parsed.job_status is JobStatus.CLAIMED
    assert parsed.model_dump(mode="json") == {
        "run_status": "running",
        "job_status": "claimed",
    }

    with pytest.raises(ValidationError):
        JobSnapshot.model_validate({"run_status": "running", "job_status": "done"})


def test_all_domain_state_exports_are_enum_types():
    for enum_type in EXPECTED_VALUES:
        assert issubclass(enum_type, str)
        assert issubclass(enum_type, Enum)
