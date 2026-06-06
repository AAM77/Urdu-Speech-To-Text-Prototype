"""Opaque domain identifiers.

These IDs are server-generated and safe to expose as resource identifiers. They
are not object keys, filenames, or authorization decisions by themselves.
"""

from __future__ import annotations

import re
import uuid
from typing import ClassVar, Self

from pydantic_core import core_schema

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


class DomainId(str):
    """Base class for strict, prefixed, UUID-style domain IDs."""

    prefix: ClassVar[str | None] = None

    def __new__(cls, value: str) -> Self:
        if cls is DomainId:
            raise TypeError("DomainId is abstract; use a concrete ID type.")
        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__} value must be a string.")
        cls._validate(value)
        return str.__new__(cls, value)

    @classmethod
    def new(cls) -> Self:
        """Generate a new opaque ID for this domain type."""
        if cls.prefix is None:
            raise TypeError(f"{cls.__name__} must define a prefix.")
        return cls(f"{cls.prefix}_{uuid.uuid4().hex}")

    @classmethod
    def parse(cls, value: str) -> Self:
        """Validate and coerce a string into this domain ID type."""
        return cls(value)

    @classmethod
    def _validate(cls, value: str) -> None:
        if cls.prefix is None:
            raise TypeError(f"{cls.__name__} must define a prefix.")
        prefix, separator, payload = value.partition("_")
        if separator != "_" or prefix != cls.prefix or not _UUID_HEX_RE.fullmatch(payload):
            raise ValueError(
                f"{cls.__name__} must match '{cls.prefix}_' followed by "
                "32 lowercase hexadecimal UUID characters."
            )

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )


class UserId(DomainId):
    prefix = "usr"


class UploadId(DomainId):
    prefix = "upl"


class RunId(DomainId):
    prefix = "run"


class JobId(DomainId):
    prefix = "job"


class ArtifactId(DomainId):
    prefix = "art"


class ProviderConfigVersionId(DomainId):
    prefix = "pcv"


class ProviderRunId(DomainId):
    prefix = "prn"


class ServiceIdentityId(DomainId):
    prefix = "svc"


class CleanupTaskId(DomainId):
    prefix = "cln"


__all__ = [
    "ArtifactId",
    "CleanupTaskId",
    "DomainId",
    "JobId",
    "ProviderConfigVersionId",
    "ProviderRunId",
    "RunId",
    "ServiceIdentityId",
    "UploadId",
    "UserId",
]
