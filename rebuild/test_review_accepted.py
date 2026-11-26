"""Tests for `rebuild.review.accepted`, the accepted list's reader: the format and its refusals, the list's stamp and its pairing with the store's `accepted` field, the rule every reader answers through, the bulk view and the review app's field, the folds the shared samples pin, the list's path, and the names the module spells out instead of importing them. Then the readers on the read path, run whole over the contract fixture's human units: a store folded and the same store unfolded give each of them the same verdicts, each refuses a store beside a list it was not folded against, each reads a store under another name beside the list `--accepted` names, and each reads an Export of the store, which holds its records alone, as it reads the store.

Most cases come from `rebuild/review/fixtures/accepted-samples.json`, whose README section says what each part holds. `fold_state` writes one side of a fold sample out as a store document and a list file, for any test that runs a reader over both sides.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from rebuild.review import accepted, export, journal, landing, unit_cache, verdict_store
from rebuild.review.verdict_store import DELTA_FORMAT, ServedCorpus, VerdictStore
from rebuild.tools import verdict_ready

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


def _view(tmp_path: Path) -> tuple[dict, accepted.View]:
    """The view sample's store read through its list (`accepted.view_of`)."""
    scenario = _samples()["view"]
    held = accepted.read(_list_at(tmp_path, scenario["list"]))
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


def test_every_reader_answers_a_unit_by_its_record_then_its_tombstone_then_the_list(tmp_path):
    scenario, view = _view(tmp_path)
    for unit, answer in scenario["answers"].items():
        assert view.verdict_of(unit) == answer, unit
        assert accepted.verdict_of(unit, view.records, view.overrides, view.accepted) == answer, unit
        assert (unit in view, view.get(unit)) == (answer is not None, answer), unit
        if answer is None:
            with pytest.raises(KeyError):
                view[unit]
        else:
            assert view[unit] == answer


def test_a_view_walks_the_stores_units_then_the_listed_ids_it_answers_from_the_list(tmp_path):
    scenario, view = _view(tmp_path)
    answered = {unit: answer for unit, answer in scenario["answers"].items() if answer is not None}
    from_list = [unit for unit in answered if unit not in view.records and unit not in view.overrides]
    assert [unit for unit in answered if view.is_accepted(unit)] == from_list
    assert list(view.from_list()) == [(unit, answered[unit]["verdict"]) for unit in from_list]
    assert list(view) == [*view.records, *view.overrides.records, *from_list]
    assert len(view) == len(answered)
    assert dict(view.items()) == answered


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


def _served_manifest(root: Path, mode: str, stamp: str, ids: list[str]) -> None:
    served = root / verdict_store.SERVED_CORPUS
    served.mkdir(parents=True, exist_ok=True)
    manifest = {"format": "ams-review-manifest/2", "mode": mode, "generated_at": stamp, "human_unit_ids": ids}
    (served / "manifest.json").write_text(json.dumps(manifest, indent=1))


def test_a_store_beside_a_table_diff_corpus_reads_no_list_and_follows_the_served_corpus(tmp_path):
    """The live store reads the accepted list beside it only while the corpus served beside it is an m1-audit corpus (`verdict_store.accepted_path_for`, by the manifest's `mode`): beside a table-diff corpus the review server's store reads none, so a save writes no `accepted` field, the full GET's field names no list, and no listed unit counts. A store constructed with no list named asks again at each reload, so when a land swaps a table-diff corpus in for an m1-audit one the store it reloads stops reading the list, and a store named another way, or given a list, reads as before."""
    root = tmp_path
    store = root / accepted.AUTOSAVE_NAME
    list_path = accepted.path_for(store)
    list_path.parent.mkdir(parents=True)
    listed = "u-" + "1" * 11
    list_path.write_bytes(accepted.render("2026-11-01T00:00:00Z", {listed: "approve"}))
    assert verdict_store.corpus_mode(root / verdict_store.SERVED_CORPUS) is None
    assert verdict_store.accepted_path_for(store) == list_path

    _served_manifest(root, "m1-audit", "A", [listed])
    assert verdict_store.corpus_mode(root / verdict_store.SERVED_CORPUS) == "m1-audit"
    server = VerdictStore(store, root / journal.JOURNAL_NAME)
    assert server.accepted.sha256 is not None and listed in server.accepted.verdicts
    assert server.stamp_empty_store("A") is not None
    assert json.loads(store.read_text())["accepted"] == server.accepted.field()

    _served_manifest(root, verdict_store.TABLE_DIFF_MODE, "B", ["u-new"])
    assert verdict_store.accepted_path_for(store) is None
    assert verdict_store.accepted_path_for(store, root / verdict_store.SERVED_CORPUS) is None
    store.write_bytes(landing.document_bytes(landing.Store("B")))
    assert server.refresh_if_changed()
    assert server.refusal is None and server.stamp == "B"
    assert server.accepted.sha256 is None and not server.accepted.verdicts
    body = {
        "format": DELTA_FORMAT,
        "manifest_generated_at": "B",
        "sets": [{"unit": "u-new", "verdict": "approve", "note": "", "at": "2026-11-26T00:00:00Z"}],
        "clears": [],
    }
    status_code, reply = server.receive(json.dumps(body).encode(), ServedCorpus("B", frozenset({"u-new"})))
    assert status_code == 200, reply
    server.write()
    written = json.loads(store.read_text())
    assert "accepted" not in written and [record["unit"] for record in written["verdicts"]] == ["u-new"]
    field = json.loads(b"".join(server.payload_chunks(token=True, within=frozenset({"u-new"}))))["accepted"]
    assert field == {"sha256": None, "closed_at": None, "approve": [], "either": [], "identical": []}

    fresh = root / "fresh"
    fresh.mkdir()
    assert VerdictStore(fresh / accepted.AUTOSAVE_NAME).stamp_empty_store("B") is None
    assert verdict_store.accepted_path_for(root / "other.json") is None
    assert VerdictStore(store, accepted_path=list_path).accepted.sha256 is not None


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
    "triage export": _triage_export,
}
READERS: dict[str, Callable[..., object]] = {
    **CLI_READERS,
    "verdict ready": _verdict_ready,
}


@pytest.mark.parametrize("reader", sorted(READERS))
def test_every_reader_gives_a_folded_store_the_verdicts_the_unfolded_store_gave(reader, tmp_path):
    """A close-out changes no decision: each fold sample's store and list after the fold give the reader the same verdict kind for every human unit as the store and list before it, so the triage export's counts and drafts and `make verdict-ready`'s checks come out the same. What differs by design is left out of each reader's answer above: the notes an accepted record drops and the `at` it takes (decisions 24 and 29)."""
    for index, fold in enumerate(_samples()["folds"]):
        answers = [
            READERS[reader](_fold_side(tmp_path / str(index) / side, fold[side]))
            for side in ("before", "after")
        ]
        assert answers[0] == answers[1], fold["why"]


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
