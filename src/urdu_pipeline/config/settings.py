"""Application settings loaded from environment / `.env`.

Uses pydantic-settings so values can be overridden via env vars or `.env`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderMode = Literal["fake", "real"]


def _project_root() -> Path:
    # src/urdu_pipeline/config/settings.py -> repo root
    return Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration. Values fall back to environment then `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(_project_root() / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- OpenAI ----
    openai_api_key: str | None = Field(default=None)
    openai_org_id: str | None = Field(default=None)
    openai_project_id: str | None = Field(default=None)

    # ---- Provider mode ----
    pipeline_provider_mode: ProviderMode = Field(default="fake")

    # ---- Budget ----
    default_budget_usd: float = Field(default=30.0, ge=0.0)
    hard_cap_usd: float = Field(default=60.0, ge=0.0)
    cost_safety_margin: float = Field(default=0.20, ge=0.0, le=1.0)

    # ---- Chunking ----
    default_chunk_length_seconds: int = Field(default=300, gt=0)
    default_overlap_seconds: int = Field(default=60, ge=0)
    max_chunk_mb: float = Field(default=24.0, gt=0.0)

    # ---- Accepted audio extensions (configurable) ----
    accepted_audio_extensions: str = Field(
        default="mp3,wav,m4a,flac,ogg,webm,mp4",
        description="Comma-separated list of accepted audio file extensions.",
    )

    # ---- Model roles ----
    transcription_model: str = Field(default="gpt-4o-transcribe")
    translation_model: str = Field(default="gpt-5.5")
    article_model: str = Field(default="gpt-5.5")
    reconciliation_model: str = Field(default="gpt-5.5")

    # ---- Output / runtime ----
    output_root: str = Field(default="runs")
    cache_root: str = Field(default=".cache_pipeline")
    log_level: str = Field(default="INFO")
    prompt_version: str = Field(default="v1")

    # -------------------------------------------------------------------------
    # Validators / derived helpers
    # -------------------------------------------------------------------------
    @field_validator("hard_cap_usd")
    @classmethod
    def _hard_cap_must_be_at_least_default_budget(cls, v: float, info) -> float:
        default = info.data.get("default_budget_usd", 0.0)
        if v < default:
            raise ValueError(
                f"HARD_CAP_USD ({v}) must be >= DEFAULT_BUDGET_USD ({default})."
            )
        return v

    @property
    def project_root(self) -> Path:
        return _project_root()

    @property
    def output_root_path(self) -> Path:
        p = Path(self.output_root)
        if not p.is_absolute():
            p = self.project_root / p
        return p

    @property
    def cache_root_path(self) -> Path:
        p = Path(self.cache_root)
        if not p.is_absolute():
            p = self.project_root / p
        return p

    @property
    def accepted_audio_extensions_set(self) -> set[str]:
        """Normalized lowercase extensions (without leading dot)."""
        out: set[str] = set()
        for raw in self.accepted_audio_extensions.split(","):
            ext = raw.strip().lower().lstrip(".")
            if ext:
                out.add(ext)
        return out

    def is_audio_extension_allowed(self, filename: str | Path) -> bool:
        ext = Path(filename).suffix.lower().lstrip(".")
        return ext in self.accepted_audio_extensions_set

    def require_real_provider_ready(self) -> None:
        """Raise a clear error if real-provider mode is not configured."""
        if self.pipeline_provider_mode != "real":
            raise RuntimeError(
                "Real provider mode is not active. "
                "Set PIPELINE_PROVIDER_MODE=real to enable paid API calls."
            )
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. "
                "Add it to your `.env` file or environment before running real mode."
            )


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    """Return a process-wide cached `Settings` instance."""
    return _cached_settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (useful in tests that mutate env vars)."""
    _cached_settings.cache_clear()
