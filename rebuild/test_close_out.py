"""Tests for the close-out (`rebuild.tools.close_out`): the fold of each shared fold sample, the archive and what the close-out deletes, the dry run, each refusal, the close-out time, a close-out killed at each rename, with its staging directory kept, past a pass's sweep of var/cycle/ or lost, the undo, and a store lagging the saves its journal holds, which the close-out's lock and the undo's write into it first. Every checkout is a git repository under `tmp_path` whose corpus is the review fixtures' manifest, with `make verdict-ready` and the server check stubbed."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rebuild.review import accepted, journal
from rebuild.review.store_lock import hold_flock
from rebuild.review.verdict_store import DELTA_FORMAT, VerdictStore
from rebuild.test_review_accepted import fold_state
from rebuild.tools import close_out

FIXTURES = Path(__file__).resolve().parent / "review" / "fixtures"
SAMPLES = FIXTURES / "accepted-samples.json"
CLOSED_AT = "2026-11-05T18:00:00Z"
STAMP = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["generated_at"]
HUMAN = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["human_unit_ids"]
STASH = "verdicts-autosave-2026-10-29T01.07.49Z.json"


class Killed(Exception):
    pass


def _folds() -> list[dict]:
    return json.loads(SAMPLES.read_text(encoding="utf-8"))["folds"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _checkout(tmp_path: Path, side: dict) -> Path:
    """Write one side of a fold sample as a checkout's store, its journal and its list, the list committed, and return the store."""
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    held, document = fold_state(root, side, STAMP)
    if held.sha256 is not None:
        _git(root, "add", str(accepted.RELATIVE_PATH))
        _git(root, "commit", "-q", "-m", "list")
    store = root / journal.AUTOSAVE_NAME
    store.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    records, cleared = accepted.joined(
        journal.latest_by_unit(document["verdicts"]),
        journal.parse_cleared(document),
        accepted.Overrides.of(document),
    )
    journal.record_transition(
        root / journal.JOURNAL_NAME,
        source="merge",
        stamp=STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=list(records.values()),
        new_cleared=cleared,
    )
    return store


def _run(store: Path, *, apply=False, undo=False, ready=True, serving=False) -> int:
    checks = (
        {}
        if ready
        else {"freshness": {"level": "fail", "detail": "the corpus is stale", "remedy": "make it"}}
    )
    return close_out.run(
        store=store,
        corpus=FIXTURES,
        apply=apply,
        undo=undo,
        now=lambda: CLOSED_AT,
        readiness=lambda: ({"checks": checks, "corpus": {"generated_at": STAMP}}, ready),
        serving=lambda _store: serving,
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts and not path.name.endswith(".lock")
    }


def _document(store: Path) -> dict:
    return json.loads(store.read_text(encoding="utf-8"))


def _save(store: Path, sets=(), clears=()) -> None:
    """Save a delta through the review server's store, which journals it and leaves the store file as it was until its idle write."""
    server = VerdictStore(store, store.parent / journal.JOURNAL_NAME)
    body = {
        "format": DELTA_FORMAT,
        "manifest_generated_at": STAMP,
        "sets": list(sets),
        "clears": list(clears),
    }
    assert server.receive(json.dumps(body).encode())[0] == 200 and server.pending


def _kinds(store: Path) -> dict[str, str | None]:
    view = accepted.view_of(_document(store), accepted.read(accepted.path_for(store)), store=store)
    return {unit: (view.verdict_of(unit) or {}).get("verdict") for unit in HUMAN}


@pytest.mark.parametrize("index", range(len(_folds())))
def test_each_fold_sample_closes_out_to_its_after_side_and_keeps_only_the_last_archive(
    tmp_path, capsys, index
):
    """The close-out of a fold sample's `before` side writes its `after` side: the list, rendered at the close-out's time; the store's non-accepting records and tombstones on human units, stamped for the corpus, whose field names the list; and a journal of one base event of that store carrying the field. Every reader's verdict kind for every human unit is the sample's. The archive holds the store as found and both lists, gzipped, and is the only one left; the root's stashes, the unjournaled marker and the scan state go, an export stays, and a second close-out finds nothing to fold and writes nothing."""
    fold = _folds()[index]
    store = _checkout(tmp_path, fold["before"])
    root = store.parent
    found, list_found = store.read_bytes(), _files(root).get(str(accepted.RELATIVE_PATH))
    (root / STASH).write_bytes(found)
    (root / "verdicts-export.json").write_bytes(found)
    older = close_out.archive_root_for(store) / "2026-10-25T05.54.00Z"
    older.mkdir(parents=True)
    journal.mark_unjournaled(store, "a test")
    close_out.scan_state_path_for(store).write_text("{}")

    assert _run(store, apply=True) == 0
    out = capsys.readouterr().out
    assert f"{len(fold['off_corpus'])} dropped because they left the corpus's human units" in out
    assert f"stashes it deletes: {STASH}" in out

    after = fold["after"]
    list_bytes = accepted.render(after["list"]["closed_at"], after["list"]["verdicts"])
    list_path = accepted.path_for(store)
    assert list_path.read_bytes() == list_bytes
    document = _document(store)
    field = accepted.read(list_path).field()
    assert document["manifest_generated_at"] == STAMP
    assert document["accepted"] == field
    assert journal.latest_by_unit(document["verdicts"]) == {
        record["unit"]: record for record in after["verdicts"]
    }
    assert journal.parse_cleared(document) == journal.parse_cleared(after)
    lines = [json.loads(line) for line in (root / journal.JOURNAL_NAME).read_text().splitlines()]
    assert [line["source"] for line in lines if line["kind"] == "event"] == [close_out.SOURCE]
    assert lines[0]["accepted"] == field and lines[0]["base"]
    assert journal.replay_store(root / journal.JOURNAL_NAME) == (
        STAMP,
        journal.latest_by_unit(document["verdicts"]),
        journal.parse_cleared(document),
    )
    assert _kinds(store) == {unit: fold["kinds"].get(unit) for unit in HUMAN}

    (archive,) = close_out.archive_root_for(store).iterdir()
    assert archive.name == CLOSED_AT.replace(":", ".")
    assert gzip.decompress((archive / close_out.ARCHIVE_STORE).read_bytes()) == found
    assert gzip.decompress((archive / close_out.ARCHIVE_AFTER).read_bytes()) == list_bytes
    before_member = archive / close_out.ARCHIVE_BEFORE
    assert (gzip.decompress(before_member.read_bytes()) if before_member.exists() else None) == list_found
    assert json.loads((archive / close_out.ARCHIVE_COUNTS).read_text())["closed_at"] == CLOSED_AT
    assert not (root / STASH).exists() and (root / "verdicts-export.json").exists()
    assert not journal.unjournaled_marker_for(store).exists()
    assert not close_out.scan_state_path_for(store).exists()
    assert not close_out.intent_path_for(store).exists() and not close_out.staging_path_for(store).exists()

    _git(root, "add", str(accepted.RELATIVE_PATH))
    _git(root, "commit", "-q", "-m", "close out")
    closed = _files(root)
    assert _run(store, apply=True) == 0
    assert "nothing to fold" in capsys.readouterr().out
    assert _files(root) == closed


def test_a_dry_run_reports_the_fold_and_writes_nothing(tmp_path, capsys):
    """The dry run prints the new list's counts and what the fold drops, among them the journal the apply replaces, which no archive keeps, and the earlier close-out's archive it deletes, and leaves every file as it was, the stash and that archive included."""
    store = _checkout(tmp_path, _folds()[1]["before"])
    (store.parent / STASH).write_bytes(store.read_bytes())
    earlier = close_out.archive_root_for(store) / "2026-10-25T12.00.00Z"
    earlier.mkdir(parents=True)
    (earlier / close_out.ARCHIVE_COUNTS).write_text("{}\n")
    journal_path = store.parent / journal.JOURNAL_NAME
    before = _files(tmp_path)
    assert _run(store) == 0
    out = capsys.readouterr().out
    assert "5 ids (3 approve, 1 either, 1 identical), closed at 2026-11-05T18:00:00Z" in out
    assert "taken out by an override or a clear, 1 dropped" in out
    assert (
        f"journal it replaces, kept nowhere: {journal.JOURNAL_NAME} ({journal_path.stat().st_size:,} B, "
        f"first event at {journal.first_event_at(journal_path)})"
    ) in out
    assert "older close-out archives it deletes: var/keep/close-outs/2026-10-25T12.00.00Z" in out
    assert "dry run: nothing written" in out
    assert _files(tmp_path) == before


def _malformed_list(store: Path) -> None:
    list_path = accepted.path_for(store)
    list_path.write_text(list_path.read_text() + "<<<<<<< HEAD\n")
    _git(store.parent, "commit", "-q", "-am", "a conflict")


def _list_off_head(store: Path) -> None:
    list_path = accepted.path_for(store)
    held = accepted.read(list_path)
    list_path.write_bytes(accepted.render(CLOSED_AT, held.verdicts))


def _another_field(store: Path) -> None:
    document = _document(store)
    document["accepted"] = {"sha256": "0" * 64, "closed_at": CLOSED_AT}
    store.write_text(json.dumps(document))


def _malformed_store(store: Path) -> None:
    document = _document(store)
    document["verdicts"].append({"unit": HUMAN[0], "verdict": "approve"})
    store.write_text(json.dumps(document))


@pytest.mark.parametrize(
    ("case", "spoil", "options", "named"),
    [
        ("pass", None, {}, "a pass holds"),
        ("server", None, {"serving": True}, "a review server listening on port 7294 may write"),
        ("readiness", None, {"ready": False}, "freshness: the corpus is stale (remedy: make it)"),
        ("head", _list_off_head, {}, "differs from HEAD's"),
        ("malformed list", _malformed_list, {}, "is a merge conflict marker"),
        ("malformed store", _malformed_store, {}, "holds a verdict record on"),
        ("mismatch", _another_field, {}, "names the accepted list 000"),
    ],
)
def test_each_refusal_names_its_cause_in_the_dry_run_and_the_apply_and_writes_nothing(
    tmp_path, capsys, case, spoil, options, named
):
    """Each check the close-out makes refuses in the dry run and in the apply, naming its cause, and leaves every file as it was."""
    store = _checkout(tmp_path, _folds()[1]["before"])
    if spoil is not None:
        spoil(store)
    before = _files(tmp_path)
    with hold_flock(close_out.pass_lock_path_for(store)) if case == "pass" else contextlib.nullcontext():
        assert _run(store, **options) == 1
        dry = capsys.readouterr().out
        assert _run(store, apply=True, **options) == 1
        out = capsys.readouterr().out
    assert "ERROR: --apply would refuse: " in dry and named in dry
    assert named in out and "Nothing was written." in out
    assert _files(tmp_path) == before


def test_the_close_out_time_is_now_or_the_latest_folded_time_cut_to_whole_seconds():
    """A time later than now is cut to whole seconds, which sorts at or after it as readers compare stamps, and one the cut cannot bound is refused."""
    assert close_out.closed_at_for([], CLOSED_AT) == CLOSED_AT
    assert close_out.closed_at_for(["2026-11-05T17:00:00.123Z"], CLOSED_AT) == CLOSED_AT
    later = "2026-11-05T18:00:07.250Z"
    assert close_out.closed_at_for([later, "2026-11-05T18:00:07Z"], CLOSED_AT) == "2026-11-05T18:00:07Z"
    assert close_out.closed_at_for([later], CLOSED_AT) >= later
    with pytest.raises(close_out.Refused, match="no close-out time in whole seconds bounds it"):
        close_out.closed_at_for(["2026-11-06, at noon"], CLOSED_AT)


@pytest.mark.parametrize("staging", ["kept", "swept", "lost"])
@pytest.mark.parametrize("killed_at", ["list", "store", "journal"])
def test_a_close_out_killed_at_a_rename_is_dropped_before_the_list_moves_and_finished_after(
    tmp_path, capsys, monkeypatch, killed_at, staging
):
    """Killed at the list's rename, the close-out changed nothing a reader sees, and the next apply drops it and the one after closes out. Killed at the store's rename, every reader refuses the store and the list on their pairing, naming the apply that finishes the close-out as the remedy, the dry run and the undo refuse on the intent, and the next apply finishes it without the readiness checks, which that pairing fails. Killed at the journal's rename, a save made before the next apply is in the journal it writes. A pass that starts before the next apply deletes the run directories under var/cycle/ (`artifact_cycle.sweep_run_dirs`), and the staging directory, outside it, outlasts that sweep. With the staging directory lost all the same, the next apply tells a close-out that renamed the list from one that did not by the list's sha256, so it still drops the second, and it refuses one that renamed the list and not the store, writing nothing, since the staged store alone could finish it."""
    fold = _folds()[1]
    store = _checkout(tmp_path, fold["before"])
    root = store.parent
    found = _files(root)
    targets = {
        "list": accepted.path_for(store),
        "store": store,
        "journal": root / journal.JOURNAL_NAME,
    }
    replace = os.replace

    def kill(source, target):
        if Path(target) == targets[killed_at]:
            raise Killed
        replace(source, target)

    monkeypatch.setattr(os, "replace", kill)
    with pytest.raises(Killed):
        _run(store, apply=True)
    monkeypatch.setattr(os, "replace", replace)
    if staging == "swept":
        for path in (root / "var" / "cycle").iterdir():
            if path.is_dir():
                shutil.rmtree(path)
    if staging == "lost":
        shutil.rmtree(close_out.staging_path_for(store))
    assert close_out.intent_path_for(store).exists()
    if killed_at == "store" and staging == "lost":
        left = _files(root)
        assert _run(store, apply=True, ready=False) == 1
        assert "its staged store is gone" in capsys.readouterr().out
        assert _files(root) == left
        return
    assert _run(store) == 1
    assert _run(store, undo=True) == 1
    assert capsys.readouterr().out.count("is left by a close-out that did not finish") == 2

    if killed_at == "list":
        assert {name: data for name, data in _files(root).items() if not name.startswith("var/")} == {
            name: data for name, data in found.items() if not name.startswith("var/")
        }
        assert _run(store, apply=True, ready=False) == 0
        assert "dropped the close-out at 2026-11-05T18:00:00Z" in capsys.readouterr().out
        assert not close_out.archive_root_for(store).exists() or not any(
            close_out.archive_root_for(store).iterdir()
        )
        assert not close_out.staging_path_for(store).exists()
    if killed_at == "store":
        with pytest.raises(accepted.Mismatch, match=r"run `make close-out ARGS=--apply`, which finishes it"):
            accepted.view_of(_document(store), accepted.read(accepted.path_for(store)), store=store)
    if killed_at == "journal":
        document = _document(store)
        document["verdicts"].append(
            {"unit": "u-Nc3wVLJaPHB", "verdict": "reject", "note": "", "at": "2026-11-05T18:30:00.000Z"}
        )
        store.write_text(json.dumps(document))
    assert _run(store, apply=True, ready=killed_at == "list") == 0
    out = capsys.readouterr().out
    assert (
        ("closed at" in out)
        if killed_at == "list"
        else ("finished the close-out at 2026-11-05T18:00:00Z" in out)
    )
    assert accepted.path_for(store).read_bytes() == accepted.render(
        CLOSED_AT, fold["after"]["list"]["verdicts"]
    )
    document = _document(store)
    accepted.pair(document, accepted.read(accepted.path_for(store)), store=store)
    assert journal.replay_store(root / journal.JOURNAL_NAME) == (
        STAMP,
        journal.latest_by_unit(document["verdicts"]),
        journal.parse_cleared(document),
    )
    assert (journal.latest_by_unit(document["verdicts"]).get("u-Nc3wVLJaPHB") is not None) == (
        killed_at == "journal"
    )
    assert not close_out.intent_path_for(store).exists()
    assert len(list(close_out.archive_root_for(store).iterdir())) == 1


@pytest.mark.parametrize("index", range(len(_folds())))
def test_the_undo_puts_back_the_list_and_lays_the_verdicts_made_since_over_the_archived_store(
    tmp_path, capsys, index
):
    """After a close-out and three verdicts saved through the review server (a reject override on a listed id, an override tombstone on another, an approve on a blank unit), which journals them and writes no store, so the undo's lock writes them into the store first, the undo puts back the list from before (none for a first close-out) and a store whose field names it and whose records and tombstones on that list's ids are its override section, so every human unit reads as before the close-out but for the three, which keep the verdicts made since; the folded notes come back; the journal replays to that store, as one, from the undo's base event; and a second undo finds nothing to undo."""
    fold = _folds()[index]
    store = _checkout(tmp_path, fold["before"])
    list_path = accepted.path_for(store)
    list_before = list_path.read_bytes() if list_path.exists() else None
    assert _run(store, apply=True) == 0
    rejected, cleared = sorted(fold["after"]["list"]["verdicts"])[:2]
    blank = next(unit for unit in HUMAN if fold["kinds"].get(unit) is None)
    _save(
        store,
        sets=[
            {"unit": rejected, "verdict": "reject", "note": "", "at": "2026-11-06T09:00:00.000Z"},
            {"unit": blank, "verdict": "approve", "note": "", "at": "2026-11-06T09:02:00.000Z"},
        ],
        clears=[{"unit": cleared, "at": "2026-11-06T09:01:00.000Z"}],
    )

    assert _run(store, undo=True) == 0
    capsys.readouterr()
    assert (list_path.read_bytes() if list_path.exists() else None) == list_before
    document = _document(store)
    assert document.get("accepted") == (accepted.read(list_path).field())
    expected = {unit: fold["kinds"].get(unit) for unit in HUMAN} | {
        rejected: "reject",
        cleared: None,
        blank: "approve",
    }
    assert _kinds(store) == expected
    notes = {record["unit"]: record["note"] for record in fold["before"]["verdicts"] if record["note"]}
    restored, tombstones = accepted.joined(
        journal.latest_by_unit(document["verdicts"]),
        journal.parse_cleared(document),
        accepted.Overrides.of(document),
    )
    assert all(
        restored[unit]["note"] == note for unit, note in notes.items() if unit not in (rejected, cleared)
    )
    listed_before = accepted.read(list_path).verdicts
    own = [*document["verdicts"], *document.get("cleared", [])]
    assert not any(entry["unit"] in listed_before for entry in own)
    assert journal.replay_store(store.parent / journal.JOURNAL_NAME) == (STAMP, restored, tombstones)
    assert not journal.unjournaled_marker_for(store).exists()
    undone = _files(store.parent)
    assert _run(store, undo=True) == 0
    assert "nothing to undo" in capsys.readouterr().out
    assert _files(store.parent) == undone


def test_a_save_the_journal_holds_past_the_stores_last_write_is_folded_or_left_and_the_new_journal_holds_it(
    tmp_path, capsys
):
    """The review server journals a save and writes the store on idle, so a close-out after a server stopped before its idle write finds the store lagging its journal. Its lock writes the journal's events past the store's last write into the store before it folds, so an accepting save folds into the list and a reject stays in the store, and the new journal's one base event holds the store it leaves, the reject among it. The dry run, which writes nothing, reports on the store as that lock leaves it, so its report is the apply's, with a note that the apply writes the saves first."""
    fold = _folds()[0]
    store = _checkout(tmp_path, fold["before"])
    blanks = [unit for unit in HUMAN if fold["kinds"].get(unit) is None]
    approved, rejected = blanks[:2]
    _save(
        store,
        sets=[
            {"unit": approved, "verdict": "approve", "note": "", "at": "2026-11-06T09:00:00.000Z"},
            {"unit": rejected, "verdict": "reject", "note": "", "at": "2026-11-06T09:01:00.000Z"},
        ],
    )
    assert approved not in json.dumps(_document(store))
    before = _files(store.parent)
    assert _run(store) == 0
    dry = capsys.readouterr().out
    assert _files(store.parent) == before
    assert "note: verdicts-journal.ndjson holds saves past the store's last write" in dry
    assert _run(store, apply=True) == 0
    applied = capsys.readouterr().out
    report = [line for line in applied.splitlines() if line.startswith("  ")]
    assert report and all(line in dry.splitlines() for line in report)
    assert accepted.read(accepted.path_for(store)).verdicts[approved] == "approve"
    assert {record["unit"]: record["verdict"] for record in _document(store)["verdicts"]}[
        rejected
    ] == "reject"
    stamp, records, _cleared = journal.replay_store(store.parent / journal.JOURNAL_NAME)
    assert stamp == STAMP and records[rejected]["verdict"] == "reject" and approved not in records
    assert [event["source"] for event in journal.iter_events(store.parent / journal.JOURNAL_NAME)] == [
        close_out.SOURCE
    ]
