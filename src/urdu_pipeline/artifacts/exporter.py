"""ZIP export of safe run artifacts.

Only the contents of `artifacts/` and `exports/` are included; raw input audio
and individual chunks are excluded from the default export to keep the file
small and to avoid redistributing source recordings unintentionally.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from urdu_pipeline.artifacts.store import RunPaths


def export_run_zip(run_paths: RunPaths, *, include_chunks: bool = False) -> Path:
    target = run_paths.exports / "full_run_export.zip"
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Always include the artifacts/ directory.
        for f in sorted(run_paths.artifacts.glob("**/*")):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(run_paths.root))
        # Optional inclusion of audio chunks (off by default, large + sensitive).
        if include_chunks and run_paths.chunks.exists():
            for f in sorted(run_paths.chunks.glob("**/*")):
                if f.is_file():
                    zf.write(f, arcname=f.relative_to(run_paths.root))
    return target
