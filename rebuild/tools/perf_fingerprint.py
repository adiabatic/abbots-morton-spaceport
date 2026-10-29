"""The output fingerprint the speed-up check (`rebuild/tools/perf_check.py`) compares between a scenario's base and candidate sides: every configuration's table digest, the compiled and site fonts, the review corpus without its stamps, the verdict store and the complaint list after the pass, the two corpus stores apart, and the review-facts pins the pass rewrote.

Run it with the tree under test's own interpreter and code, never the harness's:

  uv run --directory <tree> python -P <main>/rebuild/tools/perf_fingerprint.py --root <tree> --out <file>

`-P` keeps this script's directory off `sys.path`, and the script puts `--root` first on it before importing, so `rebuild.pipeline.table` and `rebuild.pipeline.fingerprint` come from the tree under test and a candidate that changes an intermediate encoding is read by its own reader. Its only other imports are from the standard library; it never imports a harness module.

The output is one `ams-perf-fingerprint/1` document:

- `tables`: for each `rebuild/out/m1/windows-<config>.tsv.gz` present, `table.table_digest` over its decision table and `joins-<config>.tsv`, read one configuration at a time through the official readers, so a missing configuration shows up as a difference.
- `fonts`: `fingerprint.font_content_digest` of `rebuild/out/m1/M1.otf`, the corpus's `fonts/{before,after}.otf` and the six site fonts, null for a font that is missing.
- `corpus`: one digest per file under `rebuild/out/review/` but the two stores, and a digest over the sorted `path<TAB>digest` lines. `manifest.json` and `review-facts.json` are digested as canonical JSON without the keys `MANIFEST_STAMPS` names (for the facts file, inside its `corpus` block), and without each manifest font's raw `sha256`; a gzip sidecar as its decompressed stream, with a JSON header line re-serialized without the keys `SIDECAR_STAMPS` names; a corpus font by its content digest; every other file raw.
- `stores`: the same header projection applied to the stores `STORES` names. A difference there is an intermediate format differing, which the check reports apart.
- `verdicts`: the store after the pass, projected to its `format`, the sorted `(unit, verdict, note)` of its records and the sorted unit ids it cleared, without the keys `STORE_STAMPS` names and without any record's `at`, since the fills stamp `at` with the corpus stamp.
- `complaints`: the complaint list as canonical JSON without the keys `COMPLAINT_STAMPS` names and without any `at` inside `groups`.
- `review_facts_pins`: the raw digest of `rebuild/review-facts-pins.json` as the pass rewrote it.

The first `--baseline` check confirms the projected keys: its two sides must fingerprint identically, and a difference names a missed stamp.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

FORMAT = "ams-perf-fingerprint/1"
MANIFEST_STAMPS = ("generated_at", "repo_head", "inputs_fingerprint")
SIDECAR_STAMPS = ("manifest_sha256", "generated_at", "environment", "environment_lines", "environment_detail")
STORE_STAMPS = ("manifest_generated_at", "exported_at")
COMPLAINT_STAMPS = ("manifest_generated_at", "since")
STORES = ("unit-cache.ndjson.gz", "ink-signatures.tsv.gz")
SITE_FONTS = tuple(
    f"site/AbbotsMortonSpaceport{family}-{weight}.otf"
    for family in ("Mono", "SansJunior", "SansSenior")
    for weight in ("Regular", "Bold")
)
FONTS = (
    "rebuild/out/m1/M1.otf",
    "rebuild/out/review/fonts/before.otf",
    "rebuild/out/review/fonts/after.otf",
    *SITE_FONTS,
)
M1 = Path("rebuild/out/m1")
REVIEW = Path("rebuild/out/review")
AUTOSAVE = Path("verdicts-autosave.json")
COMPLAINTS = Path("tmp/complaints-data.json")
FACTS_PINS = Path("rebuild/review-facts-pins.json")
_CHUNK = 1 << 22


def canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(path: Path) -> str:
    document = json.loads(path.read_bytes())
    for key in MANIFEST_STAMPS:
        document.pop(key, None)
    for font in (document.get("fonts") or {}).values():
        if isinstance(font, dict):
            font.pop("sha256", None)
    return sha256_bytes(canonical(document))


def facts_digest(path: Path) -> str:
    document = json.loads(path.read_bytes())
    corpus = document.get("corpus")
    if isinstance(corpus, dict):
        for key in MANIFEST_STAMPS:
            corpus.pop(key, None)
    return sha256_bytes(canonical(document))


def gz_digest(path: Path) -> str:
    """Digest a gzip file's decompressed stream, re-serializing a JSON header line that carries `format` without the keys `SIDECAR_STAMPS` names, so the gzip member's own header and the stamps never count."""
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except ValueError:
            header = None
        if isinstance(header, dict) and "format" in header:
            for key in SIDECAR_STAMPS:
                header.pop(key, None)
            digest.update(canonical(header) + b"\n")
        else:
            digest.update(first)
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_file_digest(corpus: Path, path: Path, font_digest: Callable[[Path], str]) -> str:
    rel = path.relative_to(corpus).as_posix()
    if rel == "manifest.json":
        return manifest_digest(path)
    if rel == "review-facts.json":
        return facts_digest(path)
    if rel.startswith("fonts/") and path.suffix == ".otf":
        return font_digest(path)
    if path.suffix == ".gz":
        return gz_digest(path)
    return file_sha256(path)


def corpus_digests(corpus: Path, font_digest: Callable[[Path], str]) -> tuple[dict, dict[str, str | None]]:
    """Return the corpus block and the stores block for the review corpus at `corpus`."""
    files: dict[str, str] = {}
    stores: dict[str, str | None] = {name: None for name in STORES}
    if corpus.is_dir():
        for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
            rel = path.relative_to(corpus).as_posix()
            if rel in STORES:
                stores[rel] = gz_digest(path)
            else:
                files[rel] = corpus_file_digest(corpus, path, font_digest)
    lines = "\n".join(f"{rel}\t{digest}" for rel, digest in sorted(files.items()))
    return {"digest": sha256_bytes(lines.encode()), "files": files}, stores


def _without_at(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_at(item) for key, item in value.items() if key != "at"}
    if isinstance(value, list):
        return [_without_at(item) for item in value]
    return value


def store_digest(path: Path) -> str | None:
    """Digest a verdict store's projection: its format, the sorted (unit, verdict, note) of its records and the sorted unit ids it cleared. The `cleared` list holds `{unit, at}` entries, or bare unit ids in an older file."""
    if not path.is_file():
        return None
    document = json.loads(path.read_bytes())
    for key in STORE_STAMPS:
        document.pop(key, None)
    records = sorted(
        (str(record.get("unit")), record.get("verdict"), record.get("note") or "")
        for record in document.get("verdicts") or []
    )
    cleared = sorted(
        str(entry.get("unit")) if isinstance(entry, dict) else str(entry)
        for entry in document.get("cleared") or []
    )
    return sha256_bytes(
        canonical(
            {"format": document.get("format"), "verdicts": [list(row) for row in records], "cleared": cleared}
        )
    )


def complaints_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    document = json.loads(path.read_bytes())
    for key in COMPLAINT_STAMPS:
        document.pop(key, None)
    if "groups" in document:
        document["groups"] = _without_at(document["groups"])
    return sha256_bytes(canonical(document))


def table_digests(m1: Path, table: ModuleType) -> dict[str, str]:
    """Return `table.table_digest` per configuration whose windows file exists, through the tree's own readers, one configuration at a time so only one decision table is held."""
    digests: dict[str, str] = {}
    prefix, suffix = "windows-", ".tsv.gz"
    for path in sorted(m1.glob(f"{prefix}*{suffix}")):
        config = path.name[len(prefix) : -len(suffix)]
        _, decision = table.read_windows(table.windows_path(m1, config))
        joins = table.read_join_tsv(m1 / f"joins-{config}.tsv")
        digests[config] = table.table_digest(decision, joins)
        del decision, joins
    return digests


def fingerprint_tree(root: Path, *, table: ModuleType, font_digest: Callable[[Path], str]) -> dict:
    corpus, stores = corpus_digests(root / REVIEW, font_digest)
    pins = root / FACTS_PINS
    return {
        "format": FORMAT,
        "tables": table_digests(root / M1, table),
        "fonts": {rel: font_digest(root / rel) if (root / rel).is_file() else None for rel in FONTS},
        "corpus": corpus,
        "verdicts": store_digest(root / AUTOSAVE),
        "complaints": complaints_digest(root / COMPLAINTS),
        "stores": stores,
        "review_facts_pins": file_sha256(pins) if pins.is_file() else None,
    }


def tree_modules(root: Path) -> tuple[ModuleType, ModuleType]:
    """Import `rebuild.pipeline.table` and `rebuild.pipeline.fingerprint` from `root`, which goes first on `sys.path`."""
    sys.path.insert(0, str(root))
    return importlib.import_module("rebuild.pipeline.table"), importlib.import_module(
        "rebuild.pipeline.fingerprint"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fingerprint one tree's outputs for the speed-up check.")
    parser.add_argument("--root", type=Path, required=True, help="the tree under test (a scratch clone)")
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the ams-perf-fingerprint/1 JSON"
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    table, fingerprint = tree_modules(root)
    document = fingerprint_tree(root, table=table, font_digest=fingerprint.font_content_digest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
