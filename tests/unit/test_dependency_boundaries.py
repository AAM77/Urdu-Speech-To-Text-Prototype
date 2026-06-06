"""Dependency boundary checks for the cloud-agnostic package split."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _dependency_names(dependencies: list[str]) -> set[str]:
    names: set[str] = set()
    for item in dependencies:
        name = item
        for marker in ("[", ";", "<", ">", "="):
            name = name.split(marker, 1)[0]
        names.add(name.strip())
    return names


def test_optional_dependencies_are_split_by_runtime_boundary():
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    required_extras = {"core", "cli", "ui", "api", "processor", "dev"}
    optional = pyproject["project"]["optional-dependencies"]

    assert required_extras.issubset(optional)

    base_names = _dependency_names(pyproject["project"].get("dependencies", []))
    assert "openai" not in base_names
    assert "streamlit" not in base_names
    assert "typer" not in base_names
    assert "rich" not in base_names

    assert {"pydantic", "pydantic-settings", "python-dotenv", "rapidfuzz", "tiktoken"}.issubset(
        _dependency_names(optional["core"])
    )
    assert {"typer", "rich"}.issubset(_dependency_names(optional["cli"]))
    assert {"streamlit"}.issubset(_dependency_names(optional["ui"]))
    assert {"openai"}.issubset(_dependency_names(optional["processor"]))
    assert {"pytest", "pytest-mock"}.issubset(_dependency_names(optional["dev"]))


def test_core_imports_do_not_require_optional_runtime_dependencies():
    modules = [
        "urdu_pipeline",
        "urdu_pipeline.artifacts.store",
        "urdu_pipeline.artifacts.validators",
        "urdu_pipeline.cache.cache_keys",
        "urdu_pipeline.config.settings",
        "urdu_pipeline.costs.estimator",
        "urdu_pipeline.providers",
        "urdu_pipeline.schemas",
        "urdu_pipeline.stages",
        "urdu_pipeline.standalone.english_am_chunk_transcriber",
    ]
    blocked_roots = [
        "boto3",
        "botocore",
        "fastapi",
        "minio",
        "openai",
        "redis",
        "rich",
        "sqlalchemy",
        "streamlit",
        "typer",
        "uvicorn",
    ]
    script = f"""
import importlib
import importlib.abc
import sys

blocked_roots = {blocked_roots!r}


class OptionalDependencyBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in blocked_roots:
            raise AssertionError(f"optional dependency imported during core import: {{fullname}}")
        return None


sys.meta_path.insert(0, OptionalDependencyBlocker())

for module_name in {modules!r}:
    importlib.import_module(module_name)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
