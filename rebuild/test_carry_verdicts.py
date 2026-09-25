"""Tests for the carry: the content key a unit's id is derived from, and the join of prior verdicts onto a new surface by that id.

The key leaves out `CARRY_PRESENTATION_KEYS`, the fields a rebuild changes without changing what the reviewer judged, so adding or changing one cannot change a unit's id and strand its verdicts. Every judged field stays in the key, so a real change to the window drops the old verdict. The key tests use the shipped review fixtures, which `rebuild/test_review_build.py` also runs through the §7 contract checker.
"""

import hashlib
import json
from pathlib import Path

import pytest

from rebuild.review import unit_cache
from rebuild.review.unit_cache import CARRY_PRESENTATION_KEYS as PRESENTATION_KEYS
from rebuild.review.unit_cache import carry_content_hash as content_hash
from rebuild.review.unit_cache import carry_projection as content_key
from rebuild.tools.carry_verdicts import main

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_UNITS = REPO_ROOT / "rebuild" / "review" / "fixtures" / "units"


def _fixture_units():
    units = []
    for shard in sorted(FIXTURE_UNITS.glob("*.json")):
        units.extend(json.loads(shard.read_text(encoding="utf-8")))
    return units


def test_ink_deltas_does_not_move_the_content_key():
    """A unit keys the same with and without `ink_deltas`, so verdicts recorded on units without the field still name their units."""
    units = _fixture_units()
    assert any(unit["ink_deltas"] for unit in units), "no fixture unit records a delta"
    for current in units:
        prior = {key: value for key, value in current.items() if key != "ink_deltas"}
        assert "ink_deltas" not in prior
        assert content_key(prior) == content_key(current), current["id"]


def test_ink_deltas_is_declared_presentation():
    assert "ink_deltas" in PRESENTATION_KEYS


def test_every_presentation_key_is_invisible_to_the_content_key():
    """Dropping any one presentation key from a unit leaves its content key unchanged."""
    for current in _fixture_units():
        for key in PRESENTATION_KEYS:
            prior = {name: value for name, value in current.items() if name != key}
            assert content_key(prior) == content_key(current), f"{current['id']}: {key}"


def test_content_key_stamp_does_not_move_the_content_key():
    """The `content_key` stamp is excluded from the projection it stamps, so a unit keys the same with or without it."""
    units = _fixture_units()
    assert all("content_key" in unit for unit in units), "the fixtures predate the stamp"
    for current in units:
        prior = {key: value for key, value in current.items() if key != "content_key"}
        assert content_key(prior) == content_key(current), current["id"]


def test_content_key_stamp_is_declared_presentation():
    assert "content_key" in PRESENTATION_KEYS


def test_content_hash_is_the_sha256_of_the_projection():
    """Each fixture's `content_key` is the sha256 of its projection, which catches a checked-in fixture stamp that is out of date."""
    for current in _fixture_units():
        stripped = {key: value for key, value in current.items() if key != "content_key"}
        assert content_hash(current) == content_hash(stripped), current["id"]
        assert current["content_key"] == hashlib.sha256(content_key(stripped).encode()).hexdigest()


def test_a_change_to_the_judged_window_moves_the_content_key():
    """Changing a judged field (the codepoints, the configs, either side's seams, or `ink_identical`) changes the key, so the tests above cannot pass with a key that ignores every field. A unit whose judged content changes loses its old verdict."""
    unit = _fixture_units()[0]
    for key, replacement in (
        ("codepoints", "E650:E650"),
        ("configs", ["ss07"]),
        ("after", {**unit["after"], "seams": [*unit["after"]["seams"], "break"]}),
        ("before", {**unit["before"], "seams": [*unit["before"]["seams"], "break"]}),
        ("ink_identical", not unit["ink_identical"]),
    ):
        assert content_key({**unit, key: replacement}) != content_key(unit), key


def test_picture_identity_is_invisible_to_the_content_key_unlike_ink_identity():
    """`picture_identical` follows from the window and both fonts' placed glyphs, which the key already covers, so it is a presentation key. `ink_identical` is also derived but stays in the key, because removing it would change every recorded unit id."""
    unit = _fixture_units()[0]
    assert content_key({**unit, "picture_identical": not unit["picture_identical"]}) == content_key(unit)


def _write_surface(root, stamp, units, machine=()):
    """Write a minimal surface for the carry: a manifest with its stamp, one class, and a triage index (`human_unit_ids`) listing every unit in `units`. A fragment carries no batch, so the index is what marks a unit as human. The `machine` units go in the shard but not in the index."""
    (root / "units").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "batch_size": 300,
                "human_unit_ids": [unit["id"] for unit in units],
                "classes": [{"id": "units", "shards": ["units/units.json"]}],
            }
        )
    )
    (root / "units" / "units.json").write_text(json.dumps([*units, *machine]))


def _write_verdicts(path, stamp, verdicts):
    path.write_text(
        json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "verdicts": verdicts})
    )


def _content_unit(codepoints: str, **fields) -> dict:
    """A unit with its `content_key` computed from its carry projection and its id derived from that key, as a surface writes it."""
    unit = {"id": None, "codepoints": codepoints, "configs": ["default"], "window": "w", **fields}
    unit["content_key"] = content_hash({key: value for key, value in unit.items() if key != "id"})
    unit["id"] = unit_cache.unit_id_for(unit["content_key"])
    return unit


def _record(unit, verdict, at="2026-07-01T01:00:00Z", note=""):
    return {"unit": unit["id"], "verdict": verdict, "note": note, "at": at}


def _carry(tmp_path, surface_units, *verdict_files, machine=(), **held):
    current = tmp_path / "current"
    _write_surface(current, "2026-07-02T00:00:00Z", surface_units, machine)
    out = tmp_path / "out.json"
    argv = []
    for path in verdict_files:
        argv += ["--verdicts", str(path)]
    main([*argv, "--out", str(out), "--current-surface", str(current)], **held)
    return json.loads(out.read_text())


def test_a_verdict_lands_on_the_unit_of_its_id_and_a_stale_stamp_is_no_bar(tmp_path):
    """The carry matches verdicts to units by id alone, so a verdicts file stamped for an older surface still carries. The unit still on the surface gets its verdict under the new manifest stamp, and the verdict whose unit is gone is stranded."""
    kept, gone = _content_unit("E650:E652"), _content_unit("E652:E653")
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(
        verdicts, "2026-06-01T00:00:00Z", [_record(kept, "approve", note="fine"), _record(gone, "reject")]
    )
    payload = _carry(tmp_path, [kept, _content_unit("E653:E654")], verdicts)
    assert payload["manifest_generated_at"] == "2026-07-02T00:00:00Z"
    [record] = payload["verdicts"]
    assert (record["unit"], record["verdict"], record["at"]) == (
        kept["id"],
        "approve",
        "2026-07-01T01:00:00Z",
    )
    assert record["note"] == f"[carried {kept['id']}@verdicts.json, verdicted 2026-07-01] fine"


def test_the_newest_verdict_per_unit_wins_across_files_and_skips_never_carry(tmp_path):
    unit, skipped = _content_unit("E650:E652"), _content_unit("E652:E653")
    older, newer = tmp_path / "older.json", tmp_path / "newer.json"
    _write_verdicts(
        older, "S0", [_record(unit, "reject", at="2026-07-01T01:00:00Z"), _record(skipped, "skip")]
    )
    _write_verdicts(newer, "S0", [_record(unit, "approve", at="2026-07-03T01:00:00Z")])
    payload = _carry(tmp_path, [unit, skipped], older, newer)
    assert [(record["unit"], record["verdict"]) for record in payload["verdicts"]] == [
        (unit["id"], "approve")
    ]


def test_the_carry_prints_its_four_figures_whatever_it_landed(tmp_path, capsys):
    """The `carry figures:` line, which the cycle records: the human units on the new surface, how many matched a prior verdict, how many did not, and how many prior verdicts matched no unit."""
    kept, gone, fresh = _content_unit("E650:E652"), _content_unit("E652:E653"), _content_unit("E653:E654")
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(verdicts, "S0", [_record(kept, "approve"), _record(gone, "reject")])
    _carry(tmp_path, [kept, fresh], verdicts)
    assert "carry figures: human=2 key_hits=1 unhit=1 stranded=1" in capsys.readouterr().out.splitlines()


def test_a_prior_verdict_on_a_machine_unit_is_not_stranded(tmp_path, capsys):
    """`stranded` counts prior verdicts whose unit is not on the surface at all, so it is checked against every surface id, machine units included. The test covers both ways the tool gets its units: loading the surface itself, and being passed the human records with the full id set."""
    kept, machine = _content_unit("E650:E652"), _content_unit("E652:E653")
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(verdicts, "S0", [_record(kept, "approve"), _record(machine, "reject")])
    _carry(tmp_path, [kept], verdicts, machine=[machine])
    assert "carry figures: human=1 key_hits=1 unhit=0 stranded=0" in capsys.readouterr().out.splitlines()

    held = [{"id": kept["id"], "batch": 0}]
    _carry(
        tmp_path / "held", [kept], verdicts, current_units=iter(held), current_ids={kept["id"], machine["id"]}
    )
    assert "carry figures: human=1 key_hits=1 unhit=0 stranded=0" in capsys.readouterr().out.splitlines()


def test_the_human_records_without_the_id_set_are_refused(tmp_path):
    """`main` exits when passed `current_units` without `current_ids`, or the reverse. The `stranded` figure needs the id of every unit on the surface, which the human records alone do not give."""
    kept = _content_unit("E650:E652")
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(verdicts, "S0", [_record(kept, "approve")])
    with pytest.raises(SystemExit):
        _carry(tmp_path / "records", [kept], verdicts, current_units=[{"id": kept["id"], "batch": 0}])
    with pytest.raises(SystemExit):
        _carry(tmp_path / "ids", [kept], verdicts, current_ids={kept["id"]})
