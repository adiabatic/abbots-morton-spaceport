"""The record of the accept's four-letter sweep (`engine/review/accept_sweep.py`, doc/review-design.md §2.3), which carries what the sweep finds to the corpus build.

The accept writes it at `var/review/accept-sweep.json` (`RECORD_RELATIVE`, gitignored like the rest of `var/`): an `ams-review-accept-sweep/1` document holding the key it was swept for (`SweepKey`: the accepted snapshot's digest, the new build's tables' stamp, the new font's sha256, and the two contexts, the corpus build's and the sweep's), when it ran and what it cost, `configs`, every settled configuration's texts found, each as its code points the way a unit's `codepoints` writes them (an empty list where nothing was found), and `changes`, what each text shows under its configuration.

A record applies to a build whose inputs give its key (`applicable`). The corpus build hands an applicable record's texts to the finder as candidates beside the certificates (`build.build_table_diff`), and the table-diff inputs fingerprint's `accept_sweep` component is the record's digest (`component`), or `NO_FINDS` when no record applies or the one that applies found nothing, since such a record adds no candidate. So the manifest says which record a corpus was built with, and a record with finds moves the component for the freshness check and `artifact_cycle.corpus_build_skippable` alike.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.pipeline import fingerprint

RECORD_FORMAT = "ams-review-accept-sweep/1"
RECORD_RELATIVE = Path("var") / "review" / "accept-sweep.json"
# The `accept_sweep` component of a corpus built with no record, or with one that found nothing: the digest of a fixed text, so the component is a hash like the others.
NO_FINDS = hashlib.sha256(b"ams-review-accept-sweep: no finds").hexdigest()


@dataclass(frozen=True)
class SweepKey:
    """The inputs a sweep's finds hold for: the accepted snapshot's digest, the new build's tables' stamp (`snapshot.tables_stamp`), the sha256 of its Senior-Regular.otf, the letters of context the corpus build's sweep puts around a changed letter (`ordinary_context`) and the letters the accept's sweep puts there (`context`)."""

    snapshot: str
    tables: str
    font: str
    ordinary_context: int
    context: int

    def record(self) -> dict:
        return asdict(self)


def key_for(
    snapshot_digest: str | None,
    tables: str | None,
    font_sha256: str | None,
    ordinary_context: int,
    context: int,
) -> SweepKey | None:
    """The key of a build's inputs, or None when one of them is missing: no snapshot, tables whose heads give no one stamp, or no font."""
    if not snapshot_digest or not tables or not font_sha256:
        return None
    return SweepKey(snapshot_digest, tables, font_sha256, ordinary_context, context)


def encode(text: str) -> str:
    """A text as its code points, colon-separated hex of at least four digits, the way a unit's `codepoints` writes them."""
    return ":".join(f"{ord(character):04X}" for character in text)


def decode(codepoints: str) -> str:
    return "".join(chr(int(value, 16)) for value in codepoints.split(":") if value)


def read(path: Path) -> dict | None:
    """The record at `path`, or None when there is no file there, it does not read, or it is not an `ams-review-accept-sweep/1` document. The file's existence is asked first, so no file is opened when there is none."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(record, dict) or record.get("format") != RECORD_FORMAT:
        return None
    if not isinstance(record.get("key"), dict) or not isinstance(record.get("configs"), dict):
        return None
    return record


def applicable(path: Path | None, key: SweepKey | None) -> dict | None:
    """The record at `path` when its key is `key`, else None."""
    if path is None or key is None:
        return None
    record = read(path)
    return record if record is not None and record["key"] == key.record() else None


def texts(record: Mapping | None) -> dict[str, list[str]]:
    """The record's texts by configuration, decoded, each configuration's in the record's order; none for no record."""
    if record is None:
        return {}
    return {
        str(config): [decode(value) for value in values if isinstance(value, str)]
        for config, values in record["configs"].items()
        if isinstance(values, list)
    }


def has_finds(record: Mapping | None) -> bool:
    return any(texts(record).values())


def changes(record: Mapping | None) -> list[str]:
    """The distinct changes the record names, in its order: what the refusal counts."""
    if record is None:
        return []
    found = record.get("changes")
    return list(
        dict.fromkeys(
            entry["change"]
            for entry in found or ()
            if isinstance(entry, dict) and isinstance(entry.get("change"), str)
        )
    )


def component(record: Mapping | None) -> str:
    """The `accept_sweep` component of a corpus built with `record`: `NO_FINDS` for no record or one that found nothing, else the sha256 of its key and texts as canonical JSON, leaving out when it ran and what it cost, so sweeping the same inputs again gives the same value."""
    if not has_finds(record):
        return NO_FINDS
    assert record is not None
    canonical = {"key": record["key"], "configs": record["configs"]}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot_digest(snapshot_record: Path | None) -> str | None:
    """The digest a snapshot's `snapshot.json` records, read without checking its files (`snapshot.load` checks them), or None when there is none that reads."""
    if snapshot_record is None or not Path(snapshot_record).is_file():
        return None
    try:
        record = json.loads(Path(snapshot_record).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    digest = record.get("digest") if isinstance(record, dict) else None
    return digest if isinstance(digest, str) else None


def component_for(
    path: Path,
    snapshot_record: Path | None,
    tables: str | None,
    font: Path,
    ordinary_context: int,
    context: int,
) -> str:
    """The `accept_sweep` component a corpus build over these inputs records: the record at `path` that applies to the snapshot whose `snapshot.json` is `snapshot_record`, the tables' stamp `tables` and the font at `font` (`component`). With no record file it is `NO_FINDS` and nothing is opened or hashed."""
    if not Path(path).is_file():
        return NO_FINDS
    try:
        font_sha = fingerprint.file_sha256(Path(font))
    except OSError:
        font_sha = None
    key = key_for(snapshot_digest(snapshot_record), tables, font_sha, ordinary_context, context)
    return component(applicable(path, key))


def write(
    path: Path,
    key: SweepKey,
    *,
    ran_at: str,
    cost: Mapping,
    configs: Sequence[str],
    finds: Mapping[str, Sequence[tuple[str, str]]],
) -> dict:
    """Write the record of a sweep over `key` to `path` through a staging file renamed into place, and return it. `finds` maps a configuration to its (text, change) pairs in order; every configuration of `configs` is listed, with an empty list where nothing was found."""
    listed = list(dict.fromkeys([*configs, *finds]))
    record = {
        "format": RECORD_FORMAT,
        "key": key.record(),
        "ran_at": ran_at,
        "cost": dict(cost),
        "configs": {
            config: list(dict.fromkeys(encode(text) for text, _change in finds.get(config, ())))
            for config in listed
        },
        "changes": [
            {"config": config, "change": change, "text": encode(text)}
            for config in listed
            for text, change in finds.get(config, ())
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.partial")
    staging.write_text(json.dumps(record, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(staging, path)
    return record
