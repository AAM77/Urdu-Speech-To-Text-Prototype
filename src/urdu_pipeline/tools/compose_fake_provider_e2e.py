"""Compose fake-provider E2E smoke test.

Runs inside the API container via ``make compose-test``.
"""

from __future__ import annotations

import http.cookiejar
import io
import json
import os
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from typing import Any


API_URL = os.environ.get("COMPOSE_E2E_API_URL", "http://127.0.0.1:8000")
USERNAME = os.environ.get("LOCAL_USERNAME", "local_user")
PASSWORD = os.environ.get("LOCAL_PASSWORD", "local_password_change_me")
TIMEOUT_SECONDS = int(os.environ.get("COMPOSE_E2E_TIMEOUT_SECONDS", "120"))


@dataclass
class ApiClient:
    base_url: str
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)

    def __post_init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    @property
    def csrf_token(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrf_token":
                return cookie.value
        raise RuntimeError("csrf_token cookie was not set")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        csrf: bool = False,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=body,
            method=method,
            headers=headers,
        )
        with self.opener.open(request, timeout=20) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def upload_direct(self, *, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        boundary = "----urdu-pipeline-compose-boundary"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="file"; '
                    f'filename="{filename}"\r\n'
                ).encode("ascii"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                f"\r\n--{boundary}--\r\n".encode("ascii"),
            ]
        )
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/uploads/direct",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-CSRF-Token": self.csrf_token,
            },
        )
        with self.opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


def main() -> int:
    client = ApiClient(API_URL)
    _assert_internal_ping()

    login = client.request_json(
        "POST",
        "/auth/login",
        payload={"username": USERNAME, "password": PASSWORD},
    )
    _assert_no_private_keys(login)
    if login.get("username") != USERNAME:
        raise AssertionError(f"unexpected login response: {login}")

    upload = client.upload_direct(
        filename="compose-smoke.wav",
        content=_make_wav(),
        content_type="audio/wav",
    )
    _assert_no_private_keys(upload)
    upload_id = upload["upload_id"]

    run = client.request_json(
        "POST",
        "/runs",
        payload={"upload_id": upload_id, "description": "compose fake-provider smoke"},
        csrf=True,
    )
    _assert_no_private_keys(run)
    run_id = run["run_id"]

    final = _poll_run(client, run_id)
    if final.get("status") != "succeeded":
        raise AssertionError(f"run did not succeed: {final}")

    events = client.request_json("GET", f"/runs/{run_id}/events")
    _assert_no_private_keys(events)
    if not events.get("events"):
        raise AssertionError("expected persisted processor events")

    artifacts = client.request_json("GET", f"/runs/{run_id}/artifacts")
    _assert_no_private_keys(artifacts)
    summaries = artifacts.get("artifacts", [])
    expected_types = {
        "chunk_manifest",
        "raw_urdu_transcript",
        "reconciled_urdu_transcript",
        "english_translation",
        "final_article",
    }
    actual_types = {item["artifact_type"] for item in summaries}
    if expected_types - actual_types:
        raise AssertionError(f"missing artifact types: {sorted(expected_types - actual_types)}")

    for summary in summaries:
        artifact_id = summary["artifact_id"]
        detail = client.request_json("GET", f"/artifacts/{artifact_id}")
        _assert_no_private_keys(detail)
        _download_and_assert(client, artifact_id, "json")
        if summary.get("has_markdown"):
            _download_and_assert(client, artifact_id, "markdown")

    _assert_database_document_chunks(run_id)
    _assert_object_store_outputs(run_id)
    print(f"compose fake-provider E2E passed: run_id={run_id}")
    return 0


def _poll_run(client: ApiClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.request_json("GET", f"/runs/{run_id}")
        if last.get("status") in {"succeeded", "failed", "cancelled"}:
            return last
        time.sleep(2.0)
    raise AssertionError(f"run timed out after {TIMEOUT_SECONDS}s: last={last}")


def _download_and_assert(client: ApiClient, artifact_id: str, artifact_format: str) -> None:
    response = client.request_json(
        "GET",
        f"/artifacts/{artifact_id}/download?format={artifact_format}",
    )
    _assert_no_private_keys(response)
    with urllib.request.urlopen(response["download_url"], timeout=20) as download:
        payload = download.read()
    if not payload:
        raise AssertionError(f"empty {artifact_format} download for {artifact_id}")


def _assert_internal_ping() -> None:
    token = os.environ.get("SERVICE_AUTH_TOKEN", "")
    request = urllib.request.Request(
        API_URL.rstrip("/") + "/internal/ping",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("principal_kind") != "service":
        raise AssertionError(f"internal ping did not authenticate as service: {body}")


def _assert_database_document_chunks(run_id: str) -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM artifact_document_chunks
                WHERE run_id = %s
                """,
                (run_id,),
            )
            count = int(cursor.fetchone()[0])
    if count <= 0:
        raise AssertionError("expected artifact_document_chunks rows")


def _assert_object_store_outputs(run_id: str) -> None:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT_URL") or None,
        region_name=os.environ.get("OBJECT_STORE_REGION", "local"),
        aws_access_key_id=os.environ.get("OBJECT_STORE_ACCESS_KEY") or None,
        aws_secret_access_key=os.environ.get("OBJECT_STORE_SECRET_KEY") or None,
    )
    bucket = os.environ["OBJECT_STORE_BUCKET"]
    artifact_listing = client.list_objects_v2(Bucket=bucket, Prefix="artifacts/")
    artifact_keys = [item["Key"] for item in artifact_listing.get("Contents", [])]
    if not any(key.endswith(".json") for key in artifact_keys):
        raise AssertionError("expected JSON artifacts in object store")
    if not any(key.endswith(".md") for key in artifact_keys):
        raise AssertionError("expected Markdown artifacts in object store")

    tmp_listing = client.list_objects_v2(
        Bucket=bucket,
        Prefix=f"tmp/users/",
    )
    leaked_tmp = [
        item["Key"]
        for item in tmp_listing.get("Contents", [])
        if f"/runs/{run_id}/" in item["Key"]
    ]
    if leaked_tmp:
        raise AssertionError(f"temporary run objects were not cleaned up: {leaked_tmp}")


def _assert_no_private_keys(payload: object) -> None:
    encoded = json.dumps(payload, sort_keys=True, default=str)
    for forbidden in ("object_key", "user_id", "job_id"):
        if forbidden in encoded:
            raise AssertionError(f"private field leaked in API response: {forbidden}")


def _make_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8_000)
        wav.writeframes(b"\x00\x00" * 8_000)
    return buffer.getvalue()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}")
        raise
