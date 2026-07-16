"""Tests for the review surface's ink-identity comparison: the proven census method (uharfbuzz shaping with kerning disabled, DecomposingRecordingPen outlines translated by cumulative advance plus offsets, pieces sorted and compared) reproduces the census facts — u-0000 is ink-identical, the verdict is deterministic, and the full kern-neutral histogram reproduces the machine-approved census over the M1-batch-2 workload at the name-grain (pre-merge) dedupe, concentrated in the name-grain classes whose visible stragglers differ only in the old font's kerning, with the no-verdict exemptions (the boundary-echo blanket plus the two x-height-halves deletion forks) leaving the rest as human workload. Every count is pinned in rebuild/review-census-pins.json (the "ink" group). The built surface then folds ink-duplicate sibling units (merge_ink_duplicate_units), so the shipped manifest's counts are smaller — those are pinned in test_review_build."""

from pathlib import Path

import pytest

from rebuild.review.audit import load_workload
from rebuild.review.census import ink_histogram, load_pins
from rebuild.review.enrich import LETTERS
from rebuild.review.ink import InkComparator, JuniorOracle, features_for, kern_neutral
from rebuild.validation.shaping import Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
JUNIOR_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"

PINS = load_pins()


@pytest.fixture(scope="module")
def workload():
    return load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))


@pytest.fixture(scope="module")
def comparator():
    return InkComparator(BEFORE_FONT, AFTER_FONT)


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def test_features_for_config_tokens():
    assert features_for("default") == {}
    assert features_for(None) == {}
    assert features_for("ss03") == {"ss03": True}
    assert features_for("ss02+ss03+ss05") == {"ss02": True, "ss03": True, "ss05": True}


def test_kern_neutral_always_disables_kern():
    assert kern_neutral(None) == {"kern": False}
    assert kern_neutral({}) == {"kern": False}
    assert kern_neutral({"ss03": True}) == {"ss03": True, "kern": False}
    assert kern_neutral({"kern": True}) == {"kern": False}


def test_u_0126_is_ink_identical_only_because_kerning_is_neutralized(workload, comparator):
    """The worked kern-noise example: ◊ZWNJ ·May·Oy·Pea renders the same ink in both fonts once `kern` is off, and the old font really does kern it (positions move when the feature toggles), so the unit was a kern-only straggler before the census went kern-neutral."""
    unit = next(item for item in workload.units if item.codepoints == "200C:E665:E679:E650")
    assert comparator.ink_identical(_text(unit), unit.configs) is True
    before = Shaper(BEFORE_FONT)
    kerned = before.shape(_text(unit), {**features_for(unit.configs[0]), "kern": True})
    neutral = before.shape(_text(unit), kern_neutral(features_for(unit.configs[0])))
    assert kerned.names == neutral.names
    assert kerned.positions != neutral.positions


def test_u_0000_is_ink_identical(workload, comparator):
    unit = workload.units[0]
    assert unit.unit_id == "u-0000"
    assert unit.codepoints == "0020:E650:E650"
    assert comparator.ink_identical(_text(unit), unit.configs) is True


def test_verdicts_are_deterministic_across_two_comparators(workload, comparator):
    again = InkComparator(BEFORE_FONT, AFTER_FONT)
    sample = workload.units[::200]
    assert [comparator.ink_identical(_text(unit), unit.configs) for unit in sample] == [
        again.ink_identical(_text(unit), unit.configs) for unit in sample
    ]


def test_full_histogram_reproduces_the_census(workload, comparator):
    """The kern-neutral census facts the rebatching rests on over the M1-batch-2 workload at the name-grain (pre-merge) dedupe: the machine-approved units are ink-identical under every config in their sets, concentrated in the name-grain classes (boundary-echo, dangling-anchor-dropped, bare-name-live-join) whose visible difference is only the old font's kerning; the no-verdict share of the non-identical units — the boundary-echo blanket plus the two x-height-halves deletion forks — is exempt, leaving the human workload in its batches. Every count is pinned in rebuild/review-census-pins.json. No verdict family (the UNMATCHED windows) is ink-identical: each is a real new join under review. The built surface additionally folds ink-duplicate siblings; its smaller counts are pinned in test_review_build."""
    pins = PINS["ink"]
    stats = ink_histogram(workload, comparator)
    assert stats["machine_total"] == pins["machine_total"]
    assert stats["non_identical"] == pins["non_identical"]
    assert stats["by_class"] == pins["by_class"]
    assert not any(unit.class_id == "UNMATCHED" and unit.ink_identical for unit in workload.units)

    assert stats["batches"] == pins["batches"]
    assert stats["boundary_echo_exempt"] == pins["boundary_echo_exempt"]
    assert stats["human_units"] == pins["human_units"]
    human = [unit for unit in workload.units if not unit.ink_identical and not unit.no_verdict]
    assert [unit.batch for unit in human] == [index // 300 for index in range(len(human))]
    assert all(unit.batch is None for unit in workload.units if unit.ink_identical or unit.no_verdict)


@pytest.fixture(scope="module")
def oracle():
    return JuniorOracle(JUNIOR_FONT, BEFORE_FONT, AFTER_FONT)


def test_junior_tracking_premise_holds(oracle):
    """The oracle's founding premise, verified at construction and pinned here: Junior carries the same isolated letterforms as Senior plus exactly one pixel (50 units at upem 550) of extra advance on every Quikscript glyph, and no advance difference anywhere else."""
    assert oracle.tracking == 50


def test_junior_oracle_approves_a_suppressed_ligature_unit(oracle):
    """u-14056's text (·No·Day·Utter·Utter, divergent only under ss10 because the old font still formed the ·Day·Utter ligature there): the rebuild's ss10 rendering is Junior's isolated rendering minus the tracking, so the unit is machine-approvable."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("ss10",), text) is True


def test_junior_oracle_only_judges_ss10_only_units(oracle):
    """The oracle's ruling covers exactly the units whose entire divergence is under ss10; a unit also divergent under any other config still needs its other legs judged, so the oracle abstains regardless of the ink."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("default",), text) is False
    assert oracle.approves(("default", "ss10"), text) is False
    assert oracle.approves((), text) is False


def test_junior_oracle_refuses_the_lowered_namer_dot(oracle):
    """The known counterexample from the current surface (the five `· ◊ZWNJ ·X·Y` boundary windows): Junior renders the namer dot lowered (periodcentered.lowered) where the rebuild's ss10 run draws the plain dot, so the placed ink differs and the oracle correctly leaves the unit for human eyes."""
    text = "".join(chr(value) for value in (0x00B7, 0x200C, 0xE666, 0xE653))
    assert oracle.approves(("ss10",), text) is False
