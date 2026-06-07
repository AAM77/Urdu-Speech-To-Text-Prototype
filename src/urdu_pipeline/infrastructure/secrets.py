"""Environment-backed local secret provider."""

from __future__ import annotations

import os

from urdu_pipeline.application.ports import SecretValue


class EnvSecretProvider:
    """SecretProvider that reads secrets from environment variables.

    Fails closed: missing or empty environment variables raise KeyError
    rather than returning an empty or None value.
    """

    def get_secret(self, name: str) -> SecretValue:
        value = os.environ.get(name, "")
        if not value:
            raise KeyError(f"secret is not configured: {name}")
        return SecretValue(name=name, value=value)


__all__ = ["EnvSecretProvider"]
