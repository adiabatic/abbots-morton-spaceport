"""Defect-gate tests over the fixture spec, with lightweight duck-typed decision/treaty tables standing in for Group 2's."""

from dataclasses import dataclass, field

import pytest

from rebuild.pipeline import defects, geometry
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan, GlyphRecord


@dataclass(frozen=True)
class FakeRule:
    input_glyph: str = "qsIt"
    backtrack: tuple = None
    look1: tuple = None
    look2: tuple = None
    outcome: str = "qsIt"
    joint: bool = False
    provenance: tuple = ()


@dataclass(frozen=True)
class FakeTreatyRow:
    left: CellId | None
    right: CellId | None
    join: str | None
    extension: int = 0
    provenance: tuple = ()


@dataclass
class FakeDecision:
    rules: list = field(default_factory=list)
    cells: frozenset = frozenset()

    def reachable_cells(self):
        return self.cells


@dataclass
class FakeTreaty:
    rows: tuple = ()


@pytest.fixture(scope="module")
def spec():
    return mini_spec()


def _realize(spec, rune, stance, entry, exit, adjustments=(), **kwargs):
    cell = CellId(rune, stance, entry, exit, tuple(adjustments))
    return cell, geometry.realize(spec, CellPlan(cell=cell, **kwargs))


def _tables(rules=(), rows=()):
    return {frozenset(): (FakeDecision(rules=list(rules)), FakeTreaty(rows=tuple(rows)))}


def _cite_all_policy(spec):
    """Provenance strings for every fixture policy record and scoped row, so dead-policy noise stays out of unrelated tests."""
    cited = []
    for rune in spec.runes.values():
        for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
            for record in getattr(rune.policy, kind):
                cited.append(str(record.provenance) if record.provenance else f"{rune.name}.policy.{kind}")
        for stance in rune.stances.values():
            for side, rows in (("entries", stance.surface.entries), ("exits", stance.surface.exits)):
                for height, row in rows.items():
                    if row.scope:
                        cited.append(f"{rune.name}.{stance.name}.{side}.{height}.scope")
            for unlock in stance.surface.unlocks:
                cited.append(str(unlock.provenance) if unlock.provenance else "")
    return FakeRule(provenance=tuple(cited))


class TestDangle:
    def test_unsafe_withdrawal_claim_fails(self, spec):
        cell, record = _realize(
            spec, "qsMay", "loop", None, "x-height", safety_checks=(("exit", "x-height"),)
        )
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record})
        assert any(d.code == "E-DANGLE" for d in report.errors)

    def test_safe_withdrawal_passes(self, spec):
        cell, record = _realize(spec, "qsIt", "hapax", None, None, safety_checks=(("exit", "baseline"),))
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record})
        assert not [d for d in report.errors if d.code == "E-DANGLE"]

    def test_allow_channel_blesses_by_signature(self, spec):
        cell, record = _realize(
            spec, "qsMay", "loop", None, "x-height", safety_checks=(("exit", "x-height"),)
        )
        signature = f"dangle:{record.name}:exit:x-height"
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record}, allow=frozenset({signature})
        )
        assert not report.errors
        assert any(d.signature == signature for d in report.blessed)


class TestAnchorConvention:
    def test_convention_drift_is_an_error(self, spec):
        cell = CellId("qsIt", "hapax", None, "baseline", ())
        record = GlyphRecord(name="qsIt.bad", bitmap=("#",) * 6, y_offset=0, exit=(2, 0))
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record})
        assert any(d.code == "E-ANCHOR" for d in report.errors)

    def test_exempt_side_is_skipped(self, spec):
        cell = CellId("qsIt", "hapax", None, "baseline", ("ex-trim-1",))
        record = GlyphRecord(
            name="qsIt.trimmed", bitmap=("#",) * 6, y_offset=0, exit=(2, 0), convention_exempt=("exit",)
        )
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record})
        assert not [d for d in report.errors if d.code == "E-ANCHOR"]

    def test_ink_y_fallback_satisfies_the_exit_convention(self, spec):
        cell, record = _realize(spec, "qsPea", "half", None, "x-height", exit_ink_y=6)
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record})
        assert not [d for d in report.errors if d.code == "E-ANCHOR"]


class TestUnrealized:
    def test_gap_zero_join_passes(self, spec):
        left_cell, left = _realize(spec, "qsIt", "hapax", None, "baseline")
        right_cell, right = _realize(spec, "qsMay", "loop", "baseline", "x-height")
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="baseline", extension=0)]
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), {left_cell: left, right_cell: right}
        )
        assert not [d for d in report.errors if d.code == "E-UNREALIZED"]

    def test_nonzero_gap_fails(self, spec):
        left_cell = CellId("qsIt", "hapax", None, "baseline", ())
        left = GlyphRecord(
            name="qsIt.gappy",
            bitmap=("#", "#", "#", "#", "#", "# "),
            y_offset=0,
            exit=(2, 0),
            convention_exempt=("exit",),
        )
        right_cell, right = _realize(spec, "qsMay", "loop", "baseline", "x-height")
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="baseline", extension=0)]
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), {left_cell: left, right_cell: right}
        )
        assert any(d.code == "E-UNREALIZED" for d in report.errors)


class TestExtensionBand:
    def test_extension_within_band_passes(self, spec):
        left_cell, left = _realize(spec, "qsMay", "loop", None, "x-height", ["ex-ext-1"])
        right_cell, right = _realize(spec, "qsIt", "hapax", "x-height", "baseline")
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="x-height", extension=1)]
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), {left_cell: left, right_cell: right}
        )
        assert not [d for d in report.errors if d.code == "E-EXTENSION-BAND"]
        assert not [d for d in report.flags if d.code == "E-EXTENSION-BAND"]

    def test_extension_beyond_every_band_flags(self, spec):
        left_cell, left = _realize(spec, "qsMay", "loop", None, "x-height", ["ex-ext-2"])
        right_cell, right = _realize(spec, "qsIt", "hapax", "x-height", "baseline", ["en-ext-1"])
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="x-height", extension=3)]
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), {left_cell: left, right_cell: right}
        )
        assert any(d.code == "E-EXTENSION-BAND" for d in report.flags)

    def test_extension_with_no_authored_record_fails(self, spec):
        left_cell, left = _realize(spec, "qsTea_qsOy", "hapax", None, "baseline", ["ex-ext-1"])
        right_cell, right = _realize(spec, "qsOy", "hapax", None, None)
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="baseline", extension=1)]
        glyphs = {left_cell: left, right_cell: right}
        right_entry = GlyphRecord(name=right.name, bitmap=right.bitmap, y_offset=right.y_offset, entry=(0, 0))
        glyphs[right_cell] = right_entry
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), glyphs)
        assert any(d.code == "E-EXTENSION-BAND" for d in report.errors)


class TestContact:
    def test_overlapping_ink_fails(self, spec):
        left_cell = CellId("qsIt", "hapax", None, "baseline", ())
        right_cell = CellId("qsMay", "loop", "baseline", None, ())
        left = GlyphRecord(name="l", bitmap=("##",), y_offset=0, exit=(1, 0), convention_exempt=("exit",))
        right = GlyphRecord(name="r", bitmap=("##",), y_offset=0, entry=(0, 0), convention_exempt=("entry",))
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="baseline")]
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), {left_cell: left, right_cell: right}
        )
        assert any(d.code == "E-CONTACT" for d in report.errors)
        assert any(d.code == "E-UNREALIZED" for d in report.errors)

    def test_clean_join_has_no_contact(self, spec):
        left_cell, left = _realize(spec, "qsTea", "full", None, "baseline")
        right_cell, right = _realize(spec, "qsMay", "loop", "baseline", "x-height")
        rows = [FakeTreatyRow(left=left_cell, right=right_cell, join="baseline", extension=0)]
        report = defects.run_gates(
            spec, _tables(rules=[_cite_all_policy(spec)], rows=rows), {left_cell: left, right_cell: right}
        )
        assert not [d for d in report.errors if d.code == "E-CONTACT"]


class TestDeadPolicy:
    def test_cited_records_are_live(self, spec):
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {})
        assert not report.dead_in_alphabet

    def test_deferred_partner_records_are_partitioned(self, spec):
        report = defects.run_gates(spec, _tables(), {})
        deferred = "\n".join(report.deferred_partner)
        # The fixture's qsMay contract names only qsFee, absent from the fixture spec — the batch-1 deferred-partner shape, preserved as the exemplar; the real qsMay binds pulled-back-stubless on its entry row instead of carrying the contract.
        assert "qsMay.yaml:policy.contract[0]" in deferred

    def test_in_alphabet_unexercised_records_land_in_dead_list(self, spec):
        report = defects.run_gates(spec, _tables(), {})
        dead = "\n".join(report.dead_in_alphabet)
        # qsIt's self-scoped baseline-exit extension names modeled families, so silence is dead policy, not deferral.
        assert "qsIt.yaml:policy.extend[2]" in dead

    def test_fail_if_broken_raises(self, spec):
        cell, record = _realize(
            spec, "qsMay", "loop", None, "x-height", safety_checks=(("exit", "x-height"),)
        )
        report = defects.run_gates(spec, _tables(rules=[_cite_all_policy(spec)]), {cell: record})
        with pytest.raises(AssertionError):
            report.fail_if_broken()
