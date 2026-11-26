"""The output fingerprint #618's acceptance bar compares on the live tree, read before a change's first build and again after its landing pass: every configuration's table digest, the compiled and published fonts, the review corpus without its stamps, and the verdict store after the pass.

Run it with the tree under test's own interpreter and code, never the harness's:

  uv run --directory <tree> python -P <main>/engine/tools/perf_fingerprint.py --root <tree> --out <file>

`-P` keeps this script's directory off `sys.path`, and the script puts `--root` first on it before importing, so `engine.pipeline.table` and `engine.pipeline.fingerprint` come from the tree under test and a change to an intermediate encoding is read by its own reader. Its only other imports are from the standard library; it never imports a harness module.

The output is one `ams-perf-fingerprint/3` document:

- `tables`: for each configuration with a windows head under `engine/out/build` (`windows-<config>.tsv.gz.head`), and for a tree from before the segment format each `windows-<config>.tsv.gz` present, `table.table_digest` over its decision table and `joins-<config>.tsv`, read one configuration at a time through the tree's own readers, so a missing configuration shows up as a difference. A configuration with a head is read in its materialized form (`table.materialized_table_digest`): its head file checked against its head, every part's size and sha256 checked against it too, and the base and segments merged into the whole payload, each row line hashed as it streams, so no configuration's rows are held; a tree whose `table` has no such reader builds the rows one configuration at a time (`table.read_materialized_windows`). The digest recomputed from those rows must equal the crate's own, which the head records (`table.TABLE_DIGEST_STAMP`), and the script exits nonzero naming the configuration when it does not, so a fault in the merge cannot hide behind a digest the crate reported of itself.
- `fonts`: `fingerprint.font_content_digest` of `engine/out/build/Senior-Regular.otf`, the corpus's `fonts/{before,after}.otf` and the six fonts under site/, which gate:make-test's session publishes from the build (`engine/tools/build_fonts.py`), null for a font that is missing.
- `corpus`: one digest per file under `engine/out/review/`, and a digest over the sorted `path<TAB>digest` lines. `manifest.json` is digested as canonical JSON without the keys `MANIFEST_STAMPS` names and without each manifest font's raw `sha256`; a gzip sidecar as its decompressed stream, with a JSON header line re-serialized without the keys `SIDECAR_STAMPS` names; a corpus font by its content digest; every other file raw.
- `verdicts`: the store after the pass, projected to its `format`, the sorted `(unit, verdict, note)` of its records and the sorted unit ids it cleared, without the keys `STORE_STAMPS` names and without any record's `at`, which records when a verdict was made (`store_digest`).

Two readings of the same outputs fingerprint identically, so a difference across a pass that changed no output names a missed stamp, and a tree whose windows rows are held as a base and segments fingerprints as its folded form and as the single file a build before the segment format wrote.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType

FORMAT = "ams-perf-fingerprint/3"
MANIFEST_STAMPS = ("generated_at", "repo_head", "inputs_fingerprint")
SIDECAR_STAMPS = ("manifest_sha256", "generated_at")
STORE_STAMPS = ("manifest_generated_at", "exported_at")
SITE_FONTS = tuple(
    f"site/AbbotsMortonSpaceport{family}-{weight}.otf"
    for family in ("Mono", "SansJunior", "SansSenior")
    for weight in ("Regular", "Bold")
)
FONTS = (
    "engine/out/build/Senior-Regular.otf",
    "engine/out/review/fonts/before.otf",
    "engine/out/review/fonts/after.otf",
    *SITE_FONTS,
)
BUILD_OUT = Path("engine/out/build")
REVIEW = Path("engine/out/review")
AUTOSAVE = Path("verdicts-autosave.json")
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
    return manifest_document_digest(json.loads(path.read_bytes()))


def manifest_document_digest(document: dict) -> str:
    document = json.loads(json.dumps(document))
    for key in MANIFEST_STAMPS:
        document.pop(key, None)
    for font in (document.get("fonts") or {}).values():
        if isinstance(font, dict):
            font.pop("sha256", None)
    return sha256_bytes(canonical(document))


def gz_digest(path: Path) -> str:
    """Digest a gzip file's decompressed stream, re-serializing a JSON header line that carries `format` without the keys `SIDECAR_STAMPS` names, so the gzip member's own header and the stamps never count."""
    with gzip.open(path, "rb") as handle:
        first = handle.readline()
        return stream_digest(first, iter(lambda: handle.read(_CHUNK), b""))


def stream_digest(first: bytes, rest: Iterable[bytes]) -> str:
    """Digest a decompressed stream as `gz_digest` does: its first line re-serialized without the keys `SIDECAR_STAMPS` names when it is a JSON header carrying `format`, then the rest as it is."""
    digest = hashlib.sha256()
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
    for chunk in rest:
        digest.update(chunk)
    return digest.hexdigest()


def corpus_file_digest(corpus: Path, path: Path, font_digest: Callable[[Path], str]) -> str:
    rel = path.relative_to(corpus).as_posix()
    if rel == "manifest.json":
        return manifest_digest(path)
    if rel.startswith("fonts/") and path.suffix == ".otf":
        return font_digest(path)
    if path.suffix == ".gz":
        return gz_digest(path)
    return file_sha256(path)


def corpus_digests(corpus: Path, font_digest: Callable[[Path], str]) -> dict:
    """Return the corpus block for the review corpus at `corpus`, file by file (`corpus_file_digest`)."""
    files: dict[str, str] = {}
    if corpus.is_dir():
        for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
            files[path.relative_to(corpus).as_posix()] = corpus_file_digest(corpus, path, font_digest)
    lines = "\n".join(f"{rel}\t{digest}" for rel, digest in sorted(files.items()))
    return {"digest": sha256_bytes(lines.encode()), "files": files}


def _without_at(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_at(item) for key, item in value.items() if key != "at"}
    if isinstance(value, list):
        return [_without_at(item) for item in value]
    return value


def store_digest(path: Path) -> str | None:
    """Digest a verdict store's projection: its format, the sorted (unit, verdict, note) of its records and the sorted unit ids it cleared, without the keys `STORE_STAMPS` names and without any record's `at`. The `cleared` list holds `{unit, at}` entries, or bare unit ids in an older file, and keeps a duplicate as often as the file holds it. The store holds the verdicts made since the last accept, so it is parsed whole. None for a missing store."""
    if not path.is_file():
        return None
    document = json.loads(path.read_bytes())
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


class DigestMismatch(Exception):
    """Raised when a configuration's table digest, recomputed from its materialized rows, is not the crate's digest its windows head records."""


def table_configs(build_out: Path) -> dict[str, bool]:
    """Return each configuration with windows rows under `build_out`, mapped to whether it has a windows head: every `windows-<config>.tsv.gz.head`, and every `windows-<config>.tsv.gz` a tree from before the segment format holds without one."""
    prefix = "windows-"
    found: dict[str, bool] = {}
    for suffix, headed in ((".tsv.gz.head", True), (".tsv.gz", False)):
        for path in build_out.glob(f"{prefix}*{suffix}"):
            found.setdefault(path.name[len(prefix) : -len(suffix)], headed)
    return dict(sorted(found.items()))


def table_digests(build_out: Path, table: ModuleType) -> dict[str, str]:
    """Return `table.table_digest` per configuration with windows rows (`table_configs`), through the tree's own readers, one configuration at a time: from the materialized form, every part checked, where the configuration has a windows head, and raising `DigestMismatch` naming it when the digest is not the one its head records; from the single file otherwise. Where the tree's `table` has `materialized_table_digest`, a configuration with a head is hashed as its rows stream, and no `Window` row is built; a tree without it, and a configuration with no head, build one decision table at a time."""
    streamed = getattr(table, "materialized_table_digest", None)
    digests: dict[str, str] = {}
    for config, headed in table_configs(build_out).items():
        joins = table.read_join_tsv(build_out / f"joins-{config}.tsv")
        recorded = (
            table.windows_head(build_out, config).stamps.get(table.TABLE_DIGEST_STAMP) if headed else None
        )
        if headed and streamed is not None:
            digests[config] = streamed(build_out, config, joins)
        else:
            if headed:
                _, decision = table.read_materialized_windows(build_out, config, sha256=True)
            else:
                _, decision = table.read_windows(table.windows_path(build_out, config))
            digests[config] = table.table_digest(decision, joins)
            del decision
        del joins
        if headed and digests[config] != recorded:
            raise DigestMismatch(
                f"{config}: the table digest of its materialized rows, {digests[config]}, is not the crate's {recorded} its windows head records"
            )
    return digests


def fingerprint_tree(root: Path, *, table: ModuleType, font_digest: Callable[[Path], str]) -> dict:
    return {
        "format": FORMAT,
        "tables": table_digests(root / BUILD_OUT, table),
        "fonts": {rel: font_digest(root / rel) if (root / rel).is_file() else None for rel in FONTS},
        "corpus": corpus_digests(root / REVIEW, font_digest),
        "verdicts": store_digest(root / AUTOSAVE),
    }


def tree_modules(root: Path) -> tuple[ModuleType, ModuleType]:
    """Import `engine.pipeline.table` and `engine.pipeline.fingerprint` from `root`, which goes first on `sys.path`."""
    sys.path.insert(0, str(root))
    return importlib.import_module("engine.pipeline.table"), importlib.import_module(
        "engine.pipeline.fingerprint"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fingerprint one tree's outputs for the acceptance bar's same-output reading."
    )
    parser.add_argument("--root", type=Path, required=True, help="the tree under test (a scratch clone)")
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the ams-perf-fingerprint/1 JSON"
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    table, fingerprint = tree_modules(root)
    try:
        document = fingerprint_tree(root, table=table, font_digest=fingerprint.font_content_digest)
    except DigestMismatch as error:
        print(f"perf_fingerprint: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
