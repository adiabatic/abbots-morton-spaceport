"""The review surface build's persisted per-unit cache. It keys each unit's phase-1 and phase-2 products by a content key over the unit's inputs, so after an edit the build re-enriches only the windows the edit can affect and serves every other unit from the previous surface's shards.

A unit's products are its ink diffs and machine-approval flags, its enrichment (cells, seams, highlights, explain, provenance), and its three drafts. A machine-approved or verdict-exempt unit is written as a slim fragment (`audit.slim_fragment`), which omits the highlight, the explain, and the drafts. The products are a pure function of the unit's inputs, and the cache is correct only if the content key covers all of those inputs.

The key has two levels. The per-unit key (`UnitKeyer.key`) covers the unit's audit rows, which fix the window, its configs, the kinds, the matched ledger class, and both fonts' rendered names, plus one digest per window family (`family_content_keys`). A family digest combines the family's explain-aware rune digest, the rune digests of its static `resolve.against` closure, and a digest of the after font's compiled glyphs for the family (outlines, advances, and cursive anchors), so a drawing or anchor change invalidates a unit even when no name in its rows changes. A ligature family is included when all of its components are in the window.

The whole-store stamp (`environment_stamp`) covers everything that can change a unit's products without changing a family digest: the code the surface build runs (`surface_code_paths`), the non-rune data files, the kernel's settlement mode flags, the resolved spec structure and capability-feature set (cross-rune routes: predicate-class and group memberships, ligature sequences, and the formation guard's feature combinations), the before and Junior fonts outside their `head` and `name` tables (`_font_digest`), the acceptance configs' subset tables, the draft harness (test/test_shaping.py, tools/*.py, postscript_glyph_names.yaml) and the site corpus files it validates pins against (`drafts.CORPUS_FILES`), and the after font's non-family glyphs, cmap, and GPOS wiring. `surface_code_paths` is the review modules the build imports, the pipeline and validation modules they reach, and the crate modules the `settle-cases` and `guard-sweep` subcommands run. rebuild/test_review_code_closure.py checks these lists against the import graph. So an edit to the pipeline driver, a gate, the oracle or its row cache, the conformance sweep, the GSUB emitter, the pixel geometry, the font compile, or the crate's enumeration and fold leaves the store valid.

Three inputs are outside the whole-store stamp. The after font's GSUB wiring is outside every stamp; `fingerprint.after_font_glyph_digests` explains why a window's glyph selection is covered without it. The divergence ledger reaches the shards only through the audit's `matched_entry` column, which is in the rows, and through fields the build recomputes and patches on every pass (`no_verdict`, `exemplar`, class promotion), so a ledger edit invalidates only the units whose rows it changed. The refuse `why` text the explain panel quotes is in the family keys instead, so rewording one re-enriches only the windows that contain that family.

A store record holds the address of the previous build's fragment: the shard part, byte offset, and length the shard writer returned when it wrote the fragment, so the plan does not parse the previous surface to find it. The record also holds what the parent's global reduces need: the machine flags and ink deltas, the verdict family, the judged pair, the ink-diff digest for echo grouping, the seam-home projection and per-seam rects, and the unit's mismatch lines. It records whether the fragment was written slim, because the shape depends on the exemption, a ledger fact outside the key. It also records the values the fragment was written with that come from outside the key: its echo group, its class after family promotion, the ledger's exemplar and exemption flags, its secondary-seam homes, and the rune file its policy draft names. So a unit that moves into the human workload on a ledger edit (`no_verdict` changes) is a miss and is re-enriched in full instead of served as the slim fragment it had before, and a unit that moves the other way is a miss too. This keeps a served surface byte-identical to a from-scratch one.

Every field derived from the ledger or the reduces (echo, class, `no_verdict`, `exemplar`, the secondary-seam homes) is recomputed over all units on every build, and a unit's id depends only on its content key. A served fragment whose recomputed fields all equal the values its record says it was written with is copied into the new surface by address as bytes, without parsing (`unit_store.UnitStore.served_as_is`; `PriorFragmentReader.read_bytes` checks the record's id and stamp as substrings of the bytes). The shard writer leaves a part in place when every fragment in it is copied this way. A served fragment with a changed field is parsed once, patched, and serialized again, the same way a fresh fragment is read from the build's own spool, so a cache hit never keeps a stale global field. The cluster id is the one global field taken from the served record, because its inputs (configs, final class, ink diffs) are all covered by the key.

`stream_store` parses a store line only if the workload names its key, into a `ServedUnit`, and yields the records one at a time in store order. The plan folds each into the packed unit store (`rebuild/review/unit_store.py`) and releases it, so for the rest of the build the parent holds a served unit only as columns there. If the store stops reading after some records were yielded, the stream raises `StoreUnreadable`, and the plan discards what it folded and runs a full build. An error the fold itself raises propagates unchanged. `load_store` collects the same stream into a dict.

rebuild/test_unit_cache.py::test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit checks that an incrementally rebuilt surface matches a from-scratch build byte for byte, and `test_no_change_rebuild_serves_every_unit_and_is_byte_stable` checks that the served path rewrites no shard part.

Both stores record their whole-store stamp in two forms: the hex digest that `stream_store` and `load_signature_store` compare, and the `fingerprint.EnvironmentStamp` lines the digest is computed from, with the code label's per-file lines beside them. When a load rejects a store, `store_miss_note` and `signature_miss_note` use these to report which of four causes applied: no store, a store that does not read, a manifest that changed, or a stamp line that changed. For a code line they name the changed file inside the closure (`surface_code: rebuild/review/ink.py (changed)`) instead of the closure's digest. The note is only as specific as the lines: `after_helpers` is one digest over the after font's non-family glyphs, cmap, and layout wiring, so a change there is not narrowed further. A rehearsal pass writes its surface to another directory, so a live pass after one compares against the last live store's lines and reports everything that changed since that store was written, code changes included.

This module also defines the carry content key (`carry_projection`, `carry_content_hash`), so the build and the carry share one definition. The build stamps each fragment's `content_key` with it when it drafts the fragment, and the unit's id is derived from that stamp (`unit_id_for`), so rebuild/tools/carry_verdicts.py moves each prior verdict to the unit with the same id. The stamp is excluded from the projection it hashes.

Beside the per-unit store is the ink-signature store, which does for the ink-duplicate merge what the unit store does for enrichment. The merge needs one rendered-outcome signature per (window, config) over every relabel-split window. It runs before the set of units exists, so the unit store cannot serve these signatures. Each entry's key (`UnitKeyer.signature_key`) follows the same two-level argument as the unit key: the audit row fixes the window, the config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, and the per-family digests fix the after font's outlines, advances, and cursive anchors for every family the window can touch. The whole-store stamp (`signature_environment`) covers what signatures depend on beyond that: the comparator's code (`signature_code_paths`: rebuild/review/ink.py and the three rebuild/validation modules it reaches, which rebuild/test_review_code_closure.py checks by walking the import graph from `rebuild.review.ink` in both directions), the before font outside its `head` and `name` tables, and the after font's non-family glyphs, cmap, and GPOS wiring. The rest of `surface_code_paths` is left out (the build driver, this module, the enricher, the drafts, the kernel interface, the crate), because computing a signature runs none of it. The ledger, the subset tables, the Junior font, the corpus, and the draft harness are left out because signatures read none of them. So this store stays valid through edits that invalidate the unit store, and a build that re-enriches every unit can still skip re-shaping for the merge.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Container, Iterable, Iterator, Mapping, Protocol

from rebuild.pipeline import fingerprint, kernel_exec, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import unit_index
from rebuild.review.audit import ACCEPTANCE_CONFIGS, RowColumns, UnitTable, parse_codepoints
from rebuild.review.drafts import CORPUS_FILES

STORE_FORMAT = "ams-review-unit-cache/5"
STORE_NAME = "unit-cache.ndjson.gz"
SIGNATURE_STORE_FORMAT = "ams-review-ink-signatures/2"
SIGNATURE_STORE_NAME = "ink-signatures.tsv.gz"

# The fields the carry projection leaves out; rebuild/test_carry_verdicts.py checks them. `no_verdict`, `exemplar`, `echo`, and `cluster` come from the ledger or the reduces. `id` is the projection's own digest and `content_key` is the stamp of this projection, so neither can be an input to it. `batch` is excluded so a fragment from an older surface that still carries one hashes the same. `explain`, `drafts`, `provenance`, and `secondary_seams` are derived presentation, whose content is already covered by the window and both fonts' glyphs, cells, and seams. `ink_deltas` is the same delta identity stored per config. `picture_identical` follows from the window and both fonts' placed glyphs, which the projection already covers, so excluding it changes nothing the key distinguishes, and including it would change the id of every unit whose flag changed and strand their verdicts. `ink_identical` and `junior_equivalent` are derived flags inside the projection. Every fragment carries both, so removing either would change every unit id. The projection includes `highlight`, which a slim fragment (`audit.slim_fragment`) omits, so a slim fragment's stamp differs from the one its full fragment would have. That strands nothing, because slim units take no verdicts.
CARRY_PRESENTATION_KEYS = frozenset(
    {
        "id",
        "batch",
        "no_verdict",
        "exemplar",
        "explain",
        "drafts",
        "provenance",
        "secondary_seams",
        "echo",
        "cluster",
        "ink_deltas",
        "picture_identical",
        "content_key",
    }
)


def carry_projection(unit: Mapping) -> str:
    """Return the input of the carry content key: the unit's fields outside `CARRY_PRESENTATION_KEYS` as sorted-key JSON. A unit's id is derived from this projection's digest, so changing the serialization or the exclusion set changes every unit id and strands every recorded verdict."""
    return json.dumps(
        {key: value for key, value in unit.items() if key not in CARRY_PRESENTATION_KEYS},
        sort_keys=True,
    )


def carry_content_hash(unit: Mapping) -> str:
    return hashlib.sha256(carry_projection(unit).encode()).hexdigest()


# Bitcoin's base58 alphabet: the digits and both cases minus 0, O, I and l, which are easily confused. Case is significant, which keeps ids short; ids are copied and pasted, not transcribed.
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# 58**10 < 2**64 <= 58**11, so eleven symbols hold any 64-bit value and the width never varies.
ID_SYMBOLS = 11
_ID_PATTERN = re.compile(rf"^[ue]-[{BASE58_ALPHABET}]{{{ID_SYMBOLS}}}$")


def base58_64(digest_hex: str) -> str:
    """Return the first 64 bits of a hex digest as `ID_SYMBOLS` base58 symbols, most significant first, padded with the zero symbol `1`. The width is fixed and the alphabet is in ascending ASCII order, so the strings sort in the same order as the numbers. Case is significant."""
    value = int(digest_hex[:16], 16)
    symbols = []
    for _ in range(ID_SYMBOLS):
        value, remainder = divmod(value, 58)
        symbols.append(BASE58_ALPHABET[remainder])
    return "".join(reversed(symbols))


def unit_id_for(content_key: str) -> str:
    """Return a unit's id: `u-` followed by the first 64 bits of its carry content key in base58, for example `u-3mJ7kPq2Xw9`. The content key is the sha256 of `carry_projection`, the stamp every m1-audit fragment carries. Two units with the same projection have the same id. The id does not depend on the unit's position in the queue or on which other units exist, so a served fragment keeps its bytes across builds and a verdict follows its unit across surfaces."""
    return "u-" + base58_64(content_key)


def echo_id_for(key_repr: str) -> str:
    """Return an echo group's id: `e-` followed by the same encoding of the sha256 of the group's key (the configs, the judged pair's codepoints, the final class, and the ink-diff digest, as `repr` renders them). A group has the same id on every surface it appears on, whatever other groups exist."""
    return "e-" + base58_64(hashlib.sha256(key_repr.encode()).hexdigest())


def is_content_id(value: object) -> bool:
    """Return whether `value` is a unit or echo id: the prefix, then exactly `ID_SYMBOLS` base58 symbols."""
    return isinstance(value, str) and _ID_PATTERN.match(value) is not None


def store_path(out_dir: Path) -> Path:
    return Path(out_dir) / STORE_NAME


def _sha256_file(path: Path) -> str:
    try:
        return fingerprint.file_sha256(Path(path))
    except OSError:
        return "missing"


def _font_digest(path: Path) -> str:
    """Return a site font's line in either whole-store stamp: `fingerprint.font_content_digest`, which hashes every table except `head` and `name`, or `_sha256_file`'s sentinel for a missing font. Neither dropped table affects a unit's products or an ink signature: every glyph the before font renders is drawn from `CFF ` and placed by `hmtx` and `GPOS`, and nothing that shapes reads the version strings or revision. So the `make all` a version bump runs leaves both stores valid, while a widened glyph or a moved anchor still invalidates them."""
    try:
        return fingerprint.font_content_digest(Path(path))
    except OSError:
        return "missing"


def _manifest_stamp(out_dir: Path) -> str:
    """Return the manifest's identity digest for the unit store's stamp, or the sentinel `missing` when there is no manifest to read, as on a first build. With the sentinel, `write_store`, `stream_store`, and `store_miss_note` treat a missing manifest as a stamp mismatch instead of raising."""
    try:
        return unit_index.manifest_sha256(Path(out_dir))
    except OSError:
        return "missing"


# The rebuild/pipeline modules the surface build never imports, such as the driver, its gates, the oracle, the conformance sweep, the emitters, and the font compile. rebuild/test_review_code_closure.py checks this set against build.py's import closure in both directions. The build reads the vocabulary it shares with the sweep (the alphabet, a configuration's features, a formed stream's labels, the boundary names, and the alias map) from `labels.py`, which imports none of these modules. This is an exclusion list, so a new pipeline module is hashed into `surface_code_paths` until the test shows the build never reaches it and it is added here.
PIPELINE_NON_SURFACE_MODULES = frozenset(
    {
        "compile_font.py",
        "conform.py",
        "coretext_smoke.py",
        "defects.py",
        "emit_gpos.py",
        "emit_gsub.py",
        "geometry.py",
        "ligature_outgoing_check.py",
        "manual_pins.py",
        "oracle.py",
        "oracle_cache.py",
        "oracle_positions.py",
        "pack_gsub.py",
        "readback.py",
        "run_m1.py",
        "surface.py",
        "witness.py",
    }
)

# The crate modules the surface build never runs. The build calls the crate only through the `settle-cases` and `guard-sweep` subcommands, and every module their handlers in main.rs reach stays hashed, with main.rs and lib.rs. rebuild/test_review_code_closure.py checks this exclusion list against the `crate::` references those two handlers reach outside the crate's test modules.
KERNEL_NON_SURFACE_MODULES = frozenset(
    {
        "artifacts.rs",
        "census.rs",
        "certificate.rs",
        "fanout.rs",
        "fiber.rs",
        "fixpoint.rs",
        "fold.rs",
        "liveness.rs",
        "options.rs",
        "replay.rs",
        "rulefold.rs",
        "sha256.rs",
        "shipped_order.rs",
        "stream.rs",
    }
)


def surface_code_paths(repo_root: Path) -> list[Path]:
    """Return the code whose edit invalidates the per-unit store: the code the surface build runs. The review side is `fingerprint.review_code_paths`, which is checked against build.py's import graph. The pipeline side is rebuild/pipeline minus `PIPELINE_NON_SURFACE_MODULES`, plus all of rebuild/validation, which the build reaches. The crate side is rebuild/kernel-rs/src minus `KERNEL_NON_SURFACE_MODULES`, plus Cargo.toml and Cargo.lock, since the crate's dependencies and build profile affect every subcommand. The ink-signature store uses the narrower `signature_code_paths`, so an edit here outside the comparator's closure re-enriches units and re-shapes nothing.

    The lists work at module granularity, which over-invalidates in the safe direction: a module imported for something the build never calls is still hashed. The served-versus-recomputed sample in every build checks that a served fragment equals a fresh computation. Two sets of modules are left out. The fan-out width and telemetry modules under rebuild/tools cannot change a unit's products: rebuild/test_unit_cache.py's serial-versus-parallel byte identity covers the width, and rebuild/test_review_code_closure.py checks that these are the only modules the build reaches outside the three trees. The font-compile tools are never run by the build, and the draft harness line hashes tools/*.py anyway.
    """
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    pipeline = [
        path
        for path in sorted((root / "rebuild" / "pipeline").glob("*.py"))
        if path.name not in PIPELINE_NON_SURFACE_MODULES
    ]
    validation = sorted((root / "rebuild" / "validation").glob("*.py"))
    crate = [kernel / "Cargo.toml", kernel / "Cargo.lock"] + [
        path for path in sorted((kernel / "src").rglob("*.rs")) if path.name not in KERNEL_NON_SURFACE_MODULES
    ]
    return pipeline + validation + crate + fingerprint.review_code_paths(root)


# The comparator's import closure: rebuild/review/ink.py and the rebuild/validation modules it reaches (the shaper, the seam classifier it takes `PIXEL_SIZE` from, and the row model the shaper imports). It is an inclusion list of literal paths because the walk from ink.py reaches only these files and no package `__init__.py`. rebuild/test_review_code_closure.py walks the import graph from `rebuild.review.ink` in both directions and checks that each entry exists, because `fingerprint.path_lines` skips a missing path without error, so a renamed comparator module would silently drop out of the hash.
SIGNATURE_CODE_MODULES = (
    "rebuild/review/ink.py",
    "rebuild/validation/classify.py",
    "rebuild/validation/rowmodel.py",
    "rebuild/validation/shaping.py",
)


def signature_code_paths(repo_root: Path) -> list[Path]:
    """Return the code whose edit invalidates the ink-signature store: the comparator's import closure (`SIGNATURE_CODE_MODULES`), a subset of `surface_code_paths`. A signature is `InkComparator.signature` over a `Shaper` and the fontTools outline pens, and nothing else the surface build runs can change one, so an edit elsewhere re-enriches units and re-shapes nothing. The row model is included because the shaper imports it, although no signature reads a row.

    Some inputs are outside this hash. `build.signature_text` turns a window's codepoints into the text a signature is taken over, in two steps. Its parse, `audit.parse_codepoints`, round-trips with `audit.format_codepoints` (rebuild/test_review_audit.py::test_parse_codepoints), and a mismatch between them makes the build's `ink_sig` raise KeyError on its first lookup. Its `chr` join defines what text a signature covers, so a change to it changes what a signature means. The join is versioned by `SIGNATURE_STORE_FORMAT`, as are this module's `signature_key` layout and the store's file format. A change to the key layout alone cannot serve a wrong digest, because every row gets a new key and misses. A change to the join without a format bump would, so rebuild/test_review_audit.py::test_the_signature_text_is_pinned_beside_the_store_format checks `build.signature_text`'s output and the format as one literal pair, and fails on a join change until the format changes too. The uharfbuzz and fontTools versions are in no stamp, for this store or the unit store.
    """
    root = Path(repo_root)
    return [root.joinpath(*relative.split("/")) for relative in SIGNATURE_CODE_MODULES]


def environment_stamp(
    repo_root: Path,
    spec: ResolvedSpec,
    subset_dir: Path,
    before_font: Path,
    junior_font: Path,
    after_helpers_digest: str,
    subset_digests: Mapping[str, str] | None = None,
) -> fingerprint.EnvironmentStamp:
    """Return the unit store's whole-store stamp. A change to any of its lines invalidates the whole store, and over-invalidation is the safe direction. The code line is `surface_code_paths`, so a pipeline or crate edit outside the code the build runs leaves the store valid. The two site fonts are hashed through `_font_digest`, which ignores `head` and `name`, so the fonts a version bump rebuilds leave it valid too. The rune files are left out because the family keys cover them per unit, and the divergence ledger is left out for the reason the module docstring gives. The stamp carries the code closure's per-file lines as its `surface_code` detail, from the same `path_lines` read the code digest is computed from, so the file a miss note names is the file the digest saw. `subset_digests` is each acceptance configuration's subset table, already hashed by the caller; the build hashes them once for this line and for the subset pack's header. When it is None, the tables are hashed here."""
    root = Path(repo_root)
    code_lines = fingerprint.path_lines(root, surface_code_paths(root))
    runes = set(fingerprint.rune_paths(root))
    ledger = root / "rebuild" / "m1-divergences.yaml"
    data_lines = sorted(
        f"{path.name}\t{_sha256_file(path)}"
        for path in fingerprint.data_paths(root)
        if path.is_file() and path not in runes and path != ledger
    )
    harness_paths = [root / "test" / "test_shaping.py", root / "postscript_glyph_names.yaml"]
    harness_paths += sorted((root / "tools").glob("*.py"))
    if subset_digests is None:
        subset_digests = {
            config: _sha256_file(Path(subset_dir) / f"baseline-{config}.subset.tsv.gz")
            for config in ACCEPTANCE_CONFIGS
        }
    lines = [
        f"format\t{STORE_FORMAT}",
        f"surface_code\t{fingerprint.digest_lines(code_lines)}",
        "data\t" + hashlib.sha256("\n".join(data_lines).encode()).hexdigest(),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        f"before_font\t{_font_digest(Path(before_font))}",
        f"junior_font\t{_font_digest(Path(junior_font))}",
        "subsets\t" + " ".join(f"{config}={subset_digests[config]}" for config in ACCEPTANCE_CONFIGS),
        "corpus\t" + " ".join(f"{name}={_sha256_file(root / name)}" for name in CORPUS_FILES),
        f"draft_harness\t{fingerprint.hash_paths(root, harness_paths)}",
        f"after_helpers\t{after_helpers_digest}",
    ]
    return fingerprint.EnvironmentStamp(lines=tuple(lines), detail=(("surface_code", tuple(code_lines)),))


def family_content_keys(repo_root: Path, spec: ResolvedSpec, after_font: Path) -> tuple[dict[str, str], str]:
    """Return each family's content key, for bare letters and ligature runes alike, and the after font's helpers digest for the whole-store stamps. A family's key combines its explain-aware rune digest (prose-blind except for the refuse `why` the explain text quotes), the rune digests of its static `resolve.against` closure, which is the one way its records read another rune file directly, and the after font's compiled-glyph digest for the family."""
    digests = fingerprint.rune_explain_digests(Path(repo_root))
    closure = spec_load.rune_closure(spec)
    glyph_digests, helpers = fingerprint.after_font_glyph_digests(after_font)
    keys: dict[str, str] = {}
    for name in sorted(set(digests) | set(glyph_digests)):
        reach = sorted({name} | set(closure.get(name, frozenset())))
        lines = [f"{member}\t{digests.get(member, '-')}" for member in reach]
        lines.append(f"glyphs\t{glyph_digests.get(name, '-')}")
        keys[name] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return keys, helpers


class SignatureRow(Protocol):
    """The fields `UnitKeyer.signature_key` reads from a row: the four fields of `audit.AuditRow` and `audit.RowView` that determine the placed ink, declared as a protocol so either class can be passed."""

    @property
    def config(self) -> str: ...
    @property
    def codepoints(self) -> str: ...
    @property
    def baseline(self) -> tuple[str, ...]: ...
    @property
    def new(self) -> tuple[str, ...]: ...


class UnitKeyer:
    """Computes per-unit content keys from the family keys. It memoizes the families to cite per distinct set of window letters, because many windows share a letter set and the ligature scan need not repeat per unit."""

    def __init__(self, family_keys: Mapping[str, str], family_of: Mapping[int, str]) -> None:
        self._family_keys = dict(family_keys)
        self._family_of = dict(family_of)
        self._relevant: dict[frozenset[str], tuple[str, ...]] = {}

    def _relevant_families(self, families: frozenset[str]) -> tuple[str, ...]:
        cached = self._relevant.get(families)
        if cached is None:
            cached = tuple(
                name
                for name in sorted(self._family_keys)
                if all(component in families for component in name.split("_"))
            )
            self._relevant[families] = cached
        return cached

    def key(self, table: UnitTable, rows: RowColumns, ordinal: int) -> str:
        """Return the unit's store key over its inputs, the `key` field of its `CachedUnit` (distinct from the carry `content_key` stamp on its fragment): the sha256 of its audit rows, each as the audit's own line without its newline, in the unit's row order, followed by one line per family the key cites with that family's content key (`family_content_keys`). The unit is the table's row at `ordinal`. `RowColumns.line` rebuilds each audit line byte for byte from the unit's window and the row's own rendered names, so a unit that absorbed other rows in the ink-duplicate merge is keyed over those rows under their own names."""
        values = table.codepoints(ordinal)
        families = frozenset(self._family_of[value] for value in values if value in self._family_of)
        line = rows.line
        codepoints = table.codepoints_text(ordinal)
        start = table.rows_start(ordinal)
        lines = [line(index, codepoints) for index in range(start, start + table.row_count(ordinal))]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()

    def signature_key(self, row: SignatureRow) -> str:
        """Return one ink-signature store entry's key. It covers the audit row's window, config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, plus the same per-family digests the unit key cites. It leaves out `kinds` and `matched_entry`, which classify the row but do not affect the shaped ink, so a ledger edit does not re-shape a window. Unlike the unit key, it is cut to sixteen hex characters: the store holds on the order of a million entries, and full 64-character keys were most of its bytes. At a million entries the chance of a 64-bit collision is about one in forty million, and a collision would at worst give one window's sibling group the signature of another window."""
        families = frozenset(
            self._family_of[value] for value in parse_codepoints(row.codepoints) if value in self._family_of
        )
        lines = ["\t".join((row.config, row.codepoints, "|".join(row.baseline), "|".join(row.new)))]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


@dataclass
class CachedUnit:
    """One store record as `write_store` serializes it; `ServedUnit` is the same record as `stream_store` parses it. It holds what the build needs to fetch the unit's previous fragment from the prior shards, plus the projection the parent's global reduces read. `content_key` is the fragment's own stamp, which differs from `key`, the content key over the unit's inputs. A prior fragment is served only when the stamp on disk equals `content_key`, which shows the fetched bytes are the ones this record describes. `slim` records the fragment's shape (`audit.slim_fragment`), and the build serves the fragment only when that is the shape it would write for the unit now.

    `address` is the shard part (as the manifest names it), byte offset, and length the shard writer returned when it wrote the fragment, the same `(part, start, length)` the app's sidecars use for a Range fetch. It is never a copy of the stamp: `PriorFragmentReader` checks the bytes at the address against the stamp when the write reads them back. A record with no address, from an older store or in a part whose size `stream_store` found changed, is located by `locate_prior_fragments`, which reads the address off the part's text.

    `echo`, `exemplar`, `no_verdict`, and `homes` are the values `build.patch_fragment` wrote into the fragment from the reduces and the ledger, and `policy_file` is the one drafts field the cross-unit check reads. `unit_store.UnitStore.served_as_is` compares the first four and the class with this build's values. When all are equal, the fragment's bytes on disk are already what this build would write, and it is copied by address; a shard part made up only of such fragments is left in place. Otherwise the fragment is read, patched, and serialized again.
    """

    key: str
    prior_id: str
    prior_class: str
    content_key: str
    slim: bool
    address: tuple[str, int, int] | None
    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: dict[str, str]
    diffs_digest: str
    cluster: str
    family: str
    pair_codepoints: tuple[int, int] | None
    proj: dict
    seams: list[dict]
    mismatches: list[str]
    echo: str | None
    exemplar: bool
    no_verdict: bool
    homes: list[list]
    policy_file: str | None

    def to_record(self) -> dict:
        return {
            "key": self.key,
            "id": self.prior_id,
            "class": self.prior_class,
            "content_key": self.content_key,
            "slim": self.slim,
            "ink_identical": self.ink_identical,
            "picture_identical": self.picture_identical,
            "junior_equivalent": self.junior_equivalent,
            "ink_deltas": self.ink_deltas,
            "diffs_digest": self.diffs_digest,
            "cluster": self.cluster,
            "family": self.family,
            "pair_codepoints": list(self.pair_codepoints) if self.pair_codepoints else None,
            "proj": self.proj,
            "seams": self.seams,
            "mismatches": self.mismatches,
            "echo": self.echo,
            "exemplar": self.exemplar,
            "no_verdict": self.no_verdict,
            "homes": self.homes,
            "policy_file": self.policy_file,
            "address": list(self.address) if self.address else None,
        }


@dataclass(frozen=True, slots=True)
class ServedUnit:
    """One store record as `stream_store` parses it. The plan folds each record into the packed unit store (`unit_store.UnitStore.fold_served`) as the stream yields it and then releases it, so the parent holds one at a time; only a record without an address is buffered until the walk places it. The projection the secondary-home reduce reads is stored as tuples (`pair`, the two span tuples, the three name tuples, and `seam_pairs`), which `_served_unit` pools so that records held together share one instance per distinct value. `seam_rects` is `CachedUnit.seams` unchanged, in the shape `patch_fragment` reads. The class uses slots because a caller holding the whole store holds one instance per line."""

    key: str
    prior_id: str
    prior_class: str
    content_key: str
    slim: bool
    address: tuple[str, int, int] | None
    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: dict[str, str]
    diffs_digest: str
    cluster: str
    family: str
    pair_codepoints: tuple[int, int] | None
    echo: str | None
    exemplar: bool
    no_verdict: bool
    homes: list[list]
    policy_file: str | None
    seam_rects: list[dict]
    mismatches: list[str]
    pair: tuple[int, int] | None
    after_spans: tuple[tuple[int, int], ...]
    after_cells: tuple[str, ...]
    after_seams: tuple[str, ...]
    before_spans: tuple[tuple[int, int], ...]
    before_glyphs: tuple[str, ...]
    before_seams: tuple[str, ...]
    seam_pairs: tuple[tuple[int, int], ...]

    def located(self) -> "PriorFragment | None":
        """Return the record's address as a `PriorFragment` stamped with the record's `content_key` and marked verbatim, meaning the shard writer wrote those bytes and they may be copied unchanged, or None when the walk has to locate the record."""
        if self.address is None:
            return None
        part, start, length = self.address
        return PriorFragment(part, start, length, self.prior_id, self.content_key, verbatim=True)


_KEY_PREFIX = b'{"key": "'


def _served_unit(record: dict, trusted: Container[str], pool: dict, whole: bool) -> ServedUnit:
    """Build the `ServedUnit` for one parsed store line. Strings that repeat across units (the class, the cluster and diff digests, the config names and delta digests, the address's part, and the projection's glyph names, cell names, and seam tokens) are interned with `sys.intern`, the table `audit.load_audit` describes. The projection's tuples are pooled through `pool` when `whole` is true (the caller holds the whole store) or when the record has no address (a streaming caller buffers those for the walk). An addressed record in a stream is folded and released before the next line is parsed, so it pools only within itself, and a table shared across the stream holds nothing for the records already yielded. The address is kept only when its part is in `trusted`."""
    address = record.get("address")
    address = (
        (sys.intern(address[0]), int(address[1]), int(address[2]))
        if address and address[0] in trusted
        else None
    )
    table = pool if whole or address is None else {}

    def pooled(value):
        return table.setdefault(value, value)

    def names(values) -> tuple[str, ...]:
        return pooled(tuple(sys.intern(value) for value in values))

    def spans(values) -> tuple[tuple[int, int], ...]:
        return pooled(tuple(pooled((span[0], span[1])) for span in values))

    pair = record["pair_codepoints"]
    proj = record["proj"]
    seams = record["seams"]
    return ServedUnit(
        key=record["key"],
        prior_id=record["id"],
        prior_class=sys.intern(record["class"]),
        content_key=record["content_key"],
        slim=record["slim"],
        address=address,
        ink_identical=record["ink_identical"],
        picture_identical=record["picture_identical"],
        junior_equivalent=record["junior_equivalent"],
        ink_deltas={sys.intern(config): sys.intern(delta) for config, delta in record["ink_deltas"].items()},
        diffs_digest=sys.intern(record["diffs_digest"]),
        cluster=sys.intern(record["cluster"]),
        family=sys.intern(record["family"]),
        pair_codepoints=(pair[0], pair[1]) if pair else None,
        echo=record["echo"],
        exemplar=record["exemplar"],
        no_verdict=record["no_verdict"],
        homes=[[home, bool(suppressed)] for home, suppressed in record["homes"]],
        policy_file=record["policy_file"],
        seam_rects=seams,
        mismatches=list(record["mismatches"]),
        pair=pooled((proj["pair"][0], proj["pair"][1])) if proj["pair"] else None,
        after_spans=spans(proj["after_spans"]),
        after_cells=names(proj["after_cells"]),
        after_seams=names(proj["after_seams"]),
        before_spans=spans(proj["before_spans"]),
        before_glyphs=names(proj["before_glyphs"]),
        before_seams=names(proj["before_seams"]),
        seam_pairs=spans(seam["pair"] for seam in seams),
    )


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _part_sizes(out_dir: Path, parts: Iterable[str]) -> dict[str, int | None]:
    return {part: _file_size(Path(out_dir) / part) for part in sorted(set(parts))}


def record_line(record: CachedUnit) -> bytes:
    """Return one store record as its line in the file. A line from the previous store and the record the build's unit store produces for the same unit (`unit_store.UnitStore.cached_unit`) serialize to the same bytes, because every field, including the projection read back through the store's columns and `seam_home_record`, round-trips through JSON in the order and form it was written. So when this build would write a record unchanged, it copies the previous line through a `StoreCursor` instead of building the record again. rebuild/test_unit_cache.py and rebuild/test_unit_store.py check this: the projection round trip for one record, and the no-change and incremental rebuilds down to the store's bytes."""
    return (json.dumps(record.to_record()) + "\n").encode()


class StoreCursor(unit_index.LineCursor):
    """A forward-only reader over the previous build's store that returns the line for a requested input key: a `unit_index.LineCursor` keyed on each record's leading `key` field. The build reads it in triage order while it writes the new store, copying the line of each unit whose record would be unchanged and building the rest."""

    def __init__(self, out_dir: Path) -> None:
        super().__init__(store_path(out_dir), field="key")


def _stamp_value(environment: fingerprint.EnvironmentStamp | str) -> str:
    return environment if isinstance(environment, str) else environment.value


def _stamp_header(environment: fingerprint.EnvironmentStamp | str) -> dict:
    """Return the header fields for a stamp: `environment`, the hex a load compares, and for an `EnvironmentStamp`, also `environment_lines` (its labeled lines) and `environment_detail` (the lines behind each detailed label), which the miss notes read. A stamp passed as a hex string records only the hex."""
    fields: dict = {"environment": _stamp_value(environment)}
    if not isinstance(environment, str):
        fields["environment_lines"] = list(environment.lines)
        fields["environment_detail"] = {label: list(lines) for label, lines in environment.detail}
    return fields


def write_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    records: Iterable[CachedUnit | bytes],
    parts: Iterable[str] | None = None,
) -> None:
    """Write the unit store after the manifest, stamped with the manifest's identity (`unit_index.manifest_sha256`, which leaves out the copied UI assets' component). The stamp shows the store describes the shards beside it, and an assets refresh that rewrites only that component leaves the store current. A crash between the manifest write and this one leaves a stamp mismatch, and the next build falls back to a full pass.

    The header also records the byte size of every shard part the records' addresses point into: `parts` when the caller names them, otherwise the parts the `CachedUnit` records address. The manifest stamp says nothing about the shards' bytes, so `stream_store` compares each part's current size with the recorded one, at one stat per part, and drops the addresses into a part whose size changed. Equal sizes do not prove the addresses are still right, since a rewrite can swap two fragments of equal length; `PriorFragmentReader` catches that case by checking the id and stamp at each address. A record is either a `CachedUnit` to serialize or the line a previous store already holds for it (`record_line` makes the two identical). The file is written under a sibling `.partial` name and renamed at the end, so the previous store stays readable through a `StoreCursor` until the new one is complete. The gzip mtime is fixed so consecutive identical builds write identical bytes. Compression is level 1: the file is written once and read once per build, and level 9 spent four more seconds per cycle to save ten megabytes.
    """
    if parts is None:
        records = list(records)
        parts = (record.address[0] for record in records if isinstance(record, CachedUnit) and record.address)
    header = {
        "format": STORE_FORMAT,
        **_stamp_header(environment),
        "manifest_sha256": _manifest_stamp(out_dir),
        "parts": _part_sizes(out_dir, parts),
    }
    path = store_path(out_dir)
    staging = path.with_name(path.name + ".partial")
    try:
        with open(staging, "wb") as handle:
            with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
                stream.write((json.dumps(header) + "\n").encode())
                for record in records:
                    stream.write(record if isinstance(record, bytes) else record_line(record))
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


class StoreUnreadable(Exception):
    """Raised by the generator `stream_store` returns when the store stops reading after its header did: a truncated or corrupt gzip member, a line that does not parse, or a record missing a field. The records already yielded came from a store that can no longer be trusted, so a caller that folded them as they came must discard what it folded. `load_store` catches it and returns None."""


def stream_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    wanted: Container[str] | None = None,
    pool: dict | None = None,
) -> Iterator[ServedUnit] | None:
    """Return the prior build's records one at a time in store order, or None when there is no usable store: absent, unreadable at the header, a different format or environment stamp, or stamped for a manifest other than the one on disk. A None costs a full build, which is the safe direction. The header is read before returning, so None is decided before any record is parsed. The body is a generator over the same file handle, which it closes after the last record or when a read fails. A body that fails after some records raises `StoreUnreadable` from the generator, and a caller that folded each record as it came must then discard what it folded. An error the caller's own fold raises is raised in the caller's frame and is never treated as a store failure.

    Each line's key is sliced from the start of the raw line before the line is parsed: `to_record` writes the key first, and a copied line keeps that order. A line whose key cannot be sliced is skipped, with or without `wanted`, which is the same safe direction. With `wanted`, only records whose key it contains are parsed, so a line the workload does not name costs a prefix comparison and no parse.

    Each record is a `ServedUnit` with its strings interned and its projection tuples pooled (`_served_unit`). Passing `pool` means the caller holds the records together, so every record pools through it. Without it, only the records that have no address share a table, since a streaming caller buffers those for the walk; an addressed record is folded and released before the next is parsed and pools only within itself, so nothing in the stream grows with the records already yielded. A record keeps its address only while its part has the size the header recorded. A record whose part changed size, or that has no address, comes with `address` None and is placed by the walk, so a shard rewritten under the store is still served instead of failing at the write.
    """
    path = store_path(out_dir)
    if not path.is_file():
        return None
    try:
        stream = gzip.open(path, "rb")
    except OSError:
        return None
    try:
        header = json.loads(next(stream))
        if header.get("format") != STORE_FORMAT or header.get("environment") != _stamp_value(environment):
            stream.close()
            return None
        if header.get("manifest_sha256") != _manifest_stamp(out_dir):
            stream.close()
            return None
        recorded = header.get("parts") or {}
        trusted = {
            part
            for part, size in _part_sizes(out_dir, recorded).items()
            if size is not None and size == recorded[part]
        }
    except OSError, EOFError, ValueError, KeyError, TypeError, StopIteration:
        stream.close()
        return None
    return _records(stream, trusted, wanted, pool)


def _records(
    stream: gzip.GzipFile, trusted: Container[str], wanted: Container[str] | None, pool: dict | None
) -> Iterator[ServedUnit]:
    whole = pool is not None
    table: dict = {} if pool is None else pool
    start = len(_KEY_PREFIX)
    try:
        with stream:
            for line in stream:
                if not line.startswith(_KEY_PREFIX):
                    continue
                end = line.find(b'"', start)
                if end < 0:
                    continue
                if wanted is not None and line[start:end].decode() not in wanted:
                    continue
                yield _served_unit(json.loads(line), trusted, table, whole)
    except (OSError, EOFError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise StoreUnreadable(f"the store stopped reading: {exc!r}") from exc


def load_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    wanted: Container[str] | None = None,
    pool: dict | None = None,
) -> dict[str, ServedUnit] | None:
    """Return the prior build's records keyed by input key, or None when there is no usable store. It is `stream_store` collected into a dict, except that a body that fails partway also returns None. The key-slice, `wanted`, interning, and part-size rules are `stream_store`'s. The records are held together here, so all of them pool through `pool`, or through a table made for the call. The build's plan reads the stream directly; this function is for callers, such as tests, that want the records in hand."""
    records = stream_store(out_dir, environment, wanted=wanted, pool={} if pool is None else pool)
    if records is None:
        return None
    try:
        return {cached.key: cached for cached in records}
    except StoreUnreadable:
        return None


def signature_store_path(out_dir: Path) -> Path:
    return Path(out_dir) / SIGNATURE_STORE_NAME


def signature_environment(
    repo_root: Path, before_font: Path, after_helpers_digest: str
) -> fingerprint.EnvironmentStamp:
    """Return the ink-signature store's whole-store stamp: what a signature reads that the per-entry keys do not cover. That is the comparator's code (`signature_code_paths`, the import closure of `rebuild.review.ink`), the before font outside its `head` and `name` tables (`_font_digest`), and the after font's non-family glyphs, cmap, and layout wiring. The code line covers only the comparator's closure, so an edit to the build driver, the census, the drafts, the kernel interface, or the crate leaves this store valid while `environment_stamp` invalidates the unit store. The module docstring lists the rest of what is left out. The comparator closure's per-file lines are recorded as the `comparator_code` detail, from the same read the digest is computed from."""
    root = Path(repo_root)
    code_lines = fingerprint.path_lines(root, signature_code_paths(root))
    lines = (
        f"format\t{SIGNATURE_STORE_FORMAT}",
        f"comparator_code\t{fingerprint.digest_lines(code_lines)}",
        f"before_font\t{_font_digest(Path(before_font))}",
        f"after_helpers\t{after_helpers_digest}",
    )
    return fingerprint.EnvironmentStamp(lines=lines, detail=(("comparator_code", tuple(code_lines)),))


def write_signature_store(
    out_dir: Path, environment: fingerprint.EnvironmentStamp | str, entries: Mapping[str, str]
) -> None:
    """Write one JSON header line, then one `key\\tdigest` line per entry, sorted by key. The fixed gzip mtime and the sort make consecutive builds of the same inputs byte-identical. The store is rewritten on each build with only the entries the merge needed, so entries for windows that no longer occur are dropped. Compression is level 1, like the unit store: the lines are hex digests, and level 9 spent four seconds to save well under one percent. The file is written under a sibling `.partial` name and renamed at the end, so a build killed during the write leaves the previous store intact. A truncated gzip would read as absent in `load_signature_store` and cost the next pass a full re-shaping. The gzip header records the final file name, since `GzipFile` would otherwise write the staging handle's name into the bytes."""
    header = {"format": SIGNATURE_STORE_FORMAT, **_stamp_header(environment)}
    path = signature_store_path(out_dir)
    staging = path.with_name(path.name + ".partial")
    try:
        with open(staging, "wb") as handle:
            with gzip.GzipFile(
                filename=path.name, fileobj=handle, mode="wb", mtime=0, compresslevel=1
            ) as stream:
                stream.write((json.dumps(header) + "\n").encode())
                for key in sorted(entries):
                    stream.write(f"{key}\t{entries[key]}\n".encode())
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def load_signature_store(
    out_dir: Path, environment: fingerprint.EnvironmentStamp | str
) -> dict[str, str] | None:
    """Return the prior build's signature digests keyed by content key, or None when there is no usable store: absent, unreadable, or a different format or environment stamp. A None costs one re-shaping pass, so over-invalidation is the safe direction here too."""
    path = signature_store_path(out_dir)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if header.get("format") != SIGNATURE_STORE_FORMAT or header.get("environment") != _stamp_value(
                environment
            ):
                return None
            entries: dict[str, str] = {}
            for line in stream:
                key, digest = line.rstrip("\n").split("\t")
                entries[key] = digest
            return entries
    except OSError, EOFError, ValueError, KeyError, TypeError, StopIteration:
        return None


CODE_FILES_SHOWN = 4
NO_STORE_NOTE = "no store on disk"
UNREADABLE_NOTE = "the store will not read"
MANIFEST_MOVED_NOTE = "the manifest it describes moved"


def read_header(path: Path) -> dict | None:
    """Return a store's header line, or None when none can be read. Both stores start with one JSON header line. A caller uses it to report what changed after a load has rejected the store."""
    try:
        with gzip.open(Path(path), "rb") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration:
        return None
    return header if isinstance(header, dict) else None


def _recorded_labels(lines: object) -> dict[str, str] | None:
    if not isinstance(lines, list):
        return None
    labels: dict[str, str] = {}
    for line in lines:
        label, _, digest = str(line).partition("\t")
        labels[label] = digest
    return labels


def _miss_note(
    path: Path,
    environment: fingerprint.EnvironmentStamp,
    code_label: str,
    manifest_stamp: str | None = None,
) -> str | None:
    """Return why a load of the store at `path` under `environment` fails, as one line, checking in the loader's order: no file, a header that does not read, a changed stamp, and, for the unit store, a manifest identity other than the recorded one. A changed stamp is reported through `fingerprint.moved_note`, with the code label expanded to the files that changed inside its closure, at most `CODE_FILES_SHOWN` of them plus a count of the rest. Returns None when the header matches everything checked, which leaves only one reason for the loader to fail: a body that does not parse. A store whose header has the hex but no `environment_lines` reports that the stamp moved and names no label."""
    if not path.is_file():
        return NO_STORE_NOTE
    header = read_header(path)
    if header is None:
        return UNREADABLE_NOTE
    if header.get("environment") != environment.value:
        recorded = _recorded_labels(header.get("environment_lines"))
        if recorded is None:
            return "the stamp moved"
        detail = header.get("environment_detail")
        recorded_code = _recorded_labels(detail.get(code_label)) if isinstance(detail, dict) else None
        expand = (
            {}
            if recorded_code is None
            else {
                code_label: fingerprint.moved_note(
                    recorded_code, environment.detail_labels(code_label), limit=CODE_FILES_SHOWN
                )
            }
        )
        moved = fingerprint.moved_note(recorded, environment.labels, expand=expand)
        return f"the stamp moved at {moved}" if moved else "the stamp moved"
    if manifest_stamp is not None and header.get("manifest_sha256") != manifest_stamp:
        return MANIFEST_MOVED_NOTE
    return None


def store_miss_note(out_dir: Path, environment: fingerprint.EnvironmentStamp) -> str | None:
    """Return why `stream_store` rejects the unit store under `out_dir`, or None when its header matches `environment` and the manifest beside it (`_miss_note`)."""
    return _miss_note(store_path(out_dir), environment, "surface_code", _manifest_stamp(out_dir))


def signature_miss_note(out_dir: Path, environment: fingerprint.EnvironmentStamp) -> str | None:
    """Return why `load_signature_store` rejects the ink-signature store under `out_dir`, or None when its header matches `environment` (`_miss_note`)."""
    return _miss_note(signature_store_path(out_dir), environment, "comparator_code")


@dataclass(frozen=True, slots=True)
class PriorFragment:
    """The location and stamp of one of the prior surface's fragments: the shard part it was written to (as the manifest names it), the byte offset and length of its JSON element there, its unit id, and its `content_key`. The address is the same `(part, start, length)` the app's sidecars use for a Range fetch. It comes either from the store record (`ServedUnit.located`), which recorded what the shard writer returned, or from `locate_prior_fragments`, which reads it off the part's text for a record without one and so stays correct for a hand-edited shard as long as the part is still ASCII. `verbatim` marks the first case: bytes at a store address have the shard writer's framing and may be copied into the next surface unchanged, while bytes the walk found may be any JSON and are parsed, patched, and serialized again."""

    part: str
    start: int
    length: int
    unit_id: str
    content_key: str | None
    verbatim: bool = False


_JSON_WHITESPACE = re.compile(r"[ \t\n\r]*")


def _skip_whitespace(text: str, index: int) -> int:
    match = _JSON_WHITESPACE.match(text, index)
    return match.end() if match else index


def _walk_elements(text: str):
    """Yield each element of one shard part with the character offset and length of its text. It reads the `[`, then calls `raw_decode` once per element, skipping the commas and whitespace between them, so each fragment is parsed and released before the next and the part is never held as objects all at once. It does not depend on the framing `_write_shard` writes; a compact `json.dumps` of the same list is read the same way. A part that is not a JSON array raises ValueError, which the caller treats as unreadable."""
    decoder = json.JSONDecoder()
    index = _skip_whitespace(text, 0)
    if index >= len(text) or text[index] != "[":
        raise ValueError("a shard part is a JSON array")
    index = _skip_whitespace(text, index + 1)
    while index < len(text) and text[index] != "]":
        fragment, end = decoder.raw_decode(text, index)
        yield index, end - index, fragment
        index = _skip_whitespace(text, end)
        if index < len(text) and text[index] == ",":
            index = _skip_whitespace(text, index + 1)


def locate_prior_fragments(out_dir: Path, wanted: Mapping[str, set[str]]) -> dict[str, PriorFragment]:
    """Return where the prior shards hold the given prior unit ids (`wanted` maps class id to ids), keyed by prior id, with the stamp each fragment carries. It reads only the parts the prior manifest lists for those classes, parsing one fragment at a time and keeping only its address, so the build never holds the previous surface's units. The plan uses this only as a fallback: a store record carries the address the shard writer returned, and the walk is needed only for the records `stream_store` yielded without one, from an older store or in a part whose size changed. On a surface this code wrote there are none. The manifest is needed because a class split into several parts has no single file name to guess. A missing or unreadable manifest or part contributes nothing, and its units are computed fresh. So does a part that is not pure ASCII: the address is a character offset read back as a byte offset, which are equal only under `ensure_ascii`, and every part this build writes is ASCII."""
    out_dir = Path(out_dir)
    try:
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        by_id = {meta.get("id"): meta for meta in manifest["classes"]}
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return {}
    located: dict[str, PriorFragment] = {}
    for class_id, ids in wanted.items():
        meta = by_id.get(class_id)
        if meta is None:
            continue
        try:
            parts = unit_index.class_shards(meta)
        except KeyError:
            continue
        for part in parts:
            try:
                raw = (out_dir / part).read_bytes()
                if not raw.isascii():
                    continue
                text = raw.decode("ascii")
                del raw
                for start, length, fragment in _walk_elements(text):
                    unit_id = fragment.get("id") if isinstance(fragment, dict) else None
                    if unit_id in ids:
                        located[unit_id] = PriorFragment(
                            part, start, length, unit_id, fragment.get("content_key")
                        )
            except OSError, ValueError:
                continue
    return located


class PriorFragmentReader:
    """Reads served fragments from the prior shards by address, from the store record or from the walk, keeping one part open at a time. The build reads them in shard order, so the file handle changes once per part, not once per unit. Each read is checked against the address it came from: the element must still be the unit with the stamp the plan served it under. Otherwise the file changed under this build, and the read raises ValueError. For a store-addressed fragment, this is the only time its bytes are checked against its record."""

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = Path(out_dir)
        self._part: str | None = None
        self._handle: BinaryIO | None = None

    def _refusal(self, located: PriorFragment) -> ValueError:
        return ValueError(
            f"{located.part} no longer holds unit {located.unit_id} at bytes "
            f"{located.start}+{located.length}: the prior surface changed underneath this build"
        )

    def _bytes(self, located: PriorFragment) -> bytes:
        if self._handle is None or self._part != located.part:
            self.close()
            self._handle = (self._out_dir / located.part).open("rb")
            self._part = located.part
        self._handle.seek(located.start)
        return self._handle.read(located.length)

    def read(self, located: PriorFragment) -> dict:
        fragment = json.loads(self._bytes(located))
        if (
            not isinstance(fragment, dict)
            or fragment.get("id") != located.unit_id
            or fragment.get("content_key") != located.content_key
        ):
            raise self._refusal(located)
        return fragment

    def read_bytes(self, located: PriorFragment) -> bytes:
        """Return the fragment's bytes without parsing them, checked against the address. The shard writer's framing puts each field on its own line as `"key": value`, so the id and stamp the address was recorded with are found as substrings. This catches a fragment edited in place at its address, which the part-size check cannot see, as `read` does. Only a verbatim address is checked this way, because only the shard writer's framing is known; a walked address is read with `read`."""
        body = self._bytes(located)
        if located.verbatim and (
            f'"id": {json.dumps(located.unit_id)}'.encode() not in body
            or f'"content_key": {json.dumps(located.content_key)}'.encode() not in body
        ):
            raise self._refusal(located)
        return body

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
            self._part = None

    def __enter__(self) -> "PriorFragmentReader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
