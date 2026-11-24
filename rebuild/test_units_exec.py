"""Tests for the review crate under `rebuild/review-rs/` and its units interface, `rebuild/review/units_exec.py`.

The crate is a sibling of the kernel crate, not a member of a Cargo workspace, so it has its own lock, and in its build that lock, not the kernel's, picks the versions of the kernel's dependencies. The lock-parity test keeps the kernel library the review crate links the same code the table build runs. The rest test the interface: the typed encoding, the vocabulary the init carries, the font facts, the HarfBuzz version the init names against the one the crate links, and the crate's digest, which both corpus stores' stamps carry, so a crate edit misses both stores.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
import warnings
from pathlib import Path

import pytest
import uharfbuzz
from fontTools.ttLib import TTFont

from rebuild.pipeline.model import CellId
from rebuild.review import build, unit_cache, units_exec
from rebuild.review.enrich import load_spec
from rebuild.review.ink import InkComparator
from rebuild.validation.classify import JunctionClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_LOCK = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.lock"
REVIEW_LOCK = REPO_ROOT / "rebuild" / "review-rs" / "Cargo.lock"
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_FONT = MINI / "M1.otf"


def _locked_versions(lock: Path) -> dict[str, set[str]]:
    with lock.open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    versions: dict[str, set[str]] = {}
    for package in packages:
        versions.setdefault(package["name"], set()).add(package["version"])
    return versions


def test_every_package_both_locks_name_has_one_version():
    """A package the kernel's lock and the review crate's lock both name is locked at one version across the two, so a review-side dependency edit that moves a kernel dependency fails here instead of building the kernel library against code the table build never ran."""
    kernel = _locked_versions(KERNEL_LOCK)
    review = _locked_versions(REVIEW_LOCK)
    assert "ams-m1-kernel" in review, f"{REVIEW_LOCK.relative_to(REPO_ROOT)} does not lock the kernel crate"
    shared = sorted(kernel.keys() & review.keys())
    split = {
        name: sorted(kernel[name] | review[name]) for name in shared if len(kernel[name] | review[name]) != 1
    }
    assert split == {}, f"the two crates' locks disagree on these packages' versions: {split}"


def test_the_typed_encoding_writes_every_kind_it_carries():
    """`encode` writes each kind the crate's `PyValue` reads in its tagged form: a tuple, a list, a dict as key and value pairs in insertion order, bytes as hex, a set with its members sorted by their encoded text, and a dataclass or named tuple as its class name and fields in declaration order. A float, or any other kind it has no form for, raises TypeError."""
    value = {
        "tuple": (1, -2, None, True, "\U0001f600"),
        ("key", 1): [b"\x00\xff", frozenset({"b", "a"})],
        "record": CellId("qsPea", "half", None, "y6", ("locked",)),
    }
    assert units_exec.encode(value) == {
        "dict": [
            ["tuple", {"tuple": [1, -2, None, True, "\U0001f600"]}],
            [{"tuple": ["key", 1]}, {"list": [{"bytes": "00ff"}, {"set": ["a", "b"]}]}],
            [
                "record",
                {
                    "record": [
                        "CellId",
                        [
                            ["rune", "qsPea"],
                            ["stance", "half"],
                            ["entry", None],
                            ["exit", "y6"],
                            ["adjustments", {"tuple": ["locked"]}],
                        ],
                    ]
                },
            ],
        ]
    }
    assert units_exec.compact(units_exec.encode("\U0001f600")) == '"\\ud83d\\ude00"'
    for refused in (1.5, ("a", 1.0), {"a": object()}):
        with pytest.raises(TypeError):
            units_exec.encode(refused)


def test_the_vocabulary_resolves_and_the_crate_reads_the_same_names():
    """Every `VOCABULARY` name is a constant the typed encoding can carry, and the crate's `wire::VOCABULARY`, the names it refuses an init without, is the same list."""
    for name, value in units_exec.vocabulary().items():
        assert json.loads(units_exec.compact(units_exec.encode(value))) is not None, name
    source = (REPO_ROOT / "rebuild" / "review-rs" / "src" / "wire.rs").read_text(encoding="utf-8")
    block = re.search(r"pub const VOCABULARY: &\[&str\] = &\[(.*?)\];", source, re.S)
    assert block is not None, "wire.rs does not declare VOCABULARY"
    assert tuple(re.findall(r'"([^"]+)"', block.group(1))) == units_exec.VOCABULARY
    assert re.search(rf'pub const INIT_FORMAT: &str = "{re.escape(units_exec.INIT_FORMAT)}";', source)


def test_the_font_facts_hold_what_the_shapers_and_outline_caches_read_and_refuse_a_fractional_origin(
    tmp_path, monkeypatch
):
    """The font facts file holds, for the mini's three fonts, each glyph's name, shape key and origin, each shape's picture, and the after font's curs sets as Python's shapers, outline caches and classifier read them, the keys hex in a shape space separate interns agree on; a fractional origin is refused."""
    fonts = {
        "before": build.SITE_BEFORE_FONT,
        "after": REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf",
        "junior": build.SITE_JUNIOR_FONT,
    }
    path = tmp_path / units_exec.FONT_FACTS_FILE
    units_exec.write_font_facts(fonts, path)
    document = json.loads(path.read_text(encoding="ascii"))
    assert document["format"] == units_exec.FONT_FACTS_FORMAT
    source = (REPO_ROOT / "rebuild" / "review-rs" / "src" / "fontfacts.rs").read_text(encoding="utf-8")
    assert f'pub const FONT_FACTS_FORMAT: &str = "{units_exec.FONT_FACTS_FORMAT}";' in source
    shapes = document["shapes"]
    assert list(shapes) == sorted(shapes)
    comparators = {
        "before": InkComparator(fonts["before"], fonts["after"]),
        "after": InkComparator(fonts["before"], fonts["after"]),
        "junior": InkComparator(fonts["junior"], fonts["after"]),
    }
    for side, comparator in comparators.items():
        shaper, cache = comparator._sides["after" if side == "after" else "before"]
        rows = document["fonts"][side]["glyphs"]
        assert len(rows) == len(TTFont(str(fonts[side])).getGlyphOrder())
        for glyph_id, (name, key, origin_x, origin_y) in enumerate(rows):
            assert name == shaper.glyph_name(glyph_id)
            shape_key, x, y = cache.shape_key(name)
            assert (key, origin_x, origin_y) == (shape_key.hex(), x, y)
            if shape_key:
                cells = comparator.intern.cells(shape_key)
                assert shapes[key] == [
                    comparator.intern.draws(shape_key),
                    None if cells is None else [list(cell) for cell in sorted(cells)],
                ]
    classifier = JunctionClassifier(fonts["after"])
    after = document["fonts"]["after"]
    assert [height for height, _names in after["exits"]] == list(classifier.heights())
    for kind, sets in (("exits", classifier._exit_sets), ("entries", classifier._entry_sets)):
        assert {height: frozenset(names) for height, names in after[kind]} == sets
    monkeypatch.setattr(units_exec.OutlineCache, "shape_key", lambda _self, _name: (b"\x01", 0.5, 0))
    with pytest.raises(ValueError, match="not an integer"):
        units_exec.font_facts(fonts)


@pytest.fixture(scope="module")
def mini_init(mini_bundle, tmp_path_factory) -> Path:
    """The crate built and a units init for the mini bundle written by `units_exec.write_init`, from the `init` dict `build.build_m1` builds, over the bundle with no identical classes and no site pages."""
    units_exec.ensure_built()
    init = {
        "before_font": build.SITE_BEFORE_FONT,
        "after_font": MINI_FONT,
        "junior_font": build.SITE_JUNIOR_FONT,
        "subset_dir": MINI,
        "subset_pack": mini_bundle.subset_pack,
        "repo_root": REPO_ROOT,
        "spec_root": mini_bundle.spec_root,
        "identical_classes": frozenset(),
        "fold": build._config_fold(build.SITE_BEFORE_FONT, MINI_FONT),
        "corpus_index": {},
        "dictionary": build.FRAGMENT_DICTIONARY.read_bytes(),
    }
    return units_exec.write_init(init, tmp_path_factory.mktemp("mini-init"))


def _run(arguments: list[str]) -> tuple[int, str, str]:
    process = units_exec.spawn(arguments)
    stdout, stderr = process.communicate()
    return process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8", errors="replace")


def test_the_crate_links_the_harfbuzz_uharfbuzz_links_and_the_init_names_it(mini_init):
    """`ams-review version` names uharfbuzz's HarfBuzz version, the one the units init names (`units_exec.init_values`), which the crate checks before it shapes anything."""
    returncode, stdout, _stderr = _run(["version"])
    assert returncode == 0
    linked = uharfbuzz.version_string()  # pyright: ignore[reportAttributeAccessIssue]
    assert f"harfbuzz\t{linked}" in stdout.splitlines()
    assert json.loads(mini_init.read_text(encoding="ascii"))["harfbuzz"] == linked


def _build_mini(out: Path, mini_bundle, **kwargs) -> None:
    build.build_m1(
        out,
        audit_path=MINI / "audit.tsv",
        ledger_path=mini_bundle.ledger,
        subset_dir=MINI,
        after_font=MINI_FONT,
        spec_root=mini_bundle.spec_root,
        subset_pack=mini_bundle.subset_pack,
        **kwargs,
    )


def test_the_unit_store_stamp_names_the_crate(mini_bundle):
    """The unit store's stamp carries a `units_engine` line, the digest of every file of the review crate outside `target/` (`units_exec.crate_digest`), and the line adds nothing else to the stamp."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)

    def stamp(units_engine: str) -> tuple[str, ...]:
        return unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            MINI,
            build.SITE_BEFORE_FONT,
            build.SITE_JUNIOR_FONT,
            "helpers",
            identical_classes=(),
            units_engine=units_engine,
        ).lines

    crate = stamp(units_exec.crate_digest())
    other = stamp("0" * 64)
    assert [line for line in crate if line not in other] == [f"units_engine\t{units_exec.crate_digest()}"]
    assert len(crate) == len(other)
    paths = [path.relative_to(units_exec.CRATE).as_posix() for path in units_exec.crate_paths()]
    assert {"Cargo.toml", "Cargo.lock", "build.rs", "src/lib.rs", "src/ffi.rs"} <= set(paths)
    assert not any(path.startswith("target/") for path in paths)


def test_a_crate_edit_misses_both_stores(mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch):
    """The ink-signature store's stamp ends with a `crate_code` line, the review crate's digest, since the crate shapes the signatures, and the unit store's carries the same digest as its `units_engine` line. So a crate edit misses both stores, and each miss note names the line that moved; the font facts' Python readers stay the signature stamp's code line, and the crate's line adds nothing else."""
    stamped = unit_cache.signature_environment(REPO_ROOT, MINI_FONT, "helpers", "crate")
    other = unit_cache.signature_environment(REPO_ROOT, MINI_FONT, "helpers", "other")
    assert stamped.lines[-1] == "crate_code\tcrate"
    assert stamped.lines[:-1] == other.lines[:-1]
    assert stamped.detail == other.detail
    rebuilt = tmp_path / "rebuilt"
    shutil.copytree(mini_corpus, rebuilt)
    monkeypatch.setattr(units_exec, "crate_digest", lambda: "0" * 64)
    capfd.readouterr()
    _build_mini(rebuilt, mini_bundle, jobs=2)
    err = capfd.readouterr().err
    assert re.search(r"ink-signature store: the stamp moved at [^\n]*crate_code", err), err
    assert re.search(r"unit cache: the stamp moved at [^\n]*units_engine", err), err
