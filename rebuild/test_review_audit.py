"""Tests for the review surface's M1-mode unit assembly: TSV/ledger loading, the dedupe to 2,411 units, exemplar resolution, and deterministic triage ordering."""

from pathlib import Path

import pytest

from rebuild.review.audit import (
    AuditRow,
    assign_batches,
    build_units,
    load_audit,
    load_ledger,
    load_workload,
    parse_codepoints,
    render_groups_for_rows,
)
from rebuild.review.enrich import LETTERS

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"

FIXTURE_AUDIT = """config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew
default\tE650:E665\tcell\tdangling-anchor-dropped\tqsPea|qsMay.en-y0\tqsPea/full/None/baseline/|qsMay/loop/baseline/None/
ss02\tE650:E665\tcell\tdangling-anchor-dropped\tqsPea|qsMay.en-y0\tqsPea/full/None/baseline/|qsMay/loop/baseline/None/
default\tE652:E670\tcell,seam\thalves-entry-extension-restored\tqsTea.half.ex-y5|qsIt.en-y5\tqsTea/half/None/x-height/|qsIt/bar/x-height/None/en-ext-1
"""


@pytest.fixture(scope="module")
def workload():
    return load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))


def test_load_audit_parses_fixture(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    rows = load_audit(path)
    assert len(rows) == 3
    assert rows[0].config == "default"
    assert rows[0].baseline == ("qsPea", "qsMay.en-y0")
    assert rows[2].kinds == ("cell", "seam")


def test_load_audit_rejects_wrong_header(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text("nope\tnope\n")
    with pytest.raises(ValueError):
        load_audit(path)


def test_fixture_units_dedupe_and_carry_configs(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    units = build_units(load_audit(path), load_ledger(LEDGER_PATH), dict(LETTERS))
    assert len(units) == 2
    by_codepoints = {unit.codepoints: unit for unit in units}
    assert by_codepoints["E650:E665"].configs == ("default", "ss02")
    assert by_codepoints["E652:E670"].kinds == ("cell", "seam")


def test_conflicting_class_assignment_raises():
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "class-a", ("a",), ("b",)),
        AuditRow("ss02", "E650:E665", ("cell",), "class-b", ("a",), ("b",)),
    ]
    with pytest.raises(ValueError, match="multiple ledger classes"):
        build_units(rows, load_ledger(LEDGER_PATH), dict(LETTERS))


def test_render_groups_split_by_rendered_outcome_identity():
    rows = (
        AuditRow("default", "E650:E665", ("cell",), "x", ("qsPea",), ("qsPea/full/None/None/",)),
        AuditRow("ss02", "E650:E665", ("cell",), "x", ("qsPea",), ("qsPea/half/None/None/",)),
        AuditRow("ss03", "E650:E665", ("cell",), "x", ("qsPea",), ("qsPea/full/None/None/",)),
    )
    assert render_groups_for_rows(rows) == (("default", "ss03"), ("ss02",))


def test_every_real_unit_has_exactly_one_render_group(workload):
    """The M1 invariant of the dedupe key: a unit's rows share (codepoints, baseline, new), so the per-config rendered outcomes can never differ within a unit. If this ever fails, the data violates the dedupe key's documented guarantee and the extra groups must render stacked, never collapsed."""
    for unit in workload.units:
        assert unit.render_groups == (unit.configs,)


def test_real_audit_dedupes_to_measured_counts(workload):
    assert workload.row_count == 15525
    assert len(workload.units) == 2410
    assert sum(len(unit.rows) for unit in workload.units) == 15525


def test_every_ledger_exemplar_resolves_to_a_unit(workload):
    exemplar_keys = {key for entry in workload.ledger for key in entry.exemplar_keys}
    assert len(exemplar_keys) == 18
    covered = {(row.config, row.codepoints) for unit in workload.units if unit.exemplar for row in unit.rows}
    assert exemplar_keys <= covered


def test_triage_order_follows_ledger_then_group_then_codepoints(workload):
    class_order = {entry.id: index for index, entry in enumerate(workload.ledger)}
    indices = [class_order[unit.class_id] for unit in workload.units]
    assert indices == sorted(indices)
    by_class: dict[str, list] = {}
    for unit in workload.units:
        by_class.setdefault(unit.class_id, []).append(unit)
    for units in by_class.values():
        groups = [unit.group for unit in units]
        first_seen: dict[str, int] = {}
        for index, group in enumerate(groups):
            first_seen.setdefault(group, index)
        for left, right in zip(groups, groups[1:]):
            if left != right:
                assert first_seen[left] < first_seen[right], "groups must form contiguous ordered runs"
        for left, right in zip(units, units[1:]):
            if left.group == right.group:
                assert (len(left.codepoint_values), left.codepoint_values) <= (
                    len(right.codepoint_values),
                    right.codepoint_values,
                )


def test_unit_ids_are_sequential_and_batches_unassigned_until_ink_is_known(workload):
    for index, unit in enumerate(workload.units):
        assert unit.unit_id == f"u-{index:04d}"
        assert unit.batch is None
        assert unit.ink_identical is False


def test_assign_batches_slices_the_human_workload_and_nulls_machine_units(workload):
    for index, unit in enumerate(workload.units):
        unit.ink_identical = index % 3 == 0
    try:
        total = assign_batches(workload.units, batch_size=300)
        human = [unit for unit in workload.units if not unit.ink_identical]
        assert [unit.batch for unit in human] == [index // 300 for index in range(len(human))]
        assert all(unit.batch is None for unit in workload.units if unit.ink_identical)
        assert total == (len(human) + 299) // 300
    finally:
        for unit in workload.units:
            unit.ink_identical = False
            unit.batch = None


def test_ordering_is_deterministic(workload):
    again = load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))
    assert [unit.unit_id for unit in again.units] == [unit.unit_id for unit in workload.units]
    assert [unit.codepoints for unit in again.units] == [unit.codepoints for unit in workload.units]


def test_configs_within_a_unit_are_in_acceptance_order(workload):
    order = {
        token: index
        for index, token in enumerate(
            ("default", "ss02", "ss03", "ss04", "ss05", "ss02+ss03", "ss02+ss03+ss05", "ss10")
        )
    }
    for unit in workload.units:
        ranks = [order[config] for config in unit.configs]
        assert ranks == sorted(ranks)


def test_parse_codepoints():
    assert parse_codepoints("200C:E652:E679") == (0x200C, 0xE652, 0xE679)
