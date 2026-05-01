"""Settings + model-roles tests."""

from __future__ import annotations

import pytest

from urdu_pipeline.config.model_roles import get_model_roles
from urdu_pipeline.config.settings import get_settings, reset_settings_cache


def test_fake_mode_works_without_api_key():
    s = get_settings()
    assert s.pipeline_provider_mode == "fake"
    assert s.openai_api_key in (None, "")
    # Real-mode helper should not raise here because we're in fake mode.
    # (Calling it WOULD raise because pipeline_provider_mode != "real".)
    with pytest.raises(RuntimeError):
        s.require_real_provider_ready()


def test_real_mode_requires_api_key(monkeypatch):
    monkeypatch.setenv("PIPELINE_PROVIDER_MODE", "real")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reset_settings_cache()
    s = get_settings()
    assert s.pipeline_provider_mode == "real"
    with pytest.raises(RuntimeError):
        s.require_real_provider_ready()


def test_real_mode_with_api_key_passes(monkeypatch):
    monkeypatch.setenv("PIPELINE_PROVIDER_MODE", "real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    reset_settings_cache()
    s = get_settings()
    s.require_real_provider_ready()  # must not raise


def test_model_roles_resolve_from_settings():
    roles = get_model_roles()
    # Conftest sets these to fake-* by default.
    assert roles.for_role("transcription") == "fake-transcribe"
    assert roles.for_role("translation") == "fake-text"
    assert roles.for_role("article") == "fake-text"
    assert roles.for_role("reconciliation") == "fake-text"


def test_hard_cap_must_be_at_least_default_budget(monkeypatch):
    monkeypatch.setenv("DEFAULT_BUDGET_USD", "100")
    monkeypatch.setenv("HARD_CAP_USD", "50")
    reset_settings_cache()
    with pytest.raises(Exception):
        get_settings()


def test_accepted_audio_extensions_are_configurable(monkeypatch):
    monkeypatch.setenv("ACCEPTED_AUDIO_EXTENSIONS", " .mp3 , wav ,M4A ")
    reset_settings_cache()
    s = get_settings()
    assert s.accepted_audio_extensions_set == {"mp3", "wav", "m4a"}
    assert s.is_audio_extension_allowed("foo.MP3")
    assert s.is_audio_extension_allowed("bar.wav")
    assert not s.is_audio_extension_allowed("baz.flac")


def test_default_accepted_includes_mp3():
    s = get_settings()
    assert "mp3" in s.accepted_audio_extensions_set
