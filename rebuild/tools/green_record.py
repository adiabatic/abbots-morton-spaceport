"""The last-green records every keyed stage skips on: how one is read, written and withdrawn, and the two digests the keys they hold are built from. A leaf — json, hashlib, os, datetime and pathlib — so the pyright gate the root conftest launches reaches it without importing the cycle driver, which would put the whole pipeline into every rebuild test's closure (`rebuild.tools.cycle_paths` says why that matters). `artifact_cycle` imports every name here, so the driver's callers keep reading them off the driver.

A record is `{format, fingerprint, finished_at}` plus whatever the stage stores beside its key — the per-label digest map behind the fingerprint, the contracts lane's per-test closures, a sweep's horizon — and the one rule every writer follows is that a green is recorded only over content whose key still matches once the work has run, while a red over content whose key matches the record deletes it.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def read_green_record(path: Path) -> dict | None:
    """A gate's last-green record ({fingerprint, finished_at}); None when absent or malformed."""
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
    """`files` is the per-file `label -> digest` map behind the fingerprint, when the caller has it: stored beside the key so a later skip miss can name exactly which input moved instead of reporting only that some digest did. `closures` is the contracts lane's per-test input closure over those same labels (`rebuild.tools.contracts_closure`), which is what lets a skip miss run only the tests the moved inputs can reach."""
    payload: dict = {"fingerprint": fingerprint}
    if files is not None:
        payload["files"] = files
    if closures is not None:
        payload["closures"] = closures
    _record_outcome(path, payload)


def clear_contradicted_green(path: Path, fingerprint: str | None) -> None:
    """A red result over content whose fingerprint still matches the recorded green contradicts the record; delete it so no later cycle can skip on a falsified green."""
    record = read_green_record(path)
    if fingerprint is not None and record is not None and record["fingerprint"] == fingerprint:
        path.unlink(missing_ok=True)


def _sha256_path(path: Path) -> str:
    """The streamed read matters here: the oracle's subset tables and M1.otf are large and ride keys a driver pass recomputes more than once. Spelled out rather than borrowing fingerprint.file_sha256 because this leaf imports nothing from rebuild.pipeline, and this one is called per file in a loop."""
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
