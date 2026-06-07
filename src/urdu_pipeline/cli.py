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
from urdu_pipeline.admin.seed import (
    seed_bucket,
    seed_provider_config,
    seed_service_identity,
    seed_user,
)
from urdu_pipeline.admin.users import (
    admin_create_user,
    admin_disable_user,
    admin_list_users,
    admin_reset_password,
    admin_revoke_service_identity,
)
from urdu_pipeline.infrastructure.db.migrations import connect_postgres, run_migrations
from urdu_pipeline.infrastructure.db.metadata import PostgresMetadataStore
from urdu_pipeline.processor.runtime import run_processor
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


@app.command(name="migrate-db")
def migrate_db(
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Run PostgreSQL metadata migrations."""
    target_url = database_url or get_settings().database_url
    connection = None
    try:
        connection = connect_postgres(target_url)
        report = run_migrations(connection)
    except Exception as exc:
        console.print(f"[red]Migration failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()

    console.print(
        "[green]Migrations complete.[/] "
        f"applied={len(report.applied_versions)} "
        f"skipped={len(report.skipped_versions)}"
    )


@app.command(name="seed-user")
def seed_user_cmd(
    username: str = typer.Option(..., "--username", help="Username for the new user."),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Create a pre-configured active user in the metadata database."""
    target_url = database_url or get_settings().database_url
    connection = None
    try:
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        record = seed_user(store, username=username)
    except Exception as exc:
        console.print(f"[red]seed-user failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(f"[green]User created.[/] user_id={record.user_id} username={record.username}")


@app.command(name="seed-service-identity")
def seed_service_identity_cmd(
    name: str = typer.Option(..., "--name", help="Name for the service identity."),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Create a pre-configured active service identity in the metadata database."""
    target_url = database_url or get_settings().database_url
    connection = None
    try:
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        record = seed_service_identity(store, name=name)
    except Exception as exc:
        console.print(f"[red]seed-service-identity failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(
        f"[green]Service identity created.[/] "
        f"service_identity_id={record.service_identity_id} name={record.name}"
    )


@app.command(name="seed-provider-config")
def seed_provider_config_cmd(
    provider_name: str = typer.Option(
        None,
        "--provider-name",
        help="Provider name (e.g. 'fake' or 'openai'). Defaults to 'fake'.",
    ),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Seed a provider config snapshot from current settings into the metadata database."""
    s = get_settings()
    target_url = database_url or s.database_url
    effective_provider = provider_name or ("openai" if s.pipeline_provider_mode == "real" else "fake")
    model_roles = {
        "transcription": s.transcription_model,
        "translation": s.translation_model,
        "article": s.article_model,
        "reconciliation": s.reconciliation_model,
    }
    connection = None
    try:
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        snapshot = seed_provider_config(
            store,
            provider_name=effective_provider,
            model_roles=model_roles,
        )
    except Exception as exc:
        console.print(f"[red]seed-provider-config failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(
        f"[green]Provider config created.[/] "
        f"config_version_id={snapshot.config_version_id} "
        f"provider={snapshot.provider_name} "
        f"roles={dict(snapshot.model_roles)}"
    )


@app.command(name="seed-bucket")
def seed_bucket_cmd(
    bucket: Optional[str] = typer.Option(
        None,
        "--bucket",
        help="Bucket name. Defaults to OBJECT_STORE_BUCKET from settings.",
    ),
    endpoint_url: Optional[str] = typer.Option(
        None,
        "--endpoint-url",
        help="Object store endpoint URL. Defaults to OBJECT_STORE_ENDPOINT_URL from settings.",
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help="Bucket region. Defaults to OBJECT_STORE_REGION from settings.",
    ),
) -> None:
    """Ensure the S3/MinIO bucket exists, creating it if necessary."""
    import importlib

    s = get_settings()
    effective_bucket = bucket or s.object_store_bucket
    effective_endpoint = endpoint_url or s.object_store_endpoint_url
    effective_region = region or s.object_store_region

    try:
        boto3 = importlib.import_module("boto3")
    except ModuleNotFoundError as exc:
        console.print(
            "[red]boto3 is required for seed-bucket.[/] "
            "Install the `object-store` extra: pip install -e '.[object-store]'"
        )
        raise typer.Exit(code=1) from exc

    try:
        client = boto3.client(
            "s3",
            endpoint_url=effective_endpoint or None,
            region_name=effective_region,
            aws_access_key_id=s.object_store_access_key or None,
            aws_secret_access_key=s.object_store_secret_key or None,
        )
        created = seed_bucket(client=client, bucket=effective_bucket, region=effective_region)
    except Exception as exc:
        console.print(f"[red]seed-bucket failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if created:
        console.print(f"[green]Bucket created:[/] {effective_bucket}")
    else:
        console.print(f"[yellow]Bucket already exists:[/] {effective_bucket}")


@app.command(name="admin-create-user")
def admin_create_user_cmd(
    username: str = typer.Option(..., "--username", help="Username for the new user."),
    password: str = typer.Option(..., "--password", help="Initial password (will be hashed)."),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Create an active user with a hashed password (no public signup endpoint)."""
    target_url = database_url or get_settings().database_url
    connection = None
    try:
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        record = admin_create_user(store, _Pbkdf2Hasher(), username=username, password=password)
    except Exception as exc:
        console.print(f"[red]admin-create-user failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(
        f"[green]User created.[/] user_id={record.user_id} username={record.username}"
    )


@app.command(name="admin-reset-password")
def admin_reset_password_cmd(
    user_id: str = typer.Option(..., "--user-id", help="User ID (usr_<hex>)."),
    new_password: str = typer.Option(..., "--new-password", help="New password (will be hashed)."),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Reset a user's password."""
    from urdu_pipeline.domain import UserId

    target_url = database_url or get_settings().database_url
    connection = None
    try:
        uid = UserId(user_id)
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        admin_reset_password(store, _Pbkdf2Hasher(), user_id=uid, new_password=new_password)
    except Exception as exc:
        console.print(f"[red]admin-reset-password failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(f"[green]Password reset.[/] user_id={user_id}")


@app.command(name="admin-disable-user")
def admin_disable_user_cmd(
    user_id: str = typer.Option(..., "--user-id", help="User ID (usr_<hex>)."),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Disable a user, preventing them from logging in."""
    from urdu_pipeline.domain import UserId

    target_url = database_url or get_settings().database_url
    connection = None
    try:
        uid = UserId(user_id)
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        record = admin_disable_user(store, user_id=uid)
    except Exception as exc:
        console.print(f"[red]admin-disable-user failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(f"[green]User disabled.[/] user_id={record.user_id} status={record.status}")


@app.command(name="admin-list-users")
def admin_list_users_cmd(
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """List all users."""
    target_url = database_url or get_settings().database_url
    connection = None
    try:
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        users = admin_list_users(store)
    except Exception as exc:
        console.print(f"[red]admin-list-users failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()

    table = Table(title=f"Users ({len(users)})")
    table.add_column("user_id")
    table.add_column("username")
    table.add_column("status")
    table.add_column("created_at")
    for u in users:
        table.add_row(str(u.user_id), u.username, str(u.status), str(u.created_at))
    console.print(table)


@app.command(name="admin-revoke-service-identity")
def admin_revoke_service_identity_cmd(
    service_identity_id: str = typer.Option(
        ..., "--service-identity-id", help="Service identity ID (svc_<hex>)."
    ),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        help="PostgreSQL connection URL. Defaults to DATABASE_URL from settings.",
    ),
) -> None:
    """Revoke a service identity, preventing it from authenticating."""
    from urdu_pipeline.domain import ServiceIdentityId

    target_url = database_url or get_settings().database_url
    connection = None
    try:
        svc_id = ServiceIdentityId(service_identity_id)
        connection = connect_postgres(target_url)
        store = PostgresMetadataStore(connection)
        record = admin_revoke_service_identity(store, service_identity_id=svc_id)
    except Exception as exc:
        console.print(f"[red]admin-revoke-service-identity failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if connection is not None:
            connection.close()
    console.print(
        f"[green]Service identity revoked.[/] "
        f"service_identity_id={record.service_identity_id} status={record.status}"
    )


@app.command(name="process")
def process_cmd(
    service_token: Optional[str] = typer.Option(
        None,
        "--service-token",
        envvar="SERVICE_AUTH_TOKEN",
        help="Service authentication token (set SERVICE_AUTH_TOKEN env var or pass here).",
    ),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api-url",
        help="Base URL of the Urdu Pipeline API to communicate with.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate configuration and exit without starting the processing loop.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Claim at most one job and exit after that processing attempt.",
    ),
) -> None:
    """Run the background job processor.

    The processor claims jobs from the queue, materializes audio from object
    storage, executes the pipeline stages, and uploads artifacts.

    Requires SERVICE_AUTH_TOKEN to authenticate against the internal API endpoints.
    Stage 5.1.2+ implements the full job lifecycle loop.
    """
    if not service_token:
        console.print(
            "[red]Error:[/] SERVICE_AUTH_TOKEN is required. "
            "Set the --service-token flag or the SERVICE_AUTH_TOKEN environment variable."
        )
        raise typer.Exit(code=1)

    if dry_run:
        console.print(
            f"[green]Processor configuration valid.[/] "
            f"api_url={api_url} service_token=<set>"
        )
        raise typer.Exit(code=0)

    processed = run_processor(
        service_token=service_token,
        api_url=api_url,
        once=once,
    )
    console.print(f"[green]Processor stopped.[/] processed_jobs={processed}")
    raise typer.Exit(code=0)


class _Pbkdf2Hasher:
    """Bcrypt hasher used by the admin CLI (delegates to ``BcryptHasher``)."""

    def hash_secret(self, secret: str) -> str:
        from urdu_pipeline.auth.hashing import BcryptHasher

        return BcryptHasher().hash_secret(secret)

    def verify_secret(self, secret: str, secret_hash: str) -> bool:
        from urdu_pipeline.auth.hashing import BcryptHasher

        return BcryptHasher().verify_secret(secret, secret_hash)


if __name__ == "__main__":  # pragma: no cover
    app()
