"""The kernel boundary's serializations (the Rust port of the M1 settlement kernel, issue 40): every format the future Rust kernel reads or writes, authored and tested while both sides are still Python, so each later step of the port is measured against a contract that was frozen before the port began.

The boundary has two halves and this module carries both. The resolved-spec dump — `spec_json` / `spec_of` and their file wrappers — carries a whole `model.ResolvedSpec`, `spec_load`'s entire product of runes and registry, through canonical JSON and back: it is what the kernel reads before it starts. The enriched transition stream — `write_transitions` / `read_transitions` — carries a whole `table.FixpointProduct`: it is what the kernel hands back. Neither is the windows artifact, whose label-grain rows are a projection for the sweep rather than a boundary.

Canonical here means byte-reproducible, not merely value-equivalent: every dataclass field is emitted explicitly (defaults included, `None` as `null`), separators are compact, the encoding is ASCII, and every mapping rides in its own iteration order. Never `sort_keys`. Collection order inside a resolved spec is load-bearing in three separate places — stance declaration order ranks candidates, exit declaration order is the structural floor's final tiebreak, and a rune's policy lists gather in declaration order — so a key-sorted dump would quietly describe a different alphabet from the one `spec_load` resolved. The one place order genuinely carries nothing is a resolved membership set (`Policy.groups`, `ScriptRegistry.predicate_classes`), whose `frozenset` values have no order to lose; those are emitted sorted precisely so the dump stays stable across runs of the same sources.

The codec is driven by `dataclasses.fields` and the resolved type hints rather than a hand-written field list. A field added to `model.py` therefore rides the dump with no edit here — which matters, because that module's docstring already calls any change to it a cross-group coordination event, and a dump that silently omitted a new field would make the Rust side disagree with Python about what a spec is. A type shape the codec has no rule for raises at dump time instead of dropping the field into silence, and so does a value whose runtime shape has drifted from its declared one. `Provenance` is the single leaf spelled by hand, as `[file, path]`.

Fidelity is total: the prose fields (`ductus`, `notes`, `why`) ride along verbatim even though settlement never reads one, because the dump is the resolved tree rather than a kernel-shaped projection of it. A consumer that wants less is free to drop what it does not need; the boundary itself keeps everything, so a dump plus this module reconstructs the spec `spec_load` produced.

The transition stream is the return leg, and it is the one shape that carries a whole product across in a way a windows file cannot: the windows artifact records the label view of each row alongside rules the crate had already folded, and nothing reading it back can re-derive those rules, because the rule fold reads per-transition provenance and joints while the treaty fold reads the settled cell, seam, and extension the TSV drops. What the stream carries is the whole `FixpointProduct` at exactly the grain the fixpoint left it in: rows in the product's own key order, deep right slots still at class grain, `joint` still the trace's `joint_floor` with the prospect-divergence pass unrun, plus the deep-class map those class tokens resolve through, the provenance the engine fired, and the reachable cells stated at that same class grain. It is what a consumer of the enumeration's own grain would read: the crate folds its product where it stands, so no build stage and no tool asks for the stream today, and `rebuild/test_kernel_io.py` and `rebuild/test_kernel_exec.py` are what keep the round trip honest.

Its layout follows the artifact idiom next door: gzip with a zeroed stamp, a `# ams-m1-transitions/1\\t<head json>` first line, then one compact JSON array per transition. A cell is spelled once in the head and referenced from the rows by its index there, sorted the way `table._cell_key` sorts the windows file's cells — and every row's settled cell, plus every non-None left-settled one, must be among them, checked while writing so a kernel that invented a cell fails at the boundary rather than deep inside the fold. Order is load-bearing on this side too: the rows keep the key order the fold expands and flags in — `fold::assert_key_sorted` refuses a product that lost it — and a row's provenance keeps the first-seen order the rule fold joins pointers in.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from rebuild.pipeline.model import CellId, Provenance, ResolvedSpec, Rune, Settled
from rebuild.pipeline.table import FixpointProduct, PartitionError, Transition, _cell_key

SPEC_FORMAT = "ams-m1-spec/1"
TRANSITIONS_FORMAT = "ams-m1-transitions/1"

_NONE_TYPE = type(None)
_HINTS: dict[Any, dict[str, Any]] = {}


def _hints(cls: Any) -> dict[str, Any]:
    cached = _HINTS.get(cls)
    if cached is None:
        cached = typing.get_type_hints(cls)
        _HINTS[cls] = cached
    return cached


def _optional_member(hint: Any) -> Any:
    """The one non-None member of an optional hint, or None when the hint is not a union at all. Any other union raises: the spec tree has no shape-discriminated unions, and one arriving without a codec rule written for it must fail rather than have a member guessed for it."""
    if typing.get_origin(hint) not in (types.UnionType, typing.Union):
        return None
    members = typing.get_args(hint)
    named = [member for member in members if member is not _NONE_TYPE]
    if len(named) != 1 or len(named) != len(members) - 1:
        raise TypeError(f"kernel_io has no rule for the union {hint!r}")
    return named[0]


def _mismatch(hint: Any, value: Any) -> TypeError:
    return TypeError(f"kernel_io: a {type(value).__name__} does not fit the declared {hint!r}")


def _unserializable(hint: Any) -> TypeError:
    return TypeError(f"kernel_io has no rule for the type {hint!r}")


def _encode(hint: Any, value: Any) -> Any:
    member = _optional_member(hint)
    if member is not None:
        return None if value is None else _encode(member, value)
    origin = typing.get_origin(hint)
    if origin is None:
        if dataclasses.is_dataclass(hint):
            cls: Any = hint
            if not isinstance(value, cls):
                raise _mismatch(hint, value)
            if hint is Provenance:
                return [value.file, value.path]
            hints = _hints(hint)
            return {
                field.name: _encode(hints[field.name], getattr(value, field.name))
                for field in dataclasses.fields(hint)
            }
        if hint is bool or hint is str:
            if not isinstance(value, hint):
                raise _mismatch(hint, value)
            return value
        if hint is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise _mismatch(hint, value)
            return value
        raise _unserializable(hint)
    args = typing.get_args(hint)
    if origin is tuple:
        if not isinstance(value, tuple):
            raise _mismatch(hint, value)
        if len(args) == 2 and args[1] is Ellipsis:
            return [_encode(args[0], item) for item in value]
        if len(args) != len(value):
            raise _mismatch(hint, value)
        return [_encode(arg, item) for arg, item in zip(args, value)]
    if origin is frozenset:
        if not isinstance(value, frozenset):
            raise _mismatch(hint, value)
        return sorted(_encode(args[0], item) for item in value)
    if origin is Mapping or origin is dict:
        if not isinstance(value, Mapping):
            raise _mismatch(hint, value)
        if args[0] is not str:
            raise _unserializable(hint)
        for key in value:
            if not isinstance(key, str):
                raise _mismatch(hint, key)
        return {key: _encode(args[1], item) for key, item in value.items()}
    raise _unserializable(hint)


def _decode(hint: Any, value: Any) -> Any:
    member = _optional_member(hint)
    if member is not None:
        return None if value is None else _decode(member, value)
    origin = typing.get_origin(hint)
    if origin is None:
        if dataclasses.is_dataclass(hint):
            cls: Any = hint
            if hint is Provenance:
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError(f"kernel_io: a provenance is a [file, path] pair, not {value!r}")
                return Provenance(_decode(str, value[0]), _decode(str, value[1]))
            if not isinstance(value, dict):
                raise ValueError(
                    f"kernel_io: a {cls.__name__} is a JSON object, not a {type(value).__name__}"
                )
            hints = _hints(hint)
            names = [field.name for field in dataclasses.fields(hint)]
            if set(value) != set(names):
                raise ValueError(f"kernel_io: a {cls.__name__} carries exactly the fields {sorted(names)}")
            return cls(**{name: _decode(hints[name], value[name]) for name in names})
        if hint is bool or hint is str:
            if not isinstance(value, hint):
                raise ValueError(f"kernel_io: expected {hint.__name__}, got {type(value).__name__}")
            return value
        if hint is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"kernel_io: expected int, got {type(value).__name__}")
            return value
        raise _unserializable(hint)
    args = typing.get_args(hint)
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError(f"kernel_io: expected a JSON array for {hint!r}, got {type(value).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in value)
        if len(args) != len(value):
            raise ValueError(f"kernel_io: {hint!r} takes {len(args)} entries, got {len(value)}")
        return tuple(_decode(arg, item) for arg, item in zip(args, value))
    if origin is frozenset:
        if not isinstance(value, list):
            raise ValueError(f"kernel_io: expected a JSON array for {hint!r}, got {type(value).__name__}")
        return frozenset(_decode(args[0], item) for item in value)
    if origin is Mapping or origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"kernel_io: expected a JSON object for {hint!r}, got {type(value).__name__}")
        if args[0] is not str:
            raise _unserializable(hint)
        return {key: _decode(args[1], item) for key, item in value.items()}
    raise _unserializable(hint)


def rune_payload(rune: Rune) -> Any:
    """One rune as the dump spells it — the codec's own view, mappings in their own order — for a caller that wants to hash a rune's resolved content rather than its file: `run_m1.rune_content_digests` reads records with every cross-file `against:` already resolved in and every ligature-transparent left already expanded, which is exactly what the crate reads, so a digest over this moves when what the engine reads moves and not otherwise."""
    return _encode(Rune, rune)


def spec_json(spec: ResolvedSpec) -> str:
    """The canonical dump of a resolved spec: `{"format": SPEC_FORMAT, "runes": ..., "registry": ...}`, compact separators, ASCII, mappings in their own order and never sorted. Two calls on one spec return identical text, and the text of a spec that round-tripped through `spec_of` is identical to the text it came from — the fixpoint the Rust side will diff against. Raises TypeError when a field's type or runtime shape is one the codec has no rule for, so a change to `model.py` that outgrows this codec fails at the boundary instead of dumping a spec with a field missing."""
    return json.dumps({"format": SPEC_FORMAT, **_encode(ResolvedSpec, spec)}, separators=(",", ":"))


def spec_of(text: str) -> ResolvedSpec:
    """The `spec_json` inverse: the frozen dataclass tree back in the exact types `model.py` declares — tuples rather than lists, frozensets rather than sorted lists, plain dicts holding the dump's key order, which is the order the spec was resolved in. Raises ValueError when the format marker is missing or names another format, and when a record's fields are not exactly the ones its dataclass declares; a dump from an older `model.py` is a wrong dump, not a partial one."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"not an {SPEC_FORMAT} dump: the text is not a JSON object")
    if payload.get("format") != SPEC_FORMAT:
        raise ValueError(f"not an {SPEC_FORMAT} dump: format marker is {payload.get('format')!r}")
    return _decode(ResolvedSpec, {key: item for key, item in payload.items() if key != "format"})


def write_spec(spec: ResolvedSpec, path: Path) -> None:
    """Write one canonical dump as plain text — no gzip, unlike the artifacts under `rebuild/out/`: a spec dump is read once at kernel start and is worth being greppable and diffable in the tree it is dumped into."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec_json(spec) + "\n")


def read_spec(path: Path) -> ResolvedSpec:
    """The `write_spec` inverse. Raises OSError when the file is absent and ValueError when its contents are not a dump this build understands."""
    return spec_of(path.read_text())


def write_transitions(product: FixpointProduct, path: Path) -> None:
    """Serialize one configuration's fixpoint product: a head line carrying the config, the reachable cells sorted as the windows file sorts them, the deep-class map and the cited provenance, then one compact JSON row per transition in the product's own key order. Rows name their settled cell by its index in the head, so the cell vocabulary is spelled once however many rows land in it; a row naming a cell the product does not carry raises `PartitionError` here, at the boundary, rather than surfacing later as a disagreement between the fold's cells and the product's. Byte-stable like the artifacts beside it — sorted head collections, a zeroed gzip stamp — so two writes of one product are identical files."""
    cells = sorted(product.cells, key=_cell_key)
    seats = {cell: seat for seat, cell in enumerate(cells)}
    head = {
        "config": product.config,
        "cells": [[cell.rune, cell.stance, cell.entry, cell.exit, list(cell.adjustments)] for cell in cells],
        "deep_classes": [[token, list(members)] for token, members in sorted(product.deep_classes.items())],
        "cited_provenance": sorted(product.cited_provenance),
    }

    def seated(settled: Settled, row: Transition, relation: str) -> list[Any]:
        seat = seats.get(settled.cell)
        if seat is None:
            raise PartitionError(
                f"the transition {row.key} {relation} {_cell_key(settled.cell)}, which the product does not count among its reachable cells"
            )
        return [seat, settled.seam, settled.extension]

    body = "".join(
        json.dumps(
            [
                row.input_glyph,
                row.left,
                row.right1,
                row.right2,
                row.right3,
                row.right4,
                row.outcome,
                seated(row.settled, row, "settles into"),
                (
                    None
                    if row.left_settled is None
                    else seated(row.left_settled, row, "carries the left-settled cell")
                ),
                row.joint,
                row.prospect,
                list(row.provenance),
            ],
            separators=(",", ":"),
        )
        + "\n"
        for row in product.transitions
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
        handle.write(f"# {TRANSITIONS_FORMAT}\t{json.dumps(head, separators=(',', ':'))}\n".encode())
        handle.write(body.encode())


def read_transitions(source: Path | IO[str]) -> FixpointProduct:
    """The `write_transitions` inverse: a `FixpointProduct` equal to the one written. Every label — glyph names, heights, class tokens, provenance pointers — is interned to one instance the way `table.read_windows` interns its rows, and the settled (cell, seam, extension) triples pool the same way, because a stream states the same few hundred names on every one of its rows and a parsed product otherwise costs several times the resident size of the one the fixpoint built. Raises OSError when the file is absent and ValueError when it is not a stream this build understands.

    A path is opened as the gzip every artifact under `rebuild/out/` wears; an already-open text stream is read as it stands, which is how a caller holding the crate's plain ndjson — `kernel_exec.read_stream` — reads it without first packing hundreds of megabytes into a shape the reader would only unpack again.
    """
    if isinstance(source, Path):
        with gzip.open(source, "rt") as handle:
            return _transitions_of(handle, str(source))
    return _transitions_of(source, str(getattr(source, "name", source)))


def _transitions_of(handle: IO[str], name: str) -> FixpointProduct:
    marker, _, payload = handle.readline().rstrip("\n").partition("\t")
    if marker != f"# {TRANSITIONS_FORMAT}":
        raise ValueError(f"{name}: not a {TRANSITIONS_FORMAT} stream")
    head = json.loads(payload)
    pool: dict[str, str] = {}

    def label(value: str) -> str:
        return pool.setdefault(value, value)

    def optional(value: str | None) -> str | None:
        return None if value is None else label(value)

    cells = [
        CellId(
            label(rune),
            label(stance),
            optional(entry),
            optional(exit_),
            tuple(label(token) for token in adjustments),
        )
        for rune, stance, entry, exit_, adjustments in head["cells"]
    ]

    settled_pool: dict[tuple[int, str | None, int], Settled] = {}

    def settled_of(triple: list[Any]) -> Settled:
        seat, seam, extension = triple
        key = (seat, seam, extension)
        settled = settled_pool.get(key)
        if settled is None:
            settled = Settled(cells[seat], optional(seam), extension)
            settled_pool[key] = settled
        return settled

    transitions = []
    for line in handle:
        *window, settled, left_settled, joint, prospect, provenance = json.loads(line)
        transitions.append(
            Transition(
                *(label(slot) for slot in window),
                settled=settled_of(settled),
                left_settled=None if left_settled is None else settled_of(left_settled),
                joint=joint,
                prospect=prospect,
                provenance=tuple(label(pointer) for pointer in provenance),
            )
        )
    return FixpointProduct(
        config=head["config"],
        transitions=tuple(transitions),
        deep_classes={
            label(token): tuple(label(member) for member in members)
            for token, members in head["deep_classes"]
        },
        cited_provenance=frozenset(label(pointer) for pointer in head["cited_provenance"]),
        cells=frozenset(cells),
    )
