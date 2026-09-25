"""Read, write and delete the green records that keyed stages skip on, and compute the two digests their keys are built from. The module imports only the standard library, so `pyright_gate`, which the root conftest imports, can use it without importing the cycle driver, which would put the whole pipeline into every rebuild test's closure (`rebuild.tools.cycle_paths` says why that matters). `artifact_cycle` imports every name here and re-exports them.

A record is `{format, fingerprint, finished_at}` plus whatever the stage stores beside its key: the per-label digest map behind the fingerprint, the contracts lane's per-test closures, or a sweep's horizon. A writer records a green only when the key still matches after the work has run. `clear_contradicted_green` deletes a record when a red run over content with the same key contradicts it.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def read_green_record(path: Path) -> dict | None:
    """Return a gate's green record, or None when it is absent or malformed."""
    try:
        record = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if isinstance(record, dict) and isinstance(record.get("fingerprint"), str):
        return record
    return None


def _record_outcome(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"format": f"ams-{path.stem}/1", **payload, "finished_at": stamp}) + "\n")
    os.replace(tmp, path)


def record_green(
    path: Path, fingerprint: str, files: dict[str, str] | None = None, closures: dict | None = None
) -> None:
    """Write a green record for `fingerprint` to `path`. `files` is the `label -> digest` map behind the fingerprint, stored so a later skip miss can name the inputs that changed. `closures` is the contracts lane's per-test input closure over those labels (`rebuild.tools.contracts_closure`), which lets a skip miss run only the tests the changed inputs reach."""
    payload: dict = {"fingerprint": fingerprint}
    if files is not None:
        payload["files"] = files
    if closures is not None:
        payload["closures"] = closures
    _record_outcome(path, payload)


def clear_contradicted_green(path: Path, fingerprint: str | None) -> None:
    """Delete the green record at `path` when its fingerprint matches `fingerprint`, the key of content a run just failed on, so no later cycle skips on it."""
    record = read_green_record(path)
    if fingerprint is not None and record is not None and record["fingerprint"] == fingerprint:
        path.unlink(missing_ok=True)


def _sha256_path(path: Path) -> str:
    """Return a file's SHA-256, or "absent" when it cannot be read. It streams the file because the oracle's subset tables and M1.otf are large and are hashed into keys more than once per pass. It duplicates `fingerprint.file_sha256` because this module imports nothing from rebuild.pipeline."""
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return "absent"


def _digest_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()
