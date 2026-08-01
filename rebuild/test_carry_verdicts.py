"""Tests for the cross-surface carry's content key: the identity a prior surface's verdict is re-resolved against when the surface is rebuilt. Everything the rebuild churns — ids, batches, drafts, provenance, the derived group ids, and the per-config ink_deltas map — is presentation and stays out of the key, so a field's first appearance cannot strand the verdicts recorded before it; everything the reviewer actually judged stays in, so a real change to the window loses its old verdict rather than inheriting one. The units are the shipped review fixtures, which the §7 contract checker also gates in test_review_build."""

import json
from pathlib import Path

from rebuild.tools.carry_verdicts import PRESENTATION_KEYS, content_key

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


def test_a_change_to_the_judged_window_moves_the_content_key():
    """The complement, so the exclusions above cannot pass by keying on nothing: the fields the reviewer judges — the window, the configs it covers, and the cells and seams both fonts draw — are all in the key, and moving any of them retires the old verdict instead of carrying it onto a different question."""
    unit = _fixture_units()[0]
    for key, replacement in (
        ("codepoints", "E650:E650"),
        ("configs", ["ss07"]),
        ("after", {**unit["after"], "seams": ["break"]}),
        ("before", {**unit["before"], "seams": ["break"]}),
        ("ink_identical", not unit["ink_identical"]),
    ):
        assert content_key({**unit, key: replacement}) != content_key(unit), key
