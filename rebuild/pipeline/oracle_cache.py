"""Stores the baseline oracle's per-row verdicts between runs, keyed per rune family. After a rune edit, only the subset rows that can reach an edited family are compared again; the rest are served from the previous pass's store. A row's position verdict also outlives a move of the whole-store stamp wherever the row's settled stream did not change.

Each row has two verdicts, each under its own key. The row verdict is `conform._compare_row`'s result, a `DivergentRow` or None. `_compare_row(spec, aliases, config, features, row, settled)` takes no font and no shaper, so the row key covers no font: `M1.otf`, the GSUB fold and `glyph_data/senior_quikscript_kerning.yaml` are outside it, and a rune edit serves every row that reaches no edited family. The position verdict is `oracle_positions._position_mismatch`'s result for the same row: the mismatch descriptions and the kern-attribution flag, or None. Its per-family key (`position_family_keys`) adds the after font's compiled-glyph digest for that family (`fingerprint.after_font_glyph_digests`: decomposed outlines, advances, cursive anchors) to the row key. Its whole-store stamp (`position_keys`) covers `POSITION_CODE_PATHS`, the toolchain lock that pins uharfbuzz and fontTools, the font's non-family glyphs, cmap and GPOS wiring, and the kern sidecar. `_position_mismatch(shaper, kern, features, row)` takes no settled stream, so the position key needs no settlement input beyond the row key it contains. Each record also keeps the digest of the settled stream both of its verdicts were derived against (`oracle_positions.settled_digest`), which keys the position verdict when the row key cannot.

The after font's GSUB wiring is in neither key, for the reason `fingerprint.after_font_glyph_digests` gives: a rune edit changes the lookup list on nearly every cycle, and the glyphs a row shapes to are already covered. A position is served only while its settled cells are unchanged: either the row's key and the row stamp are unchanged, or the row was walked again and its settled stream has the digest its record holds. `gate:conform` checks every cycle that the compiled font selects the settlement's cells.

The position comparison adds `"position"` to a row's `kinds` and `"position-mismatch"`, and optionally `"position-kern-attributable"`, to its `divergence_tags`, and changes nothing else, so a served row verdict and a fresh one enter the position comparison in the same state. The position verdict is stored before the ledger decides whether the row goes through the position comparison. A row the previous pass never shaped is recorded as `UNSHAPED` and is shaped when a ledger edit makes it eligible, and a shaped row's position verdict is written forward even when this pass's ledger excludes the row. If any of the facts above stops holding, the cache serves wrong verdicts without any error, and `STORE_FORMAT` must change.

Classification is outside both keys because it always runs: `classify_divergence` and `_match_compiled` run over every row on every pass, served or fresh, so `rebuild/m1-divergences.yaml` and every ledger predicate are applied again to the served verdict. A ledger-only edit therefore serves every row and still rewrites every `matched_entry`; this is the `run_m1 --gates-only` workflow. The classifier's code is outside both stamps for the same reason. It lives in `rebuild/pipeline/oracle.py`, which neither `conform.py` nor `oracle_positions.py` imports, so a classifier edit serves every row verdict and every position verdict. The producer (`_compare_row`, `_cell_deltas`, the walk, and the record codec) is in `conform.py`, which is always in `ORACLE_ROW_CODE_PATHS`. `rebuild/test_build_code_closure.py` checks that the roster names no comparison-side module.

The staleness test is per family and has no threshold. A row is served when no family it can reach has a key different from the one recorded for it. A row reaches the families of its codepoints plus every ligature rune whose components all appear among them. The ligature clause is needed because `rebuild/script.yaml` declares the ligature runes with a `sequence:` and no codepoint, yet `settle.form_ligatures` routes a `qsTea qsOy` window through `qsTea_qsOy.yaml`. A family key (`family_keys`) combines the family's prose-insensitive rune digest, the digests of the runes in its `spec_load.rune_closure`, and the alias map's entries for that family. A family present in only one of the recorded and current key maps counts as moved (`moved_families`).

The row key is not `review.unit_cache.family_content_keys`. That key includes a `glyphs` line over the after font's compiled outlines, which suits a cache of rendered review cards. The row verdict never reads a font, so that line would drop row verdicts on every font change. The position key includes the same per-family glyph digest because its verdict is shaped through that font, so a glyph edit re-shapes the rows that reach the family and re-derives none. `rebuild/review/` imports `rebuild/pipeline/` and not the reverse, so the parts both caches use (`fingerprint.rune_digests` here and `fingerprint.rune_explain_digests` in the unit cache, `fingerprint.after_font_glyph_digests`, `spec_load.rune_closure`, `spec_load.capability_features`, `spec_load.spec_structure_digest`) live in the pipeline.

The row store's whole-store stamp (`environment_stamp`) leaves out `M1.otf` and the kern sidecar, which the position stamp covers; `rebuild/m1-divergences.yaml`, because classification always runs again; `rebuild/m1-contact-allow.yaml`, which no oracle stage reads and which is not in `fingerprint.data_paths`; and the rune files, which the per-family keys cover. It covers everything that can change a verdict without changing a named family's key: the comparison's code closure, the other data inputs, the resolved spec structure and the capability-feature set, the engine's settlement flags, the configuration's feature set, and the subset table's bytes. The spec structure and the capability features cover cross-rune routes that the per-family keys cannot split: a predicate class gaining a member, a rune-local group, a ligature sequence, a feature unlock. For example, the kernel's specificity order (`rebuild/kernel-rs/src/specificity.rs`) expands a class reference to its whole member set before it compares records, so a rune joining a class can change the result in a window that contains no such rune. A store whose position stamp alone moved serves rows and re-shapes positions; one whose row stamp moved is the next paragraph's case.

A position verdict outlives a move of the row stamp, which drops every row verdict. `load_store` still loads a store whose row stamp differs only outside `POSITION_ROW_STAMP_LABELS`, with every row stale, when its position stamp holds and the pass has a font to shape against, and a row the walk settles again keeps its stored position when its fresh settled digest equals the record's, no family it reaches moved its position key, the position stamp holds, and `position_due` does not (`RowStore.position_servable`). The verdict is `_position_mismatch(shaper, kern, features, row)`, and each of its inputs is covered without the row stamp:

- The row, its baseline glyphs, positions and clusters: the `subset` line and the header's subset digest pin the table, so the ordinal still names the row, and the row check digest checks it on every read.
- `features`: the `config` and `features` lines, which must hold, with `format`, for the store to load.
- The glyphs HarfBuzz selects for the row: while `gate:conform` passes they are a function of the row's settled cells, and the settled digest names those cells slot by slot, with each junction and extension and with which of the two shapers the configuration uses.
- Those glyphs' outlines, advances and cursive anchors: the position keys of every family the row reaches. They embed the row keys, so a rune edit still re-shapes the rows that reach its family.
- The rest of the font apart from GSUB, the kern sidecar, the toolchain and the position comparison's code: the position stamp. `POSITION_CODE_PATHS` is the position comparison's import closure, so its `position_code` line covers that code without the row stamp.

The row stamp's other lines reach a position only through settlement, which the settled digest covers: the crate, the code of the walk and of this module (the part of `ORACLE_ROW_CODE_PATHS` outside the position closure), the data inputs, the spec structure, the capability features and the settlement flags. Its `alias_boundary` line is read by the row comparison alone. So a crate edit, a pipeline edit outside the position closure, or a predicate class gaining a member re-derives every row and re-shapes only the rows whose settled cells changed. A store whose `subset` line moved, which every letter addition does, still does not load: its records would have to be matched to the new table's rows by codepoints instead of by ordinal, and a letter addition also moves the position keys of every neighbor whose rune it edits.

Keying by settled window instead of by row was measured and rejected. A settled window spans six slots and a row has at most four letters, so windows name more letters than rows (287,280 of 499,989 distinct windows name four letters). With four edited runes, window keys served 45.4% of the work against row keys' 48.6%. Window keys cover only settlement, not the comparison, and a window's left slot is keyed on the previous window's output, so an edit changes keys downstream and produces misses. Added on top of a row store, window keys served 1.40% more lookups.

Records are positional and carry no row key: the subset table is the complete product over the M1 alphabet in canonical order, so the ordinal is the key. Each record starts with a row check digest (`row_check_digest`), a digest prefix of its row's codepoints that is checked on every serve, and then the settled digest. A store whose alignment was checked only by a whole-file digest and a row count would serve every row wrong with no error if the table changed under it; the row check digest makes that an abort. `baseline_glyphs`, `baseline_junctions` and `codepoints` are read from the table instead of stored, to keep the store small.

The stores of every configuration the oracle compares are held as one set under one head, `oracle-rows.head` (`SetHead`, in `rebuild/pipeline/row_delta.py`'s segment format), which commits them all or none. For each configuration it records every field its whole file's header carries (`store_header`'s inputs, `StoreEntry.header`), its row count, which the whole file's trailer counted, its cut (the members the pass that last wrote the stores ran, or a whole member), and its parts: a base, `oracle-rows-<config>.tsv.gz.<gen>.base`, the configuration's whole file byte for byte with its inline header and ages, as `RowWriter` writes one member for a whole configuration and `join_store_segments` joins the members of a cut one, and segments, `.<gen>.<n>.delta`, each gzipped lines of whole records keyed by ordinal (`row_delta.MERGE_ORDINAL`). Beside them the head lists one ages column, `oracle-rows.<generation>.ages` (`ages_name`), a signed byte a verdict, the row's and the position's, for every configuration's rows in ordinal order, written whole on every pass that writes stores and naming the generation it indexes; its ages override every record's inline ones. A pass writes a segment of the records whose line less its two ages differs from the prior's at its ordinal, so a record re-derived to the same verdict and settled digest moves only its ages (`StoreSet`). A configuration's materialized store (`materialized_store`) is that whole file over the cut the head records, and `load_store` reads a range through the head (`_load_from_head`), or the configuration's single file where a directory has no head.

Two mechanisms stop a wrong record from being served indefinitely. They are needed because a served record is written again under the current stamp, so its provenance never ages it out, and `gate:conform` checks the font against a fresh settlement but never compares a cached verdict with a fresh one. First, each record keeps the pass at which each of its two verdicts was derived, not the pass that last wrote it, and `RowStore.due` and `RowStore.position_due` force a re-derivation once that age reaches `MAX_RECORD_AGE`. The scheduled re-derivation is spread by row ordinal, so one row in `MAX_RECORD_AGE` re-derives on every pass that writes a store instead of the whole table on one pass. A pass that writes no store, such as `--gates-only`, keeps nothing it re-derives, so it skips the scheduled re-derivation and still re-derives a row recorded at `UNCOVERED_AGE` or past the cap (`RowStore`). Second, `VerificationSample` draws up to `VERIFICATION_SAMPLE_PER_FAMILY` served rows for every family that served any, seeded on the stamp, the family and the pass's coverage ordinal, so the checked rows change from pass to pass. A pass that writes no store advances that ordinal by the clock. The caller re-derives the sampled rows and compares whole records, and a second sample of the same shape re-shapes the rows whose positions were served. Because every family that served rows is sampled, a family whose records are all wrong is always caught, not with probability equal to the sample size over the rows served. A rune edited during a run produces that kind of error.

The key relies on one assumption that nothing else in the pipeline checks: every old compiled glyph name in a row belongs to a family the row's codepoints reach, so the alias entries a served row used are inside its own key. `unreachable_glyph_heads` checks it. Its answer depends only on the subset row and the registry, which the whole-store stamp covers, so the check runs when a pass that writes a store derives a row, not when a row is served: a row that fails it is recorded at `UNCOVERED_AGE`, which every later pass under the same stamp finds due.

Every failure falls back toward a full pass. `load_store` returns None for an absent, unreadable, format-mismatched, digest-mismatched, short or trailer-less store, one whose head lists a part missing or not the size it records, or an ages column naming another generation, for one whose row stamp moved in a `POSITION_ROW_STAMP_LABELS` line, and for one whose row stamp moved where no position can be served, because the position stamp moved too or the pass has no font, and None costs one uncached oracle pass. A store whose row stamp moved in another line, and whose positions can be served, loads with every row stale, which costs one pass of deriving rows and shaping the positions whose settled cells changed. A store whose position stamp or position keys do not match loads with every position stale, which costs one pass of shaping.

The settle memo that the oracle shares with the conformance sweep (`conform.SettleMemoFile`) keys its entries with the same primitives, so they live here too. `settle_family_keys` is `family_keys` without the alias line, because the walk never reads the alias map. `settle_memo_stamp` is the row stamp without the format, subset and alias-boundary lines, because a memo entry is only a settlement. A memo entry's reach is read from its window's labels instead of a row's codepoints: the six slots' families, the rune a formed ligature label names, and every ligature rune whose components all appear among them. `StaleMask.bit_of` maps one label to its bit. The memo is a base and segments under a head (`row_delta`'s probe merge), and the head records the stamp and family keys each part was settled under, so a reader builds one `StaleMask` a part, over `moved_families` between that part's recorded keys and the current ones: a row a segment added in one pass and a family key moved in the next is masked whether or not that pass asked its window again, and the next absorb names it in its segment's dead bitmap (`conform._MemoStore`, `conform.absorb_settle_memo`). `SettleMemoInputs` is the disk-derived half of both keys, read before the spec is loaded, as `run_m1.tables_inputs` is. The settlements are then at least as new as the content the keys name, so a rune edited during a run ends up under a key the next pass reports as moved, whichever side of the load the edit happened on.
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import heapq
import io
import itertools
import json
import operator
import os
import re
import struct
import sys
import threading
import time
import zlib
from array import array
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from typing import IO, Callable, Collection, Iterable, Iterator, Mapping, NoReturn, Sequence

import yaml

from rebuild.pipeline import fingerprint, kernel_exec, output_sink, row_delta, spec_load
from rebuild.pipeline.fingerprint import EnvironmentStamp, moved_note
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.position_record import (
    NO_SETTLED_DIGEST,
    SETTLED_DIGEST_WIDTH,
    UNSHAPED,
    CachedPosition,
    PositionVerdict,
)
from rebuild.tools import console
from rebuild.validation.rowmodel import Row, format_codepoints

STORE_FORMAT = "ams-m1-oracle-rows/4"
STORE_STEM = "oracle-rows"
ROW_COUNT_TRAILER = "#rows"
ROW_CHECK_WIDTH = 12

MAX_RECORD_AGE = 20
VERIFICATION_SAMPLE_PER_FAMILY = 8

# What `RowStore.servable` lets the caller take from a row's record.
SERVE_NOTHING = 0
SERVE_ROW = 1
SERVE_BOTH = 2
# The row age a writer records for a row `unreachable_glyph_heads` refuses. Every pass finds a record this old due, so the row is derived on every pass and never served.
UNCOVERED_AGE = -MAX_RECORD_AGE

# The position comparison's import closure, which the position stamp hashes on its own because a stored position is served across a move of the row stamp: oracle_positions.py (`_position_mismatch`, `_kern_normalized_positions`, the position record codec, `settled_digest`, `_verify_served_positions`, `KernEvaluator` and `_shaper_for`, which picks the shaper every stored position comes from), the shapers, the record types, `geometry.PIXEL`, the row model, and what they import. rebuild/test_oracle_code_closure.py walks the imports from oracle_positions.py and checks that this list names exactly the modules it reaches outside its stop-set (rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, as for `ORACLE_ROW_CODE_PATHS`), and that the classifier in oracle.py, this module, and conform.py are unreachable: a classifier edit re-shapes no position, and an edit to the walk, the crate driver or the store re-shapes only the rows whose settled cells changed. Every module here but oracle_positions.py is in `ORACLE_ROW_CODE_PATHS` too, so an edit to one drops both verdicts.
POSITION_CODE_PATHS = (
    "rebuild/pipeline/geometry.py",
    "rebuild/pipeline/labels.py",
    "rebuild/pipeline/model.py",
    "rebuild/pipeline/oracle_positions.py",
    "rebuild/pipeline/position_record.py",
    "rebuild/pipeline/settle.py",
    "rebuild/pipeline/shapers.py",
    "rebuild/validation/rowmodel.py",
)
# The row stamp's lines a position verdict reads apart from settlement: the store's format, the configuration and the features it shapes under, and the subset table that pins every row to its ordinal. `load_store` loads a store whose row stamp moved only in other lines, with every row verdict stale, so its positions can be served by settled digest (see the module docstring).
POSITION_ROW_STAMP_LABELS = ("format", "config", "features", "subset")
# The lock that pins uharfbuzz and fontTools, which decide the positions the same font bytes shape to. It is hashed by its dependency pins (`fingerprint.lock_digest`), so a change to the project's own version block does not move the stamp. For the same reason `artifact_cycle.comparison_side_label` does not count it as a comparison-side input, so a toolchain bump rebuilds the tables and the font.
TOOLCHAIN_LOCK = "uv.lock"

# The two alias heads that name a boundary glyph instead of a family. `_compare_row` skips every name in `labels.BOUNDARY_GLYPH_NAMES` before it reads the alias map, so these entries never reach a verdict. They are hashed into the whole-store stamp instead of a family key, and `alias_family_digests` raises on any other head that has no rune digest.
BOUNDARY_ALIAS_HEADS = frozenset({"space", "periodcentered"})

# The modules that `_compare_row` and `_SettledWindowWalk` import, directly or transitively: the comparison and its settlement, the stream vocabulary (`labels`), the crate driver, the spec loader, the fingerprints the keys are computed from, and this module. rebuild/test_oracle_code_closure.py walks the import graph from conform.py on every contracts run and fails when a reachable module outside its stop-set is missing here or a listed module is unreachable. conform.py holds the producer this cache serves: `_compare_row`, the walk, and the record codec. The classifier in oracle.py must stay out: if conform.py reached it, this test would fail until it was listed, and rebuild/test_build_code_closure.py fails when this list names it. The shapers and the record types are listed because conform.py and this module import them, `output_sink` because conform.py writes its summary through it, and `row_delta` because `table` reads the windows outputs through it; the position comparison's own closure is `POSITION_CODE_PATHS`, stamped on its own, and the test also checks that it never reaches oracle.py, which would put the classifier in the position stamp. The witness stage's rule replay is in witness.py, which imports conform.py and emit_gsub.py, so emit_gsub.py is outside this closure and an emitter edit keeps the store. The walk's stop-set is rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, so the rebuild/tools modules the comparison imports are not listed, and none of them can change a verdict: `kernel_exec` imports `memory_budget` to size its fan-out and `peak_rss` to read its peaks, and the streams are byte-identical at any width; `fingerprint` imports `site_fonts.font_paths`, which feeds only Stage B's `fonts` component, and `lock_digest.lock_digest`, whose value the position stamp's `toolchain` line takes in. The tables stamp, the run_m1 skip key, the verdict-update key and the unit store leave them out too.
ORACLE_ROW_CODE_PATHS = (
    "rebuild/pipeline/conform.py",
    "rebuild/pipeline/fingerprint.py",
    "rebuild/pipeline/geometry.py",
    "rebuild/pipeline/kernel_exec.py",
    "rebuild/pipeline/kernel_io.py",
    "rebuild/pipeline/labels.py",
    "rebuild/pipeline/model.py",
    "rebuild/pipeline/oracle_cache.py",
    "rebuild/pipeline/output_sink.py",
    "rebuild/pipeline/position_record.py",
    "rebuild/pipeline/row_delta.py",
    "rebuild/pipeline/settle.py",
    "rebuild/pipeline/shapers.py",
    "rebuild/pipeline/spec_load.py",
    "rebuild/pipeline/table.py",
    "rebuild/validation/rowmodel.py",
)


def oracle_code_paths(repo_root: Path) -> list[Path]:
    """Return `ORACLE_ROW_CODE_PATHS` resolved against a checkout, plus the kernel crate's manifest, lock and every Rust source file. The crate decides every settlement this cache stores, and a hand-kept list of its modules would miss the next one added."""
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    return (
        [root / relative for relative in ORACLE_ROW_CODE_PATHS]
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
    )


def store_path(out_dir: Path, config: str) -> Path:
    """Return the name a configuration's store answers to, beside the M1 artifacts but not one of them: the single file a tree from before the segment format holds, which `load_store` and `read_header` take to mean the configuration under the set head in the same directory where there is one. Neither it nor the names of the set head, the bases, the segments and the ages column match `artifact_cycle.M1_ARTIFACT_NAMES` or any glob over the tables there, so none is hashed into a gate key or part of the artifacts-present check. It is a cache, and a cycle that deletes it loses only time."""
    return Path(out_dir) / f"{STORE_STEM}-{config}.tsv.gz"


def _sha256_file(path: Path, digest: Callable[[Path], str] = fingerprint.file_sha256) -> str:
    try:
        return digest(Path(path))
    except OSError:
        return "missing"


def stamped_data_paths(repo_root: Path) -> list[Path]:
    """Return the data inputs the whole-store stamp and the settle memo's stamp hash, each as `fingerprint.data_path_lines` hashes it, so a schema hover or a comment in a data file moves neither stamp. They are `fingerprint.data_paths` without the rune files, the alias map, the divergence ledger and the kern sidecar, each of which is covered another way. The rune files are covered per family by the keys. The alias map's family heads are covered per family, and its two boundary heads have their own stamp line. The divergence ledger is read again by classification on every pass. The kern sidecar is in the position stamp, so an edit to it re-shapes every position and re-derives no row verdict. The contact allow-list is not in `fingerprint.data_paths`, so it needs no exclusion here."""
    root = Path(repo_root)
    runes = set(fingerprint.rune_paths(root))
    excluded = {
        root / "glyph_data" / "senior_quikscript_kerning.yaml",
        root / "rebuild" / "m1-aliases.yaml",
        root / "rebuild" / "m1-divergences.yaml",
    }
    return [
        path
        for path in fingerprint.data_paths(root)
        if path.is_file() and path not in runes and path not in excluded
    ]


def alias_family_digests(alias_path: Path, family_names: Collection[str]) -> dict[str, str]:
    """Group `rebuild/m1-aliases.yaml`'s entries by the part of each key before the first `.` and return one digest per head, over that group's sorted `key\\tdenotation` lines. Every head must be a name in `family_names` or one of `BOUNDARY_ALIAS_HEADS`. Any other head raises, because no key covers it, so a change to its entries could never be reported. The check is against the rune digests' names and not the script registry, because the registry has many families with no rune file and therefore no digest."""
    raw = yaml.safe_load(Path(alias_path).read_text()) or {}
    known = set(family_names)
    buckets: dict[str, list[str]] = {}
    for key in sorted(raw):
        head = str(key).split(".", 1)[0]
        if head not in known and head not in BOUNDARY_ALIAS_HEADS:
            raise ValueError(
                f"{Path(alias_path).name} entry {key!r} buckets to {head!r}, which has no family key — a family the alias map names but the rune digests do not can never be reported moved"
            )
        buckets.setdefault(head, []).append(f"{key}\t{json.dumps(raw[key], sort_keys=True)}")
    return {head: fingerprint.digest_lines(lines) for head, lines in buckets.items()}


def _reach_lines(name: str, digests: Mapping[str, str], closure: Mapping[str, frozenset[str]]) -> list[str]:
    """Return the `member\\tdigest` lines for the family and every rune in its `spec_load.rune_closure` (the runes reachable from it through ligature trailing components and `resolve.against` targets, transitively), which are the runes whose content its records read directly."""
    reach = sorted({name} | set(closure.get(name, frozenset())))
    return [f"{member}\t{digests.get(member, '-')}" for member in reach]


def family_keys(repo_root: Path, spec: ResolvedSpec, alias_path: Path) -> dict[str, str]:
    """Return, per rune family, the key a row's staleness test compares: a digest over the family's prose-insensitive rune digest, the digests of its `spec_load.rune_closure` (the runes reachable through the trailing component whose outgoing stroke a ligature keeps and through `resolve.against` targets, transitively), and the alias map's entries for the family, which the comparison reads to turn an old compiled name into a cell. A family with no alias entries records `-`, so its key changes when entries are added for it. The whole-store stamp covers every other cross-rune route."""
    digests = fingerprint.rune_digests(Path(repo_root))
    closure = spec_load.rune_closure(spec)
    aliases = alias_family_digests(Path(alias_path), digests.keys())
    keys: dict[str, str] = {}
    for name in sorted(digests):
        lines = _reach_lines(name, digests, closure)
        lines.append(f"alias\t{aliases.get(name, '-')}")
        keys[name] = fingerprint.digest_lines(lines)
    return keys


def position_family_keys(row_keys: Mapping[str, str], glyph_digests: Mapping[str, str]) -> dict[str, str]:
    """Return, per family, the key a row's position verdict compares: a digest of the family's row key and the after font's compiled-glyph digest for the family (`fingerprint.after_font_glyph_digests`). Including the row key makes a position stale wherever the row verdict is. Keys are computed over the union of both maps' names, with `-` for a name missing on one side, so a family with glyphs but no rune file, or the reverse, still has a key that can move."""
    return {
        name: fingerprint.digest_lines(
            (f"row\t{row_keys.get(name, '-')}", f"glyphs\t{glyph_digests.get(name, '-')}")
        )
        for name in sorted(set(row_keys) | set(glyph_digests))
    }


def position_keys(
    repo_root: Path, row_keys: Mapping[str, str], font_path: Path, kern_sidecar_path: Path | None
) -> tuple[dict[str, str], EnvironmentStamp]:
    """Return the position store's per-family keys (`position_family_keys`) and its whole-store position stamp, from one read of the font. The stamp covers the position comparison's code closure (`POSITION_CODE_PATHS`), the toolchain lock's dependency pins, the digest `fingerprint.after_font_glyph_digests` returns for the font's non-family glyphs, cmap and GPOS wiring, and the kern sidecar's bytes, or `-` when there is no sidecar. The row stamp's lines a position reads apart from settlement are `POSITION_ROW_STAMP_LABELS`, which a store must match to load at all, so they are not repeated here."""
    root = Path(repo_root)
    glyph_digests, helpers = fingerprint.after_font_glyph_digests(Path(font_path))
    lines = (
        f"format\t{STORE_FORMAT}",
        f"position_code\t{fingerprint.hash_paths(root, [root / relative for relative in POSITION_CODE_PATHS])}",
        f"toolchain\t{_sha256_file(root / TOOLCHAIN_LOCK, fingerprint.lock_digest)}",
        f"font_helpers\t{helpers}",
        f"kern\t{'-' if kern_sidecar_path is None else _sha256_file(Path(kern_sidecar_path))}",
    )
    return position_family_keys(row_keys, glyph_digests), EnvironmentStamp(lines=lines)


@dataclass(frozen=True)
class SettleMemoInputs:
    """The disk-derived half of the settle memo's keys, computed before the spec they describe is loaded, as `run_m1.tables_inputs` is, so the settlements are at least as new as the content these keys name. `settle_memo_stamp` computes the spec-derived half (`spec_structure`, `capability_features`) from the loaded spec that settles."""

    rune_digests: Mapping[str, str]
    oracle_code: str
    data: str


def settle_memo_inputs(repo_root: Path) -> SettleMemoInputs:
    root = Path(repo_root)
    return SettleMemoInputs(
        rune_digests=fingerprint.rune_digests(root),
        oracle_code=fingerprint.hash_paths(root, oracle_code_paths(root)),
        data=fingerprint.digest_lines(fingerprint.data_path_lines(root, stamped_data_paths(root))),
    )


def settle_family_keys(inputs: SettleMemoInputs, spec: ResolvedSpec) -> dict[str, str]:
    """Return, per rune family, the key a memo entry's staleness test compares: `family_keys` without the alias line. The walk that fills the memo never reads the alias map, and a settlement depends only on the rune files a window names and their `spec_load.rune_closure`."""
    closure = spec_load.rune_closure(spec)
    return {
        name: fingerprint.digest_lines(_reach_lines(name, inputs.rune_digests, closure))
        for name in sorted(inputs.rune_digests)
    }


def settle_memo_stamp(
    inputs: SettleMemoInputs, spec: ResolvedSpec, config: str, features: Collection[str]
) -> EnvironmentStamp:
    """Return the stamp over everything that can change a memoized settlement without changing a named family's key. It is `environment_stamp` without the lines only the comparison reads (the subset table and the alias map's boundary heads) and without the store format, which each part of the memo records in its own header. It uses `settlement_flags` and not `kernel_exec.enumeration_tokens`, because the walk settles windows one at a time and never enumerates, so deep-class grain cannot affect it. While the alphabet switch is set, the lines end with its token (`spec_load.alphabet_lines`), so a memo over the whole alphabet never serves a small one, nor the reverse; with it unset no line is added."""
    lines = (
        f"config\t{config}",
        "features\t" + json.dumps(sorted(features)),
        f"oracle_code\t{inputs.oracle_code}",
        f"data\t{inputs.data}",
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        *spec_load.alphabet_lines(),
    )
    return EnvironmentStamp(lines=lines)


def environment_stamp(
    repo_root: Path,
    spec: ResolvedSpec,
    config: str,
    features: Collection[str],
    subset_path: Path,
    alias_path: Path,
    family_names: Collection[str],
) -> EnvironmentStamp:
    """Return the stamp over everything that can change a row's pre-position verdict without changing a named family's key. The caller passes `family_names` so the rune tree is read once per run and not once per configuration, and passes `features` (its `labels.features_for_config(config)`) so the stamp names the feature set the caller shapes under. The alias map enters only through its boundary heads; `family_keys` covers every family head. The module docstring lists what the stamp leaves out and why."""
    root = Path(repo_root)
    boundary = alias_family_digests(Path(alias_path), family_names)
    lines = (
        f"format\t{STORE_FORMAT}",
        f"config\t{config}",
        "features\t" + json.dumps(sorted(features)),
        f"oracle_code\t{fingerprint.hash_paths(root, oracle_code_paths(root))}",
        f"data\t{fingerprint.digest_lines(fingerprint.data_path_lines(root, stamped_data_paths(root)))}",
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        "alias_boundary\t"
        + fingerprint.digest_lines(
            f"{head}\t{boundary[head]}" for head in sorted(boundary) if head in BOUNDARY_ALIAS_HEADS
        ),
        f"subset\t{_sha256_file(Path(subset_path))}",
    )
    return EnvironmentStamp(lines=lines)


def moved_families(recorded: Mapping[str, str], current: Mapping[str, str]) -> frozenset[str]:
    """Return every family whose key differs between the recorded and current maps. A name present in only one map counts as moved, so a rune file that appears or disappears is a miss and never a match."""
    moved = set(current.keys() ^ recorded.keys())
    for name in current.keys() & recorded.keys():
        if current[name] != recorded[name]:
            moved.add(name)
    return frozenset(moved)


class StaleMask:
    """The per-row staleness test, as a bitmask over the families a row can reach. There is one bit per family, in sorted registry order. `mask_of` builds a row's mask from its codepoints, and `stale` returns whether any moved family is inside it: directly, for a family with a codepoint, or through the ligature clause, which applies only when every component of a moved ligature rune is in the row. The ligature clause is the rule `unit_cache.UnitKeyer._relevant_families` applies, read from `spec.registry.families[...].sequence` instead of a `_`-split of the name, because `settle.form_ligatures` routes on the sequence. A moved ligature rune also sets its own bit, because the settle memo keys windows on formed labels: a window whose slot holds `qsThey_qsUtter` names that rune and none of its components, so the component clause alone would serve it after an edit to the ligature's file. A moved family that the registry places by neither codepoint nor sequence makes every row stale, since over-invalidation is the safe direction."""

    def __init__(self, spec: ResolvedSpec, moved: Collection[str] = ()) -> None:
        families = spec.registry.families
        self.spec = spec
        self._bit: dict[str, int] = {name: 1 << index for index, name in enumerate(sorted(families))}
        self._family_of: dict[int, str] = {
            info.codepoint: name for name, info in families.items() if info.codepoint is not None
        }
        self._ligatures: dict[str, int] = {}
        for name, info in families.items():
            if not info.sequence:
                continue
            bits = 0
            for component in info.sequence:
                bits |= self._bit.get(component, 0)
            self._ligatures[name] = bits
        self.moved = frozenset(moved)
        self.everything = False
        symbols = 0
        ligatures: list[int] = []
        for name in sorted(self.moved):
            info = families.get(name)
            if info is not None and info.codepoint is not None:
                symbols |= self._bit[name]
            elif info is not None and info.sequence:
                symbols |= self._bit[name]
                ligatures.append(self._ligatures[name])
            else:
                self.everything = True
        self._stale_symbols = symbols
        self._stale_ligatures = tuple(sorted(set(ligatures)))
        self._reach: dict[int, tuple[str, ...]] = {}

    def mask_of(self, codepoints: Iterable[int]) -> int:
        mask = 0
        for codepoint in codepoints:
            name = self._family_of.get(codepoint)
            if name is not None:
                mask |= self._bit[name]
        return mask

    def bit_of(self, family: str) -> int:
        """Return the bit for one family name, or zero for a name the registry does not place (a boundary label, a window edge, or an unknown family). The settle memo uses this because its windows name families by label, not by codepoint. A formed ligature label maps to the ligature rune's own bit, which `stale` checks directly when that rune moved."""
        return self._bit.get(family, 0)

    def stale(self, mask: int) -> bool:
        if self.everything or mask & self._stale_symbols:
            return True
        return bool(self._stale_ligatures) and any((mask & bits) == bits for bits in self._stale_ligatures)

    def families_of(self, mask: int) -> tuple[str, ...]:
        """Return every family a row with this mask can reach: the letters its codepoints name, plus each ligature rune whose components are all among them. Memoized on the mask, because many rows share a mask."""
        cached = self._reach.get(mask)
        if cached is None:
            names = [name for name, bit in self._bit.items() if mask & bit]
            names += [name for name, bits in self._ligatures.items() if bits and (mask & bits) == bits]
            cached = tuple(sorted(set(names)))
            self._reach[mask] = cached
        return cached


def unreachable_glyph_heads(glyph_names: Iterable[str], reachable: Collection[str]) -> tuple[str, ...]:
    """Return the `qs` family heads of a row's old compiled glyph names that are not in its reachable family set. A non-empty result means the row used alias entries that no key it compares covers, so the caller must derive the row instead of serving it. This checks the assumption the row key rests on (see the module docstring)."""
    known = set(reachable)
    heads = {name.split(".", 1)[0] for name in glyph_names}
    return tuple(sorted(head for head in heads if head.startswith("qs") and head not in known))


@dataclass(frozen=True, slots=True)
class CachedRow:
    """One row's pre-position comparison verdict: `conform._compare_row`'s `DivergentRow` without the three fields the subset table holds (`codepoints`, `baseline_glyphs`, `baseline_junctions`) and without `config`, which the store records. It holds no provenance, so `==` is verdict equality, which the verification sample relies on. The pass a record was derived at is kept separately, in `RowStore.age`."""

    kinds: tuple[str, ...]
    position: int
    new_cells: tuple[str, ...]
    new_junctions: tuple[str, ...]
    divergence_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One row's verdicts in the store and the pass each was derived at. The ages are per verdict because the two re-derive on different keys: a font compile that changes a family's outlines re-shapes its rows' positions while every row verdict is still served. The record's settled digest is not decoded here, because only a writing pass and a row whose row verdict is derived again read it, and they read it in place (`RowStore.settled`)."""

    row: CachedRow | None
    row_age: int
    position: PositionVerdict
    position_age: int


def _split(text: str, separator: str) -> tuple[str, ...]:
    return tuple(text.split(separator)) if text else ()


def row_check_digest(codepoints: Sequence[int]) -> str:
    """Return a record's row check digest: the first `ROW_CHECK_WIDTH` hex characters of the SHA-256 of the row's canonical codepoint string. It is checked on every serve, so a table that was refiltered or reordered under the store causes an abort instead of every row being served wrong."""
    return hashlib.sha256(format_codepoints(tuple(codepoints)).encode()).hexdigest()[:ROW_CHECK_WIDTH]


def encode_record(
    codepoints: Sequence[int],
    cached: CachedRow | None,
    derived_at_pass: int,
    position: PositionVerdict = UNSHAPED,
    position_at_pass: int = 0,
    settled: str = NO_SETTLED_DIGEST,
) -> str:
    """Return one store line: the row check digest; the settled digest, `SETTLED_DIGEST_WIDTH` characters wide; then `-` for a clean row, or `P` and the row verdict's five fields; then `?` for a position never shaped, `-` for one that matched, or `D`, the `|`-joined mismatch descriptions and `k` or `n` for the kern-attribution flag; then the two derivation passes, the row verdict's first. `|` separates cells and `,` separates the token tuples, as in `divergence-audit.tsv`. The mismatch descriptions contain commas, so they are joined on `|` alone and the flag has its own field."""
    if len(settled) != SETTLED_DIGEST_WIDTH:
        raise ValueError(f"a settled digest is {SETTLED_DIGEST_WIDTH} characters, not {settled!r}")
    fields = [row_check_digest(codepoints), settled]
    if cached is None:
        fields.append("-")
    else:
        fields += [
            "P",
            ",".join(cached.kinds),
            str(cached.position),
            "|".join(cached.new_cells),
            ",".join(cached.new_junctions),
            ",".join(cached.divergence_tags),
        ]
    if position is UNSHAPED:
        fields.append("?")
    elif position is None:
        fields.append("-")
    else:
        assert isinstance(position, CachedPosition)
        fields += ["D", "|".join(position.mismatches), "k" if position.kern_attributable else "n"]
    fields += [str(derived_at_pass), str(position_at_pass)]
    return "\t".join(fields)


def decode_record(line: str) -> StoredRecord:
    fields = line.split("\t")
    at = 2
    row: CachedRow | None = None
    if fields[at] == "-":
        at += 1
    else:
        row = CachedRow(
            kinds=_split(fields[at + 1], ","),
            position=int(fields[at + 2]),
            new_cells=_split(fields[at + 3], "|"),
            new_junctions=_split(fields[at + 4], ","),
            divergence_tags=_split(fields[at + 5], ","),
        )
        at += 6
    position: PositionVerdict
    tag = fields[at]
    at += 1
    if tag == "?":
        position = UNSHAPED
    elif tag == "-":
        position = None
    else:
        position = CachedPosition(mismatches=_split(fields[at], "|"), kern_attributable=fields[at + 1] == "k")
        at += 2
    return StoredRecord(row=row, row_age=int(fields[at]), position=position, position_age=int(fields[at + 1]))


def decode_position(line: str) -> tuple[PositionVerdict, int]:
    """Return one store line's position verdict and the pass it was shaped at, the last field, without building its row verdict. The position tag follows the row check digest, the settled digest, and the row verdict's one field for a clean row or six for a divergent one."""
    fields = line.split("\t")
    at = 3 if fields[2] == "-" else 8
    tag = fields[at]
    position: PositionVerdict
    if tag == "?":
        position = UNSHAPED
    elif tag == "-":
        position = None
    else:
        position = CachedPosition(
            mismatches=_split(fields[at + 1], "|"), kern_attributable=fields[at + 2] == "k"
        )
    return position, int(fields[-1])


class RowStore:
    """One row range's part of a configuration's loaded store: the records of rows `[first_row, first_row + len(ages))`, held as one buffer and three packed arrays (each record's offset into the buffer and its two ages), indexed by `index - first_row`, so a worker holds only its own range's records. The ages are the ones the store was written with: a single file's records' inline ages, or under the set head the ages column's, which override the records' own, so `serve` and `serve_position` answer with the arrays' ages and never with the inline ones. Every method takes the row's absolute ordinal in the table, so a cut configuration's ranges are self-contained segments of one store (rebuild/pipeline/oracle.py). An index outside the range raises instead of serving another row's record, since Python's arrays would otherwise read a negative index from the end. `rows` is the table's row count, not the range's. When it is not given it is the range's end, which is the table's count when the range runs to the table's end. The oracle treats an index at or above `rows` as fresh (`oracle._compare_config`), so a range given its own length as the count would re-derive every row above its range and write a store an uncut pass never writes. Only the rows a pass serves are decoded, and the staleness scan reads only the age arrays, never the buffer. A store whose row stamp moved serves no row verdict, so its buffer holds only each record's position part (`_position_part`) and `row_verdicts_held` is False, which makes `serve` raise.

    `read_only` and `rotation` are set by a pass that will not write a store. Such a pass keeps nothing it re-derives, so a scheduled re-derivation would correct only its own audit, and it skips the ordinal clause. Because it records no ordinal of its own it measures each record's age against `pass_ordinal`, the ordinal the store was written at, rather than the one a writing pass records: measured against that one, the age clause would take the rows the next writing pass re-derives on schedule. Its age clause still takes a row recorded at `UNCOVERED_AGE` and a record already past the cap.

    The verification samples advance with the pass ordinal, and the ordinal advances only when a store is written. A read-only pass run repeatedly, which is how `--gates-only` is used when rerunning the gates after a ledger edit, would otherwise check the same samples every time. A writing pass leaves `rotation` at zero and uses its own ordinal. A read-only pass sets it from the clock, so the rows its samples check change even when nothing on disk does.
    """

    def __init__(
        self,
        environment: EnvironmentStamp,
        recorded_lines: tuple[str, ...],
        recorded_keys: dict[str, str],
        subset_digest: str,
        pass_ordinal: int,
        mask: StaleMask,
        blob: bytes | bytearray,
        offsets: "array[int]",
        ages: "array[int]",
        rotation: int = 0,
        position_mask: StaleMask | None = None,
        position_ages: "array[int] | None" = None,
        first_row: int = 0,
        rows: int | None = None,
        row_verdicts_held: bool = True,
        read_only: bool = False,
    ) -> None:
        self.environment = environment
        self.recorded_lines = recorded_lines
        self.recorded_keys = recorded_keys
        self.subset_digest = subset_digest
        self.pass_ordinal = pass_ordinal
        self.mask = mask
        self.rotation = rotation
        self.read_only = read_only
        self._after = pass_ordinal if read_only else pass_ordinal + 1
        self.served = 0
        self.positions_served = 0
        self._blob = blob
        self._offsets = offsets
        self._ages = ages
        self.first_row = first_row
        self._rows = first_row + len(ages) if rows is None else rows
        self.row_verdicts_held = row_verdicts_held
        if position_mask is None:
            position_mask = StaleMask(mask.spec, mask.moved)
            position_mask.everything = True
        self.position_mask = position_mask
        self._position_ages = ages if position_ages is None else position_ages

    @property
    def rows(self) -> int:
        """The table's row count, as recorded in the store's trailer, whatever range this store holds."""
        return self._rows

    @property
    def stop_row(self) -> int:
        """One past the last row this store holds a record for."""
        return self.first_row + len(self._ages)

    @property
    def moved(self) -> frozenset[str]:
        return self.mask.moved

    def _at(self, index: int) -> int:
        """Return the arrays' position for the table's row `index`, raising `IndexError` for a row outside this store's range."""
        at = index - self.first_row
        if at < 0 or at >= len(self._ages):
            raise IndexError(
                f"row {index} is outside the range [{self.first_row}, {self.stop_row}) this oracle row store holds"
            )
        return at

    def age(self, index: int) -> int:
        """Return the pass this row's verdict was derived at. A pass that only serves the verdict writes this value forward unchanged, so it measures how long the verdict has stood, not how long the file has."""
        return self._ages[self._at(index)]

    def position_age(self, index: int) -> int:
        return self._position_ages[self._at(index)]

    @property
    def coverage_ordinal(self) -> int:
        """The ordinal the verification samples are drawn against. It equals `pass_ordinal + 1`, the ordinal the writer records, except on a pass that writes nothing (see `rotation`)."""
        return self.pass_ordinal + 1 + self.rotation

    def _due(self, index: int, age: int) -> bool:
        if self._after - age >= MAX_RECORD_AGE:
            return True
        return not self.read_only and self._after % MAX_RECORD_AGE == index % MAX_RECORD_AGE

    def due(self, index: int) -> bool:
        """Whether this row's verdict must be re-derived regardless of its families. The ordinal clause selects one row in `MAX_RECORD_AGE` on every pass that writes a store, so no verdict stands that many writing passes and the scheduled re-derivation is spread across them. A read-only pass skips it (see `read_only`). The age clause is a second check that catches a store whose pass ordinals skipped, and it holds on every pass for a row recorded at `UNCOVERED_AGE`. Neither clause reads `rotation`, which moves only the verification samples: a rotated ordinal in the age arithmetic would make every record look older than the cap and re-derive the whole table."""
        return self._due(index, self._ages[self._at(index)])

    def position_due(self, index: int) -> bool:
        """`due` over the position verdict's own age. The ordinal clause re-derives both of a row's verdicts on the same pass, and the age clause reads the pass the position was shaped at."""
        return self._due(index, self._position_ages[self._at(index)])

    def servable(self, index: int, mask: int) -> int:
        """Return which of this row's verdicts may be served: `SERVE_NOTHING` where `stale` holds, `SERVE_ROW` where only `position_stale` does, and `SERVE_BOTH` otherwise. The oracle's scan asks both questions of nearly every row, so they are decided here in one pass over the row's slot, and the ordinal clause `due` and `position_due` share is evaluated once."""
        if self.mask.stale(mask):
            return SERVE_NOTHING
        at = self._at(index)
        after = self._after
        if after - self._ages[at] >= MAX_RECORD_AGE or (
            not self.read_only and after % MAX_RECORD_AGE == index % MAX_RECORD_AGE
        ):
            return SERVE_NOTHING
        if self.position_mask.stale(mask) or after - self._position_ages[at] >= MAX_RECORD_AGE:
            return SERVE_ROW
        return SERVE_BOTH

    def stale(self, index: int, mask: int) -> bool:
        """Whether this row's verdict must be re-derived: a family it reaches moved, or `due` holds."""
        return self.servable(index, mask) == SERVE_NOTHING

    def position_stale(self, index: int, mask: int) -> bool:
        """Whether this row's position verdict must be shaped again: wherever its row verdict must be re-derived (the position key includes the row key, and a served position over a fresh settlement would describe the previous pass's cells), or wherever a family it reaches changed its glyphs, the position stamp moved, or `position_due` holds. `position_servable` names the rows among the first kind whose position may still be served once the walk shows their settled stream unchanged."""
        return self.servable(index, mask) != SERVE_BOTH

    def position_servable(self, index: int, mask: int) -> bool:
        """Whether this row's stored position verdict may be served after its row verdict has been derived again, provided the fresh settled stream has the digest the record holds (`settled`): no family the row reaches moved its position key, the position stamp holds, and `position_due` does not hold. The oracle asks this of the rows `servable` refuses outright, which on a pass whose row stamp moved is every row and on any other pass includes a row due only for its row age or recorded at `UNCOVERED_AGE`, and the module docstring argues why the settled digest stands in for the row key there."""
        if self.position_mask.stale(mask):
            return False
        after = self._after
        return not (
            after - self._position_ages[self._at(index)] >= MAX_RECORD_AGE
            or (not self.read_only and after % MAX_RECORD_AGE == index % MAX_RECORD_AGE)
        )

    def settled(self, index: int) -> str:
        """Return the settled digest this row's record holds, read in place without checking the row check digest or decoding the record. A writing pass carries it forward for a served row, and the oracle compares it with a walked row's fresh digest before `serve_position` checks the row and decodes its position."""
        start = self._offsets[self._at(index)] + ROW_CHECK_WIDTH + 1
        return self._blob[start : start + SETTLED_DIGEST_WIDTH].decode("ascii")

    def serve(self, index: int, codepoints: Sequence[int]) -> StoredRecord:
        """Return this row's full record after checking its row check digest. The caller decides which of the two verdicts the keys allow it to use, and counts a served position in `positions_served` itself. A mismatched row check digest is not a miss: it means the table under this store was replaced or reordered and every other record is wrong in the same way, so it exits."""
        if not self.row_verdicts_held:
            raise ValueError(
                "this oracle row store holds only its records' position parts, because its row stamp moved, so it has no row verdict to serve"
            )
        at = self._at(index)
        start = self._offsets[at]
        end = self._offsets[at + 1] - 1
        if self._blob[start : start + ROW_CHECK_WIDTH].decode("ascii") != row_check_digest(codepoints):
            self._misaligned(index, codepoints)
        self.served += 1
        record = decode_record(self._blob[start:end].decode("utf-8"))
        return StoredRecord(record.row, self._ages[at], record.position, self._position_ages[at])

    def serve_position(self, index: int, codepoints: Sequence[int]) -> tuple[PositionVerdict, int]:
        """Return this row's stored position verdict and the pass it was shaped at, after the row check `serve` makes, without building the row verdict or counting the row in `served`. A row whose row verdict is derived again reads only this, and so does the served-position verifier."""
        at = self._at(index)
        start = self._offsets[at]
        end = self._offsets[at + 1] - 1
        if self._blob[start : start + ROW_CHECK_WIDTH].decode("ascii") != row_check_digest(codepoints):
            self._misaligned(index, codepoints)
        return decode_position(self._blob[start:end].decode("utf-8"))[0], self._position_ages[at]

    def _misaligned(self, index: int, codepoints: Sequence[int]) -> NoReturn:
        start = self._offsets[self._at(index)]
        digest = self._blob[start : start + ROW_CHECK_WIDTH].decode("ascii")
        raise SystemExit(
            f"the oracle row cache does not match the table at row {index}: the record's row check digest is {digest} where the table holds {format_codepoints(tuple(codepoints))} — the store describes a different table and nothing it holds can be served"
        )


def read_header(path: Path) -> dict | None:
    """Return a store's header alone, for a caller that reports what moved without loading the store: the header the stores' set head records for the configuration `path` names (`read_set_head`) when there is a head, else the single file's first line. `None` when nothing readable is there."""
    store_file = Path(path)
    config = _config_of(store_file)
    if config is not None:
        try:
            head = read_set_head(store_file.parent)
        except row_delta.HeadError:
            return None
        if head is not None:
            entry = head.configs.get(config)
            return None if entry is None else dict(entry.header)
    try:
        with gzip.open(store_file, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration, zlib.error:
        return None
    return header if isinstance(header, dict) else None


def position_stale_mask(
    spec: ResolvedSpec,
    moved: Collection[str],
    header: Mapping,
    position_environment: EnvironmentStamp | None,
    current_position_keys: Mapping[str, str] | None,
) -> StaleMask:
    """Return the position comparison's staleness mask for a loaded store. When the store's position stamp matches this run's, the mask covers the families whose position keys moved. It covers every row (`everything`) when either side has no position keys or stamp, the stamp moved, or the header's position fields cannot be read. `moved` is the row verdicts' moved set and is always included, so a family stale for row verdicts is stale for positions too."""
    everything = StaleMask(spec, moved)
    everything.everything = True
    if position_environment is None or current_position_keys is None:
        return everything
    try:
        recorded_lines = header.get("position_environment")
        recorded_keys = header.get("position_keys")
        if not isinstance(recorded_lines, list) or not isinstance(recorded_keys, dict):
            return everything
        if tuple(recorded_lines) != position_environment.lines:
            return everything
        keys = {str(name): str(value) for name, value in recorded_keys.items()}
    except TypeError, ValueError:
        return everything
    return StaleMask(spec, set(moved) | moved_families(keys, current_position_keys))


@dataclass(frozen=True)
class _Checked:
    """What a store's header says once `_checked_header` has let it load: its recorded row stamp, whether that stamp holds, its family keys and pass ordinal, the moved families and the position mask."""

    recorded_lines: tuple[str, ...]
    rows_hold: bool
    recorded_keys: dict[str, str]
    pass_ordinal: int
    moved: frozenset[str]
    position_mask: StaleMask


def _checked_header(
    header: Mapping,
    environment: EnvironmentStamp,
    subset_digest: str,
    spec: ResolvedSpec,
    current_keys: Mapping[str, str],
    position_environment: EnvironmentStamp | None,
    current_position_keys: Mapping[str, str] | None,
) -> _Checked | None:
    """The header checks every load makes, whether the header came from a single file's first line or from the stores' set head: the format, the row stamp (or, where it moved, that positions outlive it), the subset digest, and whether any position can be served when the row stamp moved. None refuses the store; a header that does not parse raises the errors `load_store` catches."""
    if header["format"] != STORE_FORMAT:
        return None
    recorded_lines = tuple(header["environment"])
    rows_hold = recorded_lines == environment.lines
    if not rows_hold and not _positions_outlive(recorded_lines, environment):
        return None
    if header["subset_digest"] != subset_digest:
        return None
    recorded_keys = {str(name): str(value) for name, value in header["family_keys"].items()}
    pass_ordinal = int(header["pass_ordinal"])
    moved = moved_families(recorded_keys, current_keys)
    position_mask = position_stale_mask(spec, moved, header, position_environment, current_position_keys)
    if not rows_hold and position_mask.everything:
        return None
    return _Checked(recorded_lines, rows_hold, recorded_keys, pass_ordinal, moved, position_mask)


_LOAD_ERRORS = (OSError, EOFError, ValueError, KeyError, IndexError, TypeError, OverflowError, zlib.error)


def load_store(
    path: Path,
    environment: EnvironmentStamp,
    subset_digest: str,
    spec: ResolvedSpec,
    current_keys: Mapping[str, str],
    rotation: int = 0,
    position_environment: EnvironmentStamp | None = None,
    current_position_keys: Mapping[str, str] | None = None,
    *,
    first_row: int = 0,
    stop_row: int | None = None,
    read_only: bool = False,
) -> RowStore | None:
    """Return the previous pass's records for rows `[first_row, stop_row)` of one configuration (`stop_row` None means the table's end), or `None` when the store cannot be trusted: absent, unreadable, format-mismatched, stamp-mismatched in a `POSITION_ROW_STAMP_LABELS` line, written against another subset table, or missing its row count, and when its row stamp moved and `position_stale_mask` makes every position stale, because the position stamp moved, a moved family is one the registry cannot place, or the pass has no position stamp or keys (no font): such a store could serve nothing, and a pass that loads no store walks the same rows without holding its records. A store whose row stamp moved only in other lines loads with every row stale (`StaleMask.everything`), so that only its position verdicts can be served (`RowStore.position_servable`). `None` costs one uncached oracle pass, so every parse failure returns `None`, including `zlib.error`, which a corrupt deflate body raises instead of `OSError`.

    Where `path`'s directory holds the stores' set head (`read_set_head`), the configuration `path` names is read from it (`_load_from_head`): its header and row count from the head, its records from its base and segments and its ages from the ages column. Otherwise `path` is the configuration's single file (a tree from before the segment format, and the tests' stores), read as it was written (`_load_single`). Only the range's records are kept, in one buffer with three packed arrays. When the row stamp moved, each record is cut to its position part as it is read (`_position_part`), so the range holds the row check digests, settled digests, position verdicts and ages a walk over every row reads, and none of the row verdicts it derives again. A range whose kept bytes exceed the packed offsets' width returns `None` like any other parse failure. Where the row stamp holds, the position stamp and keys do not affect loading: `position_stale_mask` decides which position verdicts may be served. `rotation` and `read_only` are passed to the store unread; see `RowStore`.
    """
    store_file = Path(path)
    config = _config_of(store_file)
    if config is not None:
        try:
            head = read_set_head(store_file.parent)
        except row_delta.HeadError:
            return None
        if head is not None:
            return _load_from_head(
                store_file.parent,
                head,
                config,
                environment,
                subset_digest,
                spec,
                current_keys,
                rotation,
                position_environment,
                current_position_keys,
                first_row,
                stop_row,
                read_only,
            )
    return _load_single(
        store_file,
        environment,
        subset_digest,
        spec,
        current_keys,
        rotation,
        position_environment,
        current_position_keys,
        first_row,
        stop_row,
        read_only,
    )


def _load_single(
    store_file: Path,
    environment: EnvironmentStamp,
    subset_digest: str,
    spec: ResolvedSpec,
    current_keys: Mapping[str, str],
    rotation: int,
    position_environment: EnvironmentStamp | None,
    current_position_keys: Mapping[str, str] | None,
    first_row: int,
    stop_row: int | None,
    read_only: bool,
) -> RowStore | None:
    """`load_store` over a single file. A range that starts and ends on one member of a joined store inflates that member alone (`_indexed_records`), after checking the file's header member against the header line read here, and every member's bytes and the trailer against the store's member index. Any other range, and every range of a store with no index (an uncut configuration's, or one the index does not describe), reads the file to its end: the trailer is the last line and holds the count the store is checked against, and every record's two ages are parsed in range or out. Both reads load a store only when it is the one its writer wrote, so all ranges of one configuration agree on whether the store loads."""
    if not store_file.is_file():
        return None
    try:
        with gzip.open(store_file, "rb") as stream:
            header_line = stream.readline()
            checked = _checked_header(
                json.loads(header_line),
                environment,
                subset_digest,
                spec,
                current_keys,
                position_environment,
                current_position_keys,
            )
            if checked is None:
                return None
            rows_hold = checked.rows_hold
            blob: bytes | bytearray
            indexed = _indexed_records(
                store_file, header_line, first_row, stop_row, positions_only=not rows_hold
            )
            if indexed is not None:
                blob, seen = indexed
                offsets, ages, position_ages = _record_arrays(blob)
            else:
                blob = bytearray()
                offsets = array("I", [0])
                ages = array("i")
                position_ages = array("i")
                seen = 0
                pending = stream.readline()
                if not pending:
                    return None
                for line in stream:
                    end = len(pending) - 1
                    last_tab = pending.rindex(b"\t", 0, end)
                    position_age = int(pending[last_tab + 1 : end])
                    age = int(pending[pending.rindex(b"\t", 0, last_tab) + 1 : last_tab])
                    if seen >= first_row and (stop_row is None or seen < stop_row):
                        blob += pending if rows_hold else _position_part(pending)
                        offsets.append(len(blob))
                        ages.append(age)
                        position_ages.append(position_age)
                    seen += 1
                    pending = line
                if not pending.endswith(b"\n"):
                    return None
                trailer = pending[:-1].decode("utf-8").split("\t")
                if trailer[0] != ROW_COUNT_TRAILER:
                    return None
                if seen != int(trailer[1]):
                    return None
    except _LOAD_ERRORS:
        return None
    return _row_store(
        checked,
        environment,
        subset_digest,
        spec,
        blob,
        offsets,
        ages,
        position_ages,
        rotation,
        first_row,
        seen,
        read_only,
    )


def _row_store(
    checked: _Checked,
    environment: EnvironmentStamp,
    subset_digest: str,
    spec: ResolvedSpec,
    blob: bytes | bytearray,
    offsets: "array[int]",
    ages: "array[int]",
    position_ages: "array[int]",
    rotation: int,
    first_row: int,
    rows: int,
    read_only: bool,
) -> RowStore:
    mask = StaleMask(spec, checked.moved)
    if not checked.rows_hold:
        mask.everything = True
    return RowStore(
        environment=environment,
        recorded_lines=checked.recorded_lines,
        recorded_keys=checked.recorded_keys,
        subset_digest=subset_digest,
        pass_ordinal=checked.pass_ordinal,
        mask=mask,
        blob=blob,
        offsets=offsets,
        ages=ages,
        rotation=rotation,
        position_mask=checked.position_mask,
        position_ages=position_ages,
        first_row=first_row,
        rows=rows,
        row_verdicts_held=checked.rows_hold,
        read_only=read_only,
    )


def _load_from_head(
    directory: Path,
    head: SetHead,
    config: str,
    environment: EnvironmentStamp,
    subset_digest: str,
    spec: ResolvedSpec,
    current_keys: Mapping[str, str],
    rotation: int,
    position_environment: EnvironmentStamp | None,
    current_position_keys: Mapping[str, str] | None,
    first_row: int,
    stop_row: int | None,
    read_only: bool,
) -> RowStore | None:
    """`load_store` through the stores' set head: the configuration's header and row count from the head, with the single file's format, stamp, subset and position checks; every part the head lists for it, and the ages column, opened as the head is read and checked against the sizes it records, since a reader that seeks checks sizes only. The range's records come from its base: the base's one member that spans the range alone (`_spanning_member`), where its member index has one, or the base read to its end with every record counted and those outside the range dropped (`_base_runs`). With segments they stream beside the segments' upserts in the range, merged by ordinal, the latest segment winning (`_segment_upserts`), so a range holds its records once whatever the segments hold. The two ages come from the column (`_column_ages`), which must name the head's generation; the records' inline ages are not read. Every range of a configuration reads the same head, parts and checks, so all agree on whether the store loads."""
    entry = head.configs.get(config)
    if entry is None:
        return None
    try:
        checked = _checked_header(
            entry.header,
            environment,
            subset_digest,
            spec,
            current_keys,
            position_environment,
            current_position_keys,
        )
        if checked is None:
            return None
        rows = entry.rows
        kept = range(first_row, max(first_row, rows if stop_row is None else min(stop_row, rows)))
        positions_only = not checked.rows_hold
        with (
            row_delta.open_parts(directory, entry.view(config)) as opened,
            _opened_ages(directory, head) as column,
        ):
            base = opened.handles[0]
            held = _spanning_member(base, kept, rows)
            blob: bytes | bytearray
            if not entry.segments and held is not None:
                blob = (
                    _inflated_position_parts(held)
                    if positions_only
                    else zlib.decompress(
                        held, 16 + zlib.MAX_WBITS, max(1, int.from_bytes(held[-4:], "little"))
                    )
                )
            else:
                runs = _member_runs(held, kept) if held is not None else _base_runs(base, entry, kept)
                if entry.segments:
                    runs = _upserted(runs, kept, _segment_upserts(opened, config, entry, kept))
                blob = bytearray()
                for run in runs:
                    blob += _position_parts(run) if positions_only else run
            offsets = _record_offsets(blob)
            if len(offsets) - 1 != len(kept):
                return None
            ages, position_ages = _column_ages(column, head, config, kept)
    except (*_LOAD_ERRORS, row_delta.HeadError):
        return None
    return _row_store(
        checked,
        environment,
        subset_digest,
        spec,
        blob,
        offsets,
        ages,
        position_ages,
        rotation,
        first_row,
        rows,
        read_only,
    )


def _first_record_member(handle: IO[bytes]) -> int:
    """The byte at which a joined store's first record member starts, after its header member, read by inflating the header member from the start."""
    handle.seek(0)
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    consumed = 0
    while not inflater.eof:
        chunk = handle.read(1 << 16)
        if not chunk:
            raise ValueError("a joined store ends inside its header member")
        inflater.decompress(chunk)
        consumed += len(chunk)
    return consumed - len(inflater.unused_data)


def _spanning_member(handle: IO[bytes], kept: range, rows: int) -> bytes | None:
    """The compressed member of a joined base whose rows are exactly `kept`, or None when the base has no member index or no member spans `kept`. Every member's bytes are checked against the CRC-32 the member index records, and the trailer member against the index's row total and the head's `rows`, before the member is used, as `_indexed_records` checks a single file's, so a range that inflates its own member alone refuses whatever damage a range that reads the base to its end refuses, and all ranges of a configuration agree on whether the store loads."""
    handle.seek(0)
    index = _member_index(handle)
    if not index:
        return None
    bounds = list(itertools.accumulate((member_rows for member_rows, _length, _crc in index), initial=0))
    spans = list(zip(bounds, bounds[1:]))
    if (kept.start, kept.stop) not in spans:
        return None
    held_at = spans.index((kept.start, kept.stop))
    handle.seek(_first_record_member(handle))
    held = b""
    for member, (_rows, length, crc) in enumerate(index):
        if member == held_at:
            held = handle.read(length)
            found = (zlib.crc32(held), len(held))
        else:
            found = _crc32_of(handle, length)
        if found != (crc, length):
            raise ValueError("a store member does not match its member index")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    trailer = inflater.decompress(handle.read())
    if trailer != f"{ROW_COUNT_TRAILER}\t{rows}\n".encode() or bounds[-1] != rows:
        raise ValueError("a store base's trailer does not count its head's rows")
    if not inflater.eof or inflater.unused_data:
        raise ValueError("a store base has bytes after its trailer")
    return held


def _inflated_runs(member: bytes | memoryview) -> Iterator[bytes]:
    """One gzip member's decompressed bytes as runs of whole lines, inflated a piece at a time. A member that ends early, inside a line, or has bytes after its end raises `ValueError`."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    view = memoryview(member)
    tail = b""
    try:
        for at in range(0, len(view), _INFLATE_STEP):
            data = tail + inflater.decompress(view[at : at + _INFLATE_STEP])
            cut = data.rfind(b"\n") + 1
            if cut:
                yield data[:cut]
            tail = data[cut:]
    finally:
        view.release()
    if tail or not inflater.eof or inflater.unused_data:
        raise ValueError("a store member ends inside a record or has bytes after its end")


def _member_runs(held: bytes, kept: range) -> Iterator[bytes]:
    """The records of a member that spans `kept`, as runs of whole lines, checked to hold exactly `kept`'s rows."""
    rows = 0
    for run in _inflated_runs(held):
        rows += run.count(b"\n")
        yield run
    if rows != len(kept):
        raise ValueError(f"a store member holds {rows} records for a range of {len(kept)} rows")


def _line_start(run: bytes, lines: int) -> int:
    at = 0
    for _ in range(lines):
        at = run.index(b"\n", at) + 1
    return at


def _base_runs(handle: IO[bytes], entry: StoreEntry, kept: range) -> Iterator[bytes]:
    """A base's records in `kept`, as runs of whole lines, read through every member to the end: its header line dropped, its trailer line checked to count every record, and that count checked against the head's, so a range of any bounds refuses what every other range refuses."""
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        stream.readline()
        ordinal = 0
        tail = b""
        trailer = b""
        while chunk := stream.read(1 << 20):
            if trailer:
                raise ValueError("a store holds records after its trailer")
            data = tail + chunk
            cut = data.rfind(b"\n") + 1
            if not cut:
                tail = data
                continue
            run, tail = data[:cut], data[cut:]
            last = run.rfind(b"\n", 0, cut - 1) + 1
            if run[last:].startswith(_TRAILER_PREFIX):
                run, trailer = run[:last], run[last:]
            rows = run.count(b"\n")
            low, high = max(kept.start, ordinal), min(kept.stop, ordinal + rows)
            if low < high:
                yield run[_line_start(run, low - ordinal) : _line_start(run, high - ordinal)]
            ordinal += rows
    if tail or trailer != f"{ROW_COUNT_TRAILER}\t{ordinal}\n".encode() or ordinal != entry.rows:
        raise ValueError("a store base's records do not match its trailer and its head")


def _ordinal_rows(
    handle: IO[bytes], output: str, generation: int, number: int
) -> Iterator[tuple[int, bytes]]:
    """One store segment's upserts from an open handle, from its start, each as its ordinal and its record with its newline, after checking its header names the output, generation, number and the merge by ordinal (`row_delta.check_segment_header`). Ordinals must strictly ascend."""
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        row_delta.check_segment_header(
            row_delta.gzipped_header(stream, output, number),
            output,
            generation,
            number,
            row_delta.MERGE_ORDINAL,
        )
        previous = -1
        for line in stream:
            op, ordinal, record = line.split(b"\t", 2)
            at = int(ordinal)
            if op != row_delta.UPSERT or at <= previous:
                raise ValueError(
                    f"segment {number} of {output} holds a row out of ordinal order or not an upsert"
                )
            previous = at
            yield at, record


def _segment_upserts(
    opened: row_delta.OpenParts, config: str, entry: StoreEntry, kept: range
) -> Iterator[tuple[int, bytes]]:
    """The segments' upserts at ordinals in `kept`, in ordinal order, the latest segment's winning at each."""
    output = store_output(config)

    def ranked(number: int, handle: IO[bytes]) -> Iterator[tuple[int, int, bytes]]:
        for at, record in _ordinal_rows(handle, output, entry.generation, number):
            if at in kept:
                yield at, -number, record

    streams = [ranked(number, handle) for number, handle in enumerate(opened.handles[1:], start=1)]
    held: tuple[int, int, bytes] | None = None
    for item in heapq.merge(*streams):
        if held is not None and item[0] == held[0]:
            continue
        if held is not None:
            yield held[0], held[2]
        held = item
    if held is not None:
        yield held[0], held[2]


def _upserted(runs: Iterable[bytes], kept: range, upserts: Iterator[tuple[int, bytes]]) -> Iterator[bytes]:
    """`runs`, the records of `kept` in order, with each record an upsert names replaced; a run holding none passes through as it was read."""
    pending = next(upserts, None)
    ordinal = kept.start
    for run in runs:
        rows = run.count(b"\n")
        if pending is None or pending[0] >= ordinal + rows:
            yield run
            ordinal += rows
            continue
        lines = run.split(b"\n")
        while pending is not None and pending[0] < ordinal + rows:
            if pending[0] < ordinal:
                raise ValueError(f"a store segment's upsert at row {pending[0]} is out of order")
            lines[pending[0] - ordinal] = pending[1].rstrip(b"\n")
            pending = next(upserts, None)
        yield b"\n".join(lines)
        ordinal += rows
    if pending is not None:
        raise ValueError(f"a store segment names row {pending[0]}, past its base's records")


def _position_parts(run: bytes) -> bytes:
    """A run of whole records, each cut to its position part (`_position_part`)."""
    return b"".join(_position_part(line + b"\n") for line in run.split(b"\n")[:-1])


def _record_offsets(records: bytes | bytearray) -> "array[int]":
    """Each record's end offset in a buffer of whole records, after a leading zero."""
    offsets: array[int] = array("I", [0])
    at = 0
    while at < len(records):
        at = records.index(b"\n", at) + 1
        offsets.append(at)
    return offsets


@contextmanager
def _opened_ages(directory: Path, head: SetHead) -> Iterator[IO[bytes]]:
    """The ages column the head lists, opened and checked against the size it records."""
    with open(Path(directory) / head.ages.name, "rb") as handle:
        if os.fstat(handle.fileno()).st_size != head.ages.size:
            raise row_delta.HeadError(f"{head.ages.name} is not the {head.ages.size} bytes its head records")
        yield handle


def _column_layout(handle: IO[bytes], head: SetHead) -> tuple[int, dict[str, tuple[int, int, int]]]:
    """The ages column's header checked against the head (its format, the generation it indexes, and each configuration's row count and pass ordinal), and where each configuration's ages start: the byte past the header and, a configuration, its offset, rows and pass ordinal."""
    handle.seek(0)
    line = handle.readline()
    document = json.loads(line)
    if not isinstance(document, dict) or document.get("format") != AGES_FORMAT:
        raise row_delta.HeadError(f"{head.ages.name} is not an {AGES_FORMAT} column")
    if document.get("generation") != head.generation:
        raise row_delta.HeadError(
            f"{head.ages.name} indexes generation {document.get('generation')}, where its head is generation {head.generation}"
        )
    layout: dict[str, tuple[int, int, int]] = {}
    at = len(line)
    for config, rows, ordinal in document["configs"]:
        entry = head.configs.get(config)
        if entry is None or (entry.rows, entry.pass_ordinal) != (rows, ordinal):
            raise row_delta.HeadError(f"{head.ages.name} describes {config} as its head does not")
        layout[config] = (at, rows, ordinal)
        at += 2 * rows
    return len(line), layout


def _column_ages(
    handle: IO[bytes], head: SetHead, config: str, kept: range
) -> tuple["array[int]", "array[int]"]:
    """The row ages and position ages of `kept`'s rows from the ages column, each the pass its verdict was derived at (`decoded_ages`)."""
    _header, layout = _column_layout(handle, head)
    offset, _rows, ordinal = layout[config]
    raw = os.pread(handle.fileno(), 2 * len(kept), offset + 2 * kept.start)
    if len(raw) != 2 * len(kept):
        raise ValueError(f"{head.ages.name} ends inside {config}'s ages")
    return decoded_ages(raw, ordinal)


def _positions_outlive(recorded_lines: Sequence[str], environment: EnvironmentStamp) -> bool:
    """Whether a store whose row stamp moved may still serve position verdicts: no `POSITION_ROW_STAMP_LABELS` line differs between the recorded stamp and this pass's."""
    recorded = EnvironmentStamp(lines=tuple(recorded_lines)).labels
    current = environment.labels
    return all(recorded.get(label) == current.get(label) for label in POSITION_ROW_STAMP_LABELS)


def next_pass_ordinal(store: RowStore | None) -> int:
    return 0 if store is None else store.pass_ordinal + 1


def store_header(
    environment: EnvironmentStamp,
    subset_digest: str,
    pass_ordinal: int,
    family_keys: Mapping[str, str],
    position_environment: EnvironmentStamp | None = None,
    position_keys: Mapping[str, str] | None = None,
) -> bytes:
    """Return a store's first line: the format, the two stamps and the two key maps `load_store` compares, the pass ordinal and the subset digest, as one JSON object with sorted keys, so two writers over the same inputs write the same bytes."""
    header = {
        "format": STORE_FORMAT,
        "environment": list(environment.lines),
        "family_keys": {name: family_keys[name] for name in sorted(family_keys)},
        "pass_ordinal": pass_ordinal,
        "position_environment": (None if position_environment is None else list(position_environment.lines)),
        "position_keys": (
            None if position_keys is None else {name: position_keys[name] for name in sorted(position_keys)}
        ),
        "subset_digest": subset_digest,
    }
    return (json.dumps(header, sort_keys=True) + "\n").encode()


def _open_member(raw: IO[bytes]) -> gzip.GzipFile:
    """Open one gzip member over `raw` with the settings every member of a store uses: the mtime fixed at zero, so identical passes write identical bytes, and compression level 1, because a store is written once and read once per row range and a higher level would add compression time to every cycle."""
    return gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1)


# The subfield of a joined store's header member that holds its member index, and one entry of that index per segment: the segment's row count, its compressed length in bytes, and the CRC-32 of those bytes.
_MEMBER_INDEX_ID = b"AM"
_MEMBER_ENTRY = struct.Struct("<QQI")


def _crc32_of(stream: IO[bytes], length: int) -> tuple[int, int]:
    """Return the CRC-32 of the next `length` bytes of `stream` and how many bytes it read, which is fewer at the end of the file."""
    crc = read = 0
    while read < length:
        chunk = stream.read(min(1 << 20, length - read))
        if not chunk:
            break
        crc = zlib.crc32(chunk, crc)
        read += len(chunk)
    return crc, read


def _indexed_member(payload: bytes, index: bytes) -> bytes:
    """Return one gzip member holding `payload`, written with `_open_member`'s settings, whose header carries `index` in an extra-field subfield (RFC 1952's FEXTRA, under `_MEMBER_INDEX_ID`). A gzip reader skips the extra field, so the member decompresses as `_open_member`'s would."""
    compressor = zlib.compressobj(1, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = compressor.compress(payload) + compressor.flush()
    extra = _MEMBER_INDEX_ID + struct.pack("<H", len(index)) + index
    return b"".join(
        (
            b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x04\xff",
            struct.pack("<H", len(extra)),
            extra,
            body,
            struct.pack("<II", zlib.crc32(payload), len(payload) & 0xFFFFFFFF),
        )
    )


def _member_index(stream: IO[bytes]) -> list[tuple[int, int, int]] | None:
    """Return the member index `_indexed_member` put in the first gzip header of the file `stream` is at the start of, or None when that header carries none."""
    head = stream.read(12)
    if len(head) < 12 or head[:3] != b"\x1f\x8b\x08" or not head[3] & 0x04:
        return None
    extra = stream.read(int.from_bytes(head[10:12], "little"))
    at = 0
    while at + 4 <= len(extra):
        length = int.from_bytes(extra[at + 2 : at + 4], "little")
        field = extra[at + 4 : at + 4 + length]
        if (
            extra[at : at + 2] == _MEMBER_INDEX_ID
            and len(field) == length
            and length % _MEMBER_ENTRY.size == 0
        ):
            return list(_MEMBER_ENTRY.iter_unpack(field))
        at += 4 + length
    return None


def _indexed_records(
    path: Path, header: bytes, first_row: int, stop_row: int | None, positions_only: bool = False
) -> tuple[bytes | bytearray, int] | None:
    """Return the records of rows `[first_row, stop_row)` of a joined single file and the table's row count by inflating the one member that holds exactly those rows, or None when the caller must read the whole file instead: the store has no member index, no member starts and ends on the range's bounds, the file's header member is not `header`, or the file disagrees with the index. It serves `_load_single`; a range under the set head finds its member through the head's pinned descriptor instead (`_spanning_member`). `header` is the line the caller read through its own open of `path`, so a file replaced over the path between the two opens (`os.replace`, as a test or a hand copy does) is read whole through the caller's handle rather than paired with the header of the file it replaced. Every segment's compressed bytes are checked against the CRC-32 the index records, and the trailer member against the index's row total, before any record is used. A range therefore loads here only from a file whose every byte is the one `join_store_segments` wrote, which the whole-file read would also load, so all ranges of one configuration agree on whether the store loads, whichever of the two reads each takes. The segment is inflated into a buffer of the size its gzip trailer records, so its records are held once, or with `positions_only` a piece at a time with each record cut to its position part (`_inflated_position_parts`); a range over several segments reads the whole file instead, because joining their records would hold them twice."""
    try:
        with path.open("rb") as stream:
            index = _member_index(stream)
            if not index:
                return None
            bounds = list(itertools.accumulate((rows for rows, _length, _crc in index), initial=0))
            spans = list(zip(bounds, bounds[1:]))
            stop = bounds[-1] if stop_row is None else stop_row
            if (first_row, stop) not in spans:
                return None
            held_at = spans.index((first_row, stop))
            stream.seek(0)
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            consumed = 0
            head = bytearray()
            while not inflater.eof:
                chunk = stream.read(1 << 16)
                if not chunk:
                    return None
                head += inflater.decompress(chunk)
                consumed += len(chunk)
            if head != header:
                return None
            stream.seek(consumed - len(inflater.unused_data))
            held = b""
            for member, (_rows, length, crc) in enumerate(index):
                if member == held_at:
                    held = stream.read(length)
                    found = (zlib.crc32(held), len(held))
                else:
                    found = _crc32_of(stream, length)
                if found != (crc, length):
                    return None
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            trailer = inflater.decompress(stream.read())
            if (
                trailer != f"{ROW_COUNT_TRAILER}\t{bounds[-1]}\n".encode()
                or not inflater.eof
                or inflater.unused_data
            ):
                return None
            records: bytes | bytearray
            if positions_only:
                records = _inflated_position_parts(held)
            else:
                records = zlib.decompress(
                    held, 16 + zlib.MAX_WBITS, max(1, int.from_bytes(held[-4:], "little"))
                )
    except OSError, ValueError, IndexError, zlib.error:
        return None
    if records.count(b"\n") != stop - first_row or not records.endswith(b"\n"):
        return None
    return records, bounds[-1]


# Where a record's row verdict starts, after the row check digest, the settled digest and the tab after each, and the byte that opens a divergent row's verdict there.
_ROW_VERDICT_AT = ROW_CHECK_WIDTH + 1 + SETTLED_DIGEST_WIDTH + 1
_DIVERGENT_ROW = ord("P")


def _position_part(record: bytes) -> bytes:
    """Return a record with its row verdict replaced by the `-` a clean row's record holds there, keeping the row check digest, the settled digest, the position verdict and both ages. A store whose row stamp moved serves no row verdict, so `load_store` keeps only this part of each record, and `RowStore.settled`, `RowStore.serve_position` and the ages read it unchanged. A trailing newline is kept."""
    if record[_ROW_VERDICT_AT] != _DIVERGENT_ROW:
        return record
    return record[:_ROW_VERDICT_AT] + b"-\t" + record.split(b"\t", 8)[8]


def _inflated_position_parts(member: bytes) -> bytearray:
    """Return the records of one segment's gzip member with each cut to `_position_part`, inflated a piece at a time, so the segment's whole records are never held at once. A member that ends early, or has bytes after its end, raises `ValueError`."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    kept = bytearray()
    tail = b""
    view = memoryview(member)
    step = 1 << 20
    for at in range(0, len(member), step):
        records = (tail + inflater.decompress(view[at : at + step])).split(b"\n")
        tail = records.pop()
        for record in records:
            kept += _position_part(record)
            kept += b"\n"
    if tail or not inflater.eof or inflater.unused_data:
        raise ValueError("a store segment ends inside a record or has bytes after its member")
    return kept


def _record_arrays(records: bytes | bytearray) -> tuple["array[int]", "array[int]", "array[int]"]:
    """Return the packed arrays `RowStore` keeps beside a buffer of whole records: each record's end offset after a leading zero, its row age and its position age."""
    offsets: array[int] = array("I", [0])
    ages: array[int] = array("i")
    position_ages: array[int] = array("i")
    at = 0
    while at < len(records):
        end = records.index(b"\n", at)
        last_tab = records.rindex(b"\t", at, end)
        position_ages.append(int(records[last_tab + 1 : end]))
        ages.append(int(records[records.rindex(b"\t", at, last_tab) + 1 : last_tab]))
        at = end + 1
        offsets.append(at)
    return offsets, ages, position_ages


class RowWriter:
    """One configuration's store being written, one record per subset row in table order. The row count is a trailer instead of a header field, so the header can be written before the count is known, and a truncated store has no trailer and fails to load. A writer with a `path` writes the configuration's whole file to a temporary path and moves it into place with `os.replace`, as the tests' stores are written. A writer with no `path` builds one gzip member in memory, which `close` leaves on `member` for the range to ship home (`oracle.ship`) and the stores' writer to compare or write (`StoreSet.put`): one row range of a cut configuration's records only, with no header or trailer, which the stores' writer places between a header member and a trailer member (`_joined_pieces`) once every range of the configuration has returned, or with `whole` an uncut configuration's whole file, header and trailer included, byte for byte the file a writer with a `path` writes."""

    def __init__(
        self,
        path: Path | None,
        environment: EnvironmentStamp,
        subset_digest: str,
        pass_ordinal: int,
        family_keys: Mapping[str, str],
        position_environment: EnvironmentStamp | None = None,
        position_keys: Mapping[str, str] | None = None,
        *,
        whole: bool = False,
    ) -> None:
        self.pass_ordinal = pass_ordinal
        self.rows = 0
        self.member: bytes | None = None
        self._raw: IO[bytes]
        self._framed = path is not None or whole
        if path is None:
            self.path = None
            self._scratch = None
            self._memory = io.BytesIO()
            self._raw = self._memory
        else:
            self.path = Path(path)
            self._scratch = self.path.with_name(self.path.name + ".tmp")
            self._memory = None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._raw = self._scratch.open("wb")
        self._stream = _open_member(self._raw)
        if self._framed:
            self._stream.write(
                store_header(
                    environment, subset_digest, pass_ordinal, family_keys, position_environment, position_keys
                )
            )

    def append(
        self,
        codepoints: Sequence[int],
        cached: CachedRow | None,
        derived_at_pass: int,
        position: PositionVerdict = UNSHAPED,
        position_at_pass: int = 0,
        settled: str = NO_SETTLED_DIGEST,
    ) -> None:
        """Record one row, divergent or clean. Every subset row gets a record, in table order, because the ordinal is the key and a clean row with no age could never be re-derived on schedule. `derived_at_pass` is the pass the row verdict was computed at: `RowStore.age(index)` for a verdict this pass only served, `self.pass_ordinal` for one it derived, and `UNCOVERED_AGE` for a derived row whose glyph names `unreachable_glyph_heads` refuses. `position_at_pass` is the same for the position verdict, from `RowStore.position_age(index)` when it was served. Recording this pass for a served verdict would reset its age and defeat the age cap. `settled` is the digest of the settled stream the row verdict was derived against: the served record's (`RowStore.settled`) for a served verdict, and `oracle_positions.settled_digest` of this pass's walk for a derived one."""
        self._stream.write(
            (
                encode_record(codepoints, cached, derived_at_pass, position, position_at_pass, settled) + "\n"
            ).encode()
        )
        self.rows += 1

    def close(self) -> None:
        if self._framed:
            self._stream.write(f"{ROW_COUNT_TRAILER}\t{self.rows}\n".encode())
        self._stream.close()
        if self._memory is not None:
            self.member = self._memory.getvalue()
            self._memory.close()
            return
        assert self._scratch is not None and self.path is not None
        self._raw.close()
        os.replace(self._scratch, self.path)

    def abandon(self) -> None:
        """Delete a partly written store without promoting it, so a failed configuration leaves nothing for the next pass to load."""
        try:
            self._stream.close()
            self._raw.close()
        except OSError, ValueError:
            pass
        if self._scratch is not None:
            self._scratch.unlink(missing_ok=True)

    def __enter__(self) -> "RowWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abandon()


def _joined_pieces(
    header_line: bytes, segments: Sequence[tuple[int, bytes | memoryview]]
) -> list[bytes | memoryview]:
    """The pieces of a cut configuration's whole file, in order: a header member carrying the member index (`_indexed_member`: each member's row count, compressed length and CRC-32), each range's member as it was shipped, and a trailer member counting every row."""
    index = b"".join(_MEMBER_ENTRY.pack(rows, len(member), zlib.crc32(member)) for rows, member in segments)
    tail = io.BytesIO()
    with _open_member(tail) as trailer:
        trailer.write(f"{ROW_COUNT_TRAILER}\t{sum(rows for rows, _member in segments)}\n".encode())
    return [_indexed_member(header_line, index), *(member for _rows, member in segments), tail.getvalue()]


def join_store_segments(
    target: Path,
    segments: Sequence[tuple[int, bytes | memoryview]],
    environment: EnvironmentStamp,
    subset_digest: str,
    pass_ordinal: int,
    family_keys: Mapping[str, str],
    position_environment: EnvironmentStamp | None = None,
    position_keys: Mapping[str, str] | None = None,
    *,
    rows_bytes: int = 0,
) -> row_delta.Part:
    """Write one cut configuration's whole file to `target` from the members its row ranges returned, given in row order as each range's row count and its records' gzip member (`RowWriter` with no path), through `row_delta.write_plain_part`, and return its part, which records `rows_bytes` as its records' bytes: a header member, then each member in row order, then a trailer member counting every row (`_joined_pieces`). The header member's gzip header carries the member index (`_indexed_member`): each member's row count, compressed length and CRC-32, which lets a range inflate its own member alone. Nothing is decompressed. The result is a multi-member gzip stream and is not byte-identical to the single-member store the same records would make, but its decompressed content is, since a gzip reader skips a header's extra field. It is the form a cut configuration's base takes (`StoreSet.put`) and its materialized form (`materialized_store`)."""
    pieces = _joined_pieces(
        store_header(
            environment, subset_digest, pass_ordinal, family_keys, position_environment, position_keys
        ),
        segments,
    )
    target = Path(target)
    rows = sum(member_rows for member_rows, _member in segments)
    return row_delta.write_plain_part(target.parent, target.name, pieces, rows, rows_bytes)


SET_HEAD_FORMAT = "ams-m1-oracle-rows-head/1"
AGES_FORMAT = "ams-m1-oracle-ages/1"
SET_HEAD_NAME = f"{STORE_STEM}.head"
WHOLE_CUT = "whole"
_TRAILER_PREFIX = f"{ROW_COUNT_TRAILER}\t".encode()
_INFLATE_STEP = 1 << 18
_COMPARE_ROWS = 8192
_PART_NAMES = re.compile(rf"{STORE_STEM}-.+\.tsv\.gz\.\d+\.(?:base|\d+\.delta)|{STORE_STEM}\.\d+\.ages")
_SET_NAMES = re.compile(rf"{_PART_NAMES.pattern}|{re.escape(SET_HEAD_NAME)}")
_AGES_PART = operator.methodcaller("rsplit", b"\t", 2)


def store_output(config: str) -> str:
    """The name a configuration's store had as a single file, which its base and segments are named from (`row_delta.base_name`, `row_delta.segment_name`)."""
    return f"{STORE_STEM}-{config}.tsv.gz"


def ages_name(generation: int) -> str:
    """The ages column's name for the set head's `generation`."""
    return f"{STORE_STEM}.{generation}.ages"


def _config_of(path: Path) -> str | None:
    name = Path(path).name
    prefix, suffix = f"{STORE_STEM}-", ".tsv.gz"
    if name.startswith(prefix) and name.endswith(suffix) and len(name) > len(prefix) + len(suffix):
        return name[len(prefix) : -len(suffix)]
    return None


@dataclass(frozen=True)
class StoreEntry:
    """One configuration under the stores' set head: the header its whole file carries, as `store_header` writes it (so the materialized header is that function's line byte for byte), its row count (the whole file's trailer), its cut (each member's row count, from the pass that last wrote the stores, or None for a configuration one range wrote whole), and its parts, a base and segments of one generation."""

    header: dict
    rows: int
    cut: tuple[int, ...] | None
    generation: int
    base: row_delta.Part
    segments: tuple[row_delta.Part, ...] = ()

    @property
    def pass_ordinal(self) -> int:
        return int(self.header["pass_ordinal"])

    @property
    def parts(self) -> tuple[row_delta.Part, ...]:
        return (self.base, *self.segments)

    @property
    def header_line(self) -> bytes:
        return (json.dumps(self.header, sort_keys=True) + "\n").encode()

    def view(self, config: str) -> row_delta.Head:
        """The configuration's parts as a head of their own, which `row_delta.open_parts`, `fold_due` and `fold_forced` read."""
        return row_delta.Head(
            store_output(config), self.generation, 0, {}, self.base, self.segments, row_delta.MERGE_ORDINAL
        )

    def document(self) -> dict:
        return {
            "header": self.header,
            "rows": self.rows,
            "cut": WHOLE_CUT if self.cut is None else list(self.cut),
            "generation": self.generation,
            "base": self.base.document(),
            "segments": [part.document() for part in self.segments],
        }

    @classmethod
    def parse(cls, config: str, document: object) -> StoreEntry:
        if not isinstance(document, dict):
            raise row_delta.HeadError(f"the stores' head describes {config} as {document!r}")
        try:
            cut = document["cut"]
            entry = cls(
                dict(document["header"]),
                int(document["rows"]),
                None if cut == WHOLE_CUT else tuple(int(rows) for rows in cut),
                int(document["generation"]),
                row_delta.Part.parse(document["base"]),
                tuple(row_delta.Part.parse(part) for part in document["segments"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise row_delta.HeadError(f"the stores' head does not describe {config}: {error!r}") from None
        output = store_output(config)
        if entry.base.name != row_delta.base_name(output, entry.generation):
            raise row_delta.HeadError(f"the stores' head lists {entry.base.name} as {config}'s base")
        for number, part in enumerate(entry.segments, start=1):
            if part.name != row_delta.segment_name(output, entry.generation, number):
                raise row_delta.HeadError(
                    f"the stores' head lists {part.name} as {config}'s segment {number}"
                )
        return entry


@dataclass(frozen=True)
class SetHead:
    """The stores' set head, `oracle-rows.head`, one for every configuration the oracle compares, so the set commits all or none: its generation, which moves on every commit, each configuration's entry (`StoreEntry`), and the ages column it commits with, which names the same generation."""

    generation: int
    configs: dict[str, StoreEntry]
    ages: row_delta.Part

    def names(self) -> set[str]:
        return {
            self.ages.name,
            *(part.name for entry in self.configs.values() for part in (entry.base, *entry.segments)),
        }

    def document(self) -> dict:
        return {
            "format": SET_HEAD_FORMAT,
            "output": STORE_STEM,
            "generation": self.generation,
            "ages": self.ages.document(),
            "configs": {config: entry.document() for config, entry in self.configs.items()},
        }


def read_set_head(directory: Path) -> SetHead | None:
    """Return the stores' set head under `directory`, or None when there is none. A head that does not parse, or names another format or output, or a part under a name its place does not give, raises `row_delta.HeadError`."""
    try:
        raw = (Path(directory) / SET_HEAD_NAME).read_bytes()
    except FileNotFoundError:
        return None
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise row_delta.HeadError(f"{SET_HEAD_NAME} does not parse: {error}") from None
    if not isinstance(document, dict) or document.get("format") != SET_HEAD_FORMAT:
        raise row_delta.HeadError(f"{SET_HEAD_NAME} is not an {SET_HEAD_FORMAT} head")
    if document.get("output") != STORE_STEM or not isinstance(document.get("configs"), dict):
        raise row_delta.HeadError(f"{SET_HEAD_NAME} names no stores")
    try:
        generation = int(document["generation"])
    except KeyError, TypeError, ValueError:
        raise row_delta.HeadError(f"{SET_HEAD_NAME} names no generation") from None
    ages = row_delta.Part.parse(document.get("ages"))
    if ages.name != ages_name(generation):
        raise row_delta.HeadError(f"{SET_HEAD_NAME} lists {ages.name} as generation {generation}'s ages")
    configs = {
        str(config): StoreEntry.parse(str(config), entry) for config, entry in document["configs"].items()
    }
    return SetHead(generation, configs, ages)


def decoded_ages(raw: bytes, pass_ordinal: int) -> tuple["array[int]", "array[int]"]:
    """A run of the ages column, two signed bytes a row (the row verdict's, then the position verdict's), as the passes each verdict was derived at: `pass_ordinal` less the byte, or `UNCOVERED_AGE` where the byte is `UNCOVERED_AGE`."""
    values: array[int] = array("b")
    values.frombytes(raw)
    ages: list[array[int]] = []
    for column in (values[0::2], values[1::2]):
        derived = array("i", map(pass_ordinal.__sub__, column))
        if UNCOVERED_AGE in column:
            for at, value in enumerate(column):
                if value == UNCOVERED_AGE:
                    derived[at] = UNCOVERED_AGE
        ages.append(derived)
    return ages[0], ages[1]


def encoded_ages(derived: Sequence[int], pass_ordinal: int, config: str, first: int) -> "array[int]":
    """The column bytes of one verdict's derivation passes for rows from `first`: `pass_ordinal` less each, as a signed byte, and `UNCOVERED_AGE` for a row recorded there. A pass above the ordinal, or an age outside a signed byte, raises `ValueError` naming the row."""
    values = list(map(pass_ordinal.__sub__, derived))
    if UNCOVERED_AGE in derived:
        for at, value in enumerate(derived):
            if value == UNCOVERED_AGE:
                values[at] = UNCOVERED_AGE
    if values and (min(values) < 0 or max(values) > 127):
        for at, (value, recorded) in enumerate(zip(values, derived)):
            if recorded != UNCOVERED_AGE and not 0 <= value <= 127:
                raise ValueError(
                    f"{config} row {first + at} records pass {recorded} under pass ordinal {pass_ordinal}, which the ages column cannot hold"
                )
    return array("b", values)


class _RecordLines:
    """Records as lines without their newlines, from runs of whole lines, taken a block at a time; with `framed`, the first line (a header) is dropped and a trailer line ends them. With `upserts`, the record at each ordinal an upsert names is replaced as it is read."""

    def __init__(
        self,
        runs: Iterable[bytes],
        *,
        framed: bool,
        upserts: Iterator[tuple[int, bytes]] | None = None,
    ) -> None:
        self._runs = iter(runs)
        self._framed = framed
        self._lines: list[bytes] = []
        self._at = 0
        self._tail = b""
        self._ordinal = 0
        self._upserts = upserts
        self._pending = None if upserts is None else next(upserts, None)
        self.ended = False
        self.trailer: int | None = None

    def _refill(self) -> None:
        run = next(self._runs, None)
        if run is None:
            if self._tail:
                raise ValueError("store records end inside a record")
            self.ended = True
            return
        if self.trailer is not None:
            raise ValueError("a store holds records after its trailer")
        lines = (self._tail + run).split(b"\n")
        self._tail = lines.pop()
        if self._framed and lines:
            lines.pop(0)
            self._framed = False
        if lines and lines[-1].startswith(_TRAILER_PREFIX):
            self.trailer = int(lines.pop()[len(_TRAILER_PREFIX) :])
        start = self._ordinal + len(self._lines)
        while self._pending is not None and self._pending[0] < start + len(lines):
            if self._pending[0] < start:
                raise ValueError(f"a store segment's upsert at row {self._pending[0]} is out of order")
            lines[self._pending[0] - start] = self._pending[1].rstrip(b"\n")
            self._pending = next(self._upserts, None) if self._upserts is not None else None
        self._lines = self._lines[self._at :] + lines
        self._ordinal += self._at
        self._at = 0

    def take(self, count: int) -> list[bytes]:
        while len(self._lines) - self._at < count and not self.ended:
            self._refill()
        taken = self._lines[self._at : self._at + count]
        self._at += len(taken)
        return taken

    def finished(self) -> None:
        """Check that every upsert landed on a record."""
        if self._pending is not None:
            raise ValueError(f"a store segment names row {self._pending[0]}, past its base's records")

    def close(self) -> None:
        """Close the runs' source, so a view it took of a shipped member is released before the member's mapping closes."""
        close = getattr(self._runs, "close", None)
        if close is not None:
            close()


def _changed_records(block: list[bytes], standing: list[bytes], parts: list[list[bytes]]) -> list[int]:
    """The places in `block` whose record, less its two trailing ages (`parts`, each record split off its ages), differs from the standing record at the same place. Where few lines differ at all, only those are split; where many do, as on a pass that re-derives every row, both blocks are split whole, through C-level maps."""
    differing = list(itertools.compress(range(len(block)), map(operator.ne, block, standing)))
    if len(differing) * 8 < len(block):
        return [at for at in differing if parts[at][0] != standing[at].rsplit(b"\t", 2)[0]]
    mine = map(operator.itemgetter(0), parts)
    theirs = map(operator.itemgetter(0), map(_AGES_PART, standing))
    return list(itertools.compress(range(len(block)), map(operator.ne, mine, theirs)))


def _member_records(members: Sequence[tuple[int, bytes | memoryview]]) -> Iterator[bytes]:
    for _rows, member in members:
        yield from _inflated_runs(member)


def _part_runs(handle: IO[bytes]) -> Iterator[bytes]:
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        while chunk := stream.read(1 << 20):
            yield chunk


SEGMENT_ROUTE = "segment"
BASE_ROUTE = "base"


@dataclass
class StoreRoute:
    """How one configuration's store is written this pass, decided before any range starts (`StoreSet`), under #618's decisions 72 to 75 as change 31 applies them, with no size test:

    - `segment`: one segment of the records whose line less its two ages differs from the prior's at its ordinal, beside the ages column, or the column alone when none differs.
    - `base`: the configuration's whole file as the next generation's base, with its `reason`: `first`, no prior for the configuration; `unusable`, a set head that does not parse, a part missing or not the size and sha256 its head records, or a single file that does not read, which the cycle reports as `rewritten whole`; `rederived`, the prior's `format` or `subset` stamp line moved, so no ordinal names the row it named (a letter batch, #618's decision 57); or `fold`, its fold is due at `row_delta.SEGMENT_CAP` and the set holds the pass's fold, or holds `row_delta.FOLD_OVERRUN` segments past its cap (`forced`).

    `adopted` says `prior` is the single file a tree from before the segment format holds, linked as generation 0's base under a head no writer has committed. `opened` holds the prior's parts open for the compare on the segment route.
    """

    config: str
    route: str
    reason: str = ""
    prior: StoreEntry | None = None
    adopted: bool = False
    forced: bool = False
    opened: row_delta.OpenParts | None = None

    def unusable(self) -> None:
        self.close()
        self.route, self.reason, self.forced = BASE_ROUTE, "unusable", False

    def close(self) -> None:
        if self.opened is not None:
            self.opened.close()
            self.opened = None


def _rederived(header: Mapping, environment: EnvironmentStamp) -> bool:
    """Whether a prior's header names another store format or subset table than this pass's stamp, so its ordinals no longer name this pass's rows."""
    recorded = EnvironmentStamp(lines=tuple(header["environment"])).labels
    current = environment.labels
    return (
        header.get("format") != STORE_FORMAT
        or recorded.get("format") != current.get("format")
        or recorded.get("subset") != current.get("subset")
        or header.get("subset_digest") != current.get("subset")
    )


def _adopted_entry(directory: Path, config: str) -> StoreEntry | None:
    """Link the single file a tree from before the segment format holds for `config` as generation 0's base, which writes no data, and return an entry over it no writer has committed, from what the file's own bytes give, read through once: its header, its records' row count and bytes, checked against its trailer, its cut from its member index, and its size and sha256. None, with no link left, when it does not read."""
    single = store_path(directory, config)
    name = row_delta.base_name(store_output(config), 0)
    link = Path(directory) / name
    os.link(single, link)
    try:
        with open(link, "rb") as handle:
            index = _member_index(handle)
            handle.seek(0)
            digest = hashlib.sha256()
            size = 0
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
            handle.seek(0)
            with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
                header = json.loads(stream.readline())
                rows = rows_bytes = 0
                trailer = b""
                tail = b""
                while chunk := stream.read(1 << 20):
                    data = tail + chunk
                    cut = data.rfind(b"\n") + 1
                    run, tail = data[:cut], data[cut:]
                    if trailer and run:
                        raise ValueError("records after the trailer")
                    last = run.rfind(b"\n", 0, max(0, cut - 1)) + 1
                    if run[last:].startswith(_TRAILER_PREFIX):
                        run, trailer = run[:last], run[last:]
                    rows += run.count(b"\n")
                    rows_bytes += len(run)
        if tail or trailer != f"{ROW_COUNT_TRAILER}\t{rows}\n".encode() or not isinstance(header, dict):
            raise ValueError("a single store file does not end in its trailer")
        cut_rows = None if not index else tuple(member_rows for member_rows, _length, _crc in index)
        if cut_rows is not None and sum(cut_rows) != rows:
            raise ValueError("a single store file's member index does not count its records")
    except OSError, EOFError, ValueError, zlib.error:
        link.unlink(missing_ok=True)
        return None
    return StoreEntry(
        header, rows, cut_rows, 0, row_delta.Part(name, size, digest.hexdigest(), rows, rows_bytes)
    )


def _single_store_reads(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as stream:
            return isinstance(json.loads(stream.readline()), dict)
    except OSError, EOFError, ValueError, zlib.error:
        return False


def _parts_read(opened: row_delta.OpenParts) -> bool:
    """Whether every part of an open configuration's parts passes its header check: the base's first line is a store header, and each segment's header names the merge by ordinal, the output, the generation and its own number."""
    head = opened.head
    try:
        for number, handle in enumerate(opened.handles):
            handle.seek(0)
            with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
                line = stream.readline()
            handle.seek(0)
            if number == 0:
                if not isinstance(json.loads(line), dict):
                    return False
            else:
                row_delta.check_segment_header(
                    line, head.output, head.generation, number, row_delta.MERGE_ORDINAL
                )
    except OSError, EOFError, ValueError, zlib.error:
        return False
    return True


class StoreSet:
    """The oracle stores' writer for one pass, the pooled path's (`oracle.InOrderWriter`) and the serial path's (`oracle.compare_against_baseline`) alike, held in `run_m1`'s parent. Made before any range starts, it plans every configuration's route (`StoreRoute`): the set head read, the staging files a killed writer left and the parts no head lists deleted, which one oracle at a time does over an out dir (`run_m1_gates.oracle_lock`), each single file of a tree from before the segment format adopted by link, and the pass's one fold claimed for the set (`row_delta.claim_fold`) where a configuration's is due; the parts' sha256 checks and the adoptions' read-throughs then run on threads, a part a thread, each hashing through one buffer it reuses (`row_delta.part_hashes_to`, `row_delta.HASH_READ_BYTES` a part), which the first `put` joins, printing `[t] oracle_stores_plan`. A part that fails its check takes the `unusable` route.

    `put` takes one configuration's records as its ranges shipped them: a cut configuration's members, records alone, or a whole configuration's one member, its whole file. On a base route it writes the configuration's whole file as the next generation's base, `_joined_pieces` for a cut configuration and the member as it is for a whole one, under its final name. On the segment route it reads the prior's materialized records in lockstep (its base and segments merged by ordinal, `_RecordLines`) and writes one segment of whole records, keyed by ordinal and gzipped as a base is, holding every record whose line less its two trailing ages differs from the prior's at its ordinal; a pass with none writes no segment. On either route it takes the two ages of every record into the ages column, a signed byte a verdict (`encoded_ages`), so a record re-derived to the same verdict and settled digest moves only its ages. It compares a block of `_COMPARE_ROWS` records at a time, as lists of lines, and splits a record's ages off only where the block's lines differ, so it holds two blocks, its inflate buffers and the column. `finish` writes the column, whole, as `ages_name` of the next generation, once every configuration has an entry, and `commit` (`promote_stores`) puts the set head in place. Every part is written under its final name, which no head lists until the commit, so a pass that stops first leaves only parts the next plan deletes. `finish` prints `[t] oracle_stores`: the records compared and changed, each configuration's segment bytes, the column's bytes, the most segments a configuration holds after the pass, the compare's seconds and the most it held at once.
    """

    def __init__(
        self,
        directory: Path,
        configs: Sequence[str],
        environment: Mapping[str, EnvironmentStamp],
        family_keys: Mapping[str, str],
        position_environment: EnvironmentStamp | None = None,
        position_keys: Mapping[str, str] | None = None,
    ) -> None:
        started = time.perf_counter()
        self.directory = Path(directory)
        self.configs = tuple(configs)
        self._environment = environment
        self._family_keys = family_keys
        self._position_environment = position_environment
        self._position_keys = position_keys
        self._entries: dict[str, StoreEntry] = {}
        self._ages: dict[str, bytes] = {}
        self._column: row_delta.Part | None = None
        self._joined: dict[str, StoreRoute] | None = None
        self._lock = threading.Lock()
        self.compared = 0
        self.changed = 0
        self.seconds = 0.0
        self.held_peak_bytes = 0
        self.hash_seconds = 0.0
        self._started = started
        self._threads_ended = started
        self.directory.mkdir(parents=True, exist_ok=True)
        row_delta.remove_matching(self.directory, row_delta.staging_pattern(_SET_NAMES))
        unreadable = False
        try:
            self._prior = read_set_head(self.directory)
        except row_delta.HeadError:
            self._prior, unreadable = None, True
        row_delta.remove_matching(
            self.directory, _PART_NAMES, () if self._prior is None else self._prior.names()
        )
        routes: dict[str, StoreRoute] = {}
        work: list[Callable[[], None]] = []
        for config in self.configs:
            route = routes[config] = self._first_route(config, unreadable)
            if route.adopted:
                work.append(functools.partial(self._adopt, route))
        due = [
            config
            for config, route in routes.items()
            if route.route == SEGMENT_ROUTE
            and route.prior is not None
            and row_delta.fold_due(route.prior.view(config))
        ]
        if due:
            holds = row_delta.claim_fold(STORE_STEM)
            for config in due:
                prior = routes[config].prior
                assert prior is not None
                if holds or row_delta.fold_forced(prior.view(config)):
                    routes[config].route, routes[config].reason = BASE_ROUTE, "fold"
                    routes[config].forced = not holds
        for route in routes.values():
            if route.route == SEGMENT_ROUTE and not route.adopted:
                work.append(functools.partial(self._check, route))
        self._routes = routes
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, len(work)), thread_name_prefix="oracle-stores-plan"
        )
        self._futures = [self._pool.submit(task) for task in work]
        self._planned = time.perf_counter()

    def _ended(self, started: float) -> None:
        ended = time.perf_counter()
        with self._lock:
            self.hash_seconds += ended - started
            self._threads_ended = max(self._threads_ended, ended)

    def _first_route(self, config: str, unreadable: bool) -> StoreRoute:
        if unreadable:
            return StoreRoute(config, BASE_ROUTE, "unusable")
        if self._prior is None:
            single = store_path(self.directory, config)
            if not single.exists():
                return StoreRoute(config, BASE_ROUTE, "first")
            if not _single_store_reads(single):
                return StoreRoute(config, BASE_ROUTE, "unusable")
            return StoreRoute(config, SEGMENT_ROUTE, adopted=True)
        entry = self._prior.configs.get(config)
        if entry is None:
            return StoreRoute(config, BASE_ROUTE, "first")
        try:
            moved = _rederived(entry.header, self._environment[config])
        except KeyError, TypeError, ValueError:
            return StoreRoute(config, BASE_ROUTE, "unusable", prior=entry)
        if moved:
            return StoreRoute(config, BASE_ROUTE, "rederived", prior=entry)
        return StoreRoute(config, SEGMENT_ROUTE, prior=entry)

    def _adopt(self, route: StoreRoute) -> None:
        started = time.perf_counter()
        try:
            entry = _adopted_entry(self.directory, route.config)
            if entry is None:
                route.adopted = False
                route.unusable()
            else:
                route.prior = entry
                route.opened = row_delta.open_parts(self.directory, entry.view(route.config))
                if _rederived(entry.header, self._environment[route.config]):
                    route.close()
                    route.route, route.reason = BASE_ROUTE, "rederived"
        finally:
            self._ended(started)

    def _check(self, route: StoreRoute) -> None:
        started = time.perf_counter()
        assert route.prior is not None
        try:
            try:
                opened = row_delta.open_parts(self.directory, route.prior.view(route.config))
            except row_delta.HeadError:
                route.unusable()
                return
            route.opened = opened
            with ThreadPoolExecutor(
                max_workers=len(opened.handles), thread_name_prefix="oracle-stores-hash"
            ) as hashing:
                hashed = list(hashing.map(row_delta.part_hashes_to, opened.handles, route.prior.parts))
            if not all(hashed) or not _parts_read(opened):
                route.unusable()
        finally:
            self._ended(started)

    def routes(self) -> dict[str, StoreRoute]:
        """Join the plan's threads and return every configuration's route, printing `[t] oracle_stores_plan` the first time: the plan's seconds, from its start to the end of its own work and its threads', whichever ends last, so the ranges' compute before the first `put` is not among them; how many configurations take each route, the base routes counted again by reason, how many adopt a single file, the seconds the hashes and adoptions took, summed over the configurations' threads, and how long the first `put` waited for them."""
        if self._joined is None:
            waited = time.perf_counter()
            try:
                for future in self._futures:
                    future.result()
            finally:
                self._pool.shutdown(wait=True)
            self._joined = self._routes
            routes = self._joined.values()
            segment = sum(route.route == SEGMENT_ROUTE for route in routes)
            reasons = " ".join(
                f"{reason}={sum(route.route == BASE_ROUTE and route.reason == reason for route in routes)}"
                for reason in ("first", "unusable", "rederived", "fold")
            )
            console.timing(
                "oracle_stores_plan",
                max(self._planned, self._threads_ended) - self._started,
                f"segment_routes={segment} base_routes={len(self._joined) - segment} {reasons} "
                f"adopting={sum(route.adopted for route in routes)} hash_seconds={self.hash_seconds:.2f} "
                f"wait_seconds={time.perf_counter() - waited:.2f}",
            )
        return self._joined

    def put(
        self,
        config: str,
        members: Sequence[tuple[int, bytes | memoryview]],
        whole: bool,
        pass_ordinal: int,
    ) -> StoreEntry:
        """Compare or write one configuration's records, which its ranges shipped as `members` in row order (one member of its whole file when `whole`), recorded under `pass_ordinal`, and keep its entry and its ages for the commit."""
        started = time.perf_counter()
        route = self.routes()[config]
        stamp = self._environment[config]
        header_line = store_header(
            stamp,
            stamp.labels["subset"],
            pass_ordinal,
            self._family_keys,
            self._position_environment,
            self._position_keys,
        )
        rows = sum(member_rows for member_rows, _member in members)
        cut = None if whole else tuple(member_rows for member_rows, _member in members)
        output = store_output(config)
        prior = route.prior
        new = _RecordLines(_member_records(members), framed=whole)
        ages = bytearray()
        part_writer: row_delta.StreamedPart | None = None
        old: _RecordLines | None = None
        if route.route == SEGMENT_ROUTE:
            assert prior is not None and route.opened is not None
            if prior.rows != rows:
                raise ValueError(
                    f"{config}: the stores' head records {prior.rows} rows where this pass's ranges returned {rows}"
                )
            number = len(prior.segments) + 1
            part_writer = row_delta.StreamedPart(
                self.directory,
                row_delta.segment_name(output, prior.generation, number),
                row_delta.segment_header(output, prior.generation, number, row_delta.MERGE_ORDINAL),
                gzipped=True,
            )
            opened = route.opened
            upserts = _segment_upserts(opened, config, prior, range(prior.rows)) if prior.segments else None
            old = _RecordLines(_part_runs(opened.handles[0]), framed=True, upserts=upserts)
        rows_bytes = changed = ordinal = widest = 0
        try:
            while block := new.take(_COMPARE_ROWS):
                block_bytes = sum(map(len, block)) + len(block)
                rows_bytes += block_bytes
                widest = max(widest, block_bytes)
                parts = list(map(_AGES_PART, block))
                if old is not None:
                    standing = old.take(len(block))
                    if len(standing) != len(block):
                        raise ValueError(
                            f"{config}: the standing store ends at row {ordinal + len(standing)}"
                        )
                    if standing != block:
                        assert part_writer is not None
                        for at in _changed_records(block, standing, parts):
                            changed += 1
                            part_writer.write(b"+\t%d\t%s\n" % (ordinal + at, block[at]))
                row_ages = encoded_ages(
                    list(map(int, map(operator.itemgetter(1), parts))), pass_ordinal, config, ordinal
                )
                position_ages = encoded_ages(
                    list(map(int, map(operator.itemgetter(2), parts))), pass_ordinal, config, ordinal
                )
                interleaved = array("b", bytes(2 * len(block)))
                interleaved[0::2] = row_ages
                interleaved[1::2] = position_ages
                ages += interleaved.tobytes()
                ordinal += len(block)
            if new.trailer is not None and new.trailer != ordinal:
                raise ValueError(
                    f"{config}: its whole member's trailer counts {new.trailer} rows of {ordinal}"
                )
            if ordinal != rows:
                raise ValueError(f"{config}: its ranges counted {rows} rows and shipped {ordinal} records")
            if old is not None:
                if old.take(1):
                    raise ValueError(f"{config}: the standing store holds more than {ordinal} rows")
                old.finished()
            if route.route == SEGMENT_ROUTE:
                assert prior is not None and part_writer is not None
                part = part_writer.close()
                entry = StoreEntry(
                    json.loads(header_line),
                    rows,
                    cut,
                    prior.generation,
                    prior.base,
                    prior.segments if part is None else (*prior.segments, part),
                )
            else:
                generation = 0 if prior is None else prior.generation + 1
                name = row_delta.base_name(output, generation)
                base = (
                    row_delta.write_plain_part(self.directory, name, [members[0][1]], rows, rows_bytes)
                    if whole
                    else join_store_segments(
                        self.directory / name,
                        members,
                        stamp,
                        stamp.labels["subset"],
                        pass_ordinal,
                        self._family_keys,
                        self._position_environment,
                        self._position_keys,
                        rows_bytes=rows_bytes,
                    )
                )
                entry = StoreEntry(json.loads(header_line), rows, cut, generation, base)
        except BaseException:
            if part_writer is not None:
                part_writer.abandon()
            raise
        finally:
            new.close()
            if old is not None:
                old.close()
            route.close()
        with self._lock:
            self._entries[config] = entry
            self._ages[config] = bytes(ages)
            self.compared += rows if route.route == SEGMENT_ROUTE else 0
            self.changed += changed
            self.seconds += time.perf_counter() - started
            self.held_peak_bytes = max(self.held_peak_bytes, sum(map(len, self._ages.values())) + 2 * widest)
        return entry

    @property
    def generation(self) -> int:
        """The generation the next set head takes."""
        return 0 if self._prior is None else self._prior.generation + 1

    def complete(self, configs: Iterable[str]) -> bool:
        return all(config in self._entries for config in configs)

    def finish(self) -> None:
        """Write the ages column under the next generation's name, once every configuration has an entry, and print `[t] oracle_stores`."""
        if not self.complete(self.configs):
            return
        generation = self.generation
        header = (
            json.dumps(
                {
                    "format": AGES_FORMAT,
                    "generation": generation,
                    "configs": [
                        [config, self._entries[config].rows, self._entries[config].pass_ordinal]
                        for config in self.configs
                    ],
                }
            )
            + "\n"
        ).encode()
        rows = sum(self._entries[config].rows for config in self.configs)
        self._column = row_delta.write_plain_part(
            self.directory,
            ages_name(generation),
            [header, *(self._ages[config] for config in self.configs)],
            rows,
            2 * rows,
        )
        segment_bytes = ",".join(
            f"{config}:{(self._entries[config].segments[-1].size if self._fresh_segment(config) else 0)}"
            for config in self.configs
        )
        console.timing(
            "oracle_stores",
            self.seconds,
            f"compared={self.compared} changed={self.changed} segment_bytes={segment_bytes} "
            f"ages_bytes={self._column.size} segments={max(len(entry.segments) for entry in self._entries.values())} "
            f"held_mb={self.held_peak_bytes / 1e6:.1f}",
        )

    def _fresh_segment(self, config: str) -> bool:
        entry = self._entries[config]
        route = (self._joined or self._routes)[config]
        prior = route.prior
        return (
            route.route == SEGMENT_ROUTE and prior is not None and len(entry.segments) > len(prior.segments)
        )

    def commit(self, configs: Iterable[str]) -> list[str]:
        """Put the set head in place over every configuration's entry and the column, all of them or none (`promote_stores`), and then delete every part no head lists, and the single files a tree from before the segment format held. A base written because a head or a part did not read is counted as `rewritten whole` (`output_sink.note_rewritten`)."""
        configs = list(configs)
        if self._column is None or not self.complete(configs):
            return []
        head = SetHead(self.generation, {config: self._entries[config] for config in configs}, self._column)
        routes = self._joined or self._routes
        for config in configs:
            if routes[config].reason == "unusable":
                output_sink.note_rewritten(store_output(config))
        output_sink.replace_output(
            self.directory / SET_HEAD_NAME, json.dumps(head.document(), indent=1) + "\n"
        )
        row_delta.remove_matching(self.directory, _PART_NAMES, head.names())
        for config in configs:
            store_path(self.directory, config).unlink(missing_ok=True)
        return configs

    def abandon(self) -> None:
        """Commit nothing: join the plan, close the parts it opened, and delete the links its adoptions made while no head lists them."""
        for future in self._futures:
            with suppress(Exception):
                future.result()
        self._pool.shutdown(wait=True)
        for route in self._routes.values():
            route.close()
            if route.adopted and not (self.directory / SET_HEAD_NAME).exists():
                (self.directory / row_delta.base_name(store_output(route.config), 0)).unlink(missing_ok=True)


def promote_stores(stores: StoreSet | None, configs: Iterable[str]) -> list[str]:
    """Commit a finished run's stores, all of them or none, by putting the set head in place (`StoreSet.commit`), and return the configurations committed. The caller calls this only after the audit's head is committed (`oracle.InOrderWriter` on the pooled path) and after it has checked that neither the stamps nor any family key moved during the run. A store recorded under digests the run did not build from would be served as current on every later pass."""
    if stores is None:
        return []
    return stores.commit(configs)


def discard_stores(out_dir: Path, configs: Iterable[str]) -> None:
    """Delete the stores: the set head, every base, segment and ages column under any generation, their staging files, and each configuration's single file. `--fresh-oracle-cache` does this before a pass that will write replacements, so the next pass trusts only what that pass wrote. A pass that may not write a store never calls this: skipping the read already costs that pass a full derivation, and deleting the store would cost the next pass one as well."""
    out = Path(out_dir)
    (out / SET_HEAD_NAME).unlink(missing_ok=True)
    if out.is_dir():
        row_delta.remove_matching(out, _PART_NAMES)
        row_delta.remove_matching(out, row_delta.staging_pattern(_SET_NAMES))
    for config in configs:
        store_path(out, config).unlink(missing_ok=True)


def materialized_store(out_dir: Path, config: str) -> Iterator[bytes]:
    """One configuration's materialized store under `out_dir`, a run of bytes at a time: the file 11a's writer writes over the cut the set head records, `_joined_pieces`' bytes for a cut configuration and `RowWriter`'s one member for a whole one, over its base's records merged with its segments' by ordinal, each record's two ages from the column and the header from the head; or, for a tree with no head, the single file's bytes. A cut configuration's members are compressed before the header member, which indexes them, so it holds them, as 11a's join does."""
    out = Path(out_dir)
    head = read_set_head(out)
    if head is None:
        with open(store_path(out, config), "rb") as single:
            while chunk := single.read(1 << 20):
                yield chunk
        return
    entry = head.configs[config]
    with row_delta.open_parts(out, entry.view(config)) as opened, _opened_ages(out, head) as column:
        upserts = _segment_upserts(opened, config, entry, range(entry.rows)) if entry.segments else None
        records = _RecordLines(_part_runs(opened.handles[0]), framed=True, upserts=upserts)
        _header, layout = _column_layout(column, head)
        offset, _rows, ordinal = layout[config]

        def aged(count: int, first: int) -> bytes:
            lines = records.take(count)
            if len(lines) != count:
                raise ValueError(f"{config}'s base ends before its head's {entry.rows} rows")
            raw = os.pread(column.fileno(), 2 * count, offset + 2 * first)
            row_ages, position_ages = decoded_ages(raw, ordinal)
            return b"".join(
                b"%s\t%d\t%d\n" % (line.rsplit(b"\t", 2)[0], row_age, position_age)
                for line, row_age, position_age in zip(lines, row_ages, position_ages)
            )

        def member(first: int, count: int, header: bytes = b"", trailer: bytes = b"") -> bytes:
            buffer = io.BytesIO()
            with _open_member(buffer) as stream:
                stream.write(header)
                at = first
                while at < first + count:
                    step = min(_COMPARE_ROWS, first + count - at)
                    stream.write(aged(step, at))
                    at += step
                stream.write(trailer)
            return buffer.getvalue()

        trailer = f"{ROW_COUNT_TRAILER}\t{entry.rows}\n".encode()
        if entry.cut is None:
            yield member(0, entry.rows, entry.header_line, trailer)
        else:
            members: list[tuple[int, bytes]] = []
            first = 0
            for count in entry.cut:
                members.append((count, member(first, count)))
                first += count
            yield from (bytes(piece) for piece in _joined_pieces(entry.header_line, members))
        if records.take(1):
            raise ValueError(f"{config}'s base holds more than its head's {entry.rows} rows")
        records.finished()


_LOW64 = 0xFFFFFFFFFFFFFFFF
_ORDINAL_SPREAD = 0x9E3779B97F4A7C15
_MIX_FIRST = 0xBF58476D1CE4E5B9
_MIX_SECOND = 0x94D049BB133111EB
# One 128-bit lane holding 1, little-endian, which `_lane_masks` repeats once per lane.
_LANE_ONE = b"\x01" + bytes(15)


def _mix64(value: int) -> int:
    value = (value ^ (value >> 30)) * _MIX_FIRST & _LOW64
    value = (value ^ (value >> 27)) * _MIX_SECOND & _LOW64
    return value ^ (value >> 31)


_lane_masks_built: tuple[int, int, int] = (0, 0, 0)


def _lane_masks(count: int) -> tuple[int, int]:
    """Return the lowest bit and the low 64 bits of each of `count` 128-bit lanes, as two integers. They are shifted down from the widest pair built so far in this process, which costs less than building them again for every family."""
    global _lane_masks_built
    built, ones, low = _lane_masks_built
    if count > built:
        ones = int.from_bytes(_LANE_ONE * count, "little")
        low = ones * _LOW64
        _lane_masks_built = built, ones, low = count, ones, low
    drop = 128 * (built - count)
    return ones >> drop, low >> drop


def _lanes(values: Sequence[int]) -> int:
    """Pack 64-bit values into one integer, one value in the low half of each 128-bit lane and the first value in the lowest lane."""
    words = array("Q", bytes(16 * len(values)))
    words[0::2] = array("Q", values)
    if sys.byteorder == "big":
        words.byteswap()
    return int.from_bytes(words.tobytes(), "little")


def _mix64_lanes(packed: int, low: int) -> int:
    """Return `_mix64` of every value `_lanes` packed, packed the same way. `low` has the low 64 bits of every lane set. Each step is one operation over the whole integer: masking back to 64 bits after every shift drops what a shift moved in from the next lane, and the empty upper half of a lane holds the full product of a multiplication, so no lane carries into its neighbor."""
    value = ((packed ^ (packed >> 30)) & low) * _MIX_FIRST & low
    value = ((value ^ (value >> 27)) & low) * _MIX_SECOND & low
    return (value ^ (value >> 31)) & low


class VerificationSample:
    """The stratified sample of served rows that the caller checks again, drawn while the pass runs. Every family that served at least one row contributes up to `per_family` rows, chosen by the smallest mix of a per-family seed with the row's ordinal. The draw therefore depends only on the store's stamp, the family and the pass ordinal, not on the order or the number of rows offered. Seeding on the pass ordinal makes the checked rows change from pass to pass. Because every family that served rows is sampled, a family whose records are all wrong is always caught, not with probability equal to the sample size over the rows served. A rune edited while the oracle runs produces that error. Each kept entry holds the `Row` it was offered with, so `sampled_rows()` returns the parsed rows in index order and the verifier does not re-read the table. For the row sample the caller re-walks those rows and compares each served `CachedRow` with a fresh `_compare_row` result, and for the position sample it shapes them again. A heap entry is `(score, index, row)` and `Row` has no ordering. An index is offered once per family per pass, so no two entries tie on the first two fields and the comparison never reaches the row."""

    def __init__(
        self, stamp: str, pass_ordinal: int, per_family: int = VERIFICATION_SAMPLE_PER_FAMILY
    ) -> None:
        self.stamp = stamp
        self.pass_ordinal = pass_ordinal
        self.per_family = per_family
        self._seeds: dict[str, int] = {}
        self._kept: dict[str, list[tuple[int, int, Row]]] = {}

    def _seed(self, family: str) -> int:
        seed = self._seeds.get(family)
        if seed is None:
            digest = hashlib.sha256(f"{self.stamp}\t{family}\t{self.pass_ordinal}".encode()).digest()
            seed = int.from_bytes(digest[:8], "big")
            self._seeds[family] = seed
        return seed

    def offer(self, index: int, row: Row, families: Iterable[str]) -> None:
        if self.per_family <= 0:
            return
        for family in families:
            self._keep(family, -_mix64(self._seed(family) ^ (index * _ORDINAL_SPREAD & _LOW64)), index, row)

    def _keep(self, family: str, score: int, index: int, row: Row) -> None:
        kept = self._kept.setdefault(family, [])
        if len(kept) < self.per_family:
            heapq.heappush(kept, (score, index, row))
        elif score > kept[0][0]:
            heapq.heapreplace(kept, (score, index, row))

    def offer_many(self, offers: Sequence[tuple[int, Row, tuple[str, ...]]]) -> None:
        """Offer several `(index, row, families)` rows at once, and draw what `offer` draws from the same rows in any order. The oracle offers a chunk of rows at a time, because `offer` runs `_mix64` in the interpreter for every family of every row. Here the rows are grouped by their families tuple, so the oracle's rows, which carry `StaleMask.families_of`'s memoized tuples, are gathered per family a group at a time, and each family's rows are scored together: their ordinals are packed one to a lane (`_lanes`), the seed and the mix are applied to every lane at once (`_mix64_lanes`), and one subtraction marks the lanes whose mix is at or below the family's bound, which is the largest mix it keeps once it holds `per_family` rows. Only the marked rows reach the heap, through the comparison `offer` makes."""
        if self.per_family <= 0 or not offers:
            return
        groups: dict[tuple[str, ...], list[int]] = {}
        for position, (_index, _row, families) in enumerate(offers):
            group = groups.get(families)
            if group is None:
                groups[families] = [position]
            else:
                group.append(position)
        by_family: dict[str, list[int]] = {}
        for families, positions in groups.items():
            for family in families:
                gathered = by_family.get(family)
                if gathered is None:
                    by_family[family] = list(positions)
                else:
                    gathered.extend(positions)
        indexes = [index for index, _row, _families in offers]
        for family, positions in by_family.items():
            picked = itemgetter(*positions)(indexes) if len(positions) > 1 else (indexes[positions[0]],)
            self._offer_family(family, picked, positions, offers)

    def _offer_family(
        self,
        family: str,
        indexes: Sequence[int],
        positions: Sequence[int],
        offers: Sequence[tuple[int, Row, tuple[str, ...]]],
    ) -> None:
        count = len(indexes)
        ones, low = _lane_masks(count)
        mixed = _mix64_lanes((_lanes(indexes) * _ORDINAL_SPREAD & low) ^ (self._seed(family) * ones), low)
        kept = self._kept.get(family)
        if kept is not None and len(kept) == self.per_family:
            bound = -kept[0][0]
        elif count > self.per_family:
            words = array("Q")
            words.frombytes(mixed.to_bytes(16 * count, "little"))
            if sys.byteorder == "big":
                words.byteswap()
            bound = heapq.nsmallest(self.per_family, words[0::2])[-1]
        else:
            bound = _LOW64
        flags = (((1 << 64) | bound) * ones - mixed) >> 64 & ones
        if not flags:
            return
        marked = flags.to_bytes(16 * count, "little")
        values = mixed.to_bytes(16 * count, "little")
        lane = marked.find(1)
        while lane >= 0:
            index, row, _families = offers[positions[lane >> 4]]
            self._keep(family, -int.from_bytes(values[lane : lane + 8], "little"), index, row)
            lane = marked.find(1, lane + 16)

    def by_family(self) -> dict[str, tuple[int, ...]]:
        return {
            family: tuple(sorted(index for _score, index, _row in kept))
            for family, kept in self._kept.items()
        }

    def indexes(self) -> tuple[int, ...]:
        return tuple(sorted({index for kept in self._kept.values() for _score, index, _row in kept}))

    def sampled_rows(self) -> tuple[tuple[int, Row], ...]:
        rows = {index: row for kept in self._kept.values() for _score, index, row in kept}
        return tuple(sorted(rows.items()))
