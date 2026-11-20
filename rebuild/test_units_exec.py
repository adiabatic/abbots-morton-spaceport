"""Tests for the review crate under `rebuild/review-rs/`, the Rust engine of the review corpus build's unit recompute (`rebuild/CORPUS-UNITS-PORT-PLAN.md`).

The crate is a sibling of the kernel crate, not a member of a Cargo workspace, so it has its own lock, and in its build that lock, not the kernel's, picks the versions of the kernel's dependencies. The lock-parity test keeps the kernel library the review engine links the same code the table build runs. The rest test the engine interface, `rebuild/review/units_exec.py`: the switch, the typed encoding, the request frames and the vocabulary the init carries.
"""

from __future__ import annotations

import dataclasses
import json
import re
import tomllib
from pathlib import Path
from typing import cast

import pytest
from fontTools.ttLib import TTFont

from rebuild.pipeline.model import CellId
from rebuild.review import build, units_exec
from rebuild.review.audit import Unit, UnitTable
from rebuild.review.ink import InkComparator
from rebuild.validation.classify import JunctionClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_LOCK = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.lock"
REVIEW_LOCK = REPO_ROOT / "rebuild" / "review-rs" / "Cargo.lock"


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


def test_the_switch_reads_the_environment_and_the_build_runs_only_its_engines(monkeypatch):
    """`units_exec.engine()` reads `AMS_CORPUS_UNITS_ENGINE` at call time, `python` by default, and refuses a value the switch does not know; the runner refuses an engine outside `ENGINES` before it starts anything."""
    monkeypatch.delenv(units_exec.ENGINE_VARIABLE, raising=False)
    assert units_exec.engine() == "python"
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "cobol")
    with pytest.raises(ValueError, match="cobol"):
        units_exec.engine()
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "rust")
    assert units_exec.engine() == "rust"
    assert "rust" not in units_exec.ENGINES
    with pytest.raises(ValueError, match="the rust units engine is not one the build runs"):
        build._RecomputeRunner(
            [],
            1,
            REPO_ROOT,
            REPO_ROOT,
            REPO_ROOT,
            REPO_ROOT,
            REPO_ROOT,
            table=cast(UnitTable, None),
            subset_pack=REPO_ROOT,
            identical_classes=frozenset(),
            corpus_index={},
            dictionary=b"",
        )


def test_the_typed_encoding_gives_back_every_kind_it_writes():
    value = {
        "tuple": (1, -2, None, True, "\U0001f600"),
        ("key", 1): [b"\x00\xff", frozenset({"b", "a"})],
        "record": CellId("qsPea", "half", None, "y6", ("locked",)),
    }
    encoded = units_exec.encode(value)
    decoded = cast(dict, units_exec.decode(json.loads(units_exec.compact(encoded))))
    assert units_exec.compact(units_exec.encode(decoded)) == units_exec.compact(encoded)
    assert decoded[("key", 1)] == [b"\x00\xff", frozenset({"a", "b"})]
    assert decoded["record"] == units_exec.Record(
        "CellId",
        (
            ("rune", "qsPea"),
            ("stance", "half"),
            ("entry", None),
            ("exit", "y6"),
            ("adjustments", ("locked",)),
        ),
    )
    for refused in (1.5, ("a", 1.0), {"a": object()}):
        with pytest.raises(TypeError):
            units_exec.encode(refused)


def test_a_unit_record_carries_the_fields_phase_one_reads_and_no_other():
    """`UNIT_FIELDS` names `audit.Unit` fields in declaration order, without the machine flags or the deltas, and a batch frame's record holds exactly their wire names."""
    declared = [item.name for item in dataclasses.fields(Unit)]
    names = [name for name, _wire in units_exec.UNIT_FIELDS]
    assert names == [name for name in declared if name in names]
    assert not set(names) & {"ink_identical", "picture_identical", "junior_equivalent", "ink_deltas"}
    unit = Unit(
        codepoints="E650:0020",
        baseline=("qsPea",),
        new=("a", "b"),
        class_id="c",
        row_count=1,
        configs=("default", "ss10"),
        render_groups=(("default",), ("ss10",)),
        config_classes={"default": "c", "ss10": "d"},
        ordinal=4,
    )
    frame = units_exec.encode_batch(2, [unit])
    assert frame["op"] == "batch" and frame["batch"] == 2
    (record,) = frame["units"]
    assert set(record) == {wire for _name, wire in units_exec.UNIT_FIELDS}
    assert record["config_classes"] == [["default", "c"], ["ss10", "d"]]
    verify = units_exec.encode_verify([(unit, ("g", None, "c", ((None, False), ("id", True))))])
    assert verify["units"][0]["injection"] == {
        "duplicate_group": "g",
        "cluster": None,
        "class": "c",
        "primary_units": [[None, False], ["id", True]],
    }
    with pytest.raises(ValueError, match="ink deltas"):
        units_exec.encode_batch(0, [dataclasses.replace(unit, ink_deltas={"default": "x"})])


def test_the_vocabulary_resolves_and_the_engine_reads_the_same_names():
    """Every `VOCABULARY` name is a constant the typed encoding can carry, and the crate's `wire::VOCABULARY`, the names the engine refuses an init without, is the same list."""
    for name, value in units_exec.vocabulary().items():
        assert units_exec.decode(json.loads(units_exec.compact(units_exec.encode(value)))) is not None, name
    source = (REPO_ROOT / "rebuild" / "review-rs" / "src" / "wire.rs").read_text(encoding="utf-8")
    block = re.search(r"pub const VOCABULARY: &\[&str\] = &\[(.*?)\];", source, re.S)
    assert block is not None, "wire.rs no longer declares VOCABULARY"
    assert tuple(re.findall(r'"([^"]+)"', block.group(1))) == units_exec.VOCABULARY
    assert re.search(rf'pub const INIT_FORMAT: &str = "{re.escape(units_exec.INIT_FORMAT)}";', source)


def test_the_font_facts_hold_what_the_worker_reads_and_refuse_a_fractional_origin(tmp_path, monkeypatch):
    """The font facts file holds, for the mini's three fonts, each glyph's name, shape key and origin, each shape's picture, and the after font's curs sets as the worker's own shapers, outline caches and classifier read them, the keys hex in a shape space the worker's separate interns agree on; a fractional origin is refused."""
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
