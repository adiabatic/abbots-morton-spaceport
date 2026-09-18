"""The persisted per-unit surface cache (issue 20): the review build's phase-1/phase-2 products carried across builds, keyed by per-unit content keys, so a one-rune edit re-enriches the windows that could feel it and serves everything else from the previous surface's shards.

A unit's expensive products — the ink diffs and machine-approval flags, the enrichment (cells, seams, highlights, explain, provenance), and the three drafts, of which a machine-approved or verdict-exempt unit's fragment carries only the first half (`audit.slim_fragment`: it omits the highlight, the explain and the drafts outright, since nothing under them reaches a reviewer) — are a pure function of a nameable closure, and the cache's soundness is exactly the claim that the content key covers that closure. The key is two-grained: per unit, the audit rows (which pin the window, its configs, both fonts' rendered names, and the matched ledger classes) plus a per-family digest for every window letter — the family's explain-aware rune digest expanded by its static `resolve.against` closure, joined with a digest of the after font's compiled glyphs for that family (outlines, advances, and cursive anchors, so a drawing or anchor change invalidates even when no name in the rows moves) — with ligature families included whenever all their components appear in the window. Whole store, everything that can move a unit's products without moving a named family: the code the surface build runs (`surface_code_paths` — the review modules the build imports, the pipeline and validation modules those reach, and the crate modules the `settle-cases` and `guard-sweep` verbs run, a walked closure rebuild/test_review_code_closure.py holds the rosters to, so an edit to the driver, a gate, the oracle, the conformance sweep, the oracle's row cache, the GSUB emitter, the pixel geometry, the font compile or the crate's enumeration and fold keeps the store), the non-rune data files, the engine's semantics flags, the resolved spec structure and capability-feature universe (cross-rune routes: predicate-class and group memberships, ligature sequences, the formation guard's feature combos), the before and Junior fonts table by table outside `head` and `name` (`_font_digest`, so the `make all` a version bump runs keeps the store), the acceptance configs' subset tables, the draft harness (test/test_shaping.py, tools/, postscript_glyph_names.yaml) and the three site corpus files it validates pins against, and the after font's non-family glyphs, cmap, and GPOS wiring. What is deliberately outside every stamp is the after font's GSUB wiring; `fingerprint.after_font_glyph_digests` carries the argument for why a window's glyph selection is covered without it. The divergence ledger is deliberately not in the store stamp: its per-unit effects reach the shards only through the audit's matched_entry column (in the rows) or through fields the build re-derives and re-patches on every pass (no_verdict, exemplar, class promotion), so a ledger edit invalidates exactly the units whose rows it moved. The refuse prose the explain panel quotes is deliberately not in the store stamp either, for the same reason it is in the family keys: rewording one re-enriches the windows holding that family and leaves every other unit served.

What the store serves is the previous build's emitted fragment (read back from the shard it lives in, at the address the record carries — the part, byte offset and length the shard writer handed back as it wrote the fragment, so the plan trusts an address rather than parsing the previous surface to find one) plus the slim projection the parent's global reduces need: the machine flags and ink deltas, the verdict family, the judged pair, the ink-diff digest for echo grouping, the seam-home projection and per-seam rects, and the unit's mismatch lines — whether the fragment was written slim, because the shape a build writes turns on the exemption, a ledger fact outside the key — and the fields the fragment was written with from outside the key: its echo group, its class after family promotion, the ledger's exemplar and exemption flags, its secondary-seam homes, and the rune file its policy draft names. So a unit that crosses from machine-approved-or-exempt into the human workload on a ledger edit (no_verdict flipping) is a miss and is re-enriched in full rather than served the slim fragment it earned before the edit, and one crossing the other way is a miss too, so a served surface stays byte-identical to a from-scratch one. Everything ledger-derived or reduce-derived — echo, class, no_verdict, exemplar, the secondary-seam homes — is recomputed over the full universe every build; a unit's id is its content key's and moves with nothing else. A served fragment every one of whose recomputed fields equals what the store says it was written with is copied into the new surface by address as bytes, never parsed (`unit_store.UnitStore.served_as_is`; `PriorFragmentReader.read_bytes` holds the bytes to the record's id and stamp as substrings), and the shard writer leaves a part whose every fragment lands that way where it lies; one with a moved field is parsed once, patched and serialized again, exactly as a fresh fragment is read out of the build's own spool, so no cache hit ever freezes a global field. The cluster id alone is trusted from the served record, because its inputs (configs, final class, ink diffs) are all under the key. `stream_store` parses a store line once, and only a line whose key the workload names, into a `ServedUnit`, handed over one at a time in store order, which the plan folds into the packed unit store (`rebuild/review/unit_store.py`) the moment it is parsed and releases, so what the parent holds of a served unit for the rest of the build is its columns there; a store that stops reading after handing some records over raises `StoreUnreadable` from the stream, and the plan then discards what it folded and runs a full build, while an error the fold itself raises is the plan's and propagates as it is. `load_store` is the same stream gathered into a dict, for a caller that wants the records in hand. The byte-identity gate (rebuild/test_unit_cache.py::test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit) is the standing proof: an incrementally rebuilt live surface must match a from-scratch build byte for byte, and the no-change rebuild beside it proves the served path writes no shard part at all.

Both stores record their whole-store stamp twice over: as the hex `stream_store` and `load_signature_store` compare, and as the `fingerprint.EnvironmentStamp` lines it folds, with the code label's per-file lines beside them, so that when a load declines a store `store_miss_note` and `signature_miss_note` can say which of the four ways it missed — no store, a store that will not read, a manifest that moved, or a stamp line that moved — and for a code line name the file inside the closure (`surface_code: rebuild/review/ink.py (changed)`) rather than the closure's digest. The note is as fine as the lines are: `after_helpers` is one digest over the after font's non-family glyphs, cmap and layout wiring and a move there is named no closer, and a rehearsal pass writes its surface elsewhere, so a live pass after one reads the last live store's lines and reports whatever moved since that store was written, code moves included.

This module also owns the carry content key (the render identity rebuild/tools/carry_verdicts.py resolves prior verdicts against), so the build can stamp each unit's `content_key` at emission time and carry can probe stamped hashes instead of re-serializing every unit — one definition, shared by both sides, with the stamp itself excluded from the projection it hashes.

Beside the per-unit store lives the ink-signature store (issue 18), which does for the ink-duplicate merge what the unit store does for enrichment: the merge needs one rendered-outcome signature per (window, config) over every relabel-split window — the one per-unit product computed before the unit universe exists, so the unit store can never serve it — and re-shaping those serially was the load phase's floor. Each entry's key follows the unit key's two-grained soundness argument exactly: the audit row pins the window, the config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, and the per-family digests pin the after font's outlines, advances, and cursive anchors for every family the window can touch. The whole-store stamp carries what signatures depend on beyond that: the comparator's own code (`signature_code_paths` — rebuild/review/ink.py and the three rebuild/validation modules it imports, the import closure rebuild/test_review_code_closure.py walks from `rebuild.review.ink` in both directions) and the before font outside its `head` and `name` tables, plus the after font's non-family glyphs, cmap, and GPOS wiring. Deliberately absent: the rest of `surface_code_paths` — the build driver, this cache, the enricher, the drafts, the kernel seam, the crate, none of which a signature executes — and the ledger, the subsets, the Junior font, the corpus, and the draft harness. Signatures read none of them, so this store survives edits that drop the unit store: a build that re-enriches everything can still skip re-shaping the merge.
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

# The carry identity's non-participating fields (rebuild/test_carry_verdicts.py holds the contract): no_verdict, exemplar, echo, and cluster are ledger- or reduce-derived, and id — the projection's own digest, so it cannot feed itself — and batch, which no fragment carries, are excluded so a surface that still carries them hashes the same; explain, drafts, provenance, and secondary_seams are derived presentation whose adjudicable content is already covered by the window plus both fonts' glyphs, cells, and seams; ink_deltas is the same delta identity persisted per config; content_key is the stamp of this very projection and must not feed itself. The highlight is inside the projection, and a slim fragment (`audit.slim_fragment`) omits it, so a slim fragment's stamp is over what it carries and is not the stamp the same window's full fragment would have borne — which strands nothing, because the units written slim are the ones that take no verdict, and the exclusions here are what keep every human unit's stamp where it was. picture_identical is a pure function of the window and both fonts' placed glyphs, which the projection already covers through codepoints, configs, and both sides' glyphs, cells, and seams, so excluding it changes nothing the key says — while including it would restamp every unit whose flag flips the day the channel lands and strand the verdicts recorded against them; ink_identical is the one derived flag inside the key, kept there only as the byte-identity contract with every prior snapshot.
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
    """The carry content key as recorded historically: the unit's non-presentation fields as sorted-key JSON. This is a byte-identity contract with every verdict on record: a unit's id is this projection's digest, so changing the serialization or the exclusion set renames every unit and strands every verdict the store holds."""
    return json.dumps(
        {key: value for key, value in unit.items() if key not in CARRY_PRESENTATION_KEYS},
        sort_keys=True,
    )


def carry_content_hash(unit: Mapping) -> str:
    return hashlib.sha256(carry_projection(unit).encode()).hexdigest()


# Bitcoin's base58 alphabet: the digits and both cases minus 0, O, I and l, so the glyphs that confuse each other never appear, with mixed case kept as distinct symbols so an id stays short — ids are copied and pasted rather than transcribed.
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# 58**10 < 2**64 <= 58**11, so eleven symbols hold any 64-bit value and the width never varies.
ID_SYMBOLS = 11
_ID_PATTERN = re.compile(rf"^[ue]-[{BASE58_ALPHABET}]{{{ID_SYMBOLS}}}$")


def base58_64(digest_hex: str) -> str:
    """The first 64 bits of a hex digest as `ID_SYMBOLS` base58 symbols, most significant first, padded with the alphabet's zero symbol (`1`) to the fixed width — case-significant, and a total order that is nothing more than the string's."""
    value = int(digest_hex[:16], 16)
    symbols = []
    for _ in range(ID_SYMBOLS):
        value, remainder = divmod(value, 58)
        symbols.append(BASE58_ALPHABET[remainder])
    return "".join(reversed(symbols))


def unit_id_for(content_key: str) -> str:
    """A unit's identity: its carry content key — the sha256 of `carry_projection`, the stamp every m1-audit fragment carries — truncated to its first 64 bits and spelled in base58 behind the `u-` prefix, `u-3mJ7kPq2Xw9` being the shape. Two units with one projection are one unit, so the id is a function of what the reviewer judges and of nothing that renumbers: neither the order the surface pages in nor any other unit's presence moves it, which is what lets a served fragment keep its bytes across builds and a verdict keep its unit across surfaces."""
    return "u-" + base58_64(content_key)


def echo_id_for(key_repr: str) -> str:
    """An echo group's identity, derived the same way from the group's key — the configs, the judged pair's codepoints, the final class and the ink-diff digest, as `repr` renders them — so one group carries one id on every surface it appears on, however many other groups exist beside it."""
    return "e-" + base58_64(hashlib.sha256(key_repr.encode()).hexdigest())


def is_content_id(value: object) -> bool:
    """Whether `value` is a unit or echo id of the content-addressed shape: the prefix, then exactly `ID_SYMBOLS` base58 symbols."""
    return isinstance(value, str) and _ID_PATTERN.match(value) is not None


def store_path(out_dir: Path) -> Path:
    return Path(out_dir) / STORE_NAME


def _sha256_file(path: Path) -> str:
    try:
        return fingerprint.file_sha256(Path(path))
    except OSError:
        return "missing"


def _font_digest(path: Path) -> str:
    """A site font's line in either whole-store stamp: `fingerprint.font_content_digest`, table by table outside `head` and `name`, with `_sha256_file`'s sentinel for a font that is not there. Neither dropped table can reach a unit's products or an ink signature — every glyph the before font renders is drawn from `CFF ` and placed by `hmtx` and `GPOS`, and the version strings and revision are read by nothing that shapes — so the `make all` a version bump runs leaves both stores serving, while a widened glyph or a moved anchor drops them as before."""
    try:
        return fingerprint.font_content_digest(Path(path))
    except OSError:
        return "missing"


def _manifest_stamp(out_dir: Path) -> str:
    """The manifest's identity digest for the store's whole-store stamp, or the sentinel when there is no manifest to read — a first build, or a crash between the manifest write and this one. The sentinel turns that into a stamp mismatch and a full rebuild rather than an exception out of `load_store`."""
    try:
        return unit_index.manifest_sha256(Path(out_dir))
    except OSError:
        return "missing"


# The rebuild/pipeline modules the surface build never imports: the driver, the defect and Manual-pin gates it runs, the baseline oracle with its position channel and its row cache, the conformance sweep with its settle-memo codec, the witness stage's rule replay, the GSUB emitter, the GPOS emitter, the GSUB packer, the pixel geometry, read-back, the font compile, the CoreText smoke and the cell enumeration the driver realizes glyphs from. The vocabulary the build shares with the sweep — the alphabet, a configuration's features, a formed stream's labels, the boundary names and the alias map — lives in `labels.py`, a leaf the build reaches without reaching the sweep behind it. Every other pipeline module is in build.py's walked import closure and rides `surface_code_paths`. An exclusion roster rather than an inclusion one, so a module that lands in the tree is hashed until rebuild/test_review_code_closure.py says the build never reaches it.
PIPELINE_NON_SURFACE_MODULES = frozenset(
    {
        "belt.py",
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
    }
)

# The crate modules the surface build never executes: the enumeration, its deep-fiber and third- and fourth-slot machinery, the liveness probes, the window options, the fold and its rule fold, the artifact writers and digests, the stream, and the fan-out. The surface reaches the crate through `settle-cases` and `guard-sweep` alone, and those two verbs' handlers in main.rs reach the parser, the index, the engine and its specificity order, the case replay, the guard, the emitter and the error and type vocabularies — which is what stays hashed, beside main.rs and lib.rs themselves. The same exclusion shape as the pipeline roster, held by the same test to the `crate::` references the two handlers reach outside the crate's test modules.
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
    """The code whose edit drops the per-unit store: what the surface build actually runs, rather than every tree it might. The ink-signature store keys on the narrower `signature_code_paths`, so an edit here that stays outside the comparator's closure re-enriches units and re-shapes nothing. The review side is `fingerprint.review_code_paths`, already held to build.py's import graph. The pipeline side is rebuild/pipeline minus `PIPELINE_NON_SURFACE_MODULES` and rebuild/validation whole, every module of which the build reaches. The crate side is rebuild/kernel-rs/src minus `KERNEL_NON_SURFACE_MODULES`, plus both Cargo files, since the crate's dependencies and profile shape every verb it answers. Before this closure existed the stamps folded `fingerprint.pipeline_code_paths` whole — every pipeline module, every Rust source, the font-compile tools — so an edit to the driver, a gate, the oracle or the crate's fold dropped the store and the next build paid a cold units phase for code it never executed. The conformance sweep, the oracle's row cache, the GSUB emitter and the pixel geometry are outside the closure for the same reason: the build reads the sweep's vocabulary through the `labels` leaf and executes none of the four.

    Module grain, which over-invalidates in the safe direction: a module imported for something the build never calls is still stamped, and the served-vs-recomputed sample inside every build stays the check that a served fragment equals a fresh computation. Two things are left out on purpose. The width and telemetry modules under rebuild/tools that the build takes its fan-out and its cost readings from cannot move a byte of a unit's products — rebuild/test_unit_cache.py's serial-and-parallel byte identity holds the width half — and the test that pins the rosters also pins that those are the only modules the build reaches outside the three trees. The font-compile tools roster is code the build never runs, and the draft harness line hashes tools/*.py anyway.
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


# The comparator's import closure: rebuild/review/ink.py and the rebuild/validation modules it reaches — the shaper, the seam classifier it takes `PIXEL_SIZE` from, and the row model the shaper reads. An inclusion roster of literal paths rather than an exclusion list, since the walk from ink.py reaches these four files and no package `__init__.py`, so the roster is one literal a reader can check. rebuild/test_review_code_closure.py walks the import graph from `rebuild.review.ink` in both directions and checks each entry is on disk, because `fingerprint.path_lines` reads a missing path as an absence rather than a failure and a renamed comparator module would leave the hash silently.
SIGNATURE_CODE_MODULES = (
    "rebuild/review/ink.py",
    "rebuild/validation/classify.py",
    "rebuild/validation/rowmodel.py",
    "rebuild/validation/shaping.py",
)


def signature_code_paths(repo_root: Path) -> list[Path]:
    """The code whose edit drops the ink-signature store: the comparator's own import closure (`SIGNATURE_CODE_MODULES`), a strict subset of `surface_code_paths`. A signature is `InkComparator.signature` over a `Shaper` and the fontTools outline pens, and nothing else the surface build runs — the driver, the enricher, the drafts, the kernel seam, the crate — can move one, so an edit there re-enriches units and re-shapes nothing. Module grain, the safe over-inclusion: the row model is stamped because the shaper imports it, though no signature reads a row.

    Three inputs sit outside the roster on argument rather than hash. The codepoints-to-text derivation in `build._resolve_signature_digests` has two halves: `audit.parse_codepoints` and `audit.format_codepoints`, whose round trip rebuild/test_review_audit.py::test_parse_codepoints pins and whose drift `build.ink_sig` turns into a KeyError on its first lookup, and the one-line `chr` join between a parsed row and the text the comparator shapes, which no test reaches. That join is the definition of what text a window's signature is taken over, so an edit to it is a change to what a signature means, and like this module's own `signature_key` schema and the store's framing it is expressed through `SIGNATURE_STORE_FORMAT` rather than the roster. A key-schema move cannot serve a wrong digest on its own, since the key is a content hash and every row re-keys into a miss; a join edit under an unbumped format would, which is why the format bump is the rule and not a courtesy. The uharfbuzz and fontTools versions ride no stamp, a gap shared with the unit store.
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
    """The whole-store stamp: any of these moving drops the cache entirely, and over-invalidation is the safe direction. The code line is `surface_code_paths`, the code the build runs and nothing more, so a pipeline or crate edit outside that closure — the driver, a gate, the oracle, the conformance sweep, the oracle's row cache, the GSUB emitter, the pixel geometry, the font compile, the crate's enumeration and fold — leaves the stamp where it was and the store serving. The two site fonts ride through `_font_digest`, blind to their `head` and `name` tables, so a version bump's rebuilt fonts leave it too. The rune files are absent on purpose — they invalidate at per-unit grain through the family keys — and so is the divergence ledger (see the module docstring for why its reach is already covered). The stamp carries the code closure's per-file lines as its `surface_code` detail, taken from the one `path_lines` read the code digest is folded from, so the file a miss note names is the file the digest saw. `subset_digests` is each acceptance configuration's table already hashed by the caller — the build hashes them once, for this line and for the subset pack's header alike — and left out, the tables are hashed here."""
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
    """Per family (bare letters and ligature runes alike), the digest a window's content key cites for it: the family's explain-aware rune digest — prose-blind but for the refuse `why` the served explain text quotes — joined with the digests of its static `resolve.against` closure, the one route by which its records read another rune file directly, and the after font's compiled-glyph digest for the family. Returns the family keys plus the after font's helpers digest for the environment stamp."""
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
    """What `UnitKeyer.signature_key` reads off a row: the four fields of `audit.AuditRow` and of `audit.RowView` that pin the placed ink, stated structurally so either record serves."""

    @property
    def config(self) -> str: ...
    @property
    def codepoints(self) -> str: ...
    @property
    def baseline(self) -> tuple[str, ...]: ...
    @property
    def new(self) -> tuple[str, ...]: ...


class UnitKeyer:
    """Computes per-unit content keys over the family keys, memoizing the family-set expansion per distinct window letter set (windows share their letter sets heavily, and the ligature-membership scan need not repeat per unit)."""

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
        """The unit's content key: sha256 over its audit rows as the audit's own lines, minus their newlines, in the run's order — the columns' `line` is that line byte for byte, the window the unit's and the rendered names each row's own, so a folded survivor's key is the key over the rows it absorbed under their names — then one line per window family the key cites, with that family's content key (`family_content_keys`). The unit is the table's row at `ordinal`: its window and its run of the row columns."""
        values = table.codepoints(ordinal)
        families = frozenset(self._family_of[value] for value in values if value in self._family_of)
        line = rows.line
        codepoints = table.codepoints_text(ordinal)
        start = table.rows_start(ordinal)
        lines = [line(index, codepoints) for index in range(start, start + table.row_count(ordinal))]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()

    def signature_key(self, row: SignatureRow) -> str:
        """One ink-signature store entry's content key: the audit row's window, config, the before font's rendered names, and the settled cells the after font is compiled to reproduce — everything the row pins that a signature depends on, deliberately without `kinds` and `matched_entry`, which are classification the shaped ink never reads (a ledger edit must not re-shape a window) — plus the same per-family digests the unit key cites. Truncated to sixteen hex characters, unlike the unit key: this one is written a million times over into a store whose 64-character keys were most of its bytes, and sixty-four bits over a million entries puts a collision at one in forty million — which would in any case only hand one window's sibling group a wrong-but-equal ink signature."""
        families = frozenset(
            self._family_of[value] for value in parse_codepoints(row.codepoints) if value in self._family_of
        )
        lines = ["\t".join((row.config, row.codepoints, "|".join(row.baseline), "|".join(row.new)))]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


@dataclass
class CachedUnit:
    """The write side's shape of one store record — what `write_store` serializes for a unit; `ServedUnit` is what `stream_store` builds from the line. One prior unit's reusable products: the identity needed to fetch its emitted fragment from the prior shards, plus the slim projection the parent's global reduces read. The record also carries the fragment's own `content_key` stamp — distinct from `key`, which is the content key over the unit's *inputs* — and a prior fragment is served only when the stamp on disk equals it, so what is fetched is proved to be the bytes this record describes. `slim` says which shape those bytes are (`audit.slim_fragment`), and the build serves them only when that is the shape it would write for the unit now.

    `address` is where those bytes are: the shard part (the manifest's relative spelling), byte offset and length the shard writer handed back as the fragment went down, the same `(part, start, length)` the app's sidecars carry for a Range fetch. It is recorded from the writer's own return rather than derived from anything else, and it is never a second copy of the stamp: the stamp beside it is what `PriorFragmentReader` holds the bytes at the address to when the fragment is read back at the write. A record without one — a store written before addresses were recorded, or one whose part `stream_store` found resized underneath the store — is served through the walk (`locate_prior_fragments`), which re-derives the address off the part's own text.

    `echo`, `exemplar`, `no_verdict`, `homes` and `policy_file` are what the fragment was written with beyond its content: the four fields `build.patch_fragment` writes over a fragment from the reduces and the ledger, and the one field of the drafts the cross-unit check reads. They are what lets the build tell a served fragment whose bytes on disk are already what it would write — every patched field equal to this build's — from one it has to read, patch and serialize again; the former is copied by address, and a whole shard part of them is left where it lies.
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
    """One store record as `stream_store` parses it, held for one fold: the plan folds it into the packed unit store (`unit_store.UnitStore.fold_served`) as the stream hands it over and releases it, so the parent holds one at a time, and only a record without an address waits, buffered, for the walk that places it. The projection the secondary-home reduce reads comes as tuples — `pair`, the two span tuples, the three name tuples and `seam_pairs` — pooled at the parse so the records that are buffered, or held whole by a `load_store` caller, share one instance per distinct value, while an addressed record the stream hands over pools within itself, so no table in the stream grows with the records it has handed over (`_served_unit`). `CachedUnit` is the write side's mirror; `seam_rects` is its `seams` list unchanged, the shape `patch_fragment` reads. Slots, because a caller holding a store whole holds one of these per line."""

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
        """The record's address as the plan's `PriorFragment`, stamped with the record's own `content_key` and marked verbatim — the bytes there were written by the shard writer and may be copied as they lie — or None for a record the walk has to place."""
        if self.address is None:
            return None
        part, start, length = self.address
        return PriorFragment(part, start, length, self.prior_id, self.content_key, verbatim=True)


_KEY_PREFIX = b'{"key": "'


def _served_unit(record: dict, trusted: Container[str], pool: dict, whole: bool) -> ServedUnit:
    """One parsed store line as the `ServedUnit` the plan holds, with every string that repeats across units — the class, the cluster and diff digests, the config names and delta digests, the address's part, the projection's glyph names, cell names and seam tokens — interned through the `sys.intern` table `audit.load_audit` describes, and its projection tuples pooled to one instance per distinct value across the records that are held together: through `pool` for every record when `whole` (a caller holding the store in hand) and otherwise only for a record without an address, the kind a streaming caller buffers for the walk, while an addressed record — folded and released before the next line is parsed — pools within itself, so a table shared across the stream holds nothing for the records it has handed over. The address survives only when its part is in `trusted`."""
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
    """One store record as the line the file holds it on. A line a previous store holds and the record the build's unit store answers for the same unit (`unit_store.UnitStore.cached_unit`) serialize identically — every field round-trips through JSON in the order and spelling it was written, the projection through the store's columns and `seam_home_record` included — so a line for a record this build would write unchanged is this very line, which is what lets the build copy it through a `StoreCursor` instead of building the record again. rebuild/test_unit_cache.py and rebuild/test_unit_store.py hold the identity: the projection round trip over one record, and the no-change and incremental rebuilds down to the store's bytes."""
    return (json.dumps(record.to_record()) + "\n").encode()


class StoreCursor(unit_index.LineCursor):
    """A forward-only reader over the previous build's store, handing out the line for an input key on request; `unit_index.LineCursor` keyed on the record's leading `key` field. The build walks it in triage order while it writes the new store, copying the line of every unit whose record it would write unchanged and building the rest."""

    def __init__(self, out_dir: Path) -> None:
        super().__init__(store_path(out_dir), field="key")


def _stamp_value(environment: fingerprint.EnvironmentStamp | str) -> str:
    return environment if isinstance(environment, str) else environment.value


def _stamp_header(environment: fingerprint.EnvironmentStamp | str) -> dict:
    """The header fields a stamp writes: `environment`, the hex a load compares, and for a stamp handed over as lines, `environment_lines` (its own labeled lines) and `environment_detail` (per label, the lines that label folds), which is what a miss note reads. A caller holding only the hex records the hex alone."""
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
    """Written after the manifest, stamped with the manifest's identity (`unit_index.manifest_sha256`, which projects the copied UI assets' components out), so a store can prove it describes the shards beside it while an assets refresh that rewrites only that field leaves it current; a crash between the two leaves a stamp mismatch and the next build falls back to a full pass. The header also carries the byte size of every shard part the records' addresses point into — `parts` when the caller names them, else the parts the records address — read off the committed parts here: the manifest stamp says nothing about the shards' bytes, and the size is the one fact about a part that a rewrite cannot leave where it was without leaving every address right too, so `stream_store` can tell a part whose addresses still hold from one to walk again at the cost of a stat per part. A record arrives either as a `CachedUnit` to serialize or as the line a previous store already holds for it (`record_line` is the identity between the two), and the file is staged under a sibling name and renamed last, so the previous store stays readable through a `StoreCursor` until this one is whole. The gzip mtime is pinned so consecutive identical builds stay byte-identical, and the compression level with it — level 1 rather than 9, because this file is written once and read once per build and the four seconds level 9 spends buying ten megabytes on a scratch artifact are four seconds off every cycle."""
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
    """Raised out of the generator `stream_store` returns when the store stops reading after its header did: a gzip member cut short or corrupt, a line that will not parse, a record missing a field. The records already handed over came from a store that can no longer vouch for them, so a caller that folded as it went discards what it folded; a `load_store` caller never sees it, since the wrapper answers None in its place."""


def stream_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    wanted: Container[str] | None = None,
    pool: dict | None = None,
) -> Iterator[ServedUnit] | None:
    """The prior build's records, one at a time in store order, or None when there is no usable store: absent, unreadable at the header, format- or environment-mismatched, or stamped for a manifest whose identity is not the one on disk (over-invalidation is the safe direction — a None simply costs a full build). The header is read here, before anything is returned, so a None is decided before a record is parsed; the body is a generator over the one handle the header was read from, which closes it when the last record is out or the read fails. A body that fails after some records raises `StoreUnreadable` from the generator, and a caller that folded each record as it came must then discard what it folded; an error the caller's own fold raises is the caller's, raised in the caller's frame, and never read as the store's. Every line is selected by its key before it is parsed: the key is sliced off the front of the raw line — `to_record` writes it first, and a carried line inherits that — and a line whose key cannot be sliced reads as absent, `wanted` or not, which is the same safe direction. With `wanted`, only the records whose key it names are parsed, so a line the workload never names costs a prefix compare and no parse. Every record comes as a `ServedUnit`, its strings interned and its projection tuples pooled (`_served_unit`); `pool` is the caller's table, and passing one says the caller holds the records together, so every record's tuples pool through it. Without one, only the records that come without an address share a table, the ones a streaming caller buffers for the walk, and an addressed record, folded and released before the next is parsed, pools within itself, so nothing in the stream grows with the records it has handed over. A record keeps its address only while the part it points into is the size the header recorded; a record whose part has moved, or that carries no address at all, comes with `address` None and is placed by the walk instead, so a shard rewritten underneath the store still serves rather than refusing at the write."""
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
    """The prior build's records keyed by input key, held whole, or None when there is no usable store — `stream_store` gathered into a dict, with a body that fails partway answering None too, so over-invalidation stays the safe direction here as well: absent, unreadable anywhere, format- or environment-mismatched, or stamped for a manifest whose identity is not the one on disk. The key-slice rule, the `wanted` rule, the interning, and the untrusted-part rule are the stream's, stated there; the records are held together here, so every one of them pools its tuples through `pool`, the caller's table or one made for the call. The build's plan reads the stream itself, folding each record as it comes; this is the shape for a caller that wants the records in hand, the tests and the surface promotion among them."""
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
    """The ink-signature store's whole-store stamp: only what a signature reads that the per-entry keys do not cover — the comparator's code (`signature_code_paths`, the import closure of `rebuild.review.ink`), the before font outside its `head` and `name` tables (`_font_digest`), and the after font's non-family glyphs, cmap, and layout wiring. The code line is the comparator's closure and not the build's, so an edit to the driver, the census, the drafts, the kernel seam or the crate leaves this store serving while `environment_stamp` drops the unit store; the module docstring names the rest of what is left out. The comparator closure's per-file lines ride as the `comparator_code` detail, from the same read the digest folds."""
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
    """One JSON header line, then one `key\\tdigest` line per entry, sorted by key; the pinned gzip mtime and the sort are what keep consecutive builds of the same inputs byte-identical. Written fresh each build with exactly the entries the merge needed, so stale windows age out rather than accumulating. Level 1, like the unit store: this is a million lines of hex, which is incompressible, and level 9 was spending four seconds for well under a percent. The file is staged under a sibling name and renamed last, like the unit store, so a build killed while the write runs leaves the previous store whole rather than a truncated gzip that `load_signature_store` reads as absent and the next pass pays for with a full re-shaping pass; the gzip header names the final path, not the staging one, since `GzipFile` would otherwise stamp the handle's name into the bytes."""
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
    """The prior build's signature digests keyed by content key, or None when there is no usable store — absent, unreadable, or format- or environment-mismatched; a None costs one parallel re-shaping pass, so over-invalidation stays the safe direction here too."""
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
    """A store's header line alone, for the caller that wants to name what moved after a load has declined it; both stores open on one JSON line. `None` when there is nothing readable there."""
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
    """Why a load of the store at `path` under `environment` declines, as one line in the order the loader checks: no file, a header that will not read, a stamp that moved — naming the labels through `fingerprint.moved_note`, with the code label expanded to the files that moved inside its closure, capped at `CODE_FILES_SHOWN` so a broad closure move names a handful and a count — and, for the unit store, a manifest whose identity is not the one recorded. `None` when the header agrees with everything asked of it, which leaves the loader's remaining refusal: a body that will not parse. A store written under the hex alone, with no `environment_lines`, reports the stamp moved and names no label."""
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
    """Why `stream_store` declines the unit store under `out_dir`, or `None` when its header agrees with `environment` and the manifest beside it (`_miss_note`)."""
    return _miss_note(store_path(out_dir), environment, "surface_code", _manifest_stamp(out_dir))


def signature_miss_note(out_dir: Path, environment: fingerprint.EnvironmentStamp) -> str | None:
    """Why `load_signature_store` declines the ink-signature store under `out_dir`, or `None` when its header agrees with `environment` (`_miss_note`)."""
    return _miss_note(signature_store_path(out_dir), environment, "comparator_code")


@dataclass(frozen=True, slots=True)
class PriorFragment:
    """Where one of the prior surface's fragments lives and the stamp it carries: the shard part it was written to (the manifest's relative spelling), the byte offset and length of its own JSON element there, and its `content_key`. The address is the same `(part, start, length)` the app's sidecars carry for a Range fetch. It comes from the store record (`ServedUnit.located`), which took it off the shard writer's own return, or from `locate_prior_fragments`, which re-derives it off the part's own text for a record without one and so stays right for a shard something rewrote by hand as long as the part is still ASCII. `verbatim` says which: bytes at a store address are the shard writer's own framing and may be copied into the next surface as they lie, where bytes the walk found may be anything that parses and are read, patched and serialized again."""

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
    """The elements of one shard part, each with the character offset and length of its own bytes: the `[`, then one `raw_decode` per element with the commas and whitespace between them skipped, so a fragment is parsed and released before the next is read and the whole part is never resident as objects at once. The framing `_write_shard` lays down is what an address is later read back through, but nothing here assumes it — a compact `json.dumps` of the same list walks the same way — and a part that is not a JSON array raises out to the caller, which treats it as unreadable."""
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
    """Where the prior shards hold each of the given {class id: prior unit ids}, keyed by prior id, with the stamp each fragment carries — one pass over the parts the prior manifest names for those classes, parsing one fragment at a time and keeping an address rather than the fragment, so the build decides what to serve without ever holding the previous surface's units. This is the fallback rather than the plan's path: a store record carries the address the shard writer returned, so a served build's plan is a lookup into the store, and the walk is asked only for the records `stream_store` handed over without one, the residue the plan buffers — an older store's, or those in a part whose size moved underneath the store — which on a build from a surface this code wrote is nothing at all. The prior manifest says which parts a class was written as — a class large enough to be split has no single file to guess at — and a missing or unreadable manifest or part simply contributes nothing, its units falling back to a fresh computation. So does a part that is not pure ASCII: the address is a character offset read back as a byte offset, which only `ensure_ascii` makes the same thing, and no part this build writes is anything else."""
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
    """Reads served fragments back out of the prior shards by address — the store record's, or the one the walk recorded — one open part at a time; the build reads them in shard order, so the handle changes once per class rather than once per unit. Each read is held against the address it was made from: the element must still be the unit with the stamp the plan served it under, or the file has changed underneath this build, which is a refusal rather than a fragment. For a store-addressed fragment this is its one parse, and the one place its bytes are ever held to its record."""

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
        """The fragment's bytes as they lie, held to the address without parsing them: the shard writer's framing puts each field on its own line as `"key": value`, so the id and the stamp the address was located under are found as substrings, and a fragment edited in place under its address — the one change the part-size guard cannot see — is refused here exactly as `read` refuses it. Only a verbatim address is read this way, since the framing is the writer's; a walked address parses."""
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
