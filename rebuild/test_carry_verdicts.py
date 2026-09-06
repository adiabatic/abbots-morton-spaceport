"""Tests for the cross-surface carry: the content key a prior surface's verdict is re-resolved against when the surface is rebuilt, and the stamp guard that refuses a source pair whose verdicts were recorded against a different surface than the one offered. For the key, everything the rebuild churns — ids, batches, drafts, provenance, the derived group ids, and the per-config ink_deltas map — is presentation and stays out, so a field's first appearance cannot strand the verdicts recorded before it; everything the reviewer actually judged stays in, so a real change to the window loses its old verdict rather than inheriting one. The key tests' units are the shipped review fixtures, which the §7 contract checker also gates in test_review_build."""

import hashlib
import json
from pathlib import Path

import pytest

from rebuild.tools.carry_verdicts import PRESENTATION_KEYS, content_hash, content_key, main

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_UNITS = REPO_ROOT / "rebuild" / "review" / "fixtures" / "units"


def _fixture_units():
    units = []
    for shard in sorted(FIXTURE_UNITS.glob("*.json")):
        units.extend(json.loads(shard.read_text(encoding="utf-8")))
    return units


def test_ink_deltas_does_not_move_the_content_key():
    """The field's introduction is invisible to the carry: a prior-surface unit predating ink_deltas and the same current-surface unit carrying it key identically, so every verdict recorded against the older surface still lands."""
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
    """The build-time stamp is itself presentation: a prior-surface unit predating the stamp and the same current-surface unit carrying it key identically, so the stamp's introduction cannot strand a single verdict recorded against an unstamped surface."""
    units = _fixture_units()
    assert all("content_key" in unit for unit in units), "the fixtures predate the stamp"
    for current in units:
        prior = {key: value for key, value in current.items() if key != "content_key"}
        assert content_key(prior) == content_key(current), current["id"]


def test_content_key_stamp_is_declared_presentation():
    assert "content_key" in PRESENTATION_KEYS


def test_content_hash_reads_the_stamp_or_computes_the_same_value():
    """Stamped and unstamped surfaces resolve against each other: the fixture stamps are exactly the sha256 of the projection an unstamped unit hashes to, so a mixed source pair carries losslessly. This also pins the checked-in fixture stamps against rot."""
    for current in _fixture_units():
        stripped = {key: value for key, value in current.items() if key != "content_key"}
        assert content_hash(current) == content_hash(stripped), current["id"]
        assert current["content_key"] == hashlib.sha256(content_key(stripped).encode()).hexdigest()


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


def _run_carry(monkeypatch, prior, verdicts, out, current):
    monkeypatch.setattr(
        "sys.argv",
        [
            "carry_verdicts.py",
            "--source",
            str(prior),
            str(verdicts),
            "--out",
            str(out),
            "--current-surface",
            str(current),
        ],
    )
    main()


def test_a_source_pair_with_disagreeing_stamps_refuses(tmp_path, monkeypatch):
    """The verdicts' unit ids only resolve correctly on the surface they were recorded against, so a pair whose stamps disagree must refuse instead of carrying onto the wrong windows."""
    prior = tmp_path / "prior"
    _write_surface(prior, "2026-07-01T00:00:00Z", [])
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(verdicts, "2026-06-01T00:00:00Z", [])
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit, match="different surface"):
        _run_carry(monkeypatch, prior, verdicts, out, tmp_path / "current")
    assert not out.exists()


def test_a_stamp_matching_pair_carries_onto_the_renumbered_surface(tmp_path, monkeypatch):
    prior = tmp_path / "prior"
    unit = {"id": "u-1", "batch": 1, "codepoints": "E650:E652", "configs": ["default"], "window": "w"}
    _write_surface(prior, "2026-07-01T00:00:00Z", [unit])
    current = tmp_path / "current"
    _write_surface(current, "2026-07-02T00:00:00Z", [{**unit, "id": "u-9"}])
    verdicts = tmp_path / "verdicts.json"
    _write_verdicts(
        verdicts,
        "2026-07-01T00:00:00Z",
        [{"unit": "u-1", "verdict": "approve", "note": "", "at": "2026-07-01T01:00:00Z"}],
    )
    out = tmp_path / "out.json"
    _run_carry(monkeypatch, prior, verdicts, out, current)
    payload = json.loads(out.read_text())
    assert payload["manifest_generated_at"] == "2026-07-02T00:00:00Z"
    assert [record["unit"] for record in payload["verdicts"]] == ["u-9"]


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
    """`picture_identical` is a pure function of the window and both fonts' placed glyphs, all of which the key already covers, and it arrived after every archived snapshot was stamped — so it is a presentation key, while `ink_identical` stays inside the key only as the byte-identity contract with those snapshots."""
    unit = _fixture_units()[0]
    assert content_key({**unit, "picture_identical": not unit["picture_identical"]}) == content_key(unit)
