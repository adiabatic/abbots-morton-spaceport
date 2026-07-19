"""Tests for the headless verdict merge: the app's newer-at-wins union replicated outside the browser, the stamp guards (aligned inputs only, stale autosave stashed, never merge onto an outdated surface), the never-shrink invariant, idempotence, and the journal-backed restore path."""

import json

import pytest

from rebuild.review import journal
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
def repo(tmp_path):
    surface = tmp_path / "surface"
    surface.mkdir()
    (surface / "manifest.json").write_text(json.dumps({"generated_at": "S2"}))
    return {
        "root": tmp_path,
        "surface": surface,
        "autosave": tmp_path / "verdicts-autosave.json",
        "journal": tmp_path / "verdicts-journal.ndjson",
    }


def run(repo, *args):
    return mv.main(
        [
            *args,
            "--autosave",
            str(repo["autosave"]),
            "--surface",
            str(repo["surface"]),
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


def test_refuses_to_merge_onto_an_outdated_surface(repo, tmp_path, capsys):
    write_doc(repo["autosave"], "S3", [v("u-1")])
    incoming = write_doc(tmp_path / "incoming.json", "S2", [v("u-2")])
    assert run(repo, str(incoming)) == 1
    assert "outdated surface" in capsys.readouterr().out
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


def test_no_files_auto_picks_the_frontier(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mv, "ROOT", tmp_path)
    write_doc(tmp_path / "verdicts-carried-z.json", "S2", [v("u-1"), v("u-2")])
    write_doc(tmp_path / "verdicts-echo-fill.json", "S1", [v("u-9")])
    assert run(repo) == 0
    assert set(store_records(repo)) == {"u-1", "u-2"}
    assert "auto-picked the frontier file: verdicts-carried-z.json" in capsys.readouterr().out


def test_no_files_and_no_frontier_reports_the_aligned_store(repo, tmp_path, monkeypatch, capsys):
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


def test_restore_apply_replaces_and_stashes_the_autosave(repo, monkeypatch):
    seed_journal(repo)
    write_doc(repo["autosave"], "S2", [v("u-1"), v("u-2"), v("u-3")])
    monkeypatch.setattr(mv, "_server_listening", lambda: False)
    assert run(repo, "--restore-as-of", "2026-07-10T01:30", "--apply") == 0
    assert set(store_records(repo)) == {"u-1"}
    stashes = list(repo["root"].glob("verdicts-autosave-pre-restore-*.json"))
    assert len(stashes) == 1
    assert set(journal.latest_by_unit(json.loads(stashes[0].read_text())["verdicts"])) == {"u-1", "u-2", "u-3"}
    stamp, records = journal.replay(repo["journal"])
    assert set(records) == {"u-1"}


def test_restore_apply_refuses_while_the_server_is_up(repo, monkeypatch, capsys):
    seed_journal(repo)
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
