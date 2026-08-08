"""The persisted trace memo (issue 25): the Engine-level fixpoint memo carried across cycles, keyed by per-rune digests, so a single-rune edit re-traces the windows that could feel it and serves everything else from the previous build.

`build_tables` already memoizes `transition_trace` over the collapsed left key for one run and drops the pile before returning. This module writes that memo beside the other per-config artifacts — one entry per key, carrying exactly the fields the table build consumes (the settled cell, seam and extension; the joint floor; the prospect; the provenance notes) plus the fired-pointer delta the evaluation journaled — and serves it back to the next build's engine. The fixpoint itself always runs live: reachability, formation admissibility and the deep-slot filters are recomputed from the current spec, so a served entry can only ever short-circuit the kernel call for a key the fixpoint independently decided to visit, never resurrect a window the current spec no longer reaches.

Invalidation is two-grained, and the split is exactly the split between what an entry's evaluation reads through the runes it names and what it reads through the resolved spec around them. Per entry: the digests of the rune files the key names (the left cell's rune, the input, and every letter-valued right slot) plus their static `resolve.against` closure — the one route by which a named rune's records read another file's content directly. Whole store: everything else that can move a trace without moving a named rune's digest — the non-rune data inputs and the pipeline code (`fingerprint.tables_environment_value`), the engine's semantics flags, and the resolved spec structure (`spec_structure_digest`: the alphabet and its ligature sequences, which feed formation and the left-expansion of every other rune's conditions, and the resolved predicate-class and group memberships, which other runes' surface edits can move). A membership or alphabet change therefore drops the whole memo rather than trusting a closure computation to trace it — over-invalidation is the safe direction, and those changes are migration-shaped, not edit-loop-shaped.

The fired deltas exist for the dead-policy gate: `DecisionTable.cited_provenance` is `Engine.fired`, and a served entry must fill it exactly as a recomputation would (see the journaling machinery in settle.Engine). The conform sweep and the outcome-partition replay backstop the outcome fields; nothing backstops `fired` but the deltas themselves, which is why they ride every entry.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from rebuild.pipeline import fingerprint
from rebuild.pipeline import settle as settle_module
from rebuild.pipeline.model import CellId, ResolvedSpec, Settled, When
from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, UNKNOWN, ZWNJ, Engine, RightToken, TransitionTrace

STORE_FORMAT = "ams-m1-trace-memo/1"

_BOUNDARY_TOKENS = {
    "edge": EDGE,
    "space": SPACE,
    "zwnj": ZWNJ,
    "namer-dot": NAMER_DOT,
    "unknown": UNKNOWN,
}


def store_path(out_dir: Path, config: str) -> Path:
    return Path(out_dir) / f"trace-memo-{config}.ndjson.gz"


def spec_structure_digest(spec: ResolvedSpec) -> str:
    """The resolved-spec facts a trace can read without consulting any rune file the key names: the alphabet and its ligature sequences (formation pairs, the enumeration set, and `_expand_ligature_lefts`' rewrite of every other rune's left conditions), the registry predicate classes (whose membership every rune's surface contributes to), and the resolved rune-local groups (whose membership can move through class atoms and ligature expansion). Any of these moving invalidates the whole store."""
    payload = {
        "runes": {name: list(rune.sequence) if rune.sequence else None for name, rune in spec.runes.items()},
        "classes": {name: sorted(members) for name, members in spec.registry.predicate_classes.items()},
        "groups": {
            name: {group: sorted(members) for group, members in rune.policy.groups.items()}
            for name, rune in spec.runes.items()
            if rune.policy.groups
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def memo_environment(repo_root: Path) -> str:
    """The whole-store stamp component derived from files and flags rather than the spec: mirrors `run_m1.tables_inputs` with the per-rune digests factored out to entry grain."""
    value = fingerprint.tables_environment_value(repo_root)
    if settle_module.SIMULATED_PROSPECT_DEFAULT:
        value += "+simulated-prospect"
    if settle_module.VOTE_SLOTS_DEFAULT:
        value += "+vote-slots"
    return value


def rune_closure(spec: ResolvedSpec) -> dict[str, frozenset[str]]:
    """For each rune, the runes whose file content its records can read directly: itself, plus the transitive `resolve.against` targets — the one cross-file reference resolved into a rune's policy at load time. Every other cross-rune route rides the resolved spec structure and is stamped whole-store."""
    edges: dict[str, set[str]] = {}
    for name, rune in spec.runes.items():
        targets = set()
        for records in (
            rune.policy.refuse,
            rune.policy.prefer,
            rune.policy.extend,
            rune.policy.contract,
            rune.policy.resolve,
        ):
            for record in records:
                if record.against is not None and record.against[0] in spec.runes:
                    targets.add(record.against[0])
        edges[name] = targets
    closure: dict[str, frozenset[str]] = {}
    for name in spec.runes:
        seen = {name}
        frontier = [name]
        while frontier:
            for target in edges.get(frontier.pop(), ()):
                if target not in seen:
                    seen.add(target)
                    frontier.append(target)
        closure[name] = frozenset(seen)
    return closure


class FeatureSensitivity:
    """Which memo keys can trace differently between two feature configurations (issue 15). The active feature set is read in exactly two places — `unlock.feature` on capability rows and `when.feature` on policy records and unlock `when:`s — and a gate that is off short-circuits to a hard False before the rest of its `when` is consulted, so a key's trace can move between configurations only where some rune the key names owns a record gated on a feature in the symmetric difference whose remaining `when` could evaluate True or None over that key's window. Three grains capture that conservatively. `anywhere` holds the owners whose gate has no verifiable trigger: no `when` at all, an exit unlock (fired at enumeration grain, before any `when` is consulted), a `word:` constraint (unknowable in shifted evaluations), a right condition with `then:`/`except:` hops (which read UNKNOWN past the evaluation site's window), a side with no positive family/class axis, or a `resolve.against` closure that reaches another owner — such an owner marks every key it appears in. `right_triggers` and `left_triggers` map the remaining owners to the first-hop family sets their gates fire toward — positive `family:` axes plus expanded `class:` references, so `except:` carve-outs and non-family axes only ever widen the sensitive set. Right-facing family matching is literal (`cond_matches_right` never lead-expands a ligature), and left-facing lists were already ligature-expanded at load, so the resolved conditions are the right thing to read on both sides. A key is then sensitive when an `anywhere` owner appears at any slot; when a right-trigger owner sits immediately left of a trigger family, an UNKNOWN token, or the window's far edge (shifted evaluations read UNKNOWN there, where the gated condition returns None against the gate-off False); or when a left-trigger owner sits immediately right of a trigger family or at the left slot itself, whose own left lies beyond the key. Boundary tokens are definite non-matches for family conditions and never mark a key sensitive."""

    def __init__(self, spec: ResolvedSpec, delta: frozenset[str]):
        from rebuild.pipeline.specificity import class_members
        from rebuild.pipeline.table import right_chain_reach

        self.anywhere: set[str] = set()
        self.right_triggers: dict[str, frozenset[str]] = {}
        self.left_triggers: dict[str, frozenset[str]] = {}

        def families(cond, owner: str) -> frozenset[str] | None:
            gathered = set(cond.family)
            for name in cond.klass:
                gathered |= class_members(spec, name, owner)
            return frozenset(gathered) if gathered else None

        def classify(owner: str, when: When | None) -> None:
            if when is None or (when.left is None and when.right is None) or when.word is not None:
                self.anywhere.add(owner)
                return
            right = left = None
            if when.right is not None:
                right = families(when.right, owner)
                if right is None or right_chain_reach(when.right) > 0:
                    self.anywhere.add(owner)
                    return
            if when.left is not None:
                left = families(when.left, owner)
                if left is None:
                    self.anywhere.add(owner)
                    return
            if right is not None:
                self.right_triggers[owner] = self.right_triggers.get(owner, frozenset()) | right
            if left is not None:
                self.left_triggers[owner] = self.left_triggers.get(owner, frozenset()) | left

        for name, rune in spec.runes.items():
            for stance in rune.stances.values():
                for unlock in stance.surface.unlocks:
                    if unlock.feature in delta or (unlock.when is not None and unlock.when.feature in delta):
                        classify(name, None if unlock.exit is not None else unlock.when)
            for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
                for record in getattr(rune.policy, kind):
                    if record.when.feature in delta:
                        classify(name, record.when)
        owners = self.anywhere | set(self.right_triggers) | set(self.left_triggers)
        for name, reachable in rune_closure(spec).items():
            if (reachable & owners) - {name}:
                self.anywhere.add(name)

    def key_shared(self, key: tuple) -> bool:
        slots: list[str | RightToken | None] = [key[1] if key[0] == "letter" else None, key[5]]
        for token in key[6:10]:
            if token.kind == "letter":
                slots.append(token.rune)
            elif token.kind == "unknown":
                slots.append(UNKNOWN)
            else:
                slots.append(None)
        for index, name in enumerate(slots):
            if not isinstance(name, str):
                continue
            if name in self.anywhere:
                return False
            triggers = self.right_triggers.get(name)
            if triggers is not None:
                successor = slots[index + 1] if index + 1 < len(slots) else UNKNOWN
                if successor is UNKNOWN or (isinstance(successor, str) and successor in triggers):
                    return False
            triggers = self.left_triggers.get(name)
            if triggers is not None:
                if index == 0:
                    return False
                predecessor = slots[index - 1]
                if predecessor is UNKNOWN or (isinstance(predecessor, str) and predecessor in triggers):
                    return False
        return True


class _TraceShareReader:
    """The donor-side view one recipient configuration's engine consults: the donor's finished in-memory memo behind that recipient's `FeatureSensitivity` gate. Serving replays the donor's journaled fired delta — an insensitive key's evaluation is identical under both configurations, records consulted, declined and fired alike, so the donor's delta is exactly what a recomputation would journal."""

    def __init__(
        self,
        cache: Mapping[tuple, TransitionTrace],
        fired: Mapping[tuple, tuple[str, ...]],
        sensitivity: FeatureSensitivity,
    ):
        self._cache = cache
        self._fired = fired
        self._sensitivity = sensitivity
        self.served = 0

    def get(self, key: tuple) -> tuple[TransitionTrace, tuple[str, ...]] | None:
        if not self._sensitivity.key_shared(key):
            return None
        trace = self._cache.get(key)
        if trace is None:
            return None
        self.served += 1
        return trace, self._fired.get(key, ())


class TraceShare:
    """Cross-configuration reuse of one build's finished trace memo within a single process (issue 15): the donor configuration — the default — builds first and `offer` adopts its engine's memo whole; every configuration after it gets a `reader_for` view that serves only the keys whose named runes cannot feel that configuration's feature delta, so a recipient's fixpoint re-traces its sensitive fraction and shares the rest. The fixpoint itself still runs per configuration — reachability and the deep-slot filters are feature-dependent — so a served key is only ever one the recipient independently decided to visit, the same discipline the persisted store keeps. `release` drops the adopted memo when the run is done, because engines outlive builds in module-level caches and the pile is most of a build's resident weight."""

    def __init__(self, spec: ResolvedSpec, donor_features: frozenset[str] = frozenset()):
        self.spec = spec
        self.donor_features = frozenset(donor_features)
        self.last_reader: _TraceShareReader | None = None
        self._cache: Mapping[tuple, TransitionTrace] | None = None
        self._fired: Mapping[tuple, tuple[str, ...]] | None = None

    def offer(self, engine: Engine) -> bool:
        if self._cache is not None or engine.features != self.donor_features:
            return False
        if engine._trace_cache is None:
            return False
        self._cache = engine._trace_cache
        self._fired = engine._trace_fired
        return True

    def reader_for(self, features: frozenset[str]) -> _TraceShareReader | None:
        if self._cache is None or self._fired is None:
            return None
        delta = frozenset(features) ^ self.donor_features
        if not delta:
            return None
        self.last_reader = _TraceShareReader(self._cache, self._fired, FeatureSensitivity(self.spec, delta))
        return self.last_reader

    def release(self) -> None:
        cache, fired = self._cache, self._fired
        self._cache = None
        self._fired = None
        if isinstance(cache, dict):
            cache.clear()
        if isinstance(fired, dict):
            fired.clear()


def _serialized_token(token: RightToken) -> object:
    return token.rune if token.kind == "letter" else [token.kind]


def _entry_names(key: tuple) -> set[str]:
    names = set()
    if key[0] == "letter" and key[1] is not None:
        names.add(key[1])
    names.add(key[5])
    for token in key[6:10]:
        if token.kind == "letter":
            names.add(token.rune)
    return names


class TraceStore:
    """One configuration's persisted trace memo: `get` serves the previous build's still-valid entries to the engine, `save` rewrites the file from the engine's finished in-memory memo. Loading validates the whole-store stamp and per-entry rune digests up front, so `get` is a plain dict probe; an entry served this run is written back verbatim (same trace, same delta, same digests), which keeps a no-edit rebuild byte-identical to the store it read."""

    def __init__(
        self,
        path: Path,
        stamp: str,
        digests: dict[str, str],
        closure: dict[str, frozenset[str]],
        writable: bool = True,
    ):
        self.path = Path(path)
        self.stamp = stamp
        self.digests = digests
        self.closure = closure
        self.writable = writable
        self.served = 0
        self.saved = 0
        self.loaded = 0
        self._entries: dict[tuple, tuple] = {}
        self._pointers: list[str] = []
        self._cells: list[CellId] = []

    def get(self, key: tuple) -> tuple[TransitionTrace, tuple[str, ...]] | None:
        raw = self._entries.get(key)
        if raw is None:
            return None
        cell_index, seam, extension, joint, prospect, note_indexes, fired_indexes = raw
        trace = TransitionTrace(
            settled=Settled(cell=self._cells[cell_index], seam=seam, extension=extension),
            joint_floor=bool(joint),
            prospect=prospect,
            ranked=(),
            eliminations=(),
            decided_stage="memo",
            runner_up=None,
            notes=tuple(self._pointers[index] for index in note_indexes),
        )
        self.served += 1
        return trace, tuple(self._pointers[index] for index in fired_indexes)

    def load(self) -> None:
        try:
            with gzip.open(self.path, "rt") as handle:
                marker, _, payload = handle.readline().rstrip("\n").partition("\t")
                if marker != f"# {STORE_FORMAT}":
                    return
                head = json.loads(payload)
                if head.get("stamp") != self.stamp:
                    return
                recorded: dict[str, str] = head["rune_digests"]
                self._pointers = head["pointers"]
                self._cells = [
                    CellId(rune, stance, entry, exit_, tuple(adjustments))
                    for rune, stance, entry, exit_, adjustments in head["cells"]
                ]
                set_valid = [
                    all(self.digests.get(name) == recorded.get(name) for name in names)
                    for names in head["rune_sets"]
                ]
                tokens: dict[object, RightToken] = {}

                def token_of(raw: object) -> RightToken:
                    if isinstance(raw, str):
                        token = tokens.get(raw)
                        if token is None:
                            token = tokens.setdefault(raw, RightToken("letter", raw))
                        return token
                    assert isinstance(raw, list)
                    return _BOUNDARY_TOKENS[raw[0]]

                for line in handle:
                    row = json.loads(line)
                    if not set_valid[row[17]]:
                        continue
                    key = (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        token_of(row[6]),
                        token_of(row[7]),
                        token_of(row[8]),
                        token_of(row[9]),
                    )
                    self._entries[key] = (row[10], row[11], row[12], row[13], row[14], row[15], row[16])
        except OSError, EOFError, ValueError, KeyError, IndexError, TypeError:
            self._entries = {}
        self.loaded = len(self._entries)

    def _rows(self, engine: Engine):
        """Every entry the rewritten store keeps, as (key, cell, seam, extension, joint, prospect, notes, fired): the engine's finished memo, plus every still-valid loaded entry the run never consulted — a served parent short-circuits the nested calls that would have consulted them, but they become live again the moment that parent's runes move, and carrying them is also what keeps a no-edit rebuild's store byte-identical to the one it read."""
        cache = engine._trace_cache
        assert cache is not None
        fired_map = engine._trace_fired
        for key, trace in cache.items():
            yield (
                key,
                trace.settled.cell,
                trace.settled.seam,
                trace.settled.extension,
                trace.joint_floor,
                trace.prospect,
                trace.notes,
                fired_map.get(key, ()),
            )
        for key, raw in self._entries.items():
            if key in cache:
                continue
            cell_at, seam, extension, joint, prospect, note_indexes, fired_indexes = raw
            yield (
                key,
                self._cells[cell_at],
                seam,
                extension,
                bool(joint),
                prospect,
                tuple(self._pointers[index] for index in note_indexes),
                tuple(self._pointers[index] for index in fired_indexes),
            )

    def save(self, engine: Engine) -> None:
        if not self.writable or engine._trace_cache is None:
            return
        if self._entries and all(key in self._entries for key in engine._trace_cache):
            # Nothing was computed fresh, so the rewrite would reproduce the loaded file byte for byte — and serialization is expensive enough to dominate a fully-served build, so skip it.
            self.saved = len(self._entries)
            return
        rows = []
        for key, cell, seam, extension, joint, prospect, notes, fired in self._rows(engine):
            names = _entry_names(key)
            if not names <= self.closure.keys():
                continue
            touched = frozenset().union(*(self.closure[name] for name in names))
            rows.append((key, cell, seam, extension, joint, prospect, notes, fired, touched))
        # The intern tables are sorted before indices are assigned, so their order never depends on which entries were computed, served, or carried — a no-edit rebuild reproduces the file it read byte for byte.
        pointers = sorted({pointer for row in rows for group in (row[6], row[7]) for pointer in group})
        cells = sorted(
            {row[1] for row in rows},
            key=lambda c: (c.rune, c.stance, c.entry or "", c.exit or "", c.adjustments),
        )
        rune_sets = sorted({row[8] for row in rows}, key=sorted)
        pointer_index = {pointer: index for index, pointer in enumerate(pointers)}
        cell_index = {cell: index for index, cell in enumerate(cells)}
        set_index = {names: index for index, names in enumerate(rune_sets)}
        lines = []
        for key, cell, seam, extension, joint, prospect, notes, fired, touched in rows:
            lines.append(
                [
                    key[0],
                    key[1],
                    key[2],
                    key[3],
                    key[4],
                    key[5],
                    _serialized_token(key[6]),
                    _serialized_token(key[7]),
                    _serialized_token(key[8]),
                    _serialized_token(key[9]),
                    cell_index[cell],
                    seam,
                    extension,
                    1 if joint else 0,
                    prospect,
                    [pointer_index[note] for note in notes],
                    [pointer_index[pointer] for pointer in fired],
                    set_index[touched],
                ]
            )
        self.saved = len(lines)
        head = {
            "stamp": self.stamp,
            "rune_digests": {name: self.digests[name] for name in sorted(self.digests)},
            "pointers": pointers,
            "cells": [
                [cell.rune, cell.stance, cell.entry, cell.exit, list(cell.adjustments)] for cell in cells
            ],
            "rune_sets": [sorted(names) for names in rune_sets],
        }
        body = sorted(json.dumps(line, separators=(",", ":")) + "\n" for line in lines)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_suffix(self.path.suffix + ".tmp")
        with scratch.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(f"# {STORE_FORMAT}\t{json.dumps(head, separators=(',', ':'))}\n".encode())
            for line in body:
                handle.write(line.encode())
        os.replace(scratch, self.path)


def open_store(
    path: Path,
    spec: ResolvedSpec,
    digests: dict[str, str],
    environment: str,
    config: str,
    fresh: bool = False,
    writable: bool = True,
) -> TraceStore:
    """Build the store for one configuration and load whatever previous entries survive validation. `fresh` skips the read — every window re-traces — while the finished build still rewrites the file, which is the escape hatch's whole point: distrust the pile once, then trust what the distrusting run wrote. `writable=False` serves without ever rewriting, for read-only consumers like the prospect-divergence inventory."""
    stamp = hashlib.sha256(f"{config}\n{environment}\n{spec_structure_digest(spec)}".encode()).hexdigest()
    store = TraceStore(Path(path), stamp, dict(digests), rune_closure(spec), writable=writable)
    if not fresh:
        store.load()
    return store
