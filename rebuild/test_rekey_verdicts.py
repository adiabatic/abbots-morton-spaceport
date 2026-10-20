"""Tests for the forward re-key of verdicts from the unit ids of an old review corpus to the ids its units have under the current carry projection. The old and new corpora are built from the shipped review fixtures: the old one names each unit by an id of its own, and the new one by the id its fragment hashes to now, so every id changes whatever key set the fixtures were stamped under."""

import hashlib
import json
from pathlib import Path

import pytest

from rebuild.review import journal, unit_index
from rebuild.review.unit_cache import carry_content_hash, unit_id_for
from rebuild.tools import carry_verdicts, merge_verdicts, rekey_verdicts

FIXTURE_CORPUS = Path(__file__).resolve().parent / "review" / "fixtures"
OLD_STAMP = "2026-10-01T00:00:00Z"
NEW_STAMP = "2026-10-02T00:00:00Z"
STRAY = "u-3333333333A"


def _fragments() -> list[dict]:
    return list(unit_index.iter_shard_fragments(FIXTURE_CORPUS))


def _old(fragment: dict) -> dict:
    return {**fragment, "id": unit_id_for(hashlib.sha256(f"old {fragment['id']}".encode()).hexdigest())}


def _new(fragment: dict) -> dict:
    key = carry_content_hash(fragment)
    return {**fragment, "id": unit_id_for(key), "content_key": key}


def _corpus(path: Path, stamp: str, fragments: list[dict]) -> Path:
    """Write a corpus with one shard part per class, as the build lays them out."""
    parts: dict[str, list[dict]] = {}
    for fragment in fragments:
        parts.setdefault(fragment["class"], []).append(fragment)
    (path / "units").mkdir(parents=True)
    for class_id, members in parts.items():
        (path / "units" / f"{class_id}.000.json").write_text(json.dumps(members, indent=1), encoding="utf-8")
    classes = [{"id": class_id, "shards": [f"units/{class_id}.000.json"]} for class_id in parts]
    manifest = {"generated_at": stamp, "classes": classes}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _verdicts(path: Path, stamp: str, units: list[str], at: str = "2026-09-01T00:00:00Z") -> None:
    records = [{"unit": unit, "verdict": "approve", "note": "", "at": at} for unit in units]
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": records,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _units(path: Path) -> list[str]:
    return [record["unit"] for record in json.loads(path.read_text(encoding="utf-8"))["verdicts"]]


def _seed_journal(journal_path: Path, units: list[str]) -> None:
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[
            {"unit": unit, "verdict": "approve", "note": "", "at": "2000-01-01T00:00:00Z"} for unit in units
        ],
        at="2000-01-01T00:00:00Z",
    )


def _restore(tmp_path: Path, map_path: Path) -> list[str]:
    restored = tmp_path / "restored.json"
    argv = [
        "--restore-as-of",
        "2000-01-02",
        "--journal",
        str(tmp_path / journal.JOURNAL_NAME),
        "--autosave",
        str(tmp_path / "verdicts-autosave.json"),
        "--out",
        str(restored),
        "--rekey-map",
        str(map_path),
    ]
    assert merge_verdicts.main(argv) == 0
    return _units(restored)


def test_forward_rekey_maps_the_old_corpus_lands_each_id_once_and_undoes(tmp_path, capsys):
    """The map is computed from the old corpus alone, machine units included, and is never written over. A stamp-aligned store naming a content id in neither the map nor the new corpus blocks every write. Accepted with --allow-unmatched, old ids move to their new ids in the autosave, a stamp-aligned carried file and a referencing text file, and a unit whose new id a stale projected field changed lands by its codepoints and configs; the stray and a positional id stay. The kept map is the computed map with that pair, and its first form is copied beside it. A second run writes nothing, the kept map re-keys a journal replay from before the re-key, and the backup restores every file and the journal and leaves the marker that makes the next land that moves the stamp journal a base."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    news[1] = _new({**fragments[1], "kinds": [*fragments[1]["kinds"], "stale"]})
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    keep = tmp_path / rekey_verdicts.KEEP
    map_path = keep / rekey_verdicts.MAP_NAME

    map_argv = ["--root", str(tmp_path), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
    assert rekey_verdicts.main(map_argv) == 0
    computed = map_path.read_bytes()
    id_map = json.loads(computed)
    assert id_map["from_corpora"] == [OLD_STAMP]
    assert id_map["units"].keys() == {old["id"] for old in olds}
    assert id_map["renamed"] == {old["id"]: unit_id_for(carry_content_hash(old)) for old in olds}
    assert all(id_map["units"][old["id"]][:2] == [old["codepoints"], old["configs"]] for old in olds)
    assert rekey_verdicts.main(map_argv) == 1
    assert "exists, and a map is never written over" in capsys.readouterr().out
    assert map_path.read_bytes() == computed

    moved = {olds[index]["id"]: news[index]["id"] for index in range(4)}
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    _verdicts(autosave, OLD_STAMP, [*moved, STRAY, "u-10000"])
    _verdicts(tmp_path / "verdicts-carried-abc1234.json", OLD_STAMP, [olds[0]["id"]])
    _verdicts(tmp_path / "verdicts-carried-0000000.json", "2026-09-01T00:00:00Z", [olds[0]["id"]])
    notes = tmp_path / "standing-approvals.yaml"
    notes.write_text(f"note: approved at {olds[3]['id']}\n", encoding="utf-8")
    _seed_journal(journal_path, _units(autosave))
    before = {path.name: path.read_bytes() for path in (autosave, journal_path, notes)}
    argv = [
        "--root",
        str(tmp_path),
        "--map",
        str(map_path),
        "--corpus",
        str(new_corpus),
        "--references",
        str(notes),
    ]

    assert rekey_verdicts.main(argv) == 1
    assert f"1 of its content ids are neither in the map nor on new: {STRAY}" in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in (autosave, journal_path, notes)} == before
    assert [path.name for path in keep.iterdir()] == [rekey_verdicts.MAP_NAME]

    argv.append("--allow-unmatched")
    assert rekey_verdicts.main(argv) == 0
    out = capsys.readouterr().out
    assert "of the 4 ids of the map the files name, 4 land, 1 of them by codepoints and configs" in out
    assert "rekey counts: seen=7 rekeyed=5 current=0 unmatched=2 collided=0" in out
    assert _units(autosave) == sorted([*moved.values(), STRAY, "u-10000"])
    assert _units(tmp_path / "verdicts-carried-abc1234.json") == [news[0]["id"]]
    assert _units(tmp_path / "verdicts-carried-0000000.json") == [olds[0]["id"]]
    assert notes.read_text(encoding="utf-8") == f"note: approved at {news[3]['id']}\n"
    assert journal.replay(journal_path)[1].keys() == set(_units(autosave))
    kept = json.loads(map_path.read_text(encoding="utf-8"))
    assert kept["corpora"] == [NEW_STAMP]
    assert kept["renamed"] == {**id_map["renamed"], olds[1]["id"]: news[1]["id"]}
    (copy,) = keep.glob(f"{map_path.stem}-*.json")
    assert copy.read_bytes() == computed

    rekeyed = {path.name: path.read_bytes() for path in (autosave, journal_path, notes, map_path)}
    assert rekey_verdicts.main(argv) == 0
    assert "rekey counts: seen=7 rekeyed=0 current=5 unmatched=2 collided=0" in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in (autosave, journal_path, notes, map_path)} == rekeyed

    assert _restore(tmp_path, map_path) == _units(autosave)

    (backup,) = [path for path in keep.iterdir() if path.is_dir()]
    assert not journal.unjournaled_marker_for(autosave).exists()
    assert rekey_verdicts.main(["--root", str(tmp_path), "--undo", str(backup)]) == 0
    assert {path.name: path.read_bytes() for path in (autosave, journal_path, notes)} == before
    assert journal.unjournaled_marker_for(autosave).exists()


def test_an_id_that_lands_nowhere_blocks_and_a_map_must_be_one_to_one(tmp_path, capsys):
    """A verdicted id lands nowhere when its new id is on the new corpus with another drawing, or when its new id is missing and the one unit with its codepoints and configs draws differently or is the old id itself, as on a corpus rebuilt under the old key set; then the run writes nothing, dry run included. A map that pairs two old ids with one new id, one computed under another key set, and a corpus the map was computed from are refused before any file is read."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    news[0] = _new({**fragments[0], "after": {**fragments[0]["after"], "extensions": [0]}})
    news[2] = {**news[2], "after": {**fragments[2]["after"], "extensions": [99]}}
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    rebuilt = _corpus(tmp_path / "rebuilt", "2026-10-03T00:00:00Z", olds)
    map_path = tmp_path / "unit-id-map.json"
    assert rekey_verdicts.main(["--from-corpus", str(old_corpus), "--map-out", str(map_path)]) == 0
    computed = map_path.read_text(encoding="utf-8")
    autosave = tmp_path / "verdicts-autosave.json"
    _verdicts(autosave, OLD_STAMP, [olds[0]["id"], olds[2]["id"]])
    stored = autosave.read_bytes()
    argv = ["--root", str(tmp_path), "--map", str(map_path), "--dry-run", "--corpus"]

    assert rekey_verdicts.main([*argv, str(new_corpus)]) == 1
    out = capsys.readouterr().out
    assert f"{olds[0]['id']}: {news[0]['id']} has its codepoints and configs but draws differently" in out
    assert f"{olds[2]['id']}: {news[2]['id']} on the corpus has other codepoints, configs or drawings" in out
    assert autosave.read_bytes() == stored

    assert rekey_verdicts.main([*argv, str(rebuilt)]) == 1
    assert f"{olds[0]['id']}: it is still on the corpus under its old id" in capsys.readouterr().out

    assert rekey_verdicts.main([*argv, str(old_corpus)]) == 1
    assert f"is stamped {OLD_STAMP}, a corpus the map was computed from" in capsys.readouterr().out
    assert autosave.read_bytes() == stored

    id_map = json.loads(computed)
    id_map["renamed"][olds[2]["id"]] = id_map["renamed"][olds[0]["id"]]
    map_path.write_text(json.dumps(id_map), encoding="utf-8")
    assert rekey_verdicts.main([*argv, str(new_corpus)]) == 1
    assert f"{olds[0]['id']} and {olds[2]['id']} both map to" in capsys.readouterr().out

    id_map = json.loads(computed)
    id_map["presentation_keys"] = [*id_map["presentation_keys"], "kinds"]
    map_path.write_text(json.dumps(id_map), encoding="utf-8")
    assert rekey_verdicts.main([*argv, str(new_corpus)]) == 1
    assert "computed under another carry projection" in capsys.readouterr().out


def test_two_records_on_one_unit_or_a_non_string_at_on_a_unit_a_clear_lands_on_block_and_a_join_never_takes_another_entrys_unit(
    tmp_path, capsys
):
    """A file with a record on an old id and one on that id's new unit would hold two verdicts on one unit, so the run writes nothing, no flag overrides it, and both records count as collided. In a file other than the store, which the re-key refuses whole when it holds such a record, a record whose `at` is not a string blocks nothing while no clear lands on its unit, but a clear the map moves onto its unit stops the run the same way, since the re-key cannot weigh the two, while a journal restore through the same map refuses the record before its re-key, as it refuses any record the carry refuses. An id whose new id is missing from the new corpus never lands, by its codepoints and configs, on a unit that is another entry's new id."""
    fragments = _fragments()
    twin = {**fragments[0], "id": "twin", "kinds": [*fragments[0]["kinds"], "twin"]}
    olds = [_old(fragment) for fragment in [*fragments, twin]]
    news = [_new(fragment) for fragment in fragments]
    news[0] = _new(twin)
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    map_path = tmp_path / "unit-id-map.json"
    assert rekey_verdicts.main(["--from-corpus", str(old_corpus), "--map-out", str(map_path)]) == 0
    autosave, keep = tmp_path / "verdicts-autosave.json", tmp_path / rekey_verdicts.KEEP
    _verdicts(autosave, OLD_STAMP, [olds[2]["id"], news[2]["id"]])
    stored = autosave.read_bytes()
    argv = ["--root", str(tmp_path), "--map", str(map_path), "--corpus", str(new_corpus)]

    assert rekey_verdicts.main([*argv, "--allow-unmatched"]) == 1
    out = capsys.readouterr().out
    assert "collided=2" in out and "records naming different ids land on one unit" in out
    assert autosave.read_bytes() == stored and not keep.exists()

    autosave.unlink()
    export = tmp_path / "verdicts-export.json"
    _verdicts(export, OLD_STAMP, [news[2]["id"]])
    store = json.loads(export.read_text(encoding="utf-8"))
    store["verdicts"][0]["at"] = 5
    store["cleared"] = [{"unit": olds[3]["id"], "at": "2026-09-03T00:00:00Z"}]
    export.write_text(json.dumps(store), encoding="utf-8")
    assert rekey_verdicts.main([*argv, "--dry-run"]) == 0
    capsys.readouterr()
    store["cleared"] = [{"unit": olds[2]["id"], "at": "2026-09-03T00:00:00Z"}]
    export.write_text(json.dumps(store), encoding="utf-8")
    stored = export.read_bytes()
    untimed = f"a verdict record on {news[2]['id']} whose `at` is not a string (5) lands on the unit a clear lands on"
    assert rekey_verdicts.main(argv) == 1
    assert f"ERROR: in verdicts-export.json, {untimed}" in capsys.readouterr().out
    assert export.read_bytes() == stored and not keep.exists()
    export.unlink()
    journal_path, restored = tmp_path / journal.JOURNAL_NAME, tmp_path / "restored.json"
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=store["verdicts"],
        new_cleared=journal.parse_cleared(store),
        at="2000-01-01T00:00:00Z",
    )
    restore_argv = [
        "--restore-as-of",
        "2000-01-02",
        "--journal",
        str(journal_path),
        "--autosave",
        str(autosave),
    ]
    assert merge_verdicts.main([*restore_argv, "--out", str(restored), "--rekey-map", str(map_path)]) == 1
    assert (
        f"ERROR: the store that {journal_path} replays as of 2000-01-02 holds a verdict record on {news[2]['id']}"
        in capsys.readouterr().out
    )
    assert not restored.exists()

    _verdicts(autosave, OLD_STAMP, [olds[0]["id"]])
    assert rekey_verdicts.main([*argv, "--dry-run"]) == 1
    assert (
        f"{olds[0]['id']}: {news[0]['id']} has its codepoints and configs but is the new id of {olds[-1]['id']}"
        in capsys.readouterr().out
    )


def test_a_store_holding_a_record_the_carry_refuses_is_refused_before_anything_is_written(tmp_path, capsys):
    """A store holding a record whose `at` is not a string on a unit no clear lands on would be rewritten with that record on it as it is and journaled, so the re-key refuses the store, dry run included, as the land and the review server do (`verdict_store.read_store_document`), naming the store and the record, and every file under the root is left byte for byte as it was, with no backup or id map kept."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    map_path = tmp_path / "unit-id-map.json"
    assert rekey_verdicts.main(["--from-corpus", str(old_corpus), "--map-out", str(map_path)]) == 0
    autosave = tmp_path / "verdicts-autosave.json"
    _verdicts(autosave, OLD_STAMP, [olds[0]["id"], olds[1]["id"]])
    store = json.loads(autosave.read_text(encoding="utf-8"))
    store["verdicts"][1]["at"] = 5
    autosave.write_text(json.dumps(store), encoding="utf-8")
    _seed_journal(tmp_path / journal.JOURNAL_NAME, [olds[0]["id"]])
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    argv = ["--root", str(tmp_path), "--map", str(map_path), "--corpus", str(new_corpus)]
    for dry_run in (["--dry-run"], []):
        capsys.readouterr()
        assert rekey_verdicts.main([*argv, *dry_run]) == 1
        assert (
            f"ERROR: the store verdicts-autosave.json holds a verdict record on {olds[1]['id']} that is not an object"
            in capsys.readouterr().out
        )
    assert {path: path.read_bytes() for path in before} == before
    assert not (tmp_path / rekey_verdicts.KEEP).exists()


def test_a_later_rekey_merges_into_the_id_map_and_copies_the_previous_one(tmp_path, capsys):
    """A run whose kept id map already holds another run's pairs keeps every one of them, adds its own, lets its own pair win a clash, and copies the previous map beside it first. A journal restore from before either re-key then moves both runs' ids through the one map, and a run that changes nothing leaves the map alone."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    forward = tmp_path / "maps" / "forward.json"
    assert rekey_verdicts.main(["--from-corpus", str(old_corpus), "--map-out", str(forward)]) == 0
    capsys.readouterr()

    moved = {olds[index]["id"]: news[index]["id"] for index in range(4)}
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    _verdicts(autosave, OLD_STAMP, list(moved))
    earlier_old, earlier_new, clashing = "u-4444444444B", "u-5555555555C", "u-6666666666D"
    _seed_journal(journal_path, [*moved, earlier_old])
    keep = tmp_path / rekey_verdicts.KEEP
    keep.mkdir(parents=True)
    map_path = keep / rekey_verdicts.MAP_NAME
    earlier = {
        "format": rekey_verdicts.MAP_FORMAT,
        "from_corpora": ["2026-08-01T00:00:00Z"],
        "corpora": ["2026-09-01T00:00:00Z"],
        "presentation_keys": json.loads(forward.read_text(encoding="utf-8"))["presentation_keys"],
        "units": {earlier_old: ["E650:E650", ["default"], "a", "b", "c"]},
        "renamed": {earlier_old: earlier_new, olds[0]["id"]: clashing},
    }
    map_path.write_text(json.dumps(earlier), encoding="utf-8")
    argv = ["--root", str(tmp_path), "--map", str(forward), "--corpus", str(new_corpus)]

    assert rekey_verdicts.main(argv) == 0
    assert "1 old id(s) pair with a different unit than in the previous map" in capsys.readouterr().out
    merged = json.loads(map_path.read_text(encoding="utf-8"))
    assert merged["from_corpora"] == ["2026-08-01T00:00:00Z", OLD_STAMP]
    assert merged["corpora"] == ["2026-09-01T00:00:00Z", NEW_STAMP]
    assert {earlier_old: earlier_new, **moved}.items() <= merged["renamed"].items()
    assert merged["units"].keys() == {earlier_old, *(old["id"] for old in olds)}
    (copy,) = keep.glob(f"{map_path.stem}-*.json")
    assert json.loads(copy.read_text(encoding="utf-8")) == earlier

    assert _restore(tmp_path, map_path) == sorted([*moved.values(), earlier_new])
    capsys.readouterr()

    assert rekey_verdicts.main(argv) == 0
    assert rekey_verdicts.MAP_NAME not in capsys.readouterr().out
    assert len(list(keep.glob(f"{map_path.stem}-*.json"))) == 1


@pytest.mark.parametrize("named", [False, True])
def test_a_rekey_of_a_store_outside_the_root_journals_beside_it_unless_journal_names_another(
    tmp_path, capsys, named
):
    """With no `--journal`, the re-key appends its base event to the verdicts-journal.ndjson beside `--autosave` (`journal.journal_path_for`), not to the one at `--root`, so a re-key of a copy of the store leaves the checkout's journal alone. A `--journal` names another journal, and the re-key appends there instead."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    root, copy = tmp_path / "root", tmp_path / "copy" / "verdicts-autosave.json"
    root.mkdir()
    copy.parent.mkdir()
    map_path = tmp_path / "map.json"
    map_argv = ["--root", str(root), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
    assert rekey_verdicts.main(map_argv) == 0
    _verdicts(copy, OLD_STAMP, [olds[0]["id"]])
    named_journal = tmp_path / "named.ndjson"
    argv = ["--root", str(root), "--map", str(map_path), "--corpus", str(new_corpus), "--autosave", str(copy)]
    assert rekey_verdicts.main([*argv, *(["--journal", str(named_journal)] if named else [])]) == 0
    capsys.readouterr()
    assert _units(copy) == [news[0]["id"]]
    beside = copy.with_name(journal.JOURNAL_NAME)
    written, unwritten = (named_journal, beside) if named else (beside, named_journal)
    stamp, records = journal.replay(written)
    assert (stamp, list(records)) == (OLD_STAMP, [news[0]["id"]])
    assert not unwritten.exists()
    assert not (root / journal.JOURNAL_NAME).exists()


def test_a_rekey_of_a_copy_leaves_the_roots_files_alone_and_its_undo_marks_the_copy(tmp_path, capsys):
    """A re-key whose `--root` holds a store, its journal and a stash stamped for the corpus the map was computed from, and whose `--autosave` names a copy in another directory, leaves the root's files out (`rekey_verdicts.verdict_files`), so they keep their bytes and the copy's journal, beside it, replays to the re-keyed copy. Its backup and the id map it applied go to the keep directory beside the copy (`journal.beside_store`), and the root gets none. Its `--undo` puts the copy and its journal back and leaves the unjournaled marker beside the copy, not beside the root's store."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    root, copy = tmp_path / "root", tmp_path / "copy" / "verdicts-autosave.json"
    root.mkdir()
    copy.parent.mkdir()
    map_path = tmp_path / "map.json"
    map_argv = ["--root", str(root), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
    assert rekey_verdicts.main(map_argv) == 0
    live, live_journal = root / "verdicts-autosave.json", root / journal.JOURNAL_NAME
    copy_journal = copy.with_name(journal.JOURNAL_NAME)
    for store, store_journal, old in ((live, live_journal, olds[0]), (copy, copy_journal, olds[1])):
        _verdicts(store, OLD_STAMP, [old["id"]])
        _seed_journal(store_journal, [old["id"]])
    stash = root / "verdicts-autosave-old.json"
    _verdicts(stash, OLD_STAMP, [olds[2]["id"]])
    root_files = {path: path.read_bytes() for path in (live, live_journal, stash)}
    copied = {path: path.read_bytes() for path in (copy, copy_journal)}
    argv = ["--root", str(root), "--map", str(map_path), "--corpus", str(new_corpus), "--autosave", str(copy)]
    assert rekey_verdicts.main(argv) == 0
    capsys.readouterr()
    assert _units(copy) == [news[1]["id"]]
    rekeyed = journal.latest_by_unit(json.loads(copy.read_text(encoding="utf-8"))["verdicts"])
    assert journal.replay(copy_journal) == (OLD_STAMP, rekeyed)
    assert {path: path.read_bytes() for path in root_files} == root_files

    assert not (root / rekey_verdicts.KEEP).exists()
    keep = copy.parent / rekey_verdicts.KEEP
    assert (keep / rekey_verdicts.MAP_NAME).exists()
    (backup,) = [path for path in keep.iterdir() if path.is_dir()]
    assert rekey_verdicts.main(["--root", str(root), "--undo", str(backup)]) == 0
    assert {path: path.read_bytes() for path in copied} == copied
    assert journal.unjournaled_marker_for(copy).exists()
    assert not journal.unjournaled_marker_for(live).exists()
    assert {path: path.read_bytes() for path in root_files} == root_files


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        ("--verdicts", "verdicts-autosave.json"),
        ("--verdicts", "Verdicts-Autosave.json"),
        ("--references", "verdicts-autosave.json"),
    ],
)
def test_a_rekey_of_a_copy_refuses_the_roots_store_passed_by_name(tmp_path, capsys, flag, name):
    """A file named verdicts-autosave.json, in any letter case, that `--autosave` does not name is a journaled store the re-key would rewrite without journaling it (`rekey_verdicts.another_store`), so passing the root's store with `--verdicts` or `--references` beside a copy's `--autosave` is a usage error that writes nothing: the root pair and the copy's pair keep their bytes."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    root, copy = tmp_path / "root", tmp_path / "copy" / "verdicts-autosave.json"
    root.mkdir()
    copy.parent.mkdir()
    map_path = tmp_path / "map.json"
    map_argv = ["--root", str(root), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
    assert rekey_verdicts.main(map_argv) == 0
    live = root / "verdicts-autosave.json"
    for store, old in ((live, olds[0]), (copy, olds[1])):
        _verdicts(store, OLD_STAMP, [old["id"]])
        _seed_journal(store.with_name(journal.JOURNAL_NAME), [old["id"]])
    pairs = [live, live.with_name(journal.JOURNAL_NAME), copy, copy.with_name(journal.JOURNAL_NAME)]
    before = {path: path.read_bytes() for path in pairs}
    argv = ["--root", str(root), "--map", str(map_path), "--corpus", str(new_corpus), "--autosave", str(copy)]
    with pytest.raises(SystemExit) as refused:
        rekey_verdicts.main([*argv, flag, str(root / name)])
    assert refused.value.code == 2
    assert f"re-key it with --autosave {root / name}" in capsys.readouterr().err
    assert {path: path.read_bytes() for path in pairs} == before
    assert not (root / rekey_verdicts.KEEP).exists()


def test_a_rekey_journals_the_stores_tombstones_and_a_restore_and_the_undo_put_them_back(tmp_path):
    """The store's tombstones on ids the map moves move with its records, and the re-key's base event holds them under their new ids. A journal restore from before the re-key moves the replayed tombstones through the kept map, and the undo cuts the journal back to the store that holds them under their old ids."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    keep = tmp_path / rekey_verdicts.KEEP
    map_path = keep / rekey_verdicts.MAP_NAME
    assert (
        rekey_verdicts.main(
            ["--root", str(tmp_path), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
        )
        == 0
    )
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    _verdicts(autosave, OLD_STAMP, [olds[0]["id"]])
    cleared = {olds[1]["id"]: "2026-09-02T00:00:00Z", olds[2]["id"]: "2026-09-03T00:00:00Z"}
    store = json.loads(autosave.read_text(encoding="utf-8"))
    store["cleared"] = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]
    autosave.write_text(json.dumps(store), encoding="utf-8")
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=store["verdicts"],
        new_cleared=cleared,
        at="2000-01-01T00:00:00Z",
    )
    before = journal.replay_store(journal_path)
    assert before[2] == cleared
    argv = ["--root", str(tmp_path), "--map", str(map_path), "--corpus", str(new_corpus)]

    assert rekey_verdicts.main(argv) == 0
    rekeyed = {news[1]["id"]: cleared[olds[1]["id"]], news[2]["id"]: cleared[olds[2]["id"]]}
    assert journal.parse_cleared(json.loads(autosave.read_text(encoding="utf-8"))) == rekeyed
    assert journal.replay_store(journal_path)[2] == rekeyed
    assert _restore(tmp_path, map_path) == [news[0]["id"]]
    assert (
        journal.parse_cleared(json.loads((tmp_path / "restored.json").read_text(encoding="utf-8"))) == rekeyed
    )

    (backup,) = [path for path in keep.iterdir() if path.is_dir()]
    assert rekey_verdicts.main(["--root", str(tmp_path), "--undo", str(backup)]) == 0
    assert journal.replay_store(journal_path) == before


def test_a_store_whose_only_moved_id_is_a_tombstone_is_rewritten_so_the_carry_keeps_the_unit_cleared(
    tmp_path, capsys
):
    """A store holding no record and one tombstone on an id the map moves is rewritten and journaled with the tombstone on the new id, and the counts say so; a second run finds the tombstone current and writes nothing. The carry onto the new corpus then keeps out the older verdict a root carried file holds on that unit, which the same re-key moved, and the undo puts the tombstone back on the old id."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    keep = tmp_path / rekey_verdicts.KEEP
    map_path = keep / rekey_verdicts.MAP_NAME
    assert (
        rekey_verdicts.main(
            ["--root", str(tmp_path), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
        )
        == 0
    )
    old_id, new_id, clear_at = olds[0]["id"], news[0]["id"], "2026-09-02T00:00:00Z"
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    carried = tmp_path / "verdicts-carried-abc1234.json"
    _verdicts(carried, OLD_STAMP, [old_id])
    _verdicts(autosave, OLD_STAMP, [])
    store = json.loads(autosave.read_text(encoding="utf-8"))
    store["cleared"] = [{"unit": old_id, "at": clear_at}]
    autosave.write_text(json.dumps(store), encoding="utf-8")
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[],
        new_cleared={old_id: clear_at},
        at="2000-01-01T00:00:00Z",
    )
    before = {path.name: path.read_bytes() for path in (autosave, carried, journal_path)}
    capsys.readouterr()

    argv = ["--root", str(tmp_path), "--map", str(map_path), "--corpus", str(new_corpus)]
    assert rekey_verdicts.main(argv) == 0
    out = capsys.readouterr().out
    assert (
        f"verdicts-autosave.json (stamped {OLD_STAMP}): 0 verdicts, 0 re-keyed, 0 already current, 0 unmatched, "
        "0 collided, 1 tombstones re-keyed, 0 cleared by a tombstone\n" in out
    )
    assert "rewrote verdicts-autosave.json" in out
    assert "rekey counts: seen=1 rekeyed=1 current=0 unmatched=0 collided=0 tombstones_rekeyed=1" in out
    assert journal.parse_cleared(json.loads(autosave.read_text(encoding="utf-8"))) == {new_id: clear_at}
    assert journal.replay_store(journal_path) == (OLD_STAMP, {}, {new_id: clear_at})
    assert _units(carried) == [new_id]

    rekeyed = {path.name: path.read_bytes() for path in (autosave, carried, journal_path)}
    assert rekey_verdicts.main(argv) == 0
    out = capsys.readouterr().out
    assert "rekey counts: seen=1 rekeyed=0 current=1 unmatched=0 collided=0 tombstones_rekeyed=0" in out
    assert "rewrote verdicts-autosave.json" not in out
    assert {path.name: path.read_bytes() for path in (autosave, carried, journal_path)} == rekeyed

    onto_new = tmp_path / "carried-onto-new.json"
    carry_argv = [
        "--verdicts",
        str(autosave),
        "--verdicts",
        str(carried),
        "--current-corpus",
        str(new_corpus),
    ]
    ids = {fragment["id"] for fragment in news}
    assert (
        carry_verdicts.main(
            [*carry_argv, "--out", str(onto_new)],
            current_units=[{"id": unit} for unit in sorted(ids)],
            current_ids=ids,
        )
        == 0
    )
    assert _units(onto_new) == []

    (backup,) = [path for path in keep.iterdir() if path.is_dir()]
    assert rekey_verdicts.main(["--root", str(tmp_path), "--undo", str(backup)]) == 0
    assert {path.name: path.read_bytes() for path in (autosave, carried, journal_path)} == before
    assert journal.replay_store(journal_path) == (OLD_STAMP, {}, {old_id: clear_at})


def test_a_clear_the_map_moves_onto_an_older_record_clears_its_unit_through_the_carry_and_the_undo_puts_it_back(
    tmp_path, capsys
):
    """A store holds an approval on a unit's new id and a later clear on its old id, and a root carried file holds an approval on the old id made between the two. The re-key moves the clear onto the approved unit, where it wins, as a clear wins in a merge: the store is rewritten and journaled with the unit cleared and no record, and the counts say one record was cleared. The carry onto the new corpus then keeps out the carried file's re-keyed approval, a journal restore from before the re-key moves the replayed store through the kept map by the same rule, and the undo puts the approval back."""
    fragments = _fragments()
    olds = [_old(fragment) for fragment in fragments]
    news = [_new(fragment) for fragment in fragments]
    old_corpus = _corpus(tmp_path / "old", OLD_STAMP, olds)
    new_corpus = _corpus(tmp_path / "new", NEW_STAMP, news)
    keep = tmp_path / rekey_verdicts.KEEP
    map_path = keep / rekey_verdicts.MAP_NAME
    assert (
        rekey_verdicts.main(
            ["--root", str(tmp_path), "--from-corpus", str(old_corpus), "--map-out", str(map_path)]
        )
        == 0
    )
    old_id, new_id, clear_at = olds[0]["id"], news[0]["id"], "2026-09-03T00:00:00Z"
    autosave, journal_path = tmp_path / "verdicts-autosave.json", tmp_path / journal.JOURNAL_NAME
    carried = tmp_path / "verdicts-carried-abc1234.json"
    _verdicts(carried, OLD_STAMP, [old_id], at="2026-09-02T00:00:00Z")
    _verdicts(autosave, OLD_STAMP, [new_id])
    store = json.loads(autosave.read_text(encoding="utf-8"))
    store["cleared"] = [{"unit": old_id, "at": clear_at}]
    autosave.write_text(json.dumps(store), encoding="utf-8")
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=store["verdicts"],
        new_cleared={old_id: clear_at},
        at="2000-01-01T00:00:00Z",
    )
    before = {path.name: path.read_bytes() for path in (autosave, carried, journal_path)}
    capsys.readouterr()

    argv = ["--root", str(tmp_path), "--map", str(map_path), "--corpus", str(new_corpus)]
    assert rekey_verdicts.main(argv) == 0
    out = capsys.readouterr().out
    assert (
        f"verdicts-autosave.json (stamped {OLD_STAMP}): 1 verdicts, 0 re-keyed, 1 already current, 0 unmatched, "
        "0 collided, 1 tombstones re-keyed, 1 cleared by a tombstone\n" in out
    )
    assert (
        "rekey counts: seen=2 rekeyed=1 current=1 unmatched=0 collided=0 tombstones_rekeyed=1 records_cleared=1"
        in out
    )
    assert _units(autosave) == []
    assert journal.parse_cleared(json.loads(autosave.read_text(encoding="utf-8"))) == {new_id: clear_at}
    assert journal.replay_store(journal_path) == (OLD_STAMP, {}, {new_id: clear_at})
    assert _units(carried) == [new_id]

    onto_new = tmp_path / "carried-onto-new.json"
    ids = {fragment["id"] for fragment in news}
    carry_argv = [
        "--verdicts",
        str(autosave),
        "--verdicts",
        str(carried),
        "--current-corpus",
        str(new_corpus),
    ]
    assert (
        carry_verdicts.main(
            [*carry_argv, "--out", str(onto_new)],
            current_units=[{"id": unit} for unit in sorted(ids)],
            current_ids=ids,
        )
        == 0
    )
    assert _units(onto_new) == []

    capsys.readouterr()
    assert _restore(tmp_path, map_path) == []
    out = capsys.readouterr().out
    assert "re-keyed 0 of 1 restored verdicts and 1 of 1 restored tombstones through " in out
    assert (
        "; dropped 1 verdicts that a clear at or after them landed on and 0 tombstones that a newer verdict "
        "landed on\n" in out
    )

    (backup,) = [path for path in keep.iterdir() if path.is_dir()]
    assert rekey_verdicts.main(["--root", str(tmp_path), "--undo", str(backup)]) == 0
    assert {path.name: path.read_bytes() for path in (autosave, carried, journal_path)} == before


@pytest.mark.parametrize(
    ("record_at", "record_wins"),
    [("2026-09-01T00:00:00Z", False), ("2026-09-04T00:00:00Z", True), ("2026-09-03T00:00:00Z", False)],
    ids=["older-record", "newer-record", "tie"],
)
def test_a_clear_and_a_record_that_land_on_one_unit_keep_the_newer_and_a_tie_goes_to_the_clear(
    record_at, record_wins
):
    """A re-key and a journal restore's re-key (`merge_verdicts.rekey_store`) settle a tombstone the map moves onto a unit holding a record alike, as a merge settles a clear and a record: a clear at or after the record's `at` keeps the unit cleared and drops the record, which the re-key counts, and a record strictly newer than the clear keeps the unit and drops the tombstone."""
    renamed = {"u-A": "u-B"}
    clear_at = "2026-09-03T00:00:00Z"
    record = {"unit": "u-B", "verdict": "approve", "note": "", "at": record_at}
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": OLD_STAMP,
        "verdicts": [record],
        "cleared": [{"unit": "u-A", "at": clear_at}],
    }
    records = {"u-B": record} if record_wins else {}
    tombstones = {} if record_wins else {"u-B": clear_at}

    rekeyed, counts, _ = rekey_verdicts.rekey_payload(payload, renamed, {"u-B"})
    assert rekeyed["verdicts"] == list(records.values())
    assert journal.parse_cleared(rekeyed) == tombstones
    assert counts["records_cleared"] == (0 if record_wins else 1)
    assert merge_verdicts.rekey_store({"u-B": record}, {"u-A": clear_at}, renamed) == (records, tombstones)


def test_records_that_land_on_one_unit_are_settled_and_counted_alike_whichever_order_they_arrive_in():
    """When two restored records with one `at` land on one unit, the journal restore's re-key keeps the same one whichever the replay lists first. When a clear lands there too, it meets the newest of them whichever comes first: a record newer than the clear keeps the unit and drops the tombstone, and a clear newer than both drops both. The re-key counts every record on a unit that records naming two ids land on as collided whichever the file lists first."""
    renamed = {"u-A": "u-B"}
    moved = {"unit": "u-A", "verdict": "approve", "note": "", "at": "t1"}
    current = {"unit": "u-B", "verdict": "reject", "note": "", "at": "t1"}
    forward = merge_verdicts.rekey_store({"u-A": moved, "u-B": current}, {}, renamed)
    backward = merge_verdicts.rekey_store({"u-B": current, "u-A": moved}, {}, renamed)
    assert forward == backward == ({"u-B": {**moved, "unit": "u-B"}}, {})
    onto = {"u-A": "u-B", "u-C": "u-B"}
    older = {**moved, "at": "t1"}
    for newest_at, expected in (("t5", ({"u-B": {**current, "at": "t5"}}, {})), ("t2", ({}, {"u-B": "t3"}))):
        newest = {**current, "at": newest_at}
        for listed in ({"u-A": older, "u-B": newest}, {"u-B": newest, "u-A": older}):
            assert merge_verdicts.rekey_store(listed, {"u-C": "t3"}, onto) == expected
    for listed in ([current, moved, moved], [moved, moved, current]):
        payload = {"format": "ams-review-verdicts/1", "manifest_generated_at": OLD_STAMP, "verdicts": listed}
        assert rekey_verdicts.rekey_payload(payload, renamed, {"u-B"})[1]["collided"] == 3


def test_rekey_moves_the_stores_tombstones_through_the_pairs():
    """A re-key and a journal restore's re-key (`merge_verdicts.rekey_store`) move a store's tombstones alike: two that land on one id keep the later clear, whichever the store lists first, and one on an id a record names is passed over, though its clear is newer than the record, as the verdict store passes it over. The re-key counts only the tombstones that land."""
    renamed = {
        "u-old1": "u-new1",
        "u-old2": "u-new2",
        "u-old3": "u-new1",
        "u-old4": "u-new3",
        "u-old5": "u-new3",
    }
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": OLD_STAMP,
        "verdicts": [{"unit": "u-old2", "verdict": "approve", "note": "", "at": "t0"}],
        "cleared": [
            {"unit": "u-old1", "at": "t3"},
            {"unit": "u-old3", "at": "t5"},
            {"unit": "u-old4", "at": "t6"},
            {"unit": "u-old5", "at": "t4"},
            {"unit": "u-old2", "at": "t1"},
            {"unit": "u-kept", "at": "t2"},
        ],
    }
    rekeyed, counts, _ = rekey_verdicts.rekey_payload(payload, renamed, set())
    assert rekeyed["cleared"] == [
        {"unit": "u-kept", "at": "t2"},
        {"unit": "u-new1", "at": "t5"},
        {"unit": "u-new3", "at": "t6"},
    ]
    assert counts["tombstones_rekeyed"] == 2
    records, cleared = merge_verdicts.rekey_store(
        {"u-old2": payload["verdicts"][0]}, journal.parse_cleared(payload), renamed
    )
    assert records == {"u-new2": {**payload["verdicts"][0], "unit": "u-new2"}}
    assert cleared == {"u-kept": "t2", "u-new1": "t5", "u-new3": "t6"}


def test_a_journal_restores_rekey_counts_what_it_moves_against_what_the_journal_replays(tmp_path, capsys):
    """A journal restore through a map counts the restored verdicts, and the restored tombstones that no newer verdict drops, whose ids the map moves against the ones the journal replays, the verdicts as the re-key counts `rekeyed` of `seen`: a record or a tombstone on an id the map leaves alone counts only in the total, and two records or two tombstones that land on one unit both count as re-keyed, though only the newer record and the later clear are restored."""
    renamed = {"u-old1": "u-new1", "u-old2": "u-new1", "u-old3": "u-new3", "u-old4": "u-new3"}
    records = [
        {"unit": "u-old1", "verdict": "approve", "note": "", "at": "2026-09-01T00:00:00Z"},
        {"unit": "u-old2", "verdict": "reject", "note": "", "at": "2026-09-02T00:00:00Z"},
        {"unit": "u-kept", "verdict": "approve", "note": "", "at": "2026-09-03T00:00:00Z"},
    ]
    cleared = {
        "u-old3": "2026-09-04T00:00:00Z",
        "u-old4": "2026-09-05T00:00:00Z",
        "u-gone": "2026-09-06T00:00:00Z",
    }
    journal.record_transition(
        tmp_path / journal.JOURNAL_NAME,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=records,
        new_cleared=cleared,
        at="2000-01-01T00:00:00Z",
    )
    map_path = tmp_path / rekey_verdicts.MAP_NAME
    map_path.write_text(json.dumps({"renamed": renamed}), encoding="utf-8")
    capsys.readouterr()

    assert _restore(tmp_path, map_path) == ["u-kept", "u-new1"]
    out = capsys.readouterr().out
    assert "re-keyed 2 of 3 restored verdicts and 2 of 3 restored tombstones through " in out
    assert (
        "; dropped 0 verdicts that a clear at or after them landed on and 0 tombstones that a newer verdict "
        "landed on\n" in out
    )
    restored = json.loads((tmp_path / "restored.json").read_text(encoding="utf-8"))
    assert journal.parse_cleared(restored) == {"u-gone": cleared["u-gone"], "u-new3": cleared["u-old4"]}
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": OLD_STAMP,
        "verdicts": records,
        "cleared": [{"unit": unit, "at": at} for unit, at in cleared.items()],
    }
    counts = rekey_verdicts.rekey_payload(payload, renamed, {"u-kept"})[1]
    assert (counts["rekeyed"], counts["seen"]) == (2, 3)


@pytest.mark.parametrize(
    ("renamed", "beaten"),
    [({"u-old1": "u-new", "u-old2": "u-new"}, "u-old2"), ({"u-old1": "u-new"}, "u-new")],
    ids=["moved-tombstone", "unmoved-tombstone"],
)
def test_a_journal_restores_rekey_counts_a_tombstone_a_newer_verdict_drops_as_dropped(
    tmp_path, capsys, renamed, beaten
):
    """When the map moves a restored verdict onto the unit a restored tombstone whose clear is older than the verdict lands on, whether the map moves that tombstone there too or leaves it on its own unit, the restore keeps the verdict and drops the tombstone, and counts that tombstone as dropped and not as re-keyed, as the re-key leaves a tombstone the rule drops out of `tombstones_rekeyed`, so the counts agree with the restored file and with the re-key's on the same store."""
    records = [{"unit": "u-old1", "verdict": "approve", "note": "", "at": "2026-09-09T00:00:00Z"}]
    cleared = {beaten: "2026-09-01T00:00:00Z", "u-kept": "2026-09-02T00:00:00Z"}
    journal.record_transition(
        tmp_path / journal.JOURNAL_NAME,
        source="merge",
        stamp=OLD_STAMP,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=records,
        new_cleared=cleared,
        at="2000-01-01T00:00:00Z",
    )
    map_path = tmp_path / rekey_verdicts.MAP_NAME
    map_path.write_text(json.dumps({"renamed": renamed}), encoding="utf-8")
    capsys.readouterr()

    assert _restore(tmp_path, map_path) == ["u-new"]
    out = capsys.readouterr().out
    assert "re-keyed 1 of 1 restored verdicts and 0 of 2 restored tombstones through " in out
    assert (
        "; dropped 0 verdicts that a clear at or after them landed on and 1 tombstones that a newer verdict "
        "landed on\n" in out
    )
    restored = json.loads((tmp_path / "restored.json").read_text(encoding="utf-8"))
    assert journal.parse_cleared(restored) == {"u-kept": cleared["u-kept"]}
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": OLD_STAMP,
        "verdicts": records,
        "cleared": [{"unit": unit, "at": at} for unit, at in cleared.items()],
    }
    counts = rekey_verdicts.rekey_payload(payload, renamed, set())[1]
    assert (counts["rekeyed"], counts["tombstones_rekeyed"], counts["records_cleared"]) == (1, 0, 0)
