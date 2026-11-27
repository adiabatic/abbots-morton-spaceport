"""Tests for the accept's four-letter sweep (`engine/review/accept_sweep.py`), its record (`engine/review/sweep_record.py`) and what the corpus build, the readiness check, the cycle's corpus skip and the accept do with it: the sweep's context as a parameter, which leaves the corpus build's units as they were; one task's finds, where a change shows only with more context than the corpus build's sweep has; the merge's order; the record's key; the build taking the record's texts as candidates and making units of them; the `accept_sweep` fingerprint component; the accept's flow around the sweep; and the pool's width.

The fonts and tables are real builds of the pipeline's mini fixture spec, as `engine/test_review_diff_units.py` builds them: the edit removes ·Tea's x-height entry extension, so ·Day+Utter ~x~ ·Tea loses a pixel. Its cell change shows in `·Day·Utter·Tea`, two letters of context from ·Tea, so the tests run the sweep at small contexts (a corpus-build context of 0 or 1 against a sweep context of 2) to stand for the three-letter and four-letter sweeps. A certificate of ·Tea's rules shows the change too, so the tests that need a change no corpus-build candidate shows run their tasks without the certificates. The tasks run in this process; only the width is computed for a pool.
"""

import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest

from engine.pipeline import fingerprint, fixtures, run_build
from engine.review import accept, accept_sweep, build, diff_units, snapshot, status, sweep_record
from engine.review.verdict_store import AUTOSAVE_NAME
from engine.test_review_diff_units import _mini_build, _without_tea_extension
from engine.tools import memory_budget

SPEC = fixtures.mini_spec()
EDITED_SPEC = _without_tea_extension(fixtures.mini_spec())
TEA = ""
JUNCTION_TEXT = ""
CHANGE = "settlement qsTea: qsTea.half.en-y5.en-ext-1 -> qsTea.half.en-y5"
CHANGED = ("default", "ss04", "ss05", "ss07")
STAMP = "2026-11-26T09:00:00Z"


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    root = tmp_path_factory.mktemp("accept-sweep")
    accepted = _mini_build(SPEC, root / "accepted", "fp-accepted")
    edited = _mini_build(EDITED_SPEC, root / "edited", "fp-edited")
    home = root / "home"
    taken = snapshot.take(accepted, home, spec=SPEC)
    return accepted, edited, taken, home


def _fonts(builds) -> diff_units.Fonts:
    accepted, edited, _taken, _home = builds
    return diff_units.Fonts(accepted / "Senior-Regular.otf", edited / "Senior-Regular.otf")


def _shards(out: Path, manifest: dict) -> dict[str, bytes]:
    return {part: (out / part).read_bytes() for meta in manifest["classes"] for part in meta["shards"]}


def _units(out: Path, manifest: dict) -> list[dict]:
    return [unit for part in _shards(out, manifest) for unit in json.loads((out / part).read_text())]


def _without_certificates(tasks, width):
    """Run the tasks here, as the pool would, with no certificates, so the change stands for one no corpus-build candidate shows."""
    assert width >= 1
    return [accept_sweep.run_task(dataclasses.replace(task, certificates=())) for task in tasks]


def _in_process(tasks, width):
    assert width >= 1
    return [accept_sweep.run_task(task) for task in tasks]


# --- the context parameter ------------------------------------------------------------


def test_the_sweep_holds_the_texts_of_any_context_and_counts_them():
    alphabet = ("a", "b", " ")
    for context in (0, 1, 2, 3):
        layers = list(diff_units.sweep_layers(alphabet, "X", context))
        assert len(layers) == diff_units.sweep_size(len(alphabet), context)
        assert [total for total, _text in layers] == sorted(total for total, _text in layers)
        assert all(len(text) == total + 1 and "X" in text for total, text in layers)
    assert list(diff_units.sweep_texts(alphabet, "X")) == [
        text for _total, text in diff_units.sweep_layers(alphabet, "X", diff_units.SWEEP_CONTEXT)
    ]
    assert diff_units.ACCEPT_SWEEP_CONTEXT == diff_units.SWEEP_CONTEXT + 1


def test_the_corpus_build_at_its_own_context_with_no_record_is_the_build_it_was(builds, tmp_path):
    """The context is a parameter, and the corpus build's default is `SWEEP_CONTEXT`: a build that names it, beside a record for other inputs, writes the corpus a build that names neither writes, byte for byte, with the fixed `accept_sweep` component."""
    _accepted, edited, taken, _home = builds
    plain = build.build_table_diff(tmp_path / "plain", taken.directory, edited, spec=EDITED_SPEC)
    stray = tmp_path / "stray.json"
    sweep_record.write(
        stray,
        sweep_record.SweepKey("0" * 64, "fp-edited", "0" * 64, diff_units.SWEEP_CONTEXT, 3),
        ran_at=STAMP,
        cost={},
        configs=("default",),
        finds={"default": [(JUNCTION_TEXT, CHANGE)]},
    )
    named = build.build_table_diff(
        tmp_path / "named",
        taken.directory,
        edited,
        spec=EDITED_SPEC,
        context=diff_units.SWEEP_CONTEXT,
        sweep_path=stray,
    )
    assert named == plain and plain["inputs_fingerprint"]["accept_sweep"] == sweep_record.NO_FINDS
    assert _shards(tmp_path / "named", named) == _shards(tmp_path / "plain", plain)


def test_the_changed_letters_are_the_ones_the_finder_finds_units_for(builds):
    _accepted, edited, taken, _home = builds
    diffs = list(build.config_diffs(taken, edited, lambda: EDITED_SPEC))
    assert [diff.config for diff in diffs] == ["default", "ss03", "ss04", "ss05", "ss03+ss05", "ss07"]
    letters = {diff.config: diff_units.changed_letters(diff.settlement) for diff in diffs}
    assert {config: sorted(changed) for config, (changed, _prov) in letters.items() if changed} == {
        config: ["qsTea"] for config in CHANGED
    }
    assert letters["ss03"] == ({}, {"qsTea"})


# --- one task -------------------------------------------------------------------------


def test_a_task_returns_a_change_only_more_context_shows_with_its_shortest_text(builds):
    """·Tea's cell change shows only after ·Day+Utter, two letters to its left, so a task whose corpus-build context is 0 or 1 finds it in `·Day·Utter·Tea`, and one whose corpus-build context is 2 sees it among the corpus build's own candidates and returns nothing."""
    fonts = _fonts(builds)
    alphabet = diff_units.spec_alphabet(EDITED_SPEC)
    cell = diff_units.CellChange("qsTea", "qsTea.half.en-y5.en-ext-1", "qsTea.half.en-y5")
    for ordinary in (0, 1):
        found = diff_units.sweep_finds(
            fonts, "default", "qsTea", TEA, alphabet, words=(TEA,), ordinary_context=ordinary, context=2
        )
        assert found.cells == {cell: JUNCTION_TEXT} and not found.seen
        assert found.texts == diff_units.sweep_size(len(alphabet), 2)
    seen = diff_units.sweep_finds(
        fonts, "default", "qsTea", TEA, alphabet, words=(TEA,), ordinary_context=2, context=2
    )
    assert seen.cells == {} and seen.seen == {cell}
    narrow = diff_units.sweep_finds(fonts, "default", "qsTea", TEA, alphabet, ordinary_context=0, context=1)
    assert narrow.cells == {} and narrow.seen == frozenset()


def test_a_certificate_that_shows_the_change_makes_it_no_find(builds):
    """The corpus build shapes the letter's certificates too, so a change one of them shows is shown already, however long the certificate."""
    fonts = _fonts(builds)
    alphabet = diff_units.spec_alphabet(EDITED_SPEC)
    certificate = ""
    found = diff_units.sweep_finds(
        fonts, "default", "qsTea", TEA, alphabet, certificates=(certificate,), ordinary_context=0, context=2
    )
    assert found.cells == {} and len(found.seen) == 1
    assert found.texts == 1 + diff_units.sweep_size(len(alphabet), 2)


def test_the_plan_is_one_task_per_configuration_and_changed_letter_with_its_certificates(builds):
    _accepted, edited, taken, _home = builds
    tasks = accept_sweep.plan(taken, edited, spec=EDITED_SPEC, ordinary_context=0, context=2)
    assert [(task.config, task.rune, task.word) for task in tasks] == [
        (config, "qsTea", TEA) for config in CHANGED
    ]
    assert all(task.certificates and task.words == (TEA,) for task in tasks)
    assert tasks[0].texts == len(tasks[0].certificates) + diff_units.sweep_size(len(tasks[0].alphabet), 2)


def test_a_build_with_no_changed_letter_plans_no_task(builds):
    accepted, _edited, taken, _home = builds
    assert accept_sweep.plan(taken, accepted, spec=SPEC) == []


# --- the merge ------------------------------------------------------------------------


def _finds(config, rune, word, *, seen=(), substituted=(), unattributed=(), cells=None, quiet=()):
    return diff_units.SweepFinds(
        config,
        rune,
        word,
        10,
        frozenset(seen),
        frozenset(substituted),
        frozenset(unattributed),
        cells or {},
        tuple(quiet),
    )


def test_the_merge_keeps_what_no_task_shows_shortest_text_first_whatever_the_order():
    one = diff_units.CellChange("qsTea", "qsTea.a", "qsTea.b")
    two = diff_units.CellChange("qsIt", "qsIt.a", "qsIt.b")
    shown = diff_units.CellChange("qsIt", "qsIt.c", "qsIt.d")
    finds = [
        _finds("default", "qsTea", "T", cells={one: "zzzT", two: "bbIT"}),
        _finds("default", "qsIt", "I", seen={shown}, cells={one: "aaaT", two: "abIT"}),
        _finds("default", "qsAh", "A", cells={shown: "AAAA"}, quiet=("xAyz",)),
        _finds("ss03", "qsAh", "A", quiet=("qAqq",)),
        _finds("ss04", "qsAh", "A", unattributed={"A"}, quiet=("qAqq",)),
        _finds("ss05", "qsAh", "A", substituted={"qsAh"}, quiet=("qAqq",)),
        _finds(
            "ss07",
            "qsAh",
            "A",
            cells={diff_units.CellChange("qsAh", "qsAh.a", "qsAh.b"): "AAAA"},
            quiet=("qA",),
        ),
    ]
    merged = diff_units.merge_sweep_finds(finds)
    assert merged == diff_units.merge_sweep_finds(reversed(finds))
    assert [(entry.text, entry.change) for entry in merged["default"]] == [
        ("aaaT", "settlement qsTea: qsTea.a -> qsTea.b"),
        ("abIT", "settlement qsIt: qsIt.a -> qsIt.b"),
        ("xAyz", "quiet qsAh"),
    ]
    assert [(entry.text, entry.change) for entry in merged["ss03"]] == [("qAqq", "quiet qsAh")]
    assert merged["ss04"] == [] and merged["ss05"] == []
    assert [entry.change for entry in merged["ss07"]] == ["settlement qsAh: qsAh.a -> qsAh.b"]


# --- the record -----------------------------------------------------------------------


def _key(**changes) -> sweep_record.SweepKey:
    return dataclasses.replace(sweep_record.SweepKey("d" * 64, "tables", "f" * 64, 0, 2), **changes)


def test_a_record_applies_only_to_the_inputs_it_was_swept_for(tmp_path):
    path = tmp_path / "accept-sweep.json"
    assert (
        sweep_record.applicable(path, _key()) is None
        and sweep_record.component(None) == sweep_record.NO_FINDS
    )
    record = sweep_record.write(
        path,
        _key(),
        ran_at=STAMP,
        cost={"tasks": 1},
        configs=("default", "ss03"),
        finds={"default": [(JUNCTION_TEXT, CHANGE)]},
    )
    assert sweep_record.applicable(path, _key()) == record
    assert record["configs"] == {"default": ["E653:E67A:E652"], "ss03": []}
    assert sweep_record.texts(record) == {"default": [JUNCTION_TEXT], "ss03": []}
    assert sweep_record.changes(record) == [CHANGE]
    for moved in (
        {"snapshot": "e" * 64},
        {"tables": "other"},
        {"font": "0" * 64},
        {"context": 3},
        {"ordinary_context": 1},
    ):
        assert sweep_record.applicable(path, _key(**moved)) is None, moved
    assert sweep_record.key_for(None, "tables", "f" * 64, 0, 2) is None
    assert sweep_record.key_for("d" * 64, None, "f" * 64, 0, 2) is None


def test_the_component_is_the_records_finds_and_the_fixed_value_for_none(tmp_path):
    path = tmp_path / "accept-sweep.json"
    empty = sweep_record.write(path, _key(), ran_at=STAMP, cost={}, configs=("default",), finds={})
    assert sweep_record.component(empty) == sweep_record.NO_FINDS
    found = sweep_record.write(
        path,
        _key(),
        ran_at=STAMP,
        cost={},
        configs=("default",),
        finds={"default": [(JUNCTION_TEXT, CHANGE)]},
    )
    again = sweep_record.write(
        path,
        _key(),
        ran_at="2026-11-27T00:00:00Z",
        cost={"seconds": 9},
        configs=("default",),
        finds={"default": [(JUNCTION_TEXT, CHANGE)]},
    )
    assert sweep_record.component(found) == sweep_record.component(again) != sweep_record.NO_FINDS
    (path.parent / "other.json").write_text(json.dumps({**found, "format": "something-else"}))
    assert sweep_record.read(path.parent / "other.json") is None


# --- the build ------------------------------------------------------------------------


def _record_for(path: Path, taken, edited, finds, ordinary=0, context=2) -> dict:
    key = sweep_record.key_for(
        taken.digest,
        snapshot.tables_stamp(edited),
        fingerprint.file_sha256(edited / "Senior-Regular.otf"),
        ordinary,
        context,
    )
    assert key is not None
    return sweep_record.write(path, key, ran_at=STAMP, cost={}, configs=CHANGED, finds=finds)


def test_the_build_takes_an_applicable_records_texts_as_candidates_and_makes_units_of_them(builds, tmp_path):
    """At a corpus-build context of 0 ·Tea's change is shown only in a five-letter certificate. A record that names `·Day·Utter·Tea` under every configuration the change holds under gives the build a shorter candidate, so the change is one unit with the join rows there, the unit a build at context 2 makes, with its id; a second build over the same record writes the same bytes."""
    _accepted, edited, taken, _home = builds
    wide = build.build_table_diff(tmp_path / "wide", taken.directory, edited, spec=EDITED_SPEC, context=2)
    narrow = build.build_table_diff(tmp_path / "narrow", taken.directory, edited, spec=EDITED_SPEC, context=0)
    junction = {
        unit["id"]: unit for unit in _units(tmp_path / "wide", wide) if unit["codepoints"] == "E653:E67A:E652"
    }
    (junction_id,) = junction
    assert junction_id not in {unit["id"] for unit in _units(tmp_path / "narrow", narrow)}
    path = tmp_path / "accept-sweep.json"
    record = _record_for(path, taken, edited, {config: [(JUNCTION_TEXT, CHANGE)] for config in CHANGED})
    kwargs = {"spec": EDITED_SPEC, "context": 0, "accept_context": 2, "sweep_path": path}
    found = build.build_table_diff(tmp_path / "found", taken.directory, edited, **kwargs)
    units = {unit["id"]: unit for unit in _units(tmp_path / "found", found)}
    assert junction_id in units and units[junction_id]["row_key"] == junction[junction_id]["row_key"]
    assert CHANGE not in {unit["row_key"] for unit in units.values()}
    assert (
        found["inputs_fingerprint"]["accept_sweep"] == sweep_record.component(record) != sweep_record.NO_FINDS
    )
    assert build.check_output_dir(tmp_path / "found") == []
    again = build.build_table_diff(tmp_path / "again", taken.directory, edited, **kwargs)
    assert again == found and _shards(tmp_path / "again", again) == _shards(tmp_path / "found", found)
    elsewhere = build.build_table_diff(
        tmp_path / "elsewhere",
        taken.directory,
        edited,
        spec=EDITED_SPEC,
        context=0,
        accept_context=3,
        sweep_path=path,
    )
    assert elsewhere["inputs_fingerprint"]["accept_sweep"] == sweep_record.NO_FINDS


def test_a_record_with_finds_is_an_input_whose_time_stamps_the_corpus(builds, tmp_path):
    _accepted, edited, taken, _home = builds
    path = tmp_path / "accept-sweep.json"
    _record_for(path, taken, edited, {"default": [(JUNCTION_TEXT, CHANGE)]})
    later = max(Path(edited).stat().st_mtime, taken.directory.stat().st_mtime) + 86_400 * 365
    os.utime(path, (later, later))
    manifest = build.build_table_diff(
        tmp_path / "out",
        taken.directory,
        edited,
        spec=EDITED_SPEC,
        context=0,
        accept_context=2,
        sweep_path=path,
    )
    assert manifest["generated_at"] == build._generated_at(path)


# --- the fingerprint and the corpus skip ----------------------------------------------


def _repo_with(builds, root: Path) -> Path:
    """A checkout under `root` whose snapshot home holds the accepted mini snapshot and whose engine/out/build is the edited mini build."""
    _accepted, edited, taken, _home = builds
    home = root / status.SNAPSHOT_HOME
    shutil.copytree(taken.directory, home / taken.directory.name)
    (home / snapshot.CURRENT_NAME).write_text(taken.directory.name + "\n")
    shutil.copytree(edited, root / "engine" / "out" / "build")
    return root / "engine" / "out" / "build"


def test_the_fingerprint_component_moves_with_an_applicable_record_with_finds(builds, tmp_path):
    _accepted, _edited, taken, _home = builds
    root = tmp_path / "repo"
    build_out = _repo_with(builds, root)
    before = status.table_diff_inputs(root, build_out=build_out)
    assert (
        before["accept_sweep"] == sweep_record.NO_FINDS and tuple(before) == fingerprint.TABLE_DIFF_COMPONENTS
    )
    path = root / sweep_record.RECORD_RELATIVE
    _record_for(path, taken, build_out, {}, diff_units.SWEEP_CONTEXT, diff_units.ACCEPT_SWEEP_CONTEXT)
    assert status.table_diff_inputs(root, build_out=build_out) == before
    record = _record_for(
        path,
        taken,
        build_out,
        {"default": [(JUNCTION_TEXT, CHANGE)]},
        diff_units.SWEEP_CONTEXT,
        diff_units.ACCEPT_SWEEP_CONTEXT,
    )
    after = status.table_diff_inputs(root, build_out=build_out)
    assert after == {**before, "accept_sweep": sweep_record.component(record)}
    _record_for(path, taken, build_out, {"default": [(JUNCTION_TEXT, CHANGE)]}, 0, 2)
    assert status.table_diff_inputs(root, build_out=build_out) == before


def test_the_cycle_rebuilds_a_corpus_once_a_record_with_finds_applies(builds, tmp_path):
    """`corpus_build_skippable` compares the components a build would record now, so a corpus built before a record with finds was written is rebuilt, and one built with it, or beside a record that found nothing, is skipped."""
    from engine.tools.artifact_cycle import corpus_build_skippable

    _accepted, _edited, taken, _home = builds
    root = tmp_path / "repo"
    build_out = _repo_with(builds, root)
    corpus = root / "engine" / "out" / "review"
    current = snapshot.current(root / status.SNAPSHOT_HOME)
    path = root / sweep_record.RECORD_RELATIVE
    kwargs = {"spec": EDITED_SPEC, "repo_root": root, "sweep_path": path}
    build.build_table_diff(corpus, current, build_out, **kwargs)
    assert corpus_build_skippable(root, corpus)
    _record_for(path, taken, build_out, {}, diff_units.SWEEP_CONTEXT, diff_units.ACCEPT_SWEEP_CONTEXT)
    assert corpus_build_skippable(root, corpus)
    _record_for(
        path,
        taken,
        build_out,
        {"default": [(JUNCTION_TEXT, CHANGE)]},
        diff_units.SWEEP_CONTEXT,
        diff_units.ACCEPT_SWEEP_CONTEXT,
    )
    assert not corpus_build_skippable(root, corpus)
    shutil.rmtree(corpus)
    build.build_table_diff(corpus, current, build_out, **kwargs)
    assert corpus_build_skippable(root, corpus)


# --- the accept -----------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path, builds):
    """A checkout the accept reads: the served corpus's manifest, written by hand for the edited mini build, a store approving its one human unit, and a snapshot home holding the accepted mini snapshot as current."""
    _accepted, edited, taken, _home = builds
    home = tmp_path / "home"
    shutil.copytree(taken.directory, home / taken.directory.name)
    (home / snapshot.CURRENT_NAME).write_text(taken.directory.name + "\n")
    manifest = {
        "mode": "table-diff",
        "generated_at": STAMP,
        "human_unit_ids": ["u-1AAAAAAAAAA"],
        "totals": {"units": 1},
        "baseline": {"commit": None, "taken_at": None, "digest": None, "present": False},
        "fonts": {"after": {"sha256": fingerprint.file_sha256(edited / "Senior-Regular.otf")}},
        "inputs_fingerprint": {"tables": "fp-edited", "accept_sweep": sweep_record.NO_FINDS},
    }
    path = tmp_path / "engine" / "out" / "review" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    (tmp_path / AUTOSAVE_NAME).write_text(
        json.dumps(
            {
                "format": "ams-review-verdicts/1",
                "manifest_generated_at": STAMP,
                "exported_at": STAMP,
                "verdicts": [{"unit": "u-1AAAAAAAAAA", "verdict": "approve", "note": "", "at": STAMP}],
            }
        )
    )
    return tmp_path


def _accept(root: Path, tables: Path, execute=_without_certificates, **kwargs) -> int:
    store = root / AUTOSAVE_NAME
    kwargs.setdefault(
        "sweep",
        lambda run=True: accept_sweep.ensure(
            store=store,
            tables=tables,
            home=root / "home",
            run=run,
            spec=EDITED_SPEC,
            ordinary_context=0,
            context=2,
            execute=execute,
        ),
    )
    return accept.run(
        store=store,
        corpus=root / "engine" / "out" / "review",
        tables=tables,
        home=root / "home",
        pointer=root / snapshot.POINTER_RELATIVE,
        readiness=lambda: ({"checks": {}}, True),
        tell=lambda _store, _digest: False,
        take=lambda tables_dir, home: snapshot.take(tables_dir, home, spec=EDITED_SPEC),
        **kwargs,
    )


def test_the_accept_sweeps_when_no_record_answers_and_refuses_on_finds_the_corpus_lacks(repo, builds, capsys):
    _accepted, edited, _taken, _home = builds
    path = repo / sweep_record.RECORD_RELATIVE
    assert _accept(repo, edited) == 1
    out = capsys.readouterr().out
    assert f"accept sweep: {len(CHANGED)} tasks" in out and "found 1 change" in out
    assert "the four-letter sweep found 1 change that the Queue does not show" in out
    assert "`make artifact-cycle` adds it to the corpus for verdicts" in out
    assert "nothing written but the four-letter sweep's record" in out
    record = sweep_record.read(path)
    assert record is not None and sweep_record.changes(record) == [CHANGE]
    assert {config: values for config, values in record["configs"].items() if values} == {
        config: ["E653:E67A:E652"] for config in CHANGED
    }
    assert record["cost"]["tasks"] == len(CHANGED) and record["cost"]["width"] >= 1
    assert _accept(repo, edited, execute=lambda _tasks, _width: pytest.fail("swept again")) == 1
    assert "holds the sweep of these inputs" in capsys.readouterr().out
    assert _accept(repo, edited, apply=True, execute=lambda _tasks, _width: pytest.fail("swept again")) == 1
    out = capsys.readouterr().out
    assert "the Queue does not show" in out and "Nothing was written." in out
    assert snapshot.read_pointer(repo / snapshot.POINTER_RELATIVE) is None


def test_the_accept_proceeds_once_the_corpus_was_built_with_the_records_finds(repo, builds, capsys):
    _accepted, edited, _taken, _home = builds
    assert _accept(repo, edited) == 1
    capsys.readouterr()
    record = sweep_record.read(repo / sweep_record.RECORD_RELATIVE)
    manifest_path = repo / "engine" / "out" / "review" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["inputs_fingerprint"]["accept_sweep"] = sweep_record.component(record)
    manifest_path.write_text(json.dumps(manifest))
    assert _accept(repo, edited) == 0
    out = capsys.readouterr().out
    assert (
        "ERROR" not in out
        and "take the four-letter sweep's record accept-sweep.json, which found 1 change" in out
    )
    assert _accept(repo, edited, apply=True) == 0
    assert snapshot.read_pointer(repo / snapshot.POINTER_RELATIVE) is not None


def test_the_apply_sweeps_before_it_takes_the_snapshot(repo, builds, capsys):
    _accepted, edited, _taken, _home = builds
    assert _accept(repo, edited, apply=True) == 1
    out = capsys.readouterr().out
    assert f"accept sweep: {len(CHANGED)} tasks" in out and "the Queue does not show" in out
    assert "Nothing was written but the four-letter sweep's record" in out
    assert snapshot.read_pointer(repo / snapshot.POINTER_RELATIVE) is None


def test_a_sweep_that_finds_nothing_lets_the_accept_through(repo, builds, capsys):
    _accepted, edited, _taken, _home = builds
    assert _accept(repo, edited, execute=_in_process) == 0
    out = capsys.readouterr().out
    assert "found nothing the corpus build's candidates do not show" in out and "ERROR" not in out
    assert not sweep_record.has_finds(sweep_record.read(repo / sweep_record.RECORD_RELATIVE))


def test_with_no_changed_letter_the_sweep_is_nothing(repo, builds, capsys):
    accepted, _edited, _taken, _home = builds
    manifest_path = repo / "engine" / "out" / "review" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fonts"]["after"]["sha256"] = fingerprint.file_sha256(accepted / "Senior-Regular.otf")
    manifest["inputs_fingerprint"]["tables"] = "fp-accepted"
    manifest_path.write_text(json.dumps(manifest))
    assert _accept(repo, accepted, execute=lambda _tasks, _width: pytest.fail("a pool for no task")) == 0
    out = capsys.readouterr().out
    assert "no letter's rules changed since the accepted snapshot, so there is nothing to sweep" in out
    record = sweep_record.read(repo / sweep_record.RECORD_RELATIVE)
    assert record is not None and record["cost"]["tasks"] == 0 and not sweep_record.has_finds(record)


def test_a_retake_skips_the_sweep(repo, builds, capsys):
    _accepted, edited, _taken, _home = builds
    called: list[bool] = []
    assert (
        _accept(
            repo,
            edited,
            apply=True,
            retake=True,
            stamped=lambda _tables: True,
            sweep=lambda run=True: called.append(run),
        )
        == 0
    )
    assert called == [] and "skip the four-letter sweep" in capsys.readouterr().out
    assert not (repo / sweep_record.RECORD_RELATIVE).exists()


def test_the_dry_run_only_reads_the_record_while_a_refusal_stands(repo, builds, capsys):
    """While a refusal before the sweep stands (here a pass holding the pass lock), the dry run reads the record that answers for the inputs and sweeps nothing, so no pool runs beside a pass."""
    from engine.review.store_lock import hold_flock
    from engine.tools import store_guards

    _accepted, edited, _taken, _home = builds
    store = repo / AUTOSAVE_NAME
    with hold_flock(store_guards.pass_lock_path_for(store)):
        assert _accept(repo, edited, execute=lambda _tasks, _width: pytest.fail("swept beside a pass")) == 1
    out = capsys.readouterr().out
    assert "a pass holds" in out and "run the four-letter sweep once the refusals below clear" in out
    assert not (repo / sweep_record.RECORD_RELATIVE).exists()


# --- the pool -------------------------------------------------------------------------


def test_the_width_divides_the_memory_by_a_task_capped_at_the_cores_and_the_tasks(monkeypatch):
    monkeypatch.setattr(memory_budget, "usable_cores", lambda: 18)
    big = 48 * 2**30
    assert accept_sweep.sweep_width(14, total_bytes=big)[0] == 14
    assert accept_sweep.sweep_width(40, total_bytes=big)[0] == 18
    assert accept_sweep.sweep_width(0, total_bytes=big)[0] == 1
    reserve = memory_budget.os_reserve_bytes(total_bytes=big)
    tight = reserve + 3 * accept_sweep.SWEEP_TASK_BYTES
    width, fit = accept_sweep.sweep_width(14, total_bytes=tight)
    assert width == 3 and fit == memory_budget.describe_fit(
        accept_sweep.SWEEP_TASK_BYTES, cap=14, total_bytes=tight
    )
    assert accept_sweep.sweep_width(14, total_bytes=reserve)[0] == 1
