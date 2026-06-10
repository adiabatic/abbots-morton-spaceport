"""Conformance-module helper tests: normalization, the raw-pipeline replay, alias/ledger plumbing, kern evaluation, and the subset-identity assertion. The font-facing sweep itself runs at Phase 5 integration (it needs Group 2's settle/table and the compiled mini-font)."""

import gzip

import pytest

from rebuild.pipeline import conform
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId


@pytest.fixture(scope="module")
def spec():
    return mini_spec()


class TestAlphabet:
    def test_eight_symbols(self, spec):
        alphabet = conform.spec_alphabet(spec)
        assert sorted(ord(ch) for ch in alphabet) == [
            0x0020,
            0x00B7,
            0x200C,
            0xE650,
            0xE652,
            0xE665,
            0xE670,
            0xE679,
        ]

    def test_features_for_config(self):
        assert conform.features_for_config("default") == frozenset()
        assert conform.features_for_config("ss02+ss03") == frozenset({"ss02", "ss03"})


class TestNormalization:
    def test_expected_zwnj_sentinel(self):
        assert conform.normalize_expected(["qsIt", "uni200C", "qsTea"]) == [
            "qsIt",
            conform.ZWNJ_SENTINEL,
            "qsTea",
        ]

    def test_settled_names_prefers_glyph_name_attribute(self, spec):
        class WithName:
            glyph_name = "qsIt.ex-y0"

        assert conform.settled_names(spec, [WithName()]) == ["qsIt.ex-y0"]

    def test_settled_names_falls_back_to_display_name(self, spec):
        class WithCell:
            cell = CellId("qsMay", "loop", "baseline", "x-height", ("en-ext-1",))
            seam = None

        assert conform.settled_names(spec, [WithCell()]) == ["qsMay.en-y0.ex-y5.en-ext-1"]

    def test_settled_names_uses_supplied_inventory(self, spec):
        cell = CellId("qsMay", "loop", None, "x-height", ())

        class WithCell:
            pass

        item = WithCell()
        item.cell = cell
        assert conform.settled_names(spec, [item], {cell: "qsMay"}) == ["qsMay"]


PEA, TEA, MAY, IT, OY = chr(0xE650), chr(0xE652), chr(0xE665), chr(0xE670), chr(0xE679)
ZWNJ = chr(0x200C)
DOT = chr(0x00B7)


class TestRawLabels:
    def test_formation_folds_the_ligature(self, spec):
        assert conform.raw_labels(spec, TEA + OY, frozenset()) == ["qsTea_qsOy"]

    def test_zwnj_locks_entry_bearing_followers(self, spec):
        labels = conform.raw_labels(spec, ZWNJ + TEA + IT, frozenset())
        assert labels == ["uni200C", "qsTea.noentry", "qsIt"]

    def test_marker_fold_renames_under_features(self, spec):
        assert conform.raw_labels(spec, MAY + TEA, frozenset({"ss03"})) == ["qsMay", "qsTea.ss03"]

    def test_marker_and_lock_compose(self, spec):
        labels = conform.raw_labels(spec, ZWNJ + TEA, frozenset({"ss02", "ss03"}))
        assert labels == ["uni200C", "qsTea.ss02_ss03.noentry"]

    def test_namer_dot_does_not_lock(self, spec):
        assert conform.raw_labels(spec, DOT + IT, frozenset()) == ["periodcentered", "qsIt"]


class TestAliasAndLedger:
    def test_alias_map_round_trip(self, spec, tmp_path):
        path = tmp_path / "aliases.yaml"
        path.write_text(
            "qsIt.en-y5.ex-y0:\n"
            "  rune: qsIt\n"
            "  stance: bar\n"
            "  entry: x-height\n"
            "  exit: baseline\n"
            "uni200C: boundary\n"
        )
        aliases = conform.load_alias_map(path)
        assert aliases["qsIt.en-y5.ex-y0"] == CellId("qsIt", "bar", "x-height", "baseline", ())
        assert aliases["uni200C"] == "boundary"

    def test_ledger_matching_is_exactly_one(self):
        row = conform.DivergentRow(
            config="default",
            codepoints="200C:E652:E670",
            kinds=("cell",),
            position=1,
            baseline_glyphs=("space", "qsTea.noentry", "qsIt"),
            baseline_seams=("break", "break"),
            new_cells=("uni200C", "qsTea/full/None/None/locked", "qsIt/bar/None/None/"),
            new_seams=("break", "break"),
            phenomena=("+locked", "old-noentry"),
        )
        ledger = [
            {
                "id": "zwnj-word-initial-unification",
                "match": {"predicate": "zwnj_word_initial_unification", "configs": "all"},
            },
            {
                "id": "dangling-anchor-dropped",
                "match": {"predicate": "dangling_anchor_dropped", "configs": "all"},
            },
        ]
        assert conform._match_ledger(ledger, row) == ["zwnj-word-initial-unification"]

    def test_classifier_assigns_each_phenomenon_set_one_class(self):
        base = dict(
            config="default",
            codepoints="E670:E670",
            kinds=("cell",),
            position=0,
            baseline_glyphs=("qsIt.ex-y5", "qsIt"),
            baseline_seams=("break",),
            new_cells=("qsIt/bar/None/None/", "qsIt/bar/None/None/"),
            new_seams=("break",),
        )
        cases = [
            (("exit-dropped",), "dangling-anchor-dropped"),
            (("exit-added", "exit-dropped"), "dangling-anchor-dropped"),
            (("exit-added",), "bare-name-live-join"),
            (("+en-ext-1", "exit-dropped"), "halves-entry-extension-restored"),
            (("-en-ext-1:same-seam",), "same-seam-extension-non-summing"),
            (("+ex-bind-pulled-back", "exit-dropped"), "may-exit-withdrawal-generalized"),
            (("seam-gain:qsIt", "exit-added"), "entered-it-baseline-join-gain"),
            (("seam-gain:qsPea", "entry-dropped"), "pea-chain-regularized"),
            (("seam-gain:qsMay", "seam-loss"), "regrouping-floor-drift"),
            (("seam-loss",), None),
            ((), None),
        ]
        for phenomena, expected in cases:
            row = conform.DivergentRow(**base, phenomena=phenomena)
            assert conform.classify_divergence(row) == expected, phenomena


class TestKernEvaluator:
    def test_family_expansion_and_carve_outs(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text(
            "---\n"
            "left_family: [qsBay]\n"
            "right_family: [qsTea]\n"
            "value: -1\n"
            "---\n"
            "left_stance: [qsNo.alt]\n"
            "right: [qsPea]\n"
            "value: -2\n"
            "---\n"
            "left_family: [qsHe]\n"
            "right_group: noentry\n"
            "value: -3\n"
        )
        evaluator = conform.KernEvaluator(sidecar)
        assert evaluator.value_for("qsBay.en-y0", "qsTea") == -1
        assert evaluator.value_for("qsBay", "qsTea.half.ex-y5") == -1
        assert evaluator.value_for("qsNo.alt.en-y5", "qsPea") == -2
        assert evaluator.value_for("qsNo", "qsPea") == 0
        assert evaluator.value_for("qsHe", "qsMay.noentry") == -3
        assert evaluator.value_for("qsHe", "qsMay") == 0

    def test_global_record(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nglobal: {value: -1}\n")
        evaluator = conform.KernEvaluator(sidecar)
        assert evaluator.value_for("qsPea", "qsTea") == -1

    def test_real_sidecar_parses(self):
        from pathlib import Path

        evaluator = conform.KernEvaluator(
            Path(__file__).resolve().parents[1] / "glyph_data" / "senior_quikscript_kerning.yaml"
        )
        assert isinstance(evaluator.value_for("qsBay", "qsTea"), int)


class TestSubsetIdentity:
    def _write(self, path, rows):
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write("# config: x\n")
            for row in rows:
                fh.write(row + "\n")

    def test_identical_tables_pass(self, tmp_path):
        row = "E670\tqsIt\t0\t\t0,0,150"
        self._write(tmp_path / "baseline-ss06.subset.tsv.gz", [row])
        self._write(tmp_path / "baseline-default.subset.tsv.gz", [row])
        conform.assert_subset_identity(tmp_path, "ss06")

    def test_differing_tables_fail(self, tmp_path):
        self._write(tmp_path / "baseline-ss06.subset.tsv.gz", ["E670\tqsIt\t0\t\t0,0,150"])
        self._write(tmp_path / "baseline-default.subset.tsv.gz", ["E670\tqsIt.x\t0\t\t0,0,150"])
        with pytest.raises(AssertionError):
            conform.assert_subset_identity(tmp_path, "ss06")


class TestPositionChannel:
    def _row(self, codepoints, glyphs, positions):
        from rebuild.validation.rowmodel import Row

        return Row(
            codepoints=tuple(codepoints),
            glyphs=tuple(glyphs),
            clusters=tuple(range(len(glyphs))),
            seams=("break",) * (len(glyphs) - 1),
            positions=tuple(positions),
        )

    def test_kern_normalization_adds_sidecar_kerns_back(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nleft_family: [qsOy]\nright_family: [qsPea]\nvalue: -3\n")
        kern = conform.KernEvaluator(sidecar)
        row = self._row([0xE679, 0xE650], ["qsOy", "qsPea"], [(0, 0, 300), (0, 0, 250)])
        expected, attributable = conform._kern_normalized_positions(kern, row, 50)
        assert expected == ((0, 0, 450), (0, 0, 250))
        assert attributable == (True, False)

    def test_kern_partner_skips_the_zwnj_slot(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nleft_family: [qsOy]\nright_family: [qsPea]\nvalue: -3\n")
        kern = conform.KernEvaluator(sidecar)
        row = self._row(
            [0xE679, 0x200C, 0xE650],
            ["qsOy", "space", "qsPea.noentry"],
            [(0, 0, 300), (0, 0, 0), (0, 0, 250)],
        )
        expected, attributable = conform._kern_normalized_positions(kern, row, 50)
        assert expected == ((0, 0, 450), (0, 0, 0), (0, 0, 250))
        assert attributable == (True, True, False)


class TestClassifierRouting:
    def _row(self, config, phenomena, codepoints="E670:E665:E652"):
        return conform.DivergentRow(
            config=config,
            codepoints=codepoints,
            kinds=("cell", "seam"),
            position=0,
            baseline_glyphs=(),
            baseline_seams=(),
            new_cells=(),
            new_seams=(),
            phenomena=phenomena,
        )

    def test_unentered_it_gain_routes_to_ss03_chain(self):
        phenomena = ("seam-gain:qsIt", "seam-gain-unentered:qsIt")
        assert conform.classify_divergence(self._row("ss03", phenomena)) == "ss03-chain-join-gains"

    def test_unentered_it_gain_outside_ss03_matches_nothing(self):
        phenomena = ("seam-gain:qsIt", "seam-gain-unentered:qsIt")
        assert conform.classify_divergence(self._row("default", phenomena)) is None

    def test_entered_it_gain_keeps_its_class(self):
        assert (
            conform.classify_divergence(self._row("default", ("seam-gain:qsIt", "exit-added")))
            == "entered-it-baseline-join-gain"
        )

    def test_position_drift_never_rides_a_cell_grain_class(self):
        assert conform.classify_divergence(self._row("default", ("exit-dropped", "position-drift"))) is None
