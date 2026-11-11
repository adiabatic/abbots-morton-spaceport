"""The output fingerprint #618's acceptance bar compares on the live tree, read before a change's first build and again after its landing pass: every configuration's table digest, the compiled and site fonts, the review corpus without its stamps, the verdict store with its accepted list and the complaint list after the pass, the two corpus stores apart, and the review-facts pins the pass rewrote.

Run it with the tree under test's own interpreter and code, never the harness's:

  uv run --directory <tree> python -P <main>/rebuild/tools/perf_fingerprint.py --root <tree> --out <file>

`-P` keeps this script's directory off `sys.path`, and the script puts `--root` first on it before importing, so `rebuild.pipeline.table`, `rebuild.pipeline.fingerprint` and, for a corpus read in its materialized form, `rebuild.review.build` come from the tree under test and a change to an intermediate encoding is read by its own reader. Its only other imports are from the standard library; it never imports a harness module.

The output is one `ams-perf-fingerprint/1` document:

- `tables`: for each configuration with a windows head under `rebuild/out/m1` (`windows-<config>.tsv.gz.head`), and for a tree from before the segment format each `windows-<config>.tsv.gz` present, `table.table_digest` over its decision table and `joins-<config>.tsv`, read one configuration at a time through the tree's own readers, so a missing configuration shows up as a difference. A configuration with a head is read in its materialized form (`table.read_materialized_windows`): its head file checked against its head, every part's size and sha256 checked against it too, and the base and segments merged into the whole payload. The digest recomputed from those rows must equal the crate's own, which the head records (`table.TABLE_DIGEST_STAMP`), and the script exits nonzero naming the configuration when it does not, so a fault in the merge cannot hide behind a digest the crate reported of itself.
- `fonts`: `fingerprint.font_content_digest` of `rebuild/out/m1/M1.otf`, the corpus's `fonts/{before,after}.otf` and the six site fonts, null for a font that is missing.
- `corpus`: one digest per file under `rebuild/out/review/` but the two stores, and a digest over the sorted `path<TAB>digest` lines. `manifest.json` and `review-facts.json` are digested as canonical JSON without the keys `MANIFEST_STAMPS` names (for the facts file, inside its `corpus` block), and without each manifest font's raw `sha256`; a gzip sidecar as its decompressed stream, with a JSON header line re-serialized without the keys `SIDECAR_STAMPS` names; a corpus font by its content digest; every other file raw.
- `stores`: the same header projection applied to the stores `STORES` names. A difference there is an intermediate format differing, which a report names apart.
- `verdicts`: the store after the pass, projected to its `format`, the sorted `(unit, verdict, note)` of its records and the sorted unit ids it cleared, its override section's among them, as one store, without the keys `STORE_STAMPS` names and without any record's `at`, since the fills stamp `at` with the corpus stamp.
- `accepted`: the raw digest of the accepted list beside the store, `rebuild/accepted-units.txt`, null when there is none. Every reader of the store reads the units the list names as accepted and no pass writes it, so a pass that did shows up here and not in `verdicts`.
- `complaints`: the complaint list as canonical JSON without the keys `COMPLAINT_STAMPS` names and without any `at` inside `groups`.
- `review_facts_pins`: the raw digest of `rebuild/review-facts-pins.json` as the pass rewrote it.

A corpus whose unit store has a head (`unit-cache.ndjson.gz.head`, #618's change 28) is read in its materialized form (decision 48, the tree's `build.MaterializedCorpus`): each class's live fragments laid into the parts a from-scratch build writes, every fragment plain, a deflated one (change 35) inflated and laid out at its inflated length, the manifest's `shards` lists, the app sidecars' rows and their locator blocks in that layout, without a deflated fragment's `"deflated": true`, and the unit store's head, base and segments merged into the single file a whole write gives, every address three elements, under the key `unit-cache.ndjson.gz`, every store part checked against its head's size and sha256. The store's head, base and segment files are left out of the `corpus` block. So a build that appends a changed fragment, deflated, and a later fold, which deflates, move no digest.

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
from typing import Any

FORMAT = "ams-perf-fingerprint/1"
MANIFEST_STAMPS = ("generated_at", "repo_head", "inputs_fingerprint")
# The ink-signature store's header also carries the audit's identity and the family keys' digest (`unit_cache.AUDIT_IDENTITY_KEY`, `unit_cache.FAMILY_KEYS_KEY`), which say which audit and family keys it was written over, so a pass that rewrites the store with only those moved shows no `stores` difference. The script imports nothing of the tree under test before `--root` is on its path, so the two names are restated.
SIDECAR_STAMPS = (
    "manifest_sha256",
    "generated_at",
    "environment",
    "environment_lines",
    "environment_detail",
    "audit_identity",
    "family_keys_digest",
)
STORE_STAMPS = ("manifest_generated_at", "exported_at")
COMPLAINT_STAMPS = ("manifest_generated_at", "since")
STORES = ("unit-cache.ndjson.gz", "ink-signatures.tsv.gz")
STORE = STORES[0]
HEAD = f"{STORE}.head"
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
ACCEPTED = Path("rebuild/accepted-units.txt")
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
    return manifest_document_digest(json.loads(path.read_bytes()))


def manifest_document_digest(document: dict) -> str:
    document = json.loads(json.dumps(document))
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


def _header_line(header: dict) -> bytes:
    return (json.dumps(header) + "\n").encode()


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


def corpus_digests(
    corpus: Path, font_digest: Callable[[Path], str], materialize: Callable[[Path], Any] | None = None
) -> tuple[dict, dict[str, str | None]]:
    """Return the corpus block and the stores block for the review corpus at `corpus`, read in its materialized form through `materialize` (the tree's `build.MaterializedCorpus`) when its unit store has a head."""
    files: dict[str, str] = {}
    stores: dict[str, str | None] = {name: None for name in STORES}
    if materialize is not None and (corpus / HEAD).is_file():
        _materialized_digests(corpus, font_digest, materialize, files, stores)
    elif corpus.is_dir():
        for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
            rel = path.relative_to(corpus).as_posix()
            if rel in STORES:
                stores[rel] = gz_digest(path)
            else:
                files[rel] = corpus_file_digest(corpus, path, font_digest)
    lines = "\n".join(f"{rel}\t{digest}" for rel, digest in sorted(files.items()))
    return {"digest": sha256_bytes(lines.encode()), "files": files}, stores


def _materialized_digests(
    corpus: Path,
    font_digest: Callable[[Path], str],
    materialize: Callable[[Path], Any],
    files: dict[str, str],
    stores: dict[str, str | None],
) -> None:
    """Fill the corpus and stores blocks from the corpus's materialized form (the module docstring). A corpus that already is its materialized form keeps its own files' digests, read as the plain form reads them."""
    with materialize(corpus) as form:
        for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
            rel = path.relative_to(corpus).as_posix()
            if rel in STORES:
                stores[rel] = gz_digest(path)
            elif not form.is_layout_file(rel) or (
                form.identical and rel != STORE and not rel.startswith(STORE)
            ):
                files[rel] = corpus_file_digest(corpus, path, font_digest)
        if not form.identical:
            for name, chunks in form.part_files():
                digest = hashlib.sha256()
                for chunk in chunks:
                    digest.update(chunk)
                files[name] = digest.hexdigest()
            files["manifest.json"] = manifest_document_digest(form.manifest)
            index, app, locator, rows_name = form.SIDECARS
            files[index] = stream_digest(_header_line(form.sidecar_header(index)), form.index_lines())
            files[app] = stream_digest(_header_line(form.sidecar_header(app)), form.app_lines())
            rows, blocks, machine = form.locator()
            files[rows_name] = sha256_bytes(gzip.decompress(rows))
            table = {
                **form.sidecar_header(locator),
                "units": machine,
                "blocks": len(blocks),
                "rows_bytes": len(rows),
            }
            files[locator] = stream_digest(
                _header_line(table),
                ((json.dumps(block, ensure_ascii=False) + "\n").encode() for block in blocks),
            )
        store = form.store()
        if store is not None:
            header, lines = store
            stores[STORE] = stream_digest(_header_line(header), lines)


def _without_at(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_at(item) for key, item in value.items() if key != "at"}
    if isinstance(value, list):
        return [_without_at(item) for item in value]
    return value


def store_digest(path: Path) -> str | None:
    """Digest a verdict store's projection: its format, the sorted (unit, verdict, note) of its records and the sorted unit ids it cleared, those of its override section (`overrides`, the records and tombstones on accepted units) listed with its own, so a store with no section keeps the digest it had before the section existed. The `cleared` list holds `{unit, at}` entries, or bare unit ids in an older file."""
    if not path.is_file():
        return None
    document = json.loads(path.read_bytes())
    for key in STORE_STAMPS:
        document.pop(key, None)
    section = document.get("overrides")
    parts = [document, section] if isinstance(section, dict) else [document]
    records = sorted(
        (str(record.get("unit")), record.get("verdict"), record.get("note") or "")
        for part in parts
        for record in part.get("verdicts") or []
    )
    cleared = sorted(
        str(entry.get("unit")) if isinstance(entry, dict) else str(entry)
        for part in parts
        for entry in part.get("cleared") or []
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


class DigestMismatch(Exception):
    """Raised when a configuration's table digest, recomputed from its materialized rows, is not the crate's digest its windows head records."""


def table_configs(m1: Path) -> dict[str, bool]:
    """Return each configuration with windows rows under `m1`, mapped to whether it has a windows head: every `windows-<config>.tsv.gz.head`, and every `windows-<config>.tsv.gz` a tree from before the segment format holds without one."""
    prefix = "windows-"
    found: dict[str, bool] = {}
    for suffix, headed in ((".tsv.gz.head", True), (".tsv.gz", False)):
        for path in m1.glob(f"{prefix}*{suffix}"):
            found.setdefault(path.name[len(prefix) : -len(suffix)], headed)
    return dict(sorted(found.items()))


def table_digests(m1: Path, table: ModuleType) -> dict[str, str]:
    """Return `table.table_digest` per configuration with windows rows (`table_configs`), through the tree's own readers, one configuration at a time so only one decision table is held: from the materialized form, every part checked, where the configuration has a windows head, and raising `DigestMismatch` naming it when the digest is not the one its head records; from the single file otherwise."""
    digests: dict[str, str] = {}
    for config, headed in table_configs(m1).items():
        joins = table.read_join_tsv(m1 / f"joins-{config}.tsv")
        if headed:
            _, decision = table.read_materialized_windows(m1, config, sha256=True)
            recorded = table.windows_head(m1, config).stamps.get(table.TABLE_DIGEST_STAMP)
        else:
            _, decision = table.read_windows(table.windows_path(m1, config))
            recorded = None
        digests[config] = table.table_digest(decision, joins)
        del decision, joins
        if headed and digests[config] != recorded:
            raise DigestMismatch(
                f"{config}: the table digest of its materialized rows, {digests[config]}, is not the crate's {recorded} its windows head records"
            )
    return digests


def fingerprint_tree(
    root: Path,
    *,
    table: ModuleType,
    font_digest: Callable[[Path], str],
    materialize: Callable[[Path], Any] | None = None,
) -> dict:
    corpus, stores = corpus_digests(root / REVIEW, font_digest, materialize)
    pins = root / FACTS_PINS
    return {
        "format": FORMAT,
        "tables": table_digests(root / M1, table),
        "fonts": {rel: font_digest(root / rel) if (root / rel).is_file() else None for rel in FONTS},
        "corpus": corpus,
        "verdicts": store_digest(root / AUTOSAVE),
        "accepted": file_sha256(root / ACCEPTED) if (root / ACCEPTED).is_file() else None,
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


def tree_materialize(corpus: Path) -> Any:
    """Return the tree's `build.MaterializedCorpus` over `corpus`, importing `rebuild.review.build` from the tree first on `sys.path` only when a corpus needs it."""
    return importlib.import_module("rebuild.review.build").MaterializedCorpus(corpus)


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
        document = fingerprint_tree(
            root, table=table, font_digest=fingerprint.font_content_digest, materialize=tree_materialize
        )
    except DigestMismatch as error:
        print(f"perf_fingerprint: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
