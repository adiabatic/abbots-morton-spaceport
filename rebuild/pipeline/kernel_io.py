"""Serialization at the boundary between the Python pipeline and the Rust kernel (`rebuild/kernel-rs/`): the resolved-spec dump the kernel reads and the transition stream it writes.

The resolved-spec dump (`spec_json` / `spec_of` and the file wrappers `write_spec` / `read_spec`) carries a whole `model.ResolvedSpec` through canonical JSON and back. The transition stream (`write_transitions` / `read_transitions`) carries a whole `table.FixpointProduct`. The windows artifact that `table.read_windows` reads is a separate format.

Canonical means byte-reproducible. Every dataclass field is written, defaults included and `None` as `null`. Separators are compact and the output is ASCII. Mappings keep their iteration order and are never key-sorted, because collection order in a resolved spec affects settlement: stance declaration order ranks the stances `policy.order` omits, exit declaration order is the structural floor's final tiebreak, and a rune's policy lists are gathered in declaration order. The resolved membership sets (the values of `Policy.groups` and `ScriptRegistry.predicate_classes`) are `frozenset`s and have no order, so they are written sorted to keep the dump stable across runs.

The codec walks `dataclasses.fields` and the resolved type hints, so a field added to `model.py` enters the dump with no edit here. A type the codec has no rule for, or a value whose runtime type does not match its declared type, raises at dump time instead of being dropped. `Provenance` is the one type written by hand, as `[file, path]`.

The dump includes the prose fields (`ductus`, `notes`, `why`) verbatim even though settlement does not read them, so a dump read back through this module reproduces the spec `spec_load` built.

The transition stream carries what the windows artifact cannot. The windows artifact keeps each row's labels and outcome beside rules the crate has already folded, and those folds cannot be rerun from it: the rule fold reads each transition's provenance and joint flag, and the treaty fold reads the settled cell, seam, and extension, none of which the windows TSV keeps. The stream carries the `FixpointProduct` at the grain the fixpoint produced it: rows in the product's key order, deep right slots still at class grain, and `joint` still the trace's `joint_floor` before the prospect-divergence pass, plus the deep-class map, the provenance the engine fired, and the reachable cells at the same class grain. Only the rebuild tests parse the stream, through `kernel_exec.enumerate_transitions`; the build's `build-tables` folds the product in memory without writing a stream. `rebuild/test_kernel_io.py` and `rebuild/test_kernel_exec.py` test the round trip.

Stream layout: gzip with a zeroed timestamp, a first line `# ams-m1-transitions/1\\t<head json>`, then one compact JSON array per transition. Each cell is written once in the head, sorted by `table._cell_key` as the windows file sorts its cells, and rows refer to it by index. Every row's settled cell, and every non-None left-settled cell, must be in the head. The writer checks this, so a kernel that produced an unknown cell fails here instead of inside the fold. Order matters in the stream too: rows keep the key order the fold expands and flags in (`fold::assert_key_sorted` fails on a product out of that order), and each row's provenance keeps the first-seen order the rule fold joins pointers in.
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
    """Returns the non-None member of an optional hint, or None when the hint is not a union. Raises TypeError for any other union, because the codec has no rule for choosing which member a value belongs to."""
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
    """Returns one rune in the dump's form, for hashing a rune's resolved content instead of its file. The resolved rune already has cross-file `against:` targets and ligature-transparent lefts resolved in, as the crate reads it. The payload includes the prose fields, so `run_m1.rune_content_digests` removes `run_m1.PROSE_KEYS` before hashing it."""
    return _encode(Rune, rune)


def spec_json(spec: ResolvedSpec) -> str:
    """Returns the canonical dump of a resolved spec, `{"format": SPEC_FORMAT, "runes": ..., "registry": ...}`. Two calls on one spec return identical text, and a spec read back through `spec_of` dumps to the same text. The crate's `spec-echo` must reproduce this text byte for byte (`rebuild/test_kernel_io.py`). Raises TypeError when a field's declared type or runtime value has no codec rule, so a `model.py` change the codec cannot handle fails here instead of producing a dump with a field missing."""
    return json.dumps({"format": SPEC_FORMAT, **_encode(ResolvedSpec, spec)}, separators=(",", ":"))


def spec_of(text: str) -> ResolvedSpec:
    """Parses a `spec_json` dump back into the dataclass tree, with the container types `model.py` declares: tuples, frozensets, and dicts in the dump's key order. Raises ValueError when the format marker is missing or wrong, or when a record's fields are not exactly its dataclass's fields, so a dump from an older `model.py` fails instead of loading partially."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"not an {SPEC_FORMAT} dump: the text is not a JSON object")
    if payload.get("format") != SPEC_FORMAT:
        raise ValueError(f"not an {SPEC_FORMAT} dump: format marker is {payload.get('format')!r}")
    return _decode(ResolvedSpec, {key: item for key, item in payload.items() if key != "format"})


def write_spec(spec: ResolvedSpec, path: Path) -> None:
    """Writes the canonical dump as plain text with a trailing newline. It is not gzipped like the artifacts under `rebuild/out/`, so it can be read with grep and diff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec_json(spec) + "\n")


def read_spec(path: Path) -> ResolvedSpec:
    """The `write_spec` inverse. Raises OSError when the file is absent and ValueError when its contents are not a dump this build understands."""
    return spec_of(path.read_text())


def write_transitions(product: FixpointProduct, path: Path) -> None:
    """Writes one configuration's fixpoint product as an `ams-m1-transitions/1` stream, laid out as the module docstring describes. The head holds the configuration, the reachable cells, the deep-class map, and the cited provenance. Raises `PartitionError` when a row's settled or left-settled cell is not among the product's cells. Two writes of one product produce identical files."""
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
    """Reads a `write_transitions` stream back into an equal `FixpointProduct`. Every label (glyph names, heights, class tokens, provenance pointers) is interned to one string object, as `table.read_windows` does, and equal settled (cell, seam, extension) triples share one `Settled`. A stream repeats a small vocabulary on every row, and without this pooling a parsed product takes several times the memory of the one the fixpoint built. Raises OSError when the file is absent and ValueError when it is not a stream this build understands.

    A `Path` is opened as gzip. An open text stream is read as it is, which lets `kernel_exec.read_stream` read the crate's plain ndjson output without compressing it first.
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
