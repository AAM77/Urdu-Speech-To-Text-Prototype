"""Processor CLI runtime loop contracts."""

from __future__ import annotations

from typer.testing import CliRunner

from urdu_pipeline.cli import app


def test_process_command_runs_processing_loop_when_not_dry_run(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_run_processor(**kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr("urdu_pipeline.cli.run_processor", fake_run_processor)

    result = CliRunner().invoke(
        app,
        [
            "process",
            "--service-token",
            "service-token",
            "--api-url",
            "http://api:8000",
            "--once",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "service_token": "service-token",
            "api_url": "http://api:8000",
            "once": True,
        }
    ]
    assert "Job lifecycle loop not yet implemented" not in result.output


def test_process_command_keeps_dry_run_as_config_validation(monkeypatch):
    def fail_processor(**kwargs):  # pragma: no cover - should not be called
        raise AssertionError("processor loop should not run for --dry-run")

    monkeypatch.setattr("urdu_pipeline.cli.run_processor", fail_processor)

    result = CliRunner().invoke(
        app,
        ["process", "--service-token", "service-token", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "Processor configuration valid" in result.output
