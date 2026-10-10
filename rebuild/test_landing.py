"""Tests for the land (`rebuild/review/landing.py`), which moves a pass's corpus and verdict store into place together under the store's lock beside a running review server.

The overlay keeps every set and clear the live store gained after the snapshot over the prepared store, sends a set on a unit the new corpus lacks to the orphan document, drops a skip on a stamp change and clears the verdict the skip replaced, and keeps the live store's tombstones for surviving units. A land whose live store moved to another stamp since the snapshot aborts and keeps the staged tree. The tree swap and its fallback where the filesystem refuses the swap call, the discard rename that never deletes, the discard a land leaves to its caller, the copy-on-write clone, the stash link, the event naming the stash, which holds the sets and clears from the replaced store until the journal's last base is a day old and a base after that, the read of the journal resumed from retention's scan state, and the empty store a pass that carries nothing lands, which it journals as a base because its sets and clears would reach its record count. After a store write the journal missed (a server save whose append failed, was cut short or never ran, or a store copied back after a refused re-key undo) the land journals a base and removes the marker that write left, and the journal restores the live store from that land on. A land killed at each step of its locked section is finished or dropped by the next holder of the lock, which first cuts off what the dead land half appended to the journal, or the sets and clears it appended whole; one whose result is gone is swapped back out, or refused when the old corpus is gone too. The review server's request path does the same while the lock is free and waits while it is held, and its locked save path refuses while a dead land's intent is there. Saves applied through the server's locked POST path while two lands run end either in the landed store or in an orphan document. Last, the code digest the server advertises on /capabilities covers exactly the protocol's modules.
"""

import errno
import json
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rebuild.review import journal, landing
from rebuild.review.serve import DELTA_FORMAT, receive_autosave_locked
from rebuild.review.store_lock import store_lock
from rebuild.review.verdict_store import ServedCorpus, VerdictStore, orphan_path, orphans_dir_for


def record(unit, verdict="approve", note="", at="2026-09-01T00:00:00Z"):
    return {"unit": unit, "verdict": verdict, "note": note, "at": at}


def document(stamp, records, cleared=None):
    data = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": records,
    }
    if cleared:
        data["cleared"] = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]
    return data


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def corpus(path: Path, stamp: str, ids, marker: str = "") -> Path:
    write(path / "manifest.json", {"generated_at": stamp, "human_unit_ids": sorted(ids)})
    (path / "units-000.json").write_text(marker or stamp)
    return path


def units(path: Path) -> dict[str, dict]:
    return {entry["unit"]: entry for entry in json.loads(path.read_text())["verdicts"]}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    live = corpus(root / "rebuild" / "out" / "review", "A", ["u-1", "u-2", "u-3"])
    autosave = root / "verdicts-autosave.json"
    write(autosave, document("A", [record("u-1"), record("u-2")]))
    run = root / "var" / "cycle" / "run-1"
    return {
        "root": root,
        "live": live,
        "next": live.with_name("review.next"),
        "autosave": autosave,
        "journal": root / journal.JOURNAL_NAME,
        "run": run,
    }


def land(repo, **overrides):
    options = dict(
        autosave=repo["autosave"],
        journal_path=repo["journal"],
        run_dir=repo["run"],
        corpus=repo["next"],
        prepared=repo["run"] / landing.PREPARED_NAME,
        staged=repo["next"],
        live=repo["live"],
    )
    options.update(overrides)
    return landing.land(**options)


def test_the_overlay_keeps_every_set_and_clear_made_after_the_snapshot_over_the_prepared_fills(repo):
    """A same-stamp land lays the saves the live store gained since the snapshot over the prepared store: a changed record, a new one, and a clear all beat what the fills prepared, and a unit nobody touched keeps its fill."""
    assert landing.snapshot(repo["autosave"], repo["run"]) == "A"
    write(
        repo["run"] / landing.PREPARED_NAME,
        document(
            "A",
            [
                record("u-1"),
                record("u-2"),
                record("u-3", note="standing fill"),
                record("u-4", note="standing fill"),
            ],
        ),
    )
    write(
        repo["autosave"],
        document(
            "A",
            [
                record("u-1", "reject", at="2026-09-02T00:00:00Z"),
                record("u-3", "either", at="2026-09-02T00:00:00Z"),
            ],
            {"u-2": "2026-09-02T00:00:00Z"},
        ),
    )
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and result.overlaid == 3 and result.stash is None
    landed = json.loads(repo["autosave"].read_text())
    assert {unit: entry["verdict"] for unit, entry in units(repo["autosave"]).items()} == {
        "u-1": "reject",
        "u-3": "either",
        "u-4": "approve",
    }
    assert landed["cleared"] == [{"unit": "u-2", "at": "2026-09-02T00:00:00Z"}]
    assert not landing.intent_path_for(repo["autosave"]).exists()
    events = list(journal.iter_events(repo["journal"]))
    assert [(event["source"], event["base"], event["sets"]) for event in events] == [
        ("seed", True, 2),
        ("land", False, 1),
    ]


def test_a_stamp_change_orphans_vanished_units_drops_skips_and_carries_surviving_tombstones(repo):
    """On a stamp change a post-snapshot set on a unit the new corpus lacks goes to the orphan document of the old stamp, a post-snapshot skip is dropped so the unit is asked again, a post-snapshot clear on a surviving unit is kept as a tombstone, and of the live store's other tombstones only those on surviving units and newer than the cutoff are kept."""
    corpus(repo["next"], "B", ["u-1", "u-2", "u-4", "u-5", "u-7"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1")]))
    write(
        repo["autosave"],
        document(
            "A",
            [
                record("u-1"),
                record("u-3", "reject", at="2026-09-03T00:00:00Z"),
                record("u-4", "skip", at="2026-09-03T00:00:00Z"),
            ],
            {
                "u-2": "2026-09-03T00:00:00Z",
                "u-5": "2026-08-01T00:00:00Z",
                "u-6": "2026-09-04T00:00:00Z",
                "u-7": "2026-09-04T00:00:00Z",
            },
        ),
    )
    result = land(repo, tombstone_cutoff="2026-08-15T00:00:00Z")
    assert result.landed and (result.overlaid, result.orphaned, result.skips_dropped) == (1, 1, 1)
    landed = json.loads(repo["autosave"].read_text())
    assert landed["manifest_generated_at"] == "B"
    assert set(units(repo["autosave"])) == {"u-1"}
    assert landed["cleared"] == [
        {"unit": "u-2", "at": "2026-09-03T00:00:00Z"},
        {"unit": "u-4", "at": "2026-09-03T00:00:00Z"},
        {"unit": "u-7", "at": "2026-09-04T00:00:00Z"},
    ]
    kept = json.loads(orphan_path(orphans_dir_for(repo["autosave"]), "A").read_text())
    assert [(entry["unit"], entry["reason"]) for entry in kept["verdicts"]] == [("u-3", "orphan")]


def test_a_skip_made_during_the_pass_clears_the_verdict_the_carry_brought_over(repo):
    """The snapshot holds u-1 approved, so the carry puts that approval into the prepared store; the reviewer then skips u-1 while the pass runs. On a stamp change the land drops the skip, as the carry drops every skip, and the unit must come back blank so it is asked again, not holding the approval the skip replaced. A tombstone at the skip's `at` keeps an older save from bringing the approval back."""
    corpus(repo["next"], "B", ["u-1", "u-2"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1"), record("u-2")]))
    write(
        repo["autosave"],
        document("A", [record("u-1", "skip", at="2026-09-03T00:00:00Z"), record("u-2")]),
    )
    result = land(repo)
    assert result.landed and result.skips_dropped == 1
    landed = json.loads(repo["autosave"].read_text())
    assert set(units(repo["autosave"])) == {"u-2"}
    assert landed["cleared"] == [{"unit": "u-1", "at": "2026-09-03T00:00:00Z"}]
    assert "u-1" not in journal.replay(repo["journal"])[1]
    store = VerdictStore(repo["autosave"])
    status, body = store.receive(
        json.dumps(
            {
                "format": DELTA_FORMAT,
                "manifest_generated_at": "A",
                "sets": [{**record("u-1"), "base_at": None}],
            }
        ).encode(),
        ServedCorpus("B", frozenset({"u-1", "u-2"})),
    )
    assert status == 200 and body["conflicts"] == [{"unit": "u-1", "server": None}]
    assert "u-1" not in store.records


def test_the_land_aborts_and_keeps_the_staged_tree_when_the_store_moved_to_another_stamp(repo):
    corpus(repo["next"], "B", ["u-1"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1")]))
    write(repo["autosave"], document("Z", [record("u-9")]))
    before = repo["autosave"].read_bytes()
    result = land(repo)
    assert not result.landed and "moved from A to Z" in result.reason
    assert repo["next"].exists() and (repo["live"] / "units-000.json").read_text() == "A"
    assert repo["autosave"].read_bytes() == before
    assert not landing.intent_path_for(repo["autosave"]).exists()
    assert not repo["journal"].exists()


def test_a_stamp_change_links_the_old_store_to_its_stash_and_journals_a_base_naming_it(repo):
    corpus(repo["next"], "B", ["u-1", "u-2"], marker="new")
    landing.snapshot(repo["autosave"], repo["run"])
    old_inode = repo["autosave"].stat().st_ino
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1"), record("u-2")]))
    result = land(repo)
    assert result.landed and result.stash == "verdicts-autosave-A.json"
    stash = repo["root"] / "verdicts-autosave-A.json"
    assert stash.stat().st_ino == old_inode
    assert json.loads(stash.read_text())["manifest_generated_at"] == "A"
    assert (repo["live"] / "units-000.json").read_text() == "new"
    assert not repo["next"].exists()
    assert not landing.discard_path_for(repo["live"]).exists()
    assert result.discard is None
    (event,) = journal.iter_events(repo["journal"])
    assert event["base"] is True and event["stashed"] == "verdicts-autosave-A.json" and event["stamp"] == "B"
    assert journal.replay(repo["journal"]) == ("B", units(repo["autosave"]))
    assert (repo["run"] / landing.LANDED_NAME).read_bytes() == repo["autosave"].read_bytes()


def hours_ago(hours: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(hours=hours)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("last_base_hours_ago, base", [(1, False), (24, True)])
def test_a_stamp_change_is_journaled_as_sets_and_clears_until_the_last_base_is_a_day_old(
    repo, last_base_hours_ago, base
):
    """A land that moves the stamp journals the sets and clears from the store it replaced to its result, under an event naming the stash, while the journal's last base is less than a day old, and its whole result as a base once it is a day old (`journal.base_due`). The land's result says which it journaled. Replay reads the old store before the land and the landed one after it either way."""
    at = hours_ago(last_base_hours_ago)
    old = [record("u-1"), record("u-2"), record("u-3")]
    write(repo["autosave"], document("A", old))
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=old,
        at=at,
    )
    corpus(repo["next"], "B", ["u-1", "u-2", "u-3", "u-4"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(
        repo["run"] / landing.PREPARED_NAME,
        document("B", [record("u-1"), record("u-3"), record("u-4", note="fill")]),
    )
    result = land(repo)
    assert result.landed
    sets, clears = (3, 0) if base else (1, 1)
    assert (result.journal_base, result.journal_sets, result.journal_clears) == (base, sets, clears)
    events = list(journal.iter_events(repo["journal"]))
    assert [(e["source"], e["stamp"], e["base"], e["stashed"], e["sets"], e["clears"]) for e in events] == [
        ("merge", "A", True, None, 3, 0),
        ("land", "B", base, "verdicts-autosave-A.json", sets, clears),
    ]
    assert journal.replay(repo["journal"], as_of=at) == ("A", {entry["unit"]: entry for entry in old})
    assert journal.replay(repo["journal"]) == ("B", units(repo["autosave"]))


def test_a_land_resumes_its_read_of_the_journal_from_the_saved_scan_state(repo, monkeypatch):
    """The land reads the journal's events to decide whether a base is due, once without the lock and again under it, resuming from the scan state retention saved (`--scan-state`), so it parses only what was appended since. It never writes that state."""
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2")],
    )
    state_path = repo["root"] / "var" / "cycle" / "journal-scan.json"
    state = journal.scan(repo["journal"]).state
    journal.save_scan_state(state_path, state)
    saved = state_path.read_bytes()
    corpus(repo["next"], "B", ["u-1", "u-2"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1"), record("u-2")]))
    starts: list[int] = []
    real_scan = journal.scan

    def spy(path, *, resume=None):
        scanned = real_scan(path, resume=resume)
        starts.append(scanned.start)
        return scanned

    monkeypatch.setattr(journal, "scan", spy)
    assert land(repo, scan_state=state_path).landed
    monkeypatch.undo()
    assert starts == [state.end, state.end] and state.end > 0
    assert state_path.read_bytes() == saved
    assert [(e["base"], e["sets"], e["clears"]) for e in journal.iter_events(repo["journal"])][-1] == (
        False,
        0,
        0,
    )


def test_a_land_that_keeps_its_discard_leaves_the_old_tree_for_the_caller(repo):
    """With `keep_discard` the land renames the swapped-out tree to its discard name and names it in the result, so the cycle can tell the open tabs to move before it spends seconds deleting a whole corpus."""
    corpus(repo["next"], "B", ["u-1", "u-2"], marker="new")
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1")]))
    result = land(repo, keep_discard=True)
    discard = landing.discard_path_for(repo["live"])
    assert result.landed and result.discard == str(discard)
    assert (discard / "units-000.json").read_text() == "A"
    assert (repo["live"] / "units-000.json").read_text() == "new"


def test_a_land_with_no_prepared_store_lands_an_empty_store_on_the_new_stamp(repo):
    """A pass that carries nothing still lands its corpus with a store stamped for it, so saves keep working; the old store is stashed, and only a save made during the pass on a surviving unit is kept. No base is due, but a clear line for each unit the result drops would outnumber the result's records, so the land journals the result as a base."""
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2")],
    )
    corpus(repo["next"], "B", ["u-1", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(
        repo["autosave"],
        document("A", [record("u-1"), record("u-2"), record("u-3", at="2026-09-05T00:00:00Z")]),
    )
    result = land(repo, prepared=None)
    assert result.landed
    landed = json.loads(repo["autosave"].read_text())
    assert landed["manifest_generated_at"] == "B"
    assert set(units(repo["autosave"])) == {"u-3"}
    assert (repo["root"] / "verdicts-autosave-A.json").exists()
    assert (result.journal_base, result.journal_sets, result.journal_clears) == (True, 1, 0)
    assert journal.replay(repo["journal"]) == ("B", units(repo["autosave"]))


@pytest.mark.parametrize("atomic", [True, False])
def test_exchange_dirs_swaps_two_trees(tmp_path, atomic):
    staged, live = tmp_path / "review.next", tmp_path / "review"
    corpus(staged, "B", [], marker="new")
    corpus(live, "A", [], marker="old")
    landing.exchange_dirs(staged, live, atomic=atomic)
    assert (live / "units-000.json").read_text() == "new"
    assert (staged / "units-000.json").read_text() == "old"
    assert not (tmp_path / "review.superseded").exists()


def test_the_fallback_puts_the_live_tree_back_when_the_staged_one_will_not_move(tmp_path, monkeypatch):
    staged, live = tmp_path / "review.next", tmp_path / "review"
    corpus(staged, "B", [], marker="new")
    corpus(live, "A", [], marker="old")
    real_replace = os.replace
    calls: list[tuple] = []

    def failing_second(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError("simulated")
        return real_replace(src, dst)

    monkeypatch.setattr(landing.os, "replace", failing_second)
    with pytest.raises(OSError, match="simulated"):
        landing.exchange_dirs(staged, live, atomic=False)
    assert (live / "units-000.json").read_text() == "old"
    assert (staged / "units-000.json").read_text() == "new"


def test_a_filesystem_that_refuses_the_swap_call_falls_back_to_the_renames(tmp_path, monkeypatch):
    """A swap call the filesystem does not support (ENOTSUP, EINVAL, ENOSYS) takes the three-rename fallback; any other error is raised."""
    staged, live = tmp_path / "review.next", tmp_path / "review"
    corpus(staged, "B", [], marker="new")
    corpus(live, "A", [], marker="old")

    def unsupported(a, b):
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP), str(a))

    monkeypatch.setattr(landing, "_swap_call", unsupported)
    landing.exchange_dirs(staged, live)
    assert (live / "units-000.json").read_text() == "new"
    assert (staged / "units-000.json").read_text() == "old"

    def denied(a, b):
        raise OSError(errno.EACCES, os.strerror(errno.EACCES), str(a))

    monkeypatch.setattr(landing, "_swap_call", denied)
    with pytest.raises(PermissionError):
        landing.exchange_dirs(staged, live)
    assert (live / "units-000.json").read_text() == "new"


def test_a_tree_moved_to_discard_never_replaces_a_leftover_one(tmp_path):
    """Moving a tree to discard only renames, since it can run under the store's lock: a leftover discard tree keeps its name and the new one takes the next free numbered name, and the cycle finds both (`discard_paths`)."""
    live = corpus(tmp_path / "review", "A", [])
    leftover = corpus(landing.discard_path_for(live), "old", [], marker="leftover")
    tree = corpus(tmp_path / "review.next", "B", [], marker="swapped-out")
    moved = landing.move_to_discard(tree, live)
    assert moved == tmp_path / "review.discard-1"
    assert (moved / "units-000.json").read_text() == "swapped-out"
    assert (leftover / "units-000.json").read_text() == "leftover"
    assert landing.discard_paths(live) == [leftover, moved]


def test_clone_tree_writes_never_touch_the_source(tmp_path):
    source = corpus(tmp_path / "review", "A", [], marker="served")
    (source / "sub").mkdir()
    (source / "sub" / "shard.bin").write_bytes(b"x" * 4096)
    target = tmp_path / "review.next"
    assert landing.clone_tree(source, target) in ("clone", "copy")
    (target / "units-000.json").write_text("rebuilt")
    with (target / "sub" / "shard.bin").open("r+b") as handle:
        handle.write(b"y")
    assert (source / "units-000.json").read_text() == "served"
    assert (source / "sub" / "shard.bin").read_bytes() == b"x" * 4096


def test_link_stash_is_idempotent_on_the_same_file_and_replaces_another(tmp_path):
    store = tmp_path / "verdicts-autosave.json"
    store.write_text("live")
    stash = tmp_path / "verdicts-autosave-A.json"
    stash.write_text("an older stash")
    landing.link_stash(store, stash)
    assert stash.stat().st_ino == store.stat().st_ino
    landing.link_stash(store, stash)
    assert stash.read_text() == "live"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "verdicts-autosave-A.json",
        "verdicts-autosave.json",
    ]


class Killed(BaseException):
    pass


def kill_at(monkeypatch, *, replace_onto=None, journal_append=False):
    """Make the land die at one step of its locked section: the rename onto `replace_onto`, or the journal append, which first writes a torn line as a crashed append would."""
    real_replace = os.replace

    def replace(src, dst):
        if replace_onto is not None and Path(dst) == replace_onto:
            raise Killed()
        return real_replace(src, dst)

    monkeypatch.setattr(landing.os, "replace", replace)
    if journal_append:

        def torn(journal_path, **kwargs):
            with Path(journal_path).open("ab") as handle:
                handle.write(b'{"kind": "event", "source": "land", "base": true, "sets": 2')
            raise Killed()

        monkeypatch.setattr(landing.journal, "record_transition", torn)


def killed_land(repo, monkeypatch, **where):
    corpus(repo["next"], "B", ["u-1", "u-2"], marker="new")
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2")],
    )
    length = repo["journal"].stat().st_size
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1", note="carried")]))
    kill_at(monkeypatch, **where)
    with pytest.raises(Killed):
        land(repo)
    monkeypatch.undo()
    assert landing.intent_path_for(repo["autosave"]).exists()
    return length


def test_a_land_killed_before_the_swap_is_dropped_and_leaves_everything_as_it_was(repo, monkeypatch):
    corpus(repo["next"], "B", ["u-1"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1")]))
    before = repo["autosave"].read_bytes()
    monkeypatch.setattr(landing, "exchange_dirs", lambda *a, **k: (_ for _ in ()).throw(Killed()))
    with pytest.raises(Killed):
        land(repo)
    monkeypatch.undo()
    with store_lock(repo["autosave"]):
        recovery = landing.recover_interrupted_land(repo["autosave"])
    assert recovery is not None and "dropped" in recovery.message and recovery.discard is None
    assert repo["autosave"].read_bytes() == before
    assert (repo["live"] / "units-000.json").read_text() == "A"
    assert repo["next"].exists()
    assert not landing.intent_path_for(repo["autosave"]).exists()


def test_a_land_killed_between_the_swap_and_the_store_is_finished(repo, monkeypatch):
    """The recovery journals the landed store as a base, so it also removes the marker an earlier store write the journal missed left."""
    assert journal.mark_unjournaled(repo["autosave"], "a test")
    length = killed_land(repo, monkeypatch, replace_onto=repo["autosave"])
    assert (repo["live"] / "units-000.json").read_text() == "new"
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "A"
    assert journal.unjournaled_marker_for(repo["autosave"]).exists()
    with landing.locked_store(repo["autosave"]):
        pass
    assert not landing.intent_path_for(repo["autosave"]).exists()
    assert not journal.unjournaled_marker_for(repo["autosave"]).exists()
    assert units(repo["autosave"])["u-1"]["note"] == "carried"
    assert json.loads((repo["root"] / "verdicts-autosave-A.json").read_text())["manifest_generated_at"] == "A"
    assert not repo["next"].exists()
    assert not landing.discard_path_for(repo["live"]).exists()
    lines = repo["journal"].read_bytes()[length:].decode().splitlines()
    assert json.loads(lines[0])["stashed"] == "verdicts-autosave-A.json"
    assert journal.replay(repo["journal"]) == ("B", units(repo["autosave"]))


def test_a_swapped_land_whose_result_is_gone_swaps_the_corpus_back(repo, monkeypatch):
    """A land killed after the swap whose result was deleted with its run directory cannot finish, and the store it left is still on the old stamp, so the recovery swaps the old corpus back in and drops the intent: the store and the served corpus stay on one stamp, and the new corpus waits in `review.next`. With the old corpus gone too, nothing can be put back, so the recovery refuses and keeps the intent."""
    killed_land(repo, monkeypatch, replace_onto=repo["autosave"])
    before = repo["autosave"].read_bytes()
    (repo["run"] / landing.LANDING_NAME).unlink()
    recovery = landing.finish_interrupted_land(repo["autosave"])
    assert recovery is not None and "swapped the corpus back" in recovery.message
    assert (repo["live"] / "units-000.json").read_text() == "A"
    assert (repo["next"] / "units-000.json").read_text() == "new"
    assert repo["autosave"].read_bytes() == before
    assert not landing.intent_path_for(repo["autosave"]).exists()


def test_a_swapped_land_with_neither_its_result_nor_the_old_corpus_is_refused(repo, monkeypatch):
    killed_land(repo, monkeypatch, replace_onto=repo["autosave"])
    (repo["run"] / landing.LANDING_NAME).unlink()
    shutil.rmtree(repo["next"])
    with pytest.raises(landing.LandUnrecoverable):
        landing.finish_interrupted_land(repo["autosave"])
    assert landing.intent_path_for(repo["autosave"]).exists()
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "A"


def test_a_land_killed_mid_journal_append_is_cut_back_and_journaled_again(repo, monkeypatch):
    """A land that died while appending its base event left a torn line; the recovery cuts the journal back to the length the intent recorded before it appends the event again, so replay reads the landed store."""
    length = killed_land(repo, monkeypatch, journal_append=True)
    assert repo["journal"].stat().st_size > length
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "B"
    assert landing.recover_if_orphaned(repo["autosave"]) is True
    assert not landing.intent_path_for(repo["autosave"]).exists()
    events = list(journal.iter_events(repo["journal"]))
    assert [(event["source"], event["base"], event["stashed"]) for event in events] == [
        ("merge", True, None),
        ("land", True, "verdicts-autosave-A.json"),
    ]
    assert journal.replay(repo["journal"]) == ("B", units(repo["autosave"]))


def test_a_land_killed_after_journaling_sets_and_clears_is_cut_back_to_its_event_line(repo, monkeypatch):
    """A land that journaled its stamp change as sets and clears begins its lines with its event line, at the length the intent recorded. Killed after the append, it is finished by the recovery, which cuts the journal back to that length and appends the landed store as a base. A scan state saved over the lines it cut is not resumed, because the line at the land's offset is no longer its event line."""
    corpus(repo["next"], "B", ["u-1", "u-2"], marker="new")
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2")],
    )
    length = repo["journal"].stat().st_size
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1", note="carried"), record("u-2")]))
    real_record = landing.journal.record_transition

    def append_then_die(journal_path, **kwargs):
        real_record(journal_path, **kwargs)
        raise Killed()

    monkeypatch.setattr(landing.journal, "record_transition", append_then_die)
    with pytest.raises(Killed):
        land(repo)
    monkeypatch.undo()
    appended = [json.loads(line) for line in repo["journal"].read_bytes()[length:].splitlines()]
    assert [(line["kind"], line.get("base"), line.get("unit")) for line in appended] == [
        ("event", False, None),
        ("set", None, "u-1"),
    ]
    saved = journal.scan(repo["journal"]).state
    with landing.locked_store(repo["autosave"]):
        pass
    assert not landing.intent_path_for(repo["autosave"]).exists()
    events = list(journal.iter_events(repo["journal"]))
    assert [(event["source"], event["base"], event["stashed"]) for event in events] == [
        ("merge", True, None),
        ("land", True, "verdicts-autosave-A.json"),
    ]
    assert journal.scan(repo["journal"], resume=saved).start == 0
    assert journal.replay(repo["journal"]) == ("B", units(repo["autosave"]))


MISSED_SAVE = [record("u-2", "reject", at="2026-09-02T00:00:00Z"), record("u-3", at="2026-09-02T00:00:00Z")]


def miss_a_store_write(repo, monkeypatch, capsys, how: str) -> None:
    """Change the live store in a way the journal does not record, as `how` names: a server save whose append fails, one whose server is killed between the store write and the append, one whose append is cut short partway through its lines, or a store a re-key's `--undo` asks to have copied back by hand, which the test then copies."""
    body = json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": "A", "sets": MISSED_SAVE}).encode()
    store = VerdictStore(repo["autosave"], repo["journal"])
    real_record_delta = journal.record_delta

    def fails(journal_path, **kwargs):
        raise OSError("disk full")

    def killed(journal_path, **kwargs):
        raise Killed()

    def cut_short(journal_path, **kwargs):
        whole = journal_path.with_name("whole.ndjson")
        shutil.copyfile(journal_path, whole)
        start = whole.stat().st_size
        real_record_delta(whole, **kwargs)
        lines = whole.read_bytes()[start:]
        with journal_path.open("ab") as handle:
            handle.write(lines[:-20])
        raise Killed()

    if how == "append-fails":
        with monkeypatch.context() as patched:
            patched.setattr(journal, "record_delta", fails)
            status, answer = store.receive(body)
        assert status == 200 and answer["journal_error"] == "disk full"
    elif how in ("killed-before-append", "append-cut-short"):
        with monkeypatch.context() as patched:
            patched.setattr(journal, "record_delta", killed if how == "killed-before-append" else cut_short)
            with pytest.raises(Killed):
                store.receive(body)
    else:
        from rebuild.tools import rekey_verdicts

        backup = repo["root"] / "var" / "keep" / "rekey" / "t"
        write(backup / "00-verdicts-autosave.json", document("A", [record("u-1"), *MISSED_SAVE]))
        size = repo["journal"].stat().st_size
        write(
            backup / rekey_verdicts.BACKUP_RECORD,
            {
                "files": [{"path": "verdicts-autosave.json", "backup": "00-verdicts-autosave.json"}],
                "journal": {
                    "path": journal.JOURNAL_NAME,
                    "appended": True,
                    "size": size + 1,
                    "tail_sha256": "",
                },
            },
        )
        assert rekey_verdicts.undo(repo["root"], backup) == 1
        assert "this undo left var/cycle/unjournaled.json" in capsys.readouterr().out
        shutil.copyfile(backup / "00-verdicts-autosave.json", repo["autosave"])


@pytest.mark.parametrize("how", ["append-fails", "killed-before-append", "append-cut-short", "undo-refused"])
def test_a_land_after_a_store_write_the_journal_missed_journals_a_base(repo, monkeypatch, capsys, how):
    """The journal replays to the store as it was before a write it missed: a server save whose append failed, was cut short, or never ran because the server was killed, or a store copied back by hand after `rekey_verdicts --undo` refused. Each leaves the marker that says the journal may lack a store write, so the next land that moves the stamp journals its result as a base although the journal's last base is minutes old and the result holds the same records as the store it replaces, removes the marker, and from that land on `merge_verdicts --restore-as-of` gives the live store."""
    from rebuild.tools import merge_verdicts

    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2")],
    )
    miss_a_store_write(repo, monkeypatch, capsys, how)
    marker = journal.unjournaled_marker_for(repo["autosave"])
    live = units(repo["autosave"])
    assert marker.exists() and set(live) == {"u-1", "u-2", "u-3"}
    assert journal.replay(repo["journal"])[1] != live
    corpus(repo["next"], "B", ["u-1", "u-2", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", list(live.values())))
    result = land(repo)
    assert result.landed and (result.journal_base, result.journal_sets) == (True, 3)
    assert not marker.exists()
    landed_at = [event["at"] for event in journal.iter_events(repo["journal"]) if event["source"] == "land"]
    restored = repo["root"] / "restored.json"
    argv = ["--journal", str(repo["journal"]), "--autosave", str(repo["autosave"]), "--out", str(restored)]
    assert merge_verdicts.main(["--restore-as-of", landed_at[-1], *argv]) == 0
    assert json.loads(restored.read_text())["manifest_generated_at"] == "B"
    assert units(restored) == units(repo["autosave"]) == live


def test_the_server_path_waits_while_a_land_holds_the_lock_and_finishes_a_dead_one(repo, monkeypatch):
    """`recover_if_orphaned` is what the review server runs before a request: with no intent it does nothing, while the lock is held it reports the land as still running, and once the holder is gone it finishes the land, after which a save lands on the new store."""
    assert landing.recover_if_orphaned(repo["autosave"]) is True
    killed_land(repo, monkeypatch, replace_onto=repo["autosave"])
    with store_lock(repo["autosave"]):
        assert landing.recover_if_orphaned(repo["autosave"]) is False
    assert landing.intent_path_for(repo["autosave"]).exists()
    store = VerdictStore(repo["autosave"], repo["journal"])
    assert landing.recover_if_orphaned(repo["autosave"]) is True
    status, body = receive_autosave_locked(
        store,
        json.dumps(
            {
                "format": DELTA_FORMAT,
                "manifest_generated_at": "B",
                "sets": [record("u-2", "reject", at="2026-09-09T00:00:00Z")],
            }
        ).encode(),
        ServedCorpus("B", frozenset({"u-1", "u-2"})),
    )
    assert status == 200 and body["corpus_stamp"] == "B"
    assert set(units(repo["autosave"])) == {"u-1", "u-2"}


def test_a_save_that_takes_the_lock_after_a_land_died_is_refused_until_the_land_is_finished(
    repo, monkeypatch
):
    """The server's POST path checks for a dead land's intent before it takes the lock; a land that dies between that check and the lock leaves the intent, so the locked path checks again and answers the retryable 503, and nothing is written to the old store or the journal."""
    killed_land(repo, monkeypatch, replace_onto=repo["autosave"])
    store = VerdictStore(repo["autosave"], repo["journal"])
    before = (repo["autosave"].read_bytes(), repo["journal"].read_bytes())
    status, body = receive_autosave_locked(
        store,
        json.dumps(
            {
                "format": DELTA_FORMAT,
                "manifest_generated_at": "A",
                "sets": [record("u-2", "reject", at="2026-09-09T00:00:00Z")],
            }
        ).encode(),
        ServedCorpus("B", frozenset({"u-1", "u-2"})),
    )
    assert status == 503 and body["retry"] is True and body["reason"] == "land-interrupted"
    assert (repo["autosave"].read_bytes(), repo["journal"].read_bytes()) == before


def test_saves_made_while_two_lands_run_end_in_the_store_or_an_orphan_document(repo):
    """One thread saves through the server's locked POST path, as a tab loaded on the old corpus keeps doing, retrying each 503, while the main thread lands a new corpus and then a store alone. Every save the server acknowledged ends in the final store, with that `at` or a later one, or in an orphan document."""
    ids_a = [f"u-{n}" for n in range(1, 31)]
    ids_b = [f"u-{n}" for n in range(1, 21)] + [f"u-{n}" for n in range(31, 41)]
    corpus(repo["live"], "A", ids_a)
    write(repo["autosave"], document("A", [record(unit) for unit in ids_a[:10]]))
    corpus(repo["next"], "B", ids_b)
    store = VerdictStore(repo["autosave"], repo["journal"])

    def served() -> ServedCorpus:
        manifest = json.loads((repo["live"] / "manifest.json").read_text())
        return ServedCorpus(manifest["generated_at"], frozenset(manifest["human_unit_ids"]))

    acknowledged: list[dict] = []
    stop = threading.Event()

    def saver() -> None:
        tick = 0
        while not stop.is_set() or tick < 40:
            tick += 1
            unit = ids_a[tick % len(ids_a)]
            sent = record(unit, "reject", at=f"2026-09-10T00:{tick // 60:02d}:{tick % 60:02d}Z")
            body = json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": "A", "sets": [sent]}).encode()
            while True:
                status, _answer = receive_autosave_locked(store, body, served())
                if status == 200:
                    acknowledged.append(sent)
                    break
                assert status == 503

    thread = threading.Thread(target=saver)
    thread.start()
    first = landing.snapshot(repo["autosave"], repo["run"])
    assert first == "A"
    carried = [
        entry for entry in units(repo["run"] / landing.SNAPSHOT_NAME).values() if entry["unit"] in ids_b
    ]
    write(repo["run"] / landing.PREPARED_NAME, document("B", carried))
    assert land(repo).landed
    second = repo["root"] / "var" / "cycle" / "run-2"
    landing.snapshot(repo["autosave"], second)
    assert land(
        repo,
        run_dir=second,
        prepared=second / landing.PREPARED_NAME,
        corpus=repo["live"],
        staged=None,
        live=None,
    ).landed
    stop.set()
    thread.join(30)
    assert not thread.is_alive()

    final = units(repo["autosave"])
    kept: list[dict] = []
    for path in orphans_dir_for(repo["autosave"]).glob("*.json"):
        kept.extend(json.loads(path.read_text())["verdicts"])
    missing = [
        sent
        for sent in acknowledged
        if not (sent["unit"] in final and final[sent["unit"]]["at"] >= sent["at"])
        and not any(entry["unit"] == sent["unit"] and entry["at"] == sent["at"] for entry in kept)
    ]
    assert acknowledged and missing == []
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "B"


def test_the_code_digest_moves_with_a_protocol_module_only(tmp_path):
    """The digest the server advertises and the cycle compares covers exactly the land protocol's modules, so an edit to one of them makes the cycle restart a server started before it, and an edit to another review module does not."""
    for name in (*landing.PROTOCOL_MODULES, "build.py"):
        (tmp_path / name).write_text("x = 1\n")
    base = landing.code_digest(tmp_path)
    (tmp_path / "build.py").write_text("x = 2\n")
    assert landing.code_digest(tmp_path) == base
    for name in landing.PROTOCOL_MODULES:
        (tmp_path / name).write_text("x = 2\n")
        assert landing.code_digest(tmp_path) != base, name
        (tmp_path / name).write_text("x = 1\n")


def test_the_server_advertises_the_protocol_its_code_its_root_and_its_store():
    from rebuild.review import serve

    code = landing.code_digest(Path(serve.__file__).resolve().parent)
    assert serve.capabilities_payload(code) == {
        "land_protocol": landing.LAND_PROTOCOL,
        "code": code,
        "root": str(serve.REPO_ROOT),
        "autosave": str(serve.AUTOSAVE_PATH),
    }
