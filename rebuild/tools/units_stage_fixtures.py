"""Record what the real Python corpus units worker's and corpus build's stages take and return, for the ports of the units recompute and of the build's parent to Rust (`rebuild/CORPUS-UNITS-PORT-PLAN.md` and `rebuild/CORPUS-PARENT-PORT-PLAN.md`, each § Stage fixtures).

A stage runs the worker or the build (`Stage.run`). The worker's stages (`STAGES`) are described below. The build's (`BUILD_STAGES`) take a build source, `--scenario S` beside `--source`: the scenario's one build, prepared over the source's base prior (`open_build_source`, which `prepare --source SOURCE --scenario NAME|all [--check]` runs alone). A build stage is a call stage, whose recorders record a hooked call (`BUILD_HOOKS`) as the worker's stages do, or a boundary stage (`Stage.kind`), whose recorder returns the build's state at a phase boundary as a `Boundary`: a line a unit, every unit on the mini and every `--every`th ordinal on live (`LIVE_EVERY` by default), and a line a column with the digest of its canonical bytes over every unit (`column_line`). `dump --scenario` writes `source.json`, the prior corpus as the build finds it (`prior/`), clones of what the build reads (`inputs/`), the units init with its spec dump (`init/`) and the Rust build's input for that run's values without descriptors (`build.json`, `build._rust_inputs` through `corpus_exec.write_input`), then runs `build_m1` in this process over a clone of the prior, at the harness's default width under the Rust units engine and one write job, with the selected stages recording (`build_dump`). `selfcheck --scenario` runs that build twice, plain and with every build stage recording, and fails unless the two output trees are identical (`build_selfcheck`). `compare` takes a build stage too (`build_compare`): it opens the dump's audit clone (`audit.AuditParts`), writes `build.json` again with those descriptors, runs the Rust stage with them inherited, after the compares of the upstream build stages a boundary stage follows, and reports as for a worker stage.

The build's stages (`BUILD_STAGES`):

- `build-input` is a boundary stage that records no hooked call: `build_dump` records the values it writes `build.json` from, a line a field under `input:<field>`, and the size of each audit part the build holds under `audit.held`, which the Rust side reads through the descriptors it inherits.
- `sink` records each `output_sink.write_output`, `replace_output` and `note_rewritten` call the build makes (`_sink_call`), keyed by its origin, the call and a digest of its inputs: the target's name, what it `held` before the call, the `payload` as its kind and the sha256 of the bytes the call writes (a writer's read from the target once it has run, or the bytes the call kept), the `sha256` a writer is kept against, `keep_if_equal` and the state of the counts file the call found (`counts_file`); and as its outputs what the call `returned`, the `counts` lines it appended and what it left at the target, `after`. Every file's bytes are kept once under their sha256 in `sink.blobs/` beside the stage file (`SINK_BLOBS`), from which the Rust side sets each target up again in a scratch directory and makes the call itself. Its generated lines (`_sink_cases`) put every call and payload kind to missing, equal, differing, FIFO, directory and linked targets, with and without the compare, with each state of the counts file, and `output_sink.replacing`, which the build reaches only through `row_delta.StreamedPart`.

`dump` runs `build._corpus_worker` in this process over a source and writes, under `--out`, the units file (`units.ndjson`: one `batch` request frame per handout, each frame as `units_exec.encode_batch` writes it), the source's description (`source.json`, which names the source and its handouts), and one `STAGE.ndjson` per selected stage. A stage is a `Stage` in `STAGES`: recorders on the worker's hook points (`HOOKS`), each of which sees a call's arguments before the call and its result after it and returns the fields of the line to record, and an optional generator of inputs the source does not reach. Every recorded value is written in the typed encoding (`units_exec.encode`, `decode`). The wrappers record and return; they never change a value. A line's `key` names it, and the stage's `outputs` are the fields that hold what the Rust side must reproduce.

`selfcheck` runs the worker over a source twice, once as the build runs it and once with every hook point wrapped and every registered stage recording into a temporary directory, and fails unless both runs give the same projection digest and every projection either run returns comes back from the typed encoding's round trip equal to itself, compared type-strictly (`same`).

`compare STAGE --stages DIR` builds the crate (`units_exec.ensure_built`), writes an init for the source the dump's `source.json` names (`source_init`, `units_exec.write_init`), runs `ams-review stage STAGE --init=<init> DIR/STAGE.ndjson`, and reports every key whose outputs the Rust answer does not reproduce, type-strictly, every key either side lacks, and every problem a stage's answer checks (`Stage.answer_checks`) find in the Rust answer's further fields. `--fields` narrows the outputs it checks to a comma-separated list of the stage's output fields and field groups.

The stages (`STAGES`):

- `wire` records each unit `_phase1_unit` receives, as the `audit.Unit` fields that cross (`unit_value`), under `unit:<ordinal>`; the Rust side reads them back from the units file. Its generated lines are the init's values with the spec's runes, the dictionary's bytes and the font facts, under `init`, and a verify frame with varied injections, under `verify`, which the line carries for the Rust side to read.
- `pyfmt` records what the worker hashes and writes, each line an `input` value, the `format` it is written in (`PYFMT_FORMS`) and the text Python wrote: the ink diffs' repr (`_ink_readings`), the repr of the cluster key's head, `(configs, class)` (`_cluster_id_from_repr`), the carry projection's sorted-key JSON (`unit_cache.carry_projection`) and each fragment's shard bytes (`_fragment_bytes`), keyed by the form and a digest of the value. Its generated lines put an edge set to each form (`_pyfmt_edges`).
- `yaml` records each record `Drafter.draft_policy` dumps, as the `input` its `yaml.safe_dump` call receives and the stripped text it ships, keyed by a digest of the record; a `safe_dump` call with other arguments than `draft_policy`'s (`YAML_DUMP_ARGUMENTS`) is refused. Its generated lines put an edge set to the same dump (`_yaml_edges`).
- `shape` records each call that reaches HarfBuzz, `Shaper.shape` under the worker's memo (`ink._MemoizedShaper`), as the `font` it shapes with (the resolved path `ink.shaper_for` registers it under), the `text` and the `features` it receives and the `ShapeResult` it returns, keyed by a digest of the call.
- `ink` records each unit `_phase1_unit` reads, under `ink:<ordinal>`, as its `text` and `configs` and, as its value, the repr of the diffs its `_ink_readings` call returns, the three machine flags, `ink_deltas` and the diffs digest from the projection. Its generated lines put every distinct text of a unit that holds `ss10` to the Junior oracle directly (`_ink_generated`), under `junior:` and a digest of the text, with the oracle's tracking-free Junior pieces and its answer for `ss10` alone.
- `settle` records each unit `Enricher.explain_units` settles, under `settle:` with its first configuration and its code points, as the `codepoints` and the `config` and, as its value, the `ExplainReport`'s code points, features and positions, each position's trace whole; and each text `Enricher.formed_spans` forms, under `spans:` and its code points, as the `codepoints` and the spans it returns.
- `explain` records each explain text `Enricher.enrich` writes, the `_filter_explain` call over the `ExplainReport.render` call just before it, under `explain:` with the report's configuration token (`model.feature_config_token`), its code points and the divergent positions, as the report's `codepoints`, that `config` and the `diff_positions` the filter receives, and as its outputs the `rendered` text and the `filtered` text the filter returns.
- `subset` records each lookup `Enricher.enrich` makes in the subset pack, `SubsetPack.row`, under `subset:` with its configuration and its code points, as the `config` and the `codepoints` it receives and the `SubsetRow` or None it returns. Its generated lines (`_subset_generated`), under `generated:` with the same two, look up each configuration's first and last window, the first window spelled each way `int(part, 16)` reads it (`_SUBSET_SPELLINGS`), its misspellings `pack_key` refuses (`_SUBSET_MISSPELLINGS`), strings that refuse or miss whatever the source (`_SUBSET_MISSES`), and a configuration the pack does not hold.
- `enrich` records each unit `Enricher.enrich` enriches, under `enrich:<ordinal>`, as the unit's `record` as a request frame carries it (`units_exec.unit_record`, written as one compact JSON line) and the phase-1 `flags` the worker set on it (`ink_identical`, `picture_identical`, `junior_equivalent`), and as its outputs every `EnrichedUnit` field but the unit itself, which is the input, the `mismatches` lines the call added to the enricher's list, and the unit's `primary_unit_projection`. Its generated lines (`_enrich_generated`) make both mismatch lines fire: a unit whose `new` has one cell token replaced, under `generated:new:<ordinal>`, and a unit enriched against a subset row with a renamed glyph, which the line carries as its `row`, under `generated:row:<ordinal>`. Its field groups (`compare --fields`) name `geometry`, the fields `Enricher.enrich` computes before its summary (`ENRICH_GEOMETRY`).
- `scaffold` records each unit `_phase1_unit` drafts, under `scaffold:<ordinal>`, as the unit's `record` as a request frame carries it, taken before phase 1 sets its flags and re-routes it, and the `ink_deltas` and the `final_class` (the change group, or None) the call drafts its fragment with, read from its projection, and as its value the `scaffold` that call's `unit_scaffold` returned, copied before `unit_to_json` gives it the final class, with the projection's `change_group` and `cluster`. Its generated lines (`_scaffold_generated`) put every non-empty subset of `ACCEPTANCE_CONFIGS` to `build.config_badge`, under `badge:` and the subset, as the `configs` and, as the value, the `gate` and the `note`.
- `expect` records each call to `drafts.expect_string`, under `string:` and a digest of the call, as the `codepoints`, `spans` and `junctions` it receives and, as its value, the expect string it returns; and each pin the drafter replays against the after font, the drafted pin through `Drafter.validate_semantics` and each any-of candidate `Drafter.draft_any_of` returns, replayed as the pin is, under `pin:` and a digest of the replay's inputs, as the `text`, the `expect` and the `features` and, as its value, the pin's parse or its error, its interpretations, the after font's row and the replay's verdict (`_expect_line`). Its generated lines (`_expect_generated`) add `expect_string` calls over every boundary and connector, and the replay of every pinned cell of the site pages and of an edge set of pins (`_expect_edges`).
- `schema` records each record the drafter's checkers check, `_SchemaValidator.check` on a `for_definition` checker, under `drafted:` and a digest of the call, as the `definition` the checker checks against and the `record` and, as its value, the verdict: whether `check` found no problem, which is all the drafter's output reads. Its generated lines (`_schema_generated`), under `generated:` and the same digest, check one drafted record of each shape and every policy record of the source's rune files against each of the drafter's definitions, and mutations of each against its own (`_schema_mutations`).
- `draft` records each unit `_phase1_unit` drafts a full fragment for, under `draft:<ordinal>`, as the unit's `record` as a request frame carries it, taken before phase 1 sets its flags and re-routes it, and the phase-1 `flags` the worker set on it, and as its value the fragment's `drafts`: the pin, the policy record or None, and the any-of record, each as its `to_json` writes it. A slim fragment, which the drafter skips, records nothing.
- `fragment` records each unit `_phase1_unit` drafts, under `fragment:<ordinal>`, as the unit's `record` as a request frame carries it, taken before phase 1 sets its flags and re-routes it, the phase-1 `flags` the worker set on it, and the `ink_deltas` and the `final_class` it drafts its fragment with, read from its projection, and as its value the fragment's shard bytes as the `_fragment_bytes` call its packer makes returned them (`fragment`, ASCII text), its `content_key` and its `id`. Its answer check (`_fragment_packed`) inflates the deflated fragment the Rust answer carries as `packed` through `zlib.decompressobj` with the source's preset dictionary, as `build._FragmentArena.read` inflates one, and requires the stream to end exactly where the bytes end and to give the Python fragment's bytes.
- `check` records each fragment `_phase1_unit` checks, `check_unit` at `DRAFTED`, under `drafted:` and a digest of the fragment, as the `fragment` and, as its value, the error list. Its generated lines (`_check_generated`), under `generated:` and the same digest, mutate the smallest drafted fragment of each shape (`_check_shape`), generically (`_check_mutations`) and once or more for each predicate (`_check_extras`), and record the error list, or the class of the TypeError, AttributeError or ValueError the check raises, as `{"raised": name}`.

`unicode-tables` writes `rebuild/review-rs/src/pyfmt/unicode_tables.rs`, the code point ranges this CPython's `str.isprintable`, `str.isspace`, `str.isdigit` and `str.isdecimal` accept, and with `--check` writes nothing and fails when the file differs from what it would write.

The sources (`open_source`):

- `mini` is the frozen mini bundle under `rebuild/review/fixtures/mini/`, its spec materialized from `pin.json` and its subset tables packed under `var/rust-port/mini-source/` (`_mini_source`), as the `mini_bundle` fixture in `rebuild/conftest.py` does it under its temporary root, followed by a copy of each of its units under `ss10` alone, with an ordinal after the bundle's (`_ss10_copy`). The bundle's audit holds no `ss10` row, and the copies are what reach the isolated overlay, the `ss10` gate, the `ss10` subset rows and the Junior check on the mini. Every handout is taken.
- `live` is the last cycle's corpus under `rebuild/out/m1/` and `rebuild/out/review/`, read only and loaded as the build loads it (`build._load_corpus`). A live run takes `--per-config K` handouts from the start of each first configuration's stretch of the pool's queue (`build._configuration_order`), `--batches N` evenly spaced handouts, or `--all`. It refuses a stale subset pack or ink-signature store rather than rebuild either, since `rebuild/out/` is read only here.

Handouts are `build.PHASE1_HANDOUT_UNITS` wide, cut from the queue in configuration order, and each is passed to the worker through multiprocessing's pickler, as a pool worker receives it.

Run from the repository root:

    uv run python -m rebuild.tools.units_stage_fixtures dump --source mini --out var/rust-port/stages/mini
    uv run python -m rebuild.tools.units_stage_fixtures dump --source live --per-config 1 --out var/rust-port/stages/live
    uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source mini
    uv run python -m rebuild.tools.units_stage_fixtures compare wire --stages var/rust-port/stages/mini
    uv run python -m rebuild.tools.units_stage_fixtures unicode-tables --check
    uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source mini --scenario incremental
    uv run python -m rebuild.tools.units_stage_fixtures dump --source mini --scenario warm --out var/rust-port/build-stages/mini-warm
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import filecmp
import functools
import gzip
import hashlib
import importlib
import inspect
import json
import os
import pickle
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import warnings
import zlib
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import batched, chain, combinations, product
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import IO, Any, TypedDict

import yaml

from rebuild.pipeline import fingerprint, oracle, output_sink, row_delta, spec_load
from rebuild.pipeline.explain import ExplainReport
from rebuild.pipeline.kernel_exec import KERNEL_ALLOCATOR_ENVIRONMENT
from rebuild.pipeline.model import feature_config_token
from rebuild.review import (
    audit,
    build,
    corpus_exec,
    drafts,
    ink,
    landing,
    unit_cache,
    unit_index,
    units_exec,
)
from rebuild.review.audit import ACCEPTANCE_CONFIGS, BATCH_SIZE, Unit, UnitTable, load_ledger
from rebuild.review.drafts import Drafter, corpus_index_from_pages
from rebuild.review.enrich import LETTERS, EnrichedUnit, Enricher, load_spec, primary_unit_projection
from rebuild.review.fixtures.mini import pin
from rebuild.review.subset_pack import (
    PACK_NAME,
    SubsetPack,
    _header_disagreement,
    _read_header,
    key_codepoints,
    table_digests,
    write_pack_in_memory,
)
from rebuild.review.unit_store import UnitStore
from rebuild.review.units_exec import Record, compact, decode, encode
from rebuild.tools import cycle_paths, cycle_timings, memory_tally
from rebuild.validation.shaping import row_for

REPO_ROOT = build.REPO_ROOT
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
SOURCES = ("mini", "live")
UNITS_FILE = "units.ndjson"
SOURCE_FILE = "source.json"
SOURCE_FORMAT = "ams-review-stage-source/1"
SS10 = "ss10"
MINI_ROOT = REPO_ROOT / "var" / "rust-port" / "mini-source"

# The worker's per-unit calls a stage can record, by name: each is the module and the attribute path the wrapper replaces for the length of a run. Every caller reaches these through the module global or the class attribute, so a replacement installed before the worker starts sees every call.
HOOKS: dict[str, tuple[str, str]] = {
    "_phase1_unit": ("rebuild.review.build", "_phase1_unit"),
    "_ink_readings": ("rebuild.review.build", "_ink_readings"),
    "Enricher.explain_units": ("rebuild.review.enrich", "Enricher.explain_units"),
    "Enricher.formed_spans": ("rebuild.review.enrich", "Enricher.formed_spans"),
    "Enricher.enrich": ("rebuild.review.enrich", "Enricher.enrich"),
    "ExplainReport.render": ("rebuild.pipeline.explain", "ExplainReport.render"),
    "_filter_explain": ("rebuild.review.enrich", "_filter_explain"),
    "SubsetPack.row": ("rebuild.review.subset_pack", "SubsetPack.row"),
    "Drafter.draft_pin": ("rebuild.review.drafts", "Drafter.draft_pin"),
    "Drafter.draft_policy": ("rebuild.review.drafts", "Drafter.draft_policy"),
    "Drafter.draft_any_of": ("rebuild.review.drafts", "Drafter.draft_any_of"),
    "Drafter.validate_semantics": ("rebuild.review.drafts", "Drafter.validate_semantics"),
    "expect_string": ("rebuild.review.drafts", "expect_string"),
    "unit_to_json": ("rebuild.review.build", "unit_to_json"),
    "_fragment_bytes": ("rebuild.review.build", "_fragment_bytes"),
    "check_unit": ("rebuild.review.build", "check_unit"),
    "Shaper.shape": ("rebuild.validation.shaping", "Shaper.shape"),
    "_cluster_id_from_repr": ("rebuild.review.build", "_cluster_id_from_repr"),
    "carry_projection": ("rebuild.review.unit_cache", "carry_projection"),
    "yaml.safe_dump": ("yaml", "safe_dump"),
    "unit_scaffold": ("rebuild.review.build", "unit_scaffold"),
    "_SchemaValidator.check": ("rebuild.pipeline.spec_load", "_SchemaValidator.check"),
}


# --- the typed encoding -------------------------------------------------------------------------


def same(left: object, right: object) -> bool:
    """Whether two values are equal under the typed encoding: of one kind (a tuple is not a list, a bool is not an int, a record matches a record of its class name and fields), a dict's entries in one order, and a set's members as a set."""
    return compact(encode(left)) == compact(encode(right))


# --- the sources --------------------------------------------------------------------------------


@dataclass
class Source:
    """One source's worker `init` (as `_RecomputeRunner.__init__` builds it), its unit table and an empty unit store over it, as the plan leaves a recomputed unit, and the handouts the worker runs, each a run of ordinals of the pool's queue. An ordinal at or past the table's count names a mini unit's `ss10` copy (`_ss10_copy`). `corpus_units` is the loaded corpus's unit total, and `chosen` the handouts' indices in the queue's handouts, which is every index for the mini."""

    name: str
    init: dict
    table: UnitTable
    store: UnitStore
    handouts: list[array]
    corpus_units: int
    chosen: list[int]
    selection: dict

    def units(self, handout: Sequence[int]) -> tuple[Unit, ...]:
        """Materialize one handout's units as `_drive_phase1` does, with the mini's `ss10` copies made from the units they copy."""
        n = self.table.n
        return tuple(
            (
                self.table.unit(ordinal, self.store)
                if ordinal < n
                else _ss10_copy(self.table.unit(ordinal - n, self.store), ordinal)
            )
            for ordinal in handout
        )

    def unit_count(self) -> int:
        return sum(len(handout) for handout in self.handouts)

    def describe(self) -> dict:
        return {
            "format": SOURCE_FORMAT,
            "source": self.name,
            "selection": self.selection,
            "corpus_units": self.corpus_units,
            "handout_units": build.PHASE1_HANDOUT_UNITS,
            "handouts": self.chosen,
            "units": self.unit_count(),
        }


def _ss10_copy(unit: Unit, ordinal: int) -> Unit:
    """Return a copy of a mini unit under `ss10` alone: its configs, its render groups and its per-config classes narrowed to that one configuration, which takes the unit's class, at `ordinal`."""
    return dataclasses.replace(
        unit,
        configs=(SS10,),
        render_groups=((SS10,),),
        config_classes={SS10: unit.class_id},
        ordinal=ordinal,
    )


def _load_table(
    audit_path: Path,
    ledger_path: Path,
    spec_root: Path,
    after_font: Path,
    out_dir: Path,
    recompute_all: bool,
) -> tuple[UnitTable, UnitStore, frozenset[str]]:
    """Load a corpus's units as `_build_m1` loads them (`build._load_corpus`, at one signature job) and return the table, an empty unit store over it, and the ledger's `ink_identical` class ids. Unless `recompute_all`, the ink-signature store under `out_dir` must match this checkout: a stale one would have the load shape every window again, and its refusal names why."""
    ledger = load_ledger(ledger_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(spec_root)
    family_keys, helpers_digest = unit_cache.family_content_keys(spec_root, spec, after_font)
    keyer = unit_cache.UnitKeyer(family_keys, dict(LETTERS))
    identical_classes = frozenset(entry.id for entry in ledger if entry.ink_identical)
    if not recompute_all:
        environment = unit_cache.signature_environment(REPO_ROOT, build.SITE_BEFORE_FONT, helpers_digest)
        stale = unit_cache.signature_miss_note(out_dir, environment)
        if stale is not None:
            raise SystemExit(f"the ink-signature store under {out_dir} does not serve this checkout: {stale}")
    parts = audit.AuditParts(audit_path)
    try:
        loaded = build._load_corpus(
            parts,
            audit_path,
            ledger_path,
            ledger,
            dict(LETTERS),
            family_keys,
            keyer,
            out_dir,
            build.SITE_BEFORE_FONT,
            after_font,
            REPO_ROOT,
            helpers_digest,
            1,
            recompute_all,
            identical_classes,
        )
    finally:
        parts.close()
    table = loaded.workload.table
    return table, UnitStore(table.n, strings=table.strings, input_keys=loaded.input_keys), identical_classes


def _worker_init(
    after_font: Path,
    subset_dir: Path,
    subset_pack: Path,
    spec_root: Path,
    identical_classes: frozenset[str],
    *,
    before_font: Path = build.SITE_BEFORE_FONT,
    junior_font: Path = build.SITE_JUNIOR_FONT,
) -> dict:
    """Return the `init` dict `_RecomputeRunner.__init__` hands every pool worker, over the site's before and Junior fonts unless `before_font` and `junior_font` name copies of them."""
    site_pages = fingerprint.read_site_corpus(REPO_ROOT)
    return {
        "before_font": before_font,
        "after_font": after_font,
        "junior_font": junior_font,
        "subset_dir": subset_dir,
        "subset_pack": subset_pack,
        "repo_root": REPO_ROOT,
        "spec_root": spec_root,
        "identical_classes": identical_classes,
        "fold": build._config_fold(build.SITE_BEFORE_FONT, after_font),
        "corpus_index": corpus_index_from_pages(site_pages),
        "dictionary": build.FRAGMENT_DICTIONARY.read_bytes(),
    }


def _queue_handouts(queue: Sequence[int]) -> list[array]:
    return [array("I", handout) for handout in batched(queue, build.PHASE1_HANDOUT_UNITS)]


def _mini_source() -> Source:
    """Materialize the mini bundle's spec and pack its subset tables under `MINI_ROOT`, replacing what an earlier run left there, and load the bundle. The provenance pointers a fragment carries name rune files by their path under the spec root, so the root is the same path on every run, and a stage dump's fragments equal those of a later run over the same bundle."""
    shutil.rmtree(MINI_ROOT, ignore_errors=True)
    spec_root = pin.materialize(MINI_ROOT / "spec")
    subset_pack = spec_root / PACK_NAME
    write_pack_in_memory(MINI, ACCEPTANCE_CONFIGS, table_digests(MINI, ACCEPTANCE_CONFIGS), subset_pack)
    store_dir = MINI_ROOT / "out"
    store_dir.mkdir()
    after_font = MINI / "M1.otf"
    table, store, identical_classes = _load_table(
        MINI / "audit.tsv",
        spec_root / pin.LEDGER_PATH,
        spec_root,
        after_font,
        store_dir,
        recompute_all=True,
    )
    n = table.n
    ss10_rank = audit._config_index(SS10)
    ranks = [table.config_rank(ordinal) for ordinal in range(n)] + [ss10_rank] * n
    queue = sorted(range(2 * n), key=ranks.__getitem__)
    handouts = _queue_handouts(queue)
    return Source(
        name="mini",
        init=_worker_init(after_font, MINI, subset_pack, spec_root, identical_classes),
        table=table,
        store=store,
        handouts=handouts,
        corpus_units=n,
        chosen=list(range(len(handouts))),
        selection={},
    )


def _live_source(per_config: int, batches: int, everything: bool) -> Source:
    subset_dir = build.M1_SUBSETS
    subset_pack = subset_dir / PACK_NAME
    header, _ = _read_header(subset_pack)
    if header.get("configs") != list(ACCEPTANCE_CONFIGS):
        raise SystemExit(f"{subset_pack} packs {header.get('configs')}, not {list(ACCEPTANCE_CONFIGS)}")
    disagreement = _header_disagreement(header, table_digests(subset_dir, ACCEPTANCE_CONFIGS))
    if disagreement is not None:
        raise SystemExit(f"{subset_pack} is stale ({disagreement}); a cycle pass rewrites it")
    table, store, identical_classes = _load_table(
        build.M1_AUDIT,
        build.M1_LEDGER,
        REPO_ROOT,
        build.M1_AFTER_FONT,
        build.DEFAULT_OUT,
        recompute_all=False,
    )
    queue = build._configuration_order(array("I", range(table.n)), table)
    every = _queue_handouts(queue)
    width = build.PHASE1_HANDOUT_UNITS
    if everything:
        chosen = list(range(len(every)))
    else:
        picked: set[int] = set()
        stretch_start: dict[int, int] = {}
        stretch_end: dict[int, int] = {}
        for position, ordinal in enumerate(queue):
            rank = table.config_rank(ordinal)
            stretch_start.setdefault(rank, position)
            stretch_end[rank] = position
        for rank, start in stretch_start.items():
            first, last = start // width, stretch_end[rank] // width
            picked.update(range(first, min(first + per_config, last + 1)))
        if batches:
            step = len(every) / batches
            picked.update(int(index * step + step / 2) for index in range(batches))
        chosen = sorted(picked)
    return Source(
        name="live",
        init=_worker_init(build.M1_AFTER_FONT, subset_dir, subset_pack, REPO_ROOT, identical_classes),
        table=table,
        store=store,
        handouts=[every[index] for index in chosen],
        corpus_units=table.n,
        chosen=chosen,
        selection={"per_config": per_config, "batches": batches, "all": everything},
    )


def open_source(name: str, per_config: int = 0, batches: int = 0, everything: bool = False) -> Source:
    """Load the named source (the module docstring). The mini is always whole, so it refuses a selection; a live source needs one."""
    selected = bool(per_config or batches or everything)
    if name == "mini":
        if selected:
            raise SystemExit(
                "the mini source is always taken whole; --per-config, --batches and --all are live-only"
            )
        return _mini_source()
    if name == "live":
        if not selected:
            raise SystemExit("a live source needs --per-config K, --batches N or --all")
        return _live_source(per_config, batches, everything)
    raise SystemExit(f"unknown source {name!r}; the sources are {', '.join(SOURCES)}")


# --- the units file -------------------------------------------------------------------------------


def write_units_file(source: Source, out: Path) -> None:
    """Write the source's handouts as `batch` request frames, one compact JSON line each, to `out/units.ndjson`, and its description to `out/source.json`."""
    with (out / UNITS_FILE).open("w", encoding="ascii") as handle:
        for index, handout in enumerate(source.handouts):
            frame = units_exec.encode_batch(index, source.units(handout))
            handle.write(compact(frame) + "\n")
    (out / SOURCE_FILE).write_text(json.dumps(source.describe(), indent=1) + "\n", encoding="ascii")


# --- stages and the wrapped run -----------------------------------------------------------------


def column_line(name: str, values: Iterable[object]) -> dict[str, object]:
    """A boundary stage's line for one column, under `column:<name>`: how many values it holds and the sha256 of its canonical bytes, each value's typed encoding as one compact JSON line (`compact`, `encode`) and a newline, in the column's order. The Rust side digests its column the same way."""
    digest = hashlib.sha256()
    count = 0
    for value in values:
        digest.update(compact(encode(value)).encode("ascii") + b"\n")
        count += 1
    return {"key": f"column:{name}", "values": count, "sha256": digest.hexdigest()}


@dataclass(frozen=True)
class Boundary:
    """The build's state at one phase boundary, as a boundary stage's finisher returns it: `columns` over `count` units, each read by ordinal, and further lines, each with its own key, such as a column that does not hold one value a unit (`column_line`) or a phase note's facts."""

    count: int
    columns: Mapping[str, Callable[[int], object]]
    extra: Sequence[Mapping[str, object]] = ()

    def lines(self, every: int) -> Iterator[Mapping[str, object]]:
        """A line for every `every`th unit from ordinal 0, under `unit:<ordinal>` with each column's value there; a line a column with its digest over every unit (`column_line`); then the further lines."""
        for ordinal in range(0, self.count, every):
            yield {"key": f"unit:{ordinal}", **{name: read(ordinal) for name, read in self.columns.items()}}
        for name, read in self.columns.items():
            yield column_line(name, map(read, range(self.count)))
        yield from self.extra


# A recorder sees one call's positional and keyword arguments before the call runs and returns None, to record nothing, or a finisher, which receives the call's result and returns what to record: for a call stage the fields of the line, a list of such fields for a call that records one line per item, or None; for a boundary stage a `Boundary`, or None.
Finisher = Callable[[object], Mapping[str, object] | list[Mapping[str, object]] | Boundary | None]
Recorder = Callable[[tuple, dict], Finisher | None]


# An answer check sees the source's worker `init`, a line's expected outputs and the value the Rust answer to the line carries under the check's field, decoded, and returns a problem or None.
AnswerCheck = Callable[[Mapping[str, object], Mapping[str, object], object], str | None]

# What a stage runs: the units worker over a worker source (`Source`), or the corpus build over a build source (`BuildSource`).
RUNS = ("worker", "build")
# How a stage records: a call stage a line a hooked call (or an item of its result), a boundary stage the build's state at a phase boundary (`Boundary`).
KINDS = ("call", "boundary")


@dataclass(frozen=True)
class Stage[S]:
    """One stage of the port: what it runs (`RUNS`) and how it records (`KINDS`), its recorders by hook name (`HOOKS` for the worker, `BUILD_HOOKS` for the build), its generated inputs, which give the fields of each further line for a source of its run, the fields of a line that hold its expected output, which `compare` checks against the Rust stage's answer to the line's `key`, named groups of those fields that `compare --fields` can check alone, and checks of further fields of a Rust answer that are not compared for equality, by field name."""

    name: str
    recorders: Mapping[str, Recorder]
    generated: Callable[[S], Iterator[Mapping[str, object]]] | None = None
    outputs: tuple[str, ...] = ("value",)
    field_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    answer_checks: Mapping[str, AnswerCheck] = field(default_factory=dict)
    run: str = "worker"
    kind: str = "call"

    def __post_init__(self) -> None:
        if self.run not in RUNS or self.kind not in KINDS:
            raise ValueError(f"the {self.name} stage runs {self.run!r} and records {self.kind!r}")
        if self.kind == "boundary" and self.run != "build":
            raise ValueError(f"the {self.name} stage records a phase boundary, which only the build has")


def unit_value(unit: Unit) -> Record:
    """The `audit.Unit` fields that cross the boundary (`units_exec.UNIT_FIELDS`), as the worker holds them, in the unit's declaration order: what the Rust side's unit record must read back as."""
    return Record(
        "Unit",
        tuple(
            (name, dict(unit.config_classes) if name == "config_classes" else getattr(unit, name))
            for name, _wire in units_exec.UNIT_FIELDS
        ),
    )


def _wire_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key, value = f"unit:{unit.ordinal}", unit_value(unit)
    return lambda _result: {"key": key, "value": value}


# How many units of the first handout the `wire` stage's verify frame carries.
WIRE_VERIFY_UNITS = 6


def _wire_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `wire` stage's generated lines: the init's values as `units_exec.init_values` gives them, with each rune of the spec (its codepoint, sequence and stance names), the preset dictionary's bytes and the font facts (`units_exec.font_facts`), under `init`; and one verify frame over the first handout's first units, each with an injection that varies every field's kind, under `verify`."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(Path(source.init["spec_root"]))
    runes = {name: (rune.codepoint, rune.sequence, tuple(rune.stances)) for name, rune in spec.runes.items()}
    fonts = {side: source.init[key] for side, key in units_exec.FONT_SIDES}
    yield {
        "key": "init",
        "value": units_exec.init_values(source.init)
        | {
            "spec": runes,
            "dictionary": source.init["dictionary"],
            "font_facts": units_exec.font_facts(fonts),
        },
    }
    items: list[tuple[Unit, tuple]] = []
    for index, unit in enumerate(source.units(source.handouts[0][:WIRE_VERIFY_UNITS])):
        primary_units = tuple(
            (None if slot % 2 else f"{unit.input_key[:8]}-{slot}", slot == 1) for slot in range(index)
        )
        items.append(
            (
                unit,
                (
                    None if index % 2 else f"duplicate-{index}",
                    unit.cluster if index % 2 else f"cluster-{index}",
                    unit.class_id,
                    primary_units,
                ),
            )
        )
    yield {
        "key": "verify",
        "frame": compact(units_exec.encode_verify(items)),
        "value": [(unit_value(unit), injection) for unit, injection in items],
    }


# The forms the `pyfmt` stage checks, by the name a line's `format` gives: CPython's `repr`, the carry projection's sorted-key JSON (`unit_cache.carry_projection`), and a fragment's shard bytes (`build._fragment_bytes`).
PYFMT_FORMS: dict[str, Callable[[Any], str]] = {
    "repr": repr,
    "json-sorted": lambda value: json.dumps(value, sort_keys=True),
    "json-fragment": lambda value: build._fragment_bytes(value).decode("ascii"),
}


def _pyfmt_line(form: str, value: object, text: object) -> dict[str, object]:
    """One `pyfmt` line from the run, keyed by its form and a digest of the value's typed encoding, so a value the worker writes again gives the same line under the same key (`_Sink.write`)."""
    digest = hashlib.sha1(compact(encode(value)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{form}:{digest}", "format": form, "input": value, "value": text}


def _pyfmt_ink_readings(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        return _pyfmt_line("repr", result[0], repr(result[0]))

    return finish


def _pyfmt_cluster_head(args: tuple, _kwargs: dict) -> Finisher:
    head = (tuple(args[0]), args[1])
    return lambda _result: _pyfmt_line("repr", head, repr(head))


def _pyfmt_carry_projection(args: tuple, _kwargs: dict) -> Finisher:
    projection = {
        key: value for key, value in args[0].items() if key not in unit_cache.CARRY_PRESENTATION_KEYS
    }
    return lambda result: _pyfmt_line("json-sorted", projection, result)


def _pyfmt_fragment_bytes(args: tuple, _kwargs: dict) -> Finisher:
    fragment = args[0]

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, bytes)
        return _pyfmt_line("json-fragment", fragment, result.decode("ascii"))

    return finish


def _printable_boundaries() -> str:
    """Every code point beside an edge of a `str.isprintable` run, outside the surrogates: the characters where a wrong printable table would first show in a repr."""
    points: set[int] = set()
    previous = False
    for point in range(sys.maxunicode + 1):
        printable = chr(point).isprintable()
        if printable != previous:
            points.update((point - 1, point))
        previous = printable
    return "".join(chr(point) for point in sorted(points) if not 0xD800 <= point <= 0xDFFF)


def _pyfmt_edges() -> dict[str, list[object]]:
    """The values each form's generated lines put to it: the escapes and the quoting choices, every ASCII and Latin-1 character, characters beyond the BMP, empty and one-element containers, keys that sort differently by code point than by UTF-16, and the printable table's edges."""
    escapes = (
        "".join(chr(point) for point in range(0x20)) + "\"'\\/\x7f\x80\xa0\xad\u2028\ue650\uffff\U0001f600"
    )
    texts: list[object] = [
        "",
        "a",
        "'",
        '"',
        "'\"",
        "it's",
        'say "hi"',
        "\\",
        "\t\n\r",
        escapes,
        "".join(chr(point) for point in range(0x80, 0x100)),
        "e\u0301\u3000\U000e0001\U0010ffff",
        _printable_boundaries(),
    ]
    scalars: list[object] = [0, 1, -1, 2**63 - 1, -(2**63), True, False, None]
    containers: list[object] = [
        (),
        (1,),
        ("a",),
        ((),),
        (1, "a", None, True),
        ((((0, 1), (2, 3)), (), 0), ((), ((5, -1),), -2)),
        [],
        [()],
        ([], [1, [2, (3,)]]),
        {},
        {"a": (1, -2)},
        {"k": [], "j": {}},
        {1: "a", (2,): None, None: [True]},
    ]
    keyed: list[object] = [
        {},
        {"b": 1, "a": 2, "B": 3, "\xe9": 4, "\uffff": 5, "\U0001f600": 6, "\ue650": 7, "": 8},
        {"z": {"y": [], "x": {}}, "a": [1, (2, 3), None, True, False, -7]},
        {"s": escapes, "t": ("x", ["y", {"b": None, "a": 0}])},
        {"deep": {"a": [{"b": [{"c": []}]}]}, "n": -(2**63)},
    ]
    return {
        "repr": scalars + texts + containers + keyed[1:],
        "json-sorted": keyed + [["a", {"b": 1, "a": 0}], "x\u2028", 5, None, False],
        "json-fragment": keyed,
    }


def _pyfmt_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    for form, values in _pyfmt_edges().items():
        for index, value in enumerate(values):
            yield {
                "key": f"{form}:edge:{index}",
                "format": form,
                "input": value,
                "value": PYFMT_FORMS[form](value),
            }


class _DumpArguments(TypedDict):
    default_flow_style: bool
    width: int
    allow_unicode: bool
    sort_keys: bool


# The arguments `Drafter.draft_policy` passes `yaml.safe_dump`, the only dump the `yaml` stage checks.
YAML_DUMP_ARGUMENTS: _DumpArguments = {
    "default_flow_style": True,
    "width": 10**6,
    "allow_unicode": True,
    "sort_keys": False,
}


def yaml_dump(value: object) -> str:
    """The text `Drafter.draft_policy` ships for a record: its flow dump, stripped."""
    return yaml.safe_dump(value, **YAML_DUMP_ARGUMENTS).strip()


def _yaml_safe_dump(args: tuple, kwargs: dict) -> Finisher:
    if len(args) != 1 or kwargs != YAML_DUMP_ARGUMENTS:
        raise ValueError(
            f"the yaml stage checks only Drafter.draft_policy's dump, which a yaml.safe_dump call with "
            f"{len(args)} positional arguments and the keywords {kwargs!r} is not"
        )
    record = args[0]

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, str)
        digest = hashlib.sha1(compact(encode(record)).encode("ascii")).hexdigest()[:20]
        return {"key": f"yaml:{digest}", "input": record, "value": result.strip()}

    return finish


# How long the generated set's long scalars are: past the dump's width, so the column checks that would break a line fire.
YAML_LONG_SCALAR = 10**6 + 12


def _yaml_edge_strings() -> list[str]:
    """The strings the `yaml` stage's generated set puts to the dump: every implicit resolver's near misses and hits, each indicator at the start, inside and at the end, leading and trailing whitespace, the characters PyYAML escapes or writes as they are, and line breaks in every position."""
    resolvers = [
        *"yes Yes YES yES no No NO true True TRUE tRUE false False FALSE on On ON off Off OFF y n Y N".split(),
        *"nul null Null NULL nULL ~ ~~ < << <<< = == ! & * !a".split(),
        *"0 -0 +0 00 07 08 0o7 0b1 0b -0b1_0 0b2 0x 0x1F +0x_f 0xg 0X1F 1_000 +12 -12 _1 1_".split(),
        *"1:30 1:60 1:5 190:20:30 1:3: 012:30 -1:30 1:30.5 1:3.".split(),
        *"1. 1.5 -1.5e+3 1.5e3 1e5 1.5e+ .5 .5E-1 . ._ .inf -.Inf +.INF .iNf .nan .NaN -.nan 1_0.0_1".split(),
        "2001-12-14",
        "2001-12-1",
        "2001-12-14t21:59:43.10-05:00",
        "2001-12-14 21:59:43.10 -5",
        "2001-1-1T1:00:00",
        "2001-12-14\t 1:59:43Z",
        "2001-12-14 21:59:43.Z",
        "2001-12-14 21:59:43 +05:3",
        "2001-12-14 21:59:43+05:30",
        "2001-12-14T21:59",
        "",
        "true\n",
        "null\n",
        "5\n",
        "5\n\n",
    ]
    indicators = [
        *"-a - -- --- ---a ... .... ...a ?a ? :a : a:b a: a?b a,b ,a a] [a {a a} a#b #a &a *a !a |a >a %a @a `a".split(),
        "- a",
        "--- a",
        "? a",
        ": a",
        "a: b",
        "a ?",
        "a #b",
        "a# b",
        "'a",
        '"a',
        "a'b",
        'a"b',
        "a b",
        "a  b",
        " a",
        "a ",
        "a　",
        "　a",
        "·Pea ~b~ ·Tea | ·Day+Utter",
        "E650:E652",
        "Reviewer rejected the M1 outcome for E650:E652 (·Pea ~b~ ·Tea): note",
    ]
    characters = [
        "a\tb",
        "a\rb",
        "".join(chr(point) for point in range(0x100)),
        *(
            chr(point)
            for point in (
                *range(0x20),
                *range(0x7F, 0xA2),
                0xAD,
                0x2028,
                0x2029,
                0xD7FF,
                0xE000,
                0xE650,
                0xFEFF,
                0xFFFD,
                0xFFFE,
                0xFFFF,
                0x10000,
                0x1F600,
                0x10FFFE,
                0x10FFFF,
            )
        ),
        "a\U0010ffffb",
        "\U0001f600 a\xa0b﻿c",
    ]
    breaks = [
        "a\nb",
        "a\n\nb",
        "\na",
        "a\n",
        "\n",
        "\n\n",
        "a \nb",
        "a\n b",
        "a\n\n b",
        "a\r\nb",
        "a\x85b",
        "a b",
        "a  b",
        "a\n\x85b",
        "\x85a",
        "a\x85",
        "it's\nok",
        "'\n'",
        "a\n\tb",
        "a b\nc d\n\ne f",
    ]
    quotes = ["it's", "'", "''", 'say "hi"', "'\"", "\\", "a\\b", "\\'"]
    return resolvers + indicators + characters + breaks + quotes


def _yaml_edges() -> list[object]:
    """The values the `yaml` stage's generated lines dump, each built fresh, since PyYAML anchors a container it meets twice: every edge string at the root, as a mapping value, as a mapping key and two levels deep in a flow sequence; ints, booleans and None in the same places; empty and nested containers and keys that are containers; keys at either side of the simple-key length limit; scalars long enough to pass the width; and records shaped like the drafter's."""
    values: list[object] = []
    scalars: list[object] = [*_yaml_edge_strings(), 0, 7, -1, 2**63 - 1, -(2**63), True, False, None]
    for scalar in scalars:
        values += [scalar, {"v": scalar}, {scalar: 1}, {"a": [{"b": scalar}, scalar]}]
    values += [
        [],
        {},
        (),
        [[]],
        [{}],
        ([], {}),
        {"a": []},
        {"a": {}},
        [1, [2, (3,)]],
        {(): 1},
        {(1, "a"): 2},
        {(1, ()): 3},
        {((),): 4},
        {None: 1, True: 2, 5: 3, -5: 4, "": 5},
        {"x" * 122: 1},
        {"x" * 123: 1},
        {"\xe9" * 122: 1},
        {"\xe9" * 123: 1},
        {"a" * 60 + ": " + "b" * 60: 1},
        {"exit": "y5", "when": {"right": {"family": ["qsTea"]}}, "why": "Reviewer rejected"},
        {"entry": "y0", "by": 1, "when": {"left": {"family": ["qsPea"]}}, "why": "a: b"},
        {"cell": {"exit": "none"}, "over": {"entry": "y5"}, "mode": "absolute", "when": {"word": "isolated"}},
        {"stance": "half", "mode": "absolute", "when": {"word": "isolated"}, "why": "it's"},
    ]
    long_plain = ("ab " * (YAML_LONG_SCALAR // 3 + 1)).strip()
    long_single = "a: " * (YAML_LONG_SCALAR // 3 + 1)
    long_double = "\tb " * (YAML_LONG_SCALAR // 3 + 1)
    long_breaks = "ab \n" * (YAML_LONG_SCALAR // 4 + 1)
    for long in (long_plain, long_single, long_double, long_breaks):
        values += [long, [long, "x", "y"], {"k": long, "j": 1}]
    return values


def _yaml_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    for index, value in enumerate(_yaml_edges()):
        yield {"key": f"yaml:edge:{index}", "input": value, "value": yaml_dump(value)}


def _shaped_font(shaper: object) -> str:
    """The resolved path of the font a worker shaper shapes with. Every shaper the worker shapes with comes from `ink.shaper_for`, whose registry keys it by that path; any other shaper is refused."""
    for (path, _mtime, _size), registered in ink._shaper_registry.items():
        if registered is shaper:
            return path
    raise ValueError("the shape stage records only shapers ink.shaper_for hands out")


def _shape_line(font: str, text: str, features: object, result: object) -> dict[str, object]:
    """One `shape` line: the call, keyed by a digest of its typed encoding, and the `ShapeResult` it returned."""
    call = {"font": font, "text": text, "features": features}
    digest = hashlib.sha1(compact(encode(call)).encode("ascii")).hexdigest()[:20]
    return {"key": f"shape:{digest}", **call, "value": result}


def _shape_call(args: tuple, kwargs: dict) -> Finisher:
    shaper, text = args[0], args[1]
    features = args[2] if len(args) > 2 else kwargs.get("features")
    font = _shaped_font(shaper)
    return lambda result: _shape_line(font, text, features, result)


# The reprs of the diffs each `_ink_readings` call returns while a `_phase1_unit` call runs, which that call's `ink` line takes.
_INK_DIFFS: list[str] = []


def _ink_diffs(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> None:
        assert isinstance(result, tuple)
        _INK_DIFFS.append(repr(result[0]))

    return finish


def _ink_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"ink:{unit.ordinal}"
    text = "".join(chr(value) for value in unit.codepoint_values)
    configs = unit.configs
    mark = len(_INK_DIFFS)

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        projection = result[0]
        (diffs,) = _INK_DIFFS[mark:]
        del _INK_DIFFS[mark:]
        return {
            "key": key,
            "text": text,
            "configs": configs,
            "value": {
                "diffs": diffs,
                "ink_identical": projection.ink_identical,
                "picture_identical": projection.picture_identical,
                "junior_equivalent": projection.junior_equivalent,
                "ink_deltas": projection.ink_deltas,
                "diffs_digest": projection.diffs_digest,
            },
        }

    return finish


def _ink_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `ink` stage's generated lines: every distinct text of a source unit that holds `ss10`, in the handouts' order, put to the worker's Junior oracle directly, with its tracking-free Junior pieces and its answer for `ss10` alone. Phase 1 asks the oracle only about a unit under `ss10` alone that is neither ink- nor picture-identical, which the live corpus does not hold."""
    init = source.init
    oracle = ink.JuniorOracle(init["junior_font"], init["before_font"], init["after_font"], ink.shaper_for)
    texts = dict.fromkeys(
        "".join(chr(value) for value in unit.codepoint_values)
        for handout in source.handouts
        for unit in source.units(handout)
        if SS10 in unit.configs
    )
    try:
        for text in texts:
            digest = hashlib.sha1(compact(encode(text)).encode("ascii")).hexdigest()[:20]
            yield {
                "key": f"junior:{digest}",
                "text": text,
                "value": {
                    "junior_pieces": oracle._comparator.junior_pieces(text, oracle.tracking),
                    "approves": oracle.approves((SS10,), text),
                },
            }
    finally:
        ink.release_shape_memos()


def _settle_units(args: tuple, kwargs: dict) -> Finisher:
    units = tuple(args[1] if len(args) > 1 else kwargs["units"])

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, list) and len(result) == len(units)
        return [
            {
                "key": f"settle:{unit.configs[0]}:{unit.codepoints}",
                "codepoints": unit.codepoint_values,
                "config": unit.configs[0],
                "value": Record(
                    "ExplainReport",
                    (
                        ("codepoints", report.codepoints),
                        ("features", report.features),
                        ("positions", report.positions),
                    ),
                ),
            }
            for unit, report in zip(units, result)
        ]

    return finish


def _settle_spans(args: tuple, kwargs: dict) -> Finisher:
    values = tuple(args[1] if len(args) > 1 else kwargs["codepoint_values"])
    key = "spans:" + ":".join(f"{value:04X}" for value in values)
    return lambda result: {"key": key, "codepoints": values, "value": result}


# The report and the text of the last `ExplainReport.render` call, which the `_filter_explain` call that follows it in `Enricher.enrich` receives.
_RENDERED: list[tuple[ExplainReport, str]] = []


def _explain_render(args: tuple, _kwargs: dict) -> Finisher:
    report = args[0]

    def finish(result: object) -> None:
        assert isinstance(result, str)
        _RENDERED[:] = [(report, result)]

    return finish


def _explain_filter(args: tuple, kwargs: dict) -> Finisher:
    rendered = args[0] if args else kwargs["rendered"]
    diff_positions = tuple(args[1] if len(args) > 1 else kwargs["diff_positions"])
    if not _RENDERED or _RENDERED[0][1] is not rendered:
        raise ValueError(
            "_filter_explain received a text that no recorded ExplainReport.render call returned"
        )
    report, _text = _RENDERED.pop()
    config = feature_config_token(report.features)
    codepoints = ":".join(f"{value:04X}" for value in report.codepoints)
    key = f"explain:{config}:{codepoints}:" + ",".join(str(index) for index in diff_positions)
    return lambda result: {
        "key": key,
        "codepoints": tuple(report.codepoints),
        "config": config,
        "diff_positions": diff_positions,
        "rendered": rendered,
        "filtered": result,
    }


def _subset_line(origin: str, config: str, codepoints: str, row: object) -> dict[str, object]:
    return {
        "key": f"{origin}:{config}:{codepoints}",
        "config": config,
        "codepoints": codepoints,
        "value": row,
    }


def _subset_row(args: tuple, kwargs: dict) -> Finisher:
    config = args[1] if len(args) > 1 else kwargs["config"]
    codepoints = args[2] if len(args) > 2 else kwargs["codepoints"]
    return lambda result: _subset_line("subset", config, codepoints, result)


# Spellings of a canonical window that `int(part, 16)` reads as the window itself: lowercase, the `0x` prefix bare and with its underscore, a sign, an underscore between digits, surrounding whitespace `int` strips (an em space and a next-line among it), a leading zero slot, and the decimal digits of two other scripts.
_SUBSET_SPELLINGS: tuple[Callable[[str], str], ...] = (
    str.lower,
    lambda window: ":".join(f"0x{part}" for part in window.split(":")),
    lambda window: ":".join(f"0X_{part}" for part in window.split(":")),
    lambda window: ":".join(f"+{part}" for part in window.split(":")),
    lambda window: ":".join(f"{part[:2]}_{part[2:]}" for part in window.split(":")),
    lambda window: ":".join(f" \t{part} \x85" for part in window.split(":")),
    lambda window: f"0000:{window}" if window.count(":") < 3 else window,
    lambda window: window.translate({ord(digit): 0x0660 + int(digit) for digit in "0123456789"}),
    lambda window: window.translate({ord(digit): 0xFF10 + int(digit) for digit in "0123456789"}),
)

# Misspellings of a canonical window that `pack_key` refuses, each of which a reader too lenient by one rule would read as the window: what `int(part, 16)` refuses (a leading, trailing or doubled underscore, a doubled underscore after the prefix, a sign twice, inner whitespace, an ASCII separator `str.isspace` accepts but `int` does not strip, a NUL, a DEL), a negative, a codepoint past the slot width and one past 64 bits that both end in the window's first codepoint, zero slots that fill the key past four, and empty parts.
_SUBSET_MISSPELLINGS: tuple[Callable[[str], str], ...] = (
    lambda window: f"_{window}",
    lambda window: f"{window}_",
    lambda window: f"{window[:2]}__{window[2:]}",
    lambda window: f"0x__{window}",
    lambda window: f"+-{window}",
    lambda window: f"{window[:2]} {window[2:]}",
    lambda window: f"\x1c{window}",
    lambda window: f"{window}\x00",
    lambda window: f"{window}\x7f",
    lambda window: f"-{window}",
    lambda window: f"1{window}",
    lambda window: f"1{'0' * 16}{window}",
    lambda window: "0000:" * (5 - len(window.split(":"))) + window,
    lambda window: f":{window}",
    lambda window: f"{window}:",
    lambda window: window.replace(":", "::", 1),
)

# Strings `pack_key` refuses or no table holds: nothing, a sign alone, a bare prefix, a letter outside hex, a negative zero and a zero key, which pack, and a codepoint past the slot width.
_SUBSET_MISSES = ("", "+", "0x", "G650", "-0", "0000", "10000")


def _subset_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `subset` stage's generated lines (the module docstring), each looked up in the source's pack as `Enricher.subset_row` opens it."""
    init = source.init
    pack = SubsetPack.open(init["subset_pack"], table_digests(init["subset_dir"], ACCEPTANCE_CONFIGS))
    try:
        windows = [
            (config, key_codepoints(section.keys[index]))
            for config, section in pack._sections.items()
            if section.rows
            for index in sorted({0, section.rows - 1})
        ]
        config, window = windows[0]
        lookups = [
            *windows,
            *((config, spell(window)) for spell in _SUBSET_SPELLINGS),
            *((config, misspell(window)) for misspell in _SUBSET_MISSPELLINGS),
            *((config, text) for text in _SUBSET_MISSES),
            ("ss99", window),
        ]
        for config, codepoints in lookups:
            yield _subset_line("generated", config, codepoints, pack.row(config, codepoints))
    finally:
        pack.close()


# The `EnrichedUnit` fields the `enrich` stage records, every one but the unit, which is the line's input.
ENRICH_FIELDS = tuple(item.name for item in dataclasses.fields(EnrichedUnit) if item.name != "unit")

# The `enrich` stage's outputs: the recorded fields, the mismatch lines, and the unit's primary-unit projection.
ENRICH_OUTPUTS = (*ENRICH_FIELDS, "mismatches", "primary_unit_projection")

# The `enrich` stage's `geometry` fields: what `Enricher.enrich` computes before its summary.
ENRICH_GEOMETRY = (
    "before_glyphs",
    "before_junctions",
    "after_cells",
    "after_junctions",
    "after_extensions",
    "diff_positions",
    "pair",
    "highlight_before",
    "highlight_after",
    "boundary_marks",
    "after_spans",
    "before_spans",
    "secondary_junctions",
    "pair_codepoints",
)


def _enrich_line(key: str, enricher: Enricher, unit: Unit) -> Callable[[object], dict[str, object]]:
    """The `enrich` line of one `Enricher.enrich` call about to run on `unit`: its inputs now, and a finisher that adds the call's outputs from its result and the mismatch lines the call adds to the enricher's list."""
    mark = len(enricher.mismatches)
    line: dict[str, object] = {
        "key": key,
        "record": compact(units_exec.unit_record(unit)),
        "flags": {
            "ink_identical": unit.ink_identical,
            "picture_identical": unit.picture_identical,
            "junior_equivalent": unit.junior_equivalent,
        },
    }

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, EnrichedUnit)
        outputs: dict[str, object] = {name: getattr(result, name) for name in ENRICH_FIELDS}
        return {
            **line,
            **outputs,
            "mismatches": tuple(enricher.mismatches[mark:]),
            "primary_unit_projection": primary_unit_projection(result),
        }

    return finish


def _enrich_unit(args: tuple, kwargs: dict) -> Finisher:
    enricher = args[0]
    unit = args[1] if len(args) > 1 else kwargs["unit"]
    return _enrich_line(f"enrich:{unit.ordinal}", enricher, unit)


# The suffix the `enrich` stage's generated rows give their last glyph name, so the before font's kern-neutral shaping disagrees with the row.
RENAMED_GLYPH_SUFFIX = ".renamed"


def _enrich_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `enrich` stage's generated lines, which make both of `Enricher.enrich`'s mismatch lines fire, as the live corpus does not. The first unit of each first configuration in the handouts' order goes through `build._phase1_unit` as the worker runs it, so it carries the phase-1 flags, and is then enriched twice against that run's report: as a copy whose first `new` token is replaced by another cell token, under `generated:new:<ordinal>`, when its `new` holds only cell tokens; and against its subset row with the last glyph renamed (`RENAMED_GLYPH_SUFFIX`), which the line carries as its `row`, under `generated:row:<ordinal>`."""
    init = source.init
    firsts: dict[str, Unit] = {}
    for handout in source.handouts:
        for unit in source.units(handout):
            firsts.setdefault(unit.configs[0], unit)
    tokens = tuple(dict.fromkeys(token for unit in firsts.values() for token in unit.new if "/" in token))
    comparator = ink.InkComparator(init["before_font"], init["after_font"], ink.shaper_for)
    oracle = ink.JuniorOracle(init["junior_font"], init["before_font"], init["after_font"], ink.shaper_for)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(init["spec_root"])
    enricher = Enricher(
        spec,
        init["subset_dir"],
        init["after_font"],
        repo_root=init["repo_root"],
        before_font=init["before_font"],
        shaper_factory=ink.shaper_for,
        subset_pack=init["subset_pack"],
    )
    drafter = Drafter(
        init["after_font"],
        corpus_index=init["corpus_index"],
        repo_root=init["repo_root"],
        shaper_factory=ink.shaper_for,
    )
    try:
        for unit in firsts.values():
            (report,) = enricher.explain_units([unit])
            build._phase1_unit(
                unit, comparator, oracle, enricher, drafter, report, init["identical_classes"], init["fold"]
            )
            if unit.new and all("/" in token for token in unit.new):
                variant = copy.copy(unit)
                variant.new = (next(token for token in tokens if token != unit.new[0]), *unit.new[1:])
                finish = _enrich_line(f"generated:new:{unit.ordinal}", enricher, variant)
                yield finish(enricher.enrich(variant, report))
            row = enricher.subset_row(unit.configs[0], unit.codepoints)
            assert row is not None
            renamed = dataclasses.replace(
                row, glyphs=(*row.glyphs[:-1], row.glyphs[-1] + RENAMED_GLYPH_SUFFIX)
            )
            finish = _enrich_line(f"generated:row:{unit.ordinal}", enricher, unit)
            setattr(enricher, "subset_row", lambda _config, _codepoints: renamed)
            try:
                result = enricher.enrich(unit, report)
            finally:
                delattr(enricher, "subset_row")
            yield {**finish(result), "row": renamed}
    finally:
        ink.release_shape_memos()


# The scaffolds the `unit_scaffold` calls inside the `_phase1_unit` call under way returned, each copied before `unit_to_json` gives it the final class.
_SCAFFOLDS: list[object] = []


def _scaffold_returned(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> None:
        _SCAFFOLDS.append(copy.deepcopy(result))

    return finish


def _scaffold_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"scaffold:{unit.ordinal}"
    record = compact(units_exec.unit_record(unit))
    mark = len(_SCAFFOLDS)

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        projection = result[0]
        (scaffold,) = _SCAFFOLDS[mark:]
        del _SCAFFOLDS[mark:]
        return {
            "key": key,
            "record": record,
            "ink_deltas": dict(projection.ink_deltas),
            "final_class": projection.change_group or None,
            "value": {
                "scaffold": scaffold,
                "change_group": projection.change_group,
                "cluster": projection.cluster,
            },
        }

    return finish


def _scaffold_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    """The `scaffold` stage's generated lines: every non-empty subset of `ACCEPTANCE_CONFIGS`, its members in config order, put to `build.config_badge` against `ACCEPTANCE_CONFIGS`, under `badge:` and the subset. The corpus's config sets reach neither the three-clause gates nor the `only under:` fallback."""
    for size in range(1, len(ACCEPTANCE_CONFIGS) + 1):
        for configs in combinations(ACCEPTANCE_CONFIGS, size):
            gate, note = build.config_badge(configs, ACCEPTANCE_CONFIGS)
            yield {
                "key": "badge:" + ",".join(configs),
                "configs": configs,
                "value": {"gate": gate, "note": note},
            }


def _digest_key(prefix: str, call: Mapping[str, object]) -> str:
    return prefix + hashlib.sha1(compact(encode(call)).encode("ascii")).hexdigest()[:20]


def _raising(call: Callable[[], object]) -> object:
    """What `call` returns, or, when it raises ValueError or AssertionError, the exception's class name and message, which is what the replay gives a Python caller."""
    try:
        return call()
    except (ValueError, AssertionError) as error:
        return {"raised": type(error).__name__, "message": str(error)}


def _expect_line(
    drafter: Drafter, text: str, expect: str, features: dict[str, bool] | None, verdict: object
) -> dict[str, object]:
    """One `pin:` line of the `expect` stage: the replay's inputs, keyed by a digest of them, and as its value the pin's `parse` (its tokens and connections, or the ValueError's message), the `interpretations` `_expand_maybe_ligatures` expands it to, the `row` `row_for` shapes the text into with the drafter's after font under the replay's kern-neutral features, and the `verdict`, what `Drafter.validate_semantics` returned or raised (`_raising`). The interpretations and the row are None for a pin that does not parse, for which the replay shapes nothing."""
    call = {"text": text, "expect": expect, "features": features}
    ts = drafts._import_test_shaping()
    try:
        tokens, connections = ts.parse_expect(expect)
    except ValueError as error:
        parse, interpretations, row = str(error), None, None
    else:
        parse = (tokens, connections)
        interpretations = ts._expand_maybe_ligatures(list(tokens), list(connections))
        row = _raising(
            lambda: row_for(drafter.after_shaper, drafter.after_classifier, text, ink.kern_neutral(features))
        )
    return {
        "key": _digest_key("pin:", call),
        **call,
        "value": {"parse": parse, "interpretations": interpretations, "row": row, "verdict": verdict},
    }


def _expect_replay(args: tuple, kwargs: dict) -> Finisher:
    drafter, text, expect = args[0], args[1], args[2]
    features = args[3] if len(args) > 3 else kwargs.get("features")
    return lambda result: _expect_line(drafter, text, expect, features, result)


def _expect_any_of(args: tuple, kwargs: dict) -> Finisher:
    drafter = args[0]
    unit = (args[1] if len(args) > 1 else kwargs["enriched"]).unit
    text = "".join(chr(value) for value in unit.codepoint_values)
    features = drafts.features_dict(unit.configs) or None

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, drafts.AnyOfDraft)
        return [
            _expect_line(
                drafter,
                text,
                candidate,
                features,
                _raising(lambda candidate=candidate: drafter.validate_semantics(text, candidate, features)),
            )
            for candidate in result.candidates
        ]

    return finish


def _expect_string_line(
    values: object, spans: object, junctions: object, result: object
) -> dict[str, object]:
    call = {"codepoints": values, "spans": spans, "junctions": junctions}
    return {"key": _digest_key("string:", call), **call, "value": result}


def _expect_string(args: tuple, kwargs: dict) -> Finisher:
    values = args[0] if args else kwargs["codepoint_values"]
    spans = args[1] if len(args) > 1 else kwargs["spans"]
    junctions = args[2] if len(args) > 2 else kwargs["junctions"]
    return lambda result: _expect_string_line(values, spans, junctions, result)


# The `expect` stage's generated `drafts.expect_string` inputs: every boundary token, every connector, the two letters whose display is not their name, and a three-letter span.
_EXPECT_STRINGS: tuple[tuple[tuple[int, ...], tuple[tuple[int, int], ...], tuple[str, ...]], ...] = (
    ((0x200C, 0xE652, 0xE679), ((0, 1), (1, 3)), ("break",)),
    (
        (0x00B7, 0xE650, 0x0020, 0xE664, 0xE65D),
        ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5)),
        ("break", "y0", "y5", "y6"),
    ),
    ((0xE650, 0xE652, 0xE67A, 0xE653), ((0, 1), (1, 3), (3, 4)), ("y8", "break")),
    ((0xE652, 0xE67A, 0xE679), ((0, 3),), ()),
)

_PEA, _TEA, _DAY, _IT, _UTTER, _ZWNJ, _VS1 = "", "", "", "", "", "‌", "︀"


def _expect_edges() -> list[tuple[str, str, dict[str, bool] | None]]:
    """The `expect` stage's generated pins, each a text, an expect string and the replay's features: every connector, ligature mode, variant assertion, escape and lozenge, every parse error with texts its message must quote (quotes, an unprintable character, a code point beyond the BMP, the whitespace `str.isspace` accepts and Rust's `char::is_whitespace` does not), and the replays the corpus does not reach (more than one maybe-ligature, variation selectors, ◊ZWNJ, `ss10`, texts too short or too long for the tokens, and an empty text)."""
    word = _PEA + _TEA
    pins = [
        "",
        "   ",
        "·Pea ·Tea",
        "·Pea~x~·Tea",
        "·Pea ~b~ ·Tea",
        "·Pea ~t~ ·Tea",
        "·Pea ~6~ ·Tea",
        "·Pea | ·Tea",
        "·Pea |?| ·Tea",
        "·Pea ? ·Tea",
        "·Pea?·Tea",
        "·Pea |? ·Tea",
        "·Pea.half ·Tea.!half",
        "·Pea.alt.!half.extended ·Tea.half.∅",
        "·Pea.∅ ·Tea.∅",
        "·Pea.∅.∅ ·Tea",
        "·Pea.∅.half ·Tea",
        "·Pea.Half ·Tea",
        "·Pea.!Half ·Tea",
        "·Pea. ·Tea",
        "·pea ·Tea",
        "Pea ·Tea",
        "| ·Pea ·Tea",
        "·Pea ·Tea |",
        "·Pea ·Tea ~x~",
        "·Pea ~y~ ·Tea",
        "·Pea ·Tea",
        "·Pea　~x~ ·Tea",
        "·Pea\x1c·Tea",
        "\x85·Pea ·Tea\x85",
        "·Pea\t|\t·Tea",
        "·Pea​·Tea",
        "·Pea 'x'",
        '·Pea "x\'"',
        "·Pea ͸\x7f\U0001f600",
        "·Pea+Tea",
        "·Pea+tea",
        "·Pea+Tea+Day",
        "·Pea+??Tea",
        "\\· ·Pea",
        "\\x ·Tea",
        f"\\{_PEA} ·Tea",
        "\\",
        "\\\n ·Tea",
        "\\\U0001f600 ·Tea",
        "◊space ·Tea",
        "◊ZWNJ ·Tea",
        "◊zwnj ·Tea",
        "◊ ·Tea",
        "◊space1 ·Tea",
        "·-ing ·J'ai",
        "·J’ai ·Jai",
        "·Jay ·J'x",
        "·Jaix ·Tea",
        "·Tea+J’ai",
    ]
    edges: list[tuple[str, str, dict[str, bool] | None]] = [(word, pin, None) for pin in pins]
    edges += [
        ("", "", None),
        ("", "·Pea", None),
        (_PEA, "", None),
        (_PEA, "·Pea ·Tea", None),
        (_PEA, "·Tea", None),
        (_TEA, "·Tea+Utter", None),
        (_TEA + _UTTER, "·Tea+Utter", None),
        (_TEA + _UTTER, "·Tea+Utter.∅", None),
        (_TEA + _UTTER, "·Tea+?Utter", None),
        (_TEA + _UTTER, "·Tea+|Utter", None),
        (_TEA + _UTTER + _DAY + _UTTER, "·Tea+?Utter ·Day+|Utter", None),
        (_TEA + _UTTER + _DAY + _UTTER, "·Tea+?Utter ~x~ ·Day+?Utter", None),
        (_TEA + _UTTER + _DAY + _UTTER, "·Tea ·Utter ·Day ·Utter", None),
        (_PEA + _IT + _TEA, "·Pea ~x~ ·It.half | ·Tea.alt", None),
        (_PEA + _VS1 + _TEA, "·Pea ·Tea", None),
        (_VS1 + _PEA + _TEA + _VS1, "·Pea | ·Tea", None),
        (_PEA + _VS1 + _VS1, "·Pea", None),
        (_PEA + _ZWNJ + _TEA, "·Pea | ◊ZWNJ | ·Tea", None),
        (_PEA + _ZWNJ + _TEA, "·Pea ◊ZWNJ ·Tea", None),
        (_PEA + " " + _TEA, "·Pea | ◊space | ·Tea", None),
        (_PEA + "·" + _TEA, "·Pea | \\· | ·Tea", None),
        (word, "·Pea | ·Tea", {"ss10": True}),
        (word, "·Pea ·Tea", {"ss10": True}),
        (word, "·Pea ·Tea", {"ss03": True, "ss05": True}),
        (word, "·Pea ·Tea", {"kern": True}),
        (word, "·Pea ·Tea", {}),
        ("ab", "\\a | \\b", None),
        ("a", "\\a \\b", None),
    ]
    return edges


def _expect_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `expect` stage's generated lines: `drafts.expect_string` over `_EXPECT_STRINGS`, under `string:` and a digest of the call; and the replay of every pinned cell of the site pages, its whole text against its whole expect string under the cell's stylistic sets, and of `_expect_edges`, each under `pin:` and a digest of the replay's inputs (`_expect_line`), through a drafter over the source's after font."""
    init = source.init
    drafter = Drafter(
        init["after_font"],
        corpus_index=init["corpus_index"],
        repo_root=init["repo_root"],
        shaper_factory=ink.shaper_for,
    )
    ts = drafts._import_test_shaping()

    def line(text: str, expect: str, features: dict[str, bool] | None) -> dict[str, object]:
        verdict = _raising(lambda: drafter.validate_semantics(text, expect, features))
        return _expect_line(drafter, text, expect, features, verdict)

    try:
        for values, spans, junctions in _EXPECT_STRINGS:
            yield _expect_string_line(
                values, spans, junctions, drafts.expect_string(values, spans, junctions)
            )
        for page in fingerprint.read_site_corpus(REPO_ROOT).values():
            if page is None:
                continue
            collector = ts._DataExpectCollector()
            collector.feed(page.decode("utf-8"))
            for text, expect, _line, stylistic_set, _runs in collector.cells:
                if expect is None:
                    continue
                features = (
                    {f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()} if stylistic_set else None
                )
                yield line(text, expect, features)
        for text, expect, features in _expect_edges():
            yield line(text, expect, features)
    finally:
        ink.release_shape_memos()


# The definitions `Drafter.__init__` builds its checkers for, each of which the `schema` stage's generated set checks every base record against.
SCHEMA_DEFINITIONS = ("refuseRecord", "preferRecord", "contractRecord")
# The definition each policy list of a rune file is checked against in the `schema` stage's generated set: a drafted contract record has an extend record's shape.
SCHEMA_RUNE_LISTS = (
    ("refuse", "refuseRecord"),
    ("prefer", "preferRecord"),
    ("contract", "contractRecord"),
    ("extend", "contractRecord"),
)
# What the `schema` stage's mutations put in place of a value: every JSON kind, the Python kinds the schema must not take for one (a tuple, bytes, a frozenset), a dict with a key that is not a str, and strings near the schema's patterns and enums.
SCHEMA_PROBES: tuple[object, ...] = (
    None,
    True,
    False,
    0,
    1,
    -1,
    2,
    "",
    "x",
    "\t",
    "baseline",
    "none",
    "absolute",
    "half",
    "a-b",
    "ss01",
    "qsPea",
    "qsPea\n",
    "qsPea_qsTea",
    "isolated",
    b"x",
    frozenset({"x"}),
    [],
    ["qsPea"],
    ("qsPea",),
    {},
    {"family": "qsPea"},
    {1: "x"},
)
# The keys the `schema` stage's mutations add to each dict that lacks them, each with a value of the shape its definition takes, and two the schema knows nowhere.
SCHEMA_ADDITIONS: dict[object, object] = {
    "id": "a-b",
    "stance": "half",
    "entry": "baseline",
    "exit": "top",
    "cell": {"exit": "none"},
    "over": {"entry": "y6"},
    "mode": "absolute",
    "by": 1,
    "trim": 2,
    "why": "w",
    "when": {"word": "isolated"},
    "family": ["qsPea"],
    "class": "c",
    "except": [{"family": "qsTea"}],
    "then": {"family": "qsTea"},
    "self": {"entry": "live"},
    "left": {"family": "qsPea"},
    "right": {"family": "qsPea"},
    "zz": 1,
    2: "x",
}
# One drafted record of each shape the `schema` stage's run checks, with its definition, by the shape (`_schema_shape`): the drafted bases its generated set mutates, filled by the run that precedes the generators in `dump`.
_SCHEMA_DRAFTED: dict[str, tuple[str, object]] = {}


def _schema_shape(definition: str, record: object) -> str:
    """A drafted record's definition and the record without its `why`, which names the unit and so differs on nearly every record."""
    if isinstance(record, dict):
        record = {key: value for key, value in record.items() if key != "why"}
    return compact(encode([definition, record]))


def _schema_line(origin: str, definition: str, record: object, problems: object) -> dict[str, object]:
    call = {"definition": definition, "record": record}
    assert isinstance(problems, list)
    return {"key": _digest_key(f"{origin}:", call), **call, "value": not problems}


def _schema_check(args: tuple, kwargs: dict) -> Finisher | None:
    checker = args[0]
    record = args[1] if len(args) > 1 else kwargs["value"]
    definition = next((name for name, chosen in checker._definitions.items() if chosen is checker), None)
    if definition is None:
        return None
    _SCHEMA_DRAFTED.setdefault(_schema_shape(definition, record), (definition, copy.deepcopy(record)))
    return lambda problems: _schema_line("drafted", definition, record, problems)


def _schema_variants(value: object) -> Iterator[object]:
    """What the `schema` stage's mutations put in place of `value`: the probes, and variants of the value itself, near misses of a pattern for a string and the wrong container or length for a list."""
    yield from SCHEMA_PROBES
    if isinstance(value, str):
        yield from (
            value + "\n",
            value + "\n\n",
            value + "x",
            "x" + value,
            " " + value,
            value.upper(),
            value[:-1],
        )
    elif isinstance(value, list):
        yield from (tuple(value), value + value[:1], value[1:], [value])
    elif isinstance(value, dict):
        yield [value]
    elif isinstance(value, int) and not isinstance(value, bool):
        yield from (value - 1, value + 1)


def _schema_mutations(record: object) -> Iterator[object]:
    """Every mutation of `record` the `schema` stage's generated set checks: at every place in it, the value replaced by each variant (`_schema_variants`), each dict entry and list item removed, and each addition (`SCHEMA_ADDITIONS`) a dict lacks added."""

    def at(value: object, rebuilt: Callable[[object], object]) -> Iterator[object]:
        for variant in _schema_variants(value):
            yield rebuilt(variant)
        if isinstance(value, dict):
            for key, child in value.items():
                yield rebuilt({other: item for other, item in value.items() if other != key})
                yield from at(child, lambda new, key=key: rebuilt({**value, key: new}))
            for key, addition in SCHEMA_ADDITIONS.items():
                if key not in value:
                    yield rebuilt({**value, key: addition})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield rebuilt(value[:index] + value[index + 1 :])
                yield from at(
                    item, lambda new, index=index: rebuilt([*value[:index], new, *value[index + 1 :]])
                )

    yield from at(record, lambda new: new)


def _schema_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `schema` stage's generated lines, each checked by the rune schema the source's worker reads, under `generated:` and a digest of the definition and the record: the bases, one drafted record of each shape the run checked (`_SCHEMA_DRAFTED`) and every policy record of the source's rune files (`SCHEMA_RUNE_LISTS`), each against every definition the drafter checks (`SCHEMA_DEFINITIONS`), and each base's mutations (`_schema_mutations`) against its own definition."""
    schema_file = Path(source.init["repo_root"]) / "rebuild" / "schema" / "rune.schema.json"
    schema = spec_load._load_schema(schema_file.parent, schema_file.name)
    bases = list(_SCHEMA_DRAFTED.values())
    for path in sorted((Path(source.init["spec_root"]) / "glyph_data" / "runes").glob("*.yaml")):
        policy = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("policy") or {}
        for name, definition in SCHEMA_RUNE_LISTS:
            bases += [(definition, record) for record in policy.get(name) or ()]

    def line(definition: str, record: object) -> dict[str, object]:
        return _schema_line("generated", definition, record, schema.for_definition(definition).check(record))

    for definition, record in bases:
        for other in SCHEMA_DEFINITIONS:
            yield line(other, record)
        for mutated in _schema_mutations(record):
            yield line(definition, mutated)


def _draft_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"draft:{unit.ordinal}"
    record = compact(units_exec.unit_record(unit))

    def finish(result: object) -> dict[str, object] | None:
        assert isinstance(result, tuple)
        projection, fragment = result[0], result[1]
        if "drafts" not in fragment:
            return None
        return {
            "key": key,
            "record": record,
            "flags": {
                "ink_identical": projection.ink_identical,
                "picture_identical": projection.picture_identical,
                "junior_equivalent": projection.junior_equivalent,
            },
            "value": fragment["drafts"],
        }

    return finish


# The shard bytes the `_fragment_bytes` calls inside the `_phase1_unit` call under way returned.
_FRAGMENT_BYTES: list[bytes] = []


def _fragment_bytes_returned(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> None:
        assert isinstance(result, bytes)
        _FRAGMENT_BYTES.append(result)

    return finish


def _fragment_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"fragment:{unit.ordinal}"
    record = compact(units_exec.unit_record(unit))
    mark = len(_FRAGMENT_BYTES)

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        projection, fragment = result[0], result[1]
        (body,) = _FRAGMENT_BYTES[mark:]
        del _FRAGMENT_BYTES[mark:]
        return {
            "key": key,
            "record": record,
            "flags": {
                "ink_identical": projection.ink_identical,
                "picture_identical": projection.picture_identical,
                "junior_equivalent": projection.junior_equivalent,
            },
            "ink_deltas": dict(projection.ink_deltas),
            "final_class": projection.change_group or None,
            "value": {
                "fragment": body.decode("ascii"),
                "content_key": fragment["content_key"],
                "id": fragment["id"],
            },
        }

    return finish


def _fragment_packed(
    init: Mapping[str, object], expected: Mapping[str, object], packed: object
) -> str | None:
    """The `fragment` stage's answer check: the problem with the Rust answer's deflated fragment, or None when it inflates through the source's preset dictionary to the Python fragment's bytes and its stream ends exactly where its bytes end, the two things `build._FragmentArena.read` requires of a span."""
    if not isinstance(packed, bytes):
        return f"the deflated fragment is {type(packed).__name__}, not bytes"
    value = expected.get("value")
    if not isinstance(value, dict):
        return None
    dictionary = init["dictionary"]
    assert isinstance(dictionary, bytes)
    inflater = zlib.decompressobj(zdict=dictionary)
    try:
        body = inflater.decompress(packed)
    except zlib.error as error:
        return f"the deflated fragment does not inflate ({error})"
    if not inflater.eof or inflater.unused_data:
        return "the deflated fragment's stream does not end where its bytes end"
    if body != value["fragment"].encode("ascii"):
        return "the deflated fragment inflates to other bytes than the Python fragment's"
    return None


# The smallest fragment of each shape (`_check_shape`) the `check` stage's run checks, by shape, with its encoded length: the bases its generated set mutates, filled by the run that precedes the generators in `dump`.
_CHECK_DRAFTED: dict[str, tuple[int, dict]] = {}
# How many levels below each top-level fragment key the `check` stage's generic mutations reach; a key not named here is only replaced or removed.
CHECK_DEPTHS: dict[str, int] = {
    "before": 2,
    "after": 2,
    "highlight": 2,
    "pair": 1,
    "pair_codepoints": 1,
    "drafts": 3,
    "config_gate": 2,
    "render_groups": 2,
    "boundary_marks": 2,
    "ink_deltas": 1,
    "notation_tokens": 1,
    "configs": 1,
    "kinds": 1,
    "provenance": 1,
}
CHECK_PROBES: tuple[object, ...] = (
    None,
    True,
    False,
    0,
    1,
    -1,
    "",
    "x",
    "\n",
    [],
    ["x"],
    ("x",),
    {},
    {"x": 1},
)
# The exceptions `check_unit` raises on a malformed fragment, which the `check` stage records by class name.
CHECK_RAISES = (TypeError, AttributeError, ValueError)


def _check_shape(fragment: dict) -> str:
    """What sorts a drafted fragment into a base of the `check` stage's generated set: its slimness, its machine flags and exemption, and whether it carries a pair, a policy draft, a ledger class and a config gate."""
    drafted = fragment.get("drafts")
    return compact(
        encode(
            [
                audit.slim_fragment(fragment),
                [fragment.get(check_id) is True for check_id in audit.MACHINE_CHECKS],
                fragment.get("no_verdict") is True,
                fragment.get("pair") is None,
                not isinstance(drafted, dict) or drafted.get("policy") is None,
                bool(fragment.get("ledger_class")),
                fragment.get("config_gate") is None,
            ]
        )
    )


def _check_line(origin: str, fragment: dict) -> dict[str, object]:
    try:
        value: object = build.check_unit(fragment, at=(build.DRAFTED,))
    except CHECK_RAISES as error:
        value = {"raised": type(error).__name__}
    return {"key": _digest_key(f"{origin}:", {"fragment": fragment}), "fragment": fragment, "value": value}


def _check_unit(args: tuple, kwargs: dict) -> Finisher | None:
    if len(args) != 1 or kwargs != {"at": (build.DRAFTED,)}:
        return None
    fragment = args[0]
    text = compact(encode({"fragment": fragment}))
    key = "drafted:" + hashlib.sha1(text.encode("ascii")).hexdigest()[:20]
    shape = _check_shape(fragment)
    held = _CHECK_DRAFTED.get(shape)
    if held is None or len(text) < held[0]:
        _CHECK_DRAFTED[shape] = (len(text), copy.deepcopy(fragment))
    return lambda errors: {"key": key, "fragment": fragment, "value": errors}


def _check_variants(value: object) -> Iterator[object]:
    """What the `check` stage's generic mutations put in place of `value`: the probes, and variants of the value itself, near misses for a string, the wrong container, length or order for a list, a wrapped dict and a neighboring int."""
    yield from CHECK_PROBES
    if isinstance(value, str):
        yield from (value + "\n", value + "x", value[:-1], value.upper(), value.lower())
    elif isinstance(value, list):
        yield from (tuple(value), value + value[:1], value[1:], value[::-1], [value])
    elif isinstance(value, dict):
        yield [value]
    elif isinstance(value, int) and not isinstance(value, bool):
        yield from (value - 1, value + 1)


def _check_mutations(fragment: dict) -> Iterator[dict]:
    """The `check` stage's generic mutations of `fragment`: each top-level entry removed, and at every place `CHECK_DEPTHS` reaches below it, the value replaced by each variant (`_check_variants`), each dict entry removed, and a list's first and last item removed."""

    def at(value: object, rebuilt: Callable[[object], dict], depth: int) -> Iterator[dict]:
        for variant in _check_variants(value):
            yield rebuilt(variant)
        if depth <= 0:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                yield rebuilt({other: item for other, item in value.items() if other != key})
                yield from at(child, lambda new, key=key: rebuilt({**value, key: new}), depth - 1)
        elif isinstance(value, list):
            for index in sorted({0, len(value) - 1}) if value else ():
                yield rebuilt(value[:index] + value[index + 1 :])
                yield from at(
                    value[index],
                    lambda new, index=index: rebuilt([*value[:index], new, *value[index + 1 :]]),
                    depth - 1,
                )

    for key, child in fragment.items():
        yield {other: item for other, item in fragment.items() if other != key}
        yield from at(child, lambda new, key=key: {**fragment, key: new}, CHECK_DEPTHS.get(key, 0))


def _check_extras(fragment: dict) -> Iterator[dict]:
    """The `check` stage's targeted mutations of `fragment`, one or more for each predicate the generic ones do not reach: ids and content keys `int(..., 16)` and the id pattern read in unusual ways, every combination of the machine flags and the exemption, mismatched deltas and ledger classes, config gates with their notes, render groups that do or do not partition the configs, iterables Python walks unexpectedly, junction tokens, spans, highlights, the slim keys on a slim fragment, and drafts whose members are malformed or whose any-of candidates do not parse."""

    def put(**changes: object) -> dict:
        return {**fragment, **changes}

    unit_id = fragment.get("id")
    if isinstance(unit_id, str):
        for spelling in (
            unit_id + "\n",
            unit_id + "\n\n",
            "\n" + unit_id,
            "e" + unit_id[1:],
            unit_id[:-1] + "0",
            unit_id[:-1] + "l",
            unit_id + "1",
            unit_id[:-1],
        ):
            yield put(id=spelling)
    stamp = fragment.get("content_key")
    if isinstance(stamp, str):
        for spelling in (
            stamp.upper(),
            stamp[:-1],
            stamp + "0",
            "g" + stamp[1:],
            "0x" + stamp[2:],
            "0X_" + stamp[3:],
            " " + stamp[1:],
            "　" + stamp[1:],
            "-" + stamp[1:],
            "+" + stamp[1:],
            stamp[:4] + "_" + stamp[5:],
            stamp[:4] + "__" + stamp[6:],
            "_" + stamp[1:],
            "٣" + stamp[1:],
            "１" + stamp[1:],
        ):
            yield put(content_key=spelling)
            try:
                matching = unit_cache.unit_id_for(spelling)
            except ValueError:
                continue
            yield put(content_key=spelling, id=matching)
    for flags in product((True, False), repeat=4):
        yield put(
            **dict(zip(("ink_identical", "picture_identical", "junior_equivalent", "no_verdict"), flags))
        )
    configs = fragment.get("configs")
    first = configs[0] if isinstance(configs, list) and configs else "default"
    for deltas in (
        {first: "d-" + "0" * 12},
        {first: "d-" + "A" * 12},
        {first: "d-" + "0" * 11},
        {first: "d-" + "0" * 13},
        {first: "e-" + "0" * 12},
        {first: "d-" + "٣" * 12},
        {"": "d-" + "0" * 12},
        {"elsewhere": "d-" + "0" * 12},
        {first: 5},
        {1: "d-" + "0" * 12},
        {},
    ):
        for ink_identical, picture_identical in ((True, False), (False, True), (False, False)):
            yield put(ink_deltas=deltas, ink_identical=ink_identical, picture_identical=picture_identical)
    for ledger in (fragment.get("class"), "other", "", 5):
        for no_verdict in (True, False):
            yield put(ledger_class=ledger, no_verdict=no_verdict)
    yield put(batch=1)
    for summary in ("Old: x", "New: a\nb", "New: ", "new: x", "New:x"):
        yield put(summary=summary)
    clause = {"feature": "ss03", "state": "on", "text": "with ss03 on"}
    other = {"feature": "ss05", "state": "off", "text": "and ss05 off"}
    for gate, note in (
        ([clause], "with ss03 on"),
        ([clause], "with ss03 on "),
        ([clause, other], "with ss03 on and ss05 off"),
        ([clause, other], "with ss03 on"),
        ([{**clause, "state": "maybe"}], "with ss03 on"),
        ([{**clause, "state": True}], "with ss03 on"),
        ([{**clause, "feature": 3}], "with ss03 on"),
        ([{**clause, "text": ""}], ""),
        ([{**clause, "text": 5}], "with ss03 on"),
        ([{"feature": "ss03", "state": "on"}], ""),
        ([clause, "with ss03 on"], "with ss03 on"),
        ([clause, None], "with ss03 on"),
        ([{**clause, "text": 5}, None], "with ss03 on"),
        ([[clause]], "with ss03 on"),
        ([], None),
        ((clause,), "with ss03 on"),
    ):
        yield put(config_gate=gate, config_note=note)
    listed = list(configs) if isinstance(configs, list) else []
    for groups in (
        [{"configs": listed}, {"configs": listed}],
        [{"configs": [*listed, "elsewhere"]}],
        [{"configs": [*listed, *listed[:1]]}],
        [{"configs": listed[::-1]}],
        [{"configs": [["x"]]}],
        [{"configs": [1, "a"]}],
        [{"configs": []}],
        [{"config": listed}],
        [{"configs": tuple(listed)}],
        ["configs"],
    ):
        yield put(render_groups=groups)
    for unit_configs, grouped in (
        (["b", "a"], ["a", "b"]),
        ([2, 1], [1, 2]),
        ([True, 2], [1, 2]),
        ([1, "a"], ["a", 1]),
        ([None, 1], [1, None]),
        ([("b",), ("a",)], [("a",), ("b",)]),
        ([("a", 1), ("a", "b")], [("a", 1), ("a", "b")]),
        ([["a"]], [["a"]]),
        (["a", "a"], ["a"]),
        (["a"], ["a", "a"]),
    ):
        yield put(configs=unit_configs, render_groups=[{"configs": grouped}], ink_deltas={})
    for marks in (
        "ab",
        {"index": 0},
        5,
        True,
        b"ab",
        [{"index": 0, "kind": "k"}],
        [{"index": 0, "kind": "k", "x": 1, "y": 2}],
        [["index", "kind", "x"]],
    ):
        yield put(boundary_marks=marks)
    for codepoints in ("e650", "", "E650::E651", "E650:G", ":", None, 5, "E650 "):
        yield put(codepoints=codepoints)
    for entities in ("&#xE650", "E650;", "&#x;", None):
        yield put(text_entities=entities)
    for side in ("before", "after"):
        record = fragment.get(side)
        if not isinstance(record, dict) or not isinstance(record.get("junctions"), list):
            continue
        junctions = record["junctions"]
        for token in ("y12", "y", "y٣", "y²", "y0+y5", "Y5", "absent", "lig", "break", "y-1", "y 5", 5, None):
            yield put(**{side: {**record, "junctions": [token] * max(len(junctions), 1)}})
        for name in ("glyphs", "cells", "extensions"):
            if name in record:
                yield put(**{side: {**record, name: "abc"}})
                yield put(**{side: {**record, name: {"a": 1}}})
    tokens = fragment.get("notation_tokens")
    count = len(tokens) if isinstance(tokens, list) else 2
    for pair in (
        {"left": 1, "right": 1},
        {"left": 2, "right": 1},
        {"left": True, "right": 2},
        {"left": "0", "right": 1},
        {"left": 0},
        None,
        [0, 1],
    ):
        yield put(pair=pair)
    for span in (
        [0, count],
        [0, count - 1],
        [True, True],
        [-1, 0],
        [1, 0],
        [0, 1, 2],
        (0, 1),
        None,
        [0, "1"],
    ):
        yield put(pair_codepoints=span)
        yield put(pair_codepoints=span, pair={"left": 0, "right": 1})
        yield put(pair_codepoints=span, pair=None)
    rect = {"x_min": 0, "x_max": 1, "advance_total": 2}
    for broken in (
        {"x_min": 5, "x_max": 1, "advance_total": 9},
        {"x_min": 0, "x_max": 9, "advance_total": 1},
        {"x_min": False, "x_max": True, "advance_total": 1},
        {"x_min": 0, "x_max": 1},
        {"x_min": 0, "x_max": 1, "advance_total": "2"},
        {"x_min": -1, "x_max": -1, "advance_total": -1},
    ):
        yield put(highlight={"before": rect, "after": broken})
        yield put(highlight={"before": broken, "after": rect})
    yield put(highlight=[rect, rect])
    for key, value in (
        ("highlight", {"before": rect, "after": rect}),
        ("explain", "x"),
        ("drafts", {"pin": None, "policy": None, "any_of": None}),
    ):
        if key not in fragment:
            yield put(**{key: value})
    drafted = fragment.get("drafts")
    if not isinstance(drafted, dict):
        return

    def draft(**changes: object) -> dict:
        assert isinstance(drafted, dict)
        return put(drafts={**drafted, **changes})

    pin = drafted.get("pin")
    for value in ("pin", ["pin"], 5):
        yield draft(pin=value)
    if isinstance(pin, dict):
        for key, value in (
            ("syntax", "fail"),
            ("syntax", None),
            ("semantics_after_font", "fail: x"),
            ("attribute", "data-other"),
            ("attribute", ""),
            ("stylistic_set", 1),
            ("stylistic_set", "ss03"),
            ("expect", 5),
        ):
            yield draft(pin={**pin, key: value})
    policy = drafted.get("policy")
    if not isinstance(policy, dict):
        policy = {
            "file": "glyph_data/runes/qsPea.yaml",
            "keypath": "policy.refuse[+]",
            "suggested_record": "{}",
            "decided_stage": "refuse",
            "why_stub": "why",
            "names_provenance": [],
            "schema_valid": True,
        }
        yield draft(policy=policy)
        yield draft(policy="policy")
    names = policy.get("names_provenance")
    named = list(names) if isinstance(names, list) else []
    for key, value in (
        ("keypath", "policy.other[+]"),
        ("keypath", None),
        ("schema_valid", False),
        ("schema_valid", 1),
        ("names_provenance", [*named, "elsewhere"]),
        ("names_provenance", [["x"]]),
        ("names_provenance", "x"),
        ("names_provenance", []),
        ("why_stub", ""),
    ):
        yield draft(policy={**policy, key: value})
    yield put(provenance=[["x"]], drafts={**drafted, "policy": policy})
    any_of = drafted.get("any_of")
    if not isinstance(any_of, dict):
        return
    candidates = any_of.get("candidates")
    offered = list(candidates) if isinstance(candidates, list) else []
    expect = pin.get("expect") if isinstance(pin, dict) else None
    for value in (offered + offered[:1], [1, True], [["x"]], [1], [None], [expect], [expect, expect], []):
        yield draft(any_of={**any_of, "candidates": value})
    for _text, edge, _features in _expect_edges():
        yield draft(any_of={**any_of, "candidates": [*offered, edge]})
        yield draft(pin=None, any_of={**any_of, "candidates": [edge]})
    for key, value in (("text", ""), ("text", 5), ("features", []), ("features", None)):
        yield draft(any_of={**any_of, key: value})
    yield draft(any_of="any_of")
    yield draft(pin=None)


def _check_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    """The `check` stage's generated lines, under `generated:` and a digest of the fragment: the generic (`_check_mutations`) and targeted (`_check_extras`) mutations of the smallest drafted fragment of each shape the run checked (`_CHECK_DRAFTED`), each checked by `check_unit` at `DRAFTED`, with what it returns or the class of the exception it raises."""
    for shape in sorted(_CHECK_DRAFTED):
        base = _CHECK_DRAFTED[shape][1]
        for mutated in chain(_check_mutations(base), _check_extras(base)):
            yield _check_line("generated", mutated)


# The worker's stage registry. The task that ports a stage adds the stage here (the plan's § Working rules).
STAGES: dict[str, Stage[Source]] = {
    "wire": Stage("wire", {"_phase1_unit": _wire_unit}, _wire_generated),
    "pyfmt": Stage(
        "pyfmt",
        {
            "_ink_readings": _pyfmt_ink_readings,
            "_cluster_id_from_repr": _pyfmt_cluster_head,
            "carry_projection": _pyfmt_carry_projection,
            "_fragment_bytes": _pyfmt_fragment_bytes,
        },
        _pyfmt_generated,
    ),
    "yaml": Stage("yaml", {"yaml.safe_dump": _yaml_safe_dump}, _yaml_generated),
    "shape": Stage("shape", {"Shaper.shape": _shape_call}),
    "ink": Stage("ink", {"_phase1_unit": _ink_unit, "_ink_readings": _ink_diffs}, _ink_generated),
    "settle": Stage(
        "settle", {"Enricher.explain_units": _settle_units, "Enricher.formed_spans": _settle_spans}
    ),
    "explain": Stage(
        "explain",
        {"ExplainReport.render": _explain_render, "_filter_explain": _explain_filter},
        outputs=("rendered", "filtered"),
    ),
    "subset": Stage("subset", {"SubsetPack.row": _subset_row}, _subset_generated),
    "enrich": Stage(
        "enrich",
        {"Enricher.enrich": _enrich_unit},
        _enrich_generated,
        outputs=ENRICH_OUTPUTS,
        field_groups={"geometry": ENRICH_GEOMETRY},
    ),
    "scaffold": Stage(
        "scaffold",
        {"_phase1_unit": _scaffold_unit, "unit_scaffold": _scaffold_returned},
        _scaffold_generated,
    ),
    "expect": Stage(
        "expect",
        {
            "expect_string": _expect_string,
            "Drafter.validate_semantics": _expect_replay,
            "Drafter.draft_any_of": _expect_any_of,
        },
        _expect_generated,
    ),
    "schema": Stage("schema", {"_SchemaValidator.check": _schema_check}, _schema_generated),
    "draft": Stage("draft", {"_phase1_unit": _draft_unit}),
    "fragment": Stage(
        "fragment",
        {"_phase1_unit": _fragment_unit, "_fragment_bytes": _fragment_bytes_returned},
        answer_checks={"packed": _fragment_packed},
    ),
    "check": Stage("check", {"check_unit": _check_unit}, _check_generated),
}


class _Sink:
    """The open `STAGE.ndjson` files of one run under `out`, each line a JSON object whose `from` says whether the run or the stage's generator produced it and whose other fields are typed values. A line whose key a stage already wrote is written once: a second, equal line is dropped, and a different one raises ValueError. A boundary stage's lines are its `Boundary`'s at the stride `every`."""

    def __init__(self, out: Path, stages: Sequence[Stage[Any]], every: int = 1) -> None:
        self._handles: dict[str, IO[str]] = {
            stage.name: (out / f"{stage.name}.ndjson").open("w", encoding="ascii") for stage in stages
        }
        self._written: dict[str, dict[object, bytes]] = {stage.name: {} for stage in stages}
        self._kinds = {stage.name: stage.kind for stage in stages}
        self._every = every

    def record(
        self, stage: str, recorded: Mapping[str, object] | list[Mapping[str, object]] | Boundary
    ) -> None:
        """Write what one of the run's calls recorded for `stage`: a call stage's line or lines, or a boundary stage's `Boundary`; either kind recording the other's form raises TypeError."""
        if isinstance(recorded, Boundary) != (self._kinds[stage] == "boundary"):
            raise TypeError(f"the {stage} stage records {self._kinds[stage]} lines, not {recorded!r:.80}")
        if isinstance(recorded, Boundary):
            lines: Iterable[Mapping[str, object]] = recorded.lines(self._every)
        else:
            lines = recorded if isinstance(recorded, list) else [recorded]
        for line in lines:
            self.write(stage, "run", line)

    def write(self, stage: str, origin: str, fields: Mapping[str, object]) -> None:
        text = compact({"from": origin} | {name: encode(value) for name, value in fields.items()})
        digest = hashlib.sha1(text.encode("ascii")).digest()
        key = fields.get("key")
        written = self._written[stage]
        if key in written:
            if written[key] != digest:
                raise ValueError(f"the {stage} stage recorded two different lines under the key {key!r}")
            return
        written[key] = digest
        self._handles[stage].write(text + "\n")

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()


def _resolve(hook: str, hooks: Mapping[str, tuple[str, str]]) -> tuple[object, str]:
    module_name, path = hooks[hook]
    owner: object = importlib.import_module(module_name)
    *parents, attribute = path.split(".")
    for parent in parents:
        owner = getattr(owner, parent)
    return owner, attribute


@contextlib.contextmanager
def wrapped(
    stages: Sequence[Stage[Any]], sink: _Sink | None, hooks: Mapping[str, tuple[str, str]] = HOOKS
) -> Iterator[None]:
    """Replace every hook point of `hooks` with a wrapper that calls the original and passes the call to each stage's recorder for that hook, and restore the originals on exit. A stage recorder for a hook `hooks` does not name raises ValueError."""
    unhooked = sorted({hook for stage in stages for hook in stage.recorders if hook not in hooks})
    if unhooked:
        raise ValueError(f"no hook point is registered for {', '.join(unhooked)}")
    restore: list[tuple[object, str, object]] = []
    try:
        for hook in hooks:
            owner, attribute = _resolve(hook, hooks)
            original = getattr(owner, attribute)
            recorders = [(stage.name, stage.recorders[hook]) for stage in stages if hook in stage.recorders]
            setattr(owner, attribute, _wrapper(original, recorders, sink))
            restore.append((owner, attribute, original))
        yield
    finally:
        for owner, attribute, original in reversed(restore):
            setattr(owner, attribute, original)


def _wrapper(original: Callable, recorders: list[tuple[str, Recorder]], sink: _Sink | None) -> Callable:
    @functools.wraps(original)
    def call(*args, **kwargs):
        finishers = [
            (name, finish) for name, record in recorders if (finish := record(args, kwargs)) is not None
        ]
        result = original(*args, **kwargs)
        for name, finish in finishers:
            recorded = finish(result)
            if recorded is not None and sink is not None:
                sink.record(name, recorded)
        return result

    return call


# --- running the worker -------------------------------------------------------------------------


class _Pipe:
    """The worker's end of its pipe, in process: `recv` hands out one `phase1` message per handout, materialized as it is asked for and passed through multiprocessing's pickler as `Connection.send` and `recv` pass it, then `phase1-done` and `stop`; `send` takes each reply to `on_batch`."""

    def __init__(self, source: Source, on_batch: Callable[[list, list[str]], None]) -> None:
        self._source = source
        self._on_batch = on_batch
        self._next = 0
        self._done = False
        self.peak = 0

    def recv(self) -> tuple:
        if self._next < len(self._source.handouts):
            units = self._source.units(self._source.handouts[self._next])
            self._next += 1
            return pickle.loads(ForkingPickler.dumps(("phase1", units, "w0")))
        if not self._done:
            self._done = True
            return ("phase1-done",)
        return ("stop",)

    def send(self, reply: tuple) -> None:
        reply = pickle.loads(ForkingPickler.dumps(reply))
        if reply[0] == "batch":
            self._on_batch(reply[1], reply[2])
        elif reply[0] == "peak":
            self.peak = reply[1]
        elif reply[0] == "error":
            raise RuntimeError("the corpus worker failed:\n" + reply[1])

    def close(self) -> None:
        pass


@dataclass
class RunResult:
    """What one worker run returned: the units, a sha256 over every projection's and every batch's contract errors' typed encoding in reply order, and the projections that did not survive the typed encoding's round trip, by ordinal."""

    units: int = 0
    digest: str = ""
    unfaithful: list[int] = field(default_factory=list)
    contract_errors: int = 0


def run_worker(source: Source) -> RunResult:
    """Run `build._corpus_worker` over the source's handouts in this process, as a pool worker runs them, and return the run's digest and round-trip result. A worker failure raises RuntimeError with its traceback."""
    digest = hashlib.sha256()
    result = RunResult()

    def on_batch(projections: list, contract_errors: list[str]) -> None:
        for projection in projections:
            text = compact(encode(projection))
            digest.update(text.encode("ascii") + b"\n")
            if not same(projection, decode(json.loads(text))):
                result.unfaithful.append(projection.ordinal)
            result.units += 1
        digest.update(compact(encode(contract_errors)).encode("ascii") + b"\n")
        result.contract_errors += len(contract_errors)

    pipe = _Pipe(source, on_batch)
    build._corpus_worker(pipe, source.init)
    if not pipe.peak:
        raise RuntimeError("the corpus worker stopped without answering the stop message")
    result.digest = digest.hexdigest()
    return result


# --- the build scenarios ------------------------------------------------------------------------

SCENARIO_ROOT = REPO_ROOT / "var" / "rust-port" / "build-scenarios"
MINI_SPEC = SCENARIO_ROOT / "mini-spec"
MINI_BASE = SCENARIO_ROOT / "mini-base"
LIVE_BASE = SCENARIO_ROOT / "live-base"
# Each build scenario and the sources it runs on (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Scenarios).
BUILD_SCENARIOS: dict[str, tuple[str, ...]] = {
    "first": ("mini",),
    "cold": ("mini", "live"),
    "warm": ("mini", "live"),
    "incremental": ("mini", "live"),
    "rebase": ("mini", "live"),
    "restamped": ("mini",),
    "rederived": ("mini",),
    "relocate": ("mini",),
    "torn": ("mini",),
    "chain": ("mini",),
    "refusals": ("mini",),
}
# The scenario `refusals` runs only by name; `--scenario all` names every other scenario of the source.
BY_NAME_ONLY = ("refusals",)
BUILD_REQUEST_FORMAT = "ams-review-build-scenario-request/1"
# The audit row's fields the scenarios' edits read: the window, the matched entry, and the two rendered-name fields.
_WINDOW, _ENTRY, _BASELINE, _NEW = 1, 3, 4, 5
# The ledger class the mini's edited audit retags windows into, as `rebuild/test_unit_cache.py`'s `_edited_audit` does.
RETAG_CLASS = "identical-unused-join-name"
# The output whose fold claim a chain step's pass holds when its claim is held elsewhere: the windows rows, which claim before the corpus build in a cycle (`row_delta.claim_fold`).
HELD_CLAIM_OUTPUT = "windows-default.tsv.gz"


@dataclass(frozen=True)
class BuildInputs:
    """What one Python build of a scenario reads, as `build.build_m1`'s arguments: the audit, the ledger, the subset tables and their pack, the after font, the spec root, and whether it recomputes every unit."""

    audit: Path
    ledger: Path
    subset_dir: Path
    subset_pack: Path
    after_font: Path
    spec_root: Path
    recompute_all: bool = False

    def request(self) -> dict:
        return {
            item.name: str(value) if isinstance(value, Path) else value
            for item in dataclasses.fields(self)
            for value in (getattr(self, item.name),)
        }

    @classmethod
    def from_request(cls, request: Mapping[str, object]) -> BuildInputs:
        return cls(
            Path(str(request["audit"])),
            Path(str(request["ledger"])),
            Path(str(request["subset_dir"])),
            Path(str(request["subset_pack"])),
            Path(str(request["after_font"])),
            Path(str(request["spec_root"])),
            bool(request["recompute_all"]),
        )


@dataclass
class BuildRun:
    """One Python build as `run_python_build` ran it: its label, the tree it started from, the tree it wrote, its exit status, what it printed, and the cycle directory of a pass whose claim another output holds."""

    label: str
    prior: Path
    out: Path
    returncode: int
    log: str
    cycle_var: Path | None = None

    @functools.cached_property
    def notes(self) -> dict[str, str]:
        """Each `[t] review.build <phase>` line's note, by phase."""
        notes: dict[str, str] = {}
        for line in self.log.splitlines():
            if line.startswith("[t] review.build "):
                head, _, note = line.partition("\t")
                notes[head.split()[2]] = note
        return notes

    def note(self, phase: str) -> str:
        return self.notes.get(phase, "")

    def warns(self) -> list[str]:
        return [line[len("[warn] ") :] for line in self.log.splitlines() if line.startswith("[warn] ")]

    def says(self, text: str) -> bool:
        return any(line == text for line in self.log.splitlines())

    def counts(self, phase: str, pattern: str) -> tuple[int, ...] | None:
        """The integers `pattern`'s groups match in the phase's note, commas dropped, or None when it does not match."""
        found = re.search(pattern, self.note(phase))
        return None if found is None else tuple(int(group.replace(",", "")) for group in found.groups())

    def message(self) -> str:
        """The last line the build printed, which is a refusal's text when it stopped with one."""
        lines = self.log.rstrip("\n").splitlines()
        return lines[-1] if lines else ""


# A check sees one case's run and returns the problems it finds: each a sentence naming what the build did not reach.
BuildCheck = Callable[[BuildRun], list[str]]


@dataclass(frozen=True)
class BuildCase:
    """One build of a scenario: its name, what it reads, the prior corpus it builds over (a clone of `prior`, or, when `prior` is None, of the output of the case before it), its units width (None for the harness's default, `default_width`), the output that holds its pass's fold claim when the pass runs as a cycle's child whose claim another output took (`row_delta.claim_fold`), and the check that the Python build reached what the scenario's row names."""

    name: str
    inputs: BuildInputs
    prior: Path | None
    check: BuildCheck
    jobs: int | None = None
    claim_held_by: str | None = None


@dataclass(frozen=True)
class BuildScenario:
    """A prepared scenario: its source and name, the directory that holds its priors and its changed inputs, and its builds in order."""

    source: str
    name: str
    directory: Path
    cases: tuple[BuildCase, ...]


@dataclass(frozen=True)
class BasePrior:
    """A source's base prior: the corpus every scenario starts from and the inputs its build read, which every scenario's build reads except where its row changes them."""

    source: str
    out: Path
    inputs: BuildInputs


def default_width() -> int:
    """The harness's default units width: what a `--skip-gates` cycle passes the build."""
    from rebuild.tools.artifact_cycle import corpus_job_budget

    return corpus_job_budget(skip_gates=True)


def signature_width(jobs: int | None) -> int:
    """The signature shaping's width for a build at `jobs` units jobs (the harness's default when None): one for a build at one job, and a `--skip-gates` cycle's otherwise."""
    if jobs == 1:
        return 1
    from rebuild.tools.artifact_cycle import signature_job_budget

    return signature_job_budget()


def _clone_file(source: Path, target: Path) -> None:
    """Copy one file with its modification time, as a copy-on-write clone where the volume can make one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        result = subprocess.run(
            ["cp", "-c", "-p", str(source), str(target)], capture_output=True, check=False
        )
        if result.returncode == 0:
            return
    shutil.copy2(source, target)


def _clone_tree(source: Path, target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    landing.clone_tree(source, target)


def _pin_time(path: Path, like: Path) -> None:
    """Give `path` the modification time of `like`, which `build._generated_at` reads, so a scenario's stamp does not move with the run that wrote the file."""
    os.utime(path, ns=(path.stat().st_atime_ns, like.stat().st_mtime_ns))


def _build_environment(counts: Path, run: str | None) -> dict[str, str]:
    """The environment a scenario's build child runs in, as the cycle's step runs the build: the Rust units engine, no debug tally, the counts file named, no cycle run unless the case's pass holds one, and on macOS the allocator's large cache off."""
    environment = dict(os.environ)
    environment[units_exec.ENGINE_VARIABLE] = "rust"
    environment.pop(memory_tally.TALLY_ENV, None)
    environment.pop(row_delta.CYCLE_RUN_ENV, None)
    environment[output_sink.OUTPUT_COUNTS_ENV] = str(counts)
    if run is not None:
        environment[row_delta.CYCLE_RUN_ENV] = run
    if sys.platform == "darwin":
        environment.update(KERNEL_ALLOCATOR_ENVIRONMENT)
    return environment


def run_python_build(
    inputs: BuildInputs,
    out: Path,
    logs: Path,
    label: str,
    *,
    prior: Path | None = None,
    jobs: int | None = None,
    claim_held_by: str | None = None,
) -> BuildRun:
    """Run one Python build of `inputs` into `out` in a fresh process (`python_build_child`), as the cycle's step runs it, and return the run. `out` is first made a clone of `prior` when one is given, and otherwise built in place. `logs` holds the request, the log of standard error, the counts file, the journal the build writes its records to, and the cycle directory of a pass whose claim `claim_held_by` holds. `jobs` is the units width, the harness's default when None, and a build at one job shapes its signatures at one job too."""
    logs.mkdir(parents=True, exist_ok=True)
    if prior is not None:
        _clone_tree(prior, out)
    width = default_width() if jobs is None else jobs
    signature_jobs = signature_width(jobs)
    counts = logs / f"{label}.counts"
    counts.write_bytes(b"")
    run = None
    cycle_var = None
    if claim_held_by is not None:
        run = f"build-scenarios-{label}"
        cycle_var = logs / f"{label}-cycle"
        cycle_var.mkdir(parents=True, exist_ok=True)
        (cycle_var / cycle_paths.FOLD_CLAIM_NAME).write_text(
            json.dumps({"run": run, "output": claim_held_by}) + "\n", encoding="utf-8"
        )
    request = {
        "format": BUILD_REQUEST_FORMAT,
        "out": str(out),
        "inputs": inputs.request(),
        "jobs": width,
        "signature_jobs": signature_jobs,
        "journal": str(logs / "journal.ndjson"),
        "cycle_var": None if cycle_var is None else str(cycle_var),
    }
    request_path = logs / f"{label}.json"
    request_path.write_text(json.dumps(request, indent=1) + "\n", encoding="utf-8")
    log_path = logs / f"{label}.log"
    with log_path.open("wb") as log:
        process = subprocess.run(
            [sys.executable, "-m", "rebuild.tools.units_stage_fixtures", "python-build", str(request_path)],
            cwd=REPO_ROOT,
            env=_build_environment(counts, run),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return BuildRun(
        label,
        prior if prior is not None else out,
        out,
        process.returncode,
        log_path.read_text(encoding="utf-8", errors="replace"),
        cycle_var,
    )


def python_build_child(request_path: Path) -> int:
    """Run the build one `run_python_build` request describes, in this process: the journal and, for a pass whose claim another output holds, the cycle directory redirected as the request names them."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("format") != BUILD_REQUEST_FORMAT:
        raise SystemExit(f"{request_path} is not a {BUILD_REQUEST_FORMAT} request")
    cycle_timings.JOURNAL = Path(request["journal"])
    if request["cycle_var"] is not None:
        cycle_paths.CYCLE_VAR = Path(request["cycle_var"])
    inputs = BuildInputs.from_request(request["inputs"])
    build.build_m1(
        Path(request["out"]),
        audit_path=inputs.audit,
        ledger_path=inputs.ledger,
        subset_dir=inputs.subset_dir,
        after_font=inputs.after_font,
        jobs=request["jobs"],
        signature_jobs=request["signature_jobs"],
        recompute_all_units=inputs.recompute_all,
        spec_root=inputs.spec_root,
        subset_pack=inputs.subset_pack,
    )
    return 0


_BASES: dict[str, BasePrior] = {}


def base_prior(source: str) -> BasePrior:
    """The source's base prior, prepared once a process (`_mini_base`, `_live_base`)."""
    if source not in _BASES:
        _BASES[source] = _mini_base() if source == "mini" else _live_base()
    return _BASES[source]


def _required_run(run: BuildRun, what: str) -> BuildRun:
    if run.returncode != 0:
        raise SystemExit(f"{what} failed with status {run.returncode}: {run.message()}")
    return run


def _mini_base() -> BasePrior:
    """Materialize the mini bundle's spec at the fixed `MINI_SPEC`, pack its subset tables there and pin its ledger's modification time to the bundle audit's, so every build of a run reads one spec and stamps one `generated_at`; then build the bundle from an empty directory into `MINI_BASE`, the `first` build."""
    shutil.rmtree(MINI_SPEC, ignore_errors=True)
    spec_root = pin.materialize(MINI_SPEC)
    subset_pack = spec_root / PACK_NAME
    write_pack_in_memory(MINI, ACCEPTANCE_CONFIGS, table_digests(MINI, ACCEPTANCE_CONFIGS), subset_pack)
    audit_path = MINI / "audit.tsv"
    ledger = spec_root / pin.LEDGER_PATH
    _pin_time(ledger, audit_path)
    inputs = BuildInputs(audit_path, ledger, MINI, subset_pack, MINI / "M1.otf", spec_root)
    shutil.rmtree(MINI_BASE, ignore_errors=True)
    empty = MINI_BASE / "empty"
    empty.mkdir(parents=True)
    out = MINI_BASE / "out"
    _required_run(
        run_python_build(inputs, out, MINI_BASE / "logs", "base", prior=empty), "the mini base prior's build"
    )
    return BasePrior("mini", out, inputs)


def _audit_names(audit_path: Path) -> list[str]:
    """The files that hold an audit, beside one another: its head and every part the head lists, or the single file a tree with no head holds."""
    head = row_delta.read_head(audit_path.parent, audit_path.name)
    if head is None:
        return [audit_path.name]
    return [row_delta.head_name(audit_path.name), *(part.name for part in head.parts)]


def _clone_live_inputs(target: Path) -> None:
    """Clone what the build reads under `rebuild/out/m1/` and the ledger into `target`, modification times kept: the audit's head and every part it lists (or its single file), the after font, the subset tables and their pack, and the Stage A record the manifest copies."""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    m1 = build.M1_SUBSETS
    names = _audit_names(build.M1_AUDIT)
    names += [build.M1_AFTER_FONT.name, PACK_NAME, fingerprint.STAGE_A_FILENAME]
    names += [f"baseline-{config}.subset.tsv.gz" for config in ACCEPTANCE_CONFIGS]
    for name in names:
        if (m1 / name).is_file():
            _clone_file(m1 / name, target / name)
    _clone_file(build.M1_LEDGER, target / build.M1_LEDGER.name)


def _live_base() -> BasePrior:
    """Refresh the live base prior under `LIVE_BASE`: clone `rebuild/out/review` into its `out/` the first time (`landing.clone_tree`), clone the build's inputs into its `inputs/` again (`_clone_live_inputs`), and run one Python build at the current tree into `out/`, so every live scenario starts from the engine of record's output for the tree as it stands."""
    for needed in (build.DEFAULT_OUT / "manifest.json", build.M1_AFTER_FONT):
        if not needed.is_file():
            raise SystemExit(f"the live source needs the last cycle's {needed.relative_to(REPO_ROOT)}")
    out = LIVE_BASE / "out"
    if not out.is_dir():
        _clone_tree(build.DEFAULT_OUT, out)
    inputs_dir = LIVE_BASE / "inputs"
    _clone_live_inputs(inputs_dir)
    inputs = BuildInputs(
        inputs_dir / build.M1_AUDIT.name,
        inputs_dir / build.M1_LEDGER.name,
        inputs_dir,
        inputs_dir / PACK_NAME,
        inputs_dir / build.M1_AFTER_FONT.name,
        REPO_ROOT,
    )
    _required_run(
        run_python_build(inputs, out, LIVE_BASE / "logs", "refresh"), "the live base prior's refresh"
    )
    return BasePrior("live", out, inputs)


# --- changed inputs and priors ------------------------------------------------------------------


def _audit_rows(path: Path) -> tuple[str, list[list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[0], [line.split("\t") for line in lines[1:]]


def _write_audit(path: Path, header: str, rows: Iterable[Sequence[str]], like: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *("\t".join(row) for row in rows)]) + "\n", encoding="utf-8")
    _pin_time(path, like)
    return path


def _windows(rows: Sequence[Sequence[str]]) -> dict[str, list[Sequence[str]]]:
    windows: dict[str, list[Sequence[str]]] = {}
    for row in rows:
        windows.setdefault(row[_WINDOW], []).append(row)
    return windows


def _units_of(rows: Sequence[Sequence[str]]) -> list[list[Sequence[str]]]:
    """A window's rows grouped into its units, by (baseline, new) in the order the file first names them."""
    units: dict[tuple[str, str], list[Sequence[str]]] = {}
    for row in rows:
        units.setdefault((row[_BASELINE], row[_NEW]), []).append(row)
    return list(units.values())


def edited_rows(rows: Sequence[Sequence[str]]) -> list[Sequence[str]]:
    """The audit rows with some windows' rows changed and some windows removed. It drops the fourth window and the second window that holds two units (a sibling window, whose ink-signature entries go unhit); retags every row of the first two windows from the fifth on that are not in `RETAG_CLASS` into it, as `rebuild/test_unit_cache.py`'s `_edited_audit` does, which changes their input keys and moves them to that class's overflow part; and, in the first sibling window whose second unit holds two rows or more, moves the last row of that unit into its first, its matched entry and both rendered-name fields taken from the first unit's first row. That row's signature key is one the prior store does not hold, so the key-only read misses it, and the unit it joins keeps its first configuration, the one the enricher re-settles."""
    windows = _windows(rows)
    order = list(windows)
    siblings = [window for window in order if len(_units_of(windows[window])) > 1]
    moved_window = next(
        window for window in siblings if len(units := _units_of(windows[window])) > 1 and len(units[1]) > 1
    )
    dropped = {order[3], next(window for window in siblings if window != moved_window)}
    retagged = [
        window
        for window in order[4:]
        if window not in dropped
        and window != moved_window
        and all(row[_ENTRY] != RETAG_CLASS for row in windows[window])
    ][:2]
    first_unit, second_unit = _units_of(windows[moved_window])[:2]
    moved = second_unit[-1]
    edited: list[Sequence[str]] = []
    for row in rows:
        if row[_WINDOW] in dropped:
            continue
        if row[_WINDOW] in retagged:
            row = [*row[:_ENTRY], RETAG_CLASS, *row[_ENTRY + 1 :]]
        elif row is moved:
            row = [*row[:_ENTRY], first_unit[0][_ENTRY], first_unit[0][_BASELINE], first_unit[0][_NEW]]
        edited.append(row)
    return edited


def edited_audit(audit_path: Path, target: Path) -> Path:
    """Write the mini audit as `edited_rows` changes it, its modification time the bundle audit's, and return it."""
    header, rows = _audit_rows(audit_path)
    return _write_audit(target, header, edited_rows(rows), audit_path)


def diverging_audit(audit_path: Path, target: Path) -> Path:
    """Write the mini audit with one unit's cells changed so the enricher's re-settlement diverges from them, which fails the build's contract check: in the first window that holds two or more units, the first of which holds only cell tokens in `new`, that unit's rows each take, in place of their first token, the first token of the first such row that names another family. The rows' signature keys move with them, so the key-only read misses them and the build rewrites the signature store."""
    header, rows = _audit_rows(audit_path)

    def cells(row: Sequence[str]) -> bool:
        return all("/" in token for token in row[_NEW].split("|"))

    unit = next(
        units[0]
        for held in _windows(rows).values()
        if len(units := _units_of(held)) > 1 and cells(units[0][0])
    )
    first = unit[0][_NEW].split("|")[0]
    other = next(
        token
        for row in rows
        if cells(row) and (token := row[_NEW].split("|")[0]).split("/")[0] != first.split("/")[0]
    )
    changed = {id(row) for row in unit}
    edited = [
        [*row[:_NEW], "|".join([other, *row[_NEW].split("|")[1:]])] if id(row) in changed else row
        for row in rows
    ]
    return _write_audit(target, header, edited, audit_path)


# How many of the live audit's first windows, in its first configuration's order, the scratch segment's edits choose among.
SCRATCH_WINDOWS = 4096


def scratch_audit_segment(audit_path: Path, target_dir: Path) -> Path:
    """Clone a live audit's parts into `target_dir` beside a scratch head that lists them and one more segment, which this writes as the audit's writer writes one (`oracle.AuditWriter`: plain-keyed upserts and tombstones in the audit's order, a configuration a gzip member, `row_delta.MemberPart`), its `content` one more, and return the audit path the build reads. The segment makes `edited_rows`' edits to the audit's first `SCRATCH_WINDOWS` windows, every configuration's rows of them, so every row it holds is settled under the current spec."""
    head = row_delta.read_head(audit_path.parent, audit_path.name)
    if head is None:
        raise SystemExit(f"{audit_path} has no head to add a segment to")
    target_dir.mkdir(parents=True, exist_ok=True)
    for part in head.parts:
        _clone_file(audit_path.parent / part.name, target_dir / part.name)
    chosen: dict[str, None] = {}
    first: str | None = None
    rows: list[Sequence[str]] = []
    pending = b""
    for chunk in oracle.materialized_audit(audit_path.parent):
        lines = (pending + chunk).split(b"\n")
        pending = lines.pop()
        for line in lines:
            fields = line.decode("utf-8").split("\t")
            if fields[0] == "config":
                continue
            if first is None:
                first = fields[0]
            if fields[0] == first and len(chosen) < SCRATCH_WINDOWS:
                chosen.setdefault(fields[_WINDOW])
            if fields[_WINDOW] in chosen:
                rows.append(fields)
    before = {(row[0], row[_WINDOW]): row for row in rows}
    after = {(row[0], row[_WINDOW]): row for row in edited_rows(rows)}
    changes = sorted(
        [
            *(b"+\t" + "\t".join(row).encode() + b"\n" for key, row in after.items() if before[key] != row),
            *(b"-\t" + "\t".join(key).encode() + b"\n" for key in before if key not in after),
        ],
        key=lambda line: oracle.audit_key(line[2:]),
    )
    number = len(head.segments) + 1
    writer = row_delta.MemberPart(
        target_dir,
        row_delta.segment_name(audit_path.name, head.generation, number),
        row_delta.segment_header(audit_path.name, head.generation, number, row_delta.MERGE_PLAIN_KEY),
        field=1,
    )
    for line in changes:
        writer.write(line)
    part = writer.close()
    assert part is not None
    scratch = row_delta.with_segment(head, part, head.stamps)
    (target_dir / row_delta.head_name(audit_path.name)).write_text(
        json.dumps(scratch.document(), indent=1) + "\n", encoding="utf-8"
    )
    return target_dir / audit_path.name


def _manifest_classes(corpus: Path) -> list[dict]:
    return json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))["classes"]


def _class_units(corpus: Path, class_id: str, limit: int | None = None) -> list[dict]:
    """The units index's records of one class in the prior corpus, in index order, read until the class's run of records ends or `limit` are held."""
    opened = unit_index.open_index(corpus)
    if opened is None:
        raise SystemExit(f"{corpus} has no units index")
    marker = json.dumps({"class": class_id})[1:-1].encode()
    found: list[dict] = []
    with opened:
        for line in opened.lines():
            if marker in line:
                record = json.loads(line)
                if record["class"] == class_id:
                    found.append(record)
                    if len(found) == limit:
                        break
                    continue
            if found:
                break
    return found


def _exemplar_key(record: Mapping) -> tuple[str, str]:
    return record["configs"][0], record["codepoints"]


def _ledger_entries(ledger: Path) -> list[dict]:
    return yaml.safe_load(ledger.read_text(encoding="utf-8")) or []


def _write_ledger(path: Path, entries: Sequence[Mapping], like: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(list(entries), sort_keys=False, allow_unicode=True, width=1_000_000), encoding="utf-8"
    )
    _pin_time(path, like)
    return path


@dataclass(frozen=True)
class LedgerPlan:
    """The ledger classes a scenario edits, chosen from the base prior: `small`, the smallest ledger class present, whose `no_verdict` an edit flips, and `large`, the largest ledger class present that takes full fragments, with its units' exemplar keys in index order (the first `limit` of them, when `ledger_plan` is given one), whose exemplar flags an edit moves."""

    small: str
    large: str
    large_keys: tuple[tuple[str, str], ...]


def ledger_plan(corpus: Path, ledger: Path, limit: int | None = None) -> LedgerPlan:
    ids = {entry["id"] for entry in _ledger_entries(ledger)}
    present = [meta for meta in _manifest_classes(corpus) if meta["id"] in ids]
    small = min(present, key=lambda meta: meta["unit_count"])
    large = max((meta for meta in present if not meta["no_verdict"]), key=lambda meta: meta["unit_count"])
    return LedgerPlan(
        small["id"],
        large["id"],
        tuple(_exemplar_key(record) for record in _class_units(corpus, large["id"], limit)),
    )


def _flip_no_verdict(entry: dict) -> None:
    entry["no_verdict"] = not entry.get("no_verdict", False)


def _with_exemplars(entry: dict, keys: Iterable[tuple[str, str]]) -> None:
    entry["exemplars"] = [{"config": config, "codepoints": codepoints} for config, codepoints in keys]


def incremental_ledger(base: BasePrior, plan: LedgerPlan, target: Path) -> Path:
    """Write the base ledger with the small class's units turned slim or full (its `no_verdict` flipped) and the large class's first exemplar moved to its first unit no exemplar names, its modification time the base ledger's, and return it."""
    entries = _ledger_entries(base.inputs.ledger)
    taken = {(item["config"], item["codepoints"]) for entry in entries for item in entry.get("exemplars", ())}
    for entry in entries:
        if entry["id"] == plan.small:
            _flip_no_verdict(entry)
        if entry["id"] == plan.large:
            fresh = next(key for key in plan.large_keys if key not in taken)
            kept = [(item["config"], item["codepoints"]) for item in entry.get("exemplars", ())][1:]
            _with_exemplars(entry, [fresh, *kept])
    return _write_ledger(target, entries, base.inputs.ledger)


def _moved_line(lines: Sequence[str], label: str) -> list[str]:
    """`lines` with the digest of the line labeled `label` replaced by another, as a move of that input gives it."""
    moved = []
    found = False
    for line in lines:
        name, _, value = line.partition("\t")
        if name == label:
            value = hashlib.sha256(f"moved {value}".encode()).hexdigest()
            found = True
        moved.append(f"{name}\t{value}")
    if not found:
        raise SystemExit(f"the stamp has no {label} line to move: {list(lines)}")
    return moved


def move_store_stamp(corpus: Path, label: str) -> None:
    """Move the `label` line of the unit store's head stamps, and the `environment` digest over the lines with it."""
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    if head is None:
        raise SystemExit(f"{corpus} has no unit store head")
    lines = _moved_line(head.stamps["environment_lines"], label)
    stamps = {
        **head.stamps,
        "environment": fingerprint.EnvironmentStamp(tuple(lines)).value,
        "environment_lines": lines,
    }
    row_delta.commit(corpus, dataclasses.replace(head, stamps=stamps))


def move_signature_stamp(corpus: Path, label: str) -> None:
    """Move the `label` line of the ink-signature store's header, and the `environment` digest over the lines with it, keeping its entries."""
    path = unit_cache.signature_store_path(corpus)
    header_line, _, entries = gzip.decompress(path.read_bytes()).partition(b"\n")
    header = json.loads(header_line)
    lines = _moved_line(header["environment_lines"], label)
    header["environment"] = fingerprint.EnvironmentStamp(tuple(lines)).value
    header["environment_lines"] = lines
    with gzip.GzipFile(path, "wb", compresslevel=1, mtime=0) as stream:
        stream.write(json.dumps(header).encode() + b"\n" + entries)


def relocate_parts(corpus: Path) -> list[str]:
    """Rewrite every shard part of the corpus as compact JSON, so every part's size disagrees with the one the unit store recorded while each fragment stays readable by the walk, and return the parts."""
    recorded = (unit_cache.store_stamps(corpus) or {}).get("parts") or {}
    parts = [part for meta in _manifest_classes(corpus) for part in unit_index.class_shards(meta)]
    for part in parts:
        path = corpus / part
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
        if path.stat().st_size == recorded.get(part):
            raise SystemExit(f"{part} kept the size the store recorded for it once rewritten")
    return parts


def _store_files(corpus: Path) -> list[str]:
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    if head is None:
        raise SystemExit(f"{corpus} has no unit store head")
    return [row_delta.head_name(unit_cache.STORE_NAME), *(part.name for part in head.parts)]


def tear(corpus: Path, base: Path) -> None:
    """Make `corpus`, an incremental build's output, what a pass stopped after its facts line leaves beside a later build's: the base prior's unit store (head and parts) and `review-facts.json` put back, with a leftover staged overflow part of the first class that has one (or the first class) and a leftover staged ink-signature store, each a cut copy of what it stands for."""
    for name in _store_files(corpus):
        (corpus / name).unlink()
    for name in [*_store_files(base), "review-facts.json"]:
        _clone_file(base / name, corpus / name)
    classes = _manifest_classes(corpus)
    torn = next(
        (meta for meta in classes if any(".x" in part for part in unit_index.class_shards(meta))), classes[0]
    )
    first = corpus / unit_index.class_shards(torn)[0]
    staged = corpus / f"{unit_index.overflow_part_name(torn['id'], 0)}{output_sink.STAGING_SUFFIX}"
    staged.write_bytes(first.read_bytes()[: max(1, first.stat().st_size // 2)])
    signatures = unit_cache.signature_store_path(corpus)
    data = signatures.read_bytes()
    signatures.with_name(signatures.name + output_sink.STAGING_SUFFIX).write_bytes(data[: len(data) // 2])


def sampled_record(corpus: Path) -> tuple[int, dict]:
    """The base row and record of the first unit, by id, of the verification sample a build over unchanged inputs draws from the corpus's unit store: the store's id words in ascending order, seeded with its environment stamp (`build._verification_sample`)."""
    header, lines = _store_held(corpus)
    records = [json.loads(line) for line in lines]
    words = sorted(word for record in records if (word := unit_cache.id_word(record["id"])) is not None)
    first = min(build._verification_sample(words, header["environment"]))
    index = next(index for index, record in enumerate(records) if unit_cache.id_word(record["id"]) == first)
    return index, records[index]


def _store_held(corpus: Path) -> tuple[dict, list[bytes]]:
    materialized = unit_cache.materialized_store(corpus)
    if materialized is None:
        raise SystemExit(f"{corpus} has no unit store")
    header, lines = materialized
    return header, list(lines)


def stale_sample_store(corpus: Path) -> str:
    """Change one sampled record's ink deltas by a store segment the tool writes, which leaves the record's content key, so the plan reuses it and the verification sample's recomputation disagrees with it; return its id."""
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    if head is None or head.segments:
        raise SystemExit(
            f"{corpus}'s unit store is not a base alone, so its rows' places are not their lines"
        )
    index, record = sampled_record(corpus)
    record["ink_deltas"] = {**record["ink_deltas"], "bogus": "d-000000000000"}
    line = (json.dumps(record) + "\n").encode()
    part = row_delta.write_segment(
        corpus,
        unit_cache.STORE_NAME,
        head.generation,
        1,
        [row_delta.Row(row_delta.UPSERT, (index, ""), line)],
    )
    row_delta.commit(corpus, row_delta.with_segment(head, part, head.stamps))
    return record["id"]


def corrupt_copied_fragment(corpus: Path) -> str:
    """Change one byte of the id of the first fragment of the first class's first part, keeping the part's size, so the plan trusts its address and the write's byte copy refuses it; return the id."""
    meta = _manifest_classes(corpus)[0]
    path = corpus / unit_index.class_shards(meta)[0]
    raw = path.read_bytes()
    unit_id = json.loads(raw)[0]["id"]
    quoted = f'"id": "{unit_id}"'.encode()
    corrupted = quoted[:-2] + (b"1" if quoted[-2:-1] != b"1" else b"2") + b'"'
    if raw.count(quoted) != 1:
        raise SystemExit(f"{path} names {unit_id} other than once")
    path.write_bytes(raw.replace(quoted, corrupted))
    return unit_id


# --- the scenarios ------------------------------------------------------------------------------


def _scenario_dir(source: str, scenario: str) -> Path:
    return SCENARIO_ROOT / f"{source}-{scenario}"


def _cleared(directory: Path) -> Path:
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    return directory


def open_build_source(source: str, scenario: str) -> BuildScenario:
    """Prepare one scenario of § Scenarios over the source's base prior (`base_prior`): its directory under `SCENARIO_ROOT` cleared, its priors cloned there and changed as its row says, and its changed inputs written beside them; every input its row leaves unchanged is the base prior's own, so a build over unchanged inputs stamps the prior's manifest. Nothing under `rebuild/out/` is written."""
    if source not in SOURCES:
        raise SystemExit(f"unknown source {source!r}; the sources are {', '.join(SOURCES)}")
    if scenario not in BUILD_SCENARIOS:
        raise SystemExit(f"unknown scenario {scenario!r}; the scenarios are {', '.join(BUILD_SCENARIOS)}")
    if source not in BUILD_SCENARIOS[scenario]:
        raise SystemExit(
            f"the {scenario} scenario runs on {', '.join(BUILD_SCENARIOS[scenario])}, not {source}"
        )
    base = base_prior(source)
    directory = _cleared(_scenario_dir(source, scenario))
    prepare = _PREPARERS[scenario]
    return BuildScenario(source, scenario, directory, tuple(prepare(base, directory)))


def _prior(base: BasePrior, directory: Path, name: str = "prior") -> Path:
    target = directory / name
    _clone_tree(base.out, target)
    return target


def _first(base: BasePrior, directory: Path) -> list[BuildCase]:
    empty = directory / "prior"
    empty.mkdir()
    return [BuildCase("first", base.inputs, empty, _check_first)]


def _cold(base: BasePrior, directory: Path) -> list[BuildCase]:
    inputs = dataclasses.replace(base.inputs, recompute_all=True)
    return [BuildCase("cold", inputs, _prior(base, directory), _check_cold)]


def _warm(base: BasePrior, directory: Path) -> list[BuildCase]:
    return [BuildCase("warm", base.inputs, _prior(base, directory), _check_warm)]


def _incremental_inputs(base: BasePrior, directory: Path) -> BuildInputs:
    inputs_dir = directory / "inputs"
    exemplars = sum(len(entry.get("exemplars", ())) for entry in _ledger_entries(base.inputs.ledger))
    plan = ledger_plan(base.out, base.inputs.ledger, exemplars + 1)
    ledger = incremental_ledger(base, plan, inputs_dir / base.inputs.ledger.name)
    if base.source == "mini":
        audit_path = edited_audit(base.inputs.audit, inputs_dir / base.inputs.audit.name)
    else:
        audit_path = scratch_audit_segment(base.inputs.audit, inputs_dir)
    return dataclasses.replace(base.inputs, audit=audit_path, ledger=ledger)


def _incremental(base: BasePrior, directory: Path) -> list[BuildCase]:
    inputs = _incremental_inputs(base, directory)
    return [BuildCase("incremental", inputs, _prior(base, directory), _check_incremental)]


def _rebase(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    for output in (unit_cache.STORE_NAME, unit_index.INDEX_NAME):
        row_delta.head_path(prior, output).unlink()
    return [BuildCase("rebase", base.inputs, prior, _check_rebase)]


def _restamped(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    move_store_stamp(prior, "units_engine")
    move_signature_stamp(prior, "comparator_code")
    return [BuildCase("restamped", base.inputs, prior, _check_restamped)]


def _rederived(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    move_store_stamp(prior, "spec_structure")
    return [BuildCase("rederived", base.inputs, prior, _check_rederived)]


def _relocate(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    relocate_parts(prior)
    return [BuildCase("relocate", base.inputs, prior, _check_relocate)]


def _torn(base: BasePrior, directory: Path) -> list[BuildCase]:
    inputs = _incremental_inputs(base, directory)
    prior = directory / "prior"
    _required_run(
        run_python_build(inputs, prior, directory / "logs", "incremental", prior=base.out),
        "the torn scenario's incremental build",
    )
    tear(prior, base.out)
    return [BuildCase("torn", inputs, prior, _check_torn)]


def _refusals(base: BasePrior, directory: Path) -> list[BuildCase]:
    """One case a refusal point past the plan, each over its own clone of the base prior. The two that refuse a unit reused from the store run at the harness's default width: a build at one job stamps no `units_engine` line, so it reuses nothing from a store the Rust units engine stamped, and over unchanged inputs neither writes the signature store. The contract check's case rewrites the signature store, since its audit's identity moves, so it runs at one job, where the store is written inline (§ Failures)."""
    stale = _prior(base, directory, "prior-stale-sample")
    stale_id = stale_sample_store(stale)
    unreadable = _prior(base, directory, "prior-unreadable-fragment")
    corrupted_id = corrupt_copied_fragment(unreadable)
    diverging = dataclasses.replace(
        base.inputs, audit=diverging_audit(base.inputs.audit, directory / "inputs" / base.inputs.audit.name)
    )
    return [
        BuildCase("stale-sample", base.inputs, stale, _refused(_STALE_SAMPLE, stale_id)),
        BuildCase(
            "unreadable-fragment", base.inputs, unreadable, _refused(_UNREADABLE_FRAGMENT, corrupted_id)
        ),
        BuildCase(
            "contract-check",
            diverging,
            _prior(base, directory, "prior-contract-check"),
            _refused(
                _CONTRACT_CHECK, "enricher: re-settled cells diverge from the audit in 1 units", written=True
            ),
            jobs=1,
        ),
    ]


@dataclass(frozen=True)
class ChainStep:
    """One step of `CHAIN`: how many builds it runs, the ledger state each reads (`_chain_ledger`), whether its pass's fold claim is held by another output (`HELD_CLAIM_OUTPUT`), and what the store and the units index must write."""

    builds: int
    state: str
    held: bool
    store: str
    index: str


# How many units of the large class one chain build moves its exemplar flags onto, and as many off. Small enough that a step's records, with one fold's address moves, keep sixteen store segments below the store's base, and large enough that the re-patched fragments' dead bytes fold the class within the chain.
CHAIN_BLOCK = 8
# The chain's steps (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Scenarios, `chain`), each over the build before's output. Ledger states: `alternate` flips the small class's `no_verdict` back and forth and moves the large class's exemplar block to its next `CHAIN_BLOCK` units on every build, changing records of both the store and the index and appending re-patched fragments to the large class until its dead bytes fold it; `exemplars` moves only the exemplar block, which no index line carries; `over-half` puts exemplars on three quarters of the large class, which the share rule writes directly; `most` flips `no_verdict` on every ledger class present, which changes more than half of the store's records and nearly every index line. The store and the index outcomes are `segment`, `head`, or a base's reason. The first builds take both outputs to their cap; the next folds the index, which claims the pass's fold first, while the store writes a segment past its cap; the store then folds on a build that changes no index line; both then take segments under a claim another output holds until `row_delta.fold_forced` folds them; and the last builds write a class directly, write the store's new base by the share rule twice, as most classes flip and flip back, and fold the index once its two segments pass its base.
CHAIN = (
    ChainStep(row_delta.SEGMENT_CAP, "alternate", False, "segment", "segment"),
    ChainStep(1, "alternate", False, "segment", "fold"),
    ChainStep(1, "exemplars", False, "fold", "head"),
    ChainStep(row_delta.SEGMENT_CAP + row_delta.FOLD_OVERRUN, "alternate", True, "segment", "segment"),
    ChainStep(1, "alternate", True, "fold", "fold"),
    ChainStep(1, "over-half", False, "segment", "head"),
    ChainStep(1, "most", False, "share", "segment"),
    ChainStep(1, "alternate", False, "share", "segment"),
    ChainStep(1, "alternate", False, "segment", "fold"),
)


def _chain_ledger(
    base: BasePrior, plan: LedgerPlan, flipped: bool, exemplars: Sequence[tuple[str, str]], most: bool
) -> list[dict]:
    """The base ledger with the small class's `no_verdict` flipped when `flipped`, every present ledger class's flipped when `most`, and `exemplars` added to the large class's own."""
    entries = _ledger_entries(base.inputs.ledger)
    present = {meta["id"] for meta in _manifest_classes(base.out)}
    taken = {(item["config"], item["codepoints"]) for entry in entries for item in entry.get("exemplars", ())}
    for entry in entries:
        if (entry["id"] == plan.small and flipped) or (most and entry["id"] in present):
            _flip_no_verdict(entry)
        if entry["id"] == plan.large:
            own = [(item["config"], item["codepoints"]) for item in entry.get("exemplars", ())]
            _with_exemplars(entry, [*own, *(key for key in exemplars if key not in taken)])
    return entries


def _chain(base: BasePrior, directory: Path) -> list[BuildCase]:
    plan = ledger_plan(base.out, base.inputs.ledger)
    keys = plan.large_keys
    over_half = keys[: 3 * len(keys) // 4]
    cases: list[BuildCase] = []
    flipped = False
    moves = 0
    prior: Path | None = _prior(base, directory)
    for number, step in enumerate(CHAIN):
        for _ in range(step.builds):
            if step.state in ("alternate", "exemplars"):
                flipped = flipped != (step.state == "alternate")
                moves += 1
                block = [keys[(moves * CHAIN_BLOCK + offset) % len(keys)] for offset in range(CHAIN_BLOCK)]
                entries = _chain_ledger(base, plan, flipped, block, False)
            else:
                entries = _chain_ledger(base, plan, flipped, over_half, step.state == "most")
            label = f"{len(cases) + 1:02d}-{step.state}"
            ledger = _write_ledger(directory / "inputs" / f"{label}.yaml", entries, base.inputs.ledger)
            cases.append(
                BuildCase(
                    label,
                    dataclasses.replace(base.inputs, ledger=ledger),
                    prior,
                    _chain_check(step, number),
                    claim_held_by=HELD_CLAIM_OUTPUT if step.held else None,
                )
            )
            prior = None
    return cases


_PREPARERS: dict[str, Callable[[BasePrior, Path], list[BuildCase]]] = {
    "first": _first,
    "cold": _cold,
    "warm": _warm,
    "incremental": _incremental,
    "rebase": _rebase,
    "restamped": _restamped,
    "rederived": _rederived,
    "relocate": _relocate,
    "torn": _torn,
    "chain": _chain,
    "refusals": _refusals,
}


# --- the scenario checks ------------------------------------------------------------------------

_REUSED = r"reused (\d[\d,]*) of (\d[\d,]*) units from the cache"
_KEPT_RECORDS = r"kept (\d[\d,]*) prior records by id"
_RECOMPUTED = r"recomputed=(\d[\d,]*), verified=(\d[\d,]*) cached"
_SIGNATURES = r"\(signatures: (\d[\d,]*) cached, (\d[\d,]*) shaped"
_PRIOR_STORE = r"prior store (\d[\d,]*) hit, (\d[\d,]*) unhit, (\d[\d,]*) missed"
_SIGNATURES_WRITTEN = r"ink-signature store written, (\d[\d,]*) entries"
_BYTE_COPIED = r"byte-copied (\d[\d,]*) of (\d[\d,]*) fragments, copied forward (\d[\d,]*) sidecar rows"
_WRITE_COUNTS = r"kept (\d[\d,]*) by compare, folded (\d[\d,]*) classes, wrote (\d[\d,]*) directly, deflated (\d[\d,]*) fragments"
_SIGNATURES_LEFT = "ink-signature store left as it is"
_STALE_SAMPLE = "the unit cache supplied 1 of "
_UNREADABLE_FRAGMENT = "cannot be read back"
_CONTRACT_CHECK = "contract check failed:"


class _Problems(list):
    """The problems one check finds, each added by `expect` when what it states does not hold."""

    def expect(self, holds: object, problem: str) -> None:
        if not holds:
            self.append(problem)


def _succeeded(run: BuildRun) -> _Problems:
    problems = _Problems()
    problems.expect(run.returncode == 0, f"the build exited {run.returncode}: {run.message()}")
    return problems


def _store_note(run: BuildRun) -> str:
    return run.note("cache")


def _index_note(run: BuildRun) -> str:
    return run.note("manifest+check")


def _units_total(run: BuildRun) -> int:
    found = run.counts("plan", _REUSED)
    return found[1] if found else -1


def _overflow_parts(corpus: Path) -> list[str]:
    return sorted(
        part for meta in _manifest_classes(corpus) for part in unit_index.class_shards(meta) if ".x" in part
    )


def _plain_parts(corpus: Path) -> bool:
    return all(
        (corpus / part).read_bytes()[:1] == b"["
        for meta in _manifest_classes(corpus)
        for part in unit_index.class_shards(meta)
    )


def _check_first(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    problems.expect(
        run.counts("plan", _REUSED) == (0, _units_total(run)),
        "the plan reused a unit from an empty directory",
    )
    problems.expect(
        not _overflow_parts(run.out) and _plain_parts(run.out),
        "the first build wrote a part that is not a plain base",
    )
    problems.expect("(store: a new base (first)" in _store_note(run), "the store is not a new base (first)")
    problems.expect("(index: a new base (first)" in _index_note(run), "the index is not a new base (first)")
    signatures = run.counts("load", _SIGNATURES)
    written = run.counts("units", _SIGNATURES_WRITTEN)
    problems.expect(
        signatures is not None and signatures[0] == 0 and signatures[1] > 0 and written == (signatures[1],),
        "not every signature was shaped and written",
    )
    return problems


def _check_cold(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(
        run.counts("plan", _REUSED) == (0, total), "the plan reused a unit under --recompute-all-units"
    )
    problems.expect(run.counts("units", _RECOMPUTED) == (total, 0), "not every unit was recomputed")
    signatures = run.counts("load", _SIGNATURES)
    problems.expect(
        signatures is not None and signatures[0] == 0 and signatures[1] > 0,
        "the load did not shape every sibling window",
    )
    problems.expect("key-only read ran" in run.note("load"), "the key-only read did not run")
    problems.expect(
        run.counts("units", _SIGNATURES_WRITTEN) is not None, "the signature store was not written"
    )
    written = run.counts("manifest+check", _WRITE_COUNTS)
    problems.expect(written is not None and written[0] == total, "not every unit was kept by compare")
    problems.expect("(store: head only)" in _store_note(run), "the store's lines did not all compare equal")
    problems.expect("(index: head only)" in _index_note(run), "the index's lines did not all compare equal")
    return problems


def _check_warm(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(run.counts("plan", _REUSED) == (total, total), "not every unit was reused")
    problems.expect(
        run.counts("manifest+check", _BYTE_COPIED) == (total, total, total),
        "not every fragment was byte-copied and every sidecar row copied forward",
    )
    problems.expect("(store: head only)" in _store_note(run), "the store wrote more than its head")
    problems.expect("(index: head only)" in _index_note(run), "the index wrote more than its head")
    problems.expect(_SIGNATURES_LEFT in run.note("units"), "the signature store was rewritten")
    problems.expect(
        (run.out / "manifest.json").read_bytes() == (run.prior / "manifest.json").read_bytes(),
        "manifest.json differs from the prior's",
    )
    return problems


def _segment_rows(corpus: Path, output: str) -> list[row_delta.Row]:
    """The rows of the last segment the output's head lists, or none when it lists no segment."""
    head = row_delta.read_head(corpus, output)
    if head is None or not head.segments:
        return []
    number = len(head.segments)
    with (corpus / head.segments[-1].name).open("rb") as handle:
        if head.merge == row_delta.MERGE_KEY:
            return [
                row_delta.Row(op, (0, ""), payload)
                for op, payload in row_delta.keyed_segment_rows(handle, output, head.generation, number)
            ]
        return list(row_delta.segment_rows(handle, output, head.generation, number))


def _check_incremental(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    reused = run.counts("plan", _REUSED)
    problems.expect(
        reused is not None and 0 < reused[0] < total, "the plan did not reuse some units and recompute others"
    )
    problems.expect("key-only read ran" in run.note("load"), "the key-only read did not run")
    prior_store = run.counts("load", _PRIOR_STORE)
    problems.expect(
        prior_store is not None and all(prior_store),
        "the key-only read did not hit, leave unhit and miss prior signatures",
    )
    problems.expect(
        prior_store is not None
        and run.counts("units", _SIGNATURES_WRITTEN) == (prior_store[0] + prior_store[2],),
        "the signature store was not written as the prior hits and the misses",
    )
    written = run.counts("manifest+check", _WRITE_COUNTS)
    problems.expect(written is not None and written[2] > 0, "no class was written directly")
    copied = run.counts("manifest+check", _BYTE_COPIED)
    problems.expect(
        copied is not None and 0 < copied[2] < copied[1], "copy-forward did not run beside changed rows"
    )
    problems.expect("(store: a segment" in _store_note(run), "the store did not write a segment")
    problems.expect("(index: a segment" in _index_note(run), "the index did not write a segment")
    for output in (unit_cache.STORE_NAME, unit_index.INDEX_NAME):
        problems.expect(
            any(row.op == row_delta.TOMBSTONE for row in _segment_rows(run.out, output)),
            f"the {output} segment holds no tombstone for the removed windows",
        )
    problems.expect(
        set(_overflow_parts(run.out)) - set(_overflow_parts(run.prior)) or _appended(run),
        "nothing was appended to an overflow part",
    )
    return problems


def _appended(run: BuildRun) -> bool:
    found = re.search(r"appended (\d+\.\d) MB", run.note("manifest+check"))
    return found is not None and float(found.group(1)) > 0


def _gzip_members(path: Path) -> tuple[int, int]:
    """How many gzip members a file holds, and their inflated bytes in all."""
    data = path.read_bytes()
    members = inflated = 0
    while data:
        stream = zlib.decompressobj(wbits=31)
        inflated += len(stream.decompress(data)) + len(stream.flush())
        members += 1
        data = stream.unused_data
    return members, inflated


def _check_rebase(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(
        run.says(f"unit cache: {unit_cache.NO_STORE_NOTE}"), "the build did not say it found no unit store"
    )
    problems.expect(run.counts("plan", _REUSED) == (0, total), "the plan reused a unit with no store")
    problems.expect(run.counts("units", _RECOMPUTED) == (total, 0), "not every unit was recomputed")
    problems.expect("(store: a new base (first)" in _store_note(run), "the store is not a new base")
    problems.expect(
        "(index: a new base (first)" in _index_note(run), "the index is not a new base by its first route"
    )
    written = run.counts("manifest+check", _WRITE_COUNTS)
    classes = len(_manifest_classes(run.out))
    problems.expect(
        written is not None and written[2] == classes and written[3] == total,
        "the shards were not every class written directly, every fragment deflated",
    )
    store_head = row_delta.read_head(run.out, unit_cache.STORE_NAME)
    listed = set() if store_head is None else {part.name for part in store_head.parts}
    stray = [
        path.name
        for path in run.out.iterdir()
        if path.name.startswith(unit_cache.STORE_NAME + ".")
        and path.name != row_delta.head_name(unit_cache.STORE_NAME)
        and path.name not in listed
    ]
    problems.expect(not stray, f"the store's unlisted parts were not removed: {stray}")
    index_head = row_delta.read_head(run.out, unit_index.INDEX_NAME)
    if index_head is None:
        problems.append("the index has no head")
    else:
        members, inflated = _gzip_members(run.out / index_head.base.name)
        problems.expect(
            members == -(-inflated // unit_index.INDEX_MEMBER_BYTES),
            f"the index's new base holds {members} members, not one each {unit_index.INDEX_MEMBER_BYTES} plain bytes",
        )
    return problems


def _check_restamped(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    warns = run.warns()
    problems.expect(
        any(line.startswith("unit cache: the stamp moved at units_engine") for line in warns),
        "the unit store's stale-stamp note did not name units_engine",
    )
    problems.expect(
        any(line.startswith("ink-signature store: the stamp moved at comparator_code") for line in warns),
        "the ink-signature store's stale-stamp note did not name comparator_code",
    )
    problems.expect(run.counts("plan", _REUSED) == (0, total), "the plan parsed a store record")
    problems.expect(run.counts("units", _RECOMPUTED) == (total, 0), "not every unit was recomputed")
    signatures = run.counts("load", _SIGNATURES)
    problems.expect(
        signatures is not None and signatures[0] == 0 and signatures[1] > 0, "not every window was reshaped"
    )
    problems.expect("(store: head only)" in _store_note(run), "the store wrote more than its head")
    return problems


def _check_rederived(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    problems.expect(
        any(line.startswith("unit cache: the stamp moved at spec_structure") for line in run.warns()),
        "the unit store's note did not name spec_structure",
    )
    problems.expect(
        "(store: a new base (rederived)" in _store_note(run), "the store is not a new base (rederived)"
    )
    return problems


def _check_relocate(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(run.counts("plan", _REUSED) == (total, total), "the walk did not place every unit")
    written = run.counts("manifest+check", _WRITE_COUNTS)
    classes = len(_manifest_classes(run.out))
    problems.expect(written is not None and written[2] == classes, "not every class was written directly")
    return problems


def _check_torn(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(
        any(line.startswith("unit cache: the manifest") for line in run.warns()),
        "the unit store's note did not name the moved manifest",
    )
    problems.expect(run.counts("plan", _REUSED) == (0, total), "the plan parsed a store record")
    problems.expect(run.counts("plan", _KEPT_RECORDS) == (0,), "the plan kept a prior record by id")
    return problems


def _refused(refusal: str, detail: str, *, written: bool = False) -> BuildCheck:
    """A check that the build exited 1 with `refusal` naming `detail`, and, when `written`, after writing its signature store."""

    def check(run: BuildRun) -> list[str]:
        problems = _Problems()
        problems.expect(run.returncode == 1, f"the build exited {run.returncode}, not 1")
        problems.expect(
            refusal in run.log and detail in run.log,
            f"the build did not refuse with {refusal!r} naming {detail!r}",
        )
        if written:
            problems.expect(
                run.counts("units", _SIGNATURES_WRITTEN) is not None, "the signature store was not rewritten"
            )
        return problems

    return check


def _outcome(note: str, outcome: str) -> bool:
    if outcome == "segment":
        return "a segment of " in note
    if outcome == "head":
        return "head only" in note
    return f"a new base ({outcome})" in note


def _chain_check(step: ChainStep, number: int) -> BuildCheck:
    def check(run: BuildRun) -> list[str]:
        problems = _succeeded(run)
        problems.expect(
            _outcome(_store_note(run), step.store), f"step {number}: the store did not write {step.store}"
        )
        problems.expect(
            _outcome(_index_note(run), step.index), f"step {number}: the index did not write {step.index}"
        )
        for output, outcome in ((unit_cache.STORE_NAME, step.store), (unit_index.INDEX_NAME, step.index)):
            head = row_delta.read_head(run.prior, output)
            assert head is not None
            if outcome == "fold":
                forced = row_delta.fold_forced(head)
                problems.expect(row_delta.fold_due(head), f"step {number}: {output}'s fold was not due")
                problems.expect(
                    forced == step.held,
                    f"step {number}: {output}'s fold was {'' if forced else 'not '}forced",
                )
        if step.held:
            assert run.cycle_var is not None
            claim = json.loads((run.cycle_var / cycle_paths.FOLD_CLAIM_NAME).read_text(encoding="utf-8"))
            problems.expect(
                claim.get("output") == HELD_CLAIM_OUTPUT,
                f"step {number}: the build took the claim another output held",
            )
        if step.state == "over-half":
            written = run.counts("manifest+check", _WRITE_COUNTS)
            problems.expect(
                written is not None and written[2] > 0, f"step {number}: no class was written directly"
            )
        return problems

    return check


def check_chain(runs: Sequence[BuildRun]) -> list[str]:
    """What the chain must reach across its builds beyond each step's own check: a class folded and a fragment appended."""
    problems = _Problems()
    problems.expect(
        any((found := run.counts("manifest+check", _WRITE_COUNTS)) and found[1] > 0 for run in runs),
        "no build folded a class's shards",
    )
    problems.expect(any(_overflow_parts(run.out) for run in runs), "no build appended to an overflow part")
    return problems


def prepare(source: str, scenarios: Sequence[str], check: bool) -> bool:
    """Prepare each named scenario, and with `check` run its Python builds into clones under its directory's `check/` and report whether each reached what its row names (`BuildCase.check`). Returns whether every check passed."""
    ok = True
    for name in scenarios:
        scenario = open_build_source(source, name)
        print(
            f"{source}-{name}: prepared {len(scenario.cases)} builds under {scenario.directory}",
            file=sys.stderr,
        )
        if not check:
            continue
        runs: list[BuildRun] = []
        previous: Path | None = None
        for index, case in enumerate(scenario.cases):
            prior = case.prior if case.prior is not None else previous
            assert prior is not None, "a scenario's first build names its prior"
            out = scenario.directory / "check" / case.name
            run = run_python_build(
                case.inputs,
                out,
                scenario.directory / "check" / "logs",
                case.name,
                prior=prior,
                jobs=case.jobs,
                claim_held_by=case.claim_held_by,
            )
            runs.append(run)
            problems = case.check(run)
            ok = ok and not problems
            status = "reached its row" if not problems else "; ".join(problems)
            print(f"{source}-{name} {case.name}: {status}", file=sys.stderr)
            previous = out
            del index
        if name == "chain":
            problems = check_chain(runs)
            ok = ok and not problems
            print(
                f"{source}-{name}: {'reached its row' if not problems else '; '.join(problems)}",
                file=sys.stderr,
            )
    return ok


def scenario_names(source: str, scenario: str) -> list[str]:
    """The scenarios `--scenario` names on a source: one by name, or `all`, every scenario of the source but those that run by name only."""
    if scenario == "all":
        return [
            name
            for name, sources in BUILD_SCENARIOS.items()
            if source in sources and name not in BY_NAME_ONLY
        ]
    return [scenario]


# --- the build's stages -------------------------------------------------------------------------

BUILD_SOURCE_FORMAT = "ams-review-build-stage-source/1"
# The stride of the units a live dump's boundary stages record a line for, unless `--every` names another; a mini dump records every unit.
LIVE_EVERY = 1000
# Where a build dump keeps its clones and the units init, under its directory.
DUMP_PRIOR = "prior"
DUMP_INPUTS = "inputs"
DUMP_INIT = "init"
# Where a build dump runs its build, under its directory, at one path so that every dump into the directory hands the build the same values.
DUMP_SCRATCH = "build-stage-dump"
# The Rust build's input for the dumped run's values, written without descriptors, beside `source.json`.
BUILD_INPUT_FILE = "build.json"
BUILD_INPUT_STAGE = "build-input"
# The build's calls a build stage can record, by name, as `HOOKS` names the worker's: each is the module and the attribute path the wrapper replaces for the length of a build. The task that adds a stage adds the hook points it records.
BUILD_HOOKS: dict[str, tuple[str, str]] = {
    "output_sink.write_output": ("rebuild.pipeline.output_sink", "write_output"),
    "output_sink.replace_output": ("rebuild.pipeline.output_sink", "replace_output"),
    "output_sink.note_rewritten": ("rebuild.pipeline.output_sink", "note_rewritten"),
}


@dataclass(frozen=True)
class BuildSource:
    """A build dump's source: its scenario, prepared over the source's base prior (`open_build_source`), the scenario's one build, the dump's directory, the stride of the units a boundary stage records a line for, and the widths the build runs at (`build_widths`)."""

    scenario: BuildScenario
    case: BuildCase
    out: Path
    every: int
    jobs: int
    signature_jobs: int

    def describe(self, inputs: Mapping[str, str], init: Path) -> dict:
        return {
            "format": BUILD_SOURCE_FORMAT,
            "run": "build",
            "source": self.scenario.source,
            "scenario": self.scenario.name,
            "case": self.case.name,
            "every": self.every,
            "widths": {"jobs": self.jobs, "signature_jobs": self.signature_jobs, "write_jobs": 1},
            "read": self.case.inputs.request(),
            "prior": DUMP_PRIOR,
            "inputs": dict(inputs),
            "init": init.relative_to(self.out).as_posix(),
        }


SINK_STAGE = "sink"
# The `sink` stage's directory, beside its stage file, of the bytes its lines name by sha256: what a target held, what a call writes and the source it copies.
SINK_BLOBS = "sink.blobs"
# The `output_sink` calls the `sink` stage records, by the name `BUILD_HOOKS` gives each, with the signature that binds a hooked call's arguments.
_SINK_SIGNATURES = {
    f"output_sink.{name}": inspect.signature(getattr(output_sink, name))
    for name in ("write_output", "replace_output", "note_rewritten")
}
# The blob directory of the build whose `sink` stage records, for the length of that build (`_sink_blobs_at`).
_SINK_BLOBS_AT: list[Path] = []
# A generated payload past one chunk of `output_sink`'s compare (`output_sink._CHUNK`).
SINK_LARGE_BYTES = 300_000


@contextlib.contextmanager
def _sink_blobs_at(directory: Path) -> Iterator[None]:
    _SINK_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _SINK_BLOBS_AT.pop()


def _blob_into(directory: Path, data: bytes) -> str:
    """Keep `data` under `directory` named by its sha256, once, and return the digest."""
    digest = hashlib.sha256(data).hexdigest()
    path = directory / digest
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return digest


def _sink_held(target: Path, blobs: Path) -> tuple[tuple, int | None]:
    """What `target` holds before a call, as a `sink` line's `held` names it, with a file's bytes kept in `blobs`: `("missing",)`, `("absent",)` when its directory is missing too, `("file", sha256)`, `("fifo",)`, `("directory",)`, `("link", "null")` for a link to the null device or `("link", "file", sha256)`; and its inode, None when there is nothing there."""
    try:
        found = os.lstat(target)
    except FileNotFoundError:
        return ("missing",) if target.parent.is_dir() else ("absent",), None
    mode = found.st_mode
    if stat.S_ISLNK(mode):
        if os.readlink(target) == os.devnull:
            held: tuple = ("link", "null")
        else:
            held = ("link", "file", _blob_into(blobs, target.read_bytes()))
    elif stat.S_ISREG(mode):
        held = ("file", _blob_into(blobs, target.read_bytes()))
    elif stat.S_ISFIFO(mode):
        held = ("fifo",)
    elif stat.S_ISDIR(mode):
        held = ("directory",)
    else:
        raise ValueError(f"the sink stage cannot set {target} up again")
    return held, found.st_ino


def _sink_state(target: Path, held: int | None) -> tuple:
    """What a call left at `target`, by `lstat`: its kind, a file's sha256 (through a link to one), and whether it is still the inode `held` (None when the call found nothing there)."""
    try:
        found = os.lstat(target)
    except FileNotFoundError:
        return ("missing",)
    kept = None if held is None else found.st_ino == held
    mode = found.st_mode
    if stat.S_ISLNK(mode):
        if os.readlink(target) == os.devnull:
            return ("link", "null", kept)
        return ("link", "file", hashlib.sha256(target.read_bytes()).hexdigest(), kept)
    if stat.S_ISREG(mode):
        return ("file", hashlib.sha256(target.read_bytes()).hexdigest(), kept)
    if stat.S_ISFIFO(mode):
        return ("fifo", kept)
    if stat.S_ISDIR(mode):
        return ("directory", kept)
    return ("other", kept)


def _counts_state(path: str | None) -> tuple[str, bytes | None]:
    """The counts file a call finds, as a `sink` line's `counts_file` names it (`none` when no file is named, `missing`, `present`), and what it holds."""
    if not path:
        return "none", None
    try:
        return "present", Path(path).read_bytes()
    except FileNotFoundError:
        return "missing", None


def _counts_appended(path: str | None, counted: bytes | None) -> str | None:
    """What a call appended to the counts file it found holding `counted`, or None when no counts file is there after it."""
    if not path:
        return None
    try:
        after = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    before = counted or b""
    if not after.startswith(before):
        raise ValueError(f"the counts file {path} changed other than by appending")
    return after[len(before) :].decode()


def _sink_line(
    origin: str,
    inputs: Mapping[str, object],
    target: Path | None,
    held: int | None,
    counts: str | None,
    counted: bytes | None,
    returned: object,
) -> dict[str, object]:
    """One `sink` line, keyed by its origin and a digest of its inputs: the inputs, and as its outputs what the call `returned`, what it appended to the counts file (`counts`) and the target's state after it with whether a staging file is left (`after`, None for `note_rewritten`)."""
    after = (
        None
        if target is None
        else (
            _sink_state(target, held),
            os.path.lexists(target.with_name(target.name + output_sink.STAGING_SUFFIX)),
        )
    )
    digest = hashlib.sha1(compact(encode(dict(inputs))).encode("ascii")).hexdigest()[:20]
    return {
        "key": f"{origin}:{inputs['call']}:{digest}",
        **inputs,
        "returned": returned,
        "counts": _counts_appended(counts, counted),
        "after": after,
    }


def _sink_inputs(
    call: str,
    target: Path | None,
    held: tuple | None,
    payload: tuple,
    sha256: str | None,
    keep_if_equal: bool,
    counts_file: str,
) -> dict[str, object]:
    """A `sink` line's inputs: the call, the target's name, what it held, the payload as its kind and the sha256 of its bytes (or `("output", name)` for `note_rewritten`), the `sha256` a writer is kept against, `keep_if_equal`, and the counts file's state."""
    return {
        "call": call,
        "name": None if target is None else target.name,
        "held": held,
        "payload": payload,
        "sha256": sha256,
        "keep_if_equal": keep_if_equal,
        "counts_file": counts_file,
    }


def _sink_call(hook: str) -> Recorder:
    """The `sink` recorder of one `output_sink` call: what the target and the counts file held before it, the bytes it writes (a writer's read from the target once it has run, or, when the call kept the target, the bytes it kept), and what it returned and left."""
    signature = _SINK_SIGNATURES[hook]
    call = hook.removeprefix("output_sink.")

    def record(args: tuple, kwargs: dict) -> Finisher:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        blobs = _SINK_BLOBS_AT[-1]
        counts = os.environ.get(output_sink.OUTPUT_COUNTS_ENV)
        counts_file, counted = _counts_state(counts)
        if call == "note_rewritten":
            inputs = _sink_inputs(call, None, None, ("output", arguments["output"]), None, False, counts_file)
            return lambda result: _sink_line("run", inputs, None, None, counts, counted, result)
        target = Path(arguments["target"])
        held, inode = _sink_held(target, blobs)
        data, source = arguments["data"], arguments.get("source")
        if data is not None:
            raw = data.encode("utf-8") if isinstance(data, str) else data
            payload: tuple | None = ("str" if isinstance(data, str) else "bytes", _blob_into(blobs, raw))
        elif source is not None:
            payload = ("source", _blob_into(blobs, Path(source).read_bytes()))
        else:
            payload = None

        def finish(result: object) -> dict[str, object]:
            written = payload
            if written is None:
                written = ("writer", _blob_into(blobs, target.read_bytes()) if result else held[-1])
            inputs = _sink_inputs(
                call,
                target,
                held,
                written,
                arguments.get("sha256"),
                arguments["keep_if_equal"],
                counts_file,
            )
            return _sink_line("run", inputs, target, inode, counts, counted, result)

        return finish

    return record


class _Refused(Exception):
    """What a generated `sink` case's writer or block raises after it has written, to leave the target as it was."""


@dataclass(frozen=True)
class _SinkCase:
    """One generated `sink` case: the call and its payload's kind and value, the target's state before it (`_sink_target`), `keep_if_equal`, the `sha256` a writer is kept against, and the counts file's state; `note_rewritten`'s `output` in place of a payload."""

    call: str
    kind: str
    payload: bytes | str = b""
    held: str = "missing"
    held_bytes: bytes = b""
    keep: bool = False
    sha256: str | None = None
    counts: str = "present"


def _sink_helds(payload: bytes) -> list[tuple[str, str, bytes]]:
    """Each target state a generated case sets up against `payload`, with a label: missing, in a missing directory, the payload's bytes, other bytes of every size relation to them, a FIFO, a directory, a link to the null device and links to a file of the payload's bytes or others."""
    helds = [
        ("missing", "missing", b""),
        ("absent", "absent", b""),
        ("equal", "file", payload),
        ("longer", "file", payload + b"x"),
        ("fifo", "fifo", b""),
        ("directory", "directory", b""),
        ("link-null", "link-null", b""),
        ("link-equal", "link-file", payload),
        ("link-other", "link-file", payload + b"x"),
    ]
    if payload:
        helds += [
            ("shorter", "file", payload[:-1]),
            ("differs-last", "file", payload[:-1] + bytes([payload[-1] ^ 1])),
            ("differs-first", "file", bytes([payload[0] ^ 1]) + payload[1:]),
        ]
    return helds


def _sink_cases() -> Iterator[_SinkCase]:
    """The generated `sink` cases: each call and payload kind over every target state of `_sink_helds`, with and without `keep_if_equal` (but `replacing`, which never compares, and `write_output` over a FIFO, which blocks with no reader), a writer kept against a digest the target does not have, each counts file state, and `note_rewritten`."""
    large = bytes((index * 31 + index // 251) % 256 for index in range(SINK_LARGE_BYTES))
    payloads = {"empty": b"", "small": b"head\n", "large": large}
    calls: list[tuple[str, str, list[bytes | str]]] = [
        ("write_output", "bytes", list(payloads.values())),
        ("write_output", "str", ["\xe9 \xfcn\xefcode ·Pea\n"]),
        ("replace_output", "bytes", list(payloads.values())),
        ("replace_output", "str", ["\xe9 \xfcn\xefcode ·Pea\n"]),
        ("replace_output", "source", list(payloads.values())),
        ("replace_output", "writer", list(payloads.values())),
        ("replace_output", "writer-raises", [payloads["small"]]),
        ("replacing", "stream", list(payloads.values())),
        ("replacing", "stream-raises", [payloads["small"]]),
    ]
    for call, kind, values in calls:
        for value in values:
            raw = value.encode("utf-8") if isinstance(value, str) else value
            for _label, held, held_bytes in _sink_helds(raw):
                if call == "write_output" and held == "fifo":
                    continue
                for keep in (False,) if call == "replacing" else (False, True):
                    sha256 = hashlib.sha256(raw).hexdigest() if keep and kind.startswith("writer") else None
                    yield _SinkCase(call, kind, value, held, held_bytes, keep, sha256)
    small = payloads["small"]
    for held in ("equal", "longer"):
        yield _SinkCase(
            "replace_output",
            "writer",
            small,
            "file",
            small if held == "equal" else small + b"x",
            True,
            hashlib.sha256(b"other").hexdigest(),
        )
    for counts in ("none", "missing"):
        for call, kind, keep in (
            ("write_output", "bytes", True),
            ("replace_output", "bytes", True),
            ("replace_output", "writer", True),
            ("replacing", "stream", False),
        ):
            for held, held_bytes in (("missing", b""), ("file", small)):
                sha256 = hashlib.sha256(small).hexdigest() if kind == "writer" else None
                yield _SinkCase(call, kind, small, held, held_bytes, keep, sha256, counts)
    for counts in ("present", "none", "missing"):
        for output in ("unit-cache.ndjson.gz", "units-index.ndjson.gz"):
            yield _SinkCase("note_rewritten", "output", output, counts=counts)


def _sink_target(directory: Path, case: _SinkCase) -> Path:
    """Set the case's target up under `directory` as its `held` names: nothing, nothing in a missing directory, a file, a FIFO, a directory, or a link to the null device or to a sibling file."""
    directory.mkdir(parents=True)
    name = "target.out"
    if case.held == "absent":
        return directory / "absent" / name
    target = directory / name
    if case.held == "file":
        target.write_bytes(case.held_bytes)
    elif case.held == "fifo":
        os.mkfifo(target)
    elif case.held == "directory":
        target.mkdir()
    elif case.held == "link-null":
        target.symlink_to(os.devnull)
    elif case.held == "link-file":
        (directory / f"{name}.linked").write_bytes(case.held_bytes)
        target.symlink_to(f"{name}.linked")
    return target


@contextlib.contextmanager
def _counts_named(path: Path | None) -> Iterator[None]:
    """Name `path` as the counts file (`output_sink.OUTPUT_COUNTS_ENV`), or none, for the length of the block."""
    held = os.environ.pop(output_sink.OUTPUT_COUNTS_ENV, None)
    if path is not None:
        os.environ[output_sink.OUTPUT_COUNTS_ENV] = str(path)
    try:
        yield
    finally:
        os.environ.pop(output_sink.OUTPUT_COUNTS_ENV, None)
        if held is not None:
            os.environ[output_sink.OUTPUT_COUNTS_ENV] = held


def _sink_make(case: _SinkCase, target: Path, source: Path) -> object:
    """Make the case's call over `target` and return what it returned."""
    raw = case.payload.encode("utf-8") if isinstance(case.payload, str) else case.payload
    if case.call == "write_output":
        return output_sink.write_output(target, case.payload, keep_if_equal=case.keep)
    if case.call == "replacing":
        with output_sink.replacing(target) as handle:
            handle.write(raw)
            if case.kind == "stream-raises":
                raise _Refused
        return None
    if case.kind == "source":
        source.write_bytes(raw)
        return output_sink.replace_output(target, source=source, keep_if_equal=case.keep)
    if case.kind.startswith("writer"):

        def fill(staging: Path) -> None:
            staging.write_bytes(raw)
            if case.kind == "writer-raises":
                raise _Refused

        return output_sink.replace_output(target, writer=fill, sha256=case.sha256, keep_if_equal=case.keep)
    return output_sink.replace_output(target, case.payload, keep_if_equal=case.keep)


def _sink_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `sink` stage's generated lines (`_sink_cases`), each made through `output_sink` over a target set up in a directory of its own, with its bytes kept in the dump's blob directory."""
    blobs = source.out / SINK_BLOBS
    with tempfile.TemporaryDirectory(prefix="sink-generated-") as held:
        scratch = Path(held)
        for index, case in enumerate(_sink_cases()):
            counts = None if case.counts == "none" else scratch / f"{index}.counts"
            if case.counts == "present":
                assert counts is not None
                counts.write_bytes(b"")
            with _counts_named(counts):
                counts_file, counted = _counts_state(None if counts is None else str(counts))
                if case.call == "note_rewritten":
                    assert isinstance(case.payload, str)
                    inputs = _sink_inputs(
                        case.call, None, None, ("output", case.payload), None, False, counts_file
                    )
                    output_sink.note_rewritten(case.payload)
                    yield _sink_line(
                        "generated",
                        inputs,
                        None,
                        None,
                        None if counts is None else str(counts),
                        counted,
                        None,
                    )
                    continue
                target = _sink_target(scratch / str(index), case)
                held_state, inode = _sink_held(target, blobs)
                raw = case.payload.encode("utf-8") if isinstance(case.payload, str) else case.payload
                inputs = _sink_inputs(
                    case.call,
                    target,
                    held_state,
                    (case.kind, _blob_into(blobs, raw)),
                    case.sha256,
                    case.keep,
                    counts_file,
                )
                try:
                    returned = _sink_make(case, target, scratch / f"{index}.source")
                except OSError, _Refused:
                    returned = ("raised",)
                yield _sink_line(
                    "generated",
                    inputs,
                    target,
                    inode,
                    None if counts is None else str(counts),
                    counted,
                    returned,
                )


# The build's stage registry, in the order `compare` runs the upstream stages' compares before a boundary stage's (the plan's § Stage fixtures). The task that ports a stage adds the stage here. `build-input` records no hooked call: `build_dump` records it from the values it writes `BUILD_INPUT_FILE` from.
BUILD_STAGES: dict[str, Stage[BuildSource]] = {
    BUILD_INPUT_STAGE: Stage(BUILD_INPUT_STAGE, {}, run="build", kind="boundary"),
    SINK_STAGE: Stage(
        SINK_STAGE,
        {hook: _sink_call(hook) for hook in _SINK_SIGNATURES},
        _sink_generated,
        outputs=("returned", "counts", "after"),
        run="build",
    ),
}


def default_every(source: str, every: int | None) -> int:
    """The stride of a dump's boundary stages: every unit on the mini, which refuses another stride, and `every` or `LIVE_EVERY` on live."""
    if source == "mini":
        if every not in (None, 1):
            raise SystemExit("a mini dump records every unit; --every strides a live dump")
        return 1
    if every is not None and every < 1:
        raise SystemExit(f"--every {every} is not a stride; it takes a positive count of units")
    return LIVE_EVERY if every is None else every


def single_build(scenario: BuildScenario) -> BuildCase:
    """The scenario's one build; a scenario of several builds is refused, since a dump records one build's calls and boundaries."""
    if len(scenario.cases) != 1:
        raise SystemExit(
            f"the {scenario.name} scenario runs {len(scenario.cases)} builds; "
            "a build dump or selfcheck takes a scenario of one"
        )
    return scenario.cases[0]


def build_widths(case: BuildCase) -> tuple[int, int]:
    """The units width a dump's build runs at, the case's own or the harness's default (`default_width`), and its signature shaping's (`signature_width`)."""
    jobs = default_width() if case.jobs is None else case.jobs
    return jobs, signature_width(case.jobs)


def open_build_dump(source: str, scenario: str, out: Path, every: int | None) -> BuildSource:
    """Prepare the named scenario (`open_build_source`) for a dump into `out` at the stride `default_every` gives."""
    stride = default_every(source, every)
    prepared = open_build_source(source, scenario)
    case = single_build(prepared)
    jobs, signature_jobs = build_widths(case)
    return BuildSource(prepared, case, out, stride, jobs, signature_jobs)


def clone_build_inputs(inputs: BuildInputs, target: Path) -> dict[str, str]:
    """Clone what a build of `inputs` reads, but the spec, into `target`, modification times kept (`_clone_file`): the audit's files (`_audit_names`), the ledger, the after font and the site's before and Junior fonts, the subset tables and their pack. Return each input's clone by its role, as a path relative to `target`'s parent."""
    roles = {
        "ledger": inputs.ledger,
        "after_font": inputs.after_font,
        "before_font": build.SITE_BEFORE_FONT,
        "junior_font": build.SITE_JUNIOR_FONT,
        "subset_pack": inputs.subset_pack,
        **{
            f"subset_{config}": inputs.subset_dir / f"baseline-{config}.subset.tsv.gz"
            for config in ACCEPTANCE_CONFIGS
        },
    }
    files = {name: inputs.audit.parent / name for name in _audit_names(inputs.audit)}
    for role, path in roles.items():
        if files.setdefault(path.name, path) != path:
            raise SystemExit(f"the build's inputs {files[path.name]} and {path} share one name")
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name, path in files.items():
        _clone_file(path, target / name)
    base = target.name
    cloned = {role: f"{base}/{path.name}" for role, path in roles.items()}
    return {"audit": f"{base}/{inputs.audit.name}", "subset_dir": base, **cloned}


def write_dump_init(inputs: BuildInputs, dump: Path, cloned: Mapping[str, str]) -> Path:
    """Write the units init a build of `inputs` hands its units engine, as `_RecomputeRunner.__init__` builds it, over the dump's clones of the fonts, the subset tables and their pack, into `dump/DUMP_INIT/` beside its spec dump and font facts (`units_exec.write_init`), and return its path."""
    identical_classes = frozenset(entry.id for entry in load_ledger(inputs.ledger) if entry.ink_identical)
    init = _worker_init(
        dump / cloned["after_font"],
        dump / cloned["subset_dir"],
        dump / cloned["subset_pack"],
        inputs.spec_root,
        identical_classes,
        before_font=dump / cloned["before_font"],
        junior_font=dump / cloned["junior_font"],
    )
    directory = _cleared(dump / DUMP_INIT)
    return units_exec.write_init(init, directory)


@contextlib.contextmanager
def _as_build_child(scratch: Path) -> Iterator[None]:
    """Hold this process as `run_python_build` holds its child for the length of one build: the child's environment (`_build_environment`) with its counts file under `scratch`, and the journal and the cycle directory redirected there, so the build's fold claims start from none, as a fresh process's do (`row_delta.claim_fold`). Everything is put back on exit."""
    scratch.mkdir(parents=True, exist_ok=True)
    counts = scratch / "counts"
    counts.write_bytes(b"")
    cycle_var = scratch / "cycle"
    cycle_var.mkdir(exist_ok=True)
    held = dict(os.environ)
    environment = _build_environment(counts, None)
    journal, cycle = cycle_timings.JOURNAL, cycle_paths.CYCLE_VAR
    os.environ.clear()
    os.environ.update(environment)
    cycle_timings.JOURNAL = scratch / "journal.ndjson"
    cycle_paths.CYCLE_VAR = cycle_var
    try:
        yield
    finally:
        cycle_timings.JOURNAL, cycle_paths.CYCLE_VAR = journal, cycle
        os.environ.clear()
        os.environ.update(held)


def _build_here(source: BuildSource, out: Path) -> None:
    inputs = source.case.inputs
    build.build_m1(
        out,
        audit_path=inputs.audit,
        ledger_path=inputs.ledger,
        subset_dir=inputs.subset_dir,
        after_font=inputs.after_font,
        jobs=source.jobs,
        signature_jobs=source.signature_jobs,
        recompute_all_units=inputs.recompute_all,
        spec_root=inputs.spec_root,
        subset_pack=inputs.subset_pack,
        write_jobs=1,
    )


def run_build_here(source: BuildSource, out: Path, scratch: Path) -> None:
    """Run the source's build into `out` in this process (`_as_build_child`), with `python_build_child`'s arguments at the source's widths and one write job, so every write runs here."""
    with _as_build_child(scratch):
        _build_here(source, out)


def build_input_values(
    source: BuildSource, out: Path, scratch: Path, init: Path
) -> tuple[dict, tuple[int, ...]]:
    """The values of the Rust build's input (`build._rust_inputs`) for the build `_build_here` runs into `out`, with `build_m1`'s defaults for every argument it leaves out, over the scenario's own audit held as the build holds it (`audit.AuditParts`), the units init `init` and the spill directory `scratch`; and the size of each audit part held, base first. Run it in the build's environment (`_as_build_child`), whose cycle and counts file the input names."""
    inputs = source.case.inputs
    parts = audit.AuditParts(inputs.audit)
    try:
        values = build._rust_inputs(
            parts,
            out_dir=out,
            audit_path=inputs.audit,
            ledger_path=inputs.ledger,
            subset_dir=inputs.subset_dir,
            before_font=build.SITE_BEFORE_FONT,
            after_font=inputs.after_font,
            junior_font=build.SITE_JUNIOR_FONT,
            repo_root=REPO_ROOT,
            batch_size=BATCH_SIZE,
            static_dir=build.STATIC_DIR,
            jobs=source.jobs,
            signature_jobs=source.signature_jobs,
            recompute_all_units=inputs.recompute_all,
            spec_root=inputs.spec_root,
            subset_digests=table_digests(inputs.subset_dir, ACCEPTANCE_CONFIGS),
            gates_idle_jobs=None,
            gates_idle_marker=None,
            write_jobs=1,
            units_init=init,
            scratch=scratch,
        )
        held = tuple(os.fstat(descriptor).st_size for descriptor in parts.descriptors)
    finally:
        parts.close()
    return values, held


def build_input_boundary(values: Mapping[str, object], held: Sequence[int]) -> Boundary:
    """The `build-input` stage's lines: each input value under `input:<field>`, in `corpus_exec.INPUT_FIELDS` order, and the held audit parts' sizes under `audit.held`."""
    lines = [{"key": f"input:{name}", "value": value} for name, value in values.items()]
    return Boundary(0, {}, [*lines, {"key": "audit.held", "value": tuple(held)}])


def _build_prior(source: BuildSource) -> Path:
    prior = source.case.prior
    assert prior is not None, "a scenario's first build names its prior"
    return prior


def build_dump(source: BuildSource, stages: Sequence[Stage[BuildSource]]) -> None:
    """Write a build dump under `source.out`: the prior corpus as the build finds it (`DUMP_PRIOR`), clones of what it reads (`DUMP_INPUTS`, `clone_build_inputs`), the units init (`write_dump_init`), `source.json`, and the Rust build's input for the run's values without descriptors (`BUILD_INPUT_FILE`, `build_input_values`), taken in the run's environment; then, when any stage is selected, record the `build-input` stage's lines from those values when it is one of them, run the build in this process over a clone of the prior, with every build hook point wrapped and the selected stages recording, and add each stage's generated lines. The build reads the scenario's own inputs, whose paths its manifest names, and the build's tree is deleted once it returns."""
    out = source.out
    out.mkdir(parents=True, exist_ok=True)
    prior = out / DUMP_PRIOR
    _clone_tree(_build_prior(source), prior)
    cloned = clone_build_inputs(source.case.inputs, out / DUMP_INPUTS)
    init = write_dump_init(source.case.inputs, out, cloned)
    (out / SOURCE_FILE).write_text(
        json.dumps(source.describe(cloned, init), indent=1) + "\n", encoding="ascii"
    )
    sink = _Sink(out, stages, source.every) if stages else None
    if any(stage.name == SINK_STAGE for stage in stages):
        shutil.rmtree(out / SINK_BLOBS, ignore_errors=True)
    scratch = out / DUMP_SCRATCH
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        built = scratch / "out"
        with _as_build_child(scratch / "run"):
            values, held = build_input_values(source, built, scratch / "spill", init)
            corpus_exec.write_input(values, out / BUILD_INPUT_FILE)
            if sink is not None:
                if any(stage.name == BUILD_INPUT_STAGE for stage in stages):
                    sink.record(BUILD_INPUT_STAGE, build_input_boundary(values, held))
                _clone_tree(prior, built)
                with wrapped(stages, sink, BUILD_HOOKS), _sink_blobs_at(out / SINK_BLOBS):
                    _build_here(source, built)
        shutil.rmtree(scratch)
        if sink is not None:
            for stage in stages:
                if stage.generated is not None:
                    for fields in stage.generated(source):
                        sink.write(stage.name, "generated", fields)
    finally:
        if sink is not None:
            sink.close()
    print(
        f"{out}: {source.scenario.source}-{source.scenario.name} {source.case.name}, "
        f"{SOURCE_FILE}, {BUILD_INPUT_FILE}, {DUMP_PRIOR}/, {DUMP_INPUTS}/ and {init.relative_to(out)}",
        file=sys.stderr,
    )
    for stage in stages:
        print(out / f"{stage.name}.ndjson", file=sys.stderr)


def _tree_files(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*") if not path.is_dir()}


def tree_differences(left: Path, right: Path) -> tuple[int, list[str]]:
    """How many files two output trees hold between them, and each relative path that only one holds or whose bytes differ, sorted."""
    left_files, right_files = _tree_files(left), _tree_files(right)
    names = sorted(left_files.keys() | right_files.keys())
    differing = [
        name
        for name in names
        if name not in left_files
        or name not in right_files
        or not filecmp.cmp(left_files[name], right_files[name], shallow=False)
    ]
    return len(names), differing


def build_selfcheck(source: str, scenario: str) -> bool:
    """Prepare the scenario and run its one build twice in this process, each over its own clone of the prior (`run_build_here`): once as the build runs and once with every build hook point wrapped and every build stage recording into a temporary directory. Print what differs and return whether the two output trees are identical."""
    prepared = open_build_source(source, scenario)
    case = single_build(prepared)
    jobs, signature_jobs = build_widths(case)
    with tempfile.TemporaryDirectory(prefix="build-stage-selfcheck-", dir=prepared.directory) as held:
        scratch = Path(held)
        built = BuildSource(
            prepared, case, scratch / "stages", default_every(source, None), jobs, signature_jobs
        )
        plain, recorded = scratch / "plain", scratch / "wrapped"
        _clone_tree(_build_prior(built), plain)
        run_build_here(built, plain, scratch / "plain-run")
        built.out.mkdir()
        stages = list(BUILD_STAGES.values())
        sink = _Sink(built.out, stages, built.every)
        try:
            _clone_tree(_build_prior(built), recorded)
            with wrapped(stages, sink, BUILD_HOOKS), _sink_blobs_at(built.out / SINK_BLOBS):
                run_build_here(built, recorded, scratch / "wrapped-run")
        finally:
            sink.close()
        files, differing = tree_differences(plain, recorded)
    print(
        f"{source}-{scenario} {case.name}: the plain and wrapped builds ({len(stages)} stages, "
        f"{len(BUILD_HOOKS)} hook points) wrote {files:,} files, {len(differing):,} differing",
        file=sys.stderr,
    )
    for name in differing[:EXAMPLES_SHOWN]:
        print(f"  differs: {name}", file=sys.stderr)
    if not differing:
        print("selfcheck: the wrapped build writes the plain build's tree byte for byte", file=sys.stderr)
    return not differing


# --- commands -----------------------------------------------------------------------------------


def _selected[S](names: Sequence[str] | None, registry: Mapping[str, Stage[S]]) -> list[Stage[S]]:
    if not names:
        return list(registry.values())
    unknown = [name for name in names if name not in registry]
    if unknown:
        known = ", ".join(registry) or "none yet"
        raise SystemExit(f"unknown stage {', '.join(unknown)}; the registered stages are: {known}")
    return [registry[name] for name in dict.fromkeys(names)]


def dump(source: Source, stages: Sequence[Stage[Source]], out: Path) -> None:
    """Write the units file and every selected stage's lines under `out`: the worker's run records the stages with recorders, and each stage's generator adds its generated lines after them."""
    out.mkdir(parents=True, exist_ok=True)
    write_units_file(source, out)
    print(
        f"{out / UNITS_FILE}: {source.unit_count():,} units in {len(source.handouts)} handouts",
        file=sys.stderr,
    )
    if not stages:
        return
    sink = _Sink(out, stages)
    try:
        if any(stage.recorders for stage in stages):
            with wrapped(stages, sink):
                run = run_worker(source)
            print(f"recorded {run.units:,} units", file=sys.stderr)
        for stage in stages:
            if stage.generated is not None:
                for fields in stage.generated(source):
                    sink.write(stage.name, "generated", fields)
    finally:
        sink.close()
    for stage in stages:
        print(out / f"{stage.name}.ndjson", file=sys.stderr)


def selfcheck(source: Source) -> bool:
    """Run the worker plain and wrapped (the module docstring), print both digests and the round trip's result, and return whether everything agreed."""
    plain = run_worker(source)
    with tempfile.TemporaryDirectory(prefix="units-stage-selfcheck-") as scratch:
        sink = _Sink(Path(scratch), list(STAGES.values()))
        try:
            with wrapped(list(STAGES.values()), sink):
                recorded = run_worker(source)
        finally:
            sink.close()
    print(
        f"plain: {plain.units:,} units, {plain.contract_errors} contract errors, digest {plain.digest}",
        file=sys.stderr,
    )
    print(
        f"wrapped ({len(STAGES)} stages, {len(HOOKS)} hook points): {recorded.units:,} units, "
        f"{recorded.contract_errors} contract errors, digest {recorded.digest}",
        file=sys.stderr,
    )
    ok = True
    if plain.units != source.unit_count():
        print(
            f"the worker answered {plain.units:,} of {source.unit_count():,} units handed out",
            file=sys.stderr,
        )
        ok = False
    if plain.digest != recorded.digest or plain.units != recorded.units:
        print("the wrapped run's projections differ from the plain run's", file=sys.stderr)
        ok = False
    for label, run in (("plain", plain), ("wrapped", recorded)):
        if run.unfaithful:
            print(
                f"{label}: {len(run.unfaithful):,} projections did not survive the typed encoding, "
                f"ordinals {run.unfaithful[:10]}",
                file=sys.stderr,
            )
            ok = False
    if ok:
        print(
            "selfcheck: the wrapped run matches the plain run and every projection round-trips",
            file=sys.stderr,
        )
    return ok


def source_init(description: Mapping[str, object]) -> dict:
    """The worker `init` of the source a dump's `source.json` names, built as `open_source` builds it but, for a live source, without loading the corpus, which the init does not read."""
    name = description.get("source")
    if name == "mini":
        return _mini_source().init
    if name == "live":
        identical_classes = frozenset(
            entry.id for entry in load_ledger(build.M1_LEDGER) if entry.ink_identical
        )
        return _worker_init(
            build.M1_AFTER_FONT, build.M1_SUBSETS, build.M1_SUBSETS / PACK_NAME, REPO_ROOT, identical_classes
        )
    raise SystemExit(f"the dump names source {name!r}; the sources are {', '.join(SOURCES)}")


# How many disagreements `compare` prints.
EXAMPLES_SHOWN = 5


def _answers(lines: Sequence[str], fields: Sequence[str], origin: str) -> dict[str, dict[str, object]]:
    """Each line's `fields` decoded from the typed encoding, by the line's key; a key on two lines is refused."""
    answers: dict[str, dict[str, object]] = {}
    for line in lines:
        record = json.loads(line)
        key = record.get("key")
        if not isinstance(key, str):
            raise SystemExit(f"{origin}: a line has no key: {line[:200]}")
        if key in answers:
            raise SystemExit(f"{origin}: the key {key!r} is on two lines")
        answers[key] = {name: decode(record[name]) for name in fields if name in record}
    return answers


def _difference(expected: object, actual: object) -> str:
    left, right = compact(encode(expected)), compact(encode(actual))
    offset = next(
        (index for index, (x, y) in enumerate(zip(left, right)) if x != y), min(len(left), len(right))
    )
    start = max(0, offset - 80)
    return f"at character {offset}:\n    python: …{left[start:offset + 80]}…\n    rust:   …{right[start:offset + 80]}…"


def compared_fields(stage: Stage[Any], names: str | None) -> tuple[str, ...]:
    """The outputs `compare` checks: every output of the stage, or the comma-separated output fields and field groups `names` lists, in that order, each once. A name the stage has neither as an output nor as a group is refused."""
    if names is None:
        return stage.outputs
    chosen: list[str] = []
    for name in names.split(","):
        if name in stage.field_groups:
            chosen += stage.field_groups[name]
        elif name in stage.outputs:
            chosen.append(name)
        else:
            known = ", ".join([*stage.field_groups, *stage.outputs])
            raise SystemExit(f"the {stage.name} stage has no field or field group {name!r}; it has {known}")
    return tuple(dict.fromkeys(chosen))


def _run_worker_stage(stage: Stage[Any], stages: Path, stage_file: Path) -> tuple[dict, int, bytes, bytes]:
    """Run a worker stage's Rust side over a dump with an init written for the dump's source (`source_init`, `units_exec.write_init`) into a directory held for the run, and return that init with the process's status and output."""
    init = source_init(json.loads((stages / SOURCE_FILE).read_text(encoding="ascii")))
    with tempfile.TemporaryDirectory(prefix="units-stage-compare-") as scratch:
        init_path = units_exec.write_init(init, Path(scratch))
        process = units_exec.spawn(["stage", stage.name, f"--init={init_path}", str(stage_file)])
        stdout, stderr = process.communicate()
    return init, process.returncode, stdout, stderr


def _run_build_stage(stage: Stage[Any], stages: Path, stage_file: Path) -> tuple[dict, int, bytes, bytes]:
    """Run a build stage's Rust side over a dump: open the dump's audit clone as the build holds an audit (`audit.AuditParts`), write the dump's `BUILD_INPUT_FILE` again with those descriptors into a directory held for the run, and run the stage with them inherited. Return the input's document with the process's status and output."""
    description = json.loads((stages / SOURCE_FILE).read_text(encoding="ascii"))
    document = json.loads((stages / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    parts = audit.AuditParts(stages / description["inputs"]["audit"])
    try:
        document["audit"]["descriptors"] = list(parts.descriptors)
        with tempfile.TemporaryDirectory(prefix="build-stage-compare-") as scratch:
            input_path = Path(scratch) / BUILD_INPUT_FILE
            input_path.write_text(compact(document) + "\n", encoding="ascii")
            process = units_exec.spawn(
                ["stage", stage.name, f"--init={input_path}", str(stage_file)], pass_fds=parts.descriptors
            )
            stdout, stderr = process.communicate()
    finally:
        parts.close()
    return document, process.returncode, stdout, stderr


def build_compare(stage: Stage[BuildSource], stages: Path, checked: Sequence[str] | None = None) -> bool:
    """`compare` a build stage. Before a boundary stage's own compare, compare each build stage registered ahead of it (`BUILD_STAGES`) whose lines the dump holds, every output checked, and stop at the first that differs, which names the stage a disagreement starts in."""
    if stage.kind == "boundary":
        for upstream in BUILD_STAGES.values():
            if upstream.name == stage.name:
                break
            if (stages / f"{upstream.name}.ndjson").is_file() and not compare(upstream, stages):
                print(
                    f"compare {stage.name}: the upstream {upstream.name} stage differs first", file=sys.stderr
                )
                return False
    return compare(stage, stages, checked)


def compare(stage: Stage[Any], stages: Path, checked: Sequence[str] | None = None) -> bool:
    """Run the Rust side of `stage` over the dump under `stages` and report every line whose expected output it does not reproduce, every problem the stage's answer checks find in an answer, every key it does not answer, and every key it answers that the dump does not hold. Only the outputs `checked` names are checked, every output when None. The crate is built first (`units_exec.ensure_built`); a worker stage reads an init written for the dump's source (`_run_worker_stage`), and a build stage the dump's build input with the dump's audit clone held and inherited (`_run_build_stage`), whose document its answer checks see in place of the init."""
    outputs = tuple(checked) if checked is not None else stage.outputs
    stage_file = stages / f"{stage.name}.ndjson"
    if not stage_file.is_file():
        raise SystemExit(f"{stage_file} is missing; dump --stage {stage.name} writes it")
    expected = _answers(stage_file.read_text(encoding="ascii").splitlines(), outputs, str(stage_file))
    units_exec.ensure_built()
    run = _run_build_stage if stage.run == "build" else _run_worker_stage
    init, returncode, stdout, stderr = run(stage, stages, stage_file)
    if returncode != 0 or stderr:
        print(
            f"compare {stage.name}: ams-review exited {returncode}: "
            + stderr.decode(errors="replace").strip(),
            file=sys.stderr,
        )
        return False
    actual = _answers(
        stdout.decode("utf-8").splitlines(),
        (*outputs, *stage.answer_checks),
        f"ams-review stage {stage.name}",
    )
    missing = [key for key in expected if key not in actual]
    extra = [key for key in actual if key not in expected]
    differing = []
    problems: list[tuple[str, str, str]] = []
    for key, fields in expected.items():
        answer = actual.get(key)
        if answer is None:
            continue
        for name in outputs:
            if name not in fields or name not in answer or not same(fields[name], answer[name]):
                differing.append((key, name))
        for name, check in stage.answer_checks.items():
            problem = check(init, fields, answer[name]) if name in answer else f"the answer has no {name}"
            if problem is not None:
                problems.append((key, name, problem))
    for label, keys in (("unanswered", missing), ("not in the dump", extra)):
        if keys:
            print(
                f"compare {stage.name}: {len(keys):,} keys {label}, the first {keys[:EXAMPLES_SHOWN]}",
                file=sys.stderr,
            )
    for key, name in differing[:EXAMPLES_SHOWN]:
        print(
            f"compare {stage.name}: {key} {name} differs "
            + _difference(expected[key].get(name), actual[key].get(name)),
            file=sys.stderr,
        )
    for key, name, problem in problems[:EXAMPLES_SHOWN]:
        print(f"compare {stage.name}: {key} {name}: {problem}", file=sys.stderr)
    ok = not (missing or extra or differing or problems)
    print(
        f"compare {stage.name} ({stages}): {len(expected):,} lines, {len(differing):,} differ, "
        + (f"{len(problems):,} fail their answer checks, " if stage.answer_checks else "")
        + f"{len(missing):,} unanswered, {len(extra):,} not in the dump"
        + ("; Rust reproduces every line" if ok else ""),
        file=sys.stderr,
    )
    return ok


UNICODE_TABLES = REPO_ROOT / "rebuild" / "review-rs" / "src" / "pyfmt" / "unicode_tables.rs"
# Each table's Rust name, the `str` predicate it holds, and what its doc comment says it is.
UNICODE_PREDICATES = (
    ("PRINTABLE", "isprintable", "`str.isprintable`"),
    ("SPACE", "isspace", "`str.isspace`, behind `strip`, `split` and `re`'s `\\s`"),
    ("DIGIT", "isdigit", "`str.isdigit`"),
    ("DECIMAL", "isdecimal", "`str.isdecimal`, the digits `int` reads in any script,"),
)


def _predicate_ranges(predicate: Callable[[str], bool]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = None
    for point in range(sys.maxunicode + 2):
        if point <= sys.maxunicode and predicate(chr(point)):
            if start is None:
                start = point
        elif start is not None:
            ranges.append((start, point - 1))
            start = None
    return ranges


def _check_decimal_runs(ranges: Sequence[tuple[int, int]]) -> None:
    """Raise ValueError unless every `str.isdecimal` range is whole runs of ten digits from zero, the premise `pyfmt::decimal_value` reads a digit's value from."""
    for start, end in ranges:
        for point in range(start, end + 1):
            if unicodedata.decimal(chr(point)) != (point - start) % 10:
                raise ValueError(f"U+{point:04X} breaks the runs of ten decimal digits from U+{start:04X}")


def unicode_tables_text() -> str:
    """The text of `UNICODE_TABLES` for this CPython: each predicate's inclusive code point ranges in ascending order, one per line under `#[rustfmt::skip]`."""
    lines = [
        "//! The pinned CPython's `str` predicates as code point ranges, for `repr` and the ported string handling ([`super::is_printable`], [`super::is_space`], [`super::is_digit`], [`super::decimal_value`]). `uv run python -m rebuild.tools.units_stage_fixtures unicode-tables` generates this file and `--check` fails when it is stale; it is never edited by hand.",
        "",
        "/// The Unicode version of the CPython the tables come from (`unicodedata.unidata_version`).",
        f'pub const UNICODE_VERSION: &str = "{unicodedata.unidata_version}";',
    ]
    for name, method, what in UNICODE_PREDICATES:
        ranges = _predicate_ranges(getattr(str, method))
        if method == "isdecimal":
            _check_decimal_runs(ranges)
        lines += [
            "",
            f"/// The code points {what} accepts, as inclusive ranges in ascending order.",
            "#[rustfmt::skip]",
            f"pub static {name}: &[(u32, u32)] = &[",
            *(f"    (0x{start:04X}, 0x{end:04X})," for start, end in ranges),
            "];",
        ]
    return "\n".join(lines) + "\n"


def unicode_tables(check: bool) -> bool:
    """Write `UNICODE_TABLES`, or with `check` report whether it holds what this CPython would write, and return whether it does."""
    text = unicode_tables_text()
    if not check:
        UNICODE_TABLES.write_text(text, encoding="utf-8")
        print(f"wrote {UNICODE_TABLES.relative_to(REPO_ROOT)}", file=sys.stderr)
        return True
    current = UNICODE_TABLES.read_text(encoding="utf-8") if UNICODE_TABLES.is_file() else None
    if current != text:
        print(
            f"{UNICODE_TABLES.relative_to(REPO_ROOT)} is not what this CPython (Unicode "
            f"{unicodedata.unidata_version}) gives; unicode-tables rewrites it",
            file=sys.stderr,
        )
        return False
    print(f"{UNICODE_TABLES.relative_to(REPO_ROOT)} is current", file=sys.stderr)
    return True


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--per-config", type=int, default=0, metavar="K")
    parser.add_argument("--batches", type=int, default=0, metavar="N")
    parser.add_argument("--all", action="store_true", dest="everything")


def _add_scenario_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scenario",
        choices=BUILD_SCENARIOS,
        help="take a build source: this scenario's one build, in place of the worker's handouts",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="units_stage_fixtures", description=(__doc__ or "").split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    dump_parser = commands.add_parser("dump", help="write the units file and the selected stages' lines")
    _add_source_arguments(dump_parser)
    _add_scenario_argument(dump_parser)
    dump_parser.add_argument("--stage", action="append", metavar="STAGE")
    dump_parser.add_argument("--out", type=Path, required=True)
    dump_parser.add_argument(
        "--every", type=int, metavar="N", help="a live build dump's boundary stages record every Nth unit"
    )
    check_parser = commands.add_parser("selfcheck", help="check that the wrappers change nothing")
    _add_source_arguments(check_parser)
    _add_scenario_argument(check_parser)
    compare_parser = commands.add_parser("compare", help="check the Rust side of a stage against a dump")
    compare_parser.add_argument("stage", metavar="STAGE")
    compare_parser.add_argument("--stages", type=Path, required=True, metavar="DIR")
    compare_parser.add_argument(
        "--fields", metavar="NAMES", help="check only these comma-separated output fields and field groups"
    )
    tables_parser = commands.add_parser("unicode-tables", help="write the crate's str predicate tables")
    tables_parser.add_argument("--check", action="store_true", help="fail when the tables are stale")
    prepare_parser = commands.add_parser("prepare", help="prepare build scenarios over a source's base prior")
    prepare_parser.add_argument("--source", choices=SOURCES, required=True)
    prepare_parser.add_argument("--scenario", choices=(*BUILD_SCENARIOS, "all"), required=True)
    prepare_parser.add_argument(
        "--check", action="store_true", help="run each scenario's Python builds and check what they reached"
    )
    child_parser = commands.add_parser("python-build", help="run one build a prepare request describes")
    child_parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    if args.command == "unicode-tables":
        return 0 if unicode_tables(args.check) else 1
    if args.command == "python-build":
        return python_build_child(args.request)
    if args.command == "prepare":
        names = scenario_names(args.source, args.scenario)
        for name in names:
            if args.source not in BUILD_SCENARIOS[name]:
                raise SystemExit(
                    f"the {name} scenario runs on {', '.join(BUILD_SCENARIOS[name])}, not {args.source}"
                )
        return 0 if prepare(args.source, names, args.check) else 1
    if args.command == "compare":
        if args.stage in BUILD_STAGES:
            (build_stage,) = _selected([args.stage], BUILD_STAGES)
            return (
                0 if build_compare(build_stage, args.stages, compared_fields(build_stage, args.fields)) else 1
            )
        (stage,) = _selected([args.stage], STAGES)
        return 0 if compare(stage, args.stages, compared_fields(stage, args.fields)) else 1
    if args.scenario is not None:
        if args.per_config or args.batches or args.everything:
            raise SystemExit(
                "--per-config, --batches and --all choose a worker source's handouts, not a build's"
            )
        if args.command == "dump":
            build_stages = _selected(args.stage, BUILD_STAGES)
            build_dump(open_build_dump(args.source, args.scenario, args.out, args.every), build_stages)
            return 0
        return 0 if build_selfcheck(args.source, args.scenario) else 1
    if getattr(args, "every", None) is not None:
        raise SystemExit("--every strides a build dump's boundary stages; it takes --scenario")
    stages = _selected(args.stage, STAGES) if args.command == "dump" else []
    source = open_source(args.source, args.per_config, args.batches, args.everything)
    if args.command == "dump":
        dump(source, stages, args.out)
        return 0
    return 0 if selfcheck(source) else 1


if __name__ == "__main__":
    raise SystemExit(main())
