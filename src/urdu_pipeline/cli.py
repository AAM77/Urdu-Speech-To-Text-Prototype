"""Command-line interface (Typer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from urdu_pipeline.artifacts.exporter import export_run_zip
from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.artifacts.validators import (
    ArtifactValidationError,
    load_and_validate_artifact,
    require_artifact_type,
)
from urdu_pipeline.config.pricing import MissingPricingError, get_pricing_table
from urdu_pipeline.config.settings import get_settings
from urdu_pipeline.costs.budget_guard import BudgetGuard
from urdu_pipeline.costs.estimator import (
    estimate_text_cost,
    estimate_transcription_cost,
    rough_token_count,
)
from urdu_pipeline.stages.article_generator import run_article_stage
from urdu_pipeline.stages.chunker import (
    probe_audio_duration_seconds,
    run_chunker_stage,
)
from urdu_pipeline.stages.transcriber import run_transcriber_stage
from urdu_pipeline.stages.transcript_reconciler import run_reconciler_stage
from urdu_pipeline.stages.translator import run_translator_stage
from urdu_pipeline.standalone.english_am_chunk_transcriber import run_english_am_transcriber

app = typer.Typer(
    add_completion=False,
    help="Urdu audio -> Urdu transcript -> American English translation -> standalone article.",
)

console = Console()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _show_estimates(audio_path: Path, settings) -> None:
    duration = probe_audio_duration_seconds(audio_path)
    pricing = get_pricing_table()

    rows = []
    try:
        t = estimate_transcription_cost(duration, settings.transcription_model, pricing=pricing)
        rows.append(("transcription", settings.transcription_model, f"${t.estimated_cost_usd:.4f}", json.dumps(t.detail)))
    except MissingPricingError as e:
        rows.append(("transcription", settings.transcription_model, "?", f"missing: {e}"))

    # Rough text-stage estimates assume ~4 chars/token of Urdu output for a
    # 5-minute lecture; we don't have a reconciled transcript at estimate-time
    # so we proxy with the audio duration.
    proxy_chars = int(duration * 12)  # ~12 chars/sec speaking
    proxy_text = "x" * proxy_chars
    for role, model_id in (
        ("translation", settings.translation_model),
        ("article", settings.article_model),
    ):
        try:
            est = estimate_text_cost(
                input_text=proxy_text,
                model_id=model_id,
                expected_output_tokens=rough_token_count(proxy_text),
                pricing=pricing,
            )
            rows.append((role, model_id, f"${est.estimated_cost_usd:.4f}", json.dumps(est.detail)))
        except MissingPricingError as e:
            rows.append((role, model_id, "?", f"missing: {e}"))

    table = Table(title=f"Estimated cost for {audio_path.name} ({duration:.1f}s)")
    table.add_column("stage")
    table.add_column("model")
    table.add_column("estimate")
    table.add_column("detail", overflow="fold")
    for r in rows:
        table.add_row(*r)
    console.print(table)


def _confirm_paid_run(*, confirm_paid_run: bool) -> None:
    s = get_settings()
    if s.pipeline_provider_mode != "real":
        return
    if confirm_paid_run:
        return
    console.print(
        "[yellow]Real-provider mode is active but --confirm-paid-run was not "
        "passed. Re-run with --confirm-paid-run to proceed.[/]"
    )
    raise typer.Exit(code=2)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------
@app.command()
def estimate(
    audio: Path = typer.Option(..., "--audio", help="Path to audio file."),
) -> None:
    """Print a cost estimate for a full pipeline run on the given audio."""
    s = get_settings()
    if not audio.exists():
        console.print(f"[red]Audio file not found: {audio}[/]")
        raise typer.Exit(code=1)
    if not s.is_audio_extension_allowed(audio):
        console.print(
            f"[red]Audio extension '{audio.suffix}' is not in "
            f"ACCEPTED_AUDIO_EXTENSIONS={sorted(s.accepted_audio_extensions_set)}.[/]"
        )
        raise typer.Exit(code=1)
    _show_estimates(audio, s)


@app.command()
def chunk(
    audio: Path = typer.Option(..., "--audio", help="Path to audio file."),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Run directory (defaults to a new run under OUTPUT_ROOT)."
    ),
    chunk_length_seconds: int = typer.Option(None, "--chunk-length-seconds"),
    overlap_seconds: int = typer.Option(None, "--overlap-seconds"),
) -> None:
    """Split an audio file into 5-minute chunks (configurable)."""
    store = (
        ArtifactStore.for_existing_run(out)
        if out is not None
        else ArtifactStore.for_new_run(audio.stem)
    )
    artifact = run_chunker_stage(
        audio_path=audio,
        store=store,
        chunk_length_seconds=chunk_length_seconds,
        overlap_seconds=overlap_seconds,
    )
    console.print(f"[green]Chunked into {len(artifact.chunks)} chunks.[/]")
    console.print(f"Run directory: {store.paths.root}")


@app.command()
def transcribe(
    chunk_manifest: Path = typer.Option(..., "--chunk-manifest"),
    confirm_paid_run: bool = typer.Option(False, "--confirm-paid-run"),
) -> None:
    """Transcribe chunks listed in the given chunk manifest."""
    _confirm_paid_run(confirm_paid_run=confirm_paid_run)
    manifest = require_artifact_type(chunk_manifest, "chunk_manifest")
    store = ArtifactStore.for_existing_run(chunk_manifest.resolve().parent.parent)
    budget = BudgetGuard.for_run()
    artifact = run_transcriber_stage(
        chunk_manifest=manifest, store=store, budget_guard=budget
    )
    console.print(f"[green]Transcribed {len(artifact.chunks)} chunks (Urdu script).[/]")


@app.command("transcribe-english-am")
def transcribe_english_am(
    chunk_manifest: Path = typer.Option(..., "--chunk-manifest"),
    confirm_paid_run: bool = typer.Option(False, "--confirm-paid-run"),
) -> None:
    """Transcribe English-language chunks into American English (same artifact layout)."""
    _confirm_paid_run(confirm_paid_run=confirm_paid_run)
    manifest = require_artifact_type(chunk_manifest, "chunk_manifest")
    store = ArtifactStore.for_existing_run(chunk_manifest.resolve().parent.parent)
    budget = BudgetGuard.for_run()
    artifact = run_english_am_transcriber(
        chunk_manifest=manifest,
        store=store,
        budget_guard=budget,
    )
    console.print(f"[green]Transcribed {len(artifact.chunks)} chunks (American English).[/]")


@app.command()
def reconcile(
    transcript: Path = typer.Option(..., "--transcript"),
) -> None:
    """Reconcile overlapping raw Urdu transcript chunks into one transcript."""
    raw = require_artifact_type(transcript, "raw_urdu_transcript")
    store = ArtifactStore.for_existing_run(transcript.resolve().parent.parent)
    artifact = run_reconciler_stage(raw=raw, store=store)
    console.print(f"[green]Reconciled into {len(artifact.segments)} segments.[/]")


@app.command()
def translate(
    transcript: Path = typer.Option(..., "--transcript"),
    confirm_paid_run: bool = typer.Option(False, "--confirm-paid-run"),
) -> None:
    """Translate a reconciled Urdu transcript into American English."""
    _confirm_paid_run(confirm_paid_run=confirm_paid_run)
    reconciled = require_artifact_type(transcript, "reconciled_urdu_transcript")
    store = ArtifactStore.for_existing_run(transcript.resolve().parent.parent)
    budget = BudgetGuard.for_run()
    artifact = run_translator_stage(
        reconciled=reconciled, store=store, budget_guard=budget
    )
    console.print(f"[green]Translated transcript ({len(artifact.full_text_english)} chars).[/]")


@app.command()
def article(
    translation: Path = typer.Option(..., "--translation"),
    confirm_paid_run: bool = typer.Option(False, "--confirm-paid-run"),
) -> None:
    """Generate a polished American English article from the translation."""
    _confirm_paid_run(confirm_paid_run=confirm_paid_run)
    tr = require_artifact_type(translation, "english_translation")
    store = ArtifactStore.for_existing_run(translation.resolve().parent.parent)
    budget = BudgetGuard.for_run()
    artifact = run_article_stage(translation=tr, store=store, budget_guard=budget)
    console.print(f"[green]Article generated: {artifact.article.title}[/]")


@app.command(name="run-all")
def run_all(
    audio: Path = typer.Option(..., "--audio"),
    budget: float = typer.Option(None, "--budget", help="Per-run USD budget."),
    provider_mode: Optional[str] = typer.Option(
        None, "--provider-mode", help="Override PIPELINE_PROVIDER_MODE for this run."
    ),
    confirm_paid_run: bool = typer.Option(False, "--confirm-paid-run"),
) -> None:
    """Run the entire pipeline on a fresh audio file."""
    if provider_mode is not None:
        import os

        os.environ["PIPELINE_PROVIDER_MODE"] = provider_mode
        from urdu_pipeline.config.settings import reset_settings_cache

        reset_settings_cache()

    s = get_settings()
    if s.pipeline_provider_mode == "real" and not confirm_paid_run:
        console.print(
            "[yellow]Real provider mode requires --confirm-paid-run. "
            "Showing cost estimate and exiting.[/]"
        )
        _show_estimates(audio, s)
        raise typer.Exit(code=2)

    store = ArtifactStore.for_new_run(audio.stem)
    budget_guard = BudgetGuard.for_run(budget)

    chunk_manifest = run_chunker_stage(audio_path=audio, store=store)
    raw = run_transcriber_stage(
        chunk_manifest=chunk_manifest, store=store, budget_guard=budget_guard
    )
    reconciled = run_reconciler_stage(raw=raw, store=store)
    translation = run_translator_stage(
        reconciled=reconciled, store=store, budget_guard=budget_guard
    )
    article_artifact = run_article_stage(
        translation=translation, store=store, budget_guard=budget_guard
    )
    export_path = export_run_zip(store.paths)

    console.print(f"[green]Run complete: {store.paths.root}[/]")
    console.print(f"Article: {article_artifact.article.title}")
    console.print(f"Export: {export_path}")


@app.command(name="validate-artifact")
def validate_artifact(
    artifact: Path = typer.Option(..., "--artifact"),
) -> None:
    """Validate an artifact JSON against its schema."""
    try:
        loaded = load_and_validate_artifact(artifact)
    except ArtifactValidationError as e:
        console.print(f"[red]Invalid: {e}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]Valid artifact[/]: type={loaded.artifact_type}")


@app.command(name="export-run")
def export_run(
    run_dir: Path = typer.Option(..., "--run-dir"),
    include_chunks: bool = typer.Option(False, "--include-chunks"),
) -> None:
    """Export a run directory to a ZIP under exports/."""
    store = ArtifactStore.for_existing_run(run_dir)
    target = export_run_zip(store.paths, include_chunks=include_chunks)
    console.print(f"[green]Exported:[/] {target}")


if __name__ == "__main__":  # pragma: no cover
    app()
