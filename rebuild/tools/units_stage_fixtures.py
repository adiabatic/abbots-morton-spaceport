"""Record what the real Python corpus units worker's stages take and return, for the port of the units recompute to Rust (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § Stage fixtures).

`dump` runs `build._corpus_worker` in this process over a source and writes, under `--out`, the units file (`units.ndjson`: one `batch` request frame per handout, each frame as `units_exec.encode_batch` writes it), the source's description (`source.json`, which names the source and its handouts), and one `STAGE.ndjson` per selected stage. A stage is a `Stage` in `STAGES`: recorders on the worker's hook points (`HOOKS`), each of which sees a call's arguments before the call and its result after it and returns the fields of the line to record, and an optional generator of inputs the source does not reach. Every recorded value is written in the typed encoding (`units_exec.encode`, `decode`). The wrappers record and return; they never change a value. A line's `key` names it, and the stage's `outputs` are the fields that hold what the Rust side must reproduce.

`selfcheck` runs the worker over a source twice, once as the build runs it and once with every hook point wrapped and every registered stage recording into a temporary directory, and fails unless both runs give the same projection digest and every projection either run returns comes back from the typed encoding's round trip equal to itself, compared type-strictly (`same`).

`compare STAGE --stages DIR` builds the crate (`units_exec.ensure_built`), writes an init for the source the dump's `source.json` names (`source_init`, `units_exec.write_init`), runs `ams-review stage STAGE --init=<init> DIR/STAGE.ndjson`, and reports every key whose outputs the Rust answer does not reproduce, type-strictly, and every key either side lacks. `--fields` narrows the outputs it checks to a comma-separated list of the stage's output fields and field groups.

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
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import functools
import hashlib
import importlib
import json
import pickle
import shutil
import sys
import tempfile
import unicodedata
import warnings
from array import array
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import batched, combinations
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import IO, Any, TypedDict

import yaml

from rebuild.pipeline import fingerprint, spec_load
from rebuild.pipeline.explain import ExplainReport
from rebuild.pipeline.model import feature_config_token
from rebuild.review import audit, build, drafts, ink, unit_cache, units_exec
from rebuild.review.audit import ACCEPTANCE_CONFIGS, Unit, UnitTable, load_ledger
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
) -> dict:
    """Return the `init` dict `_RecomputeRunner.__init__` hands every pool worker, over the site's before and Junior fonts."""
    site_pages = fingerprint.read_site_corpus(REPO_ROOT)
    return {
        "before_font": build.SITE_BEFORE_FONT,
        "after_font": after_font,
        "junior_font": build.SITE_JUNIOR_FONT,
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

# A recorder sees one call's positional and keyword arguments before the call runs and returns None, to record nothing, or a finisher, which receives the call's result and returns the fields of the line to record, a list of such fields for a call that records one line per item, or None.
Finisher = Callable[[object], Mapping[str, object] | list[Mapping[str, object]] | None]
Recorder = Callable[[tuple, dict], Finisher | None]


@dataclass(frozen=True)
class Stage:
    """One stage of the port: its recorders by hook name (`HOOKS`), its generated inputs, which give the fields of each further line for a source, the fields of a line that hold its expected output, which `compare` checks against the Rust stage's answer to the line's `key`, and named groups of those fields that `compare --fields` can check alone."""

    name: str
    recorders: Mapping[str, Recorder]
    generated: Callable[[Source], Iterator[Mapping[str, object]]] | None = None
    outputs: tuple[str, ...] = ("value",)
    field_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


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


# The stage registry. The task that ports a stage adds the stage here (the plan's § Working rules).
STAGES: dict[str, Stage] = {
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
}


class _Sink:
    """The open `STAGE.ndjson` files of one run under `out`, each line a JSON object whose `from` says whether the run or the stage's generator produced it and whose other fields are typed values. A line whose key a stage already wrote is written once: a second, equal line is dropped, and a different one raises ValueError."""

    def __init__(self, out: Path, stages: Sequence[Stage]) -> None:
        self._handles: dict[str, IO[str]] = {
            stage.name: (out / f"{stage.name}.ndjson").open("w", encoding="ascii") for stage in stages
        }
        self._written: dict[str, dict[object, bytes]] = {stage.name: {} for stage in stages}

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


def _resolve(hook: str) -> tuple[object, str]:
    module_name, path = HOOKS[hook]
    owner: object = importlib.import_module(module_name)
    *parents, attribute = path.split(".")
    for parent in parents:
        owner = getattr(owner, parent)
    return owner, attribute


@contextlib.contextmanager
def wrapped(stages: Sequence[Stage], sink: _Sink | None) -> Iterator[None]:
    """Replace every hook point with a wrapper that calls the original and passes the call to each stage's recorder for that hook, and restore the originals on exit."""
    restore: list[tuple[object, str, object]] = []
    try:
        for hook in HOOKS:
            owner, attribute = _resolve(hook)
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
            fields = finish(result)
            if fields is not None and sink is not None:
                for line in fields if isinstance(fields, list) else [fields]:
                    sink.write(name, "run", line)
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


# --- commands -----------------------------------------------------------------------------------


def _selected(names: Sequence[str] | None) -> list[Stage]:
    if not names:
        return list(STAGES.values())
    unknown = [name for name in names if name not in STAGES]
    if unknown:
        known = ", ".join(STAGES) or "none yet"
        raise SystemExit(f"unknown stage {', '.join(unknown)}; the registered stages are: {known}")
    return [STAGES[name] for name in dict.fromkeys(names)]


def dump(source: Source, stages: Sequence[Stage], out: Path) -> None:
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


def compared_fields(stage: Stage, names: str | None) -> tuple[str, ...]:
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


def compare(stage: Stage, stages: Path, checked: Sequence[str] | None = None) -> bool:
    """Run the Rust side of `stage` over the dump under `stages` and report every line whose expected output it does not reproduce, every key it does not answer, and every key it answers that the dump does not hold. Only the outputs `checked` names are checked, every output when None. The crate is built first (`units_exec.ensure_built`), and the init it reads is written for the dump's source (`units_exec.write_init`) into a directory held for the run."""
    outputs = tuple(checked) if checked is not None else stage.outputs
    stage_file = stages / f"{stage.name}.ndjson"
    if not stage_file.is_file():
        raise SystemExit(f"{stage_file} is missing; dump --stage {stage.name} writes it")
    description = json.loads((stages / SOURCE_FILE).read_text(encoding="ascii"))
    expected = _answers(stage_file.read_text(encoding="ascii").splitlines(), outputs, str(stage_file))
    units_exec.ensure_built()
    init = source_init(description)
    with tempfile.TemporaryDirectory(prefix="units-stage-compare-") as scratch:
        init_path = units_exec.write_init(init, Path(scratch))
        process = units_exec.spawn(["stage", stage.name, f"--init={init_path}", str(stage_file)])
        stdout, stderr = process.communicate()
    if process.returncode != 0 or stderr:
        print(
            f"compare {stage.name}: ams-review exited {process.returncode}: "
            + stderr.decode(errors="replace").strip(),
            file=sys.stderr,
        )
        return False
    actual = _answers(stdout.decode("utf-8").splitlines(), outputs, f"ams-review stage {stage.name}")
    missing = [key for key in expected if key not in actual]
    extra = [key for key in actual if key not in expected]
    differing = []
    for key, fields in expected.items():
        answer = actual.get(key)
        if answer is None:
            continue
        for name in outputs:
            if name not in fields or name not in answer or not same(fields[name], answer[name]):
                differing.append((key, name))
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
    ok = not (missing or extra or differing)
    print(
        f"compare {stage.name} ({stages}): {len(expected):,} lines, {len(differing):,} differ, "
        f"{len(missing):,} unanswered, {len(extra):,} not in the dump"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="units_stage_fixtures", description=(__doc__ or "").split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    dump_parser = commands.add_parser("dump", help="write the units file and the selected stages' lines")
    _add_source_arguments(dump_parser)
    dump_parser.add_argument("--stage", action="append", metavar="STAGE")
    dump_parser.add_argument("--out", type=Path, required=True)
    check_parser = commands.add_parser("selfcheck", help="check that the wrappers change nothing")
    _add_source_arguments(check_parser)
    compare_parser = commands.add_parser("compare", help="check the Rust side of a stage against a dump")
    compare_parser.add_argument("stage", metavar="STAGE")
    compare_parser.add_argument("--stages", type=Path, required=True, metavar="DIR")
    compare_parser.add_argument(
        "--fields", metavar="NAMES", help="check only these comma-separated output fields and field groups"
    )
    tables_parser = commands.add_parser("unicode-tables", help="write the crate's str predicate tables")
    tables_parser.add_argument("--check", action="store_true", help="fail when the tables are stale")
    args = parser.parse_args(argv)
    if args.command == "unicode-tables":
        return 0 if unicode_tables(args.check) else 1
    if args.command == "compare":
        (stage,) = _selected([args.stage])
        return 0 if compare(stage, args.stages, compared_fields(stage, args.fields)) else 1
    stages = _selected(args.stage) if args.command == "dump" else []
    source = open_source(args.source, args.per_config, args.batches, args.everything)
    if args.command == "dump":
        dump(source, stages, args.out)
        return 0
    return 0 if selfcheck(source) else 1


if __name__ == "__main__":
    raise SystemExit(main())
