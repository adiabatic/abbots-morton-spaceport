"""Tests for the review surface's ink-identity comparison: the proven census method (uharfbuzz shaping with kerning disabled, DecomposingRecordingPen outlines translated by cumulative advance plus offsets, pieces sorted and compared) reproduces the census facts — u-0000 is ink-identical, the verdict is deterministic, and the full kern-neutral histogram pins 10,083 machine-approved units over the M1-batch-2 workload, concentrated in the name-grain classes whose visible stragglers differ only in the old font's kerning; of the 5,889 non-identical units, the boundary-echo no-verdict exemption leaves 4,010 as human workload."""

from pathlib import Path

import pytest

from rebuild.review.audit import assign_batches, load_workload
from rebuild.review.enrich import LETTERS
from rebuild.review.ink import InkComparator, features_for, kern_neutral
from rebuild.validation.shaping import Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"

MACHINE_BY_CLASS = {
    "boundary-echo": 4465,
    "dangling-anchor-dropped": 4148,
    "bare-name-live-join": 1470,
}


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
    """The kern-neutral census facts the rebatching rests on over the M1-batch-2 workload: 10,083 of 15,972 units are ink-identical under every config in their sets, concentrated in the name-grain classes (boundary-echo, dangling-anchor-dropped, bare-name-live-join) whose visible difference is only the old font's kerning. Of the 5,889 non-identical units, the 1,879 boundary-echo ones are exempt under the ratified boundary rule (no_verdict), leaving 4,010 units of human workload in 14 batches. No verdict family (the UNMATCHED windows) is ink-identical: each is a real new join under review."""
    machine_by_class: dict[str, int] = {}
    for unit in workload.units:
        if comparator.ink_identical(_text(unit), unit.configs):
            unit.ink_identical = True
            machine_by_class[unit.class_id] = machine_by_class.get(unit.class_id, 0) + 1
    assert sum(machine_by_class.values()) == 10083
    assert len(workload.units) - sum(machine_by_class.values()) == 5889
    assert machine_by_class == MACHINE_BY_CLASS
    assert not any(unit.class_id == "UNMATCHED" and unit.ink_identical for unit in workload.units)

    batches = assign_batches(workload.units)
    assert batches == 14
    exempt = [unit for unit in workload.units if unit.no_verdict and not unit.ink_identical]
    assert len(exempt) == 1879
    human = [unit for unit in workload.units if not unit.ink_identical and not unit.no_verdict]
    assert len(human) == 4010
    assert [unit.batch for unit in human] == [index // 300 for index in range(len(human))]
    assert all(unit.batch is None for unit in workload.units if unit.ink_identical or unit.no_verdict)
