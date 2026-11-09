"""The review corpus build's persisted per-unit cache. It keys each unit's phase-1 and phase-2 products by a content key over the unit's inputs, so after an edit the build re-enriches only the windows the edit can affect and reuses every other unit from the previous corpus's shards.

A unit's products are its ink diffs and machine-approval flags, its enrichment (cells, junctions, highlights, explain, provenance), and its three drafts. A machine-approved or verdict-exempt unit is written as a slim fragment (`audit.slim_fragment`), which omits the highlight, the explain, and the drafts. The products are a pure function of the unit's inputs, and the cache is correct only if the content key covers all of those inputs.

The key has two levels. The per-unit key (`UnitKeyer.key`) covers the unit's audit rows, which fix the window, its configs, the kinds, the matched ledger class, and both fonts' rendered names, plus one digest per window family (`family_content_keys`). A family digest combines the family's explain-aware rune digest, the rune digests of its static `resolve.against` closure, and a digest of the after font's compiled glyphs for the family (outlines, advances, and cursive anchors), so a drawing or anchor change invalidates a unit even when no name in its rows changes. A ligature family is included when all of its components are in the window.

The whole-store stamp (`environment_stamp`) covers everything that can change a unit's products without changing a family digest: the code the corpus build runs (`corpus_code_paths`), the non-rune data files, the kernel's settlement mode flags, the resolved spec structure and capability-feature set (cross-rune routes: predicate-class and group memberships, ligature sequences, and the formation guard's feature combinations), the before and Junior fonts outside their `head` and `name` tables (`_font_digest`), the acceptance configs' subset tables, the site corpus pages whose pins set a pin draft's `attribute` and `duplicate_of` and the draft harness that checks every pin draft (`fingerprint.draft_input_lines`, which the corpus manifest's Stage B `draft_inputs` component hashes too), and the after font's non-family glyphs, cmap, and GPOS wiring. `corpus_code_paths` is the review modules the build imports, the pipeline and validation modules they reach, and the crate modules the `settle-cases` and `guard-sweep` subcommands run. rebuild/test_review_code_closure.py checks these lists against the import graph. So an edit to the pipeline driver, a gate, the oracle or its row cache, the conformance sweep, the GSUB emitter, the pixel geometry, the pipeline's font-compile driver (rebuild/pipeline/compile_font.py), or the crate's enumeration and fold leaves the store valid.

Four inputs are outside the whole-store stamp. The after font's GSUB wiring is outside every stamp; `fingerprint.after_font_glyph_digests` explains why a window's glyph selection is covered without it. The divergence ledger reaches the shards through the audit's `matched_entry` column, which is in the rows, through its `ink_identical` ids, which decide which units take a change group instead of their class (`change_groups.reroutes`) and are a stamp line of their own, and through fields the build recomputes and patches on every pass (`no_verdict`, `exemplar`, class promotion), so a ledger edit that leaves those ids alone invalidates only the units whose rows it changed. The refuse `why` text the explain panel quotes is in the family keys instead, so rewording one re-enriches only the windows that contain that family. The change groups' copy (rebuild/review/change-groups.yaml) reaches only the manifest, which every build writes afresh, so rewording a group re-enriches no unit.

A store record holds the address of the previous build's fragment: the shard part, byte offset, and length the shard writer returned when it wrote the fragment, so the plan does not parse the previous corpus to find it. The record also holds what the parent's whole-corpus passes need: the machine flags and ink deltas, the change group and the ledger class a re-routed unit was taken from, the judged pair, the ink-diff digest for duplicate grouping, the primary-unit projection and per-junction rects, and the unit's mismatch lines. It records whether the fragment was written slim, because the shape depends on the exemption, a ledger fact outside the key. It also records the values the fragment was written with that come from outside the key: its duplicate group, its class after change-group promotion, the ledger's exemplar and exemption flags, its primary units of secondary junctions, and the rune file its policy draft names. So a unit that becomes a human unit on a ledger edit (`no_verdict` changes) is a miss and is re-enriched in full instead of reused as the slim fragment it had before, and a unit that moves the other way is a miss too. This keeps a cached corpus byte-identical to a from-scratch one.

Every field derived from the ledger or the whole-corpus passes (duplicate group, class, `no_verdict`, `exemplar`, the primary units of secondary junctions) is recomputed over all units on every build, and a unit's id depends only on its content key. A cached fragment whose recomputed fields all equal the values its record says it was written with stays at its address in the new corpus, read as bytes without parsing (`unit_store.UnitStore.cached_as_is`; `PriorFragmentReader.read_bytes` checks the record's id and stamp as substrings of the bytes). So does a recomputed or re-patched fragment whose bytes equal those at its prior record's address, found by unit id (`unit_store.UnitStore.prior_fragment`, `PriorFragmentReader.same_bytes`). The shard writer appends every other fragment to its class's overflow parts and leaves the parts it found in place, unless the class is written whole (`build._ShardWriter`, `build._route`). A cached fragment with a changed field is parsed once, patched, and serialized again, the same way a recomputed fragment is read back from the build's own arena of deflated fragments (`build._FragmentArena`), so a cache hit never keeps a stale global field. The cluster id, the change group and the ledger class are the global fields taken from the cached record, because their inputs (configs, per-config classes, junctions, highlight totals, machine flags and ink diffs) are covered by the key, the `ink_identical` ids by the stamp, and the exemption, which decides whether an identical class's unit re-routes, by the slim check, since such a unit is slim exactly when it is exempt.

`stream_store` parses a store line only if the workload names its key, into a `ParsedCachedUnit`, and yields the records one at a time in the store's materialized order. The plan loads each into the packed unit store (`rebuild/review/unit_store.py`) and releases it, so for the rest of the build the parent holds a cached unit only as columns there. If the store stops reading after some records were yielded, the stream raises `StoreUnreadable`, and the plan discards what it loaded and runs a full build. An error the load itself raises propagates unchanged. `load_store` collects the same stream into a dict.

The store is held in the segment format (`rebuild/pipeline/row_delta.py`): a head, `unit-cache.ndjson.gz.head`, which carries the header below as its stamps and lists a base, the single file a whole write gives (`write_store`), and the segments later builds wrote over it. A cached build writes only what changed (`update_store`): one segment of the records whose line changed, tombstones for the records no unit names any more and at the old place of each record that moved, and a place for each record in this build's triage order, since the base's lines carry no triage key; or the head alone when no record changed. A structure-stamp move or a letter batch (`REDERIVING_LABELS`), the share rule and the fold write a new base instead, and a store that did not read is rewritten whole, which the cycle reports (`output_sink.note_rewritten`). The store's materialized form (`materialized_store`) is the file a whole write of the same records gives, byte for byte. A store written before the segment format, the single file `unit-cache.ndjson.gz`, is read as a base with no head, and the first build that finds it links it as generation 0's base under a head as soon as its plan has read it (`adopt_single_file`). The plan reads every record of the prior store whatever its stamp (`scan_store`): to place this build's records against it, and, while its manifest stamp and part sizes hold, to keep each record it does not load by unit id (`PriorRecord`), so the write finds the prior fragment of a unit it recomputed.

rebuild/test_unit_cache.py::test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit checks that an incrementally rebuilt corpus holds a from-scratch build's fragments by id and materializes to its files byte for byte, `test_no_change_rebuild_reuses_every_unit_and_is_byte_stable` that the cached path rewrites and appends no shard part and writes the store's head alone, and `test_the_stores_materialized_form_is_the_file_a_whole_write_gives` that the store's segments materialize to `write_store`'s file.

Both stores record their whole-store stamp in two forms: the hex digest that `stream_store` and `load_signature_store` compare, and the `fingerprint.EnvironmentStamp` lines the digest is computed from, with the code label's per-file lines beside them. When a load rejects a store, `store_miss_note` and `signature_miss_note` use these to report which of four causes applied: no store, a store that does not read, a manifest that changed, or a stamp line that changed. For a code line they name the changed file inside the closure (`corpus_code: rebuild/review/ink.py (changed)`) instead of the closure's digest. The note is only as specific as the lines: `after_helpers` is one digest over the after font's non-family glyphs, cmap, and layout wiring, so a change there is not narrowed further. A staging pass writes its corpus to another directory, so a live pass after one compares against the last live store's lines and reports everything that changed since that store was written, code changes included.

This module also defines the carry content key (`carry_projection`, `carry_content_hash`), so the build and the carry share one definition. The build stamps each fragment's `content_key` with it when it drafts the fragment, and the unit's id is derived from that stamp (`unit_id_for`), so rebuild/tools/carry_verdicts.py moves each prior verdict to the unit with the same id. The stamp is excluded from the projection it hashes.

Beside the per-unit store is the ink-signature store, which does for the ink-duplicate merge what the unit store does for enrichment. The merge needs one rendered-outcome signature per (window, config) over every window split only by glyph names. It runs before the set of units exists, so the unit store cannot supply these signatures. Each entry's key (`UnitKeyer.signature_key`) follows the same two-level argument as the unit key: the audit row fixes the window, the config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, and the per-family digests fix the after font's outlines, advances, and cursive anchors for every family the window can touch. The whole-store stamp (`signature_environment`) covers what signatures depend on beyond that: the comparator's code (`signature_code_paths`: rebuild/review/ink.py and the three rebuild/validation modules it reaches, which rebuild/test_review_code_closure.py checks by walking the import graph from `rebuild.review.ink` in both directions), the before font outside its `head` and `name` tables, and the after font's non-family glyphs, cmap, and GPOS wiring. The rest of `corpus_code_paths` is left out (the build driver, this module, the enricher, the drafts, the kernel interface, the crate), because computing a signature runs none of it. The ledger, the subset tables, the Junior font, the site corpus files, and the draft harness are left out because signatures read none of them. So this store stays valid through edits that invalidate the unit store, and a build that re-enriches every unit can still skip re-shaping for the merge.
"""

from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import os
import re
import sys
import zlib
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Container, Iterable, Iterator, Mapping, Protocol, Sequence

from rebuild.pipeline import fingerprint, kernel_exec, output_sink, row_delta, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import unit_index
from rebuild.review.audit import ACCEPTANCE_CONFIGS, RowColumns, UnitTable, parse_codepoints

STORE_FORMAT = unit_index.STORE_FORMAT
STORE_NAME = unit_index.STORE_NAME
# The name a base's gzip header records: the staging name the single-file writer wrote the store under before the segment format, so a base is that file byte for byte.
_BASE_GZIP_NAME = STORE_NAME + ".partial"
# The environment stamp's lines whose move means every record is re-derived (#618's decision 72): a structure-stamp move or a letter batch, on which the store writes a new base instead of a segment. A letter batch moves `spec_structure`, which hashes the alphabet's runes; the `subsets` line is not one of them, because an old-font edit moves it too.
REDERIVING_LABELS = ("spec_structure", "capability_features", spec_load.ALPHABET_LABEL)
SIGNATURE_STORE_FORMAT = "ams-review-ink-signatures/2"
SIGNATURE_STORE_NAME = "ink-signatures.tsv.gz"

# The fields the carry projection leaves out; rebuild/test_carry_verdicts.py checks them. A field is left out when a rebuild can change it without changing what the reviewer judged, and what the reviewer judged stays in: the window and its configs, both fonts' glyphs, cells, and junctions, the extensions, the highlight geometry, and the `ink_identical` and `junior_equivalent` flags. `no_verdict`, `exemplar`, `duplicate_group`, and `cluster` come from the ledger or the whole-corpus passes. `class`, `ledger_class`, `summary`, and the config badge (`config_note`, `config_gate`, `config_classes`, `config_class_note`) say which class lists the unit and how the app describes it, and each follows from the audit rows, the ledger, the grouping code, or `configs`, so renaming or regrouping a class or rewording a summary changes no unit's id. `id` is the projection's own digest and `content_key` is the stamp of this projection, so neither can be an input to it. `batch` is excluded so a fragment from an older corpus that still carries one hashes the same. `explain`, `drafts`, `provenance`, and `secondary_junctions` are derived presentation, whose content is already covered by the window and both fonts' glyphs, cells, and junctions. `ink_deltas` is the same delta identity stored per config. `picture_identical` follows from the window and both fonts' placed glyphs, which the projection already covers, so excluding it changes nothing the key distinguishes, and including it would change the id of every unit whose flag changed and orphan their verdicts. `ink_identical` and `junior_equivalent` are derived flags inside the projection. Every fragment carries both, so removing either would change every unit id. The projection includes `highlight`, which a slim fragment (`audit.slim_fragment`) omits, so a slim fragment's stamp differs from the one its full fragment would have. That orphans nothing, because slim units take no verdicts.
CARRY_PRESENTATION_KEYS = frozenset(
    {
        "id",
        "batch",
        "no_verdict",
        "exemplar",
        "class",
        "ledger_class",
        "summary",
        "config_note",
        "config_gate",
        "config_classes",
        "config_class_note",
        "explain",
        "drafts",
        "provenance",
        "secondary_junctions",
        "duplicate_group",
        "cluster",
        "ink_deltas",
        "picture_identical",
        "content_key",
    }
)


def carry_projection(unit: Mapping) -> str:
    """Return the input of the carry content key: the unit's fields outside `CARRY_PRESENTATION_KEYS` as sorted-key JSON. A unit's id is derived from this projection's digest, so changing the serialization or the exclusion set changes every unit id and orphans every recorded verdict."""
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
    """Return a unit's id: `u-` followed by the first 64 bits of its carry content key in base58, for example `u-3mJ7kPq2Xw9`. The content key is the sha256 of `carry_projection`, the stamp every m1-audit fragment carries. Two units with the same projection have the same id. The id does not depend on the unit's position in the queue or on which other units exist, so a cached fragment keeps its bytes across builds and a verdict follows its unit across corpora."""
    return "u-" + base58_64(content_key)


def duplicate_group_id_for(key_repr: str) -> str:
    """Return a duplicate group's id: `e-` followed by the same encoding of the sha256 of the group's key (the configs, the judged pair's codepoints, the final class, and the ink-diff digest, as `repr` renders them). A group has the same id on every corpus it appears on, whatever other groups exist."""
    return "e-" + base58_64(hashlib.sha256(key_repr.encode()).hexdigest())


def is_content_id(value: object) -> bool:
    """Return whether `value` is a unit or duplicate-group id: the prefix, then exactly `ID_SYMBOLS` base58 symbols."""
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


# The rebuild/pipeline modules the corpus build never imports, such as the driver, its gates, the oracle, the conformance sweep, the emitters, and the font compile. rebuild/test_review_code_closure.py checks this set against build.py's import closure in both directions. The build reads the vocabulary it shares with the sweep (the alphabet, a configuration's features, a formed stream's labels, the boundary names, and the alias map) from `labels.py`, which imports none of these modules. This is an exclusion list, so a new pipeline module is hashed into `corpus_code_paths` until the test shows the build never reaches it and it is added here.
PIPELINE_NON_CORPUS_MODULES = frozenset(
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
        "position_record.py",
        "readback.py",
        "run_m1.py",
        "run_m1_gates.py",
        "shapers.py",
        "surface.py",
        "witness.py",
    }
)

# The crate modules the corpus build never runs. The build calls the crate only through the `settle-cases` and `guard-sweep` subcommands, and every module their handlers in main.rs reach stays hashed, with main.rs and lib.rs. rebuild/test_review_code_closure.py checks this exclusion list against the `crate::` references those two handlers reach outside the crate's test modules.
KERNEL_NON_CORPUS_MODULES = frozenset(
    {
        "artifacts.rs",
        "certificate.rs",
        "crossconfig.rs",
        "deep_slots.rs",
        "fanout.rs",
        "fiber.rs",
        "fixpoint.rs",
        "fold.rs",
        "liveness.rs",
        "options.rs",
        "replay.rs",
        "row_merge.rs",
        "rulefold.rs",
        "sha256.rs",
        "shipped_order.rs",
        "stream.rs",
    }
)


def corpus_code_paths(repo_root: Path) -> list[Path]:
    """Return the code whose edit invalidates the per-unit store: the code the corpus build runs. The review side is `fingerprint.review_code_paths`, which is checked against build.py's import graph. The pipeline side is rebuild/pipeline minus `PIPELINE_NON_CORPUS_MODULES`, plus all of rebuild/validation, which the build reaches. The crate side is rebuild/kernel-rs/src minus `KERNEL_NON_CORPUS_MODULES`, plus Cargo.toml and Cargo.lock, since the crate's dependencies and build profile affect every subcommand. The ink-signature store uses the narrower `signature_code_paths`, so an edit here outside the comparator's closure re-enriches units and re-shapes nothing.

    The lists work at module granularity, which over-invalidates in the safe direction: a module imported for something the build never calls is still hashed. The cached-versus-recomputed sample in every build checks that a cached fragment equals a recomputation. Two sets of modules are left out. The fan-out width and telemetry modules under rebuild/tools cannot change a unit's products: rebuild/test_unit_cache.py's serial-versus-parallel byte identity covers the width, and rebuild/test_review_code_closure.py checks that these are the only modules the build reaches outside the three trees. The font-compile tools are the other set: the build reaches tools/ only through test/test_shaping.py, and the draft harness line of `environment_stamp` hashes that file and the tools/ modules it imports (`fingerprint.font_compile_tool_paths`).
    """
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    pipeline = [
        path
        for path in sorted((root / "rebuild" / "pipeline").glob("*.py"))
        if path.name not in PIPELINE_NON_CORPUS_MODULES
    ]
    validation = sorted((root / "rebuild" / "validation").glob("*.py"))
    crate = [kernel / "Cargo.toml", kernel / "Cargo.lock"] + [
        path for path in sorted((kernel / "src").rglob("*.rs")) if path.name not in KERNEL_NON_CORPUS_MODULES
    ]
    return pipeline + validation + crate + fingerprint.review_code_paths(root)


# The comparator's import closure: rebuild/review/ink.py and the rebuild/validation modules it reaches (the shaper, the junction classifier it takes `PIXEL_SIZE` from, and the row model the shaper imports). It is an inclusion list of literal paths because the walk from ink.py reaches only these files and no package `__init__.py`. rebuild/test_review_code_closure.py walks the import graph from `rebuild.review.ink` in both directions and checks that each entry exists, because `fingerprint.path_lines` skips a missing path without error, so a renamed comparator module would silently drop out of the hash.
SIGNATURE_CODE_MODULES = (
    "rebuild/review/ink.py",
    "rebuild/validation/classify.py",
    "rebuild/validation/rowmodel.py",
    "rebuild/validation/shaping.py",
)


def signature_code_paths(repo_root: Path) -> list[Path]:
    """Return the code whose edit invalidates the ink-signature store: the comparator's import closure (`SIGNATURE_CODE_MODULES`), a subset of `corpus_code_paths`. A signature is `InkComparator.signature` over a `Shaper` and the fontTools outline pens, and nothing else the corpus build runs can change one, so an edit elsewhere re-enriches units and re-shapes nothing. The row model is included because the shaper imports it, although no signature reads a row.

    Some inputs are outside this hash. `build.signature_text` turns a window's codepoints into the text a signature is taken over, in two steps. Its parse, `audit.parse_codepoints`, must invert `audit.format_codepoints`, which writes the window strings (`audit.UnitTable.codepoints_text`) that the store's keys cover and that the build's signature table and the merge's lookups are keyed by. Nothing in the build round-trips the pair, so a mismatch would shape a text other than the window's under that window's key without raising; rebuild/test_review_audit.py::test_parse_codepoints pins the pair. Its `chr` join defines what text a signature covers, so a change to it changes what a signature means. The join is versioned by `SIGNATURE_STORE_FORMAT`, as are this module's `signature_key` layout and the store's file format. A change to the key layout alone cannot supply a wrong digest, because every row gets a new key and misses. A change to the join without a format bump would, so rebuild/test_review_audit.py::test_the_signature_text_is_pinned_beside_the_store_format checks `build.signature_text`'s output and the format as one literal pair, and fails on a join change until the format changes too. The uharfbuzz and fontTools versions are in no stamp, for this store or the unit store.
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
    *,
    identical_classes: Iterable[str],
    draft_lines: Sequence[str] | None = None,
) -> fingerprint.EnvironmentStamp:
    """Return the unit store's whole-store stamp. A change to any of its lines invalidates the whole store, and over-invalidation is the safe direction. The code line is `corpus_code_paths`, so a pipeline or crate edit outside the code the build runs leaves the store valid. The two site fonts are hashed through `_font_digest`, which ignores `head` and `name`, so the fonts a version bump rebuilds leave it valid too. The rune files are left out because the family keys cover them per unit, and the divergence ledger is left out for the reason the module docstring gives, except its `ink_identical` ids (`identical_classes`), sorted, which decide a re-routed unit's class, cluster and fragment. The other data inputs are hashed by `fingerprint.data_path_lines`, so a schema hover or a comment in one of them leaves the store valid. The stamp carries the code closure's per-file lines as its `corpus_code` detail, from the same `path_lines` read the code digest is computed from, so the file a miss note names is the file the digest saw. `subset_digests` is each acceptance configuration's subset table, already hashed by the caller; the build hashes them once for this line and for the subset pack's header. When it is None, the tables are hashed here. The `site_corpus` and `draft_harness` lines are `fingerprint.draft_input_lines`, the lines the manifest's `draft_inputs` component is the digest of. `draft_lines` is those lines when the caller has already taken them, as the build does from the one read of the pages its drafters' index comes from; when it is None, they are taken here. While the alphabet switch is set, the lines end with its token (`spec_load.alphabet_lines`), so a store built over the whole alphabet never serves a small one, nor the reverse; with it unset no line is added."""
    root = Path(repo_root)
    code_lines = fingerprint.path_lines(root, corpus_code_paths(root))
    runes = set(fingerprint.rune_paths(root))
    ledger = root / "rebuild" / "m1-divergences.yaml"
    data_lines = fingerprint.data_path_lines(
        root, [path for path in fingerprint.data_paths(root) if path not in runes and path != ledger]
    )
    if subset_digests is None:
        subset_digests = {
            config: _sha256_file(Path(subset_dir) / f"baseline-{config}.subset.tsv.gz")
            for config in ACCEPTANCE_CONFIGS
        }
    lines = [
        f"format\t{STORE_FORMAT}",
        f"corpus_code\t{fingerprint.digest_lines(code_lines)}",
        "data\t" + hashlib.sha256("\n".join(data_lines).encode()).hexdigest(),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        f"before_font\t{_font_digest(Path(before_font))}",
        f"junior_font\t{_font_digest(Path(junior_font))}",
        "subsets\t" + " ".join(f"{config}={subset_digests[config]}" for config in ACCEPTANCE_CONFIGS),
        *(fingerprint.draft_input_lines(root) if draft_lines is None else draft_lines),
        f"after_helpers\t{after_helpers_digest}",
        "identical_classes\t" + json.dumps(sorted(identical_classes)),
        *spec_load.alphabet_lines(),
    ]
    return fingerprint.EnvironmentStamp(lines=tuple(lines), detail=(("corpus_code", tuple(code_lines)),))


def family_content_keys(repo_root: Path, spec: ResolvedSpec, after_font: Path) -> tuple[dict[str, str], str]:
    """Return each family's content key, for bare letters and ligature runes alike, and the after font's helpers digest for the whole-store stamps. A family's key combines its explain-aware rune digest (prose-insensitive except for the refuse `why` the explain text quotes), the rune digests of its static `resolve.against` closure, which is the one way its records read another rune file directly, and the after font's compiled-glyph digest for the family."""
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
    """The fields `UnitKeyer.signature_key` and `UnitKeyer.signature_keys` read from a row: the four fields of `audit.AuditRow` and `audit.RowView` that determine the placed ink, declared as a protocol so either class can be passed."""

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
        """Return one ink-signature store entry's key. It covers the audit row's window, config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, plus the same per-family digests the unit key cites. It leaves out `kinds` and `matched_entry`, which classify the row but do not affect the shaped ink, so a ledger edit does not re-shape a window. Unlike the unit key, it is cut to sixteen hex characters: the store holds an entry for every window and config the merge compares (the corpus build's `load` line counts them), and a full 64-character key would be half of each line, beside the 64-character digest. At a million entries the chance of a 64-bit collision is about one in forty million, and a collision would at worst give one window's sibling group the signature of another window."""
        families = frozenset(
            self._family_of[value] for value in parse_codepoints(row.codepoints) if value in self._family_of
        )
        lines = ["\t".join((row.config, row.codepoints, "|".join(row.baseline), "|".join(row.new)))]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]

    def signature_keys[R: SignatureRow](self, rows: Iterable[R]) -> Iterator[tuple[R, str]]:
        """Yield each row with its `signature_key`, in order. Rows of one window arrive together (`audit.signature_rows` yields them sibling by sibling), so the families' lines are computed once per run of rows sharing a window string and appended to each row's line as one suffix, rather than once per row. The keys are byte-identical to `signature_key`'s, which rebuild/test_unit_cache.py checks across window boundaries."""
        family_of = self._family_of
        family_keys = self._family_keys
        relevant = self._relevant_families
        sha256 = hashlib.sha256
        window = None
        suffix = ""
        for row in rows:
            codepoints = row.codepoints
            if codepoints != window:
                families = frozenset(
                    family_of[value] for value in parse_codepoints(codepoints) if value in family_of
                )
                suffix = "".join(f"\n{name}\t{family_keys[name]}" for name in relevant(families))
                window = codepoints
            line = "\t".join((row.config, codepoints, "|".join(row.baseline), "|".join(row.new))) + suffix
            yield row, sha256(line.encode()).hexdigest()[:16]


@dataclass
class CachedUnit:
    """One store record as `write_store` serializes it; `ParsedCachedUnit` is the same record as `stream_store` parses it. It holds what the build needs to fetch the unit's previous fragment from the prior shards, plus the projection the parent's whole-corpus passes read. `content_key` is the fragment's own stamp, which differs from `key`, the content key over the unit's inputs. A prior fragment is reused only when the stamp on disk equals `content_key`, which shows the fetched bytes are the ones this record describes. `slim` records the fragment's shape (`audit.slim_fragment`), and the build reuses the fragment only when that is the shape it would write for the unit now.

    `address` is the shard part (as the manifest names it), byte offset, and length the shard writer returned when it wrote the fragment, the same `(part, start, length)` the app's sidecars use for a Range fetch. It is never a copy of the stamp: `PriorFragmentReader` checks the bytes at the address against the stamp when the write reads them back. A record with no address, from an older store or in a part whose size `stream_store` found changed, is located by `locate_prior_fragments`, which reads the address off the part's text.

    `duplicate_group`, `exemplar`, `no_verdict`, and `primary_units` are the values `build.patch_fragment` wrote into the fragment from the whole-corpus passes and the ledger, and `policy_file` is the one drafts field the cross-unit check reads. `unit_store.UnitStore.cached_as_is` compares the first four and the class with this build's values. When all are equal, the fragment's bytes on disk are already what this build would write, and it is copied by address; a shard part made up only of such fragments is left in place. Otherwise the fragment is read, patched, and serialized again.
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
    change_group: str
    ledger_class: str
    pair_codepoints: tuple[int, int] | None
    proj: dict
    junctions: list[dict]
    mismatches: list[str]
    duplicate_group: str | None
    exemplar: bool
    no_verdict: bool
    primary_units: list[list]
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
            "change_group": self.change_group,
            "ledger_class": self.ledger_class,
            "pair_codepoints": list(self.pair_codepoints) if self.pair_codepoints else None,
            "proj": self.proj,
            "junctions": self.junctions,
            "mismatches": self.mismatches,
            "duplicate_group": self.duplicate_group,
            "exemplar": self.exemplar,
            "no_verdict": self.no_verdict,
            "primary_units": self.primary_units,
            "policy_file": self.policy_file,
            "address": list(self.address) if self.address else None,
        }


@dataclass(frozen=True, slots=True)
class ParsedCachedUnit:
    """One store record as `stream_store` parses it. The plan loads each record into the packed unit store (`unit_store.UnitStore.load_cached`) as the stream yields it and then releases it, so the parent holds one at a time; only a record without an address is buffered until the walk places it. The projection the primary-unit resolution pass reads is stored as tuples (`pair`, the two span tuples, the three name tuples, and `junction_pairs`), which `_parsed_cached_unit` pools so that records held together share one instance per distinct value. `junction_rects` is `CachedUnit.junctions` unchanged, in the shape `patch_fragment` reads. The class uses slots because a caller holding the whole store holds one instance per line."""

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
    change_group: str
    ledger_class: str
    pair_codepoints: tuple[int, int] | None
    duplicate_group: str | None
    exemplar: bool
    no_verdict: bool
    primary_units: list[list]
    policy_file: str | None
    junction_rects: list[dict]
    mismatches: list[str]
    pair: tuple[int, int] | None
    after_spans: tuple[tuple[int, int], ...]
    after_cells: tuple[str, ...]
    after_junctions: tuple[str, ...]
    before_spans: tuple[tuple[int, int], ...]
    before_glyphs: tuple[str, ...]
    before_junctions: tuple[str, ...]
    junction_pairs: tuple[tuple[int, int], ...]

    def located(self) -> "PriorFragment | None":
        """Return the record's address as a `PriorFragment` stamped with the record's `content_key` and marked byte-copied, meaning the shard writer wrote those bytes and they may be copied unchanged, or None when the walk has to locate the record."""
        if self.address is None:
            return None
        part, start, length = self.address
        return PriorFragment(part, start, length, self.prior_id, self.content_key, byte_copied=True)


_KEY_PREFIX = b'{"key": "'


def _parsed_cached_unit(record: dict, trusted: Container[str], pool: dict, whole: bool) -> ParsedCachedUnit:
    """Build the `ParsedCachedUnit` for one parsed store line. Strings that repeat across units (the class, the change group and ledger class, the cluster and diff digests, the config names and delta digests, the address's part, and the projection's glyph names, cell names, and junction tokens) are interned with `sys.intern`, the table `audit.load_audit` describes. The projection's tuples are pooled through `pool` when `whole` is true (the caller holds the whole store) or when the record has no address (a streaming caller buffers those for the walk). An addressed record in a stream is loaded and released before the next line is parsed, so it pools only within itself, and a table shared across the stream holds nothing for the records already yielded. The address is kept only when its part is in `trusted`."""
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
    junctions = record["junctions"]
    return ParsedCachedUnit(
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
        change_group=sys.intern(record["change_group"]),
        ledger_class=sys.intern(record["ledger_class"]),
        pair_codepoints=(pair[0], pair[1]) if pair else None,
        duplicate_group=record["duplicate_group"],
        exemplar=record["exemplar"],
        no_verdict=record["no_verdict"],
        primary_units=[
            [primary_unit, bool(suppressed)] for primary_unit, suppressed in record["primary_units"]
        ],
        policy_file=record["policy_file"],
        junction_rects=junctions,
        mismatches=list(record["mismatches"]),
        pair=pooled((proj["pair"][0], proj["pair"][1])) if proj["pair"] else None,
        after_spans=spans(proj["after_spans"]),
        after_cells=names(proj["after_cells"]),
        after_junctions=names(proj["after_junctions"]),
        before_spans=spans(proj["before_spans"]),
        before_glyphs=names(proj["before_glyphs"]),
        before_junctions=names(proj["before_junctions"]),
        junction_pairs=spans(junction["pair"] for junction in junctions),
    )


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _part_sizes(out_dir: Path, parts: Iterable[str]) -> dict[str, int | None]:
    return {part: _file_size(Path(out_dir) / part) for part in sorted(set(parts))}


def record_line(record: CachedUnit) -> bytes:
    """Return one store record as its line in the file. A line from the previous store and the record the build's unit store produces for the same unit (`unit_store.UnitStore.cached_unit`) serialize to the same bytes, because every field, including the projection read back through the store's columns and `primary_unit_projection_record`, round-trips through JSON in the order and form it was written. So a record this build would write unchanged is left where the store holds it, and the store's segment holds only the records whose line changed (`update_store`). rebuild/test_unit_cache.py and rebuild/test_unit_store.py check this: the projection round trip for one record, and the no-change and incremental rebuilds down to the store's bytes."""
    return (json.dumps(record.to_record()) + "\n").encode()


def _stamp_value(environment: fingerprint.EnvironmentStamp | str) -> str:
    return environment if isinstance(environment, str) else environment.value


def _stamp_header(environment: fingerprint.EnvironmentStamp | str) -> dict:
    """Return the header fields for a stamp: `environment`, the hex a load compares, and for an `EnvironmentStamp`, also `environment_lines` (its labeled lines) and `environment_detail` (the lines behind each detailed label), which the miss notes read. A stamp passed as a hex string records only the hex."""
    fields: dict = {"environment": _stamp_value(environment)}
    if not isinstance(environment, str):
        fields["environment_lines"] = list(environment.lines)
        fields["environment_detail"] = {label: list(lines) for label, lines in environment.detail}
    return fields


def store_header(
    out_dir: Path, environment: fingerprint.EnvironmentStamp | str, parts: Iterable[str]
) -> dict:
    """Return the store's header for a write: its format, the stamp (`_stamp_header`), the manifest's identity (`unit_index.manifest_sha256`, which leaves out the copied UI assets' component) and the byte size of every shard part in `parts`. The manifest stamp shows the store describes the shards beside it, and an assets refresh that rewrites only that component leaves the store current. The manifest stamp says nothing about the shards' bytes, so a reader compares each part's current size with the recorded one, at one stat per part, and drops the addresses into a part whose size changed. Equal sizes do not prove the addresses are still right, since a rewrite can swap two fragments of equal length; `PriorFragmentReader` catches that case by checking the id and stamp at each address. The head carries this header as its stamps (`row_delta`), and a base carries it inline."""
    return {
        "format": STORE_FORMAT,
        **_stamp_header(environment),
        "manifest_sha256": _manifest_stamp(out_dir),
        "parts": _part_sizes(out_dir, parts),
    }


def _write_lines(stream: row_delta.Sink, header: dict, lines: Iterable[bytes]) -> tuple[int, int]:
    """Write a store's header and its record lines to `stream` and return the records and their bytes."""
    stream.write((json.dumps(header) + "\n").encode())
    count = total = 0
    for line in lines:
        stream.write(line)
        count += 1
        total += len(line)
    return count, total


def _readable_head(out_dir: Path) -> row_delta.Head | None:
    try:
        return row_delta.read_head(out_dir, STORE_NAME)
    except row_delta.HeadError:
        return None


def _write_base(
    out_dir: Path, prior: row_delta.Head | None, header: dict, lines: Iterable[bytes], *, content: int
) -> row_delta.Head:
    """Write `lines` under `header` as the next generation's base, commit a head that lists it alone, and delete the parts the head does not list and the single file a store written before the segment format leaves. The base is the file a whole write gives, byte for byte: gzip level 1, since it is written once and read once a build and level 9 compresses about four times slower to make it about a fifth smaller, with a fixed mtime so consecutive identical builds write identical bytes."""
    generation = 0 if prior is None else prior.generation + 1
    base = row_delta.write_part(
        out_dir,
        row_delta.base_name(STORE_NAME, generation),
        lambda stream: _write_lines(stream, header, lines),
        gzip_name=_BASE_GZIP_NAME,
    )
    head = row_delta.Head(STORE_NAME, generation, content, header, base)
    row_delta.commit(out_dir, head)
    store_path(out_dir).unlink(missing_ok=True)
    return head


def write_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    records: Iterable[CachedUnit | bytes],
    parts: Iterable[str] | None = None,
) -> row_delta.Head:
    """Write the unit store whole after the manifest: a new base, holding a header (`store_header`) and each record's line, as the next generation under a new head, which is committed last (`row_delta`). The header records `parts` when the caller names them, otherwise the parts the `CachedUnit` records address. A crash between the manifest write and this one leaves a stamp mismatch, and the next build falls back to a full pass. A record is either a `CachedUnit` to serialize or its line (`record_line` makes the two identical). The previous head and its parts stay readable until the new head is in place. This is the materialized form of the store for these records: `update_store` writes the same content as a segment when it can."""
    if parts is None:
        records = list(records)
        parts = (record.address[0] for record in records if isinstance(record, CachedUnit) and record.address)
    header = store_header(out_dir, environment, parts)
    prior = _readable_head(out_dir)
    row_delta.remove_unlisted(out_dir, STORE_NAME, prior)
    lines = (record if isinstance(record, bytes) else record_line(record) for record in records)
    return _write_base(out_dir, prior, header, lines, content=0 if prior is None else prior.content + 1)


class StoreUnreadable(Exception):
    """Raised when a store that exists cannot be read: a head that does not parse or whose parts are missing or differ from it, a header that does not read, or, from the generator `stream_store` returns, a body that stops reading after its header did: a truncated or corrupt gzip member, a line that does not parse, or a record missing a field. The records already yielded came from a store that can no longer be trusted, so a caller that loaded them as they came must discard what it loaded. `load_store` catches it and returns None."""


_STORE_ERRORS = (OSError, EOFError, ValueError, KeyError, TypeError, StopIteration)


@dataclass
class OpenStore:
    """A unit store open for reading: its stamps (the head's, or the single file's inline header), its head (None for the single file a store written before the segment format leaves), and its parts, each opened as the head was read (`row_delta.open_parts`). `lines` yields its materialized records in order, each with its place (`row_delta.Place`), merging the base and the segments as streams (`row_delta.merge_placed`). Close it when done."""

    stamps: dict
    head: row_delta.Head | None
    handles: list[BinaryIO] = field(default_factory=list)

    def lines(self) -> Iterator[tuple[row_delta.Place, bytes]]:
        return unit_index.store_lines(self.head, self.handles)

    def close(self) -> None:
        handles, self.handles = self.handles, []
        for handle in handles:
            handle.close()


def open_store(out_dir: Path, *, sha256: bool = False) -> OpenStore | None:
    """Open the unit store under `out_dir` and return it, or None when there is none. A store with a head opens every part the head lists and checks each part's size, and with `sha256` its digest too, as a reader that merges every byte does. A directory with no head is read from the single file a store written before the segment format leaves, as one base. A store that exists and does not open raises `StoreUnreadable`."""
    try:
        head = row_delta.read_head(out_dir, STORE_NAME)
    except row_delta.HeadError as error:
        raise StoreUnreadable(str(error)) from None
    if head is None:
        try:
            handle = open(store_path(out_dir), "rb")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StoreUnreadable(str(error)) from None
        try:
            with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
                header = json.loads(next(stream))
            if not isinstance(header, dict):
                raise ValueError("the header is not an object")
        except _STORE_ERRORS as error:
            handle.close()
            raise StoreUnreadable(f"the header does not read: {error!r}") from None
        return OpenStore(header, None, [handle])
    try:
        parts = row_delta.open_parts(out_dir, head, sha256=sha256)
    except row_delta.HeadError as error:
        raise StoreUnreadable(str(error)) from None
    return OpenStore(dict(head.stamps), head, parts.handles)


def _trusted_parts(out_dir: Path, stamps: Mapping) -> set[str]:
    recorded = stamps.get("parts") or {}
    return {
        part
        for part, size in _part_sizes(out_dir, recorded).items()
        if size is not None and size == recorded[part]
    }


def stream_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    wanted: Container[str] | None = None,
    pool: dict | None = None,
) -> Iterator[ParsedCachedUnit] | None:
    """Return the prior build's records one at a time in store order, or None when there is no usable store: absent, unreadable at its head or header, a different format or environment stamp, or stamped for a manifest other than the one on disk. A None costs a full build, which is the safe direction. The head is read and its parts opened before returning, so None is decided before any record is parsed. The body is a generator over the open parts, which it closes after the last record or when a read fails. A body that fails after some records raises `StoreUnreadable` from the generator, and a caller that loaded each record as it came must then discard what it loaded. An error the caller's own load raises is raised in the caller's frame and is never treated as a store failure.

    Each line's key is sliced from the start of the raw line before the line is parsed: `to_record` writes the key first. A line whose key cannot be sliced is skipped, with or without `wanted`, which is the same safe direction. With `wanted`, only records whose key it contains are parsed, so a line the workload does not name costs a prefix comparison and no parse.

    Each record is a `ParsedCachedUnit` with its strings interned and its projection tuples pooled (`_parsed_cached_unit`). Passing `pool` means the caller holds the records together, so every record pools through it. Without it, only the records that have no address share a table, since a streaming caller buffers those for the walk; an addressed record is loaded and released before the next is parsed and pools only within itself, so nothing in the stream grows with the records already yielded. A record keeps its address only while its part has the size the header recorded. A record whose part changed size, or that has no address, comes with `address` None and is placed by the walk, so a shard rewritten under the store is still reused instead of failing at the write.
    """
    try:
        opened = open_store(out_dir)
    except StoreUnreadable:
        return None
    if opened is None:
        return None
    stamps = opened.stamps
    if (
        stamps.get("format") != STORE_FORMAT
        or stamps.get("environment") != _stamp_value(environment)
        or stamps.get("manifest_sha256") != _manifest_stamp(out_dir)
    ):
        opened.close()
        return None
    try:
        trusted = _trusted_parts(out_dir, stamps)
    except _STORE_ERRORS:
        opened.close()
        return None
    return _records(opened, trusted, wanted, pool)


def _records(
    opened: OpenStore, trusted: Container[str], wanted: Container[str] | None, pool: dict | None
) -> Iterator[ParsedCachedUnit]:
    whole = pool is not None
    table: dict = {} if pool is None else pool
    start = len(_KEY_PREFIX)
    try:
        for _place, line in opened.lines():
            if not line.startswith(_KEY_PREFIX):
                continue
            end = line.find(b'"', start)
            if end < 0:
                continue
            if wanted is not None and line[start:end].decode() not in wanted:
                continue
            yield _parsed_cached_unit(json.loads(line), trusted, table, whole)
    except _STORE_ERRORS as exc:
        raise StoreUnreadable(f"the store stopped reading: {exc!r}") from exc
    finally:
        opened.close()


def load_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp | str,
    wanted: Container[str] | None = None,
    pool: dict | None = None,
) -> dict[str, ParsedCachedUnit] | None:
    """Return the prior build's records keyed by input key, or None when there is no usable store. It is `stream_store` collected into a dict, except that a body that fails partway also returns None. The key-slice, `wanted`, interning, and part-size rules are `stream_store`'s. The records are held together here, so all of them pool through `pool`, or through a table made for the call. The build's plan reads the store through `open_store` directly; this function is for callers, such as tests, that want the records in hand."""
    records = stream_store(out_dir, environment, wanted=wanted, pool={} if pool is None else pool)
    if records is None:
        return None
    try:
        return {cached.key: cached for cached in records}
    except StoreUnreadable:
        return None


_PATCHED_SEAM = b', "duplicate_group": '
_POLICY_SEAM = b', "policy_file": '
_ADDRESS_SEAM = b', "address": '
record_id = unit_index.record_id
record_class = unit_index.record_class
record_address = unit_index.record_address


def primary_units_crc(primary_units: list[list]) -> int:
    """Return the CRC-32 a prior record's `primary_units` is kept by (`PriorRecord`), from the field's JSON as `to_record` writes it, or 0 for none."""
    return zlib.crc32(json.dumps(primary_units).encode()) if primary_units else 0


@dataclass(frozen=True, slots=True)
class PriorRecord:
    """What the plan keeps of a prior store record it does not load as a cached unit, so the write can find the prior fragment of a unit it recomputed and decide its class's route (#618's decision 73): the unit's id, the address of its fragment, and the fields the fragment was patched with, its class, duplicate group, exemplar and exemption flags, and its primary units by CRC-32 (`primary_units_crc`). Sliced from the line, which `to_record` writes in a fixed order, without parsing the rest."""

    unit_id: str
    class_id: str
    duplicate_group: str | None
    exemplar: bool
    no_verdict: bool
    primary_units: int
    address: tuple[str, int, int] | None


def prior_record(line: bytes) -> PriorRecord:
    """Slice a store line into a `PriorRecord`. A line in another shape raises ValueError or KeyError."""
    patched = line.index(_PATCHED_SEAM)
    policy = line.index(_POLICY_SEAM, patched)
    fields = json.loads(b"{" + line[patched + 2 : policy] + b"}")
    return PriorRecord(
        record_id(line),
        record_class(line),
        fields["duplicate_group"],
        bool(fields["exemplar"]),
        bool(fields["no_verdict"]),
        primary_units_crc(fields["primary_units"]),
        record_address(line),
    )


def prior_record_or_none(line: bytes) -> PriorRecord | None:
    """Return `prior_record(line)`, or None for a line in another shape, whose unit then has no prior record."""
    try:
        return prior_record(line)
    except ValueError, KeyError, TypeError:
        return None


@dataclass(frozen=True, slots=True)
class ScannedLine:
    """One line of the prior store as the plan scans it (`scan_store`): its place in the materialized order, the ordinal whose input key it names (None when no unit of this build does), the record parsed when the scan parses named lines, and the raw line."""

    index: int
    ordinal: int | None
    cached: ParsedCachedUnit | None
    line: bytes


def scan_store(
    opened: OpenStore, named: Mapping[str, int], *, parse: bool, trusted: Container[str]
) -> Iterator[ScannedLine]:
    """Yield every line of an open store in its materialized order, each matched to this build's units by input key (`named`), and, with `parse`, a named line parsed as `stream_store` parses it, its address kept only in a `trusted` part. Every line is yielded, named or not, so the plan reads every record for its address and its place whatever its stamp. A store that stops reading raises `StoreUnreadable` from the generator, which closes the store either way; an error the caller's own load raises is raised in the caller's frame."""
    start = len(_KEY_PREFIX)
    pool: dict = {}
    try:
        for index, (_place, line) in enumerate(opened.lines()):
            ordinal = (
                named.get(line[start : start + 64].decode("ascii")) if line.startswith(_KEY_PREFIX) else None
            )
            cached = None
            if parse and ordinal is not None:
                cached = _parsed_cached_unit(json.loads(line), trusted, pool, False)
            yield ScannedLine(index, ordinal, cached, line)
    except _STORE_ERRORS as exc:
        raise StoreUnreadable(f"the store stopped reading: {exc!r}") from exc
    finally:
        opened.close()


def materialized_store(
    out_dir: Path,
    *,
    address_of: Callable[[str, tuple[str, int, int] | None], tuple[str, int, int] | None] | None = None,
    stamps: Mapping | None = None,
    sha256: bool = False,
) -> tuple[dict, Iterator[bytes]] | None:
    """Return the store's materialized form, the file a whole write of the same records gives (`write_store`): its header under the head's stamps, with `stamps` laid over them, and its record lines in order, with each record's address replaced by `address_of(unit_id, address)` when given. The corpus's materialized form (`build.materialize_corpus`, `perf_fingerprint`) passes the address each fragment takes in a from-scratch layout, with that layout's manifest identity and part sizes. None when there is no store; a store that does not open raises `StoreUnreadable`."""
    opened = open_store(out_dir, sha256=sha256)
    if opened is None:
        return None
    header = {**opened.stamps, **(stamps or {})}

    def lines() -> Iterator[bytes]:
        try:
            for _place, line in opened.lines():
                if address_of is None:
                    yield line
                    continue
                cut = line.rindex(_ADDRESS_SEAM)
                moved = address_of(record_id(line), record_address(line))
                yield line[:cut] + _ADDRESS_SEAM + json.dumps(
                    list(moved) if moved else None
                ).encode() + b"}\n"
        finally:
            opened.close()

    return header, lines()


def write_materialized_store(target_dir: Path, header: dict, lines: Iterable[bytes]) -> None:
    """Write a materialized store as the single file a store written before the segment format leaves, `unit-cache.ndjson.gz`, byte for byte as a base holds it."""

    def fill(staging: Path) -> None:
        with open(staging, "wb") as handle:
            with gzip.GzipFile(
                filename=_BASE_GZIP_NAME, fileobj=handle, mode="wb", mtime=0, compresslevel=1
            ) as stream:
                _write_lines(stream, header, lines)

    output_sink.replace_output(store_path(target_dir), writer=fill)


@dataclass(frozen=True)
class StorePlan:
    """What the plan learned of the prior store, for the cache write's choice of a head alone, a segment or a new base (`update_store`): whether a store existed, whether it read to its end in this format (`readable`, which the segment route needs, whatever its stamps say, since a segment states this pass's records against the prior ones), its head (for the single file a store written before the segment format leaves, the one the plan put it under, `adopt_single_file`), its materialized record count and its base's row bytes, which the share rule compares a segment with, and whether a stamp line moved that says every record is re-derived (`REDERIVING_LABELS`)."""

    existed: bool = False
    readable: bool = False
    head: row_delta.Head | None = None
    lines: int = 0
    rows_bytes: int = 0
    rederived: bool = False


def rederiving_moved(stamps: Mapping, environment: fingerprint.EnvironmentStamp) -> bool:
    """Whether one of `REDERIVING_LABELS` moved between a store's stamps and `environment`: a structure-stamp move or a letter batch (#618's decision 72). A store whose stamps carry no lines counts as moved."""
    recorded = _recorded_labels(stamps.get("environment_lines"))
    if recorded is None:
        return True
    current = environment.labels
    return any(recorded.get(label) != current.get(label) for label in REDERIVING_LABELS)


@dataclass(frozen=True)
class StoreWrite:
    """What the cache write did: wrote the `head` alone, a `segment`, or a new `base`, with the reason for a base (`first`, no store before; `unusable`, a store that did not read; `rederived`, `fold` and `share`, the format's rules), and the segment's or base's rows and row bytes."""

    outcome: str
    reason: str = ""
    rows: int = 0
    rows_bytes: int = 0

    def note(self) -> str:
        if self.outcome == "head":
            return "head only"
        if self.outcome == "segment":
            return f"a segment of {self.rows:,} rows, {self.rows_bytes / 1e6:.1f} MB"
        return f"a new base ({self.reason}), {self.rows:,} records"


NO_PRIOR_LINE = 0xFFFFFFFF


class _OverHalf(Exception):
    """Raised inside `update_store`'s walk once its segment passes half the base's row bytes, the share rule's limit."""


def _kept(order: Sequence[int], prior_line: Callable[[int], int]) -> bytearray:
    """Return, for each ordinal, whether its record keeps its place: the longest run of this pass's records, in `order`, whose prior lines ascend (a longest increasing subsequence, by patience sorting), so the fewest records move. A pass whose prior lines all ascend keeps every record that has one, which is checked first in one walk."""
    size = max(order, default=-1) + 1
    kept = bytearray(size)
    values = array("I")
    ordinals = array("I")
    for ordinal in order:
        line = prior_line(ordinal)
        if line != NO_PRIOR_LINE:
            values.append(line)
            ordinals.append(ordinal)
    if all(values[index - 1] < values[index] for index in range(1, len(values))):
        for ordinal in ordinals:
            kept[ordinal] = 1
        return kept
    tails: list[int] = []
    tail_at = array("i")
    before = array("i", [-1]) * len(values)
    for index, value in enumerate(values):
        position = bisect.bisect_left(tails, value)
        if position:
            before[index] = tail_at[position - 1]
        if position == len(tails):
            tails.append(value)
            tail_at.append(index)
        else:
            tails[position] = value
            tail_at[position] = index
    index = tail_at[-1] if len(tail_at) else -1
    while index >= 0:
        kept[ordinals[index]] = 1
        index = before[index]
    return kept


def update_store(
    out_dir: Path,
    environment: fingerprint.EnvironmentStamp,
    plan: StorePlan,
    order: Sequence[int],
    *,
    prior_line: Callable[[int], int],
    unchanged: Callable[[int], bool],
    line_of: Callable[[int], bytes],
    parts: Iterable[str],
) -> StoreWrite:
    """Write this pass's store after the manifest: its records are `line_of(ordinal)` for the ordinals in `order`, this pass's triage order, so its materialized form is `write_store`'s file over those lines (#618's decision 48).

    With a prior store that read to its end in this format, which the plan put under a head (`adopt_single_file`) if it had none, it writes only what changed, as one segment of the prior generation. A record is matched to its prior line by input key (`prior_line`, which the plan filled), and `unchanged` says, without building the line, that a record is its prior line (a cached fragment written as it was, at its own address). The base's lines carry no triage key, so each record has a place (`row_delta.Place`): the records that keep theirs are the longest run of them whose prior lines ascend in this order (`_kept`), so the fewest move. A kept record whose line changed is an upsert at its own place; every other record of this pass is an upsert at a place cut between the kept records around it (`row_delta.places_between`); and every prior line no kept record holds is a tombstone at its place, a record no unit of this pass names and the old place of every record that moved. The segment's rows are built in that order, which is place order, and held as columns of ordinals and places (`_SegmentRows`) until the share rule is decided, so the parent holds the changed records' places and not their lines, and each changed line is built twice.

    A new base is written instead, under the format's rules: when no store read before (`first` when none existed, `unusable` when one did, which `output_sink.note_rewritten` counts as `rewritten whole`), when a stamp line says every record is re-derived (`rederived`), when the prior head's fold is due and the store holds the pass's one fold or has waited `row_delta.FOLD_OVERRUN` segments past its cap for it (`fold`, `row_delta.claim_fold`, `row_delta.fold_forced`; a store whose fold another output holds writes its segment), or when the segment would pass half the base's row bytes (`share`, `row_delta.writes_base` over the segment's exact line bytes). With no record changed, it writes the head alone, with this pass's stamps. Before any part is written, the parts no head lists are deleted, and the single file a store written before the segment format leaves is removed once the new head is in place.
    """
    header = store_header(out_dir, environment, parts)
    prior = plan.head
    if plan.existed and not plan.readable:
        prior = _readable_head(out_dir)
    row_delta.remove_unlisted(out_dir, STORE_NAME, prior)
    content = 0 if prior is None else prior.content + 1

    def whole(reason: str) -> StoreWrite:
        if reason == "unusable":
            output_sink.note_rewritten(STORE_NAME)
        lines = (line_of(ordinal) for ordinal in order)
        head = _write_base(out_dir, prior, header, lines, content=content)
        return StoreWrite("base", reason, head.base.rows, head.base.rows_bytes)

    if not plan.readable:
        return whole("unusable" if plan.existed else "first")
    if plan.rederived:
        return whole("rederived")
    opened = open_store(out_dir, sha256=True)
    if opened is None or opened.head != plan.head:
        if opened is not None:
            opened.close()
        return whole("unusable")
    try:
        rows = _changed_rows(opened, plan, order, prior_line, unchanged, line_of)
    except _OverHalf:
        return whole("share")
    finally:
        opened.close()
    if (
        plan.head is not None
        and row_delta.fold_due(plan.head)
        and (row_delta.claim_fold(STORE_NAME) or row_delta.fold_forced(plan.head))
    ):
        if not rows:
            content -= 1
        return whole("fold")
    base = plan.head
    assert base is not None, "the plan puts a store it read under a head"
    if not rows:
        head = row_delta.Head(STORE_NAME, base.generation, base.content, header, base.base, base.segments)
        row_delta.commit(out_dir, head)
        store_path(out_dir).unlink(missing_ok=True)
        return StoreWrite("head")
    part = row_delta.write_segment(
        out_dir, STORE_NAME, base.generation, len(base.segments) + 1, rows.rows(line_of)
    )
    row_delta.commit(out_dir, row_delta.with_segment(base, part, header))
    store_path(out_dir).unlink(missing_ok=True)
    return StoreWrite("segment", "", part.rows, part.rows_bytes)


def adopt_single_file(out_dir: Path, stamps: dict, lines: int, rows_bytes: int) -> row_delta.Head:
    """Put the single file a store written before the segment format leaves under a head, as generation 0's base, and return the head: a hard link to the file, which writes no data, and a head over it whose stamps are the file's inline header (`stamps`) and whose base holds `lines` records of `rows_bytes` bytes, committed at once. The build's plan adopts the file once it has read it to its end, before the write can append to any shard part, so a corpus a build appended to always has a head, without which the live-fragment iterator could not tell its live fragments from its dead slots (`unit_index.LiveCorpus`). The single file stays under its own name until a later head replaces this one (`update_store`), and every reader reads the head first."""
    row_delta.remove_unlisted(out_dir, STORE_NAME, None)
    name = row_delta.base_name(STORE_NAME, 0)
    path = Path(out_dir) / name
    os.link(store_path(out_dir), path)
    base = row_delta.Part(name, path.stat().st_size, row_delta.file_sha256(path), lines, rows_bytes)
    head = row_delta.Head(STORE_NAME, 0, 0, dict(stamps), base)
    row_delta.commit(out_dir, head)
    return head


class _SegmentRows:
    """A segment's rows as `update_store` holds them until the share rule decides, as columns rather than an object a row: each row's operation, its place's base row and fraction (the fractions' bytes end to end, a cut place's few digits and a base place's none), and for an upsert the ordinal whose line it writes, for a tombstone its payload, the prior line's key, in one buffer of them. `CORPUS_PARENT_BYTES`' comment in rebuild/tools/artifact_cycle.py books what they hold: 13 bytes an upsert at its own place, and 114 a unit that takes a tombstone and an upsert at a cut place."""

    def __init__(self) -> None:
        self._ops = bytearray()
        self._anchors = array("i")
        self._fractions = bytearray()
        self._fraction_ends = array("I")
        self._values = array("I")
        self._tombstones = bytearray()
        self._tombstone_ends = array("I")

    def __len__(self) -> int:
        return len(self._ops)

    def upsert(self, place: row_delta.Place, ordinal: int) -> None:
        self._add(row_delta.UPSERT, place, ordinal)

    def tombstone(self, place: row_delta.Place, payload: bytes) -> None:
        self._add(row_delta.TOMBSTONE, place, len(self._tombstone_ends))
        self._tombstones += payload
        self._tombstone_ends.append(len(self._tombstones))

    def _add(self, op: bytes, place: row_delta.Place, value: int) -> None:
        self._ops += op
        self._anchors.append(place[0])
        self._fractions += place[1].encode("ascii")
        self._fraction_ends.append(len(self._fractions))
        self._values.append(value)

    def rows(self, line_of: Callable[[int], bytes]) -> Iterator[row_delta.Row]:
        """Yield the rows in the order they were added, each upsert's line built by `line_of`."""
        start = 0
        for index, op in enumerate(self._ops):
            end = self._fraction_ends[index]
            place = (self._anchors[index], self._fractions[start:end].decode("ascii"))
            start = end
            value = self._values[index]
            if op == row_delta.TOMBSTONE[0]:
                low = self._tombstone_ends[value - 1] if value else 0
                yield row_delta.Row(
                    row_delta.TOMBSTONE, place, bytes(self._tombstones[low : self._tombstone_ends[value]])
                )
            else:
                yield row_delta.Row(row_delta.UPSERT, place, line_of(value))


def _changed_rows(
    opened: OpenStore,
    plan: StorePlan,
    order: Sequence[int],
    prior_line: Callable[[int], int],
    unchanged: Callable[[int], bool],
    line_of: Callable[[int], bytes],
) -> _SegmentRows:
    """Walk this pass's records in `order` beside the prior store's lines in theirs and return the segment's rows in place order (`update_store`). Raises `_OverHalf` once the rows' lines pass half the base's row bytes (`row_delta.writes_base`)."""
    kept = _kept(order, prior_line)
    held = bytearray(plan.lines)
    for ordinal in order:
        if kept[ordinal]:
            held[prior_line(ordinal)] = 1
    rows = _SegmentRows()
    written = 0

    def count(op: bytes, place: row_delta.Place, size: int) -> None:
        nonlocal written
        written += row_delta.row_length(op, place, size)
        if row_delta.writes_base(written, plan.rows_bytes, every_row_rederived=False):
            raise _OverHalf

    prior = enumerate(opened.lines())
    pending: list[int] = []
    previous: row_delta.Place | None = None

    def gap(stop: int | None) -> tuple[row_delta.Place | None, tuple[row_delta.Place, bytes] | None]:
        """Advance the prior lines to line `stop`, tombstoning every one before it that no kept record holds, and return the last place passed and line `stop` itself."""
        last = previous
        for index, (place, line) in prior:
            if index == stop:
                return last, (place, line)
            if not held[index]:
                tombstone = line[: len(_KEY_PREFIX) + 64] + b'"}\n'
                rows.tombstone(place, tombstone)
                count(row_delta.TOMBSTONE, place, len(tombstone))
                last = place
        if stop is not None:
            raise StoreUnreadable(f"the prior store ends before its line {stop}")
        return last, None

    def place_pending(low: row_delta.Place | None, high: row_delta.Place | None) -> None:
        for place, ordinal in zip(row_delta.places_between(low, high, len(pending)), pending, strict=True):
            rows.upsert(place, ordinal)
            count(row_delta.UPSERT, place, len(line_of(ordinal)))
        pending.clear()

    for ordinal in order:
        if not kept[ordinal]:
            pending.append(ordinal)
            continue
        low, found = gap(prior_line(ordinal))
        assert found is not None
        place, line = found
        place_pending(low, place)
        if not unchanged(ordinal):
            new = line_of(ordinal)
            if new != line:
                rows.upsert(place, ordinal)
                count(row_delta.UPSERT, place, len(new))
        previous = place
    low, _found = gap(None)
    place_pending(low, None)
    return rows


def signature_store_path(out_dir: Path) -> Path:
    return Path(out_dir) / SIGNATURE_STORE_NAME


def signature_environment(
    repo_root: Path, before_font: Path, after_helpers_digest: str
) -> fingerprint.EnvironmentStamp:
    """Return the ink-signature store's whole-store stamp: what a signature reads that the per-entry keys do not cover. That is the comparator's code (`signature_code_paths`, the import closure of `rebuild.review.ink`), the before font outside its `head` and `name` tables (`_font_digest`), and the after font's non-family glyphs, cmap, and layout wiring. The code line covers only the comparator's closure, so an edit to the build driver, the review facts, the drafts, the kernel interface, or the crate leaves this store valid while `environment_stamp` invalidates the unit store. The module docstring lists the rest of what is left out. The comparator closure's per-file lines are recorded as the `comparator_code` detail, from the same read the digest is computed from."""
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
    """Write one JSON header line, then one `key\\tdigest` line per entry, sorted by key. The fixed gzip mtime and the sort make consecutive builds of the same inputs byte-identical. The store is rewritten on each build with only the entries the merge needed, so entries for windows that no longer occur are dropped. Compression is level 1, like the unit store: the lines are hex digests, and level 9 takes about four times as long to save a few percent. The file is written under a sibling `.partial` name and renamed at the end, so a build killed during the write leaves the previous store intact. A truncated gzip would read as absent in `load_signature_store` and cost the next pass a full re-shaping. The gzip header records the final file name, since `GzipFile` would otherwise write the staging handle's name into the bytes."""
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
    header: dict | None,
    environment: fingerprint.EnvironmentStamp,
    code_label: str,
    manifest_stamp: str | None = None,
) -> str | None:
    """Return why a load of a store whose header (or head's stamps) is `header` fails under `environment`, as one line, checking in the loader's order: a header that does not read (None), a changed stamp, and, for the unit store, a manifest identity other than the recorded one. A changed stamp is reported through `fingerprint.moved_note`, with the code label expanded to the files that changed inside its closure, at most `CODE_FILES_SHOWN` of them plus a count of the rest. Returns None when the header matches everything checked, which leaves only one reason for the loader to fail: a body that does not parse. A store whose header has the hex but no `environment_lines` reports that the stamp moved and names no label."""
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


def store_stamps(out_dir: Path) -> dict | None:
    """Return the unit store's stamps, the head's, or the inline header of the single file a store written before the segment format leaves, or None when neither reads."""
    try:
        head = row_delta.read_head(out_dir, STORE_NAME)
    except row_delta.HeadError:
        return None
    if head is not None:
        return dict(head.stamps)
    return read_header(store_path(out_dir))


def store_miss_note(out_dir: Path, environment: fingerprint.EnvironmentStamp) -> str | None:
    """Return why `stream_store` rejects the unit store under `out_dir`, or None when its stamps match `environment` and the manifest beside it (`_miss_note`), read through its head."""
    if not row_delta.head_path(out_dir, STORE_NAME).is_file() and not store_path(out_dir).is_file():
        return NO_STORE_NOTE
    return _miss_note(store_stamps(out_dir), environment, "corpus_code", _manifest_stamp(out_dir))


def signature_miss_note(out_dir: Path, environment: fingerprint.EnvironmentStamp) -> str | None:
    """Return why `load_signature_store` rejects the ink-signature store under `out_dir`, or None when its header matches `environment` (`_miss_note`)."""
    path = signature_store_path(out_dir)
    if not path.is_file():
        return NO_STORE_NOTE
    return _miss_note(read_header(path), environment, "comparator_code")


@dataclass(frozen=True, slots=True)
class PriorFragment:
    """The location and stamp of one of the prior corpus's fragments: the shard part it was written to (as the manifest names it), the byte offset and length of its JSON element there, its unit id, and its `content_key`. The address is the same `(part, start, length)` the app's sidecars use for a Range fetch. It comes either from the store record (`ParsedCachedUnit.located`), which recorded what the shard writer returned, or from `locate_prior_fragments`, which reads it off the part's text for a record without one and so stays correct for a hand-edited shard as long as the part is still ASCII. `byte_copied` marks the first case: bytes at a store address have the shard writer's framing and may be copied into the next corpus unchanged, while bytes the walk found may be any JSON and are parsed, patched, and serialized again."""

    part: str
    start: int
    length: int
    unit_id: str
    content_key: str | None
    byte_copied: bool = False


def locate_prior_fragments(out_dir: Path, wanted: Mapping[str, set[str]]) -> dict[str, PriorFragment]:
    """Return where the prior shards hold the given prior unit ids (`wanted` maps class id to ids), keyed by prior id, with the stamp each fragment carries, read through the live-fragment iterator (`unit_index.LiveCorpus`) over the classes `wanted` names, one fragment at a time, so the build never holds the previous corpus's units and never takes a dead slot. The plan uses this only as a fallback: a store record carries the address the shard writer returned, and the walk is needed only for the records `stream_store` yielded without one, from an older store or in a part whose size changed. On a corpus this code wrote there are none. A corpus the iterator refuses, or a part that is missing, unreadable or not pure ASCII, contributes nothing, and its units are recomputed: an address is a character offset read back as a byte offset, which are equal only under `ensure_ascii`, and every part this build writes is ASCII."""
    located: dict[str, PriorFragment] = {}
    try:
        with unit_index.LiveCorpus(Path(out_dir)) as live:
            for meta, slot, fragment in live.fragments(classes=wanted):
                if slot.unit_id in wanted.get(str(meta.get("id")), ()):
                    located[slot.unit_id] = PriorFragment(
                        slot.part, slot.start, slot.length, slot.unit_id, fragment.get("content_key")
                    )
    except OSError, ValueError:
        return located
    return located


class PriorFragmentReader:
    """Reads cached fragments from the prior shards by address, from the store record or from the walk. Each read is one `os.pread` on a descriptor kept open per part, up to `OPEN_PARTS` at once, the oldest closed first, so the write's run of reads from one part opens it once. On the 18-core M5 Pro MacBook Pro (`doc/fleet.md`), reopening the part on each read costs about 16–17 µs more than an `os.pread`, and a seek and read on a buffered handle kept open costs 4–5 µs more. Each read is checked against the address it came from: the element must still be the unit with the stamp the plan reused it under. Otherwise the file changed under this build, and the read raises ValueError. For a store-addressed fragment, this is the only time its bytes are checked against its record."""

    OPEN_PARTS = 64

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = Path(out_dir)
        self._descriptors: dict[str, int] = {}

    def _refusal(self, located: PriorFragment) -> ValueError:
        return ValueError(
            f"{located.part} no longer holds unit {located.unit_id} at bytes "
            f"{located.start}+{located.length}: the prior corpus changed underneath this build"
        )

    def _bytes(self, located: PriorFragment) -> bytes:
        descriptor = self._descriptors.get(located.part)
        if descriptor is None:
            if len(self._descriptors) >= self.OPEN_PARTS:
                os.close(self._descriptors.pop(next(iter(self._descriptors))))
            descriptor = os.open(self._out_dir / located.part, os.O_RDONLY)
            self._descriptors[located.part] = descriptor
        return os.pread(descriptor, located.length, located.start)

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
        """Return the fragment's bytes without parsing them, checked against the address. The shard writer's framing puts each field on its own line as `"key": value`, so the id and stamp the address was recorded with are found as substrings. This catches a fragment edited in place at its address, which the part-size check cannot see, as `read` does, and an address into a dead slot that holds another unit. Only a byte-copied address is checked this way, because only the shard writer's framing is known; a walked address is read with `read`."""
        body = self._bytes(located)
        if located.byte_copied and (
            f'"id": {json.dumps(located.unit_id)}'.encode() not in body
            or f'"content_key": {json.dumps(located.content_key)}'.encode() not in body
        ):
            raise self._refusal(located)
        return body

    def same_bytes(self, located: PriorFragment, body: bytes) -> bool:
        """Return whether the prior corpus holds `body` at `located`: the write's compare of a fragment with its prior (#618's decision 72), and equal bytes are a byte copy. A fragment of another length is not read. Equal bytes hold this unit's own id and stamp, since `body` does, so the compare needs no check of its own: a slot that holds another unit or another stamp, as one the corpus changed under, compares unequal, and the fragment is appended."""
        return located.length == len(body) and self._bytes(located) == body

    def close(self) -> None:
        descriptors, self._descriptors = self._descriptors, {}
        for descriptor in descriptors.values():
            os.close(descriptor)

    def __enter__(self) -> "PriorFragmentReader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
