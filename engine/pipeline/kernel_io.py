"""Serialization at the kernel interface between the Python pipeline and the Rust kernel (`engine/kernel-rs/`): the resolved-spec dump the kernel reads.

The resolved-spec dump (`spec_json` / `spec_of`, the bytes `spec_bytes` hands `build-tables` through a pipe, and the file wrappers `write_spec` / `read_spec`) carries a whole `model.ResolvedSpec` through canonical JSON and back. What the kernel writes back, the tables and the windows artifact, `table` reads.

Canonical means byte-reproducible. Every dataclass field is written, defaults included and `None` as `null`. Separators are compact and the output is ASCII. Mappings keep their iteration order and are never key-sorted, because collection order in a resolved spec affects settlement: stance declaration order ranks the stances `policy.order` omits, exit declaration order is the final tiebreak's last key, and a rune's policy lists are gathered in declaration order. The resolved membership sets (the values of `Policy.groups` and `ScriptRegistry.predicate_classes`) are `frozenset`s and have no order, so they are written sorted to keep the dump stable across runs.

The codec walks `dataclasses.fields` and the resolved type hints, so a field added to `model.py` enters the dump with no edit here. A type the codec has no rule for, or a value whose runtime type does not match its declared type, raises at dump time instead of being dropped. `Provenance` is the one type written by hand, as `[file, path]`. A field whose metadata sets `model.KERNEL_FIELD` to False is the one kind the codec skips (`_kernel_fields`): the dump leaves it out and a parsed dump gives it its default. Those are the fields `model.py` marks with `KERNEL_FIELD: False`, which its module docstring lists, so none of them reaches a byte the crate reads.

The dump includes the prose fields (`ductus`, `notes`, `why`) verbatim even though settlement does not read them, so a dump read back through this module reproduces the spec `spec_load` built, less the fields the codec skips.
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from engine.pipeline.model import KERNEL_FIELD, Provenance, ResolvedSpec, ScriptRegistry

SPEC_FORMAT = "ams-spec/1"

_NONE_TYPE = type(None)
_HINTS: dict[Any, dict[str, Any]] = {}


def _hints(cls: Any) -> dict[str, Any]:
    cached = _HINTS.get(cls)
    if cached is None:
        cached = typing.get_type_hints(cls)
        _HINTS[cls] = cached
    return cached


def _kernel_fields(cls: Any) -> list[dataclasses.Field]:
    """Returns the dataclass's fields the dump carries, in declaration order: every field but one whose metadata sets `KERNEL_FIELD` to False."""
    return [field for field in dataclasses.fields(cls) if field.metadata.get(KERNEL_FIELD, True)]


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
                for field in _kernel_fields(hint)
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
            names = [field.name for field in _kernel_fields(hint)]
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


def registry_payload(registry: ScriptRegistry) -> Any:
    """Returns the script registry in the dump's form, for hashing what the crate reads of it (`run_build.memo_structure_stamp`)."""
    return _encode(ScriptRegistry, registry)


def spec_json(spec: ResolvedSpec) -> str:
    """Returns the canonical dump of a resolved spec, `{"format": SPEC_FORMAT, "runes": ..., "registry": ...}`. Two calls on one spec return identical text, and a spec read back through `spec_of` dumps to the same text. The crate's `spec-echo` must reproduce this text byte for byte (`engine/test_kernel_io.py`). Raises TypeError when a field's declared type or runtime value has no codec rule, so a `model.py` change the codec cannot handle fails here instead of producing a dump with a field missing."""
    return json.dumps({"format": SPEC_FORMAT, **_encode(ResolvedSpec, spec)}, separators=(",", ":"))


def spec_of(text: str) -> ResolvedSpec:
    """Parses a `spec_json` dump back into the dataclass tree, with the container types `model.py` declares: tuples, frozensets, and dicts in the dump's key order. Raises ValueError when the format marker is missing or wrong, or when a record's fields are not exactly its dataclass's fields, so a dump from an older `model.py` fails instead of loading partially."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"not an {SPEC_FORMAT} dump: the text is not a JSON object")
    if payload.get("format") != SPEC_FORMAT:
        raise ValueError(f"not an {SPEC_FORMAT} dump: format marker is {payload.get('format')!r}")
    return _decode(ResolvedSpec, {key: item for key, item in payload.items() if key != "format"})


def spec_bytes(spec: ResolvedSpec) -> bytes:
    """Returns the canonical dump with a trailing newline as UTF-8 bytes: what `write_spec` writes and what `kernel_exec.build_table_files` hands `build-tables` through an inherited pipe. The dump is ASCII, so the encoding changes no byte."""
    return (spec_json(spec) + "\n").encode("utf-8")


def write_spec(spec: ResolvedSpec, path: Path) -> None:
    """Writes `spec_bytes`, the canonical dump as plain text with a trailing newline, so a file holds exactly what `build-tables` reads through its pipe. It is not gzipped like the artifacts under `engine/out/`, so it can be read with grep and diff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(spec_bytes(spec))


def read_spec(path: Path) -> ResolvedSpec:
    """The `write_spec` inverse. Raises OSError when the file is absent and ValueError when its contents are not a dump this build understands."""
    return spec_of(path.read_text())
