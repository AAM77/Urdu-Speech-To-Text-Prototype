"""Import checks for planned API-conversion package boundaries."""

from __future__ import annotations

import importlib


def test_cloud_agnostic_module_skeleton_imports():
    modules = [
        "urdu_pipeline.domain",
        "urdu_pipeline.application",
        "urdu_pipeline.infrastructure",
        "urdu_pipeline.api",
        "urdu_pipeline.processor",
    ]

    for module_name in modules:
        assert importlib.import_module(module_name).__name__ == module_name
