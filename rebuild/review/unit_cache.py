"""The review corpus build's persisted per-unit cache, as the build's Python side stamps and reads it. The review crate (`rebuild/review-rs/`) keys each unit's products by a content key over the unit's inputs and keeps them in the unit store beside the corpus, so after an edit a build re-enriches only the windows the edit can affect and reuses every other unit from the previous corpus's shards. This module gives the crate what it does not compute: both stores' whole-store stamps, over code closures and fonts (`environment_stamp`, `signature_environment`), and the family keys (`family_content_keys`). It defines the carry content key and the unit ids (`carry_content_hash`, `unit_id_for`), and it reads both stores for the tools and the tests (`open_store`, `materialized_store`, `load_store`, `load_signature_store`).

A unit's products are its ink diffs and machine-approval flags, its enrichment (cells, junctions, highlights, explain, provenance), and its three drafts. A machine-approved or verdict-exempt unit is written as a slim fragment (`audit.slim_fragment`), which omits the highlight, the explain, and the drafts. The products are a pure function of the unit's inputs, and the cache is correct only if the content key covers all of those inputs.

The key has two levels. The per-unit key covers the unit's audit rows, which fix the window, its configs, the kinds, the matched ledger class, and both fonts' rendered names, plus one digest per window family (`family_content_keys`). A family digest combines the family's explain-aware rune digest, the rune digests of its static `resolve.against` closure, and a digest of the after font's compiled glyphs for the family (outlines, advances, and cursive anchors), so a drawing or anchor change invalidates a unit even when no name in its rows changes. A ligature family is included when all of its components are in the window.

The whole-store stamp (`environment_stamp`) covers everything that can change a unit's products without changing a family digest: the code the corpus build runs (`corpus_code_paths`, and the review crate as its `units_engine` line), the non-rune data files, the kernel's settlement mode flags, the resolved spec structure and capability-feature set (cross-rune routes: predicate-class and group memberships, ligature sequences, and the formation guard's feature combinations), the before and Junior fonts outside their `head` and `name` tables (`_font_digest`), the acceptance configs' subset tables, the site corpus pages whose pins set a pin draft's `attribute` and `duplicate_of` and the draft harness that checks every pin draft (`fingerprint.draft_input_lines`, which the corpus manifest's Stage B `draft_inputs` component hashes too), and the after font's helper glyphs and the cmap entries a Quikscript run reaches (`fingerprint.RUNE_HELPER_GLYPHS`), and GPOS wiring. `corpus_code_paths` is the review modules the build imports, the pipeline and validation modules they reach, and the kernel crate's modules the corpus build runs. rebuild/test_review_code_closure.py checks these lists against the import graph. So an edit to the pipeline driver, a gate, the oracle or its row cache, the conformance sweep, the GSUB emitter, the pixel geometry, the pipeline's font-compile driver (rebuild/pipeline/compile_font.py), or the kernel crate's enumeration and fold leaves the store valid.

Four inputs are outside the whole-store stamp. The after font's GSUB wiring is outside every stamp; `fingerprint.after_font_glyph_digests` explains why a window's glyph selection is covered without it. The divergence ledger reaches the shards through the audit's `matched_entry` column, which is in the rows, through its `ink_identical` ids, which decide which units take a change group instead of their class and are a stamp line of their own, and through fields the build recomputes and patches on every pass (`no_verdict`, `exemplar`, class promotion), so a ledger edit that leaves those ids alone invalidates only the units whose rows it changed. The refuse `why` text the explain panel quotes is in the family keys instead, so rewording one re-enriches only the windows that contain that family. The change groups' copy (rebuild/review/change-groups.yaml) reaches only the manifest, which every build writes afresh, so rewording a group re-enriches no unit.

A store record is one JSON line keyed by the unit's input key. Among its fields are the unit's id and class and the address of its fragment: the shard part, byte offset, and length on disk the crate's shard writer returned, and for a fragment written deflated (#618's change 35) its inflated length as a fourth element (decisions 76 and 77), which `record_id` and `record_address` slice from the line without parsing it. The store is held in the segment format (`rebuild/pipeline/row_delta.py`): a head, `unit-cache.ndjson.gz.head`, which carries the store's header as its stamps and lists a base, the single file a whole write gives (`write_store`), and the segments later builds wrote over it. The store's materialized form (`materialized_store`) is the file a whole write of the same records gives, byte for byte. A store written before the segment format, the single file `unit-cache.ndjson.gz`, is read as a base with no head.

Both stores record their whole-store stamp in two forms (`_stamp_header`): the hex digest a load compares, and the `fingerprint.EnvironmentStamp` lines the digest is computed from, with the code label's per-file lines beside them. The build input carries both to the crate, whose miss notes name which of four causes applied when it rejects a store: no store (`NO_STORE_NOTE`), a store that does not read (`UNREADABLE_NOTE`), a manifest that changed (`MANIFEST_MOVED_NOTE`), or a stamp line that changed, a code line narrowed to the changed files inside its closure.

This module also defines the carry content key (`carry_projection`, `carry_content_hash`), so the build and the carry share one definition. The build stamps each fragment's `content_key` with it when it drafts the fragment, and the unit's id is derived from that stamp (`unit_id_for`), so rebuild/tools/carry_verdicts.py moves each prior verdict to the unit with the same id. The stamp is excluded from the projection it hashes.

Beside the per-unit store is the ink-signature store, which does for the ink-duplicate merge what the unit store does for enrichment. The merge needs one rendered-outcome signature per (window, config) over every window split only by glyph names. It runs before the set of units exists, so the unit store cannot supply these signatures. Each entry's key follows the same two-level argument as the unit key: the audit row fixes the window, the config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, and the per-family digests fix the after font's outlines, advances, and cursive anchors for every family the window can touch. The crate takes each signature from both fonts' shaped outlines, as the font facts carry them (`units_exec.write_font_facts`). The whole-store stamp (`signature_environment`) covers what signatures depend on beyond the key: the code that reads the outlines into the font facts (`signature_code_paths`), the crate (`units_exec.crate_digest`), the before font outside its `head` and `name` tables, and the after font's helper glyphs and the cmap entries a Quikscript run reaches (`fingerprint.RUNE_HELPER_GLYPHS`), and GPOS wiring. The rest of `corpus_code_paths` is left out (the build driver, this module, the kernel interface), because computing a signature runs none of it, and so are the ledger, the subset tables, the Junior font, the site corpus files, and the draft harness, which signatures read none of. So this store stays valid through edits that invalidate the unit store, and a build that re-enriches every unit can still skip re-shaping for the merge. The store's header carries two stamps beside its own, the audit's identity (`audit_identity`) and the family keys' digest (`family_keys_digest`): when both equal the build's and the store's stamp holds, no key can miss, and the build skips its key-only read of the audit.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from rebuild.pipeline import fingerprint, kernel_exec, output_sink, row_delta, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import unit_index
from rebuild.review.audit import ACCEPTANCE_CONFIGS

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
# Every two-symbol string indexed by its value, so `base58_64` spells two symbols a division: the leading symbol and five pairs make `ID_SYMBOLS`.
_PAIR_BASE = 58 * 58
_BASE58_PAIRS = tuple(high + low for high in BASE58_ALPHABET for low in BASE58_ALPHABET)


def base58_64(digest_hex: str) -> str:
    """Return the first 64 bits of a hex digest as `ID_SYMBOLS` base58 symbols, most significant first, padded with the zero symbol `1`. The width is fixed and the alphabet is in ascending ASCII order, so the strings sort in the same order as the numbers. Case is significant."""
    pairs = _BASE58_PAIRS
    value, p0 = divmod(int(digest_hex[:16], 16), _PAIR_BASE)
    value, p1 = divmod(value, _PAIR_BASE)
    value, p2 = divmod(value, _PAIR_BASE)
    value, p3 = divmod(value, _PAIR_BASE)
    value, p4 = divmod(value, _PAIR_BASE)
    return BASE58_ALPHABET[value] + pairs[p4] + pairs[p3] + pairs[p2] + pairs[p1] + pairs[p0]


def unit_id_for(content_key: str) -> str:
    """Return a unit's id: `u-` followed by the first 64 bits of its carry content key in base58, for example `u-3mJ7kPq2Xw9`. The content key is the sha256 of `carry_projection`, the stamp every m1-audit fragment carries. Two units with the same projection have the same id. The id does not depend on the unit's position in the queue or on which other units exist, so a cached fragment keeps its bytes across builds and a verdict follows its unit across corpora."""
    return "u-" + base58_64(content_key)


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
    """Return the manifest's identity digest for the unit store's stamp, or the sentinel `missing` when there is no manifest to read, as on a first build. With the sentinel, `store_header` records a missing manifest, and `load_store` treats it as a stamp mismatch instead of raising."""
    try:
        return unit_index.manifest_sha256(Path(out_dir))
    except OSError:
        return "missing"


# The rebuild/pipeline modules the corpus build never imports, such as the driver, its gates, the oracle, the conformance sweep, the emitters, the font compile and the Mono and Junior build, the explain renderer the crate ports, and the test fixtures. rebuild/test_review_code_closure.py checks this set against the import closure of build.py and of the modules whose constants the crate reads (`corpus_exec.VOCABULARY`, `units_exec.VOCABULARY`), in both directions. The build reads the vocabulary it shares with the sweep (the alphabet, a configuration's features, a formed stream's labels, the boundary names, and the alias map) from `labels.py`, which imports none of these modules. This is an exclusion list, so a new pipeline module is hashed into `corpus_code_paths` until the test shows the build never reaches it and it is added here.
PIPELINE_NON_CORPUS_MODULES = frozenset(
    {
        "build_family.py",
        "compile_font.py",
        "conform.py",
        "coretext_smoke.py",
        "defects.py",
        "emit_gpos.py",
        "emit_gsub.py",
        "explain.py",
        "fixtures.py",
        "geometry.py",
        "glyph_facts.py",
        "kern.py",
        "kern_inventory.py",
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

# The rebuild/validation modules the corpus build never imports: the old-font pin replay, which the crate ports for the pin drafts' check, and the corpus collector and the checker, which the suite and the pin gate run. rebuild/test_review_code_closure.py checks this set as it checks `PIPELINE_NON_CORPUS_MODULES`.
VALIDATION_NON_CORPUS_MODULES = frozenset({"check.py", "corpus.py", "pins.py"})

# The kernel crate modules the corpus build never runs. The review crate links the kernel library and runs the modules it names (`ams_m1_kernel::<module>`) in process, with their `crate::` closure, and the `settle-cases` and `guard-sweep` subcommands reach every one of those too, so every module those two handlers in main.rs reach stays hashed, with main.rs and lib.rs. rebuild/test_review_code_closure.py checks this exclusion list against the `crate::` references the two handlers reach outside the crate's test modules, and checks that the review crate's own reach is hashed.
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
    """Return the code whose edit invalidates the per-unit store: the code the corpus build runs. The review side is `fingerprint.review_code_paths`, which is checked against the import graph of build.py and of the modules whose constants the crate reads. The pipeline side is rebuild/pipeline minus `PIPELINE_NON_CORPUS_MODULES`, plus rebuild/validation minus `VALIDATION_NON_CORPUS_MODULES`. The kernel crate side is rebuild/kernel-rs/src minus `KERNEL_NON_CORPUS_MODULES`, plus Cargo.toml and Cargo.lock, since the crate's dependencies and build profile affect every subcommand. The review crate is the stamp's `units_engine` line, not a path here. The ink-signature store uses the narrower `signature_code_paths`, so an edit here outside the font facts' readers re-enriches units and re-shapes nothing.

    The lists work at module granularity, which over-invalidates in the safe direction: a module imported for something the build never calls is still hashed. The cached-versus-recomputed sample in every build checks that a cached fragment equals a recomputation. One set of modules is left out: the fan-out width and telemetry modules under rebuild/tools cannot change a unit's products, rebuild/test_unit_cache.py's serial-versus-parallel byte identity covers the width, and rebuild/test_review_code_closure.py checks that these are the only modules the build reaches outside the three trees. The build reaches no tools/ module (rebuild/test_build_code_closure.py), and the draft harness, the `data-expect` grammar in rebuild/validation, is hashed here and again, with the glyph-name map it reads, by the draft harness line of `environment_stamp` (`fingerprint.draft_harness_paths`).
    """
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    pipeline = [
        path
        for path in sorted((root / "rebuild" / "pipeline").glob("*.py"))
        if path.name not in PIPELINE_NON_CORPUS_MODULES
    ]
    validation = [
        path
        for path in sorted((root / "rebuild" / "validation").glob("*.py"))
        if path.name not in VALIDATION_NON_CORPUS_MODULES
    ]
    crate = [kernel / "Cargo.toml", kernel / "Cargo.lock"] + [
        path for path in sorted((kernel / "src").rglob("*.rs")) if path.name not in KERNEL_NON_CORPUS_MODULES
    ]
    return pipeline + validation + crate + fingerprint.review_code_paths(root)


# The code that reads the fonts' outlines into the font facts the crate takes each signature from: the facts' writer, rebuild/review/units_exec.py (`units_exec.font_facts`), and the import closure of rebuild/review/ink.py, whose `OutlineCache` and `OutlineIntern` give each glyph's shape key and cells, with the rebuild/validation modules it reaches (the shaper, the junction classifier, whose exit and entry sets the facts carry and from which ink.py takes `PIXEL_SIZE`, and the row model the shaper imports). It is an inclusion list of literal paths because the walk from ink.py reaches only those files and no package `__init__.py`, and the writer's own imports reach most of the build. rebuild/test_review_code_closure.py walks the import graph from `rebuild.review.ink` in both directions, checks the writer beside it, and checks that each entry exists, because `fingerprint.path_lines` skips a missing path without error, so a renamed module would silently drop out of the hash.
SIGNATURE_CODE_MODULES = (
    "rebuild/review/ink.py",
    "rebuild/review/units_exec.py",
    "rebuild/validation/classify.py",
    "rebuild/validation/rowmodel.py",
    "rebuild/validation/shaping.py",
)


def signature_code_paths(repo_root: Path) -> list[Path]:
    """Return the Python code whose edit invalidates the ink-signature store (`SIGNATURE_CODE_MODULES`), a subset of `corpus_code_paths`: the code that reads both fonts' outlines into the font facts the crate takes each signature from. Nothing else the corpus build runs in Python can change a signature, so an edit elsewhere re-enriches units and re-shapes nothing. The shaper and the row model are included because ink.py imports them, although no signature runs either.

    Some inputs are outside this hash. A signature is taken over the text a window's code points spell, its `chr` join, so a change to that join changes what a signature means. The join is versioned by `SIGNATURE_STORE_FORMAT`, as are the key layout and the store's file format. A change to the key layout alone cannot supply a wrong digest, because every row gets a new key and misses; a change to the join without a format bump would. The uharfbuzz and fontTools versions are in no stamp, for this store or the unit store.
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
    units_engine: str,
) -> fingerprint.EnvironmentStamp:
    """Return the unit store's whole-store stamp. A change to any of its lines invalidates the whole store, and over-invalidation is the safe direction. The code line is `corpus_code_paths`, so a pipeline or crate edit outside the code the build runs leaves the store valid. The two site fonts are hashed through `_font_digest`, which ignores `head` and `name`, so the fonts a version bump rebuilds leave it valid too. The rune files are left out because the family keys cover them per unit, and the divergence ledger is left out for the reason the module docstring gives, except its `ink_identical` ids (`identical_classes`), sorted, which decide a re-routed unit's class, cluster and fragment. The other data inputs are hashed by `fingerprint.data_path_lines`, so a schema hover or a comment in one of them leaves the store valid. The stamp carries the code closure's per-file lines as its `corpus_code` detail, from the same `path_lines` read the code digest is computed from, so the file a miss note names is the file the digest saw. `subset_digests` is each acceptance configuration's subset table, already hashed by the caller; the build hashes them once for this line and for the subset pack's header. When it is None, the tables are hashed here. The `site_corpus` and `draft_harness` lines are `fingerprint.draft_input_lines`, the lines the manifest's `draft_inputs` component is the digest of. `draft_lines` is those lines when the caller has already taken them, as the build does from its read of the pages; when it is None, they are taken here. `units_engine` is the review crate's digest (`units_exec.crate_digest`), the `units_engine` line, so a crate edit invalidates the store. While the alphabet switch is set, the lines end with its token (`spec_load.alphabet_lines`), so a store built over the whole alphabet never serves a small one, nor the reverse; with it unset no line is added."""
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
        f"units_engine\t{units_engine}",
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


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _part_sizes(out_dir: Path, parts: Iterable[str]) -> dict[str, int | None]:
    return {part: _file_size(Path(out_dir) / part) for part in sorted(set(parts))}


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
    """Return the store's header for a write: its format, the stamp (`_stamp_header`), the manifest's identity (`unit_index.manifest_sha256`, which leaves out the copied UI assets' component) and the byte size of every shard part in `parts`. The manifest stamp shows the store describes the shards beside it, and an assets refresh that rewrites only that component leaves the store current. The manifest stamp says nothing about the shards' bytes, so a reader compares each part's current size with the recorded one, at one stat per part, and drops the addresses into a part whose size changed. Equal sizes do not prove the addresses are still right, since a rewrite can swap two fragments of equal length; the build catches that case by checking the id and stamp at each address it reads back. The head carries this header as its stamps (`row_delta`), and a base carries it inline."""
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
    lines: Iterable[bytes],
    parts: Iterable[str],
) -> row_delta.Head:
    """Write a unit store whole, as the crate's whole write does: a new base, holding a header (`store_header`, which records `parts`) and each record's line, as the next generation under a new head, which is committed last (`row_delta`). The previous head and its parts stay readable until the new head is in place. This is the materialized form of the store for these records. The tools' and the tests' corpora write their stores with it."""
    header = store_header(out_dir, environment, parts)
    prior = _readable_head(out_dir)
    row_delta.remove_unlisted(out_dir, STORE_NAME, prior)
    return _write_base(out_dir, prior, header, lines, content=0 if prior is None else prior.content + 1)


class StoreUnreadable(Exception):
    """Raised when a store that exists cannot be read: a head that does not parse or whose parts are missing or differ from it, or a header that does not read. `load_store` catches it and returns None."""


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


def load_store(out_dir: Path, environment: fingerprint.EnvironmentStamp | str) -> dict[str, dict] | None:
    """Return the store's records keyed by input key, each parsed whole, or None when there is no usable store: absent, unreadable, a different format or environment stamp, stamped for a manifest other than the one on disk, or a body that stops reading partway. A None costs the build a full recompute, which is the safe direction. This is for callers, such as tests, that want the records in hand."""
    try:
        opened = open_store(out_dir)
    except StoreUnreadable:
        return None
    if opened is None:
        return None
    try:
        stamps = opened.stamps
        if (
            stamps.get("format") != STORE_FORMAT
            or stamps.get("environment") != _stamp_value(environment)
            or stamps.get("manifest_sha256") != _manifest_stamp(out_dir)
        ):
            return None
        records = {}
        for _place, line in opened.lines():
            record = json.loads(line)
            records[record["key"]] = record
        return records
    except _STORE_ERRORS:
        return None
    finally:
        opened.close()


_ADDRESS_SEAM = b', "address": '
record_id = unit_index.record_id
record_address = unit_index.record_address


def materialized_store(
    out_dir: Path,
    *,
    address_of: Callable[[str, tuple[str, int, int, int] | None], tuple[str, int, int] | None] | None = None,
    stamps: Mapping | None = None,
    sha256: bool = False,
) -> tuple[dict, Iterator[bytes]] | None:
    """Return the store's materialized form, the file a whole write of the same records gives (`write_store`): its header under the head's stamps, with `stamps` laid over them, and its record lines in order, with each record's address, as `record_address` reads it, replaced by the three-element `address_of(unit_id, address)` when given. The corpus's materialized form (`build.materialize_corpus`, `perf_fingerprint`) passes the address each fragment takes in a from-scratch layout, plain, with that layout's manifest identity and part sizes, so no record there carries a deflated fragment's fourth element (#618's decision 48). None when there is no store; a store that does not open raises `StoreUnreadable`."""
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


def signature_store_path(out_dir: Path) -> Path:
    return Path(out_dir) / SIGNATURE_STORE_NAME


def signature_environment(
    repo_root: Path, before_font: Path, after_helpers_digest: str, crate_code: str
) -> fingerprint.EnvironmentStamp:
    """Return the ink-signature store's whole-store stamp: what a signature reads that the per-entry keys do not cover. That is the code that reads the outlines into the font facts (`signature_code_paths`, the `comparator_code` line), the before font outside its `head` and `name` tables (`_font_digest`), the after font's helper glyphs and the cmap entries a Quikscript run reaches (`fingerprint.RUNE_HELPER_GLYPHS`), and layout wiring, and the review crate, which shapes the windows and takes the signatures (`crate_code`, its digest, `units_exec.crate_digest`). The code line covers only the font facts' readers, so an edit to the build driver, the review facts, or the kernel interface leaves this store valid while `environment_stamp` invalidates the unit store. The module docstring lists the rest of what is left out. The code line's per-file lines are recorded as the `comparator_code` detail, from the same read the digest is computed from."""
    root = Path(repo_root)
    code_lines = fingerprint.path_lines(root, signature_code_paths(root))
    lines = (
        f"format\t{SIGNATURE_STORE_FORMAT}",
        f"comparator_code\t{fingerprint.digest_lines(code_lines)}",
        f"before_font\t{_font_digest(Path(before_font))}",
        f"after_helpers\t{after_helpers_digest}",
        f"crate_code\t{crate_code}",
    )
    return fingerprint.EnvironmentStamp(lines=lines, detail=(("comparator_code", tuple(code_lines)),))


def load_signature_store(
    out_dir: Path, environment: fingerprint.EnvironmentStamp | str
) -> dict[str, str] | None:
    """Return the prior build's signature digests keyed by content key, or None when there is no usable store: absent, unreadable, or a different format or environment stamp. A None costs one re-shaping pass, so over-invalidation is the safe direction here too. The build reads the store in the crate; this dict is for the tests and the tools that want the entries in hand."""
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


def audit_identity(descriptor: int, head: row_delta.Head | None = None) -> list[int]:
    """The audit's identity as the signature store's header records it. Under a head, the head's content generation and the time its content last moved (`row_delta.CONTENT_TIME_STAMP`): run_m1's oracle moves both when it writes a segment or a base whose rows differ from what the head served, and neither on a head-alone write or a fold (`Head.identity`), so a fold keeps the store's stamps holding. For a tree with no head, the held descriptor's `fstat`, `(st_dev, st_ino, st_size, st_mtime_ns)`: a single file is only ever replaced by rename, which moves the inode. Either stands for the content at the cost of no read, where a content digest would read the whole audit."""
    if head is not None:
        return [head.content, int(head.stamps[row_delta.CONTENT_TIME_STAMP])]
    stat = os.fstat(descriptor)
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def family_keys_digest(family_keys: Mapping[str, str]) -> str:
    """The family keys' digest as the signature store's header records it: the sha256 over the sorted `name\\tkey` lines."""
    return hashlib.sha256(
        "".join(f"{name}\t{family_keys[name]}\n" for name in sorted(family_keys)).encode()
    ).hexdigest()


# The miss notes' fixed texts, which the crate reads through the build input's vocabulary (`corpus_exec.VOCABULARY`).
NO_STORE_NOTE = "no store on disk"
UNREADABLE_NOTE = "the store will not read"
MANIFEST_MOVED_NOTE = "the manifest it describes moved"


def read_header(path: Path) -> dict | None:
    """Return a store's header line, or None when none can be read. Both stores start with one JSON header line."""
    try:
        with gzip.open(Path(path), "rb") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration:
        return None
    return header if isinstance(header, dict) else None


def store_stamps(out_dir: Path) -> dict | None:
    """Return the unit store's stamps, the head's, or the inline header of the single file a store written before the segment format leaves, or None when neither reads."""
    try:
        head = row_delta.read_head(out_dir, STORE_NAME)
    except row_delta.HeadError:
        return None
    if head is not None:
        return dict(head.stamps)
    return read_header(store_path(out_dir))


_ID_PREFIX = "u-"
_BASE58_INDEX = {symbol: index for index, symbol in enumerate(BASE58_ALPHABET)}


def id_word(unit_id: str) -> int | None:
    """The 64-bit word a unit id encodes (`unit_id_for`), or None for a string of another shape, which no content-derived id has: the ids and their words correspond one to one, so a word stands for its id."""
    if len(unit_id) != len(_ID_PREFIX) + ID_SYMBOLS or not unit_id.startswith(_ID_PREFIX):
        return None
    value = 0
    for symbol in unit_id[len(_ID_PREFIX) :]:
        index = _BASE58_INDEX.get(symbol)
        if index is None:
            return None
        value = value * 58 + index
    return None if value >> 64 else value
