"""The review crate's units interface: the Python side of the units code in the crate under `rebuild/review-rs/`, which the corpus build's process (`ams-review build`, `corpus_exec`) runs on its threads. It owns the formats of what the units code reads, in the pattern of `rebuild/pipeline/kernel_exec.py`: nothing pickled crosses, only files and compact JSON this module writes. `doc/rebuild-design.md` §14.2 states the crate's design facts.

`write_init` writes the units init (`INIT_FORMAT`) into a directory the caller holds, beside the spec dump, the font facts and the preset dictionary, from the `init` dict `build.build_m1` builds; `init_values` gives the values it carries as Python holds them. `write_font_facts` writes the font facts (`FONT_FACTS_FORMAT`), what the units code reads of the three fonts' outlines, junction classes and glyph names, and `font_facts` gives them as Python holds them. Values whose Python type the crate must keep, the vocabulary's among them, cross in the typed encoding (`encode`). `crate_digest` is the crate's digest, which both corpus stores' stamps carry. `ensure_built` builds the crate once per process under a relink lock in the crate's `target/`, held as `kernel_exec` holds its own, and `spawn` starts the binary with that lock held shared for the spawn only.
"""

from __future__ import annotations

import dataclasses
import fcntl
import importlib
import json
import subprocess
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import uharfbuzz
from fontTools.ttLib import TTFont

from rebuild.pipeline import fingerprint, kernel_exec, kernel_io
from rebuild.pipeline.labels import load_alias_map
from rebuild.pipeline.model import CellId
from rebuild.review.audit import ACCEPTANCE_CONFIGS
from rebuild.review.drafts import _import_test_shaping
from rebuild.review.enrich import load_spec
from rebuild.review.ink import EMPTY_OUTLINE_KEY, OutlineCache, OutlineIntern, junior_tracking
from rebuild.review.subset_pack import PACK_FORMAT, PACKER_DIGEST, table_digests
from rebuild.validation.classify import JunctionClassifier

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
FONT_FACTS_FORMAT = "ams-review-font-facts/1"
FONT_FACTS_FILE = "font_facts.json"
# The three fonts the init names, by the name the init and the font facts give each, and the `init` key that holds its path.
FONT_SIDES = (("before", "before_font"), ("after", "after_font"), ("junior", "junior_font"))
DICTIONARY_FILE = "dictionary.bin"

# The module-level constants the crate's units code reads, each by its dotted name, which is also its name in the init. `vocabulary` reads them at call time, and the crate refuses an init that lacks a name it reads.
VOCABULARY = (
    "rebuild.pipeline.labels.BOUNDARY_GLYPH_NAMES",
    "rebuild.review.audit.ACCEPTANCE_CONFIGS",
    "rebuild.review.audit.MACHINE_CHECKS",
    "rebuild.review.audit.SLIM_OMITTED_KEYS",
    "rebuild.review.audit.WINDOW_SCOPED_CONFIGS",
    "rebuild.review.build.CONTRACT_ERRORS_SHOWN",
    "rebuild.review.enrich.EXPLAIN_UNIT_BATCH_SIZE",
    "rebuild.review.enrich.LETTERS",
    "rebuild.review.feature_reach.DEFAULT",
    "rebuild.review.ink.IDENTITY_DIFF",
    "rebuild.validation.classify.PIXEL_SIZE",
)


class EngineBuildError(RuntimeError):
    """`cargo` is absent or the review crate did not build."""


class EngineRunError(RuntimeError):
    """The review binary is missing or did not answer as its contract says."""


def crate_paths() -> list[Path]:
    """Every file under the review crate outside its `target/`, sorted (`fingerprint.review_crate_paths`)."""
    return fingerprint.review_crate_paths(REPO_ROOT)


def crate_digest() -> str:
    """The digest of `crate_paths` through `fingerprint.code_file_digest`: the unit store stamp's `units_engine` line (`unit_cache.environment_stamp`) and the ink-signature store stamp's `crate_code` line (`unit_cache.signature_environment`)."""
    return fingerprint.digest_lines(fingerprint.path_lines(REPO_ROOT, crate_paths()))


# --- the typed encoding -------------------------------------------------------------------------


def encode(value: object) -> object:
    """Return `value` in the typed JSON encoding the crate's `PyValue` reads: an int, a string, a boolean or None as itself, `{"tuple": [...]}`, `{"list": [...]}`, `{"dict": [[key, value], ...]}` in insertion order, `{"bytes": hex}`, `{"set": [...]}` for a set or frozenset with its members sorted by their encoded JSON text, and `{"record": [class name, [[field, value], ...]]}` for a dataclass or a named tuple. Any other type raises TypeError, a float and a subclass of a listed type among them, so nothing crosses as a value the crate cannot read back as the type Python held."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return value
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


def compact(data: object) -> str:
    """One line of compact, ASCII-only JSON: how the init, the font facts, the build input and every typed value are written."""
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


# --- the init file ------------------------------------------------------------------------------


def vocabulary() -> dict[str, object]:
    """Every `VOCABULARY` constant's value by its dotted name, read now."""
    values: dict[str, object] = {}
    for name in VOCABULARY:
        module, attribute = name.rsplit(".", 1)
        values[name] = getattr(importlib.import_module(module), attribute)
    return values


def init_values(init: Mapping[str, Any]) -> dict[str, Any]:
    """Return the values the init file carries for an `init` dict, as Python holds them; `write_init` writes them, with the spec and the dictionary as files beside it. The semantics defaults and the vocabulary are read now."""
    repo_root = Path(init["repo_root"])
    fold = init["fold"]
    return {
        "format": INIT_FORMAT,
        "settlement": {
            "simulated_prospect": kernel_exec.SIMULATED_PROSPECT_DEFAULT,
            "follower_prefer_slots": kernel_exec.FOLLOWER_PREFER_SLOTS_DEFAULT,
        },
        "harfbuzz": uharfbuzz.version_string(),  # pyright: ignore[reportAttributeAccessIssue]
        "fonts": {side: _path_text(init[key]) for side, key in FONT_SIDES},
        "junior_tracking": junior_tracking(init["junior_font"], init["before_font"]),
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


def font_facts(fonts: Mapping[str, Path | str]) -> dict[str, Any]:
    """Return the font facts of the init's three fonts, `fonts` naming each font's path by its side (`FONT_SIDES`), as Python holds them: under `fonts`, each side's `glyphs`, one `(glyph name, shape key, origin x, origin y)` per glyph id in the font's glyph order, the key and origin as `OutlineCache.shape_key` gives them, and on the after side `JunctionClassifier`'s `exits` and `entries`, each height's glyph names in ascending height order; under `shapes`, each shape key the glyphs place, in key order, with `(OutlineIntern.draws, OutlineIntern.cells)`. The three caches share one intern, and a key derives from the outline's content, so the keys compare across the fonts. An origin that is not an int raises ValueError."""
    intern = OutlineIntern()
    sides: dict[str, dict[str, Any]] = {}
    for side, _key in FONT_SIDES:
        path = fonts[side]
        cache = OutlineCache(path, intern)
        rows = []
        for name in TTFont(str(path)).getGlyphOrder():
            key, origin_x, origin_y = cache.shape_key(name)
            if type(origin_x) is not int or type(origin_y) is not int:
                raise ValueError(
                    f"{path}: the glyph {name} has the origin ({origin_x!r}, {origin_y!r}), not an integer one"
                )
            rows.append((name, key, origin_x, origin_y))
        sides[side] = {"glyphs": tuple(rows)}
    classifier = JunctionClassifier(fonts["after"])
    heights = classifier.heights()
    sides["after"]["exits"] = {height: classifier._exit_sets[height] for height in heights}
    sides["after"]["entries"] = {height: classifier._entry_sets[height] for height in heights}
    keys = sorted({row[1] for facts in sides.values() for row in facts["glyphs"]} - {EMPTY_OUTLINE_KEY})
    return {"fonts": sides, "shapes": {key: (intern.draws(key), intern.cells(key)) for key in keys}}


def write_font_facts(fonts: Mapping[str, Path | str], path: Path) -> None:
    """Write `font_facts(fonts)` to `path` as the font facts file, `FONT_FACTS_FORMAT`: under `fonts`, each side's `glyphs` as `[glyph name, shape key, origin x, origin y]` rows, the key as lowercase hex and empty for a glyph with no outline, and the after side's `exits` and `entries` as `[height, [glyph name, ...]]` pairs, the names sorted; under `shapes`, an object keyed by the hex, in key order, of `[drawn, cells]`, the cells as sorted `[column, row]` pairs or null for a shape with no grid picture."""
    facts = font_facts(fonts)
    sides: dict[str, dict[str, Any]] = {}
    for side, values in facts["fonts"].items():
        sides[side] = {
            "glyphs": [
                [name, key.hex(), origin_x, origin_y] for name, key, origin_x, origin_y in values["glyphs"]
            ]
        }
        for kind in ("exits", "entries"):
            if kind in values:
                sides[side][kind] = [[height, sorted(names)] for height, names in values[kind].items()]
    shapes = {
        key.hex(): [drawn, None if cells is None else [list(cell) for cell in sorted(cells)]]
        for key, (drawn, cells) in facts["shapes"].items()
    }
    document = {"format": FONT_FACTS_FORMAT, "fonts": sides, "shapes": shapes}
    path.write_text(compact(document) + "\n", encoding="ascii")


def write_init(init: Mapping[str, Any], directory: Path) -> Path:
    """Write the init file for an `init` dict into `directory`, beside the spec dump (`kernel_io.write_spec` over `enrich.load_spec` of the init's spec root), the font facts (`write_font_facts`) and the preset dictionary's bytes, and return its path. The caller holds the directory until the build process exits."""
    directory = Path(directory)
    values = init_values(init)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(Path(init["spec_root"]))
    spec_path = directory / SPEC_FILE
    kernel_io.write_spec(spec, spec_path)
    facts_path = directory / FONT_FACTS_FILE
    write_font_facts({side: init[key] for side, key in FONT_SIDES}, facts_path)
    dictionary_path = directory / DICTIONARY_FILE
    dictionary_path.write_bytes(init["dictionary"])
    fold = values["fold"]
    document = {
        "format": INIT_FORMAT,
        "spec": str(spec_path.absolute()),
        "settlement": values["settlement"],
        "harfbuzz": values["harfbuzz"],
        "fonts": values["fonts"],
        "font_facts": str(facts_path.absolute()),
        "junior_tracking": values["junior_tracking"],
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


def spawn(
    arguments: Sequence[str],
    *,
    stdin: int | None = subprocess.DEVNULL,
    stderr: int | None = subprocess.PIPE,
    pass_fds: Sequence[int] = (),
) -> subprocess.Popen:
    """Start the review binary with `arguments`, stdout piped, stderr piped unless `stderr` says otherwise (None inherits this process's), and the descriptors `pass_fds` inherited, holding the relink lock shared for the spawn only. Raises `EngineRunError` for a missing binary."""
    try:
        with _RelinkLock(fcntl.LOCK_SH):
            return subprocess.Popen(
                [str(BINARY), *arguments],
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=stderr,
                pass_fds=tuple(pass_fds),
            )
    except FileNotFoundError:
        raise EngineRunError(f"no review binary at {BINARY}; ensure_built() builds it") from None
