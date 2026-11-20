"""The units engine interface: the Python side of the review crate under `rebuild/review-rs/`, the Rust engine of the corpus build's unit recompute (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § Boundary). It owns every format that crosses to the crate, in the pattern of `rebuild/pipeline/kernel_exec.py`: nothing pickled crosses, only files and compact JSON this module writes.

`engine()` reads the switch, `AMS_CORPUS_UNITS_ENGINE`, at call time; `ENGINES` holds the engines the build runs, and `_RecomputeRunner` refuses any other. `ensure_built` builds the crate once per process under a relink lock in the crate's `target/`, held as `kernel_exec` holds its own, and `spawn` starts the binary with that lock held shared for the spawn only.

`write_init` writes the init file (`INIT_FORMAT`) into a directory the caller holds, beside the spec dump and the preset dictionary, from the `init` dict `_RecomputeRunner.__init__` hands a Python worker; `init_values` gives the values it carries as Python holds them. `encode_batch` and `encode_verify` write the request frames, one unit record per `audit.Unit` (`unit_record`, `UNIT_FIELDS`). Values whose Python type the crate must keep, the vocabulary's among them, cross in the typed encoding (`encode`, `decode`), which the stage dumps of `rebuild/tools/units_stage_fixtures.py` use too.
"""

from __future__ import annotations

import dataclasses
import fcntl
import importlib
import json
import os
import subprocess
import sys
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uharfbuzz

from rebuild.pipeline import kernel_exec, kernel_io
from rebuild.pipeline.labels import load_alias_map
from rebuild.pipeline.model import CellId
from rebuild.review.audit import ACCEPTANCE_CONFIGS, Unit
from rebuild.review.drafts import _import_test_shaping
from rebuild.review.enrich import load_spec
from rebuild.review.subset_pack import PACK_FORMAT, PACKER_DIGEST, table_digests

REPO_ROOT = Path(__file__).resolve().parents[2]
CRATE = REPO_ROOT / "rebuild" / "review-rs"
MANIFEST = CRATE / "Cargo.toml"
BINARY = CRATE / "target" / "release" / "ams-review"
# The review crate's own form of `kernel_exec.LOCK_PATH`, whose comment says why a build and a spawn are ordered on macOS.
LOCK_PATH = CRATE / "target" / ".ams-review-relink.lock"
TIMEOUT = 1800
BUILD_TAIL_LINES = 20

INIT_FORMAT = "ams-review-units-init/1"
INIT_FILE = "init.json"
SPEC_FILE = "spec.json"
DICTIONARY_FILE = "dictionary.bin"
ENGINE_VARIABLE = "AMS_CORPUS_UNITS_ENGINE"
SWITCH_VALUES = ("python", "rust")
ENGINES = ("python",)

# The module-level constants the ported worker reads, each by its dotted name, which is also its name in the init. `vocabulary` reads them at call time, and the engine refuses an init that lacks a name it reads.
VOCABULARY = (
    "rebuild.pipeline.labels.BOUNDARY_GLYPH_NAMES",
    "rebuild.review.audit.ACCEPTANCE_CONFIGS",
    "rebuild.review.audit.MACHINE_CHECKS",
    "rebuild.review.audit.SLIM_OMITTED_KEYS",
    "rebuild.review.build.CONTRACT_ERRORS_SHOWN",
    "rebuild.review.enrich.EXPLAIN_UNIT_BATCH_SIZE",
    "rebuild.review.enrich.LETTERS",
    "rebuild.review.feature_reach.DEFAULT",
)

# The `audit.Unit` fields a unit record carries, in the unit's declaration order, each with its name on the wire. The machine flags and `ink_deltas` do not cross, and phase 1 reads no other field.
UNIT_FIELDS = (
    ("codepoints", "codepoints"),
    ("new", "new"),
    ("class_id", "class"),
    ("configs", "configs"),
    ("kinds", "kinds"),
    ("group", "group"),
    ("exemplar", "exemplar"),
    ("unit_id", "unit_id"),
    ("input_key", "input_key"),
    ("ordinal", "ordinal"),
    ("render_groups", "render_groups"),
    ("no_verdict", "no_verdict"),
    ("config_classes", "config_classes"),
    ("duplicate_group", "duplicate_group"),
    ("cluster", "cluster"),
    ("ledger_class", "ledger_class"),
)


class EngineBuildError(RuntimeError):
    """`cargo` is absent or the review crate did not build."""


class EngineRunError(RuntimeError):
    """The review binary is missing or did not answer as its contract says."""


def engine() -> str:
    """The units engine this process asks for: `AMS_CORPUS_UNITS_ENGINE`, read at call time, `python` when it is unset. A value outside `SWITCH_VALUES` raises ValueError."""
    value = os.environ.get(ENGINE_VARIABLE, "python")
    if value not in SWITCH_VALUES:
        raise ValueError(f"{ENGINE_VARIABLE}={value!r}; the units engines are {', '.join(SWITCH_VALUES)}")
    return value


# --- the typed encoding -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Record:
    """A dataclass or named tuple as the typed encoding carries it: its class name and its fields in declaration order."""

    name: str
    fields: tuple[tuple[str, object], ...]


def encode(value: object) -> object:
    """Return `value` in the typed JSON encoding the crate's `PyValue` reads: an int, a string, a boolean or None as itself, `{"tuple": [...]}`, `{"list": [...]}`, `{"dict": [[key, value], ...]}` in insertion order, `{"bytes": hex}`, `{"set": [...]}` for a set or frozenset with its members sorted by their encoded JSON text, and `{"record": [class name, [[field, value], ...]]}` for a dataclass, a named tuple or a `Record`. Any other type raises TypeError, a float and a subclass of a listed type among them, so nothing crosses as a value the encoding cannot give back."""
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
        return {"set": sorted((encode(item) for item in value), key=compact)}
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


def compact(data: object) -> str:
    """One line of compact, ASCII-only JSON: how every frame and every typed value is written."""
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


# --- the request frames -------------------------------------------------------------------------


def unit_record(unit: Unit) -> dict:
    """Return one unit as a request frame carries it (the plan's § Batches in), in the plan's field order. A unit that carries ink deltas is refused rather than sent without them, since a materialized unit never does."""
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


def encode_batch(batch: int, units: Sequence[Unit]) -> dict:
    """The `batch` request frame for one handout."""
    return {"op": "batch", "batch": batch, "units": [unit_record(unit) for unit in units]}


def encode_verify(items: Sequence[tuple[Unit, tuple]]) -> dict:
    """The `verify` request frame for the verification sample: each unit with the injection `_recompute_fragment` patches onto it, `(duplicate group, cluster, final class, primary units)`, the primary units as `(id or None, suppressed)` pairs."""
    return {
        "op": "verify",
        "units": [
            {
                "unit": unit_record(unit),
                "injection": {
                    "duplicate_group": duplicate_group,
                    "cluster": cluster,
                    "class": final_class,
                    "primary_units": [[primary, suppressed] for primary, suppressed in primary_units],
                },
            }
            for unit, (duplicate_group, cluster, final_class, primary_units) in items
        ],
    }


# --- the init file ------------------------------------------------------------------------------


def vocabulary() -> dict[str, object]:
    """Every `VOCABULARY` constant's value by its dotted name, read now."""
    values: dict[str, object] = {}
    for name in VOCABULARY:
        module, attribute = name.rsplit(".", 1)
        values[name] = getattr(importlib.import_module(module), attribute)
    return values


def init_values(init: Mapping[str, Any]) -> dict[str, Any]:
    """Return the values the init file carries for a worker `init` dict, as Python holds them; `write_init` writes them, with the spec and the dictionary as files beside it. The semantics defaults and the vocabulary are read now."""
    repo_root = Path(init["repo_root"])
    fold = init["fold"]
    return {
        "format": INIT_FORMAT,
        "settlement": {
            "simulated_prospect": kernel_exec.SIMULATED_PROSPECT_DEFAULT,
            "follower_prefer_slots": kernel_exec.FOLLOWER_PREFER_SLOTS_DEFAULT,
        },
        "harfbuzz": uharfbuzz.version_string(),  # pyright: ignore[reportAttributeAccessIssue]
        "fonts": {
            "before": _path_text(init["before_font"]),
            "after": _path_text(init["after_font"]),
            "junior": _path_text(init["junior_font"]),
        },
        "aliases": load_alias_map(repo_root / "rebuild" / "m1-aliases.yaml"),
        "subset_pack": {
            "path": _path_text(init["subset_pack"]),
            "format": PACK_FORMAT,
            "packer": PACKER_DIGEST,
            "byteorder": sys.byteorder,
            "digests": table_digests(Path(init["subset_dir"]), ACCEPTANCE_CONFIGS),
        },
        "fold": {"configs": tuple(fold.configs), "triggers": dict(fold.triggers)},
        "identical_classes": frozenset(init["identical_classes"]),
        "corpus_index": dict(init["corpus_index"]),
        "rune_schema": _path_text(repo_root / "rebuild" / "schema" / "rune.schema.json"),
        "glyph_names_by_char": dict(_import_test_shaping()._CHAR_TO_GLYPH),
        "vocabulary": vocabulary(),
    }


def _path_text(path: Path | str) -> str:
    return str(Path(path).absolute())


def write_init(init: Mapping[str, Any], directory: Path) -> Path:
    """Write the init file for a worker `init` dict into `directory`, beside the spec dump (`kernel_io.write_spec` over `enrich.load_spec` of the init's spec root) and the preset dictionary's bytes, and return its path. The caller holds the directory until the engine stops."""
    directory = Path(directory)
    values = init_values(init)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(Path(init["spec_root"]))
    spec_path = directory / SPEC_FILE
    kernel_io.write_spec(spec, spec_path)
    dictionary_path = directory / DICTIONARY_FILE
    dictionary_path.write_bytes(init["dictionary"])
    fold = values["fold"]
    document = {
        "format": INIT_FORMAT,
        "spec": str(spec_path.absolute()),
        "settlement": values["settlement"],
        "harfbuzz": values["harfbuzz"],
        "fonts": values["fonts"],
        "aliases": [[name, _alias_json(alias)] for name, alias in values["aliases"].items()],
        "subset_pack": values["subset_pack"],
        "fold": {
            "configs": list(fold["configs"]),
            "triggers": [
                [config, sorted(ord(letter) for letter in letters)]
                for config, letters in fold["triggers"].items()
            ],
        },
        "identical_classes": sorted(values["identical_classes"]),
        "corpus_index": [
            [text, token, entry["source"], entry["attribute"]]
            for (text, token), entry in values["corpus_index"].items()
        ],
        "rune_schema": values["rune_schema"],
        "glyph_names_by_char": [[ord(char), name] for char, name in values["glyph_names_by_char"].items()],
        "dictionary": str(dictionary_path.absolute()),
        "vocabulary": [[name, encode(value)] for name, value in values["vocabulary"].items()],
    }
    path = directory / INIT_FILE
    path.write_text(compact(document) + "\n", encoding="ascii")
    return path


def _alias_json(alias: CellId | str) -> object:
    if isinstance(alias, str):
        return alias
    return {
        "rune": alias.rune,
        "stance": alias.stance,
        "entry": alias.entry,
        "exit": alias.exit,
        "adjustments": list(alias.adjustments),
    }


# --- building and spawning the binary -----------------------------------------------------------

_BUILT = False


class _RelinkLock:
    def __init__(self, mode: int) -> None:
        self._mode = mode
        self._handle = None

    def __enter__(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._handle = LOCK_PATH.open("w")
        fcntl.flock(self._handle, self._mode)
        return self

    def __exit__(self, *_exc) -> None:
        assert self._handle is not None
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()


def cargo_build() -> None:
    """Build the review crate in release mode, holding the relink lock exclusively through the build and the new binary's first run, which answers with its usage and exit 2 once the file's first-exec scan has ended."""
    arguments = ["cargo", "build", "--release", "--manifest-path", str(MANIFEST)]
    try:
        with _RelinkLock(fcntl.LOCK_EX):
            finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
            if finished.returncode == 0:
                first = subprocess.run([str(BINARY)], capture_output=True, timeout=TIMEOUT)
                if first.returncode != 2:
                    raise EngineRunError(
                        f"the review binary exited {first.returncode} on its first run: "
                        + first.stderr.decode(errors="replace").strip()
                    )
    except FileNotFoundError as error:
        if error.filename == "cargo":
            raise EngineBuildError(
                "no cargo on PATH — install the Rust toolchain (https://rustup.rs) to build the review crate"
            ) from None
        raise EngineRunError(f"no review binary at {BINARY} after its build") from None
    except subprocess.TimeoutExpired:
        raise EngineBuildError(
            f"cargo gave no answer within {TIMEOUT} seconds on {' '.join(arguments)}"
        ) from None
    if finished.returncode != 0:
        errors = finished.stderr.decode(errors="replace").strip().split("\n")
        tail = "\n".join(errors[-BUILD_TAIL_LINES:])
        raise EngineBuildError(
            f"the review crate did not build (cargo exited {finished.returncode}):\n{tail}"
        )


def ensure_built() -> None:
    """Run `cargo_build` once per process and do nothing on later calls."""
    global _BUILT
    if _BUILT:
        return
    cargo_build()
    _BUILT = True


def spawn(arguments: Sequence[str], *, stdin: int | None = subprocess.DEVNULL) -> subprocess.Popen:
    """Start the review binary with `arguments`, stdout and stderr piped, holding the relink lock shared for the spawn only. Raises `EngineRunError` for a missing binary."""
    try:
        with _RelinkLock(fcntl.LOCK_SH):
            return subprocess.Popen(
                [str(BINARY), *arguments], stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
    except FileNotFoundError:
        raise EngineRunError(f"no review binary at {BINARY}; ensure_built() builds it") from None
