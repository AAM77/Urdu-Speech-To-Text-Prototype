"""Prompt loader.

Prompts are versioned Markdown files in this directory. The loader returns the
raw text plus the file's path for use in manifests/cache keys.
"""

from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent

# Maps abstract prompt IDs to filename roots without the `_vN.md` suffix.
_PROMPT_FILES = {
    "transcription": "transcription",
    "transcription_english_am": "transcription_english_am",
    "reconciliation": "reconciliation",
    "translation": "translation",
    "article": "article",
    "glossary": "glossary",
}


def prompt_path(prompt_id: str, version: str) -> Path:
    if prompt_id not in _PROMPT_FILES:
        raise KeyError(f"Unknown prompt id: {prompt_id!r}")
    if prompt_id == "glossary":
        # Glossary is unversioned for the prototype; users edit it freely.
        return _PROMPT_DIR / "glossary.md"
    return _PROMPT_DIR / f"{_PROMPT_FILES[prompt_id]}_{version}.md"


def load_prompt(prompt_id: str, version: str = "v1") -> str:
    p = prompt_path(prompt_id, version)
    if not p.exists():
        raise FileNotFoundError(f"Prompt file not found: {p}")
    return p.read_text(encoding="utf-8")


def load_glossary() -> str:
    return load_prompt("glossary")
