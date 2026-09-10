"""Tests for the carry: the content key a unit's id derives from, which is what lets a verdict follow its unit across surface rebuilds, and the join on that id the carry performs. For the key, everything the rebuild churns — ids, batches, drafts, provenance, the derived group ids, and the per-config ink_deltas map — is presentation and stays out, so a field's first appearance cannot rename a unit and strand the verdicts recorded on it; everything the reviewer actually judged stays in, so a real change to the window loses its old verdict rather than inheriting one. The key tests' units are the shipped review fixtures, which the §7 contract checker also gates in test_review_build."""

import hashlib
import json
from pathlib import Path

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
    """The field's introduction is invisible to the key: a unit predating ink_deltas and the same unit carrying it key identically, so every verdict recorded before the field still names its unit."""
    units = _fixture_units()
    assert any(unit["ink_deltas"] for unit in units), "no fixture unit records a delta"
    for current in units:
        prior = {key: value for key, value in current.items() if key != "ink_deltas"}
        assert "ink_deltas" not in prior
        assert content_key(prior) == content_key(current), current["id"]


def test_ink_deltas_is_declared_presentation():
    assert "ink_deltas" in PRESENTATION_KEYS


def test_every_presentation_key_is_invisible_to_the_content_key():
    """The whole exclusion list behaves the same way ink_deltas does — dropping any one of them, as an older surface would have, leaves the key untouched."""
    for current in _fixture_units():
        for key in PRESENTATION_KEYS:
            prior = {name: value for name, value in current.items() if name != key}
            assert content_key(prior) == content_key(current), f"{current['id']}: {key}"


def test_content_key_stamp_does_not_move_the_content_key():
    """The build-time stamp is itself presentation: a unit predating the stamp and the same unit carrying it key identically, so the stamp's introduction renamed nothing."""
    units = _fixture_units()
    assert all("content_key" in unit for unit in units), "the fixtures predate the stamp"
    for current in units:
        prior = {key: value for key, value in current.items() if key != "content_key"}
        assert content_key(prior) == content_key(current), current["id"]


def test_content_key_stamp_is_declared_presentation():
    assert "content_key" in PRESENTATION_KEYS


def test_content_hash_is_the_sha256_of_the_projection():
    """The fixture stamps are exactly the sha256 of the projection, stamped or not, which pins the checked-in fixture stamps against rot."""
    for current in _fixture_units():
        stripped = {key: value for key, value in current.items() if key != "content_key"}
        assert content_hash(current) == content_hash(stripped), current["id"]
        assert current["content_key"] == hashlib.sha256(content_key(stripped).encode()).hexdigest()


def test_a_change_to_the_judged_window_moves_the_content_key():
    """The complement, so the exclusions above cannot pass by keying on nothing: the fields the reviewer judges — the window, the configs it covers, and the cells and seams both fonts draw — are all in the key, and moving any of them retires the old verdict instead of carrying it onto a different question."""
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
    """`picture_identical` is a pure function of the window and both fonts' placed glyphs, all of which the key already covers, and it arrived after the ids on record were stamped — so it is a presentation key, while `ink_identical` stays inside the key only as the byte-identity contract with those ids."""
    unit = _fixture_units()[0]
    assert content_key({**unit, "picture_identical": not unit["picture_identical"]}) == content_key(unit)


def _write_surface(root, stamp, units):
    """A surface skeleton the carry reads: the manifest's stamp, its one class, and its triage index — every unit here is human, and the index is what says so, since a fragment carries no batch."""
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
    (root / "units" / "units.json").write_text(json.dumps(units))


def _write_verdicts(path, stamp, verdicts):
    path.write_text(
        json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "verdicts": verdicts})
    )


def _content_unit(codepoints: str, **fields) -> dict:
    """A unit as a content-addressed surface writes it: stamped over its carry projection and named by that stamp."""
    unit = {"id": None, "codepoints": codepoints, "configs": ["default"], "window": "w", **fields}
    unit["content_key"] = content_hash({key: value for key, value in unit.items() if key != "id"})
    unit["id"] = unit_cache.unit_id_for(unit["content_key"])
    return unit


def _record(unit, verdict, at="2026-07-01T01:00:00Z", note=""):
    return {"unit": unit["id"], "verdict": verdict, "note": note, "at": at}


def _carry(tmp_path, current_units, *verdict_files):
    current = tmp_path / "current"
    _write_surface(current, "2026-07-02T00:00:00Z", current_units)
    out = tmp_path / "out.json"
    argv = []
    for path in verdict_files:
        argv += ["--verdicts", str(path)]
    main([*argv, "--out", str(out), "--current-surface", str(current)])
    return json.loads(out.read_text())


def test_a_verdict_lands_on_the_unit_of_its_id_and_a_stale_stamp_is_no_bar(tmp_path):
    """The id is the identity, so a verdicts file stamped for an older surface carries onto the live one by id alone: the unit still on the surface takes its verdict under the new stamp, and the unit that is gone strands its verdict."""
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
    """The line the cycle records off the carry: every human unit on the new surface, how many a prior verdict keyed onto, how many none did, and how many prior verdicts found no unit to land on."""
    kept, gone, fresh = _content_unit("E650:E652"), _content_unit("E652:E653"), _content_unit("E653:E654")
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(verdicts, "S0", [_record(kept, "approve"), _record(gone, "reject")])
    _carry(tmp_path, [kept, fresh], verdicts)
    assert "carry figures: human=2 key_hits=1 unhit=1 stranded=1" in capsys.readouterr().out.splitlines()
