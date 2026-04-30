"""Streamlit UI for the Urdu Audio Article Prototype.

Run with:
    streamlit run src/urdu_pipeline/ui/streamlit_app.py

The UI never displays the API key. It does, however, surface every cost
estimate before any paid call, and refuses to proceed when the projected total
exceeds the configured hard cap.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import streamlit as st

from urdu_pipeline.artifacts.exporter import export_run_zip
from urdu_pipeline.artifacts.store import ArtifactStore, RunPaths
from urdu_pipeline.artifacts.validators import (
    ArtifactValidationError,
    require_artifact_type,
)
from urdu_pipeline.config.pricing import MissingPricingError, get_pricing_table
from urdu_pipeline.config.settings import get_settings, reset_settings_cache
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

st.set_page_config(page_title="Urdu Audio Article Prototype", layout="wide")


def _ensure_session_state() -> None:
    st.session_state.setdefault("current_run", None)


def _save_uploaded_to_tempfile(uploaded, suffix: str) -> Path:
    """Persist a Streamlit-uploaded file to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded.getbuffer())
    tmp.close()
    return Path(tmp.name)


def _new_store_for(audio_name: str) -> ArtifactStore:
    return ArtifactStore.for_new_run(Path(audio_name).stem)


def _existing_store(run_dir: Path) -> ArtifactStore:
    return ArtifactStore.for_existing_run(run_dir)


def _download_button_for(path: Path, label: str | None = None, mime: str = "application/octet-stream") -> None:
    if not path.exists():
        st.warning(f"File missing: {path}")
        return
    with path.open("rb") as fh:
        st.download_button(
            label or f"Download {path.name}",
            data=fh.read(),
            file_name=path.name,
            mime=mime,
        )


def _show_cost_estimates(audio_path: Path, settings) -> None:
    duration = probe_audio_duration_seconds(audio_path)
    pricing = get_pricing_table()
    rows = []
    try:
        t = estimate_transcription_cost(duration, settings.transcription_model, pricing=pricing)
        rows.append({
            "stage": "transcription",
            "model": settings.transcription_model,
            "estimate USD": round(t.estimated_cost_usd, 4),
        })
    except MissingPricingError as e:
        rows.append({
            "stage": "transcription",
            "model": settings.transcription_model,
            "estimate USD": f"missing pricing: {e}",
        })
    proxy_text = "x" * int(duration * 12)
    for role, model in (
        ("translation", settings.translation_model),
        ("article", settings.article_model),
    ):
        try:
            est = estimate_text_cost(
                input_text=proxy_text,
                model_id=model,
                expected_output_tokens=rough_token_count(proxy_text),
                pricing=pricing,
            )
            rows.append({"stage": role, "model": model, "estimate USD": round(est.estimated_cost_usd, 4)})
        except MissingPricingError as e:
            rows.append({"stage": role, "model": model, "estimate USD": f"missing pricing: {e}"})

    st.write(f"Detected audio duration: **{duration:.1f}s**")
    st.dataframe(rows, hide_index=True)


def _full_pipeline_tab():
    st.header("Full Pipeline")
    st.caption("Upload an audio file and run every stage end-to-end.")
    settings = get_settings()
    st.write(
        f"Provider mode: **{settings.pipeline_provider_mode}** | "
        f"Chunk length: **{settings.default_chunk_length_seconds}s** | "
        f"Overlap: **{settings.default_overlap_seconds}s**"
    )

    accepted = sorted(settings.accepted_audio_extensions_set)
    uploaded = st.file_uploader(
        f"Audio file (accepted: {', '.join(accepted)})",
        type=accepted,
    )
    budget = st.number_input(
        "Per-run budget (USD)", min_value=0.0, value=float(settings.default_budget_usd), step=1.0
    )
    confirm = False
    if settings.pipeline_provider_mode == "real":
        confirm = st.checkbox(
            "I confirm I want to run paid OpenAI API calls.",
            value=False,
        )

    if uploaded is None:
        return

    audio_path = _save_uploaded_to_tempfile(uploaded, suffix=Path(uploaded.name).suffix)
    _show_cost_estimates(audio_path, settings)

    if settings.pipeline_provider_mode == "real" and not confirm:
        st.info("Tick the confirmation checkbox above to start the paid run.")
        return

    if st.button("Run full pipeline"):
        store = _new_store_for(uploaded.name)
        guard = BudgetGuard.for_run(budget)

        with st.status("Chunking audio…", expanded=True) as status:
            chunk_manifest = run_chunker_stage(audio_path=audio_path, store=store)
            status.update(label=f"Chunked into {len(chunk_manifest.chunks)} chunks.")

        with st.status("Transcribing…", expanded=True) as status:
            raw = run_transcriber_stage(
                chunk_manifest=chunk_manifest, store=store, budget_guard=guard
            )
            status.update(label=f"Transcribed {len(raw.chunks)} chunks.")

        with st.status("Reconciling…", expanded=True) as status:
            reconciled = run_reconciler_stage(raw=raw, store=store)
            status.update(label=f"Reconciled into {len(reconciled.segments)} segments.")

        with st.status("Translating…", expanded=True) as status:
            translation = run_translator_stage(
                reconciled=reconciled, store=store, budget_guard=guard
            )
            status.update(label=f"Translated ({len(translation.full_text_english)} chars).")

        with st.status("Generating article…", expanded=True) as status:
            article_art = run_article_stage(
                translation=translation, store=store, budget_guard=guard
            )
            status.update(label=f"Article generated: {article_art.article.title}")

        export_path = export_run_zip(store.paths)
        st.session_state["current_run"] = str(store.paths.root)

        st.success(f"Run complete: {store.paths.root}")
        st.subheader("Downloads")
        _download_button_for(store.paths.artifacts / "chunk_manifest.json", mime="application/json")
        _download_button_for(store.paths.artifacts / "raw_urdu_transcript.json", mime="application/json")
        _download_button_for(store.paths.artifacts / "reconciled_urdu_transcript.json", mime="application/json")
        _download_button_for(store.paths.artifacts / "english_translation.json", mime="application/json")
        _download_button_for(store.paths.artifacts / "final_article.json", mime="application/json")
        _download_button_for(store.paths.artifacts / "final_article.md", mime="text/markdown")
        _download_button_for(export_path, mime="application/zip")


def _stage_tab(stage_name: str, run_func, expected_artifact_type: str | None = None):
    """Generic per-stage tab: lets user pick a run or upload a prior artifact."""
    settings = get_settings()
    st.header(stage_name.replace("_", " ").title())

    run_root = st.text_input(
        "Run directory (relative to OUTPUT_ROOT or absolute)",
        value=st.session_state.get("current_run") or "",
    )

    artifact_input = st.file_uploader(
        f"Or upload {expected_artifact_type or 'an artifact'} JSON",
        type=["json"],
        key=f"upload-{stage_name}",
    )

    if st.button(f"Run {stage_name}", key=f"run-{stage_name}"):
        if artifact_input is not None:
            try:
                payload = json.loads(artifact_input.getvalue().decode("utf-8"))
            except json.JSONDecodeError as e:
                st.error(f"Uploaded file is not valid JSON: {e}")
                return
            try:
                artifact = require_artifact_type(payload, expected_artifact_type or "")
            except ArtifactValidationError as e:
                st.error(str(e))
                return
            target_run = run_root or st.session_state.get("current_run")
            if not target_run:
                st.error("Please specify a run directory to write outputs to.")
                return
            store = _existing_store(Path(target_run))
        else:
            if not run_root:
                st.error("Provide a run directory or upload an artifact.")
                return
            store = _existing_store(Path(run_root))
            artifact_path = store.paths.artifacts / f"{expected_artifact_type}.json"
            try:
                artifact = require_artifact_type(artifact_path, expected_artifact_type or "")
            except ArtifactValidationError as e:
                st.error(str(e))
                return

        result = run_func(store=store, artifact=artifact, settings=settings)
        st.success(f"{stage_name} complete.")
        st.json(result.model_dump(mode="json"))


def _chunker_tab():
    st.header("Chunker")
    settings = get_settings()
    accepted = sorted(settings.accepted_audio_extensions_set)
    uploaded = st.file_uploader(
        f"Audio file (accepted: {', '.join(accepted)})", type=accepted, key="chunker-audio"
    )
    chunk_len = st.number_input(
        "Chunk length (seconds)",
        min_value=10,
        max_value=3600,
        value=settings.default_chunk_length_seconds,
    )
    overlap = st.number_input(
        "Overlap (seconds)",
        min_value=0,
        max_value=chunk_len - 1,
        value=settings.default_overlap_seconds,
    )
    if uploaded and st.button("Run chunker"):
        audio_path = _save_uploaded_to_tempfile(uploaded, suffix=Path(uploaded.name).suffix)
        store = _new_store_for(uploaded.name)
        artifact = run_chunker_stage(
            audio_path=audio_path,
            store=store,
            chunk_length_seconds=chunk_len,
            overlap_seconds=overlap,
        )
        st.session_state["current_run"] = str(store.paths.root)
        st.success(f"Chunked into {len(artifact.chunks)} chunks.")
        _download_button_for(store.paths.artifacts / "chunk_manifest.json", mime="application/json")
        _download_button_for(store.paths.artifacts / "chunk_summary.md", mime="text/markdown")


def _transcribe_tab():
    def _runner(store, artifact, settings):
        return run_transcriber_stage(
            chunk_manifest=artifact, store=store, budget_guard=BudgetGuard.for_run()
        )

    _stage_tab("transcribe", _runner, expected_artifact_type="chunk_manifest")


def _reconcile_tab():
    def _runner(store, artifact, settings):
        return run_reconciler_stage(raw=artifact, store=store)

    _stage_tab("reconcile", _runner, expected_artifact_type="raw_urdu_transcript")


def _translate_tab():
    def _runner(store, artifact, settings):
        return run_translator_stage(
            reconciled=artifact, store=store, budget_guard=BudgetGuard.for_run()
        )

    _stage_tab("translate", _runner, expected_artifact_type="reconciled_urdu_transcript")


def _article_tab():
    def _runner(store, artifact, settings):
        return run_article_stage(
            translation=artifact, store=store, budget_guard=BudgetGuard.for_run()
        )

    _stage_tab("article", _runner, expected_artifact_type="english_translation")


def _settings_tab():
    settings = get_settings()
    st.header("Settings")
    st.write(
        "Edit `.env` to change these values, then click **Reload settings** below."
    )
    rows = {
        "Provider mode": settings.pipeline_provider_mode,
        "API key set": bool(settings.openai_api_key),
        "Default budget (USD)": settings.default_budget_usd,
        "Hard cap (USD)": settings.hard_cap_usd,
        "Safety margin": settings.cost_safety_margin,
        "Chunk length (s)": settings.default_chunk_length_seconds,
        "Overlap (s)": settings.default_overlap_seconds,
        "Max chunk (MB)": settings.max_chunk_mb,
        "Accepted audio extensions": sorted(settings.accepted_audio_extensions_set),
        "Transcription model": settings.transcription_model,
        "Translation model": settings.translation_model,
        "Article model": settings.article_model,
        "Reconciliation model": settings.reconciliation_model,
        "Output root": str(settings.output_root_path),
        "Cache root": str(settings.cache_root_path),
        "Prompt version": settings.prompt_version,
    }
    st.json(rows)
    if st.button("Reload settings"):
        reset_settings_cache()
        st.success("Settings cache cleared. Re-render to see updated values.")


def main() -> None:
    _ensure_session_state()
    st.title("Urdu Audio Article Prototype")
    st.caption(
        "Audio -> Urdu transcript -> American English translation -> standalone article."
    )

    tabs = st.tabs(
        [
            "Full Pipeline",
            "Chunker",
            "Transcription",
            "Reconciliation",
            "Translation",
            "Article",
            "Settings",
        ]
    )
    with tabs[0]:
        _full_pipeline_tab()
    with tabs[1]:
        _chunker_tab()
    with tabs[2]:
        _transcribe_tab()
    with tabs[3]:
        _reconcile_tab()
    with tabs[4]:
        _translate_tab()
    with tabs[5]:
        _article_tab()
    with tabs[6]:
        _settings_tab()


main()
