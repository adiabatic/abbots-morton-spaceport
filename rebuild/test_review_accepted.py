"""Tests for `rebuild.review.accepted`, the accepted list's reader: the format and its refusals, the list's stamp and its pairing with the store's `accepted` field, the rule every reader answers through, the bulk view and the review app's field, the folds the shared samples pin, the list's path, and the names the module spells out instead of importing them. Then the readers on the read path, run whole over the contract fixture's human units: a store folded and the same store unfolded give each of them the same verdicts, each refuses a store beside a list it was not folded against, each reads a store under another name beside the list `--accepted` names, and each reads an Export of the store, which holds its records alone, as it reads the store.

Most cases come from `rebuild/review/fixtures/accepted-samples.json`, whose README section says what each part holds. `fold_state` writes one side of a fold sample out as a store document and a list file, for any test that runs a reader over both sides.
"""

from __future__ import annotations

import ast
import collections
import contextlib
import dataclasses
import functools
import hashlib
import io
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import types
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from pathlib import Path
from unittest import mock

import pytest
import yaml

from rebuild.review import accepted, export, journal, landing, unit_cache, unit_index, verdict_store
from rebuild.review.serve import write_pending_store
from rebuild.review.verdict_store import DELTA_FORMAT, ServedCorpus, VerdictStore
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import (
    close_out,
    complaint_list,
    duplicate_verdicts,
    held_store,
    merge_verdicts,
    novelty_order,
    rekey_verdicts,
    review_queue,
    standing_probe,
    standing_verdicts,
    verdict_ready,
)
from rebuild.tools import verdict_update as vu
from rebuild.tools.verdict_notes import cap_markers

FIXTURES = Path(__file__).resolve().parent / "review" / "fixtures"
SAMPLES = FIXTURES / "accepted-samples.json"
CLOSED_AT = "2026-11-05T18:00:00Z"


def _samples() -> dict:
    return json.loads(SAMPLES.read_text(encoding="utf-8"))


def _list_at(directory: Path, text: str | None) -> Path:
    path = directory / "rebuild" / "accepted-units.txt"
    if text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return path


def fold_state(directory: Path, state: dict, stamp: str) -> tuple[accepted.Accepted, dict]:
    """Write one side of a fold sample (`before` or `after`) under `directory` and return its list, as `accepted.read` reads it, and its store document, stamped `stamp`, whose `accepted` field names that list when the side has one, and whose records and tombstones on that list's ids are its override section."""
    listed = state["list"]
    text = (
        None if listed is None else accepted.render(listed["closed_at"], listed["verdicts"]).decode("ascii")
    )
    held = accepted.read(_list_at(directory, text))
    document = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "verdicts": [record for record in state["verdicts"] if record["unit"] not in held.verdicts],
        "cleared": [entry for entry in state["cleared"] if entry["unit"] not in held.verdicts],
    }
    section = {
        "verdicts": [record for record in state["verdicts"] if record["unit"] in held.verdicts],
        "cleared": [entry for entry in state["cleared"] if entry["unit"] in held.verdicts],
    }
    if section["verdicts"] or section["cleared"]:
        document["overrides"] = section
    if (field := held.field()) is not None:
        document["accepted"] = field
    return held, document


def _view(tmp_path: Path, form: str = "parsed") -> tuple[dict, accepted.View]:
    """The view sample's store read through its list: parsed (`accepted.view_of`), or held as record lines with a table over them, as the verdict update hands its prepared store to its readers (`held_store.Held.view`)."""
    scenario = _samples()["view"]
    held = accepted.read(_list_at(tmp_path, scenario["list"]))
    if form == "held":
        store = tmp_path / "store.json"
        store.write_text(json.dumps(scenario["store"]), encoding="utf-8")
        return scenario, _held(store).view(held)
    return scenario, accepted.view_of(scenario["store"], held, store="the store")


def test_read_and_iter_lines_give_each_list_sample_its_recorded_answer(tmp_path):
    samples = _samples()["lists"]
    assert {"verdicts" in sample for sample in samples} == {True, False}
    for index, sample in enumerate(samples):
        path = tmp_path / f"{index}.txt"
        path.write_bytes(sample["text"].encode("utf-8"))
        if "verdicts" in sample:
            held = accepted.read(path)
            assert dict(held.verdicts) == sample["verdicts"], sample["why"]
            assert held.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
            assert list(accepted.iter_lines(path)) == list(sample["verdicts"].items())
            assert accepted.read_stamp(path) == held.stamp
            continue
        readers: list[Callable[[Path], object]] = [
            accepted.read,
            lambda file: list(accepted.iter_lines(file)),
        ]
        if sample["line"] == 1:
            readers.append(accepted.read_stamp)
        for reader in readers:
            with pytest.raises(accepted.Malformed) as refusal:
                reader(path)
            assert (refusal.value.reason, refusal.value.line) == (accepted.MALFORMED, sample["line"]), sample[
                "why"
            ]
            assert f"{path} line {sample['line']} " in str(refusal.value)


def test_a_missing_list_is_the_empty_list(tmp_path):
    path = tmp_path / "accepted-units.txt"
    held = accepted.read(path)
    assert held == accepted.Accepted.empty(path)
    assert (held.sha256, held.closed_at, held.field(), dict(held.verdicts)) == (None, None, None, {})
    assert accepted.read_stamp(path) == held.stamp
    assert list(accepted.iter_lines(path)) == []


def test_render_writes_the_bytes_read_reads_back_and_refuses_what_the_format_cannot_hold():
    for sample in _samples()["lists"]:
        if "verdicts" in sample:
            closed_at = sample["text"].partition("closed_at=")[2].partition("\n")[0]
            shuffled = dict(reversed(sample["verdicts"].items()))
            assert accepted.render(closed_at, shuffled) == sample["text"].encode("ascii"), sample["why"]
    for closed_at, verdicts in (
        ("2026-11-05T18:00:00.000Z", {}),
        (CLOSED_AT, {"e-1AAAAAAAAAA": "approve"}),
        (CLOSED_AT, {"u-1AAAAAAAAAA": "neither"}),
        (CLOSED_AT, {"u-1AAAAAAAAAA": "skip"}),
    ):
        with pytest.raises(ValueError):
            accepted.render(closed_at, verdicts)


def test_pair_gives_each_pairing_sample_its_recorded_answer_and_names_both_lists(tmp_path):
    samples = _samples()["pairings"]
    assert {sample["pairs"] for sample in samples} == {True, False}
    for index, sample in enumerate(samples):
        path = _list_at(tmp_path / str(index), sample["list"])
        held = accepted.read(path)
        for listed in (held, accepted.read_stamp(path)):
            if sample["pairs"]:
                accepted.pair(sample["store"], listed, store="the store")
                continue
            with pytest.raises(accepted.Mismatch) as refusal:
                accepted.pair(sample["store"], listed, store="the store")
            assert refusal.value.reason == accepted.MISMATCH, sample["why"]
            named = sample["store"].get("accepted")
            for sha256 in (held.sha256, named.get("sha256") if isinstance(named, dict) else None):
                assert sha256 is None or sha256 in str(refusal.value), sample["why"]


@pytest.mark.parametrize("form", ["parsed", "held"])
def test_every_reader_answers_a_unit_by_its_record_then_its_tombstone_then_the_list(tmp_path, form):
    scenario, view = _view(tmp_path, form)
    for unit, answer in scenario["answers"].items():
        assert view.verdict_of(unit) == answer, unit
        assert accepted.verdict_of(unit, view.records, view.overrides, view.accepted) == answer, unit
        assert (unit in view, view.get(unit)) == (answer is not None, answer), unit
        if answer is None:
            with pytest.raises(KeyError):
                view[unit]
        else:
            assert view[unit] == answer


@pytest.mark.parametrize("form", ["parsed", "held"])
def test_a_view_walks_the_stores_units_then_the_listed_ids_it_answers_from_the_list(tmp_path, form):
    scenario, view = _view(tmp_path, form)
    answered = {unit: answer for unit, answer in scenario["answers"].items() if answer is not None}
    from_list = [unit for unit in answered if unit not in view.records and unit not in view.overrides]
    assert [unit for unit in answered if view.is_accepted(unit)] == from_list
    assert list(view.from_list()) == [(unit, answered[unit]["verdict"]) for unit in from_list]
    assert list(view) == [*view.records, *view.overrides.records, *from_list]
    assert len(view) == len(answered)
    assert dict(view.items()) == answered


def test_a_held_store_answers_through_its_table_as_its_records_do(monkeypatch):
    """The verdict update holds its prepared store as sorted record lines with a table over them (`held_store.Held`) and patches it by address after each merge (`held_store.patched`). Over random stores and random changes, unit ids of the table's 13 bytes and of other widths, 13-byte ids under prefixes other than `u-` that sort before and after it, verdicts of the six kinds and others among them, which the table cannot hold and keeps beside it, its records answer `in`, `[]`, `get`, iteration, `len`, `items` and `values` as a dict of the same records does, its counts are the merge's, and its document is the layout the land writes (`landing.document_bytes`)."""
    monkeypatch.setattr(journal, "now_stamp", lambda: "2026-07-30T00:00:00Z")
    rng = random.Random(18)
    alphabet = accepted.ID_ALPHABET
    for _round in range(60):
        units = sorted(
            {
                rng.choice(
                    (
                        "u-" + "".join(rng.choice(alphabet) for _ in range(11)),
                        "u-" + "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14))),
                        "u-é" + "".join(rng.choice(alphabet) for _ in range(9)),
                        rng.choice(("a-", "e-", "x-", "u_"))
                        + "".join(rng.choice(alphabet) for _ in range(11)),
                    )
                )
                for _ in range(rng.randint(0, 40))
            }
        )
        kinds = ("approve", "reject", "either", "identical", "neither", "skip", "bogus")

        def record(unit: str) -> dict:
            return {
                "unit": unit,
                "verdict": rng.choice(kinds),
                "note": rng.choice(("", "a note")),
                "at": f"2026-07-{rng.randint(10, 28)}",
            }

        records = {unit: record(unit) for unit in units if rng.random() < 0.7}
        builder = held_store.Builder()
        for unit in sorted(records):
            builder.add(records[unit])
        held = builder.held("S", None, {}, accepted.Overrides())
        changes = {unit: record(unit) for unit in rng.sample(units, min(len(units), rng.randint(0, 6)))}
        if rng.random() < 0.7:
            held = held_store.patched(
                held, [changes[unit] for unit in sorted(changes)], {}, accepted.Overrides()
            )
            records.update(changes)
        table = held.records()
        for unit in [*units, "u-absent", "u-1AAAAAAAAAA", "x-1AAAAAAAAAA"]:
            assert (unit in table, table.get(unit)) == (unit in records, records.get(unit)), unit
            if unit in records:
                assert table[unit] == records[unit]
            else:
                with pytest.raises(KeyError):
                    table[unit]
        assert list(table) == sorted(records) and len(table) == len(records)
        assert dict(table.items()) == records and list(table.values()) == [
            records[unit] for unit in sorted(records)
        ]
        assert held_store.counts(held) == (
            len(records),
            sum(r["verdict"] != "skip" for r in records.values()),
        )
        assert b"".join(held.chunks("2026-07-30T00:00:00Z")) == landing.document_bytes(
            landing.Store("S", records)
        )


def test_the_app_field_lists_the_accepted_units_on_the_served_corpus(tmp_path):
    scenario, view = _view(tmp_path)
    assert view.app_field(frozenset(scenario["human_unit_ids"])) == scenario["field"]
    assert scenario["records"] == [
        dict(view.verdict_of(unit) or {}, accepted=True)
        for verdict in accepted.MARKS
        for unit in scenario["field"][verdict]
    ]
    nothing = accepted.View({}, {}, accepted.Accepted.empty(tmp_path / "none.txt"))
    assert nothing.app_field(frozenset()) == {
        "sha256": None,
        "closed_at": None,
        "approve": [],
        "either": [],
        "identical": [],
    }


def test_the_merge_and_the_carry_keep_out_only_an_equal_or_stale_record_on_an_accepted_unit(tmp_path):
    _scenario, view = _view(tmp_path)
    held = view.accepted
    unit, blank = "u-1AAAAAAAAAA", "u-1KKKKKKKKKK"
    after = "2026-11-06T10:00:00.000Z"
    cases = [
        ({"unit": unit, "verdict": "approve", "note": "", "at": after}, True, True),
        ({"unit": unit, "verdict": "approve", "note": None, "at": after}, True, True),
        ({"unit": unit, "verdict": "approve", "note": "a note typed after", "at": after}, False, False),
        ({"unit": unit, "verdict": "either", "note": "", "at": after}, False, False),
        ({"unit": unit, "verdict": "reject", "note": "", "at": CLOSED_AT}, False, True),
        ({"unit": unit, "verdict": "reject", "note": "", "at": "2026-11-05T18:00:00.999Z"}, False, True),
        ({"unit": blank, "verdict": "reject", "note": "", "at": "2026-10-01T00:00:00.000Z"}, False, False),
        ({"unit": blank, "note": "", "at": after}, False, False),
    ]
    for record, matches, keeps_out in cases:
        assert (held.matches(record), held.keeps_out(record)) == (matches, keeps_out), record


def test_each_fold_sample_keeps_every_human_units_verdict_kind_and_follows_the_fold_rule(tmp_path):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    human = manifest["human_unit_ids"]
    for index, fold in enumerate(_samples()["folds"]):
        before, after = fold["before"], fold["after"]
        views = {}
        for side in ("before", "after"):
            held, document = fold_state(tmp_path / str(index) / side, fold[side], manifest["generated_at"])
            views[side] = accepted.view_of(document, held, store=side)
        named = {
            record["unit"] for state in (before, after) for record in (*state["verdicts"], *state["cleared"])
        } | {unit for state in (before, after) if state["list"] for unit in state["list"]["verdicts"]}
        assert named - set(human) == set(fold["off_corpus"]), fold["why"]
        assert set(fold["kinds"]) <= set(human)
        for unit in human:
            kinds = [(views[side].verdict_of(unit) or {}).get("verdict") for side in ("before", "after")]
            assert kinds == [fold["kinds"].get(unit)] * 2, (fold["why"], unit)
        folded = {
            unit: record["verdict"]
            for unit in human
            if (record := views["before"].verdict_of(unit))
            and record["verdict"] in accepted.ACCEPTING_VERDICTS
        }
        assert after["list"]["verdicts"] == folded, fold["why"]
        kept = [
            record for record in before["verdicts"] if record["verdict"] not in accepted.ACCEPTING_VERDICTS
        ]
        assert (after["verdicts"], after["cleared"]) == (kept, before["cleared"]), fold["why"]
        times = [record["at"] for record in before["verdicts"]]
        times += [before["list"]["closed_at"]] if before["list"] else []
        assert max(times) <= after["list"]["closed_at"], fold["why"]


def test_the_list_beside_a_store_is_the_live_stores_alone(tmp_path):
    assert (
        accepted.path_for(tmp_path / "verdicts-autosave.json") == tmp_path / "rebuild" / "accepted-units.txt"
    )
    with pytest.raises(ValueError, match="pass --accepted"):
        accepted.path_for(tmp_path / "prepared.json")
    with pytest.raises(ValueError, match="pass --list"):
        accepted.path_for(tmp_path / "Verdicts-Autosave.json", "--list")


def test_the_names_the_module_spells_out_agree_with_their_sources(tmp_path):
    assert (accepted.ID_ALPHABET, accepted.ID_SYMBOLS) == (unit_cache.BASE58_ALPHABET, unit_cache.ID_SYMBOLS)
    unit = unit_cache.unit_id_for(hashlib.sha256(b"a unit").hexdigest())
    written = _list_at(tmp_path, accepted.render(CLOSED_AT, {unit: "either"}).decode("ascii"))
    assert dict(accepted.read(written).verdicts) == {unit: "either"}
    assert accepted.AUTOSAVE_NAME == journal.AUTOSAVE_NAME
    assert accepted.ACCEPTING_VERDICTS == review_queue.ACCEPTING_VERDICTS
    assert accepted.EXPORT_FORMAT == verdict_store.EXPORT_FORMAT == journal.EXPORT_FORMAT
    for sample in json.loads((FIXTURES / "verdict-record-samples.json").read_text(encoding="utf-8")):
        held = sample["record"]
        assert accepted._well_formed(held) == verdict_store.well_formed(held) == sample["well_formed"]
    scenario, view = _view(tmp_path / "view")
    assert view.records == journal.latest_by_unit(scenario["store"]["verdicts"])
    assert view.cleared == journal.parse_cleared(scenario["store"])
    section = scenario["store"]["overrides"]
    assert view.overrides.records == journal.latest_by_unit(section["verdicts"])
    assert view.overrides.cleared == journal.parse_cleared(section)


def test_the_module_imports_the_standard_library_alone():
    """`rebuild.tools.review_queue` imports the module, so a repo import here would join the standing memo's code stamp (`standing_verdicts.MEMO_CODE_MODULES`)."""
    tree = ast.parse(Path(accepted.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." if node.level else (node.module or "").partition(".")[0])
    assert imported - set(sys.stdlib_module_names) == set()


def _fold_side(directory: Path, state: dict) -> Path:
    """Write one side of a fold sample under `directory` in the live store's layout, the store as verdicts-autosave.json stamped for the contract fixture with its list at rebuild/accepted-units.txt beside it, and return the store's path."""
    stamp = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["generated_at"]
    _held, document = fold_state(directory, state, stamp)
    store = directory / accepted.AUTOSAVE_NAME
    directory.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(document), encoding="utf-8")
    return store


def _printed(run: Callable[[], object], root: Path) -> str:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        run()
    return (out.getvalue() + err.getvalue()).replace(str(root), "<root>")


def _ink_rules(directory: Path) -> Path:
    """Write a standing rules file whose one ink-delta rule matches every human unit of the contract fixture, so the fill's tallies, its fills and its disputed-match warning read every unit's verdict."""
    units, _ids = unit_index.load_human_units(FIXTURES)
    digests = sorted({digest for unit in units for digest in (unit.get("ink_deltas") or {}).values()})
    rule = {
        "id": "every-fixture-change",
        "verdict": "approve",
        "note": "every change the contract fixture shows",
        "match": {"after": {"ink_deltas": digests}, "except_left": []},
    }
    path = directory / "standing-approvals.yaml"
    path.write_text(yaml.safe_dump({"format": standing_verdicts.FORMAT, "rules": [rule]}), encoding="utf-8")
    return path


def _queue(store: Path, flags: tuple[str, ...] = ()) -> object:
    """The queue snapshot without the notes of its evidence samples, which a close-out drops (decision 24), and without the verdicts file's name."""
    out = store.parent / "queue-data.json"
    argv = [str(store), "--corpus", str(FIXTURES), "--data-out", str(out), *flags]
    printed = _printed(lambda: review_queue.main(argv), store.parent)
    snapshot = json.loads(out.read_text(encoding="utf-8"))
    del snapshot["verdicts_file"]
    for cluster in snapshot["clusters"]:
        for sample in cluster["evidence"]["samples"]:
            del sample["note"]
    return snapshot, printed


def _held(store: Path) -> held_store.Held:
    """The store at `store` held as record lines with a table over them, as the verdict update holds its prepared store after a merge (`held_store.Held`): its own records settled by unit and normalized, its tombstones on units holding none, and its override section."""
    document = json.loads(store.read_text(encoding="utf-8"))
    records = journal.latest_by_unit(document["verdicts"])
    builder = held_store.Builder()
    for unit in sorted(records):
        builder.add(records[unit])
    section = accepted.Overrides.of(document)
    overrides = accepted.Overrides(
        {unit: held_store.normalized(record) for unit, record in section.records.items()}, section.cleared
    )
    cleared = {unit: at for unit, at in journal.parse_cleared(document).items() if unit not in records}
    return builder.held(document["manifest_generated_at"], document.get("accepted"), cleared, overrides)


def _duplicate_fill(store: Path, flags: tuple[str, ...] = (), *, held: bool = False) -> object:
    """Each fill's unit and verdict, and the disagreement report without the members' notes: a fill copied from an accepted member carries the close-out's time and its marker alone (decisions 24 and 29). With `held`, the fill reads the store held in memory (`_held`), as the verdict update hands it over."""
    out = store.parent / "duplicate-fill.json"
    argv = [str(store), "--corpus", str(FIXTURES), "--out", str(out), *flags]
    printed = _printed(
        lambda: duplicate_verdicts.main(argv, held=_held(store) if held else None), store.parent
    )
    fills = [(record["unit"], record["verdict"]) for record in json.loads(out.read_text())["verdicts"]]
    report = [line.split()[:3] if line.startswith("    u-") else line for line in printed.splitlines()]
    return fills, report


def _standing_fill(store: Path, flags: tuple[str, ...] = (), *, held: bool = False) -> object:
    """The fills and the report of the whole-domain dry run and of the cycle's form, reading the store held in memory with `held` (`_held`)."""
    rules = _ink_rules(store.parent)
    out = store.parent / "standing-fill.json"
    runs = []
    for form in ((), ("--open-only", "--require-reach")):
        argv = [str(store), "--corpus", str(FIXTURES), "--rules", str(rules), "--out", str(out)]
        argv += ["--daemon", "never", *form, *flags]
        printed = _printed(
            lambda: standing_verdicts.main(argv, held=_held(store) if held else None), store.parent
        )
        runs.append((json.loads(out.read_text(encoding="utf-8"))["verdicts"], printed))
    return runs


def _complaint_list(store: Path, flags: tuple[str, ...] = (), *, held: bool = False) -> object:
    """The complaint list over the fixture's human units, each handed over carrying the first provenance pointers a unit carries: the fixture's units share none with a complaint, and sharing them puts every unit in each complaint group's reach, its blanks among the defer candidates and its accepting verdicts among the approved units at risk. With `held`, the list reads the store held in memory (`_held`)."""
    units, _ids = unit_index.load_human_units(FIXTURES)
    pointers = next(unit["provenance"] for unit in units if unit.get("provenance"))
    shared = [{**unit, "provenance": pointers} for unit in units]
    human = frozenset(unit["id"] for unit in units)
    out = store.parent / "complaints-data.json"
    argv = [str(store), "--corpus", str(FIXTURES), "--data-out", str(out), *flags]
    printed = _printed(
        lambda: complaint_list.main(argv, units=shared, human=human, held=_held(store) if held else None),
        store.parent,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    del payload["verdicts_file"]
    return payload, printed


def _novelty_order(store: Path, flags: tuple[str, ...] = ()) -> object:
    argv = ["novelty_order.py", str(store), "--corpus", str(FIXTURES), "--limit", "0", *flags]
    with mock.patch.object(sys, "argv", argv):
        return _printed(lambda: novelty_order.main(lambda _url: None), store.parent)


def _standing_probe(store: Path, flags: tuple[str, ...] = ()) -> object:
    """Every human unit's blankness as the probe's `--find` lists it."""
    rules = _ink_rules(store.parent)
    argv = ["--find", "·", "--limit", "100", "--verdicts", str(store), "--corpus", str(FIXTURES)]
    argv += ["--rules", str(rules), "--daemon", "never", *flags]
    return _printed(lambda: standing_probe.main(argv), store.parent)


def _triage_export(store: Path, flags: tuple[str, ...] = ()) -> object:
    """The triage's counts and the units each draft section names, without the notes the drafts copy (decision 24); a section lists its units in the order the file's records come, the accepted ones after the rest, so the units are compared as sets."""
    out = store.parent / "review-triage.yaml"
    argv = [str(store), "--review-dir", str(FIXTURES), "--out", str(out), *flags]
    printed = _printed(lambda: export.main(argv), store.parent)
    triage = yaml.safe_load(out.read_text(encoding="utf-8"))
    sections = ("pins", "policy_edits", "any_of", "neither", "identical")
    drafted = {section: sorted(entry["unit"] for entry in triage[section]) for section in sections}
    return triage["review"]["counts"], drafted, printed


def _verdict_ready(store: Path) -> object:
    """What `make verdict-ready` decides of the store: each check's level, the fullest verdicts file and its count of effective verdicts, the blanks left, and the verdict; a check's wording may say how many of the verdicts are accepted ones."""
    root = store.parent
    result, ready = verdict_ready.readiness(
        with_server=False,
        repo_root=root,
        review_dir=FIXTURES,
        m1_out=root / "m1",
        autosave_path=store,
        cycle_summary_path=root / "cycle_summary.json",
        recompute=lambda _repo: {},
    )
    checks = result["checks"]
    fullest, blanks = checks["fullest_verdicts"], checks["blanks"]
    levels = {name: check["level"] for name, check in checks.items()}
    return levels, (fullest["path"], fullest["count"]), blanks["count"], ready


CLI_READERS: dict[str, Callable[..., object]] = {
    "queue": _queue,
    "duplicate fill": _duplicate_fill,
    "standing fill": _standing_fill,
    "complaint list": _complaint_list,
    "novelty order": _novelty_order,
    "standing probe": _standing_probe,
    "triage export": _triage_export,
}
HELD_READERS: dict[str, Callable[..., object]] = {
    "duplicate fill, held": lambda store: _duplicate_fill(store, held=True),
    "standing fill, held": lambda store: _standing_fill(store, held=True),
    "complaint list, held": lambda store: _complaint_list(store, held=True),
}
READERS: dict[str, Callable[..., object]] = {
    **CLI_READERS,
    **HELD_READERS,
    "verdict ready": _verdict_ready,
}


@pytest.mark.parametrize("reader", sorted(READERS))
def test_every_reader_gives_a_folded_store_the_verdicts_the_unfolded_store_gave(reader, tmp_path):
    """A close-out changes no decision: each fold sample's store and list after the fold give the reader the same verdict kind for every human unit as the store and list before it, so the queue's blanks and clusters, both fills' targets and verdicts, the complaint list, the novelty order, the probe's blankness, the triage export's counts and drafts, and `make verdict-ready`'s checks come out the same. What differs by design is left out of each reader's answer above: the notes an accepted record drops and the `at` it takes (decisions 24 and 29). One case would differ and the samples leave it out: a duplicate group whose accepted members mix approve and identical, where every accepted member is dated `closed_at` and the fill copies the first in corpus order rather than the newest (decision 40)."""
    for index, fold in enumerate(_samples()["folds"]):
        answers = [
            READERS[reader](_fold_side(tmp_path / str(index) / side, fold[side]))
            for side in ("before", "after")
        ]
        assert answers[0] == answers[1], fold["why"]


@pytest.mark.parametrize("reader", sorted(HELD_READERS))
def test_a_reader_handed_the_store_held_in_memory_answers_as_it_reads_the_file(reader, tmp_path):
    """The verdict update hands the duplicate and standing fills and the complaint list its prepared store held as record lines with a table over them (`held_store.Held`) in place of the file, which they read through the table. Over each fold sample's store before and after the fold, each gives the fills, conflicts, reports and complaint list it gives reading the store's file."""
    name = reader.removesuffix(", held")
    for index, fold in enumerate(_samples()["folds"]):
        for side in ("before", "after"):
            store = _fold_side(tmp_path / str(index) / side, fold[side])
            assert HELD_READERS[reader](store) == CLI_READERS[name](store), (fold["why"], side)


def test_the_duplicate_fill_and_its_merge_leave_a_member_cleared_before_the_close_out_blank(tmp_path):
    """A duplicate group with one member approved and the other cleared after that approval: unfolded, the fill copies the approval's time, which the tombstone outdates, so the duplicate merge keeps the member blank. Folded, the accepted record carries the close-out's time, later than the clear, at the close-out that took the approval and at every one after it, which keeps the tombstone; the fill leaves the cleared member out, so the merge keeps it blank there too."""
    cleared, approved = "u-HLeS39nnjSM", "u-WT2Pjqw8CKB"
    tombstones = [{"unit": cleared, "at": "2026-10-28T09:00:00.000Z"}]
    sides = {
        "unfolded": {
            "list": None,
            "verdicts": [
                {"unit": approved, "verdict": "approve", "note": "", "at": "2026-10-20T10:00:00.000Z"}
            ],
            "cleared": tombstones,
        },
        "folded": {
            "list": {"closed_at": "2026-10-25T12:00:00Z", "verdicts": {approved: "approve"}},
            "verdicts": [],
            "cleared": tombstones,
        },
        "folded again": {
            "list": {"closed_at": CLOSED_AT, "verdicts": {approved: "approve"}},
            "verdicts": [],
            "cleared": tombstones,
        },
    }
    for name, state in sides.items():
        store = _fold_side(tmp_path / name, state)
        out = store.parent / "duplicate-fill.json"
        argv = [str(store), "--corpus", str(FIXTURES), "--out", str(out)]
        _printed(lambda: duplicate_verdicts.main(argv), store.parent)
        document = json.loads(store.read_text(encoding="utf-8"))
        listed = accepted.read(accepted.path_for(store))
        records, tombstoned = journal.latest_by_unit(document["verdicts"]), journal.parse_cleared(document)
        merge_verdicts.merge_into(records, json.loads(out.read_text())["verdicts"], tombstoned, listed)
        view = accepted.View(records, tombstoned, listed)
        kinds = {unit: (view.verdict_of(unit) or {}).get("verdict") for unit in (cleared, approved)}
        assert kinds == {cleared: None, approved: "approve"}, name


def _beside_another_list(directory: Path) -> tuple[Path, str, str]:
    fold = _samples()["folds"][1]
    store = _fold_side(directory, fold["after"])
    earlier = fold["before"]["list"]
    _list_at(directory, accepted.render(earlier["closed_at"], earlier["verdicts"]).decode("ascii"))
    named = json.loads(store.read_text(encoding="utf-8"))["accepted"]["sha256"]
    return store, named, accepted.read(accepted.path_for(store)).sha256 or ""


@pytest.mark.parametrize("reader", sorted(CLI_READERS))
def test_every_reader_refuses_a_store_beside_a_list_it_was_not_folded_against(reader, tmp_path):
    store, named, beside = _beside_another_list(tmp_path)
    with pytest.raises(SystemExit) as refusal:
        CLI_READERS[reader](store)
    assert named in str(refusal.value) and beside in str(refusal.value)


@pytest.mark.parametrize("reader", sorted(CLI_READERS))
def test_a_reader_of_a_store_under_another_name_reads_the_list_accepted_names(reader, tmp_path):
    """Only a store named verdicts-autosave.json has a list beside it (`accepted.path_for`), so a reader of a copy under another name, such as the cycle's prepared store, refuses with a usage error until `--accepted` names the list, and then reads what it reads through the live store's name."""
    fold = _samples()["folds"][1]
    store = _fold_side(tmp_path / "live", fold["after"])
    expected = CLI_READERS[reader](store)
    prepared = tmp_path / "cycle" / "prepared.json"
    prepared.parent.mkdir()
    shutil.copyfile(store, prepared)
    with pytest.raises(SystemExit) as usage:
        CLI_READERS[reader](prepared)
    assert usage.value.code == 2
    assert CLI_READERS[reader](prepared, ("--accepted", str(accepted.path_for(store)))) == expected


@pytest.mark.parametrize("reader", sorted(CLI_READERS))
def test_every_reader_reads_an_export_of_the_store_as_it_reads_the_store(reader, tmp_path):
    """The review app's Export holds the store's records alone, its overrides among them, with no `accepted` field and no tombstones (decision 32). A reader given one with `--accepted` reads its records over the list, those on listed ids as overrides, and takes the tombstones of the store beside the list, so an accepted unit cleared since the close-out reads blank in the Export as it does in the store, and the reader answers for the Export as it answers for the store."""
    fold = _samples()["folds"][1]
    store = _fold_side(tmp_path / "live", fold["before"])
    expected = CLI_READERS[reader](store)
    document = json.loads(store.read_text(encoding="utf-8"))
    exported = tmp_path / "downloads" / "verdicts-2026-11-20.json"
    exported.parent.mkdir()
    head = {"format": document["format"], "manifest_generated_at": document["manifest_generated_at"]}
    records = accepted.records_of(document)
    exported.write_text(json.dumps({**head, "exported_at": CLOSED_AT, "verdicts": records}))
    assert CLI_READERS[reader](exported, ("--accepted", str(accepted.path_for(store)))) == expected


def test_a_reader_reads_an_unchanged_list_once_and_a_replaced_one_again(tmp_path):
    """The standing daemon runs a reader's `main` for each request, so `review_queue.read_accepted` holds the list it read last until the file changes; a close-out or a re-key replaces the file by a rename, and that list is read again."""
    path = _list_at(tmp_path, accepted.render(CLOSED_AT, {"u-1AAAAAAAAAA": "approve"}).decode("ascii"))
    held = review_queue.read_accepted(path)
    assert review_queue.read_accepted(path) is held
    replacement = path.with_name("accepted-units.txt.new")
    replacement.write_bytes(accepted.render(CLOSED_AT, {"u-1AAAAAAAAAA": "either"}))
    os.replace(replacement, path)
    assert dict(review_queue.read_accepted(path).verdicts) == {"u-1AAAAAAAAAA": "either"}


GROUPS = ("e-0001", "e-0001", "e-0002", None, None, "e-0001", "e-0002", None)
LISTED_MARKS = ("approve", "either", "identical", "approve", "approve")
KINDS = ("approve", "reject", "either", "identical", "neither", "skip")
BEFORE_THE_FIRST_CLOSE_OUT = "2026-11-04T00:00:00.000Z"
STEPS = 80
INK_DELTA = "d-222222222222"
STANDING_RULE = {
    "id": "every-fragment",
    "verdict": "approve",
    "note": "every change the fragments show",
    "match": {"after": {"ink_deltas": [INK_DELTA]}, "except_left": []},
}
STANDING_NOTE = f"[standing: {STANDING_RULE['id']}] {STANDING_RULE['note']}"
PAGE_HARNESS = """
import * as V from VERDICTS_JS;
import { sameRecord } from OUTBOX_JS;
import { createInterface } from 'node:readline';

const RealDate = Date;
let fixed = null;
globalThis.Date = class extends RealDate {
  constructor(...args) {
    super(...(args.length ? args : [fixed ?? RealDate.now()]));
  }

  static now() {
    return fixed === null ? RealDate.now() : new RealDate(fixed).getTime();
  }
};

for await (const line of createInterface({ input: process.stdin })) {
  const { get, stamp, actions, now } = JSON.parse(line);
  const store = V.createStore();
  const server = new Map();
  for (const record of get.verdicts) server.set(record.unit, record);
  fixed = now;
  const loaded = V.importVerdicts(store, get, stamp);
  V.noteServerRecords(store, server.values());
  V.applyAccepted(store, get.accepted);
  V.markExported(store);
  for (const unit of loaded.units) store.dirty.delete(unit);
  const outbox = new Map();
  for (const action of actions) {
    fixed = action.at;
    if (action.kind === 'set') V.recordVerdict(store, action.unit, action.verdict, { note: action.note, at: action.at });
    else if (action.kind === 'clear') V.recordVerdict(store, action.unit, null);
    else if (action.kind === 'undo') V.undo(store);
    else if (action.kind === 'note') V.updateNote(store, action.unit, action.note);
    else V.recordVerdictWithDuplicates(store, action.unit, action.verdict, action.duplicates, { note: action.note, at: action.at });
    for (const unit of store.dirty) {
      const record = store.records.get(unit) ?? null;
      if (outbox.has(unit) && sameRecord(outbox.get(unit).record, record)) continue;
      const snapshot = record ? { unit, verdict: record.verdict, note: record.note, at: record.at } : null;
      outbox.set(unit, { unit, record: snapshot, base_at: store.serverAt.get(unit) ?? null, stamp, tab: 'test', seq: 1, written_at: action.at });
    }
  }
  fixed = now;
  const dirty = [...store.dirty];
  const reply = { delta: V.assembleDelta(store, stamp, dirty), exported: V.assembleExport(store, stamp), unsaved: V.unsavedFiles([...outbox.values()]) };
  process.stdout.write(`${JSON.stringify(reply)}\\n`);
}
"""


def _fragment(index: int, generation: str) -> dict:
    """The contract's eighth-sized corpus: one fixture-class unit per index, distinct by codepoint, in the duplicate group `GROUPS` gives it, under an old id or under the id its content hashes to now (`generation`), so a re-key moves every id."""
    fragment = {
        "id": unit_cache.unit_id_for(hashlib.sha256(f"old {index}".encode()).hexdigest()),
        "class": "fixture",
        "codepoints": f"{0xE650 + index:04X}",
        "notation": f"unit {index}",
        "duplicate_group": GROUPS[index],
        "configs": ["default"],
        "ink_deltas": {"default": INK_DELTA},
        "render_groups": [{}],
        "before": {"glyphs": ["qsPea"], "junctions": []},
        "after": {"cells": ["qsPea/sole/None/None/"], "junctions": []},
        "provenance": ["glyph_data/runes/qsPea.yaml:policies[0]"],
    }
    if generation == "new":
        key = unit_cache.carry_content_hash(fragment)
        fragment = {**fragment, "id": unit_cache.unit_id_for(key), "content_key": key}
    return fragment


def _corpus_at(path: Path, stamp: str, fragments: list[dict]) -> Path:
    (path / "units").mkdir(parents=True)
    (path / "units" / "fixture.json").write_text(json.dumps(fragments), encoding="utf-8")
    manifest = {
        "generated_at": stamp,
        "batch_size": 10,
        "human_unit_ids": [fragment["id"] for fragment in fragments],
        "classes": [{"id": "fixture", "section": "open-questions", "shards": ["units/fixture.json"]}],
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    unit_index.write_index(path, [("fixture", fragments)])
    return path


class _Killed(BaseException):
    """A process killed in the middle of a write, which no `except Exception` handles."""


@contextlib.contextmanager
def _killed_at(
    owner: object, name: str, when: Callable[..., bool] = lambda *_args, **_kwargs: True
) -> Iterator[list[bool]]:
    """Make the first call of `owner.name` that `when` takes raise `_Killed` instead of running, and yield a list that holds True once one has; the attribute is put back on leaving."""
    original = getattr(owner, name)
    fired: list[bool] = []

    def dying(*args, **kwargs):
        if not fired and when(*args, **kwargs):
            fired.append(True)
            raise _Killed(name)
        return original(*args, **kwargs)

    setattr(owner, name, dying)
    try:
        yield fired
    finally:
        setattr(owner, name, original)


class _Page:
    """The review app's verdict store (`rebuild/review/static/verdicts.js`) in one node process, for the randomized test below. Each `session` boots a page from the server's full GET of /autosave as `restoreAutosave` in app.js does, applies the reader's actions at the times they name, writing an outbox entry after each for every unit whose record it changed as app.js does, and returns the delta its next save sends for the units it changed, its Export, and the files `unsavedFiles` lays out for those outbox entries."""

    def __init__(self, node: str) -> None:
        static = Path(__file__).resolve().parent / "review" / "static"
        harness = PAGE_HARNESS.replace("VERDICTS_JS", json.dumps((static / "verdicts.js").as_uri()))
        harness = harness.replace("OUTBOX_JS", json.dumps((static / "outbox.js").as_uri()))
        self.process = subprocess.Popen(
            [node, "--input-type=module", "-e", harness],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def session(self, get: dict, stamp: str, actions: list[dict], now: str) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(
            json.dumps({"get": get, "stamp": stamp, "actions": actions, "now": now}) + "\n"
        )
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        assert line, "the page's node process exited"
        return json.loads(line)

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)


def _rekey_beats(entry: tuple, other: tuple) -> bool:
    """Whether `entry` stays over `other` when a re-key lands both on one unit: the newer record, a later clear, or a clear at or after the record's `at`; a tie between two records goes to `other`, whose own id sorts first."""
    if entry[0] == "tombstone" and other[0] == "record":
        return entry[-1] >= other[-1]
    return entry[-1] > other[-1]


@dataclasses.dataclass
class _Model:
    """Every unit's verdict as the rules the writers' docstrings state leave it, for the randomized test below. `state` holds each unit's standing act, a record `("record", verdict, note, at)` or a tombstone `("tombstone", at)`, whichever part of the store holds it, and a listed unit without one answers with its accepted record, the list being `listed`, closed at `closed_at`. Each method is one writer's rule."""

    listed: dict[str, str]
    closed_at: str
    state: dict[str, tuple] = dataclasses.field(default_factory=dict)

    def copy(self) -> _Model:
        return _Model(dict(self.listed), self.closed_at, dict(self.state))

    def answer(self, unit: str) -> tuple | None:
        """The unit's `(verdict, note, at)`, or None for blank."""
        held = self.state.get(unit)
        if held is not None:
            return held[1:] if held[0] == "record" else None
        mark = self.listed.get(unit)
        return None if mark is None else (mark, "", self.closed_at)

    def tombstone(self, unit: str) -> str | None:
        held = self.state.get(unit)
        return held[1] if held is not None and held[0] == "tombstone" else None

    def matches(self, record: dict) -> bool:
        return self.listed.get(record["unit"]) == record["verdict"] and not record["note"]

    def admits(self, unit: str, at: str, base_at: str | None) -> bool:
        current = self.answer(unit)
        if current is None:
            tombstone = self.tombstone(unit)
            return tombstone is None or tombstone <= at
        return current[2] == base_at or at >= current[2]

    def save(self, record: dict, *, tested: bool = False, members: frozenset[str] | None = None) -> None:
        """A set the review server receives: last writer wins (rule 1), or under the per-unit test when `tested` (a replay, and rule 2), where rule 2's `members`, the served corpus's human units, orphans a set on any other unit and turns a skip into a clear. A set equal to a listed unit's accepted record dated `closed_at` removes its override."""
        unit = record["unit"]
        if members is not None and unit not in members:
            return
        current = self.answer(unit)
        restore = self.matches(record) and record["at"] == self.closed_at
        signature = (record["verdict"], record["note"], record["at"])
        if (restore and unit not in self.state) or (not restore and current == signature):
            return
        skip = members is not None and record["verdict"] == "skip"
        if skip and current is None:
            return
        if tested and not self.admits(unit, record["at"], record.get("base_at")):
            return
        if skip:
            self.state[unit] = ("tombstone", record["at"])
        elif restore:
            del self.state[unit]
        else:
            self.state[unit] = ("record", *signature)

    def clear(self, unit: str, at: str, *, tested: bool = False, base_at: str | None = None) -> None:
        """A clear the review server receives, which leaves a tombstone on a unit that does not read blank."""
        if self.answer(unit) is None or (tested and not self.admits(unit, at, base_at)):
            return
        self.state[unit] = ("tombstone", at)

    def merge(self, record: dict) -> None:
        """A record a merge takes in: it replaces the unit's record or tombstone when strictly newer, and a listed unit's accepted record unless it equals that by value or is dated at or before the close-out."""
        unit, at = record["unit"], record["at"]
        held = self.state.get(unit)
        if held is not None:
            if held[-1] >= at:
                return
        elif unit in self.listed and (self.matches(record) or at <= self.closed_at):
            return
        self.state[unit] = ("record", record["verdict"], record["note"], at)

    def moved(self, members: frozenset[str]) -> None:
        """A move of the store onto a corpus whose human units are `members`, by the server or a carry: every skip becomes a tombstone at its `at`, an unlisted unit off `members` keeps nothing, and a listed unit keeps its override wherever its unit is."""
        for unit, held in list(self.state.items()):
            if unit not in self.listed and unit not in members:
                del self.state[unit]
            elif held[0] == "record" and held[1] == "skip":
                self.state[unit] = ("tombstone", held[3])

    def carried(self, members: frozenset[str], extra: list[dict]) -> None:
        """A carry of the store, with an Export holding `extra`, onto a corpus whose human units are `members`, merged into the store moved there. The cycle's plan lists the Export before the store among the carry's files, so an Export record on `members` that is not a skip merges in, and on a tie with an unlisted unit's own record other than a skip it wins the carry and replaces that record; a skip at or after such a record, the tie included, wins the carry and is not carried, so that unit keeps nothing; a listed unit's override stays whatever the Export holds."""
        before = self.copy()
        self.moved(members)
        for record in extra:
            unit, held = record["unit"], before.state.get(record["unit"])
            if unit not in members:
                continue
            own = unit not in self.listed and held is not None and held[0] == "record" and held[1] != "skip"
            at = held[3] if own and held is not None else None
            if record["verdict"] != "skip":
                if record["at"] == at:
                    self.state[unit] = ("record", record["verdict"], record["note"], record["at"])
                else:
                    self.merge(record)
            elif at is not None and record["at"] >= at:
                del self.state[unit]

    def emptied(self, members: frozenset[str]) -> None:
        """The store a land with no prepared store starts from on a move onto a corpus whose human units are `members`, as a --no-merge or --no-carry pass lands it: a listed unit keeps its override, a skip becoming a tombstone at its `at`, and an unlisted unit keeps only a tombstone on `members`."""
        for unit, held in list(self.state.items()):
            if unit in self.listed:
                if held[0] == "record" and held[1] == "skip":
                    self.state[unit] = ("tombstone", held[3])
            elif held[0] != "tombstone" or unit not in members:
                del self.state[unit]

    def fills(self, groups: list[list[str]]) -> list[dict]:
        """The duplicate fill's proposals: in each group whose judged members agree, each blank member takes the newest judged member's verdict, note and `at`, the first in corpus order on a tie, except a member with a tombstone when that `at` is `closed_at`."""
        proposals = []
        for members in groups:
            judged = [
                (unit, answer)
                for unit in members
                if (answer := self.answer(unit)) is not None and answer[0] != "skip"
            ]
            blanks = [unit for unit in members if self.answer(unit) is None]
            kinds = {answer[0] for _unit, answer in judged}
            if len(members) < 2 or not kinds or not blanks or not review_queue.verdicts_agree(kinds):
                continue
            source, (verdict, note, at) = max(judged, key=lambda pair: pair[1][2])
            if at == self.closed_at:
                blanks = [unit for unit in blanks if self.tombstone(unit) is None]
            note = cap_markers(f"[duplicate-fill from {source}] {note}".strip())
            proposals += [{"unit": unit, "verdict": verdict, "note": note, "at": at} for unit in blanks]
        return proposals

    def fill(
        self,
        groups: list[list[str]],
        *,
        twice: bool = False,
        standing: tuple[Iterable[str], str] | None = None,
    ) -> None:
        """A duplicate fill merged in, or the verdict update's three: a duplicate fill, the standing fill over `standing`'s units, whose one rule approves each blank one dated `standing`'s stamp, and a second duplicate fill, whose merge takes the first's proposals with its own for the units the first did not propose."""
        first = self.fills(groups)
        for record in first:
            self.merge(record)
        if standing is not None:
            units, stamp = standing
            for unit in [unit for unit in units if self.answer(unit) is None]:
                self.merge({"unit": unit, "verdict": "approve", "note": STANDING_NOTE, "at": stamp})
        if twice:
            proposed = {record["unit"] for record in first}
            for record in [
                *first,
                *(record for record in self.fills(groups) if record["unit"] not in proposed),
            ]:
                self.merge(record)

    def landed(self, before: _Model, live: _Model, members: frozenset[str] | None) -> None:
        """The land's overlay onto this prepared store: each unit whose state the live store changed since the snapshot `before` takes the live one, and on a move onto a corpus whose human units are `members` a skip becomes a tombstone and an unlisted unit off `members` keeps nothing."""
        for unit in sorted(before.state.keys() | live.state.keys()):
            held = live.state.get(unit)
            if held == before.state.get(unit):
                continue
            if members is not None:
                if unit not in self.listed and unit not in members:
                    continue
                if held is not None and held[0] == "record" and held[1] == "skip":
                    held = ("tombstone", held[3])
            if held is None:
                self.state.pop(unit, None)
            else:
                self.state[unit] = held

    def closed(self, members: frozenset[str], now: str) -> None:
        """A close-out against a corpus whose human units are `members` at `now`: the new list holds each listed unit of `members` with no record or tombstone and each unit of `members` whose record accepts, the store keeps every other record and the tombstones on `members`, and with no accepting record and no listed unit off `members` nothing changes."""
        accepting = {
            unit: held[1]
            for unit, held in self.state.items()
            if held[0] == "record" and held[1] in accepted.ACCEPTING_VERDICTS
        }
        if not accepting and all(unit in members for unit in self.listed):
            return
        listed = {
            unit: mark for unit, mark in self.listed.items() if unit in members and unit not in self.state
        }
        listed.update((unit, verdict) for unit, verdict in accepting.items() if unit in members)
        self.state = {
            unit: held
            for unit, held in self.state.items()
            if (held[0] == "record" and unit not in accepting) or (held[0] == "tombstone" and unit in members)
        }
        self.listed, self.closed_at = listed, now

    def undone(self, archived: _Model) -> None:
        """The close-out's undo, which puts back the list of `archived`, the store the close-out found, and lays that store under this one: an archived tombstone on a unit holding no record here stays when it is later than this one's, and an archived record stays when it is newer than this unit's record or tombstone."""
        state = dict(self.state)
        for unit, held in archived.state.items():
            mine = state.get(unit)
            if held[0] == "tombstone" and (mine is None or (mine[0] == "tombstone" and held[1] > mine[1])):
                state[unit] = held
        for unit, held in archived.state.items():
            mine = state.get(unit)
            if held[0] == "record" and (mine is None or held[3] > mine[-1]):
                state[unit] = held
        self.listed, self.closed_at, self.state = dict(archived.listed), archived.closed_at, state

    def rekeyed(self, renamed: Mapping[str, str], present: Container[str] = ()) -> None:
        """A re-key that moves each id in `renamed` to its new id onto a corpus whose units are `present`: the list keeps each listed id it moves and each one `present` holds, and an entry lands on its unit's new id, where two that land on one unit leave the one `_rekey_beats` says stays."""
        self.listed = {
            renamed.get(unit, unit): mark
            for unit, mark in self.listed.items()
            if unit in renamed or unit in present
        }
        moved: dict[str, tuple] = {}
        for unit, held in sorted(self.state.items()):
            target = renamed.get(unit, unit)
            if target not in moved or _rekey_beats(held, moved[target]):
                moved[target] = held
        self.state = moved


def _page_reply(model: _Model, within: Container[str], actions: list[dict]) -> tuple[dict, dict, dict]:
    """What a page booted from the store `model` makes of `actions` as `verdicts.js` states each one, for the randomized test below: the page holds every record of the store and the accepted record of each listed unit of `within` that holds no override; a set and a duplicate approval replace records and can be undone, the latter only on the members holding none; a clear removes a record and can be undone; an undo puts back what the last of those replaced; a note typed on an accepted record makes it a record dated then, and on another record changes its note alone. Returns the sets and the clears, each by unit, of the save that sends every unit an action touched, its record or the time it was left without one, and its Export's records by unit, the accepted ones left out."""
    records = {
        unit: {"verdict": held[1], "note": held[2], "at": held[3]}
        for unit, held in model.state.items()
        if held[0] == "record"
    }
    records.update(
        (unit, {"verdict": mark, "note": "", "at": model.closed_at, "accepted": True})
        for unit, mark in model.listed.items()
        if unit in within and unit not in model.state
    )
    undo: list[list[tuple[str, dict | None]]] = []
    touched: dict[str, str] = {}

    def touch(unit: str, at: str) -> None:
        touched[unit] = at if unit not in records else touched.get(unit, at)

    for action in actions:
        unit, at, kind = action["unit"], action["at"], action["kind"]
        made = {"verdict": action.get("verdict"), "note": action.get("note"), "at": at}
        if kind == "set":
            undo.append([(unit, records.get(unit))])
            records[unit] = made
            touch(unit, at)
        elif kind == "clear" and unit in records:
            undo.append([(unit, records.pop(unit))])
            touch(unit, at)
        elif kind == "undo" and undo:
            for undone, prior in undo.pop():
                if prior is None:
                    records.pop(undone, None)
                else:
                    records[undone] = prior
                touch(undone, at)
        elif kind == "note" and unit in records and records[unit]["note"] != action["note"]:
            if records[unit].get("accepted"):
                records[unit] = {**records[unit], "note": action["note"], "at": at, "accepted": False}
            else:
                records[unit]["note"] = action["note"]
            touch(unit, at)
        elif kind == "duplicates":
            entries = [(unit, records.get(unit))]
            records[unit] = made
            touch(unit, at)
            for member in action["duplicates"]:
                if member != unit and member not in records:
                    entries.append((member, None))
                    records[member] = dict(made)
                    touch(member, at)
            undo.append(entries)
    sets = {
        unit: (records[unit]["verdict"], records[unit]["note"], records[unit]["at"])
        for unit in touched
        if unit in records
    }
    clears = {unit: at for unit, at in touched.items() if unit not in records}
    exported = {
        unit: (record["verdict"], record["note"], record["at"])
        for unit, record in records.items()
        if not record.get("accepted")
    }
    return sets, clears, exported


def _state_of(records: Mapping[str, dict], cleared: Mapping[str, str]) -> dict[str, tuple]:
    """Records and tombstones held as one store, as `_Model.state` holds them."""
    state: dict[str, tuple] = {unit: ("tombstone", at) for unit, at in cleared.items() if unit not in records}
    state.update(
        (unit, ("record", record["verdict"], record.get("note") or "", record["at"]))
        for unit, record in records.items()
    )
    return state


def _caught_up_copy(store: Path, journal_path: Path, list_path: Path, shadow: Path) -> Path:
    """Copy the store, its journal and its list into `shadow`, a new directory, and catch the copy up with the journal (`landing.catch_up`), and return the copy: the store the next holder of the store's lock reads, the review server's saves it has journaled and not written among it, while the store itself and the server keep them pending."""
    copy = shadow / store.name
    (shadow / list_path.relative_to(store.parent)).parent.mkdir(parents=True)
    for path in (store, journal_path, list_path):
        if path.exists():
            shutil.copyfile(path, shadow / path.relative_to(store.parent))
    landing.catch_up(copy, journal.journal_path_for(copy), accepted.path_for(copy))
    return copy


def _stored(document: dict) -> dict[str, tuple]:
    """A store document's own records and tombstones and its override section's, as `_Model.state` holds them."""
    records = journal.latest_by_unit(document["verdicts"])
    return _state_of(
        *accepted.joined(records, journal.parse_cleared(document), accepted.Overrides.of(document))
    )


@dataclasses.dataclass(frozen=True)
class _Moment:
    """A moment a restore can go back to: its journal event's `at`, the store then as `_Model` gives it, the store's stamp, its list's `accepted` field, and whether its ids were the old ones."""

    at: str
    model: _Model
    stamp: str
    field: dict | None
    old_ids: bool


@pytest.mark.parametrize("seed", range(12))
def test_no_writer_drops_an_override_on_a_listed_unit_until_the_next_close_out(seed, tmp_path, monkeypatch):
    """Random sequences over a store beside a list, each step driving a real writer in process. The review server boots on no store and stamps an empty one (`stamp_empty_store`), and takes saves, clears and undos on its stamp, from a stale tab on another corpus (rule 2) and replayed from an outbox; a page of the review app (`_Page`, in node) boots from its full GET, records, clears, undoes, edits a note and approves a duplicate group, and sends its delta, or has its save fail and the reader Import its unsaved files, and sometimes Exports and Imports that. A merge takes an Import or a carried file the cycle left at the root; a duplicate fill is merged in; the complaint list's defer file for a group is Imported. Cycle passes run the steps `artifact_cycle.build_plan` gives them: a carried pass (the verdict update's carry of the store and sometimes an Export onto a new corpus, its merges, both duplicate fills and the standing fill, and the land that swaps the corpus in), a store-only pass that merges a master and lands on the served corpus, and a --no-merge or --no-carry pass that moves the corpus and lands no prepared store; a land is sometimes killed at one of its writes and finished by `landing.finish_interrupted_land`. The server moves the store onto a new corpus (rule 3). A close-out, sometimes killed at one of its writes and finished or dropped by the next one, and its undo; a restore, previewed and applied, of a moment the journal holds under the current list, re-keyed through the kept id map for a moment from before the re-key, followed, when it puts the store on an older corpus, by a store-only pass that carries that store onto the served corpus or by a save that moves it there; retention's stash prune and a journal compaction at a moment's `at`; the store deleted, with or without its journal, and stamped empty at boot or merged into; the store left unreadable and then written over by a save, a merge or a --no-carry pass's land. One re-key, whose map comes from every old id or from the served corpus and whose new corpus holds every unit or some of them under --allow-unmatched, sometimes undone and applied again, followed by its carried pass; after it, an Import still on old ids and a second re-key, which refuses when an old id lands beside a listed id or a record on its new id (`listed_collisions`). Every new corpus holds all eight units or a random subset, so units leave the corpus and come back. The server journals each save first and writes the store on idle, so the store file lags its saves until a writer that takes the store's lock catches it up or a tool that reads it without the lock waits for the idle write. After every step each unit, listed or not, under either generation of ids, answers through `accepted.view_of` over the store as the next holder of its lock reads it (`_caught_up_copy`) and through the server's view as `_Model` says: a listed unit its last override, a record or a blank for a tombstone, or its accepted mark when it has none, and an unlisted unit its last record or blank, as each writer's stated rule leaves them; the list is the one the close-outs, their undos and the re-keys leave; the store's own records and tombstones name no listed unit; and the store, a restore's preview and the journal's replay hold exactly the records and tombstones `_Model` holds, the journal except after a save written over an unreadable store, whose journal keeps what that store held until the two agree again. The seed fixes the whole sequence, so a failure names its seed, its step and the steps before it."""
    rng = random.Random(seed)
    clock = itertools.count(1)
    issued: list[str] = []

    def tick(*, whole: bool = False) -> str:
        moment = next(clock)
        stamp = f"2030-01-01T{moment // 3600:02d}:{moment % 3600 // 60:02d}:{moment % 60:02d}"
        if whole:
            return f"{stamp}Z"
        issued.append(f"{stamp}.000Z")
        return issued[-1]

    root = tmp_path / "root"
    root.mkdir()
    store = root / accepted.AUTOSAVE_NAME
    journal_path = root / journal.JOURNAL_NAME
    list_path = accepted.path_for(store)
    list_path.parent.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    served = tmp_path / "served" / "review"
    kept_map = root / "var" / "keep" / "class-rejig-rekey" / "unit-id-map.json"
    rules = tmp_path / "standing-approvals.yaml"
    rules.write_text(yaml.safe_dump({"format": standing_verdicts.FORMAT, "rules": [STANDING_RULE]}))
    monkeypatch.setattr(journal, "now_stamp", lambda: tick(whole=True))
    monkeypatch.setattr(merge_verdicts, "_server_listening", lambda: False)
    monkeypatch.setattr(rekey_verdicts, "server_listening", lambda: False)
    monkeypatch.setattr(close_out, "_head_refusal", lambda _list_path, _root: None)
    monkeypatch.setattr(ac, "journal_scan_path", lambda: tmp_path / "journal-scan.json")
    for owner, name, value in (
        (ac, "AUTOSAVE", store),
        (ac, "REVIEW_OUT", served),
        (vu, "AUTOSAVE", store),
        (vu, "CORPUS", served),
        (standing_verdicts, "RULES", rules),
        (complaint_list, "DATA_OUT", tmp_path / "complaints-data.json"),
    ):
        monkeypatch.setattr(owner, name, value)
    olds = [_fragment(index, "old") for index in range(len(GROUPS))]
    news = [_fragment(index, "new") for index in range(len(GROUPS))]
    every = [fragment["id"] for fragment in (*olds, *news)]
    list_path.write_bytes(
        accepted.render(CLOSED_AT, {olds[index]["id"]: mark for index, mark in enumerate(LISTED_MARKS)})
    )
    corpora: dict[str, tuple[Path, list[dict]]] = {}
    serials = itertools.count(1)
    previous: dict[str, tuple | None] = {}
    trail: list[str] = []
    run = types.SimpleNamespace(
        generation=olds,
        model=_Model(dict(accepted.read(list_path).verdicts), CLOSED_AT),
        archive=None,
        moments=[],
        diverged=False,
    )

    def build(keep: Iterable[int] | None = None) -> str:
        serial = next(serials)
        stamp = f"2026-11-{10 + serial // 1440:02d}T{serial % 1440 // 60:02d}:{serial % 60:02d}:00Z"
        if keep is None:
            everything = range(len(GROUPS))
            keep = everything if rng.random() < 0.5 else sorted(rng.sample(everything, rng.randint(3, 7)))
        fragments = [run.generation[index] for index in keep]
        corpora[stamp] = (
            _corpus_at(tmp_path / "corpora" / stamp.replace(":", "."), stamp, fragments),
            fragments,
        )
        return stamp

    def members(stamp: str) -> frozenset[str]:
        return frozenset(fragment["id"] for fragment in corpora[stamp][1])

    def groups(stamp: str) -> list[list[str]]:
        held: dict[str, list[str]] = collections.defaultdict(list)
        for fragment in corpora[stamp][1]:
            if fragment["duplicate_group"]:
                held[fragment["duplicate_group"]].append(fragment["id"])
        return [held[group] for group in sorted(held)]

    def serve(stamp: str) -> None:
        shutil.rmtree(served, ignore_errors=True)
        shutil.copytree(corpora[stamp][0], served)

    def served_stamp() -> str:
        return json.loads((served / "manifest.json").read_text())["generated_at"]

    run.current = build(range(len(GROUPS)))
    serve(run.current)
    run.server = VerdictStore(store, journal_path)
    assert run.server.stamp_empty_store(run.current) is not None

    def ran(call: Callable[[], int]) -> str:
        codes: list[int] = []
        printed = _printed(lambda: codes.append(call()), root)
        assert codes == [0], (seed, trail[-8:], printed)
        return printed

    def refused(call: Callable[[], int]) -> str:
        codes: list[int] = []
        printed = _printed(lambda: codes.append(call()), root)
        assert codes == [1], (seed, trail[-8:], printed)
        return printed

    def send(*, sets=(), clears=(), stamp: str | None = None, replay: bool = False) -> None:
        body = {
            "format": DELTA_FORMAT,
            "manifest_generated_at": stamp or run.current,
            "sets": list(sets),
            "clears": list(clears),
            "replay": replay,
        }
        status, reply = run.server.receive(
            json.dumps(body).encode(), ServedCorpus(run.current, members(run.current))
        )
        assert status == 200, (seed, trail[-8:], reply)

    def act(*, moving: bool = False) -> str:
        """One save, clear or undo a tab sends, applied to the server and to the model, stamped for the served corpus unless it comes from a stale tab; an undo puts back the unit's state before its last save, a listed unit with none taking its accepted record back. `moving` says the server has just begun to serve that corpus, so the save moves the store onto it first (rule 3)."""
        model = run.model
        unit = rng.choice(run.generation)["id"]
        kinds = ("set", "clear", "undo", "stale", "replay")
        kind = rng.choices(kinds[:3] if moving else kinds, (6, 3, 2, 2, 1)[: 3 if moving else 5])[0]
        stale = [stamp for stamp in corpora if stamp != run.current]
        if (kind == "undo" and unit not in previous and unit not in model.listed) or (
            kind == "stale" and not stale
        ):
            kind = "set"
        before, prior = model.state.get(unit), previous.get(unit)
        at = tick()
        if kind in ("stale", "replay"):
            at = rng.choice((at, rng.choice(issued)))
            current = model.answer(unit)
            base_at = current[2] if current is not None and rng.random() < 0.3 else None
            stamp = rng.choice(stale) if kind == "stale" else None
            members_served = members(run.current) if kind == "stale" else None
            if rng.random() < 0.7:
                record = {
                    "unit": unit,
                    "verdict": rng.choice(KINDS),
                    "note": "",
                    "at": at,
                    "base_at": base_at,
                }
                send(sets=[record], stamp=stamp, replay=kind == "replay")
                model.save(record, tested=True, members=members_served)
            else:
                send(
                    clears=[{"unit": unit, "base_at": base_at, "at": at}],
                    stamp=stamp,
                    replay=kind == "replay",
                )
                model.clear(unit, at, tested=True, base_at=base_at)
            previous[unit] = before
            return f"{kind} {unit}"
        record = None
        if kind == "set":
            record = {
                "unit": unit,
                "verdict": rng.choice(KINDS),
                "note": rng.choice(("", "", "typed")),
                "at": at,
            }
            if unit in model.listed and rng.random() < 0.25:
                record.update(verdict=model.listed[unit], note="")
        elif kind == "undo" and prior is not None and prior[0] == "record":
            _record, verdict, note, then = prior
            record = {"unit": unit, "verdict": verdict, "note": note, "at": then}
        elif kind == "undo" and prior is None and unit in model.listed:
            record = {"unit": unit, "verdict": model.listed[unit], "note": "", "at": model.closed_at}
        if record is not None:
            send(sets=[record])
            model.save(record)
        else:
            send(clears=[{"unit": unit, "base_at": None, "at": at}])
            model.clear(unit, at)
        previous[unit] = before
        return f"{kind} {unit}" + (f" {record['verdict']}" if record is not None else "")

    def page() -> str:
        """A page of the review app on the served corpus: one to three of the reader's actions, then its save, or a failed save whose unsaved files the reader Imports, and sometimes an Export that the reader Imports."""
        run.server.refresh_if_changed()
        get = json.loads(run.server.payload_bytes(token=True, within=members(run.current)))
        units = sorted(members(run.current))
        actions = []
        for _ in range(rng.randint(1, 3)):
            kind = rng.choices(("set", "clear", "undo", "note", "duplicates"), (5, 2, 2, 1, 1))[0]
            unit = rng.choice(units)
            action: dict[str, object] = {"kind": kind, "unit": unit, "at": tick()}
            if kind in ("set", "duplicates"):
                action.update(verdict=rng.choice(KINDS), note=rng.choice(("", "typed")))
            if kind == "note":
                action["note"] = f"typed at {action['at']}"
            if kind == "duplicates":
                action["duplicates"] = next((group for group in groups(run.current) if unit in group), [])
            actions.append(action)
        reply = run.page.session(get, run.current, actions, tick())
        sets, clears, exported = _page_reply(run.model, members(run.current), actions)
        unsaved = [file["payload"] for file in reply["unsaved"]]
        for made, wanted in (
            (reply["delta"]["sets"], sets),
            (reply["exported"]["verdicts"], exported),
            ([record for payload in unsaved for record in payload["verdicts"]], sets),
        ):
            assert {
                record["unit"]: (record["verdict"], record["note"], record["at"]) for record in made
            } == wanted, (
                seed,
                trail[-8:],
                actions,
            )
        for made in (
            reply["delta"]["clears"],
            [entry for payload in unsaved for entry in payload.get("cleared", [])],
        ):
            assert {entry["unit"]: entry["at"] for entry in made} == clears, (seed, trail[-8:], actions)
        imports = []
        if rng.random() < 0.8:
            delta = reply["delta"]
            send(sets=delta["sets"], clears=delta["clears"])
            for record in delta["sets"]:
                run.model.save(record)
            for clear in delta["clears"]:
                run.model.clear(clear["unit"], clear["at"])
            done = "saved"
        else:
            imports += unsaved
            done = "save failed, unsaved files imported"
        if rng.random() < 0.3:
            imports.append(reply["exported"])
            done += ", Export imported"
        for document in imports:
            path = inputs / f"page-{next(serials)}.json"
            path.write_text(json.dumps(document))
            ran(lambda: run_merge(path))
            for record in accepted.records_of(document):
                run.model.merge(record)
        return f"page {[action['kind'] for action in actions]}: {done}"

    def records() -> list[dict]:
        """One to three records an Import, an Export or a master holds, dated now, earlier, before the first close-out or at the last one."""
        held = []
        for unit in rng.sample([fragment["id"] for fragment in run.generation], rng.randint(1, 3)):
            at = rng.choice(
                (tick(), tick(), rng.choice(issued), BEFORE_THE_FIRST_CLOSE_OUT, run.model.closed_at)
            )
            record = {
                "unit": unit,
                "verdict": rng.choice(KINDS),
                "note": rng.choice(("", "merged")),
                "at": at,
            }
            if unit in run.model.listed and rng.random() < 0.3:
                record.update(verdict=run.model.listed[unit], note="")
            held.append(record)
        return held

    def verdicts_file(path: Path, stamp: str, held: list[dict]) -> Path:
        path.write_text(
            json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "verdicts": held})
        )
        return path

    def run_merge(path: Path) -> int:
        corpus = corpora[run.current][0]
        return merge_verdicts.run_merge(
            [path], autosave=store, corpus=corpus, journal_path=journal_path, dry_run=False
        )

    def merge() -> str:
        """A merge of an Import, or of a carried file a --no-merge pass left at the root stamped for the store's corpus."""
        carried = [
            path
            for path in sorted(root.glob("verdicts-carried-*.json"))
            if json.loads(path.read_text())["manifest_generated_at"] == run.current
        ]
        if carried and rng.random() < 0.4:
            path = rng.choice(carried)
            held = accepted.records_of(json.loads(path.read_text()))
        else:
            held = records()
            path = verdicts_file(inputs / f"merge-{next(serials)}.json", run.current, held)
        ran(lambda: run_merge(path))
        for record in held:
            run.model.merge(record)
        return f"merge {path.name} {held}"

    def written() -> None:
        """The review server's idle write of the saves it journaled first, which a tool that reads the store without its lock, run by hand, waits for to read them."""
        assert write_pending_store(run.server), (seed, trail[-8:])

    def fill() -> str:
        out = inputs / f"fill-{next(serials)}.json"
        corpus = corpora[run.current][0]
        written()
        ran(lambda: duplicate_verdicts.main([str(store), "--corpus", str(corpus), "--out", str(out)]))
        ran(lambda: run_merge(out))
        run.model.fill(groups(run.current))
        return "fill"

    def defer() -> str:
        """The complaint list, and an Import of the defer file for one of its groups, which holds a skip for every blank or skipped unit its pointers reach."""
        corpus = corpora[run.current][0]
        out = inputs / f"complaints-{next(serials)}.json"
        argv = [str(store), "--corpus", str(corpus), "--data-out", str(out)]
        written()
        ran(lambda: complaint_list.main(argv))
        deferrable = [
            group
            for group in json.loads(out.read_text())["groups"]
            if group["defer_file"] is not None and group["defer_candidates"]["count"]
        ]
        if not deferrable:
            return "complaint list, nothing to defer"
        group = rng.choice(deferrable)
        ran(lambda: complaint_list.main([*argv, "--defer", group["id"], "--defer-dir", str(inputs)]))
        path = inputs / group["defer_file"]
        held = json.loads(path.read_text())["verdicts"]
        blank = {
            unit
            for unit in members(run.current)
            if (answer := run.model.answer(unit)) is None or answer[0] == "skip"
        }
        assert {record["unit"] for record in held} == blank, (seed, trail[-8:])
        ran(lambda: run_merge(path))
        for record in held:
            run.model.merge(record)
        return f"defer {sorted(blank)}"

    def planned(run_dir: Path, **flags) -> dict[str, list[str] | None]:
        """The argv of each step `artifact_cycle.build_plan` plans for a pass whose scratch directory is `run_dir`, with `flags` naming its master and its form."""
        with monkeypatch.context() as patch:
            patch.setattr(ac, "ROOT", root)
            plan = ac.build_plan(
                no_carry=flags.pop("no_carry", False),
                carry_out=None,
                skip_gates=True,
                first_run=False,
                short_id=run_dir.name,
                skip_run_m1=True,
                skip_corpus=True,
                skip_contracts=True,
                scratch_dir=run_dir,
                allow_orphans=True,
                ncores=4,
                total_bytes=16 << 30,
                **flags,
            )
        return {step.name: step.argv for step in plan.steps}

    def update(steps: dict[str, list[str] | None]) -> None:
        argv = steps["verdict-update"]
        if argv is not None:
            ran(lambda: vu.main(argv[5:]))

    def land(argv: list[str] | None, staged: bool, *, dying: bool = True) -> str | None:
        """Run the land the plan names, when `dying` sometimes killing it at one of its writes and finishing it as the next writer of the store does (`landing.finish_interrupted_land`), and return where it was killed, or None; a land killed before it swapped the corpus in (`exchange_dirs`) is dropped, and any other lands."""
        assert argv is not None
        points = ("record_transition", "exchange_dirs", "link_stash") if staged else ("record_transition",)
        point = rng.choice(points) if dying and rng.random() < 0.2 else None
        owner = journal if point == "record_transition" else landing
        with _killed_at(owner, point) if point is not None else contextlib.nullcontext([]) as fired:
            try:
                ran(lambda: landing.main(argv[5:]))
            except _Killed:
                pass
        if fired:
            assert landing.finish_interrupted_land(store) is not None, (seed, trail[-8:], point)
        for discard in landing.discard_paths(served):
            shutil.rmtree(discard)
        return point if fired else None

    def cycle_pass(target: str = "", *, store_only: bool = False) -> str:
        """A cycle pass: the snapshot, the verdict update into the prepared store, saves made meanwhile, and the land. A carried pass carries the store, and sometimes an Export, onto `target` or a new corpus and swaps it in; a store-only pass merges a master into the store on the served corpus, carrying the store onto it first when the store is on an older corpus, as after a restore, when no save is made meanwhile."""
        run_dir = tmp_path / "passes" / str(next(serials))
        landing.snapshot(store, run_dir)
        before = run.model.copy()
        prepared = before.copy()
        target = served_stamp() if store_only else target or build()
        moving = target != run.current
        held: list[dict] = []
        if store_only:
            held = records()
            flags: dict = {
                "verdicts": verdicts_file(run_dir / "master.json", target, held),
                "direct_merge": True,
            }
            if moving:
                prepared.carried(members(target), [])
            for record in held:
                prepared.merge(record)
        else:
            promote = served.with_name(f"review.{run_dir.name}")
            shutil.copytree(corpora[target][0], promote)
            flags = {"verdicts": store, "promote_corpus": promote}
            if rng.random() < 0.5:
                held = records()
                flags["verdicts"] = verdicts_file(run_dir / "export.json", rng.choice(list(corpora)), held)
            prepared.carried(members(target), held)
        steps = planned(run_dir, **flags)
        update(steps)
        prepared.fill(groups(target), twice=True, standing=(members(target), target))
        during = [] if store_only and moving else [act() for _ in range(rng.choice((0, 1, 1, 2)))]
        done = f"{'store-only' if store_only else 'carried'} pass {held} with {during}"
        killed = land(steps["land"], staged=not store_only)
        if killed == "exchange_dirs":
            shutil.rmtree(served.with_name(f"review.{run_dir.name}"))
            return f"{done}, its land killed before the swap"
        prepared.landed(before, run.model, members(target) if moving else None)
        run.model, run.current = prepared, target
        return done + (f", its land killed at {killed} and finished" if killed else "")

    def bare_pass(*, unreadable: bool = False) -> str:
        """A pass that moves the corpus and merges nothing into the store, a --no-merge one, whose carry leaves its file at the root, or a --no-carry one: the snapshot, saves made meanwhile, and a land with no prepared store. Over an `unreadable` store it is a --no-carry pass with no save meanwhile."""
        run_dir = tmp_path / "passes" / str(next(serials))
        landing.snapshot(store, run_dir)
        before = run.model.copy()
        target = build()
        promote = served.with_name(f"review.{run_dir.name}")
        shutil.copytree(corpora[target][0], promote)
        form = "no_carry" if unreadable or rng.random() < 0.5 else "no_merge"
        steps = planned(run_dir, verdicts=store, promote_corpus=promote, **{form: True})
        update(steps)
        prepared = before.copy()
        prepared.emptied(members(target))
        during = [] if unreadable else [act() for _ in range(rng.choice((0, 1, 1, 2)))]
        killed = land(steps["land"], staged=True, dying=not unreadable)
        if killed == "exchange_dirs":
            shutil.rmtree(promote)
            return f"{form} pass with {during}, its land killed before the swap"
        prepared.landed(before, run.model, members(target))
        run.model, run.current = prepared, target
        return f"{form} pass with {during}" + (
            f", its land killed at {killed} and finished" if killed else ""
        )

    def server_move() -> str:
        target = build()
        serve(target)
        run.model.moved(members(target))
        run.current = target
        return f"server move: {act(moving=True)}"

    def close() -> str:
        """A close-out, sometimes killed before it renamed anything, after it renamed the list, or before it wrote the new journal, and then run again, which drops the first or finishes it; a dropped one folds on the run after."""
        now = tick(whole=True)
        archived, found = run.model.copy(), list_path.read_bytes()
        corpus = corpora[run.current][0]

        def apply() -> int:
            return close_out.run(
                store=store,
                corpus=corpus,
                apply=True,
                now=lambda: now,
                readiness=lambda: ({"checks": {}, "corpus": {"generated_at": run.current}}, True),
                serving=lambda _store: False,
            )

        point = rng.choice(("intent", "store", "journal")) if rng.random() < 0.2 else None
        killers = {
            "intent": lambda: _killed_at(close_out, "_complete"),
            "store": lambda: _killed_at(os, "replace", lambda _source, target, **_: Path(target) == store),
            "journal": lambda: _killed_at(journal, "record_transition"),
        }
        with killers[point]() if point is not None else contextlib.nullcontext([]) as fired:
            try:
                ran(apply)
            except _Killed:
                pass
        if fired:
            ran(apply)
            if point == "intent":
                ran(apply)
        run.model.closed(members(run.current), now)
        if list_path.read_bytes() != found:
            run.archive, run.moments = archived, []
        run.server = VerdictStore(store, journal_path)
        previous.clear()
        return f"close-out{f' killed at {point}' if fired else ''}"

    def undo_close() -> str:
        if run.archive is None:
            return act()
        corpus = corpora[run.current][0]
        ran(lambda: close_out.run(store=store, corpus=corpus, undo=True, serving=lambda _store: False))
        run.model.undone(run.archive)
        run.server = VerdictStore(store, journal_path)
        previous.clear()
        return "close-out undo"

    def restore() -> str:
        """A restore of a moment the journal holds under the current list, or from before the re-key with the kept id map, previewed and then applied; one that puts the store on an older corpus is followed by a store-only pass onto the served corpus or a save that moves the store there."""
        held = accepted.read(list_path)
        choices = []
        for moment in run.moments:
            if moment.old_ids == (run.generation is olds) and moment.field == held.field():
                choices.append((moment, None))
            elif moment.old_ids and run.generation is news and kept_map.exists():
                if merge_verdicts.rekeyed_field(moment.field, kept_map) == held.field():
                    choices.append((moment, kept_map))
        if not choices:
            return act()
        moment, rekey_map = rng.choice(choices)
        expected = moment.model.copy()
        if rekey_map is not None:
            expected.rekeyed(json.loads(kept_map.read_text())["renamed"], expected.listed)
            expected.listed, expected.closed_at = dict(run.model.listed), run.model.closed_at
        preview = inputs / f"restore-{next(serials)}.json"

        def restored(out: Path | None) -> int:
            return merge_verdicts.run_restore(
                moment.at,
                autosave=store,
                journal_path=journal_path,
                out=out,
                apply=out is None,
                yes=True,
                rekey_map=rekey_map,
                accepted_path=list_path,
            )

        ran(lambda: restored(preview))
        document = json.loads(preview.read_text())
        accepted.pair(document, held, store=preview)
        assert _stored(document) == expected.state, (seed, trail[-8:])
        ran(lambda: restored(None))
        serving = run.current
        run.model, run.current = expected, moment.stamp
        previous.clear()
        done = f"restore as of {moment.at}{' re-keyed' if rekey_map else ''}"
        if moment.stamp == serving:
            return done
        if rng.random() < 0.5:
            return f"{done}, then {cycle_pass(store_only=True)}"
        run.model.moved(members(serving))
        run.current = serving
        return f"{done}, then a save that moves it: {act(moving=True)}"

    def retention() -> str:
        """Retention's stash prune, and a compaction of the journal at a moment's `at`, after which a restore reaches no moment before the base it keeps; neither writes the store once a lock holder has caught it up with the journal."""
        with landing.locked_store(store):
            held = store.read_bytes()
        ac.prune_stashes(root, journal_path, lock=landing.locked_store(store))
        done = "retention"
        if run.moments:
            cutoff = rng.choice(run.moments).at
            result = journal.compact(journal_path, cutoff=cutoff, lock=landing.locked_store(store))
            if result["compacted"]:
                run.moments = [moment for moment in run.moments if moment.at >= result["floor_at"]]
                done += f", journal compacted from {result['floor_at']}"
        assert store.read_bytes() == held, (seed, trail[-8:])
        return done

    def lost() -> str:
        """The store deleted, as on a fresh clone or a machine whose store was deleted, sometimes with its journal, and then stamped empty by the review server at boot or merged into."""
        store.unlink()
        if rng.random() < 0.3:
            journal_path.unlink(missing_ok=True)
            run.moments = []
        run.model.state = {}
        previous.clear()
        if rng.random() < 0.5:
            run.server = VerdictStore(store, journal_path)
            assert run.server.stamp_empty_store(run.current) is not None, (seed, trail[-8:])
            return "store lost, stamped empty at boot"
        held = records()
        ran(lambda: run_merge(verdicts_file(inputs / f"merge-{next(serials)}.json", run.current, held)))
        for record in held:
            run.model.merge(record)
        return f"store lost, merged into {held}"

    def unreadable() -> str:
        """The store left unreadable, as by a write cut short, and then written over by a save, which journals its delta over what the journal holds, by a merge, which stashes it, or by a --no-carry pass's land."""
        store.write_bytes(b'{"format": "ams-review-verdicts/1", "verdicts": [\n')
        run.model.state = {}
        previous.clear()
        form = rng.choice(("save", "merge", "land"))
        if form == "save":
            run.diverged = True
            return f"store unreadable, then {act()}"
        if form == "merge":
            held = records()
            ran(lambda: run_merge(verdicts_file(inputs / f"merge-{next(serials)}.json", run.current, held)))
            for record in held:
                run.model.merge(record)
            return f"store unreadable, then merged into {held}"
        return f"store unreadable, then {bare_pass(unreadable=True)}"

    def rekey() -> str:
        """The re-key: its map from every old id or from the served corpus, applied onto a corpus holding every new id or, under --allow-unmatched, some of them, sometimes undone and applied again, and then its carried pass."""
        form = rng.choice(("whole", "subset", "served"))
        source = corpora[run.current][0] if form == "served" else tmp_path / "rekey-old"
        if not source.exists():
            _corpus_at(source, "2026-11-09T00:00:00Z", olds)
        map_path = tmp_path / "rekey" / f"unit-id-map-{next(serials)}.json"
        ran(
            lambda: rekey_verdicts.main(
                ["--root", str(root), "--from-corpus", str(source), "--map-out", str(map_path)]
            )
        )
        renamed = json.loads(map_path.read_text())["renamed"]
        saved = (run.model.copy(), run.archive.copy() if run.archive is not None else None, list(run.moments))
        run.generation = news
        target = build(None if form == "subset" else range(len(GROUPS)))
        argv = ["--root", str(root), "--map", str(map_path), "--corpus", str(corpora[target][0])]
        argv += [] if form == "whole" else ["--allow-unmatched"]
        landed = {old: new for old, new in renamed.items() if new in members(target)}

        def backups() -> list[Path]:
            return sorted(path.parent for path in kept_map.parent.glob(f"*/{rekey_verdicts.BACKUP_RECORD}"))

        def apply() -> str:
            printed = ran(lambda: rekey_verdicts.main(argv))
            for model in (run.model, run.archive):
                if model is not None:
                    model.rekeyed(landed)
            dropped = re.search(
                r"(\d+) dropped as off the map's corpus, (\d+) landing nowhere dropped", printed
            )
            return f"{dropped[1]} listed ids left, {dropped[2]} landing nowhere" if dropped else "no list"

        kept = backups()
        drops = apply()
        undone = rng.random() < 0.3 and backups() != kept
        if undone:
            ran(lambda: rekey_verdicts.main(["--root", str(root), "--undo", str(backups()[-1])]))
            run.model, run.archive, run.moments = saved
            run.generation = olds
            check(-1)
            run.generation = news
            apply()
        previous.clear()
        return f"re-key ({form}, {drops}{', undone and applied again' if undone else ''}), then {cycle_pass(target)}"

    def stale_rekey() -> str:
        """After the re-key, an Import still on old ids, and a second re-key through the kept map onto a corpus holding every new id, which refuses, writing nothing, when an old id lands on a listed id (`listed_collisions`) or beside a record on its new id."""
        if run.generation is olds:
            return act()
        renamed = json.loads(kept_map.read_text())["renamed"]
        mapped = [fragment["id"] for fragment in olds if fragment["id"] in renamed]
        held = [
            {
                "unit": unit,
                "verdict": rng.choice(KINDS),
                "note": "",
                "at": rng.choice((tick(), rng.choice(issued))),
            }
            for unit in rng.sample(mapped, min(len(mapped), rng.randint(1, 2)))
        ]
        ran(lambda: run_merge(verdicts_file(inputs / f"merge-{next(serials)}.json", run.current, held)))
        for record in held:
            run.model.merge(record)
        full = build(range(len(GROUPS)))
        argv = ["--root", str(root), "--map", str(kept_map), "--corpus", str(corpora[full][0])]
        argv.append("--allow-unmatched")

        def collided(units: Iterable[str]) -> bool:
            named = set(units)
            return any(renamed.get(unit, unit) != unit and renamed[unit] in named for unit in named)

        def recorded(model: _Model | None) -> list[str]:
            return (
                [] if model is None else [unit for unit, entry in model.state.items() if entry[0] == "record"]
            )

        stamps = set(json.loads(kept_map.read_text())["from_corpora"])
        stashed = []
        for path in sorted(root.glob("verdicts-*.json")):
            with contextlib.suppress(ValueError):
                document = json.loads(path.read_text())
                if path != store and document.get("manifest_generated_at") in stamps:
                    stashed.append([record["unit"] for record in accepted.records_of(document)])
        reasons = set()
        model = run.model
        for unit, entry in model.state.items():
            new = renamed.get(unit, unit)
            if new != unit and new in model.listed and (entry[0] == "record" or entry[1] <= model.closed_at):
                reasons.add("listed")
        if any(collided(units) for units in (recorded(model), recorded(run.archive), *stashed)):
            reasons.add("collided")
        with landing.locked_store(store):
            files = (store.read_bytes(), list_path.read_bytes())
        if reasons:
            printed = refused(lambda: rekey_verdicts.main(argv))
            reason = "listed" if "lands on one unit with the store's" in printed else "collided"
            assert reason in reasons and (store.read_bytes(), list_path.read_bytes()) == files, (
                seed,
                trail[-8:],
            )
            return f"stale import {held}, its re-key refused ({reason})"
        ran(lambda: rekey_verdicts.main(argv))
        moves = {unit: new for unit, new in renamed.items() if new in members(full)}
        for model in (run.model, run.archive):
            if model is not None:
                model.rekeyed(moves, model.listed)
        return f"stale import {held}, re-keyed"

    def check(step: int) -> None:
        model, where = run.model, (seed, step, trail[-8:])
        held = accepted.read(list_path)
        assert (dict(held.verdicts), held.closed_at) == (model.listed, model.closed_at), where
        document = json.loads(
            _caught_up_copy(store, journal_path, list_path, tmp_path / "shadow" / str(step)).read_text()
        )
        assert document["manifest_generated_at"] == run.current, where
        run.server.refresh_if_changed()
        for view in (accepted.view_of(document, held, store=store), run.server.view):
            for unit in every:
                got = view.verdict_of(unit)
                answer = None if got is None else (got["verdict"], got.get("note") or "", got["at"])
                assert answer == model.answer(unit), (*where, unit)
        own = [entry["unit"] for entry in (*document["verdicts"], *document.get("cleared", []))]
        assert not [unit for unit in own if unit in held.verdicts], where
        section = accepted.Overrides.of(document)
        assert set(section.records) | set(section.cleared) <= set(held.verdicts), where
        assert _stored(document) == model.state, (*where, "store")
        stamp, replayed, tombstones, field = journal.replay_with_list(journal_path)
        agrees = (stamp, field, _state_of(replayed, tombstones)) == (run.current, held.field(), model.state)
        assert agrees or run.diverged, (*where, "journal")
        run.diverged = run.diverged and not agrees
        last = None
        for event in journal.iter_events(journal_path):
            last = event["at"]
        if agrees and last is not None and (not run.moments or run.moments[-1].at != last):
            run.moments.append(_Moment(last, model.copy(), run.current, held.field(), run.generation is olds))

    store_only_pass = functools.partial(cycle_pass, store_only=True)
    steps = [act] * 8 + [
        merge,
        merge,
        fill,
        defer,
        cycle_pass,
        cycle_pass,
        store_only_pass,
        bare_pass,
        server_move,
        close,
        undo_close,
        restore,
        restore,
        retention,
        lost,
        unreadable,
        stale_rekey,
    ]
    node = shutil.which("node")
    with contextlib.ExitStack() as stack:
        if node is not None:
            run.page = _Page(node)
            stack.callback(run.page.close)
            steps += [page, page]
        rekeyed_at = rng.randrange(10, STEPS - 15)
        for step in range(STEPS):
            trail.append(rekey() if step == rekeyed_at else rng.choice(steps)())
            check(step)
