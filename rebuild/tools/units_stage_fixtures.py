"""Record what the real Python corpus units worker's stages take and return, for the port of the units recompute to Rust (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § Stage fixtures).

`dump` runs `build._corpus_worker` in this process over a source and writes, under `--out`, the units file (`units.ndjson`: one `batch` request frame per handout, each unit in the record shape of the plan's § Batches in), the source's description (`source.json`, which names the source and its handouts), and one `STAGE.ndjson` per selected stage. A stage is a `Stage` in `STAGES`: recorders on the worker's hook points (`HOOKS`), each of which sees a call's arguments before the call and its result after it and returns the fields of the line to record, and an optional generator of inputs the source does not reach. Every recorded value is written in the typed encoding (`encode`, `decode`). The wrappers record and return; they never change a value.

`selfcheck` runs the worker over a source twice, once as the build runs it and once with every hook point wrapped and every registered stage recording into a temporary directory, and fails unless both runs give the same projection digest and every projection either run returns comes back from the typed encoding's round trip equal to itself, compared type-strictly (`same`).

The sources (`open_source`):

- `mini` is the frozen mini bundle under `rebuild/review/fixtures/mini/`, its spec materialized from `pin.json` and its subset tables packed under `var/rust-port/mini-source/` (`_mini_source`), as the `mini_bundle` fixture in `rebuild/conftest.py` does it under its temporary root, followed by a copy of each of its units under `ss10` alone, with an ordinal after the bundle's (`_ss10_copy`). The bundle's audit holds no `ss10` row, and the copies are what reach the isolated overlay, the `ss10` gate, the `ss10` subset rows and the Junior check on the mini. Every handout is taken.
- `live` is the last cycle's corpus under `rebuild/out/m1/` and `rebuild/out/review/`, read only and loaded as the build loads it (`build._load_corpus`). A live run takes `--per-config K` handouts from the start of each first configuration's stretch of the pool's queue (`build._configuration_order`), `--batches N` evenly spaced handouts, or `--all`. It refuses a stale subset pack or ink-signature store rather than rebuild either, since `rebuild/out/` is read only here.

Handouts are `build.PHASE1_HANDOUT_UNITS` wide, cut from the queue in configuration order, and each is passed to the worker through multiprocessing's pickler, as a pool worker receives it.

Run from the repository root:

    uv run python -m rebuild.tools.units_stage_fixtures dump --source mini --out var/rust-port/stages/mini
    uv run python -m rebuild.tools.units_stage_fixtures dump --source live --per-config 1 --out var/rust-port/stages/live
    uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source mini
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import hashlib
import importlib
import json
import pickle
import shutil
import sys
import tempfile
import warnings
from array import array
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import batched
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import IO

from rebuild.pipeline import fingerprint
from rebuild.review import audit, build, unit_cache
from rebuild.review.audit import ACCEPTANCE_CONFIGS, Unit, UnitTable, load_ledger
from rebuild.review.drafts import corpus_index_from_pages
from rebuild.review.enrich import LETTERS, load_spec
from rebuild.review.fixtures.mini import pin
from rebuild.review.subset_pack import (
    PACK_NAME,
    _header_disagreement,
    _read_header,
    table_digests,
    write_pack_in_memory,
)
from rebuild.review.unit_store import UnitStore

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
    "Enricher.enrich": ("rebuild.review.enrich", "Enricher.enrich"),
    "Drafter.draft_pin": ("rebuild.review.drafts", "Drafter.draft_pin"),
    "Drafter.draft_policy": ("rebuild.review.drafts", "Drafter.draft_policy"),
    "Drafter.draft_any_of": ("rebuild.review.drafts", "Drafter.draft_any_of"),
    "unit_to_json": ("rebuild.review.build", "unit_to_json"),
    "_fragment_bytes": ("rebuild.review.build", "_fragment_bytes"),
    "check_unit": ("rebuild.review.build", "check_unit"),
    "Shaper.shape": ("rebuild.validation.shaping", "Shaper.shape"),
}


# --- the typed encoding -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Record:
    """A dataclass or named tuple as the typed encoding carries it: its class name and its fields in declaration order."""

    name: str
    fields: tuple[tuple[str, object], ...]


def encode(value: object) -> object:
    """Return `value` in the typed JSON encoding `PyValue` reads: an int, a string, a boolean or None as itself, `{"tuple": [...]}`, `{"list": [...]}`, `{"dict": [[key, value], ...]}` in insertion order, `{"bytes": hex}`, `{"set": [...]}` for a set or frozenset with its members sorted by their encoded JSON text, and `{"record": [class name, [[field, value], ...]]}` for a dataclass, a named tuple or a `Record`. Any other type raises TypeError, a float and a subclass of a listed type among them, so nothing crosses as a value the encoding cannot give back."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return value
    if isinstance(value, Record):
        return {"record": [value.name, [[name, encode(item)] for name, item in value.fields]]}
    if isinstance(value, tuple) and hasattr(kind, "_fields"):
        names = getattr(kind, "_fields")
        return {"record": [kind.__name__, [[name, encode(item)] for name, item in zip(names, value)]]}
    if isinstance(value, tuple) and kind is tuple:
        return {"tuple": [encode(item) for item in value]}
    if isinstance(value, list) and kind is list:
        return {"list": [encode(item) for item in value]}
    if isinstance(value, dict) and kind is dict:
        return {"dict": [[encode(key), encode(item)] for key, item in value.items()]}
    if isinstance(value, bytes) and kind is bytes:
        return {"bytes": value.hex()}
    if isinstance(value, (set, frozenset)) and kind in (set, frozenset):
        return {"set": sorted((encode(item) for item in value), key=_text)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "record": [
                kind.__name__,
                [[item.name, encode(getattr(value, item.name))] for item in dataclasses.fields(value)],
            ]
        }
    raise TypeError(f"the typed encoding has no form for a {kind.__qualname__}: {value!r:.200}")


def decode(data: object) -> object:
    """Return the value `encode` wrote as `data`, after a JSON round trip: tuples, lists, dicts and bytes as themselves, a set as a frozenset, and a record as a `Record`. Anything `encode` never writes raises ValueError."""
    if data is None or type(data) in (bool, int, str):
        return data
    if type(data) is not dict or len(data) != 1:
        raise ValueError(f"not a typed value: {data!r:.200}")
    ((tag, body),) = data.items()
    if tag == "tuple" and type(body) is list:
        return tuple(decode(item) for item in body)
    if tag == "list" and type(body) is list:
        return [decode(item) for item in body]
    if tag == "dict" and type(body) is list:
        return {decode(key): decode(item) for key, item in body}
    if tag == "bytes" and type(body) is str:
        return bytes.fromhex(body)
    if tag == "set" and type(body) is list:
        return frozenset(decode(item) for item in body)
    if tag == "record" and type(body) is list and len(body) == 2:
        name, fields = body
        return Record(name, tuple((field_name, decode(item)) for field_name, item in fields))
    raise ValueError(f"not a typed value: {data!r:.200}")


def _text(encoded: object) -> str:
    return json.dumps(encoded, ensure_ascii=True, separators=(",", ":"))


def same(left: object, right: object) -> bool:
    """Whether two values are equal under the typed encoding: of one kind (a tuple is not a list, a bool is not an int, a record matches a record of its class name and fields), a dict's entries in one order, and a set's members as a set."""
    return _text(encode(left)) == _text(encode(right))


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


# --- the units file -----------------------------------------------------------------------------


def unit_record(unit: Unit) -> dict:
    """Return one unit as the batch request carries it (the plan's § Batches in). The machine flags and the ink deltas do not cross, so a unit that carries deltas is refused rather than sent without them."""
    if unit.ink_deltas:
        raise ValueError(f"unit {unit.ordinal} carries ink deltas, which a materialized unit never does")
    return {
        "ordinal": unit.ordinal,
        "codepoints": unit.codepoints,
        "new": list(unit.new),
        "class": unit.class_id,
        "configs": list(unit.configs),
        "kinds": list(unit.kinds),
        "group": unit.group,
        "exemplar": unit.exemplar,
        "unit_id": unit.unit_id,
        "input_key": unit.input_key,
        "render_groups": [list(group) for group in unit.render_groups],
        "no_verdict": unit.no_verdict,
        "config_classes": [[config, class_id] for config, class_id in unit.config_classes.items()],
        "duplicate_group": unit.duplicate_group,
        "cluster": unit.cluster,
        "ledger_class": unit.ledger_class,
    }


def write_units_file(source: Source, out: Path) -> None:
    """Write the source's handouts as `batch` request frames, one compact JSON line each, to `out/units.ndjson`, and its description to `out/source.json`."""
    with (out / UNITS_FILE).open("w", encoding="ascii") as handle:
        for index, handout in enumerate(source.handouts):
            frame = {
                "op": "batch",
                "batch": index,
                "units": [unit_record(unit) for unit in source.units(handout)],
            }
            handle.write(_text(frame) + "\n")
    (out / SOURCE_FILE).write_text(json.dumps(source.describe(), indent=1) + "\n", encoding="ascii")


# --- stages and the wrapped run -----------------------------------------------------------------

# A recorder sees one call's positional and keyword arguments before the call runs and returns None, to record nothing, or a finisher, which receives the call's result and returns the fields of the line to record, or None.
Finisher = Callable[[object], Mapping[str, object] | None]
Recorder = Callable[[tuple, dict], Finisher | None]


@dataclass(frozen=True)
class Stage:
    """One stage of the port: its recorders by hook name (`HOOKS`), and its generated inputs, which give the fields of each further line for a source."""

    name: str
    recorders: Mapping[str, Recorder]
    generated: Callable[[Source], Iterator[Mapping[str, object]]] | None = None


# The stage registry. The task that ports a stage adds the stage here (the plan's § Working rules).
STAGES: dict[str, Stage] = {}


class _Sink:
    """The open `STAGE.ndjson` files of one run under `out`, each line a JSON object whose `from` says whether the run or the stage's generator produced it and whose other fields are typed values."""

    def __init__(self, out: Path, stages: Sequence[Stage]) -> None:
        self._handles: dict[str, IO[str]] = {
            stage.name: (out / f"{stage.name}.ndjson").open("w", encoding="ascii") for stage in stages
        }

    def write(self, stage: str, origin: str, fields: Mapping[str, object]) -> None:
        line = {"from": origin} | {name: encode(value) for name, value in fields.items()}
        self._handles[stage].write(_text(line) + "\n")

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
                sink.write(name, "run", fields)
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
            text = _text(encode(projection))
            digest.update(text.encode("ascii") + b"\n")
            if not same(projection, decode(json.loads(text))):
                result.unfaithful.append(projection.ordinal)
            result.units += 1
        digest.update(_text(encode(contract_errors)).encode("ascii") + b"\n")
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
    args = parser.parse_args(argv)
    stages = _selected(args.stage) if args.command == "dump" else []
    source = open_source(args.source, args.per_config, args.batches, args.everything)
    if args.command == "dump":
        dump(source, stages, args.out)
        return 0
    return 0 if selfcheck(source) else 1


if __name__ == "__main__":
    raise SystemExit(main())
