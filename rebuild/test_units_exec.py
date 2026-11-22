"""Tests for the review crate under `rebuild/review-rs/`, the Rust engine of the review corpus build's unit recompute (`rebuild/CORPUS-UNITS-PORT-PLAN.md`).

The crate is a sibling of the kernel crate, not a member of a Cargo workspace, so it has its own lock, and in its build that lock, not the kernel's, picks the versions of the kernel's dependencies. The lock-parity test keeps the kernel library the review engine links the same code the table build runs. The rest test the engine interface, `rebuild/review/units_exec.py`: the switch, the typed encoding, the request frames, the vocabulary the init carries and the font facts, and the engine's HarfBuzz against uharfbuzz on the mini bundle. The last four test the engine behind the switch: a mini build under it writes the Python build's corpus, the unit store's stamp names the crate only under it, every kernel module it runs is stamped, and it agrees with the Python workers on the mini source's `ss10` copies. From the `opt-in-switch` task of the plan on, a change to the Python a stage mirrors fails these until the crate follows it.
"""

from __future__ import annotations

import dataclasses
import io
import json
import re
import select
import shutil
import subprocess
import threading
import tomllib
import warnings
from pathlib import Path
from typing import cast

import pytest
import uharfbuzz
from fontTools.ttLib import TTFont

from rebuild.pipeline.model import CellId
from rebuild.review import build, unit_cache, units_exec
from rebuild.review.audit import ACCEPTANCE_CONFIGS, Unit, UnitTable
from rebuild.review.enrich import load_spec
from rebuild.review.ink import InkComparator, features_for, kern_neutral
from rebuild.tools import cycle_timings, units_dual_run, units_stage_fixtures
from rebuild.validation.classify import JunctionClassifier
from rebuild.validation.shaping import Shaper

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


def test_the_switch_reads_the_environment_and_the_build_runs_only_its_engines(monkeypatch):
    """`units_exec.engine()` reads `AMS_CORPUS_UNITS_ENGINE` at call time, `rust` by default, and refuses a value the switch does not know; the build runs both engines the switch names, and the runner refuses an engine outside `ENGINES` before it starts anything."""
    monkeypatch.delenv(units_exec.ENGINE_VARIABLE, raising=False)
    assert units_exec.engine() == "rust"
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "cobol")
    with pytest.raises(ValueError, match="cobol"):
        units_exec.engine()
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "python")
    assert units_exec.engine() == "python"
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "rust")
    assert units_exec.ENGINES == units_exec.SWITCH_VALUES
    monkeypatch.setattr(units_exec, "ENGINES", ("python",))
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


@pytest.fixture(scope="module")
def mini_init(mini_bundle, tmp_path_factory) -> Path:
    """The engine built and an init for the mini bundle written by `units_exec.write_init`, from the worker `init` `_RecomputeRunner` would hand a worker over the bundle with no identical classes and no site pages."""
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


def _run_stage(arguments: list[str]) -> tuple[int, str, str]:
    process = units_exec.spawn(arguments)
    stdout, stderr = process.communicate()
    return process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8", errors="replace")


def _mini_texts() -> list[str]:
    lines = (MINI / "audit.tsv").read_text(encoding="utf-8").splitlines()
    column = lines[0].split("\t").index("codepoints")
    return sorted(
        {"".join(chr(int(value, 16)) for value in line.split("\t")[column].split(":")) for line in lines[1:]}
    )


def test_harfbuzz_shapes_like_uharfbuzz_on_the_mini_texts(mini_init, tmp_path):
    """The engine's HarfBuzz, through the `shape` stage, gives the glyph names, clusters and positions uharfbuzz gives through `Shaper.shape` for every mini text under each acceptance configuration's kern-neutral features on the site Senior font and the mini's `M1.otf`, and with only `kern` off on the site Junior font: the calls the worker makes. Every field of every glyph is compared, so the hand-written declarations' ABI is checked here."""
    texts = _mini_texts()
    calls = [
        (font, text, kern_neutral(features_for(config)))
        for font in (build.SITE_BEFORE_FONT, MINI_FONT)
        for config in ACCEPTANCE_CONFIGS
        for text in texts
    ]
    calls += [(build.SITE_JUNIOR_FONT, text, kern_neutral({})) for text in texts]
    shapers = {font: Shaper(font) for font in (build.SITE_BEFORE_FONT, MINI_FONT, build.SITE_JUNIOR_FONT)}
    stage_file = tmp_path / "shape.ndjson"
    expected: dict[str, object] = {}
    with stage_file.open("w", encoding="ascii") as handle:
        for index, (font, text, features) in enumerate(calls):
            key = f"shape:{index}"
            expected[key] = units_exec.encode(shapers[font].shape(text, features))
            line = {"key": key, "font": str(Path(font).resolve()), "text": text, "features": features}
            handle.write(
                units_exec.compact({name: units_exec.encode(value) for name, value in line.items()}) + "\n"
            )
    returncode, stdout, stderr = _run_stage(["stage", "shape", f"--init={mini_init}", str(stage_file)])
    assert (returncode, stderr) == (0, "")
    answers = {record["key"]: record["value"] for record in map(json.loads, stdout.splitlines())}
    assert answers.keys() == expected.keys()
    differing = [key for key, value in expected.items() if answers[key] != value]
    assert (
        not differing
    ), f"{len(differing)} of {len(calls)} shapes differ, the first {calls[int(differing[0][6:])]!r}"


def test_the_engine_links_the_harfbuzz_uharfbuzz_links_and_refuses_an_init_naming_another(
    mini_init, tmp_path
):
    """`ams-review version` names uharfbuzz's HarfBuzz version, and an init that names another one is refused before anything is shaped."""
    returncode, stdout, _stderr = _run_stage(["version"])
    assert returncode == 0
    linked = uharfbuzz.version_string()  # pyright: ignore[reportAttributeAccessIssue]
    assert f"harfbuzz\t{linked}" in stdout.splitlines()
    document = json.loads(mini_init.read_text(encoding="ascii"))
    assert document["harfbuzz"] == linked
    document["harfbuzz"] = "0.0.1"
    other = tmp_path / "init.json"
    other.write_text(units_exec.compact(document) + "\n", encoding="ascii")
    stage_file = tmp_path / "shape.ndjson"
    stage_file.write_text("", encoding="ascii")
    returncode, stdout, stderr = _run_stage(["stage", "shape", f"--init={other}", str(stage_file)])
    assert (returncode, stdout) == (1, "")
    assert "HarfBuzz 0.0.1" in stderr and f"HarfBuzz {linked}" in stderr


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


def _tree(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(Path(path).rglob("*")) if p.is_file()
    }


def test_a_rust_build_writes_the_python_build_s_corpus_files(
    python_mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch, python_build
):
    """A pooled build under the Rust engine writes the serial Python build's corpus byte for byte, as `rebuild/test_unit_cache.py`'s serial-versus-parallel test compares a pooled Python build, once the unit store's `units_engine` line, which only says which engine wrote the store, is held out; its units line names the engine and it files the engine's `corpus-units-rs` pool record. A no-change rebuild under the engine reuses every unit, recomputes its verification sample through the engine's `verify`, and leaves the corpus as it was, and so does one whose sample is a single unit, which the engine takes too."""
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "rust")
    monkeypatch.setattr(units_exec, "engine_stamp", lambda: None)
    rust = tmp_path / "rust"
    _build_mini(rust, mini_bundle, jobs=2)
    assert _tree(rust) == _tree(python_mini_corpus)
    err = capfd.readouterr().err
    assert re.search(r"\[t\] review\.build units .*\tjobs=2 engine=rust \(recomputed=", err), err
    records = [json.loads(line) for line in cycle_timings.JOURNAL.read_text(encoding="utf-8").splitlines()]
    (record,) = [record for record in records if record["unit"] == "corpus-units-rs"]
    assert (record["width"], list(record["worker_peak_rss_bytes"])) == (2, ["engine"])
    assert record["worker_peak_rss_bytes"]["engine"] > 0 and record["worker_reading"] == "footprint"
    _build_mini(rust, mini_bundle, jobs=2)
    err = capfd.readouterr().err
    total = json.loads((rust / "manifest.json").read_text(encoding="utf-8"))["totals"]["units"]
    assert f"reused {total:,} of {total:,} units from the cache" in err
    verified = re.search(r"\tjobs=2 engine=rust \(recomputed=0, verified=(\d+) cached;", err)
    assert verified and int(verified.group(1)) == min(build.VERIFICATION_SAMPLE, total), err
    assert _tree(rust) == _tree(python_mini_corpus)
    sample = build._verification_sample
    monkeypatch.setattr(build, "_verification_sample", lambda cached, seed: sample(cached, seed, 1))
    _build_mini(rust, mini_bundle, jobs=2)
    assert "\tjobs=2 engine=rust (recomputed=0, verified=1 cached;" in capfd.readouterr().err
    assert _tree(rust) == _tree(python_mini_corpus)


def test_the_unit_store_stamp_names_the_crate_only_under_the_rust_engine(
    python_mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch, python_build
):
    """Under the Python engine the unit store's stamp has no `units_engine` line and keeps the bytes it had before the switch existed; under the Rust engine it gains one, the digest of every file of the review crate outside `target/`, and nothing else moves. So a store the Python engine wrote is a miss to a Rust build, which names the line that moved. A build at one job computes its units with the Python code under either switch value and stamps them as the Python engine's, so it misses a store the Rust engine wrote."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)

    def stamp() -> tuple[str, ...]:
        return unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            MINI,
            build.SITE_BEFORE_FONT,
            build.SITE_JUNIOR_FONT,
            "helpers",
            identical_classes=(),
            units_engine=units_exec.engine_stamp(),
        ).lines

    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "python")
    assert units_exec.engine_stamp() is None
    python = stamp()
    assert (
        python
        == unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            MINI,
            build.SITE_BEFORE_FONT,
            build.SITE_JUNIOR_FONT,
            "helpers",
            identical_classes=(),
        ).lines
    )
    assert not any(line.startswith("units_engine\t") for line in python)
    monkeypatch.delenv(units_exec.ENGINE_VARIABLE)
    rust = stamp()
    added = [line for line in rust if line not in python]
    assert added == [f"units_engine\t{units_exec.engine_stamp()}"]
    assert [line for line in rust if line not in added] == list(python)
    paths = [path.relative_to(units_exec.CRATE).as_posix() for path in units_exec.crate_paths()]
    assert {"Cargo.toml", "Cargo.lock", "build.rs", "src/lib.rs", "src/ffi.rs"} <= set(paths)
    assert not any(path.startswith("target/") for path in paths)
    rebuilt = tmp_path / "rebuilt"
    shutil.copytree(python_mini_corpus, rebuilt)
    capfd.readouterr()
    _build_mini(rebuilt, mini_bundle, jobs=2)
    err = capfd.readouterr().err
    assert re.search(r"unit cache: the stamp moved at [^\n]*units_engine", err), err
    assert "reused 0 of" in err
    _build_mini(rebuilt, mini_bundle, jobs=1)
    err = capfd.readouterr().err
    assert re.search(r"unit cache: the stamp moved at [^\n]*units_engine", err), err
    assert "reused 0 of" in err


def test_a_crate_edit_reaches_the_signature_store_under_either_build(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """The ink-signature store's stamp carries a `crate_code` line, the review crate's digest, since the Rust build, the default, shapes the signatures in the crate, and the Python build stamps it too, so the two write the same header. So a crate edit misses the signature store under either build, as it misses a unit store the Rust units engine stamped, and each miss note names the line that moved; the comparator's Python closure stays the stamp's code line, and the line adds nothing else."""
    base = unit_cache.signature_environment(REPO_ROOT, MINI_FONT, "helpers")
    stamped = unit_cache.signature_environment(REPO_ROOT, MINI_FONT, "helpers", "crate")
    assert stamped.lines == (*base.lines, "crate_code\tcrate")
    assert stamped.detail == base.detail
    rebuilt = tmp_path / "rebuilt"
    shutil.copytree(mini_corpus, rebuilt)
    monkeypatch.setattr(units_exec, "crate_digest", lambda: "0" * 64)
    monkeypatch.delenv(units_exec.ENGINE_VARIABLE, raising=False)
    capfd.readouterr()
    _build_mini(rebuilt, mini_bundle, jobs=2)
    err = capfd.readouterr().err
    assert re.search(r"ink-signature store: the stamp moved at [^\n]*crate_code", err), err
    assert re.search(r"unit cache: the stamp moved at [^\n]*units_engine", err), err


def test_every_kernel_module_the_review_crate_names_is_stamped():
    """The Rust engine runs the kernel library in process, so each kernel module the review crate names (`ams_m1_kernel::<module>`) must be in the unit store's `corpus_code` closure (`unit_cache.corpus_code_paths`), outside `unit_cache.KERNEL_NON_CORPUS_MODULES`, or a kernel edit could leave a store the engine wrote serving stale units."""
    named: set[str] = set()
    for path in sorted((units_exec.CRATE / "src").rglob("*.rs")):
        source = path.read_text(encoding="utf-8")
        named.update(re.findall(r"\bams_m1_kernel::([a-z_][a-z0-9_]*)", source))
        for group in re.findall(r"\bams_m1_kernel::\{([^}]*)\}", source):
            named.update(re.findall(r"(?:^|,)\s*([a-z_][a-z0-9_]*)", group))
    assert named, "the review crate names no kernel module"
    kernel = REPO_ROOT / "rebuild" / "kernel-rs" / "src"
    stamped = set(unit_cache.corpus_code_paths(REPO_ROOT))
    unstamped = {}
    for module in sorted(named):
        files = [path for path in (kernel / f"{module}.rs", kernel / module / "mod.rs") if path.is_file()]
        assert files, f"the review crate names ams_m1_kernel::{module}, which has no file under {kernel}"
        if files[0] not in stamped or files[0].name in unit_cache.KERNEL_NON_CORPUS_MODULES:
            unstamped[module] = files[0].relative_to(REPO_ROOT).as_posix()
    assert (
        unstamped == {}
    ), f"kernel modules the Rust units engine runs but the unit store does not stamp: {unstamped}"


def test_the_engines_agree_on_the_mini_s_ss10_copies(mini_bundle, tmp_path):
    """The Python workers and the Rust engine, run side by side by the dual-run harness over the mini source's `ss10` copies (`units_stage_fixtures._ss10_copy`), agree on every projection field, every inflated fragment, every handout's contract errors and the verification sample under its comparator. The bundle holds no `ss10` row, so these copies are what reach the isolated overlay, the `ss10` gate, the `ss10` subset rows and the Junior check, which the mini build above never shows the engine."""
    table, store, identical_classes = units_stage_fixtures._load_table(
        MINI / "audit.tsv", mini_bundle.ledger, mini_bundle.spec_root, MINI_FONT, tmp_path, recompute_all=True
    )
    handouts = units_stage_fixtures._queue_handouts(range(table.n, 2 * table.n))
    source = units_stage_fixtures.Source(
        name="mini",
        init=units_stage_fixtures._worker_init(
            MINI_FONT, MINI, mini_bundle.subset_pack, mini_bundle.spec_root, identical_classes
        ),
        table=table,
        store=store,
        handouts=handouts,
        corpus_units=table.n,
        chosen=list(range(len(handouts))),
        selection={},
    )
    report = units_dual_run.Report()
    units_dual_run.run(source, ("python", "rust"), 2, 2, None, report)
    disagreements = {name: tally.examples[:1] for name, tally in report.fields.items() if tally.disagreements}
    assert disagreements == {}
    assert report.compared == report.handed_out == table.n
    assert report.verify_sample and report.verify_agreed == report.verify_sample


def test_an_engine_that_fails_while_requests_are_still_being_written_reports_why(mini_bundle, tmp_path):
    """An engine whose work ends in a failure while its parent is still writing requests ends with its error raised, not with both sides waiting on each other. The engine is sent the mini's units as one batch, whose reply is far longer than a pipe holds, then, once that reply is waiting unread, a request it refuses and more requests than a pipe holds: the engine keeps reading its input, so the write finishes, the batch's reply is read, and the next read raises the engine's refusal. A stopped engine that this side finds only when a write fails reports the error frame left among its unread replies."""
    table, store, identical_classes = units_stage_fixtures._load_table(
        MINI / "audit.tsv", mini_bundle.ledger, mini_bundle.spec_root, MINI_FONT, tmp_path, recompute_all=True
    )
    init = units_stage_fixtures._worker_init(
        MINI_FONT, MINI, mini_bundle.subset_pack, mini_bundle.spec_root, identical_classes
    )
    engine = units_exec.UnitsProcess(init, 1)
    try:
        engine.send_batch(0, tuple(table.unit(ordinal, store) for ordinal in range(table.n)))
        stdin, stdout = engine._process.stdin, engine._process.stdout
        assert stdin is not None and stdout is not None
        assert select.select([stdout], [], [], 120)[0], "the engine never answered the batch"

        def write() -> None:
            try:
                stdin.write(b"not a request\n" + b'{"op":"stop"}\n' * 400_000)
                stdin.flush()
            except BrokenPipeError:
                pass

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        writer.join(timeout=60)
        assert not writer.is_alive(), "the engine stopped reading its input, so the write never finished"
        reply = engine.read_reply()
        assert reply[0] == "batch" and len(reply[2]) == table.n
        with pytest.raises(units_exec.EngineRunError, match="the units engine failed: "):
            engine.read_reply()
    finally:
        engine.close()

    class _Stopped:
        """A process that has exited with a batch reply and an error frame unread."""

        stdout = io.BytesIO(
            b'{"op":"batch","batch":0,"projections":[],"contract_errors":[],"payload":3}\nabc'
            b'{"op":"error","message":"batch 1: the after font refutes the drafted pin"}\n'
        )
        stderr = io.BytesIO(b"")

        def kill(self) -> None:
            pass

        def wait(self) -> int:
            return 1

    stopped = units_exec.UnitsProcess.__new__(units_exec.UnitsProcess)
    stopped._process = cast(subprocess.Popen, _Stopped())
    failure = stopped._failed("stopped reading before the batch frame")
    assert str(failure) == "the units engine failed: batch 1: the after font refutes the drafted pin"


def test_the_memory_fit_separates_the_process_from_its_threads():
    """`--memory` fits `peak = process + threads × thread` to the Rust side's peak footprints by least squares, the two figures `artifact_cycle.CORPUS_UNITS_RS_PROCESS_BYTES` and `CORPUS_UNITS_RS_THREAD_BYTES` are set from, and refuses widths that cannot separate them."""
    points = [(1, 450_000_000), (4, 600_000_000), (11, 950_000_000)]
    assert units_dual_run.fit_memory(points) == (400_000_000, 50_000_000)
    assert units_dual_run.fit_memory([(1, 440_000_000), (2, 470_000_000), (3, 520_000_000)]) == (
        396_666_667,
        40_000_000,
    )
    with pytest.raises(ValueError):
        units_dual_run.fit_memory([(4, 600_000_000), (4, 610_000_000)])
