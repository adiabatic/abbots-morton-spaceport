"""Tests for `rebuild/tools/merge_verdicts.py`, which merges verdict files into the autosave outside the browser: the review app's union in which the newer `at` wins, the store's tombstones kept through an aligned merge, the stamp checks (only inputs stamped for the current corpus, a stale autosave stashed, no merge onto an outdated corpus), the store lock a merge waits on and the land a dead holder left, which it finishes first, the refusal while the review server is listening, which takes no lock, idempotence, and the restore from the journal, which gives the same store at every moment whether a land that moved the stamp was journaled as a base or as sets and clears."""

import json
import threading

import pytest

from rebuild.review import journal
from rebuild.review import store_lock as store_lock_module
from rebuild.review.store_lock import store_lock
from rebuild.tools import merge_verdicts as mv


def v(unit, verdict="approve", note="", at="2026-07-10T00:00:00Z"):
    return {"unit": unit, "verdict": verdict, "note": note, "at": at}


def doc(stamp, verdicts):
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": list(verdicts),
    }


def write_doc(path, stamp, verdicts):
    path.write_text(json.dumps(doc(stamp, verdicts)))
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(mv, "_server_listening", lambda: False)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": "S2"}))
    return {
        "root": tmp_path,
        "corpus": corpus,
        "autosave": tmp_path / "verdicts-autosave.json",
        "journal": tmp_path / "verdicts-journal.ndjson",
    }


def run(repo, *args):
    return mv.main(
        [
            *args,
            "--autosave",
            str(repo["autosave"]),
            "--corpus",
            str(repo["corpus"]),
            "--journal",
            str(repo["journal"]),
        ]
    )


def store_records(repo):
    return journal.latest_by_unit(json.loads(repo["autosave"].read_text())["verdicts"])


def test_merge_into_missing_autosave_writes_and_journals(repo, tmp_path):
    carried = write_doc(tmp_path / "verdicts-carried-x.json", "S2", [v("u-1"), v("u-2", "either")])
    assert run(repo, str(carried)) == 0
    records = store_records(repo)
    assert set(records) == {"u-1", "u-2"}
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "S2"
    stamp, replayed = journal.replay(repo["journal"])
    assert stamp == "S2"
    assert set(replayed) == {"u-1", "u-2"}


def test_an_aligned_merge_keeps_the_stores_tombstones_unless_a_newer_record_replaces_one(repo, tmp_path):
    repo["autosave"].write_text(
        json.dumps(
            {
                **doc("S2", [v("u-1")]),
                "cleared": [
                    {"unit": "u-2", "at": "2026-07-10T05:00:00Z"},
                    {"unit": "u-3", "at": "2026-07-10T05:00:00Z"},
                    {"unit": "u-4", "at": "2026-07-10T05:00:00Z"},
                ],
            }
        )
    )
    carried = write_doc(
        tmp_path / "verdicts-carried-x.json",
        "S2",
        [v("u-2", at="2026-07-10T04:00:00Z"), v("u-3", at="2026-07-10T06:00:00Z"), v("u-5")],
    )
    assert run(repo, str(carried)) == 0
    written = json.loads(repo["autosave"].read_text())
    assert set(store_records(repo)) == {"u-1", "u-3", "u-5"}
    assert written["cleared"] == [
        {"unit": "u-2", "at": "2026-07-10T05:00:00Z"},
        {"unit": "u-4", "at": "2026-07-10T05:00:00Z"},
    ]


def test_union_is_newer_at_wins(repo, tmp_path):
    write_doc(
        repo["autosave"],
        "S2",
        [v("u-1", "approve", at="2026-07-10T02:00:00Z"), v("u-2", "approve", at="2026-07-10T01:00:00Z")],
    )
    incoming = write_doc(
        tmp_path / "incoming.json",
        "S2",
        [
            v("u-1", "reject", at="2026-07-10T01:00:00Z"),
            v("u-2", "reject", at="2026-07-10T02:00:00Z"),
            v("u-3", "neither", at="2026-07-10T01:30:00Z"),
        ],
    )
    assert run(repo, str(incoming)) == 0
    records = store_records(repo)
    assert records["u-1"]["verdict"] == "approve"
    assert records["u-2"]["verdict"] == "reject"
    assert records["u-3"]["verdict"] == "neither"


def test_refuses_a_cross_stamp_input(repo, tmp_path, capsys):
    write_doc(repo["autosave"], "S2", [v("u-1")])
    before = repo["autosave"].read_text()
    stale = write_doc(tmp_path / "stale.json", "S1", [v("u-9")])
    assert run(repo, str(stale)) == 1
    assert repo["autosave"].read_text() == before
    assert not repo["journal"].exists()
    assert "carry_verdicts.py" in capsys.readouterr().out


def test_stashes_a_stale_autosave_and_starts_from_the_carried_file(repo, tmp_path):
    old = write_doc(repo["autosave"], "S1", [v("u-old")])
    old_raw = old.read_text()
    carried = write_doc(tmp_path / "verdicts-carried-y.json", "S2", [v("u-1")])
    assert run(repo, str(carried)) == 0
    assert set(store_records(repo)) == {"u-1"}
    stash = repo["root"] / "verdicts-autosave-S1.json"
    assert stash.read_text() == old_raw
    events = list(journal.iter_events(repo["journal"]))
    assert events[-1]["base"] is True
    assert events[-1]["stashed"] == "verdicts-autosave-S1.json"


def test_refuses_to_merge_onto_an_outdated_corpus(repo, tmp_path, capsys):
    write_doc(repo["autosave"], "S3", [v("u-1")])
    incoming = write_doc(tmp_path / "incoming.json", "S2", [v("u-2")])
    assert run(repo, str(incoming)) == 1
    assert "outdated corpus" in capsys.readouterr().out
    assert not repo["journal"].exists()


def test_dry_run_writes_nothing(repo, tmp_path, capsys):
    carried = write_doc(tmp_path / "carried.json", "S2", [v("u-1")])
    assert run(repo, "--dry-run", str(carried)) == 0
    assert not repo["autosave"].exists()
    assert not repo["journal"].exists()
    assert "dry run" in capsys.readouterr().out


def test_second_run_is_a_no_op(repo, tmp_path, capsys):
    carried = write_doc(tmp_path / "carried.json", "S2", [v("u-1")])
    assert run(repo, str(carried)) == 0
    first = repo["journal"].read_text()
    assert run(repo, str(carried)) == 0
    assert repo["journal"].read_text() == first
    assert "nothing changed" in capsys.readouterr().out


def test_a_merge_waits_on_the_store_lock_and_keeps_the_holders_write(repo, tmp_path, monkeypatch):
    """A merge takes the store's lock before it reads the store, so a write the holder makes before releasing it is part of the union, not overwritten."""
    carried = write_doc(tmp_path / "carried.json", "S2", [v("u-1")])
    write_doc(repo["autosave"], "S2", [v("u-0")])
    waiting = threading.Event()
    monkeypatch.setattr(store_lock_module, "announce_wait", lambda lock_path: waiting.set())
    monkeypatch.setattr(mv, "AUTOSAVE", repo["autosave"])
    codes: list[int] = []
    with store_lock(repo["autosave"]):
        merge = threading.Thread(target=lambda: codes.append(run(repo, str(carried))))
        merge.start()
        assert waiting.wait(10)
        write_doc(repo["autosave"], "S2", [v("u-0"), v("u-holder")])
    merge.join(10)
    assert codes == [0]
    assert set(store_records(repo)) == {"u-0", "u-holder", "u-1"}


def test_merge_refuses_while_the_server_is_up_without_taking_the_lock(repo, tmp_path, monkeypatch, capsys):
    """While the server listens, a merge that would write the live store refuses. It decides that without the store's lock, as a dry run and a merge with nothing to change do, so none of them makes the server refuse a save."""
    carried = write_doc(tmp_path / "carried.json", "S2", [v("u-1")])
    monkeypatch.setattr(mv, "AUTOSAVE", repo["autosave"])
    monkeypatch.setattr(mv, "_server_listening", lambda: True)

    def no_waiting(lock_path):
        raise AssertionError(f"took {lock_path}")

    monkeypatch.setattr(store_lock_module, "announce_wait", no_waiting)
    with store_lock(repo["autosave"]):
        assert run(repo, str(carried)) == 1
        assert "listening on port 7294" in capsys.readouterr().out
        assert not repo["autosave"].exists()
        assert not repo["journal"].exists()
        assert run(repo, "--dry-run", str(carried)) == 0
        assert not repo["autosave"].exists()
    assert run(repo, "--yes", str(carried)) == 0
    assert set(store_records(repo)) == {"u-1"}
    with store_lock(repo["autosave"]):
        assert run(repo, str(carried)) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_merge_to_a_scratch_store_proceeds_while_the_server_is_up(repo, tmp_path, monkeypatch, capsys):
    """The refusal guards the store the server's tabs save into, which is only the file the server serves (`mv.AUTOSAVE`). A merge into any other file runs without `--yes`, so that users do not get used to passing `--yes` where the guard matters."""
    carried = write_doc(tmp_path / "carried.json", "S2", [v("u-1")])
    monkeypatch.setattr(mv, "AUTOSAVE", tmp_path / "elsewhere" / "verdicts-autosave.json")
    monkeypatch.setattr(mv, "_server_listening", lambda: True)
    assert run(repo, str(carried)) == 0
    assert set(store_records(repo)) == {"u-1"}


def test_no_files_auto_picks_the_fullest_verdicts_file(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mv, "ROOT", tmp_path)
    write_doc(tmp_path / "verdicts-carried-z.json", "S2", [v("u-1"), v("u-2")])
    write_doc(tmp_path / "verdicts-duplicate-fill.json", "S1", [v("u-9")])
    assert run(repo) == 0
    assert set(store_records(repo)) == {"u-1", "u-2"}
    assert "auto-picked the fullest verdicts file: verdicts-carried-z.json" in capsys.readouterr().out


def test_no_files_and_no_fullest_verdicts_file_reports_the_aligned_store(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mv, "ROOT", tmp_path)
    write_doc(repo["autosave"], "S2", [v("u-1")])
    assert run(repo) == 0
    assert "nothing to merge" in capsys.readouterr().out


def test_invalid_verdict_kinds_are_skipped(repo, tmp_path, capsys):
    carried = write_doc(
        tmp_path / "carried.json", "S2", [v("u-1"), {"unit": "u-2", "verdict": "maybe", "at": "x"}]
    )
    assert run(repo, str(carried)) == 0
    assert set(store_records(repo)) == {"u-1"}
    assert "1 invalid" in capsys.readouterr().out


def seed_journal(repo):
    for hour, units in ((1, ["u-1"]), (2, ["u-1", "u-2"])):
        journal.record_transition(
            repo["journal"],
            source="autosave",
            stamp="S2",
            old_stamp="S2" if hour > 1 else None,
            old_verdicts=[v(f"u-{n}") for n in range(1, hour)],
            new_verdicts=[v(unit) for unit in units],
            at=f"2026-07-10T0{hour}:00:00Z",
        )


def test_restore_without_apply_writes_a_file(repo, capsys):
    seed_journal(repo)
    out = repo["root"] / "restored.json"
    assert run(repo, "--restore-as-of", "2026-07-10T01:30", "--out", str(out)) == 0
    data = json.loads(out.read_text())
    assert [record["unit"] for record in data["verdicts"]] == ["u-1"]
    assert data["manifest_generated_at"] == "S2"
    assert not repo["autosave"].exists()
    assert "--apply" in capsys.readouterr().out


def test_restore_refuses_a_moment_between_a_torn_base_and_the_next_complete_one(repo, capsys):
    seed_journal(repo)
    for stamp, old_stamp, units, hour in (("S3", "S2", ["u-5", "u-6"], 3), ("S4", "S3", ["u-9"], 5)):
        journal.record_transition(
            repo["journal"],
            source="merge",
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=[],
            new_verdicts=[v(unit) for unit in units],
            at=f"2026-07-10T0{hour}:00:00Z",
        )
        if stamp == "S3":
            lines = repo["journal"].read_bytes().splitlines(keepends=True)
            repo["journal"].write_bytes(b"".join(lines[:-1]))
    out = repo["root"] / "restored.json"
    assert run(repo, "--restore-as-of", "2026-07-10T04:00", "--out", str(out)) == 1
    assert "before 2026-07-10T03:00:00Z or at or after 2026-07-10T05:00:00Z" in capsys.readouterr().out
    assert not out.exists()
    assert run(repo, "--restore-as-of", "2026-07-10T05:30", "--out", str(out)) == 0
    assert [record["unit"] for record in json.loads(out.read_text())["verdicts"]] == ["u-9"]


def journal_lands(path, *, base):
    """Journal a history whose lands move the stamp, each journaled as a base (`base` None) or as the sets and clears against the store it replaced (`base` False): a merge, an autosave that changes one verdict and clears another, a land that drops a unit and fills a new one, an autosave on the new stamp, a land onto the same records, and an autosave after it."""
    first = [v("u-1"), v("u-2"), v("u-3", "reject"), v("u-6")]
    saved = [v("u-1"), v("u-2", "reject", at="2026-07-10T02:00:00Z"), v("u-6")]
    landed = [saved[1], v("u-4", note="fill"), v("u-6")]
    edited = [saved[1], v("u-4", "either", at="2026-07-10T04:00:00Z"), v("u-6")]
    for source, stamp, old_stamp, old, new, hour, land in (
        ("merge", "S1", None, [], first, 1, False),
        ("autosave", "S1", "S1", first, saved, 2, False),
        ("land", "S2", "S1", saved, landed, 3, True),
        ("autosave", "S2", "S2", landed, edited, 4, False),
        ("land", "S3", "S2", edited, edited, 5, True),
        ("autosave", "S3", "S3", edited, [*edited, v("u-5")], 6, False),
    ):
        journal.record_transition(
            path,
            source=source,
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=old,
            new_verdicts=new,
            stashed=f"verdicts-autosave-{old_stamp}.json" if land else None,
            at=f"2026-07-10T0{hour}:00:00Z",
            base=base if land else None,
        )


def test_restore_gives_the_same_store_at_every_moment_whether_lands_journal_bases_or_sets_and_clears(
    repo, capsys
):
    """A land that moves the stamp journals the sets and clears from the store it replaced unless a base is due. `--restore-as-of` gives the same store at every recorded moment, and between them, as it does from a journal where every such land wrote a base."""
    bases, deltas = repo["root"] / "bases.ndjson", repo["root"] / "deltas.ndjson"
    journal_lands(bases, base=None)
    journal_lands(deltas, base=False)
    assert [event["base"] for event in journal.iter_events(bases)] == [True, False, True, False, True, False]
    assert [event["base"] for event in journal.iter_events(deltas)] == [
        True,
        False,
        False,
        False,
        False,
        False,
    ]
    assert deltas.stat().st_size < bases.stat().st_size
    moments = [f"2026-07-10T0{hour}:{minute}" for hour in range(7) for minute in ("00", "30")]
    restored: dict[str, list[tuple]] = {}
    for moment in moments:
        restored[moment] = []
        for path in (bases, deltas):
            out = repo["root"] / f"restored-{path.stem}.json"
            out.unlink(missing_ok=True)
            code = mv.main(
                [
                    "--restore-as-of",
                    moment,
                    "--out",
                    str(out),
                    "--autosave",
                    str(repo["autosave"]),
                    "--corpus",
                    str(repo["corpus"]),
                    "--journal",
                    str(path),
                ]
            )
            data = json.loads(out.read_text()) if out.exists() else {}
            restored[moment].append((code, data.get("manifest_generated_at"), data.get("verdicts")))
        assert restored[moment][0] == restored[moment][1], moment
    capsys.readouterr()
    assert restored[moments[0]][0] == (1, None, None)
    assert restored[moments[-1]][0] == (
        0,
        "S3",
        [
            v("u-2", "reject", at="2026-07-10T02:00:00Z"),
            v("u-4", "either", at="2026-07-10T04:00:00Z"),
            v("u-5"),
            v("u-6"),
        ],
    )


def test_restore_apply_replaces_and_stashes_the_autosave(repo, monkeypatch):
    seed_journal(repo)
    write_doc(repo["autosave"], "S2", [v("u-1"), v("u-2"), v("u-3")])
    monkeypatch.setattr(mv, "_server_listening", lambda: False)
    assert run(repo, "--restore-as-of", "2026-07-10T01:30", "--apply") == 0
    assert set(store_records(repo)) == {"u-1"}
    stashes = list(repo["root"].glob("verdicts-autosave-pre-restore-*.json"))
    assert len(stashes) == 1
    assert set(journal.latest_by_unit(json.loads(stashes[0].read_text())["verdicts"])) == {
        "u-1",
        "u-2",
        "u-3",
    }
    stamp, records = journal.replay(repo["journal"])
    assert set(records) == {"u-1"}


def test_restore_apply_refuses_while_the_server_is_up(repo, monkeypatch, capsys):
    seed_journal(repo)
    monkeypatch.setattr(mv, "AUTOSAVE", repo["autosave"])
    monkeypatch.setattr(mv, "_server_listening", lambda: True)
    assert run(repo, "--restore-as-of", "2026-07-10T01:30", "--apply") == 1
    assert "Stop the server" in capsys.readouterr().out
    monkeypatch.setattr(mv, "_server_listening", lambda: False)
    assert run(repo, "--restore-as-of", "2026-07-10T01:30", "--apply", "--yes") == 0


def test_restore_before_any_event_errors(repo, capsys):
    seed_journal(repo)
    assert run(repo, "--restore-as-of", "2026-07-10T00:30") == 1
    assert "no event" in capsys.readouterr().out


def test_list_prints_the_events(repo, capsys):
    seed_journal(repo)
    assert run(repo, "--list") == 0
    out = capsys.readouterr().out
    assert out.count("autosave") == 2
    assert "stamp S2" in out


def test_a_merge_finishes_a_land_whose_holder_died_before_it_writes(repo, tmp_path):
    """A land killed after it swapped the corpus in but before it replaced the store leaves its intent file. A merge takes the store's lock through `landing.locked_store`, which finishes that land first, so the merge writes onto the landed store instead of one the recovery would then overwrite."""
    from rebuild.review import landing

    write_doc(repo["autosave"], "S1", [v("u-0")])
    run_dir = tmp_path / "var" / "cycle" / "run"
    run_dir.mkdir(parents=True)
    result = run_dir / landing.LANDING_NAME
    write_doc(result, "S2", [v("u-landed")])
    intent = landing.intent_path_for(repo["autosave"])
    intent.write_text(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "landing": str(result),
                "staged": None,
                "live": None,
                "staged_inode": None,
                "old_stamp": "S1",
                "new_stamp": "S2",
                "stash": "verdicts-autosave-S1.json",
                "journal": str(repo["journal"]),
                "journal_inode": None,
                "journal_length": None,
            }
        )
    )
    carried = write_doc(tmp_path / "carried.json", "S2", [v("u-1")])
    assert run(repo, str(carried)) == 0
    assert not intent.exists()
    assert set(store_records(repo)) == {"u-landed", "u-1"}
    assert json.loads((tmp_path / "verdicts-autosave-S1.json").read_text())["manifest_generated_at"] == "S1"
    events = list(journal.iter_events(repo["journal"]))
    assert [(event["source"], event["stashed"]) for event in events] == [
        ("land", "verdicts-autosave-S1.json"),
        ("merge", None),
    ]
