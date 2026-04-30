"""Maps abstract pipeline roles to concrete model IDs.

Stage code calls `get_model_roles().for_role("translation")` rather than
hardcoding a model ID. This makes it trivial to swap models via `.env` without
editing source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from urdu_pipeline.config.settings import Settings, get_settings

Role = Literal["transcription", "translation", "article", "reconciliation"]


@dataclass(frozen=True)
class ModelRoles:
    transcription: str
    translation: str
    article: str
    reconciliation: str

    def for_role(self, role: Role) -> str:
        return getattr(self, role)


def get_model_roles(settings: Settings | None = None) -> ModelRoles:
    s = settings or get_settings()
    return ModelRoles(
        transcription=s.transcription_model,
        translation=s.translation_model,
        article=s.article_model,
        reconciliation=s.reconciliation_model,
    )
