"""Tests for the one-shot re-key of verdicts from the unit ids the corpus had before the #357 renames to the ids it has after them. The shipped review fixtures are a post-rename corpus, and the pre-rename ids the test records verdicts on are the ids those fixtures carried before the renames, so the test checks the reversed projection against the ids the old code derived. The fixture ledger's `fixture-drift` class became `fixture-mismatch` in the same batch, a rename the tool's tables leave out because no real class has it, so those units stand for a missed rename."""

import json
from pathlib import Path

from rebuild.review import journal
from rebuild.tools import merge_verdicts, rekey_verdicts

FIXTURE_CORPUS = Path(__file__).resolve().parent / "review" / "fixtures"
STAMP = "2026-09-26T17:49:41Z"
PRE_TO_POST = {
    "u-2WvdGAWe6bX": "u-WTMwXA3Hvr2",
    "u-8nacGTcgMRS": "u-dy33xBw4wVZ",
    "u-DdcTojn1hba": "u-6wxep7mamL7",
    "u-hRgMc2EJjbs": "u-E8egK4dqX5U",
}
MISSED = "u-Ng8Npb18Kha"


def _verdicts(path: Path, stamp: str, units: list[str]) -> None:
    records = [
        {"unit": unit, "verdict": "approve", "note": "", "at": "2026-09-01T00:00:00Z"} for unit in units
    ]
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": records,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _units(path: Path) -> list[str]:
    return [record["unit"] for record in json.loads(path.read_text(encoding="utf-8"))["verdicts"]]


def test_rekey_moves_pre_rename_ids_once_and_undoes(tmp_path, capsys):
    """A stamp-aligned store naming a unit whose rename the tables miss blocks every write. Accepted with --allow-unmatched, pre-rename ids move to their units' post-rename ids in the autosave, a stamp-aligned carried file and a referencing text file; a current id, the missed unit and a positional id stay. Renaming a kind keeps the kinds sorted in both directions. A second run writes nothing, the id map re-keys a journal replay from before the re-key, and the backup restores every file and the journal."""
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    _verdicts(autosave, STAMP, [*PRE_TO_POST, "u-fDT3GBdycaj", MISSED, "u-10000"])
    _verdicts(tmp_path / "verdicts-carried-abc1234.json", STAMP, ["u-2WvdGAWe6bX"])
    _verdicts(tmp_path / "verdicts-carried-0000000.json", "2026-09-01T00:00:00Z", ["u-2WvdGAWe6bX"])
    notes = tmp_path / "standing-approvals.yaml"
    notes.write_text("note: approved at u-hRgMc2EJjbs\n", encoding="utf-8")
    seeded = json.loads(autosave.read_text(encoding="utf-8"))["verdicts"]
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=seeded,
        at="2000-01-01T00:00:00Z",
    )
    before = {path.name: path.read_bytes() for path in (autosave, journal_path, notes)}
    argv = ["--root", str(tmp_path), "--corpus", str(FIXTURE_CORPUS), "--references", str(notes)]

    assert rekey_verdicts.main(argv) == 1
    assert (
        f"1 of its content ids match no unit before or after the renames: {MISSED}" in capsys.readouterr().out
    )
    assert {path.name: path.read_bytes() for path in (autosave, journal_path, notes)} == before
    assert not (tmp_path / rekey_verdicts.KEEP).exists()

    argv.append("--allow-unmatched")
    assert rekey_verdicts.main(argv) == 0
    assert "rekey counts: seen=8 rekeyed=5 current=1 unmatched=2" in capsys.readouterr().out
    assert _units(autosave) == sorted([*PRE_TO_POST.values(), "u-fDT3GBdycaj", MISSED, "u-10000"])
    assert _units(tmp_path / "verdicts-carried-abc1234.json") == ["u-WTMwXA3Hvr2"]
    assert _units(tmp_path / "verdicts-carried-0000000.json") == ["u-2WvdGAWe6bX"]
    assert notes.read_text(encoding="utf-8") == "note: approved at u-E8egK4dqX5U\n"
    assert journal.replay(journal_path)[1].keys() == set(_units(autosave))
    id_map = json.loads(
        (tmp_path / rekey_verdicts.KEEP / rekey_verdicts.MAP_NAME).read_text(encoding="utf-8")
    )
    assert PRE_TO_POST.items() <= id_map["renamed"].items() and MISSED not in id_map["renamed"]
    for pre, post in (
        (["ligation", "seam"], ["junction", "ligation"]),
        (["cell", "position", "seam"], ["cell", "junction", "position"]),
    ):
        assert rekey_verdicts.FORWARD.projection({"kinds": pre})["kinds"] == post
        assert rekey_verdicts.REVERSE.projection({"kinds": post})["kinds"] == pre

    rekeyed = {path.name: path.read_bytes() for path in (autosave, journal_path, notes)}
    assert rekey_verdicts.main(argv) == 0
    assert "rekey counts: seen=8 rekeyed=0 current=6 unmatched=2" in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in (autosave, journal_path, notes)} == rekeyed

    restored = tmp_path / "restored.json"
    map_path = tmp_path / rekey_verdicts.KEEP / rekey_verdicts.MAP_NAME
    assert (
        merge_verdicts.main(
            [
                "--restore-as-of",
                "2000-01-02",
                "--journal",
                str(journal_path),
                "--autosave",
                str(autosave),
                "--out",
                str(restored),
                "--rekey-map",
                str(map_path),
            ]
        )
        == 0
    )
    assert _units(restored) == _units(autosave)

    (backup,) = [path for path in (tmp_path / rekey_verdicts.KEEP).iterdir() if path.is_dir()]
    assert rekey_verdicts.main(["--root", str(tmp_path), "--undo", str(backup)]) == 0
    assert {path.name: path.read_bytes() for path in (autosave, journal_path, notes)} == before


def test_a_later_rekey_merges_into_the_id_map_and_copies_the_previous_one(tmp_path, capsys):
    """A run on a corpus other than the one the id map was written for keeps every pair already in the map, adds its own, lets its own pair win a clash, and copies the previous map beside it first. A journal restore from before either re-key then moves both runs' ids through the one map."""
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    _verdicts(autosave, STAMP, list(PRE_TO_POST))
    earlier_old, earlier_new, clashing = "u-3333333333A", "u-4444444444B", "u-5555555555C"
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[
            {"unit": unit, "verdict": "approve", "note": "", "at": "2000-01-01T00:00:00Z"}
            for unit in [*PRE_TO_POST, earlier_old]
        ],
        at="2000-01-01T00:00:00Z",
    )
    keep = tmp_path / rekey_verdicts.KEEP
    keep.mkdir(parents=True)
    map_path = keep / rekey_verdicts.MAP_NAME
    earlier = {
        "format": rekey_verdicts.MAP_FORMAT,
        "corpus": "2026-09-01T00:00:00Z",
        "renamed": {earlier_old: earlier_new, "u-2WvdGAWe6bX": clashing},
    }
    map_path.write_text(json.dumps(earlier), encoding="utf-8")

    assert rekey_verdicts.main(["--root", str(tmp_path), "--corpus", str(FIXTURE_CORPUS)]) == 0
    out = capsys.readouterr().out
    assert "1 pre-rename id(s) pair with a different unit than in the previous map" in out
    merged = json.loads(map_path.read_text(encoding="utf-8"))
    corpus_stamp = json.loads((FIXTURE_CORPUS / "manifest.json").read_text(encoding="utf-8"))["generated_at"]
    assert merged["corpora"] == ["2026-09-01T00:00:00Z", corpus_stamp]
    assert {earlier_old: earlier_new, **PRE_TO_POST}.items() <= merged["renamed"].items()
    (copy,) = keep.glob(f"{map_path.stem}-*.json")
    assert json.loads(copy.read_text(encoding="utf-8")) == earlier

    restored = tmp_path / "restored.json"
    argv = ["--restore-as-of", "2000-01-02", "--journal", str(journal_path), "--autosave", str(autosave)]
    assert merge_verdicts.main([*argv, "--out", str(restored), "--rekey-map", str(map_path)]) == 0
    assert _units(restored) == sorted([*PRE_TO_POST.values(), earlier_new])
    capsys.readouterr()

    assert rekey_verdicts.main(["--root", str(tmp_path), "--corpus", str(FIXTURE_CORPUS)]) == 0
    assert rekey_verdicts.MAP_NAME not in capsys.readouterr().out
    assert len(list(keep.glob(f"{map_path.stem}-*.json"))) == 1


def test_rekey_moves_the_stores_tombstones_through_the_renames():
    renamed = {"u-old1": "u-new1", "u-old2": "u-new2", "u-old3": "u-new1"}
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": STAMP,
        "verdicts": [{"unit": "u-old2", "verdict": "approve", "note": "", "at": "t9"}],
        "cleared": [
            {"unit": "u-old1", "at": "t3"},
            {"unit": "u-old3", "at": "t5"},
            {"unit": "u-old2", "at": "t1"},
            {"unit": "u-kept", "at": "t2"},
        ],
    }
    rekeyed, _, _ = rekey_verdicts.rekey_payload(payload, renamed, set())
    assert rekeyed["cleared"] == [{"unit": "u-kept", "at": "t2"}, {"unit": "u-new1", "at": "t5"}]
