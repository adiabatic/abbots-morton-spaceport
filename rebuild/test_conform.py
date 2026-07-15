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

    def test_isolated_overlay_names_render_ss10_twins(self, spec):
        class Letter:
            cell = CellId("qsIt", "bar", "x-height", "baseline", ())
            seam = None

        class Ligature:
            cell = CellId("qsTea_qsOy", "bar-into-loop", None, "baseline", ())
            seam = None

        class Boundary:
            glyph_name = "uni200C"

        names = conform.isolated_overlay_names(spec, [Letter(), Ligature(), Boundary()])
        assert names == ["qsIt.ss10", "qsTea.ss10", "qsOy.ss10", "uni200C"]


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
                "id": "boundary-echo",
                "match": {"predicate": "boundary_echo", "configs": "all"},
            },
            {
                "id": "zwnj-word-initial-unification",
                "match": {"predicate": "zwnj_word_initial_unification", "configs": "all"},
            },
            {
                "id": "dangling-anchor-dropped",
                "match": {"predicate": "dangling_anchor_dropped", "configs": "all"},
            },
        ]
        assert conform._match_ledger(ledger, row) == ["boundary-echo"]
        namer_dot_row = conform.DivergentRow(
            config="default",
            codepoints="00B7:E652:E670",
            kinds=("cell",),
            position=1,
            baseline_glyphs=("periodcentered", "qsTea.noentry", "qsIt"),
            baseline_seams=("break", "break"),
            new_cells=("periodcentered", "qsTea/full/None/None/", "qsIt/bar/None/None/"),
            new_seams=("break", "break"),
            phenomena=("old-noentry",),
        )
        assert conform._match_ledger(ledger, namer_dot_row) == ["zwnj-word-initial-unification"]

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
            (("-en-ext-1:qsMay", "exit-dropped"), "may-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsDay",), "day-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsDay", "exit-dropped"), "day-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsDay_qsUtter",), "day-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsNo",), None),
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

    def test_boundary_blanket_takes_every_nonposition_row(self):
        """The ratified boundary-equals-word-boundary rule: a window containing a run-splitting boundary (space or ZWNJ) has its cell/seam-grain divergence absorbed ahead of every other class, whatever its phenomena; position-only rows stay on the kern-attribution channel."""
        base = dict(
            config="default",
            kinds=("cell",),
            position=1,
            baseline_glyphs=("space", "qsIt.ex-y5", "qsIt"),
            baseline_seams=("break", "break"),
            new_cells=("uni200C", "qsIt/bar/None/None/locked", "qsIt/bar/None/None/"),
            new_seams=("break", "break"),
        )
        for codepoints in ["200C:E670:E670", "0020:E670:E670"]:
            for phenomena in [
                ("+locked", "old-noentry"),
                ("exit-dropped",),
                ("seam-gain:qsIt", "exit-added"),
                ("seam-loss",),
                ("+en-ext-1",),
                ("ligation",),
            ]:
                row = conform.DivergentRow(**base, codepoints=codepoints, phenomena=phenomena)
                assert conform.classify_divergence(row) == "boundary-echo", (codepoints, phenomena)
            position_row = conform.DivergentRow(
                **{**base, "kinds": ("position",)},
                codepoints=codepoints,
                phenomena=("position-kern-attributable",),
            )
            assert conform.classify_divergence(position_row) is None


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

    def test_ss10_predicate_yields_boundary_rows_to_the_blanket(self):
        for boundary in ("0020", "200C"):
            row = conform.DivergentRow(
                config="ss10",
                codepoints=f"{boundary}:E665:E653",
                kinds=("cell", "seam"),
                position=1,
                baseline_glyphs=("space", "qsMay", "qsDay"),
                baseline_seams=("break", "y5"),
                new_cells=("space", "qsMay/loop/None/None/", "qsDay/full/None/None/"),
                new_seams=("break", "break"),
                phenomena=("seam-loss",),
            )
            assert conform.PREDICATES["ss10_isolation_completed"](row) is False, boundary
            assert conform.classify_divergence(row) == "boundary-echo", boundary

    def test_ss10_ligation_routes_to_ligature_suppressed(self):
        for pair in ("E653:E67A", "E652:E679"):
            row = self._row("ss10", ("ligation",), codepoints=f"E650:{pair}")
            assert conform.classify_divergence(row) == "ss10-ligature-suppressed", pair

    def test_ss10_namer_dot_ligation_outranks_marker_staging(self):
        row = self._row("ss10", ("ligation",), codepoints="00B7:E653:E67A")
        assert conform.classify_divergence(row) == "ss10-ligature-suppressed"

    def test_ss10_ligation_boundary_rows_stay_on_the_blanket(self):
        row = self._row("ss10", ("ligation",), codepoints="200C:E653:E67A")
        assert conform.classify_divergence(row) == "boundary-echo"

    def test_ss10_ligation_without_a_formable_pair_matches_nothing(self):
        row = self._row("ss10", ("ligation",), codepoints="E650:E665:E652")
        assert conform.classify_divergence(row) is None

    def test_non_ss10_ligation_keeps_marker_staging(self):
        row = self._row("ss03", ("ligation",), codepoints="E665:E652:E679")
        assert conform.classify_divergence(row) == "marker-staging-ligature-formation"


class TestConformanceMerge:
    def _result(self, config, **overrides):
        kw = dict(
            config=config,
            sequences=100,
            shaping_runs=100,
            divergences=[],
            uncovered_rules=0,
            uncovered_transitions=0,
            topped_up_rules=0,
            topped_up_sequences=0,
            notes=[],
            modes=[],
        )
        kw.update(overrides)
        return conform.ConformanceConfigResult(**kw)

    def test_sequences_come_from_the_first_result_and_counters_sum(self):
        merged = conform.merge_conformance_results(
            "M1.otf",
            [
                self._result("default", shaping_runs=120, topped_up_rules=2, topped_up_sequences=20),
                self._result("ss02", shaping_runs=110, uncovered_rules=1, uncovered_transitions=3),
            ],
        )
        assert merged.sequences == 100
        assert merged.shaping_runs == 230
        assert merged.topped_up_rules == 2
        assert merged.topped_up_sequences == 20
        assert merged.uncovered_rules == 1
        assert merged.uncovered_transitions == 3
        assert merged.passed is False

    def test_divergences_and_notes_concatenate_in_caller_order(self):
        divergence = conform.Divergence(
            text="", config="ss02", position=0, expected="qsPea", got="qsPea.alt", kind="oracle"
        )
        merged = conform.merge_conformance_results(
            "M1.otf",
            [
                self._result("default", notes=["default: first"]),
                self._result("ss02", notes=["ss02: second"], divergences=[divergence]),
            ],
        )
        assert merged.notes == ["default: first", "ss02: second"]
        assert merged.divergences == [divergence]
        assert merged.passed is False

    def test_modes_union_sorted_after_the_config_notes(self):
        merged = conform.merge_conformance_results(
            "M1.otf",
            [
                self._result("default", notes=["default: note"], modes=["mode-b"]),
                self._result("ss02", modes=["mode-a", "mode-b"]),
            ],
        )
        assert merged.notes == ["default: note", "mode-a", "mode-b"]
        assert merged.passed is True

    def test_empty_results_merge_to_an_empty_pass(self):
        merged = conform.merge_conformance_results("M1.otf", [])
        assert merged.sequences == 0
        assert merged.shaping_runs == 0
        assert merged.passed is True
