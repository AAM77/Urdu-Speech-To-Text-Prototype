"""Domain ID type tests."""

from __future__ import annotations

import json
import re

import pytest
from pydantic import BaseModel, ValidationError

from urdu_pipeline.domain import UserId as PackageUserId
from urdu_pipeline.domain.ids import (
    ArtifactId,
    CleanupTaskId,
    DomainId,
    JobId,
    ProviderConfigVersionId,
    ProviderRunId,
    RunId,
    ServiceIdentityId,
    UploadId,
    UserId,
)


ID_TYPES = [
    (UserId, "usr"),
    (UploadId, "upl"),
    (RunId, "run"),
    (JobId, "job"),
    (ArtifactId, "art"),
    (ProviderConfigVersionId, "pcv"),
    (ProviderRunId, "prn"),
    (ServiceIdentityId, "svc"),
    (CleanupTaskId, "cln"),
]


def test_domain_id_builders_create_unique_opaque_uuid_style_values():
    assert PackageUserId is UserId

    for id_type, prefix in ID_TYPES:
        first = id_type.new()
        second = id_type.new()

        assert isinstance(first, id_type)
        assert isinstance(first, str)
        assert first != second
        assert re.fullmatch(rf"{prefix}_[0-9a-f]{{32}}", str(first))


def test_domain_ids_validate_and_round_trip_from_strings():
    for id_type, _prefix in ID_TYPES:
        created = id_type.new()

        assert id_type.parse(str(created)) == created
        assert id_type(str(created)) == created


def test_domain_ids_reject_unsafe_or_wrongly_scoped_values():
    unsafe_values = [
        "",
        "usr_",
        "usr_not-a-uuid",
        "usr_1234567890abcdef1234567890abcde",  # too long
        "usr_1234567890abcdef1234567890abcdeg",  # non-hex
        "usr_1234567890ABCDEF1234567890ABCDEF",  # uppercase
        "usr/1234567890abcdef1234567890abcdef",
        "../usr_1234567890abcdef1234567890abcdef",
        "lecture_one",
        "lecture.mp3",
        "tmp/users/usr_1234567890abcdef1234567890abcdef/uploads/upl_1234567890abcdef1234567890abcdef/source",
    ]

    for raw in unsafe_values:
        with pytest.raises(ValueError):
            UserId(raw)

    with pytest.raises(ValueError):
        UserId(str(UploadId.new()))


def test_domain_ids_serialize_as_strings_for_json_boundaries():
    user_id = UserId.new()
    payload = json.dumps({"user_id": user_id})

    assert json.loads(payload) == {"user_id": str(user_id)}


def test_domain_ids_validate_and_serialize_in_pydantic_models():
    class RequestModel(BaseModel):
        user_id: UserId
        run_id: RunId

    user_id = UserId.new()
    run_id = RunId.new()

    parsed = RequestModel.model_validate(
        {"user_id": str(user_id), "run_id": str(run_id)}
    )

    assert parsed.user_id == user_id
    assert parsed.run_id == run_id
    assert parsed.model_dump(mode="json") == {
        "user_id": str(user_id),
        "run_id": str(run_id),
    }

    with pytest.raises(ValidationError):
        RequestModel.model_validate({"user_id": str(run_id), "run_id": str(run_id)})


def test_base_domain_id_cannot_be_constructed_directly():
    with pytest.raises(TypeError):
        DomainId("usr_1234567890abcdef1234567890abcdef")
