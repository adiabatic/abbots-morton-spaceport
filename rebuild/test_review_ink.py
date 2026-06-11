"""Tests for the review surface's ink-identity comparison: the proven census method (uharfbuzz shaping, DecomposingRecordingPen outlines translated by cumulative advance plus offsets, pieces sorted and compared) reproduces the census facts — u-0000 is ink-identical, may-exit-withdrawal-generalized units are not, the verdict is deterministic, and the full histogram pins 1,549 machine-approved / 862 human units with the three name-grain per-class splits."""

from pathlib import Path

import pytest

from rebuild.review.audit import assign_batches, load_workload
from rebuild.review.enrich import LETTERS
from rebuild.review.ink import InkComparator, features_for

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"

INK_CLASS_SPLITS = {
    "dangling-anchor-dropped": (1218, 1334),
    "zwnj-word-initial-unification": (206, 213),
    "bare-name-live-join": (125, 139),
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


def test_u_0000_is_ink_identical(workload, comparator):
    unit = workload.units[0]
    assert unit.unit_id == "u-0000"
    assert unit.codepoints == "E650:200C:E650:E665"
    assert comparator.ink_identical(_text(unit), unit.configs) is True


def test_a_may_exit_withdrawal_unit_is_visibly_different(workload, comparator):
    units = [unit for unit in workload.units if unit.class_id == "may-exit-withdrawal-generalized"]
    assert units
    unit = units[0]
    assert comparator.ink_identical(_text(unit), unit.configs) is False


def test_verdicts_are_deterministic_across_two_comparators(workload, comparator):
    again = InkComparator(BEFORE_FONT, AFTER_FONT)
    sample = workload.units[::200]
    assert [comparator.ink_identical(_text(unit), unit.configs) for unit in sample] == [
        again.ink_identical(_text(unit), unit.configs) for unit in sample
    ]


def test_full_histogram_reproduces_the_census(workload, comparator):
    """The census facts the rebatching rests on: 1,549 of 2,411 units are ink-identical under every config in their sets, all inside the three name-grain classes, leaving 862 units of human workload."""
    machine_by_class: dict[str, int] = {}
    total_by_class: dict[str, int] = {}
    for unit in workload.units:
        total_by_class[unit.class_id] = total_by_class.get(unit.class_id, 0) + 1
        if comparator.ink_identical(_text(unit), unit.configs):
            unit.ink_identical = True
            machine_by_class[unit.class_id] = machine_by_class.get(unit.class_id, 0) + 1
    assert sum(machine_by_class.values()) == 1549
    assert len(workload.units) - sum(machine_by_class.values()) == 862
    assert machine_by_class == {class_id: split[0] for class_id, split in INK_CLASS_SPLITS.items()}
    for class_id, (machine, total) in INK_CLASS_SPLITS.items():
        assert total_by_class[class_id] == total, class_id
        assert total - machine > 0, f"{class_id} must keep visible stragglers for human eyes"

    batches = assign_batches(workload.units)
    assert batches == 3
    human = [unit for unit in workload.units if not unit.ink_identical]
    assert [unit.batch for unit in human] == [index // 300 for index in range(len(human))]
    assert all(unit.batch is None for unit in workload.units if unit.ink_identical)
