"""Tests for the environment-backed local secret provider."""

from __future__ import annotations

import pytest

from urdu_pipeline.application.ports import SecretProvider


def test_env_secret_provider_satisfies_secret_provider_protocol():
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    provider = EnvSecretProvider()

    assert isinstance(provider, SecretProvider)


def test_env_secret_provider_returns_secret_from_environment(monkeypatch):
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    monkeypatch.setenv("URDU_TEST_API_KEY", "super-secret-value-123")
    provider = EnvSecretProvider()

    result = provider.get_secret("URDU_TEST_API_KEY")

    assert result.name == "URDU_TEST_API_KEY"
    assert result.value == "super-secret-value-123"


def test_env_secret_provider_fails_closed_for_missing_secret(monkeypatch):
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    monkeypatch.delenv("URDU_TEST_MISSING_SECRET", raising=False)
    provider = EnvSecretProvider()

    with pytest.raises(KeyError, match="URDU_TEST_MISSING_SECRET"):
        provider.get_secret("URDU_TEST_MISSING_SECRET")


def test_env_secret_provider_fails_closed_when_variable_is_empty(monkeypatch):
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    monkeypatch.setenv("URDU_TEST_EMPTY_SECRET", "")
    provider = EnvSecretProvider()

    with pytest.raises(KeyError, match="URDU_TEST_EMPTY_SECRET"):
        provider.get_secret("URDU_TEST_EMPTY_SECRET")


def test_secret_value_repr_does_not_expose_secret_value(monkeypatch):
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    monkeypatch.setenv("URDU_TEST_REDACTED", "my-actual-secret-abc-xyz")
    provider = EnvSecretProvider()
    secret = provider.get_secret("URDU_TEST_REDACTED")

    representation = repr(secret)

    assert "my-actual-secret-abc-xyz" not in representation
    assert "URDU_TEST_REDACTED" in representation


def test_secret_value_str_does_not_expose_secret_value(monkeypatch):
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    monkeypatch.setenv("URDU_TEST_REDACTED", "my-actual-secret-abc-xyz")
    provider = EnvSecretProvider()
    secret = provider.get_secret("URDU_TEST_REDACTED")

    as_str = str(secret)

    assert "my-actual-secret-abc-xyz" not in as_str
    assert "URDU_TEST_REDACTED" in as_str


def test_in_memory_secret_value_repr_also_redacts():
    from urdu_pipeline.infrastructure.in_memory import InMemorySecretProvider

    provider = InMemorySecretProvider({"URDU_TEST_INLINE": "exposed-inline-value"})
    secret = provider.get_secret("URDU_TEST_INLINE")

    representation = repr(secret)

    assert "exposed-inline-value" not in representation
    assert "URDU_TEST_INLINE" in representation
