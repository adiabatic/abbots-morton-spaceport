"""Tests for the land (`rebuild/review/landing.py`), which moves a pass's corpus and verdict store into place together under the store's lock beside a running review server.

The overlay keeps every set and clear the live store gained after the snapshot over the prepared store, among them a clear on a unit the snapshot held no verdict for, which lands at its own `at` over a fill, an older tombstone, or nothing in the prepared store, except on a stamp change, where it sends a set on a unit outside the new corpus's human units to the orphan document, drops a clear on such a unit, and drops a skip and clears the verdict the skip replaced; a tombstone the live store shares with the snapshot is kept only for a unit the prepared store holds no verdict for, and on a stamp change only for a surviving unit. A land whose live store moved to another stamp since the snapshot aborts and keeps the staged tree, and so does a guarded land whose stamp change would orphan more than the limit of the live store's verdicts other than skips, and a land whose live store, snapshot or prepared store holds a record the carry refuses, which writes nothing. The tree swap and its fallback where the filesystem refuses the swap call, the discard rename that never deletes, the discard a land leaves to its caller, the copy-on-write clone, the stash link, the event naming the stash, which holds the sets and clears from the replaced store until the journal's last base is a day old and a base after that, either of which replays to the landed store's tombstones too, the read of the journal resumed from retention's scan state, and the empty store a pass that carries nothing lands, which keeps the override section whole and which it journals as a base because its sets and clears would reach its record count. After a store write the journal missed (a server save whose append failed, a move onto the served corpus whose append was cut short or never ran, or a store copied back after a refused re-key undo) no lock holder replays the journal into the store, the land journals a base and removes the marker that write left, and the journal restores the live store from that land on. Saves the server journaled and did not write during the pass are written into the live store by the land's lock before the land reads it. A land killed at each step of its locked section is finished or dropped by the next holder of the lock, which first cuts off what the dead land half appended to the journal, or the sets and clears it appended whole; one whose result is gone is swapped back out, or refused when the old corpus is gone too, and one whose store or result holds a record the carry refuses is refused, by a land after it too. The review server's request path does the same while the lock is free and waits while it is held, and its locked save path refuses while a dead land's intent is there and reads the served corpus only under the lock. Every save applied through that path while two lands run, or a later save on its unit, ends in the landed store, in an orphan document, or, for a save the pass's snapshot already held on a unit the new corpus lacks, in the old corpus's stash. The code digest the server advertises on /capabilities covers exactly the protocol's modules and the modules its /status judges freshness with. Last, the accepted list beside the store: the land names it in its result and aborts, moving nothing, when the live store does not pair with it or it changed since the snapshot; its orphan guard counts the accepted units of the live corpus in both terms, so a wholesale id move is refused on a store a close-out emptied, and an override on a unit the live corpus lacks in neither; a set back to an accepted record made during the pass lands as that, not as a clear; and a stamp change leaves a tombstone on each accepted unit the new corpus lacks that the live store holds a record or tombstone for. A land whose prepared store the verdict update recorded as its snapshot's bytes leaves the store and the journal in place after every kind of save the server makes during the pass, as a land without the record over a copy of the same tree shows it would change neither, still swaps a staged corpus, and leaves a recovery only its discard; a prepared store written since, a live store the overlay would not give back, and a journal not yet there get the land's result written over the store and journaled. The snapshot and the prepared store are the live store's own file, which no writer writes through. The land holds no store whole: over random lands its walk over the stores as streams leaves every file as the land that reads them whole leaves it, but for the prepared store it renames into place as its result when the live store is the snapshot and the prepared store holds the result's bytes, which leaves the store and the journal the land that writes its result leaves but for `exported_at`, a set line it splices from the landed store is the line `json.dumps` writes, a time the walk takes is taken under the lock, and a store whose records do not stream by unit lands whole, naming itself, with the same bytes. The stream readers give `read_store`'s answer for every store a fixture holds and every store the random lands build, a block of any size at a time, and hold a few blocks of a long store.
"""

import collections
import errno
import json
import os
import random
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rebuild.review import accepted, journal, landing
from rebuild.review.serve import DELTA_FORMAT, receive_autosave_locked, write_pending_store
from rebuild.review.store_lock import holder, lock_path_for, store_lock
from rebuild.review.verdict_store import (
    ServedCorpus,
    VerdictStore,
    file_signature,
    orphan_path,
    orphans_dir_for,
    stash_path_for,
)


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
    """Write `data` to `path` through a temporary file and a rename, as every writer of the store does, so the snapshot and the prepared store, which start as the live store's file, never see the write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".write")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


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
    """On a stamp change a post-snapshot set on a unit the new corpus lacks goes to the orphan document of the old stamp, a post-snapshot skip is dropped so the unit is asked again, a post-snapshot clear on a surviving unit is kept as a tombstone, and of the tombstones the live store already held at the snapshot those on surviving units are kept."""
    corpus(repo["next"], "B", ["u-1", "u-2", "u-4", "u-5", "u-7"])
    held = {"u-5": "2026-08-01T00:00:00Z", "u-6": "2026-09-04T00:00:00Z", "u-7": "2026-09-04T00:00:00Z"}
    write(repo["autosave"], document("A", [record("u-1"), record("u-2")], held))
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
            {"u-2": "2026-09-03T00:00:00Z", **held},
        ),
    )
    result = land(repo)
    assert result.landed and (result.overlaid, result.orphaned, result.skips_dropped) == (1, 1, 1)
    landed = json.loads(repo["autosave"].read_text())
    assert landed["manifest_generated_at"] == "B"
    assert set(units(repo["autosave"])) == {"u-1"}
    assert landed["cleared"] == [
        {"unit": "u-2", "at": "2026-09-03T00:00:00Z"},
        {"unit": "u-4", "at": "2026-09-03T00:00:00Z"},
        {"unit": "u-5", "at": "2026-08-01T00:00:00Z"},
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


@pytest.mark.parametrize(
    "held, prepared",
    [
        (None, "fill"),
        ("2026-07-01T00:00:00Z", "fill"),
        (None, "nothing"),
        ("2026-07-01T00:00:00Z", "tombstone"),
    ],
)
def test_a_clear_made_during_the_pass_lands_on_a_unit_the_snapshot_held_no_verdict_for(repo, held, prepared):
    """The snapshot holds no verdict for u-3, and no tombstone or an older one, and the prepared store holds a fill's approval on u-3, nothing for it, or the snapshot's older tombstone. While the pass runs the reviewer sets a verdict on u-3 and clears it again through the server, which journals both and writes no store before the land, so the store lags its journal and the land's lock writes the two saves into it, and the live store holds only a newer tombstone for u-3. That clear is the reviewer's act after the snapshot, so whatever the prepared store holds for u-3, the land leaves no record for it and keeps the tombstone at the clear's `at`, and the journal replays to the landed store."""
    cleared = {"u-3": held} if held else None
    live = {**document("A", [record("u-1"), record("u-2")], cleared), "exported_at": "2026-01-01T00:00:00Z"}
    write(repo["autosave"], live)
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2")],
        new_cleared=cleared,
    )
    landing.snapshot(repo["autosave"], repo["run"])
    fills = [record("u-3", note="standing fill", at="2026-07-10T00:00:00Z")] if prepared == "fill" else []
    prepared_cleared = cleared if prepared == "tombstone" else None
    write(
        repo["run"] / landing.PREPARED_NAME,
        document("A", [record("u-1"), record("u-2"), *fills], prepared_cleared),
    )
    store = VerdictStore(repo["autosave"], repo["journal"])
    for change in (
        {"sets": [record("u-3", "reject", at="2026-07-11T00:00:00Z")]},
        {"clears": [{"unit": "u-3", "at": "2026-07-11T00:05:00Z"}]},
    ):
        body = json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": "A", **change}).encode()
        assert store.receive(body)[0] == 200
    assert store.pending and json.loads(repo["autosave"].read_text()) == live
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and result.overlaid == 1
    assert set(units(repo["autosave"])) == {"u-1", "u-2"}
    tombstone = {"u-3": "2026-07-11T00:05:00Z"}
    assert json.loads(repo["autosave"].read_text())["cleared"] == [{"unit": "u-3", "at": tombstone["u-3"]}]
    assert journal.replay_store(repo["journal"]) == ("A", units(repo["autosave"]), tombstone)


def test_a_tombstone_the_snapshot_already_held_keeps_out_no_verdict_the_pass_prepared(repo):
    """A tombstone the live store shares unchanged with the snapshot is no act of the reviewer's since the snapshot, so the verdict a fill prepared for its unit lands, and the tombstone stays only on a unit the prepared store holds nothing for."""
    held = {"u-3": "2026-07-01T00:00:00Z", "u-4": "2026-07-01T00:00:00Z"}
    write(repo["autosave"], document("A", [record("u-1"), record("u-2")], held))
    landing.snapshot(repo["autosave"], repo["run"])
    fill = record("u-3", note="standing fill", at="2026-07-10T00:00:00Z")
    write(repo["run"] / landing.PREPARED_NAME, document("A", [record("u-1"), record("u-2"), fill]))
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and result.overlaid == 0
    assert units(repo["autosave"])["u-3"] == fill
    assert json.loads(repo["autosave"].read_text())["cleared"] == [{"unit": "u-4", "at": held["u-4"]}]


def test_on_a_stamp_change_a_clear_made_during_the_pass_lands_only_on_a_surviving_unit(repo):
    """On a stamp change a clear the live store gained since the snapshot on a unit the snapshot held no verdict for removes the verdict the prepared store holds for it when the unit is one of the new corpus's human units, and is dropped when it is not."""
    corpus(repo["next"], "B", ["u-1", "u-2", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(
        repo["run"] / landing.PREPARED_NAME,
        document("B", [record("u-1"), record("u-2"), record("u-3", note="standing fill")]),
    )
    cleared = {"u-3": "2026-09-05T00:00:00Z", "u-4": "2026-09-05T00:00:00Z"}
    write(repo["autosave"], document("A", [record("u-1"), record("u-2")], cleared))
    result = land(repo)
    assert result.landed and (result.overlaid, result.orphaned) == (1, 0)
    assert set(units(repo["autosave"])) == {"u-1", "u-2"}
    assert json.loads(repo["autosave"].read_text())["cleared"] == [{"unit": "u-3", "at": cleared["u-3"]}]
    assert journal.replay_store(repo["journal"]) == ("B", units(repo["autosave"]), {"u-3": cleared["u-3"]})


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


@pytest.mark.parametrize("held_by", ["live store", "snapshot", "prepared store"])
def test_the_land_aborts_and_writes_nothing_when_a_store_holds_a_record_the_carry_refuses(repo, held_by):
    """A live store hand-edited after the snapshot to hold an `at` that is not a string on u-2 and no `at` on u-3 would be laid over the prepared store as `5` and `""`, so the land refuses it, as it refuses such a snapshot or prepared store, naming the store and its first such record, and every file under the root, the store, the run directory and both corpora among them, is left byte for byte as it was."""
    corpus(repo["next"], "B", ["u-1", "u-2", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1"), record("u-2")]))
    held = {
        "live store": repo["autosave"],
        "snapshot": repo["run"] / landing.SNAPSHOT_NAME,
        "prepared store": repo["run"] / landing.PREPARED_NAME,
    }[held_by]
    stamp = "B" if held_by == "prepared store" else "A"
    malformed = [record("u-1"), {**record("u-2", "reject"), "at": 5}, {"unit": "u-3", "verdict": "reject"}]
    write(held, document(stamp, malformed))
    before = {path: path.read_bytes() for path in repo["root"].rglob("*") if path.is_file()}
    result = land(repo)
    assert not result.landed
    assert result.reason.startswith(
        f"the {held_by} {held} holds a verdict record on u-2 that is not an object"
    )
    assert {path: path.read_bytes() for path in repo["root"].rglob("*") if path.is_file()} == before


MIXED_AT = [record("u-1"), {**record("u-1", "reject"), "at": 5}]


def files_under(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    ("verdicts", "named"),
    [(MIXED_AT, "on u-1"), (["u-1"], "at index 0 of its `verdicts` list")],
    ids=["mixed-at", "unit-id"],
)
def test_a_snapshot_of_a_store_holding_a_record_the_carry_refuses_is_refused(repo, verdicts, named):
    """Two records on one unit, one with an `at` that is not a string, would raise TypeError where their `at`s are compared, and a unit id in place of a record names no verdict, so the snapshot refuses either store before it compares anything, as the land does (`verdict_store.read_store_document`), naming the live store, the record and the journal restore, and leaves every file it found byte for byte as it was."""
    write(repo["autosave"], document("A", verdicts))
    before = files_under(repo["root"])
    with pytest.raises(landing.MalformedStore) as refused:
        landing.snapshot(repo["autosave"], repo["run"])
    assert str(refused.value).startswith(
        f"the live store {repo['autosave']}, linked to {repo['run'] / landing.SNAPSHOT_NAME}, holds a verdict record {named} "
        "that is not an object"
    )
    assert str(refused.value).endswith("--restore-as-of <time> --apply`")
    assert {path: path.read_bytes() for path in before} == before


def test_the_orphan_guard_aborts_a_stamp_change_past_the_limit_and_counts_no_skip(repo, monkeypatch):
    """With `guard_orphans`, a land that moves the stamp aborts before it writes anything, its result included, when more than `landing.ORPHAN_SHARE_LIMIT` of the live store's verdicts name a unit outside the new corpus's triage index, and the reason names the override; a save made during the pass, which makes the land walk the stores again under the lock, changes none of that. Once the vanished unit's verdict is a skip, nothing counts as orphaned and the same land goes ahead."""
    corpus(repo["next"], "B", ["u-1"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1")]))
    write(
        repo["autosave"], document("A", [record("u-1", "reject", at="2026-09-03T00:00:00Z"), record("u-2")])
    )
    before = repo["autosave"].read_bytes()
    results: list[Path] = []
    writer = landing._ResultWriter
    monkeypatch.setattr(
        landing, "_ResultWriter", lambda path, *rest: results.append(path) or writer(path, *rest)
    )
    result = land(repo, guard_orphans=True)
    assert not result.landed and "1 of 2" in result.reason and "--allow-orphans" in result.reason
    assert results == []
    assert repo["next"].exists() and (repo["live"] / "units-000.json").read_text() == "A"
    assert repo["autosave"].read_bytes() == before
    assert not landing.intent_path_for(repo["autosave"]).exists()
    assert not repo["journal"].exists()
    write(repo["autosave"], document("A", [record("u-1"), record("u-2", "skip", at="2026-09-02T00:00:00Z")]))
    assert land(repo, guard_orphans=True).landed


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
    """A land that moves the stamp journals the sets and clears from the store it replaced to its result, under an event naming the stash, while the journal's last base is less than a day old, and its whole result as a base once it is a day old (`journal.base_due`). The land's result says which it journaled. Replay reads the old store before the land and the landed one after it either way, the tombstone the land keeps included."""
    at = hours_ago(last_base_hours_ago)
    old = [record("u-1"), record("u-2"), record("u-3")]
    cleared = {"u-5": "2026-09-02T00:00:00Z"}
    write(repo["autosave"], document("A", old, cleared))
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=old,
        new_cleared=cleared,
        at=at,
    )
    corpus(repo["next"], "B", ["u-1", "u-2", "u-3", "u-4", "u-5"])
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
    assert journal.replay_store(repo["journal"], as_of=at) == (
        "A",
        {entry["unit"]: entry for entry in old},
        cleared,
    )
    assert json.loads(repo["autosave"].read_text())["cleared"] == [{"unit": "u-5", "at": cleared["u-5"]}]
    assert journal.replay_store(repo["journal"]) == ("B", units(repo["autosave"]), cleared)


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


def test_a_land_with_no_prepared_store_on_an_unchanged_stamp_keeps_the_live_stores_verdicts(repo):
    """A `--no-merge` or `--no-carry` pass whose corpus moves but keeps its stamp (a rune `why:` reworded, which only Stage B reads) lands with no prepared store; the live store's own records, its skips and the saves made during the pass all stay, no stash is made, and the journal replays to the landed store."""
    reject = record("u-1", "reject", at="2026-11-06T00:00:00Z")
    skipped = record("u-2", "skip", at="2026-11-06T00:10:00Z")
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[reject, skipped],
    )
    write(repo["autosave"], document("A", [reject, skipped]))
    corpus(repo["next"], "A", ["u-1", "u-2", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    during = record("u-3", at="2026-11-06T00:20:00Z")
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp="A",
        old_verdicts=[reject, skipped],
        new_verdicts=[reject, skipped, during],
    )
    write(repo["autosave"], document("A", [reject, skipped, during]))
    result = land(repo, prepared=None)
    assert result.landed
    assert units(repo["autosave"]) == {entry["unit"]: entry for entry in (reject, skipped, during)}
    assert not (repo["root"] / "verdicts-autosave-A.json").exists()
    assert journal.replay(repo["journal"]) == ("A", units(repo["autosave"]))


def test_a_land_with_no_prepared_store_lands_an_empty_store_on_the_new_stamp(repo):
    """A pass that carries nothing still lands its corpus with a store stamped for it, so saves keep working; the old store is stashed, and of the store's own records only a save made during the pass on a surviving unit is kept. The override section is kept whole, its skip becoming a tombstone at its `at`, so an accepted unit rejected or cleared since the close-out does not read as its accepted mark. No base is due, but a clear line for each unit the result drops would outnumber the result's records, so the land journals the result as a base."""
    field = write_list(repo, dict.fromkeys((ACCEPT_A, ACCEPT_B, ACCEPT_C), "approve"))
    override = record(ACCEPT_A, "reject", at="2026-11-06T00:00:00Z")
    skipped = record(ACCEPT_C, "skip", at="2026-11-06T00:10:00Z")
    tombstone = {ACCEPT_B: "2026-11-06T00:20:00Z"}
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2"), override, skipped],
        new_cleared=tombstone,
        accepted=field,
    )
    write(
        repo["autosave"],
        named(document("A", [record("u-1"), record("u-2")]), field, [override, skipped], tombstone),
    )
    corpus(repo["next"], "B", ["u-1", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(
        repo["autosave"],
        named(
            document("A", [record("u-1"), record("u-2"), record("u-3", at="2026-09-05T00:00:00Z")]),
            field,
            [override, skipped],
            tombstone,
        ),
    )
    result = land(repo, prepared=None)
    assert result.landed
    landed = json.loads(repo["autosave"].read_text())
    assert landed["manifest_generated_at"] == "B"
    assert set(units(repo["autosave"])) == {"u-3"}
    cleared = {**tombstone, ACCEPT_C: skipped["at"]}
    assert accepted.Overrides.of(landed) == accepted.Overrides({ACCEPT_A: override}, cleared)
    assert (repo["root"] / "verdicts-autosave-A.json").exists()
    assert (result.journal_base, result.journal_sets, result.journal_clears) == (True, 2, 0)
    assert journal.replay_store(repo["journal"]) == (
        "B",
        {**units(repo["autosave"]), ACCEPT_A: override},
        cleared,
    )


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


def killed_land(repo, monkeypatch, cleared=None, **where):
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
    write(repo["run"] / landing.PREPARED_NAME, document("B", [record("u-1", note="carried")], cleared))
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
    """The recovery journals the landed store as a base, its tombstones included, so it also removes the marker an earlier store write the journal missed left."""
    assert journal.mark_unjournaled(repo["autosave"], "a test")
    cleared = {"u-2": "2026-09-02T00:00:00Z"}
    length = killed_land(repo, monkeypatch, cleared, replace_onto=repo["autosave"])
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
    assert journal.replay_store(repo["journal"]) == ("B", units(repo["autosave"]), cleared)


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


@pytest.mark.parametrize("held_by", ["store", "result"])
def test_a_recovery_that_reads_two_records_on_one_unit_one_with_an_at_that_is_not_a_string_refuses_and_keeps_the_intent(
    repo, monkeypatch, held_by
):
    """A store hand-edited after a land died, read for its stamp because the land's result is gone, or a hand-edited result, holding such a pair stops the recovery with the refusal the land gives, naming the file and the record, instead of a TypeError, before it replaces the store, swaps a corpus back or discards one, or journals anything; the intent stays, so the next holder of the lock, a land among them, refuses the same way while that file holds the pair. The refusal names the way out it gives: for the store, a restore from the journal, and for the result, deleting it, after which the recovery swaps the corpus back out as for a land whose run directory is gone."""
    killed_land(repo, monkeypatch, replace_onto=repo["autosave"])
    result = repo["run"] / landing.LANDING_NAME
    if held_by == "store":
        result.unlink()
    held, name = (repo["autosave"], "the store") if held_by == "store" else (result, "the result")
    write(held, document("A" if held_by == "store" else "B", MIXED_AT))
    before = files_under(repo["root"])
    with pytest.raises(landing.LandUnrecoverable) as refused:
        landing.finish_interrupted_land(repo["autosave"])
    message = str(refused.value)
    assert refused.value.reason == "store-malformed"
    assert message.startswith(f"{name} {held} holds a verdict record on u-1 that is not an object")
    assert (
        f"{landing.intent_path_for(repo['autosave'])} stays" in message
        and f"while {held} holds such a record; " in message
    )
    restore = f"--autosave {repo['autosave']} --journal {repo['journal']} --restore-as-of"
    assert (restore in message) == (held_by == "store")
    retried = land(repo, corpus=repo["live"], staged=None, live=None)
    assert not retried.landed and retried.reason == message
    assert files_under(repo["root"]) == before
    if held_by == "result":
        assert f"deleting {held} makes the next recovery leave {repo['autosave']} as it stands" in message
        held.unlink()
        recovery = landing.finish_interrupted_land(repo["autosave"])
        assert recovery is not None and "swapped the corpus back" in recovery.message
        assert (repo["live"] / "units-000.json").read_text() == "A"


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
    """Change the live store in a way the journal does not record, as `how` names: a server save whose append fails, which writes the store at once, a save that moves the store onto the served corpus, which writes the store before its append, whose server is killed between the store write and the append or whose append is cut short partway through its lines, or a store a re-key's `--undo` asks to have copied back by hand, which the test then copies."""
    body = json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": "A", "sets": MISSED_SAVE}).encode()
    moving = json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": "A1", "sets": MISSED_SAVE}).encode()
    store = VerdictStore(repo["autosave"], repo["journal"])
    real_record_transition = journal.record_transition

    def fails(journal_path, **kwargs):
        raise OSError("disk full")

    def killed(journal_path, **kwargs):
        raise Killed()

    def cut_short(journal_path, **kwargs):
        whole = journal_path.with_name("whole.ndjson")
        shutil.copyfile(journal_path, whole)
        start = whole.stat().st_size
        real_record_transition(whole, **kwargs)
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
            patched.setattr(
                journal, "record_transition", killed if how == "killed-before-append" else cut_short
            )
            with pytest.raises(Killed):
                store.receive(moving, ServedCorpus("A1", frozenset({"u-1", "u-2", "u-3"})))
    else:
        from rebuild.tools import rekey_verdicts

        backup = repo["root"] / "var" / "keep" / "rekey" / "t"
        copy = {**document("A", [record("u-1"), *MISSED_SAVE]), "exported_at": "2026-01-01T00:00:00Z"}
        write(backup / "00-verdicts-autosave.json", copy)
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
    """The journal replays to the store as it was before a write it missed, or, for a base event cut short, to no store from it on: a server save whose append failed, a move onto the served corpus whose append was cut short or never ran because the server was killed, or a store copied back by hand after `rekey_verdicts --undo` refused. Each leaves the marker that says the journal may lack a store write, so no lock holder replays the journal into the store (#618's decision 82), the copy among them, whose `exported_at` is older than the journal's events, and the next land that moves the stamp journals its result as a base although the journal's last base is minutes old and the result holds the same records as the store it replaces, removes the marker, and from that land on `merge_verdicts --restore-as-of` gives the live store."""
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
    if how == "append-cut-short":
        with pytest.raises(journal.JournalGap):
            journal.replay(repo["journal"])
    else:
        assert journal.replay(repo["journal"])[1] != live
    corpus(repo["next"], "B", ["u-1", "u-2", "u-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    assert units(repo["run"] / landing.SNAPSHOT_NAME) == live
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
        lambda: ServedCorpus("B", frozenset({"u-1", "u-2"})),
    )
    assert status == 200 and body["corpus_stamp"] == "B" and write_pending_store(store)
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
        lambda: ServedCorpus("B", frozenset({"u-1", "u-2"})),
    )
    assert status == 503 and body["retry"] is True and body["reason"] == "land-interrupted"
    assert (repo["autosave"].read_bytes(), repo["journal"].read_bytes()) == before


def test_the_locked_save_path_reads_the_served_corpus_under_the_lock(repo):
    """A land swaps the corpus under the store's lock, so the server's locked POST path reads the served corpus only once it holds that lock. A corpus read before it could be the one a land has just replaced, and a save stamped for that corpus would carry the landed store back onto it."""
    store = VerdictStore(repo["autosave"], repo["journal"])
    reads: list[str | None] = []

    def served() -> ServedCorpus:
        reads.append(holder(lock_path_for(repo["autosave"])))
        return ServedCorpus("A", frozenset({"u-1", "u-2", "u-3"}))

    status, _body = receive_autosave_locked(
        store,
        json.dumps(
            {
                "format": DELTA_FORMAT,
                "manifest_generated_at": "A",
                "sets": [record("u-3", "reject", at="2026-09-09T00:00:00Z")],
            }
        ).encode(),
        served,
    )
    assert status == 200 and len(reads) == 1 and reads[0] is not None


def test_saves_made_while_two_lands_run_end_in_the_store_or_an_orphan_document(repo):
    """One thread saves through the server's locked POST path, as a tab loaded on the old corpus keeps doing, retrying each 503, while the main thread lands a new corpus and then a store alone. Every save the server acknowledged ends, with that `at` or a later one, in the final store or an orphan document, or, when the first snapshot already held it on a unit the new corpus lacks, in the old corpus's stash, where the carry leaves it. The server journals each save and writes no store, so each snapshot and each land takes the saves made since the store's last write from the journal when it takes the store's lock, as the lock holder after the saver stops does. The saver waits a millisecond after each save and stops at 2,000, since each save appends to the journal, and an unpaced saver makes millions of them while slow lands run on a loaded machine."""
    ids_a = [f"u-{n}" for n in range(1, 31)]
    ids_b = [f"u-{n}" for n in range(1, 21)] + [f"u-{n}" for n in range(31, 41)]
    corpus(repo["live"], "A", ids_a)
    live = document("A", [record(unit) for unit in ids_a[:10]])
    write(repo["autosave"], {**live, "exported_at": "2026-01-01T00:00:00Z"})
    corpus(repo["next"], "B", ids_b)
    store = VerdictStore(repo["autosave"], repo["journal"])

    def served() -> ServedCorpus:
        manifest = json.loads((repo["live"] / "manifest.json").read_text())
        return ServedCorpus(manifest["generated_at"], frozenset(manifest["human_unit_ids"]))

    acknowledged: list[dict] = []
    stop = threading.Event()

    def saver() -> None:
        tick = 0
        while (not stop.is_set() or tick < 40) and tick < 2_000:
            tick += 1
            unit = ids_a[tick % len(ids_a)]
            sent = record(unit, "reject", at=f"2026-09-10T00:{tick // 60:02d}:{tick % 60:02d}Z")
            body = json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": "A", "sets": [sent]}).encode()
            while True:
                status, _answer = receive_autosave_locked(store, body, served)
                if status == 200:
                    acknowledged.append(sent)
                    break
                assert status == 503
            stop.wait(0.001)

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
    with landing.locked_store(repo["autosave"]):
        pass

    final = units(repo["autosave"])
    snapshotted = units(repo["run"] / landing.SNAPSHOT_NAME)
    stashed = units(stash_path_for(repo["autosave"], "A"))
    kept: list[dict] = []
    for path in orphans_dir_for(repo["autosave"]).glob("*.json"):
        kept.extend(json.loads(path.read_text())["verdicts"])

    def holds(records: dict[str, dict], sent: dict) -> bool:
        return sent["unit"] in records and records[sent["unit"]]["at"] >= sent["at"]

    missing = [
        sent
        for sent in acknowledged
        if not holds(final, sent)
        and not any(entry["unit"] == sent["unit"] and entry["at"] >= sent["at"] for entry in kept)
        and not (sent["unit"] not in ids_b and holds(snapshotted, sent) and holds(stashed, sent))
    ]
    assert acknowledged and missing == []
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "B"


def test_the_code_digest_moves_with_a_protocol_or_freshness_module_only(tmp_path):
    """The digest the server advertises and the cycle compares covers exactly the land protocol's modules and the modules its /status judges freshness with, so an edit to one of them makes the cycle restart a server started before it, and an edit to another review module does not. A missing module hashes as empty, so the freshness modules are checked to exist in the tree."""
    rebuild_dir = Path(landing.__file__).resolve().parent.parent
    assert all((rebuild_dir / name).is_file() for name in landing.FRESHNESS_MODULES)
    review = tmp_path / "review"
    watched = [review / name for name in landing.PROTOCOL_MODULES]
    watched += [tmp_path / name for name in landing.FRESHNESS_MODULES]
    other = review / "build.py"
    for path in (*watched, other):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    base = landing.code_digest(review)
    other.write_text("x = 2\n")
    assert landing.code_digest(review) == base
    for path in watched:
        path.write_text("x = 2\n")
        assert landing.code_digest(review) != base, path
        path.write_text("x = 1\n")


def test_the_server_advertises_the_protocol_its_code_its_root_and_its_store():
    from rebuild.review import serve

    code = landing.code_digest(Path(serve.__file__).resolve().parent)
    assert serve.capabilities_payload(code) == {
        "land_protocol": landing.LAND_PROTOCOL,
        "code": code,
        "root": str(serve.REPO_ROOT),
        "autosave": str(serve.AUTOSAVE_PATH),
    }


ACCEPT_A, ACCEPT_B, ACCEPT_C = "u-1AAAAAAAAAA", "u-1BBBBBBBBBB", "u-1CCCCCCCCCC"
CLOSED_AT = "2026-11-05T18:00:00Z"


def write_list(repo, verdicts, closed_at=CLOSED_AT) -> dict:
    """Write the list beside the repo's store and return the `accepted` field that names it."""
    listed = accepted.path_for(repo["autosave"])
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_bytes(accepted.render(closed_at, verdicts))
    field = accepted.read(listed).field()
    assert field is not None
    return field


def named(data, field, records=(), cleared=None):
    """Return `data` naming the list `field`, with `records` and `cleared` (unit to the clear's `at`) as its override section when either holds one."""
    data = {**data, "accepted": field}
    if records or cleared:
        data["overrides"] = {
            "verdicts": list(records),
            "cleared": [{"unit": unit, "at": at} for unit, at in sorted((cleared or {}).items())],
        }
    return data


def test_the_land_names_the_list_in_its_result_and_moves_nothing_when_a_store_does_not_pair_with_it(repo):
    """The land writes the list's field into the store it lands. It aborts, moving nothing, when the live store does not pair with the list, as a checkout or a revert of the list leaves it, and when the snapshot or the prepared store does not, since a close-out or a re-key then changed the list after the verdict update read it."""
    field = write_list(repo, {ACCEPT_A: "approve"})
    write(repo["autosave"], named(document("A", [record("u-1")]), field))
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, named(document("A", [record("u-1"), record("u-2")]), field))
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and json.loads(repo["autosave"].read_text())["accepted"] == field

    landing.snapshot(repo["autosave"], repo["run"])
    newer = write_list(repo, {ACCEPT_B: "approve"}, closed_at="2026-11-07T00:00:00Z")
    write(repo["autosave"], named(document("A", []), newer))
    before = repo["autosave"].read_bytes()
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert not result.landed and "the accepted list changed since the pass's snapshot" in result.reason
    assert repo["autosave"].read_bytes() == before
    write(repo["autosave"], named(document("A", [record("u-1")]), field))
    before = repo["autosave"].read_bytes()
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert not result.landed and str(accepted.path_for(repo["autosave"])) in result.reason
    assert repo["autosave"].read_bytes() == before and not landing.intent_path_for(repo["autosave"]).exists()


def test_the_orphan_guard_counts_the_live_corpus_accepted_units_so_an_emptied_store_still_has_a_share(repo):
    """After a close-out the store can hold nothing, and its units are the list's. The guard counts the listed units of the live corpus's human units that the live store holds no override for in both terms, so a stamp change that moves every id is refused, naming the override, and goes ahead once the new corpus keeps the units. An override on a unit the live corpus lacks, whose window an earlier pass redrew, counts in neither term, since the override section keeps it until the next close-out and it would otherwise count as orphaned on every pass."""
    redrawn = "u-1DDDDDDDDDD"
    field = write_list(
        repo, {ACCEPT_A: "approve", ACCEPT_B: "either", ACCEPT_C: "identical", redrawn: "approve"}
    )
    corpus(repo["live"], "A", [ACCEPT_A, ACCEPT_B, ACCEPT_C])
    rejected = [record(redrawn, "reject", at="2026-11-06T00:00:00Z")]
    write(repo["autosave"], named(document("A", []), field, rejected))
    corpus(repo["next"], "B", ["u-moved-1", "u-moved-2", "u-moved-3"])
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, named(document("B", []), field, rejected))
    result = land(repo, guard_orphans=True)
    assert not result.landed and "3 of 3" in result.reason and "--allow-orphans" in result.reason
    corpus(repo["next"], "B", [ACCEPT_A, ACCEPT_B, ACCEPT_C])
    assert land(repo, guard_orphans=True).landed


def test_a_set_back_to_an_accepted_record_during_the_pass_lands_as_that_and_a_clear_stays_a_tombstone(repo):
    """The snapshot, and the prepared store made from it, hold a reject override on one accepted unit, an override tombstone on a second and nothing on a third. During the pass the reader sets the first back to its accepted record and undoes the second's clear, each of which the server stores as a removal of its override, and clears the third. The land removes the prepared store's overrides on the first two and leaves none, so both read as accepted, and lays the third's tombstone, so it reads blank, all in the override section."""
    field = write_list(repo, {ACCEPT_A: "approve", ACCEPT_B: "approve", ACCEPT_C: "approve"})
    override = record(ACCEPT_A, "reject", at="2026-11-06T00:00:00Z")
    snapshotted = named(document("A", []), field, [override], {ACCEPT_C: "2026-11-06T00:30:00Z"})
    write(repo["autosave"], snapshotted)
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, snapshotted)
    write(repo["autosave"], named(document("A", []), field, cleared={ACCEPT_B: "2026-11-06T01:00:00Z"}))
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and result.overlaid == 3
    landed = json.loads(repo["autosave"].read_text())
    assert landed["verdicts"] == [] and "cleared" not in landed
    assert landed["overrides"] == {
        "verdicts": [],
        "cleared": [{"unit": ACCEPT_B, "at": "2026-11-06T01:00:00Z"}],
    }


def test_a_stamp_change_keeps_the_override_section_whole_on_the_accepted_units_the_new_corpus_lacks(repo):
    """The new corpus lacks four accepted units. The snapshot holds a reject override on the first, an override tombstone on the second and a reject override on the fourth, and the prepared store holds the same section, which the merge keeps whole. During the pass the reader clears the third and sets the fourth back to its accepted record. The land keeps the first's reject and the second's tombstone, lays the third's clear and removes the fourth's override, so if their windows come back before the next close-out the first reads as the reject, the second and third blank, and the fourth as its accepted record."""
    gone = "u-1DDDDDDDDDD"
    field = write_list(repo, dict.fromkeys((ACCEPT_A, ACCEPT_B, ACCEPT_C, gone), "approve"))
    corpus(repo["live"], "A", [ACCEPT_A, ACCEPT_B, ACCEPT_C, gone])
    override = record(ACCEPT_A, "reject", at="2026-11-06T00:00:00Z")
    undone = record(gone, "reject", at="2026-11-06T00:10:00Z")
    tombstone = {ACCEPT_B: "2026-11-06T00:20:00Z"}
    write(repo["autosave"], named(document("A", []), field, [override, undone], tombstone))
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, named(document("B", []), field, [override, undone], tombstone))
    clear = {ACCEPT_C: "2026-11-06T00:30:00Z"}
    write(repo["autosave"], named(document("A", []), field, [override], {**tombstone, **clear}))
    corpus(repo["next"], "B", ["u-new"])
    result = land(repo)
    assert result.landed
    landed = json.loads(repo["autosave"].read_text())
    assert landed["verdicts"] == [] and "cleared" not in landed
    assert accepted.Overrides.of(landed) == accepted.Overrides({ACCEPT_A: override}, {**tombstone, **clear})


KEPT_AT = "2026-11-07T00:00:00Z"
SERVER_SAVES = {
    "nothing": [],
    "a set": [{"sets": [record("u-3", at=KEPT_AT)]}],
    "a change": [{"sets": [record("u-1", "reject", at=KEPT_AT)]}],
    "a clear": [{"clears": [{"unit": "u-2", "base_at": "2026-09-01T00:00:00Z", "at": KEPT_AT}]}],
    "a set after a clear": [
        {"clears": [{"unit": "u-2", "base_at": "2026-09-01T00:00:00Z", "at": KEPT_AT}]},
        {"sets": [record("u-2", "either", at="2026-11-07T00:10:00Z")]},
    ],
    "an override set": [{"sets": [record(ACCEPT_A, "reject", at=KEPT_AT)]}],
    "an override set and cleared": [
        {"sets": [record(ACCEPT_A, "reject", at=KEPT_AT)]},
        {"clears": [{"unit": ACCEPT_A, "base_at": KEPT_AT, "at": "2026-11-07T00:10:00Z"}]},
    ],
    "an override set back to the accepted record": [
        {"sets": [record(ACCEPT_B, "approve", at=CLOSED_AT, note="")]},
    ],
}


def unchanged_pass(repo) -> None:
    """Snapshot a store that names an accepted list and holds records, a tombstone and an override, with its journal there, and record the prepared store as the verdict update records one it left as the snapshot's bytes."""
    field = write_list(repo, {ACCEPT_A: "approve", ACCEPT_B: "approve"})
    override = record(ACCEPT_B, "reject", at="2026-11-06T00:00:00Z")
    snapshotted = named(
        document("A", [record("u-1"), record("u-2")], {"u-5": "2026-09-02T00:00:00Z"}), field, [override]
    )
    write(repo["autosave"], snapshotted)
    journal.record_transition(
        repo["journal"],
        source="merge",
        stamp="A",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[record("u-1"), record("u-2"), override],
        new_cleared={"u-5": "2026-09-02T00:00:00Z"},
        accepted=field,
    )
    landing.snapshot(repo["autosave"], repo["run"])
    landing.record_unchanged(repo["run"] / landing.PREPARED_NAME, True)


def save(repo, deltas) -> None:
    """Apply each delta through the review server's locked save path, as a tab's saves during the pass are, and then write the store as the server's idle write does."""
    store = VerdictStore(repo["autosave"], repo["journal"])
    for delta in deltas:
        body = {"format": DELTA_FORMAT, "manifest_generated_at": "A", **delta}
        status, answer = receive_autosave_locked(
            store, json.dumps(body).encode(), lambda: ServedCorpus("A", frozenset({"u-1", "u-2", "u-3"}))
        )
        assert status == 200, answer
    assert write_pending_store(store)


def held(store: landing.Store | None):
    assert store is not None
    return store.records, store.cleared, store.overrides, store.accepted


@pytest.mark.parametrize("saves", SERVER_SAVES)
def test_a_land_whose_prepared_store_is_its_snapshot_leaves_the_store_and_the_journal_in_place(repo, saves):
    """When the verdict update left the prepared store R as the snapshot S's bytes, laying the live store C's saves since S over R gives C itself for every kind of save the server makes, so the land writes no result, renames nothing over the store and appends nothing to the journal: the store keeps its inode and modification time, which the review server's reload check reads, and the land links it as the store it landed. The proof is the land without the record over a copy of the same tree, which lands C's records, tombstones, override section and `accepted` field and journals nothing."""
    unchanged_pass(repo)
    save(repo, SERVER_SAVES[saves])
    twin = repo["root"].with_name("twin")
    shutil.copytree(repo["root"], twin)
    (twin / repo["run"].relative_to(repo["root"]) / landing.UNCHANGED_NAME).unlink()
    signature = file_signature(repo["autosave"])
    journaled = repo["journal"].read_bytes()

    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and result.store_kept and result.journal_base is None
    assert file_signature(repo["autosave"]) == signature
    assert repo["journal"].read_bytes() == journaled
    assert (repo["run"] / landing.LANDED_NAME).stat().st_ino == repo["autosave"].stat().st_ino
    assert not (repo["run"] / landing.LANDING_NAME).exists()
    assert not landing.intent_path_for(repo["autosave"]).exists()

    def at(path: Path) -> Path:
        return twin / path.relative_to(repo["root"])

    today = land(
        repo,
        autosave=at(repo["autosave"]),
        journal_path=at(repo["journal"]),
        run_dir=at(repo["run"]),
        prepared=at(repo["run"] / landing.PREPARED_NAME),
        corpus=at(repo["live"]),
        staged=None,
        live=None,
        accepted_path=accepted.path_for(at(repo["autosave"])),
    )
    assert today.landed and not today.store_kept and today.overlaid == result.overlaid
    assert held(landing.read_store(at(repo["autosave"]))) == held(landing.read_store(repo["autosave"]))
    assert at(repo["journal"]).read_bytes() == journaled


def test_a_land_that_leaves_the_store_in_place_still_swaps_its_staged_corpus(repo):
    """A corpus rebuilt on the same stamp, whose verdict update changed nothing, is swapped in under the store's lock, its swapped-out tree discarded, while the store keeps its inode and modification time."""
    unchanged_pass(repo)
    corpus(repo["next"], "A", ["u-1", "u-2", "u-3"], marker="rebuilt")
    signature = file_signature(repo["autosave"])
    result = land(repo)
    assert result.landed and result.store_kept
    assert (repo["live"] / "units-000.json").read_text() == "rebuilt"
    assert not repo["next"].exists() and not landing.discard_path_for(repo["live"]).exists()
    assert file_signature(repo["autosave"]) == signature
    assert not landing.intent_path_for(repo["autosave"]).exists()


@pytest.mark.parametrize(
    "how", ["prepared store written since", "store not laid over as itself", "no journal yet"]
)
def test_a_land_that_would_change_the_store_or_its_journal_lands_its_result(repo, how):
    """The land leaves the store in place only when it would change nothing. A prepared store written after the verdict update recorded it, here with a fill, is landed with the fill. A live store that the snapshot's saves laid over R would not give back, here one a hand restore during the pass left without a record and without its tombstone, gets the land's result written over it, with a tombstone for the record that went. And with no journal yet, the land writes the result over the store and the seed base that starts the journal."""
    unchanged_pass(repo)
    prepared = repo["run"] / landing.PREPARED_NAME
    if how == "prepared store written since":
        data = json.loads(prepared.read_text())
        data["verdicts"].append(record("u-3", note="standing fill"))
        write(prepared, data)
    elif how == "store not laid over as itself":
        data = json.loads(repo["autosave"].read_text())
        data["verdicts"] = [entry for entry in data["verdicts"] if entry["unit"] != "u-2"]
        write(repo["autosave"], data)
    else:
        repo["journal"].unlink()
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and not result.store_kept
    landed = landing.read_store(repo["autosave"])
    assert landed is not None
    if how == "prepared store written since":
        assert landed.records["u-3"]["note"] == "standing fill"
    elif how == "store not laid over as itself":
        assert "u-2" not in landed.records and "u-2" in landed.cleared
    else:
        events = list(journal.iter_events(repo["journal"]))
        assert [event["source"] for event in events] == ["seed"]


def test_a_land_that_left_the_store_in_place_and_died_after_its_swap_needs_only_its_discard(
    repo, monkeypatch
):
    """A land that leaves the store in place writes its intent only to swap a staged corpus, marked `store_kept`. Killed after the swap, it is finished by the next holder of the lock, which discards the swapped-out tree and deletes the intent, and neither replaces the store nor journals it."""
    unchanged_pass(repo)
    corpus(repo["next"], "A", ["u-1", "u-2", "u-3"], marker="rebuilt")
    signature = file_signature(repo["autosave"])
    journaled = repo["journal"].read_bytes()

    def link(source, target):
        raise Killed()

    monkeypatch.setattr(landing.os, "link", link)
    with pytest.raises(Killed):
        land(repo)
    monkeypatch.undo()
    intent = json.loads(landing.intent_path_for(repo["autosave"]).read_text())
    assert intent["store_kept"] is True
    assert (repo["live"] / "units-000.json").read_text() == "rebuilt"
    with landing.locked_store(repo["autosave"]):
        pass
    assert not landing.intent_path_for(repo["autosave"]).exists()
    assert not repo["next"].exists() and not landing.discard_path_for(repo["live"]).exists()
    assert file_signature(repo["autosave"]) == signature
    assert repo["journal"].read_bytes() == journaled


WALK_NOTES = (
    "",
    "plain",
    'a "quoted" note',
    "a back\\slash",
    "a ] bracket",
    '"cleared": [',
    "two\nlines",
    "·Pea — the ’s",
    "a 🙂 face",
)
WALK_VERDICTS = ("approve", "reject", "either", "neither", "identical", "skip")
FROZEN = "2026-10-01T12:00:00Z"


def walk_record(rng, unit: str, day: int) -> dict:
    return {
        "unit": unit,
        "verdict": rng.choice(WALK_VERDICTS),
        "note": rng.choice(WALK_NOTES),
        "at": "" if rng.random() < 0.04 else f"2026-09-{day:02d}T00:00:{rng.randrange(60):02d}Z",
    }


def walk_store(rng, units, listed, day: int) -> tuple[dict, dict, accepted.Overrides]:
    """Return a store's own records, its tombstones and its override section over `units`, the section on `listed` units, and now and then an own record on a listed unit, which the journal records through the section."""
    own: dict[str, dict] = {}
    cleared: dict[str, str] = {}
    overrides = accepted.Overrides()
    for unit in units:
        roll = rng.random()
        if unit in listed and rng.random() < 0.9:
            if roll < 0.25:
                overrides.set(walk_record(rng, unit, day))
            elif roll < 0.35:
                overrides.clear(unit, f"2026-09-{day:02d}T01:00:00Z")
        elif roll < 0.55:
            own[unit] = walk_record(rng, unit, day)
        elif roll < 0.65:
            cleared[unit] = f"2026-09-{day:02d}T02:00:{rng.randrange(60):02d}Z"
    return own, cleared, overrides


def write_walk_store(rng, path: Path, stamp: str, own, cleared, overrides, field, layout: str) -> None:
    """Write a store through a temporary file and a rename in one of three layouts: the review server's, one record a line (`landing.document_bytes`); `merge_verdicts`', `indent=2`; or one line of `json.dumps` whose records may lack a note, hold a null one or carry another key, beside a tombstone on a unit that holds a record."""
    if layout == "server":
        data = landing.document_bytes(landing.Store(stamp, own, cleared, field, overrides))
    elif layout == "merge":
        payload = journal.payload_for(stamp, own, cleared, accepted=field, overrides=overrides.field())
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    else:
        verdicts = []
        for unit in sorted(own):
            entry = dict(own[unit])
            if not entry["note"] and rng.random() < 0.5:
                entry.pop("note") if rng.random() < 0.5 else entry.update(note=None)
            if rng.random() < 0.1:
                entry["configs"] = ["ss03"]
            verdicts.append(entry)
        tombstones = dict(cleared)
        if own and rng.random() < 0.3:
            tombstones[rng.choice(sorted(own))] = "2026-09-30T00:00:00Z"
        document: dict[str, object] = {
            "format": "ams-review-verdicts/1",
            "manifest_generated_at": stamp,
            "exported_at": stamp,
        }
        if field is not None:
            document["accepted"] = field
        document["verdicts"] = verdicts
        if tombstones:
            document["cleared"] = [{"unit": unit, "at": at} for unit, at in sorted(tombstones.items())]
        if (section := overrides.field()) is not None:
            document["overrides"] = section
        data = json.dumps(document).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".write")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def saved_during_the_pass(
    rng, units, listed, own, cleared, overrides
) -> tuple[dict, dict, accepted.Overrides]:
    """Return the store after a few saves made during the pass: sets, changes, skips, removals with a tombstone and without one, tombstones moved or dropped, and override sets, clears and removals."""
    own, cleared, overrides = dict(own), dict(cleared), overrides.copy()
    for _ in range(rng.randrange(1, 7)):
        unit = rng.choice(units)
        roll = rng.random()
        if unit in listed and rng.random() < 0.8:
            if roll < 0.4:
                overrides.set(walk_record(rng, unit, 20))
            elif roll < 0.7:
                overrides.clear(unit, "2026-09-20T03:00:00Z")
            else:
                overrides.drop(unit)
        elif roll < 0.35:
            own[unit] = walk_record(rng, unit, 20)
            cleared.pop(unit, None)
        elif roll < 0.5:
            own.pop(unit, None)
            cleared[unit] = "2026-09-20T04:00:00Z"
        elif roll < 0.6:
            own.pop(unit, None)
            cleared.pop(unit, None)
        elif roll < 0.7:
            cleared.pop(unit, None)
        elif roll < 0.85:
            own[unit] = {**walk_record(rng, unit, 20), "verdict": "skip"}
            cleared.pop(unit, None)
        elif unit not in own:
            cleared[unit] = "2026-09-20T05:00:00Z"
    return own, cleared, overrides


def build_walk_case(seed: int, root: Path) -> dict:
    """Build one random land under `root`: a live store on a stamp, its snapshot and prepared store linked from it, a prepared store the verdict update rewrote, left as the snapshot, deleted or never named, saves made during the pass or none, a journal with a recent base, an old one, a marker that forces a base or no journal at all, and a land that keeps the stamp, moves it with a staged corpus, or moves it on the served corpus from an older stamp."""
    rng = random.Random(seed)
    units = sorted(
        {"u-" + "".join(rng.choices(accepted.ID_ALPHABET, k=accepted.ID_SYMBOLS)) for _ in range(30)}
    )
    with_list = rng.random() < 0.7
    listed = set(rng.sample(units, 10)) if with_list else set()
    mode = rng.choice(("same", "moved", "moved", "older"))
    live_ids = units[:24]
    new_ids = sorted({*rng.sample(live_ids, 22), *rng.sample(units[24:], 3)}) if mode == "moved" else live_ids
    live = corpus(root / "rebuild" / "out" / "review", "A", live_ids)
    staged = corpus(live.with_name("review.next"), "B", new_ids, marker="new") if mode == "moved" else None
    autosave = root / "verdicts-autosave.json"
    journal_path = root / journal.JOURNAL_NAME
    run = root / "var" / "cycle" / "run-1"
    field = None
    if with_list:
        list_path = accepted.path_for(autosave)
        list_path.parent.mkdir(parents=True, exist_ok=True)
        list_path.write_bytes(accepted.render(CLOSED_AT, dict.fromkeys(sorted(listed), "approve")))
        field = accepted.read(list_path).field()
    old_stamp = "0" if mode == "older" else "A"
    new_stamp = "B" if mode == "moved" else "A"
    layouts = ("server", "merge", "hand")
    own, cleared, overrides = walk_store(rng, units, listed, 5)
    write_walk_store(rng, autosave, old_stamp, own, cleared, overrides, field, rng.choice(layouts))
    shape = rng.choice(("recent", "old", "marker", "none"))
    if shape != "none":
        records, tombstones = accepted.joined(own, cleared, overrides)
        journal.record_transition(
            journal_path,
            source="merge",
            stamp=old_stamp,
            old_stamp=None,
            new_verdicts=list(records.values()),
            new_cleared=tombstones,
            at="2026-09-29T00:00:00Z" if shape == "old" else "2026-10-01T11:00:00Z",
            accepted=field,
        )
        if shape == "marker":
            journal.mark_unjournaled(autosave, "a test")
    landing.snapshot(autosave, run)
    prepared_path = run / landing.PREPARED_NAME
    prepared = rng.choice(("rewritten", "rewritten", "unchanged", "missing", "none"))
    if prepared == "unchanged" and mode != "same":
        prepared = "rewritten"
    if prepared == "rewritten":
        carried, kept_cleared, kept_overrides = dict(own), dict(cleared), overrides
        if mode != "same":
            carried = {u: r for u, r in own.items() if u in new_ids and r["verdict"] != "skip"}
            kept_cleared = {u: at for u, at in cleared.items() if u in new_ids}
            kept_overrides = overrides.moved(FROZEN)
        for _ in range(rng.randrange(0, 8)):
            unit = rng.choice(new_ids)
            if unit not in listed and unit not in carried and unit not in kept_cleared:
                carried[unit] = {**walk_record(rng, unit, 15), "note": "standing fill"}
        if carried and rng.random() < 0.3:
            unit = rng.choice(sorted(carried))
            carried[unit] = walk_record(rng, unit, 16)
        layout = "merge" if rng.random() < 0.8 else rng.choice(layouts)
        write_walk_store(rng, prepared_path, new_stamp, carried, kept_cleared, kept_overrides, field, layout)
    elif prepared == "unchanged":
        landing.record_unchanged(prepared_path, True)
    elif prepared in ("missing", "none"):
        prepared_path.unlink()
    if rng.random() < 0.5:
        own, cleared, overrides = saved_during_the_pass(rng, units, listed, own, cleared, overrides)
        write_walk_store(rng, autosave, old_stamp, own, cleared, overrides, field, "server")
    return {
        "autosave": autosave,
        "journal_path": journal_path,
        "run_dir": run,
        "corpus": staged if staged is not None else live,
        "prepared": None if prepared == "none" else prepared_path,
        "staged": staged,
        "live": live if staged is not None else None,
        "guard_orphans": rng.random() < 0.3,
        "accepted_path": accepted.path_for(autosave),
    }


def landed_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != landing.UNCHANGED_NAME
    }


def test_the_walk_lands_the_bytes_the_stores_read_whole_land(tmp_path, monkeypatch, capsys):
    """Over random lands, the walk over the stores as streams leaves every file, the store, the journal, the orphan document, the stash and both corpora, byte for byte as the land that reads every store whole (`read_store`, `overlay`, `document_bytes`, `journal.record_transition`) leaves them over the same tree, and gives the same `LandResult`, with the clock held still so the stamps agree. Where the live store is the snapshot and the prepared store holds the bytes the land writes as its result, as the verdict update writes it, the walk renames the prepared store into place instead of writing that result, so its run directory no longer holds the prepared store, which the whole land leaves beside a store holding the same bytes. The lands cover a live store saved to during the pass, which the walk reads again under the lock, all three layouts of the prepared store, a prepared store left as the snapshot, deleted or never named, a stamp kept or moved with a staged corpus or on the served one, a journal due a base, not due one, forced to one by its marker and not there at all, so seeded, and the orphan guard on and off."""
    monkeypatch.setattr(journal, "now_stamp", lambda: FROZEN)

    def read_whole(**_options):
        raise landing._Unstreamable("the test", "reads it whole")

    seen: collections.Counter = collections.Counter()
    for seed in range(400):
        streamed, whole = tmp_path / f"{seed}-streamed", tmp_path / f"{seed}-whole"
        options, whole_options = build_walk_case(seed, streamed), build_walk_case(seed, whole)
        assert landed_files(streamed) == landed_files(whole), seed
        snapshot = options["run_dir"] / landing.SNAPSHOT_NAME
        seen["saved"] += options["autosave"].stat().st_ino != snapshot.stat().st_ino
        result = landing.land(**options)
        assert "reads the stores whole" not in capsys.readouterr().out, seed
        with monkeypatch.context() as patched:
            patched.setattr(landing, "_land_streamed", read_whole)
            expected = landing.land(**whole_options)
        assert "reads the stores whole" in capsys.readouterr().out
        ours_files, theirs_files = landed_files(streamed), landed_files(whole)
        prepared_name = str(options["run_dir"].relative_to(streamed) / landing.PREPARED_NAME)
        if prepared_name in theirs_files and prepared_name not in ours_files:
            seen["renamed"] += 1
            store_name = str(options["autosave"].relative_to(streamed))
            assert theirs_files.pop(prepared_name) == theirs_files[store_name] == ours_files[store_name], seed
        assert ours_files == theirs_files, seed
        ours, theirs = result.as_dict(), expected.as_dict()
        for held in (ours, theirs):
            held.pop("locked_s")
        assert json.dumps(ours).replace(str(streamed), "") == json.dumps(theirs).replace(str(whole), ""), seed
        seen["landed" if result.landed else "refused"] += 1
        seen["kept" if result.store_kept else "written"] += result.landed
        seen[f"base={result.journal_base}"] += result.landed
        if options["journal_path"].exists():
            events = journal.iter_events(options["journal_path"])
            seen["seeded"] += any(event["source"] == "seed" for event in events)
    covered = (
        "saved",
        "landed",
        "refused",
        "kept",
        "written",
        "renamed",
        "base=True",
        "base=False",
        "seeded",
    )
    assert all(seen[key] for key in covered), seen


@pytest.mark.parametrize("moved", [False, True])
def test_a_land_over_the_snapshot_renames_the_prepared_store_into_place_as_its_result(
    tmp_path, monkeypatch, moved
):
    """The verdict update writes the prepared store once, in the layout the land writes its result in (`rebuild.tools.held_store`). When no save landed during the pass, so the live store is the snapshot, the land renames that file over the store instead of writing its result, on a stamp kept or moved: the store takes the prepared store's inode and keeps its `exported_at`, the time the update wrote it, its bytes are those the land that writes its result leaves but for `exported_at`, and the journal gains the same lines. A save made during the pass makes the land write its result as it always has, leaving the prepared store in the run directory."""
    from rebuild.tools import held_store

    monkeypatch.setattr(journal, "now_stamp", lambda: FROZEN)
    stamp = "B" if moved else "A"

    def build(root: Path, saved: bool) -> dict:
        repo = {
            "root": root,
            "live": corpus(root / "rebuild" / "out" / "review", "A", ["u-1", "u-2", "u-3", "u-4"]),
            "autosave": root / "verdicts-autosave.json",
            "journal": root / journal.JOURNAL_NAME,
            "run": root / "var" / "cycle" / "run-1",
        }
        repo["next"] = corpus(repo["live"].with_name("review.next"), stamp, ["u-1", "u-2", "u-3", "u-4"])
        field = write_list(repo, {ACCEPT_A: "approve", ACCEPT_B: "approve"})
        sections = ([record(ACCEPT_A, "reject")], {ACCEPT_B: "2026-09-03T00:00:00Z"})
        live = named(
            document("A", [record("u-1"), record("u-2")], {"u-3": "2026-09-02T00:00:00Z"}), field, *sections
        )
        write(repo["autosave"], live)
        journal.record_transition(
            repo["journal"],
            source="merge",
            stamp="A",
            old_stamp=None,
            new_verdicts=live["verdicts"],
            accepted=field,
        )
        landing.snapshot(repo["autosave"], repo["run"])
        builder = held_store.Builder()
        for held in (record("u-1"), record("u-2", "reject", "a fill", "2026-09-04T00:00:00Z"), record("u-4")):
            builder.add(held)
        overrides = accepted.Overrides(
            {ACCEPT_A: record(ACCEPT_A, "reject")}, {ACCEPT_B: "2026-09-03T00:00:00Z"}
        )
        prepared = builder.held(stamp, field, {"u-3": "2026-09-02T00:00:00Z"}, overrides)
        prepared.write(repo["run"] / landing.PREPARED_NAME, "2026-09-05T00:00:00Z")
        if saved:
            write(
                repo["autosave"], {**live, "verdicts": [record("u-1", "either", at="2026-09-06T00:00:00Z")]}
            )
        return repo

    def unstamped(data: bytes) -> bytes:
        return re.sub(rb'"exported_at": "[^"]*"', b'"exported_at": ""', data)

    renamed, written = build(tmp_path / "renamed", False), build(tmp_path / "written", False)
    prepared = renamed["run"] / landing.PREPARED_NAME
    prepared_inode, prepared_bytes = prepared.stat().st_ino, prepared.read_bytes()
    assert land(renamed).landed
    assert not prepared.exists() and renamed["autosave"].stat().st_ino == prepared_inode
    assert renamed["autosave"].read_bytes() == prepared_bytes
    assert json.loads(prepared_bytes)["exported_at"] == "2026-09-05T00:00:00Z"
    with monkeypatch.context() as patched:
        patched.setattr(landing, "_as_result", lambda *_args: None)
        assert land(written).landed
    assert (written["run"] / landing.PREPARED_NAME).exists()
    assert unstamped(renamed["autosave"].read_bytes()) == unstamped(written["autosave"].read_bytes())
    assert renamed["journal"].read_bytes() == written["journal"].read_bytes()

    saved = build(tmp_path / "saved", True)
    prepared_inode = (saved["run"] / landing.PREPARED_NAME).stat().st_ino
    assert land(saved).landed
    assert (saved["run"] / landing.PREPARED_NAME).stat().st_ino == prepared_inode
    assert saved["autosave"].stat().st_ino != prepared_inode
    assert units(saved["autosave"])["u-1"]["verdict"] == "either"


def test_a_time_the_walk_takes_is_taken_under_the_lock(repo, monkeypatch):
    """A skip with no `at` in the override section the snapshot shares with the live store becomes a tombstone at the time of the land when the prepared store holds none for its unit and the stamp moves. The walk without the lock takes that time, so the land walks again under the lock, and the tombstone carries a time taken there, as the land that reads the stores whole takes it."""
    field = write_list(repo, {ACCEPT_A: "approve"})
    corpus(repo["next"], "B", ["u-1", "u-2"])
    write(repo["autosave"], named(document("A", [record("u-1")]), field, [record(ACCEPT_A, "skip", at="")]))
    landing.snapshot(repo["autosave"], repo["run"])
    write(repo["run"] / landing.PREPARED_NAME, named(document("B", [record("u-1")]), field))
    lock = lock_path_for(repo["autosave"])
    monkeypatch.setattr(
        journal,
        "now_stamp",
        lambda: "2026-10-01T00:00:01Z" if holder(lock) is not None else "2026-10-01T00:00:00Z",
    )
    assert land(repo).landed
    assert json.loads(repo["autosave"].read_text())["overrides"]["cleared"] == [
        {"unit": ACCEPT_A, "at": "2026-10-01T00:00:01Z"}
    ]


def test_a_spliced_set_line_is_the_one_json_dumps_writes(tmp_path):
    """The journal's set line for a record is spliced from the record's line in the store the land wrote, which holds the same fields after `"kind": "set"`, so it is the line `json.dumps(journal._set_line(record))` writes: on the last record line, which no comma follows, and on an override record's line."""
    notes = ('a "quoted" ] note', "back\\slash and\nnewline", "·Pea — ’")
    own = {f"u-{n}": record(f"u-{n}", "reject", note) for n, note in enumerate(notes)}
    override = record(ACCEPT_A, "either", '"cleared": [')
    store = landing.Store(
        "A", own, {"u-9": "2026-09-02T00:00:00Z"}, None, accepted.Overrides({ACCEPT_A: override})
    )
    lines = landing.document_bytes(store).split(b"\n")
    closing = next(index for index, line in enumerate(lines) if line.startswith(b"]"))
    last, overridden = lines[closing - 1], lines[closing + 1]
    for line, held in ((last, own["u-2"]), (overridden, override)):
        assert not line.endswith(b",")
        assert (
            landing._splice(line) == (json.dumps(journal._set_line(held), ensure_ascii=False) + "\n").encode()
        )


def test_the_snapshot_links_the_store_and_no_writer_writes_through_a_link(repo, monkeypatch):
    """The snapshot S and the prepared store R are the live store C's own file, so the review server's signature of C does not move. A save through the server replaces C by rename, leaving S and R as they were, and a merge into R replaces R the same way, leaving S and C. Where a link fails, as across filesystems, the snapshot copies instead."""
    from rebuild.tools import merge_verdicts

    signature = file_signature(repo["autosave"])
    before = repo["autosave"].read_bytes()
    landing.snapshot(repo["autosave"], repo["run"])
    snapshot, prepared = repo["run"] / landing.SNAPSHOT_NAME, repo["run"] / landing.PREPARED_NAME
    inode = repo["autosave"].stat().st_ino
    assert snapshot.stat().st_ino == prepared.stat().st_ino == inode
    assert file_signature(repo["autosave"]) == signature
    save(repo, [{"sets": [record("u-3", "reject", at="2026-09-09T00:00:00Z")]}])
    assert repo["autosave"].stat().st_ino != inode
    assert snapshot.read_bytes() == prepared.read_bytes() == before
    saved = repo["autosave"].read_bytes()
    merge_verdicts._write_store(prepared, journal.payload_for("A", {"u-4": record("u-4")}))
    assert snapshot.read_bytes() == before and repo["autosave"].read_bytes() == saved
    assert units(prepared) == {"u-4": record("u-4")}

    def refused(source, target):
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))

    copied = repo["root"] / "var" / "cycle" / "run-2"
    monkeypatch.setattr(os, "link", refused)
    assert landing.snapshot(repo["autosave"], copied) == "A"
    monkeypatch.undo()
    taken = copied / landing.SNAPSHOT_NAME
    assert taken.read_bytes() == (copied / landing.PREPARED_NAME).read_bytes() == saved
    assert (
        len(
            {
                taken.stat().st_ino,
                (copied / landing.PREPARED_NAME).stat().st_ino,
                repo["autosave"].stat().st_ino,
            }
        )
        == 3
    )


def unstreamable_case(root: Path, how: str) -> dict:
    """Build a land under `root` whose prepared store lists a unit before the unit ahead of it, whose live store names a unit twice with another between, whose snapshot names its `verdicts` list twice, or whose live store names a unit twice in a row, which streams."""
    autosave = root / "verdicts-autosave.json"
    live = corpus(root / "rebuild" / "out" / "review", "A", ["u-1", "u-2", "u-3"])
    run = root / "var" / "cycle" / "run-1"
    stored = document("A", [record("u-1"), record("u-2")], {"u-3": "2026-09-02T00:00:00Z"})
    write(autosave, stored)
    landing.snapshot(autosave, run)
    prepared = [record("u-1"), record("u-2"), record("u-3", note="standing fill")]
    if how == "out of order":
        prepared = prepared[::-1]
    write(run / landing.PREPARED_NAME, document("A", prepared))
    if how in ("apart", "in a row"):
        saves = [
            record("u-1"),
            record("u-2", "reject", at="2026-09-03T00:00:00Z"),
            record("u-2", at="2026-09-04T00:00:00Z"),
        ]
        if how == "apart":
            saves = [saves[1], saves[0], saves[2]]
        write(autosave, document("A", saves))
    elif how == "twice":
        text = json.dumps(stored)
        (run / landing.SNAPSHOT_NAME).unlink()
        (run / landing.SNAPSHOT_NAME).write_text(
            text[:-1] + ', "verdicts": ' + json.dumps(stored["verdicts"]) + "}"
        )
    return {
        "autosave": autosave,
        "journal_path": root / journal.JOURNAL_NAME,
        "run_dir": run,
        "corpus": live,
        "prepared": run / landing.PREPARED_NAME,
        "accepted_path": accepted.path_for(autosave),
    }


@pytest.mark.parametrize(
    ("how", "line"),
    [
        ("out of order", "the prepared store {run}/prepared.json lists u-2 after u-3"),
        ("apart", "the live store {root}/verdicts-autosave.json lists u-1 after u-2"),
        ("twice", "the snapshot {run}/verdicts-autosave.json names its `verdicts` list more than once"),
        ("in a row", None),
    ],
)
def test_a_store_whose_records_do_not_stream_by_unit_lands_whole_with_the_same_bytes(
    tmp_path, monkeypatch, capsys, how, line
):
    """A store whose records go out of unit order, that names a unit twice with another between, or that names its `verdicts` list twice makes the land read every store whole, after one line naming the store and why, and it lands the bytes the land that reads them whole lands. A unit named twice in a row streams, settled as `journal.latest_by_unit` settles it."""
    monkeypatch.setattr(journal, "now_stamp", lambda: FROZEN)
    streamed, whole = tmp_path / "streamed", tmp_path / "whole"
    options, whole_options = unstreamable_case(streamed, how), unstreamable_case(whole, how)
    assert landing.land(**options).landed
    printed = capsys.readouterr().out
    if line is None:
        assert "reads the stores whole" not in printed
    else:
        run = options["run_dir"]
        assert printed == f"the land reads the stores whole: {line.format(run=run, root=streamed)}\n"

    def read_whole(**_options):
        raise landing._Unstreamable("the test", "reads it whole")

    monkeypatch.setattr(landing, "_land_streamed", read_whole)
    assert landing.land(**whole_options).landed
    assert landed_files(streamed) == landed_files(whole)


FIXTURES = Path(__file__).resolve().parent / "review" / "fixtures"


def fixture_documents() -> list[dict]:
    """Every store a fixture holds, as it holds it and as an ams-review-verdicts/1 document, one document a record of the record samples, the malformed ones among them, each on its own and in the override section, and ams-review-verdicts/1 documents whose stamp is not a string or whose `verdicts` is not a list."""
    found: list[dict] = []

    def visit(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("verdicts"), list):
                found.append(node)
                found.append({"format": "ams-review-verdicts/1", "manifest_generated_at": "A", **node})
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(json.loads((FIXTURES / "accepted-samples.json").read_text(encoding="utf-8")))
    head = {"format": "ams-review-verdicts/1", "manifest_generated_at": "A", "exported_at": 12.5e3}
    found.append({**head, "numbers": [1, 2.25, -3e2, True, None], "verdicts": [record("u-0", note="🙂")]})
    found.append({**head, "verdicts": [12.5, record("u-0")]})
    unstamped = {"format": "ams-review-verdicts/1", "verdicts": [record("u-0")]}
    found += [unstamped, *({**unstamped, "manifest_generated_at": stamp} for stamp in (None, 7))]
    stamped = {"format": "ams-review-verdicts/1", "manifest_generated_at": "A"}
    found += [stamped, *({**stamped, "verdicts": verdicts} for verdicts in (None, {"u-0": record("u-0")}))]
    for sample in json.loads((FIXTURES / "verdict-record-samples.json").read_text(encoding="utf-8")):
        head = {"format": "ams-review-verdicts/1", "manifest_generated_at": "A"}
        found.append({**head, "verdicts": [record("u-0"), sample["record"]]})
        found.append({**head, "verdicts": [], "overrides": {"verdicts": [sample["record"]], "cleared": []}})
    return found


def streamed_answer(path: Path):
    """Return what the land's stream readers answer for the store at `path`, as `read_store` answers: None, the refusal, or the store, its records read through `accepted.iter_records` in file order."""
    try:
        tail = landing._tail_of(path, "the store")
    except landing.MalformedStore as malformed:
        return str(malformed)
    if tail is None:
        return None
    records = {
        unit: landing._normalize(held)
        for unit, held in journal.latest_by_unit(accepted.iter_records(path)).items()
    }
    side = landing._side_of(path, tail, "the store")
    cleared = {unit: at for unit, at in side.cleared.items() if unit not in records}
    return landing.Store(tail.stamp, records, cleared, tail.accepted, side.overrides)


def whole_answer(path: Path):
    try:
        return landing.read_store(path, "the store")
    except landing.MalformedStore as malformed:
        return str(malformed)


@pytest.mark.parametrize("block", [1, 3, 64, 1 << 20])
def test_the_stream_readers_give_the_answer_read_store_gives(tmp_path, monkeypatch, block):
    """For every store a fixture holds and every store the random lands build, in the review server's layout, `merge_verdicts`' and as UTF-16 and with a UTF-8 byte order mark, which `json.loads` reads too, the stream readers (`accepted.read_tail`, `accepted.iter_records`) give `read_store`'s answer read a block of any size at a time: the store, or the refusal naming a malformed record among its own or in its override section first, or None for a file cut short, one that is not JSON, or one that is not an ams-review-verdicts/1 document."""
    monkeypatch.setattr(accepted, "_READ_BLOCK", block)
    blobs: list[bytes] = []
    for document in fixture_documents():
        whole = json.dumps(document, ensure_ascii=False).encode()
        indented = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
        blobs += [whole, indented, whole.decode().encode("utf-16"), b"\xef\xbb\xbf" + indented]
        blobs += [indented[: len(indented) // 2], whole.replace(b"[", b"(", 1)]
    for seed in range(12):
        options = build_walk_case(seed, tmp_path / f"case-{seed}")
        for store in (options["autosave"], options["run_dir"] / landing.PREPARED_NAME):
            if store.exists():
                blobs.append(store.read_bytes())
    path = tmp_path / "store.json"
    checked = collections.Counter()
    for blob in blobs:
        path.write_bytes(blob)
        answer = whole_answer(path)
        assert streamed_answer(path) == answer, blob[:200]
        checked["store" if isinstance(answer, landing.Store) else "refused" if answer else "none"] += 1
    assert all(checked[kind] for kind in ("store", "refused", "none")), checked


def test_the_stream_readers_hold_a_few_blocks_of_a_store_many_blocks_long(tmp_path, monkeypatch):
    """A store forty times the block the readers read holds no more than a few blocks of its text at once, so neither reader ever holds the whole file."""
    monkeypatch.setattr(accepted, "_READ_BLOCK", 4096)
    records = [record(f"u-{n:06d}", note="a note " * 10) for n in range(1600)]
    path = tmp_path / "store.json"
    path.write_bytes(landing.document_bytes(landing.Store("A", {r["unit"]: r for r in records})))
    assert path.stat().st_size > 40 * 4096
    held: list[int] = []
    more = accepted._Text._more

    def counted(text) -> None:
        more(text)
        held.append(len(text.text))

    monkeypatch.setattr(accepted._Text, "_more", counted)
    tail = accepted.read_tail(path)
    assert tail is not None and tail.records == 1600
    assert sum(1 for _ in accepted.iter_records(path)) == 1600
    assert held and max(held) < 3 * 4096
