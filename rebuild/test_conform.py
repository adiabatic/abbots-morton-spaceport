"""Conformance-module helper tests: normalization, the raw-pipeline replay, alias/ledger plumbing, kern evaluation, the subset-identity assertion, and the memoized settled-window walk's equivalence to the unmemoized pair it replaced. The font-facing sweep itself runs in run_m1 (it needs settle/table and the compiled mini-font)."""

import gzip
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from rebuild.pipeline import conform
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId


@pytest.fixture(scope="module")
def spec():
    return mini_spec()


class TestAlphabet:
    def test_twelve_symbols(self, spec):
        alphabet = conform.spec_alphabet(spec)
        assert sorted(ord(ch) for ch in alphabet) == [
            0x0020,
            0x00B7,
            0x200C,
            0xE650,
            0xE652,
            0xE653,
            0xE65A,
            0xE665,
            0xE667,
            0xE670,
            0xE679,
            0xE67A,
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
            cell: CellId

        item = WithCell()
        item.cell = cell
        assert conform.settled_names(spec, [item], {cell: "qsMay"}) == ["qsMay"]

    def test_isolated_overlay_names_render_ss10_twins(self, spec):
        class Letter:
            cell = CellId("qsIt", "hapax", "x-height", "baseline", ())
            seam = None

        class Ligature:
            cell = CellId("qsTea_qsOy", "hapax", None, "baseline", ())
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
            "  stance: hapax\n"
            "  entry: x-height\n"
            "  exit: baseline\n"
            "uni200C: boundary\n"
            "qsPea: pending\n"
        )
        aliases = conform.load_alias_map(path)
        assert aliases["qsIt.en-y5.ex-y0"] == CellId("qsIt", "hapax", "x-height", "baseline", ())
        assert aliases["uni200C"] == "boundary"
        assert aliases["qsPea"] == "pending"

    def test_ledger_matching_is_exactly_one(self):
        row = conform.DivergentRow(
            config="default",
            codepoints="200C:E652:E670",
            kinds=("cell",),
            position=1,
            baseline_glyphs=("space", "qsTea.noentry", "qsIt"),
            baseline_seams=("break", "break"),
            new_cells=("uni200C", "qsTea/full/None/None/locked", "qsIt/hapax/None/None/"),
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
            new_cells=("periodcentered", "qsTea/full/None/None/", "qsIt/hapax/None/None/"),
            new_seams=("break", "break"),
            phenomena=("old-noentry",),
        )
        assert conform._match_ledger(ledger, namer_dot_row) == ["zwnj-word-initial-unification"]

    def test_classifier_assigns_each_phenomenon_set_one_class(self):
        base = conform.DivergentRow(
            config="default",
            codepoints="E670:E670",
            kinds=("cell",),
            position=0,
            baseline_glyphs=("qsIt.ex-y5", "qsIt"),
            baseline_seams=("break",),
            new_cells=("qsIt/hapax/None/None/", "qsIt/hapax/None/None/"),
            new_seams=("break",),
        )
        cases: list[tuple[tuple[str, ...], str | None]] = [
            (("exit-dropped",), "dangling-anchor-dropped"),
            (("exit-added", "exit-dropped"), "dangling-anchor-dropped"),
            (("exit-added",), "bare-name-live-join"),
            (("+en-ext-1", "exit-dropped"), "halves-entry-extension-restored"),
            (("-en-ext-1:same-seam",), "same-seam-extension-non-summing"),
            (("-en-ext-1:qsMay", "exit-dropped"), "may-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsDay",), "day-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsDay", "exit-dropped"), "day-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsDay_qsUtter",), "day-baseline-entry-extension-dropped"),
            (("-en-ext-1:qsNo",), "no-xheight-entry-extension-dropped"),
            (("-en-ext-1:qsNo", "exit-added"), "no-xheight-entry-extension-dropped"),
            (("+ex-bind-pulled-back", "exit-dropped"), None),
            (("seam-gain:qsIt", "exit-added"), "entered-it-baseline-join-gain"),
            (("seam-gain:qsPea", "entry-dropped"), "pea-chain-regularized"),
            (("seam-gain:qsMay", "seam-loss"), "regrouping-floor-drift"),
            (("seam-loss",), None),
            ((), None),
        ]
        for phenomena, expected in cases:
            row = replace(base, phenomena=phenomena)
            assert conform.classify_divergence(row) == expected, phenomena

    def test_boundary_blanket_takes_every_nonposition_row(self):
        """The ratified boundary-equals-word-boundary rule: a window containing a run-splitting boundary (space or ZWNJ) has its cell/seam-grain divergence absorbed ahead of every other class, whatever its phenomena; position-only rows stay on the kern-attribution channel."""
        for codepoints in ["200C:E670:E670", "0020:E670:E670"]:
            base = conform.DivergentRow(
                config="default",
                codepoints=codepoints,
                kinds=("cell",),
                position=1,
                baseline_glyphs=("space", "qsIt.ex-y5", "qsIt"),
                baseline_seams=("break", "break"),
                new_cells=("uni200C", "qsIt/hapax/None/None/locked", "qsIt/hapax/None/None/"),
                new_seams=("break", "break"),
            )
            for phenomena in [
                ("+locked", "old-noentry"),
                ("exit-dropped",),
                ("seam-gain:qsIt", "exit-added"),
                ("seam-loss",),
                ("+en-ext-1",),
                ("ligation",),
            ]:
                row = replace(base, phenomena=phenomena)
                assert conform.classify_divergence(row) == "boundary-echo", (codepoints, phenomena)
            position_row = replace(base, kinds=("position",), phenomena=("position-kern-attributable",))
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


class TestAliasCompleteness:
    def _write(self, path, rows):
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write("# config: x\n")
            for row in rows:
                fh.write(row + "\n")

    def _aliases(self, tmp_path):
        path = tmp_path / "aliases.yaml"
        path.write_text("qsIt: {rune: qsIt, stance: hapax}\nqsTea.noentry: pending\n")
        return path

    def test_known_pending_and_boundary_names_resolve(self, tmp_path):
        self._write(
            tmp_path / "baseline-default.subset.tsv.gz",
            [
                "0020:E670\tspace|qsIt\t0,1\tbreak\t0,0,150|0,0,150",
                "E652\tqsTea.noentry\t0\t\t0,0,150",
            ],
        )
        assert conform.unaliased_subset_names(tmp_path, self._aliases(tmp_path)) == {}

    def test_missing_names_are_reported_with_their_configs(self, tmp_path):
        self._write(
            tmp_path / "baseline-default.subset.tsv.gz",
            ["E650:E670\tqsPea.ex-y0|qsIt\t0,1\tbreak\t0,0,150|0,0,150"],
        )
        self._write(tmp_path / "baseline-ss03.subset.tsv.gz", ["E650\tqsPea.ex-y0\t0\t\t0,0,150"])
        assert conform.unaliased_subset_names(tmp_path, self._aliases(tmp_path)) == {
            "qsPea.ex-y0": ["default", "ss03"]
        }

    def test_pending_alias_reads_as_unaliased_in_the_comparison(self, spec):
        from rebuild.pipeline import settle as settle_module
        from rebuild.validation.rowmodel import Row

        row = Row(codepoints=(0xE652,), glyphs=("qsTea",), clusters=(0,), seams=(), positions=((0, 0, 150),))
        divergent = conform._compare_row(
            spec, settle_module, {"qsTea": "pending"}, "default", frozenset(), row
        )
        assert divergent is not None
        assert "unaliased" in divergent.kinds
        assert "unaliased:qsTea" in divergent.phenomena
        assert divergent == conform._compare_row(spec, settle_module, {}, "default", frozenset(), row)


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
    def _result(
        self,
        config: str,
        sequences: int = 100,
        shaping_runs: int = 100,
        divergences: Sequence[conform.Divergence] = (),
        uncovered_rules: int = 0,
        uncovered_transitions: int = 0,
        topped_up_rules: int = 0,
        topped_up_sequences: int = 0,
        notes: Sequence[str] = (),
        modes: Sequence[str] = (),
    ) -> conform.ConformanceConfigResult:
        return conform.ConformanceConfigResult(
            config=config,
            sequences=sequences,
            shaping_runs=shaping_runs,
            divergences=list(divergences),
            uncovered_rules=uncovered_rules,
            uncovered_transitions=uncovered_transitions,
            topped_up_rules=topped_up_rules,
            topped_up_sequences=topped_up_sequences,
            notes=list(notes),
            modes=list(modes),
        )

    def test_sequences_come_from_the_first_result_and_counters_sum(self):
        merged = conform.merge_conformance_results(
            Path("M1.otf"),
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
            Path("M1.otf"),
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
            Path("M1.otf"),
            [
                self._result("default", notes=["default: note"], modes=["mode-b"]),
                self._result("ss02", modes=["mode-a", "mode-b"]),
            ],
        )
        assert merged.notes == ["default: note", "mode-a", "mode-b"]
        assert merged.passed is True

    def test_empty_results_merge_to_an_empty_pass(self):
        merged = conform.merge_conformance_results(Path("M1.otf"), [])
        assert merged.sequences == 0
        assert merged.shaping_runs == 0
        assert merged.passed is True


class TestWitnessCacheFile:
    def test_round_trip_including_space_and_zwnj_texts(self, tmp_path):
        path = conform.witnesses_path(tmp_path, "default")
        rules = {0: PEA + TEA + MAY, 7: " " + ZWNJ + IT}
        windows = {
            ("qsPea", "#EDGE", "qsTea", "#NA", "#NA", "#NA"): PEA + TEA,
            ("qsIt", "space", "qsMay", "qsTea", "#NA", "#NA"): " " + IT + MAY + TEA,
        }
        conform.write_witnesses(path, "digest-a", rules, windows)
        assert conform.read_witnesses(path, "digest-a") == (rules, windows)

    def test_another_tables_digest_reads_as_empty(self, tmp_path):
        path = conform.witnesses_path(tmp_path, "default")
        conform.write_witnesses(path, "digest-a", {0: PEA}, {})
        assert conform.read_witnesses(path, "digest-b") == ({}, {})

    def test_a_missing_or_garbled_file_reads_as_empty(self, tmp_path):
        path = conform.witnesses_path(tmp_path, "default")
        assert conform.read_witnesses(path, "digest-a") == ({}, {})
        path.write_bytes(b"not a cache")
        assert conform.read_witnesses(path, "digest-a") == ({}, {})

    def test_two_writes_of_one_hunt_are_byte_identical(self, tmp_path):
        first, second = tmp_path / "first.tsv.gz", tmp_path / "second.tsv.gz"
        for path in (first, second):
            conform.write_witnesses(
                path, "digest-a", {3: PEA + MAY}, {("qsPea", "#EDGE", "#NA", "#NA", "#NA", "#NA"): PEA}
            )
        assert first.read_bytes() == second.read_bytes()


class TestProvenBoundaryHorizon:
    def _summary(self, tmp_path, font_bytes=b"font", overrides=None):
        import hashlib
        import json

        font = tmp_path / "M1.otf"
        font.write_bytes(font_bytes)
        summary = {
            "pass": True,
            "max_length": 5,
            "configs": list(conform.ACCEPTANCE_CONFIGS),
            "font_sha256": hashlib.sha256(b"font").hexdigest(),
        }
        summary.update(overrides or {})
        path = tmp_path / "boundary_equivalence_summary.json"
        path.write_text(json.dumps(summary))
        return font, path

    def test_a_green_summary_for_these_font_bytes_hands_over_its_horizon(self, tmp_path):
        font, path = self._summary(tmp_path)
        assert conform.proven_boundary_horizon(font, path) == 5

    def test_a_red_summary_declines(self, tmp_path):
        font, path = self._summary(tmp_path, overrides={"pass": False})
        assert conform.proven_boundary_horizon(font, path) is None

    def test_a_rebuilt_font_declines(self, tmp_path):
        font, path = self._summary(tmp_path, font_bytes=b"rebuilt since")
        assert conform.proven_boundary_horizon(font, path) is None

    def test_a_summary_short_a_requested_config_declines(self, tmp_path):
        font, path = self._summary(tmp_path, overrides={"configs": ["default"]})
        assert conform.proven_boundary_horizon(font, path) is None
        assert conform.proven_boundary_horizon(font, path, configs=("default",)) == 5

    def test_a_summary_predating_the_provenance_keys_declines(self, tmp_path):
        font, path = self._summary(tmp_path)
        import json

        recorded = json.loads(path.read_text())
        for key in ("max_length", "configs", "font_sha256"):
            recorded.pop(key)
        path.write_text(json.dumps(recorded))
        assert conform.proven_boundary_horizon(font, path) is None

    def test_a_missing_summary_declines(self, tmp_path):
        font = tmp_path / "M1.otf"
        font.write_bytes(b"font")
        assert conform.proven_boundary_horizon(font, tmp_path / "absent.json") is None


class TestBoundarySummaryProvenance:
    def test_merged_boundary_results_carry_the_proof_the_conform_sweep_leans_on(self, tmp_path):
        import json

        font = tmp_path / "M1.otf"
        font.write_bytes(b"font")
        results = [conform.BoundaryConfigResult(config=config) for config in conform.ACCEPTANCE_CONFIGS]
        report = conform.merge_boundary_results(font, results, max_length=5)
        summary_path = tmp_path / "boundary_equivalence_summary.json"
        report.write(summary_path)
        recorded = json.loads(summary_path.read_text())
        assert recorded["max_length"] == 5
        assert recorded["configs"] == list(conform.ACCEPTANCE_CONFIGS)
        assert conform.proven_boundary_horizon(font, summary_path) == 5

    def test_a_conformance_summary_stays_in_its_established_shape(self, tmp_path):
        import json

        report = conform.merge_conformance_results(Path("M1.otf"), [])
        path = tmp_path / "conform_summary.json"
        report.write(path)
        recorded = json.loads(path.read_text())
        assert not {"max_length", "configs", "font_sha256"} & set(recorded)


class _SilentShaper:
    """Enough of a Shaper for the coverage machinery, which never reads shaped output: every text shapes to nothing, so the oracle records one length divergence per text and the structural checks see no slots. The texts it was asked to shape are the observable."""

    def __init__(self):
        self.shaped: list[str] = []

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        self.shaped.append(text)
        return []

    def has_ink(self, glyph_name: str) -> bool:
        return False

    def outline_signature(self, glyph_name: str) -> tuple:
        return ()


class TestTopUpEconomics:
    """The issue-13 levers over a real decision table (mini spec, horizon short enough that plenty of windows outlive the sweep) with the font faked out: dedupe of the hunt's candidate texts, the recorded-witness warm start, and the boundary gate's structural checks inherited within its proven horizon."""

    HORIZON = 2

    @pytest.fixture(scope="class")
    def decision(self, spec):
        from rebuild.pipeline.table import build_tables

        return build_tables(spec, frozenset())[0]

    def _run(self, spec, decision, boundary_horizon=None, witness_cache=None):
        from rebuild.pipeline.settle import cell_label

        shaper = _SilentShaper()
        result = conform._conformance_config(
            shaper,  # pyright: ignore[reportArgumentType]
            spec,
            "default",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            {cell: cell_label(spec, cell) for cell in decision.reachable_cells()},
            None,
            self.HORIZON,
            decision=decision,
            boundary_horizon=boundary_horizon,
            witness_cache=witness_cache,
        )
        return result, shaper

    def test_no_text_sweeps_twice_and_top_ups_count_distinct_texts(self, spec, decision, monkeypatch):
        monkeypatch.setattr(conform, "check_split_buffer", lambda *args, **kwargs: None)
        result, shaper = self._run(spec, decision)
        assert result.topped_up_sequences > 0
        assert len(shaper.shaped) == len(set(shaper.shaped))
        assert result.shaping_runs == result.sequences + result.topped_up_sequences
        assert result.uncovered_rules == 0
        assert result.uncovered_transitions == 0

    def test_a_recorded_hunt_spares_the_next_run_the_assembly(self, spec, decision, monkeypatch, tmp_path):
        assemblies: list[int] = [0]
        real = conform._window_witness_candidates

        def counted(*args, **kwargs):
            assemblies[0] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(conform, "_window_witness_candidates", counted)
        cache = conform.witnesses_path(tmp_path, "default")
        cold, _shaper = self._run(spec, decision, witness_cache=cache)
        cold_assemblies = assemblies[0]
        assert cold_assemblies > 0
        assert cache.exists()
        first_bytes = cache.read_bytes()
        warm, _shaper = self._run(spec, decision, witness_cache=cache)
        assert assemblies[0] == cold_assemblies
        assert (warm.uncovered_rules, warm.uncovered_transitions) == (0, 0)
        assert warm.topped_up_rules == cold.topped_up_rules
        assert 0 < warm.topped_up_sequences <= cold.topped_up_sequences
        assert cache.read_bytes() == first_bytes

    def test_a_stale_recording_falls_back_to_the_hunt(self, spec, decision, monkeypatch, tmp_path):
        from rebuild.pipeline import table as table_module

        cache = conform.witnesses_path(tmp_path, "default")
        self._run(spec, decision, witness_cache=cache)
        conform.write_witnesses(cache, table_module.windows_digest(decision), {}, {})
        result, _shaper = self._run(spec, decision, witness_cache=cache)
        assert (result.uncovered_rules, result.uncovered_transitions) == (0, 0)
        assert result.topped_up_sequences > 0

    def test_the_boundary_gates_green_is_inherited_within_its_horizon(self, spec, decision, monkeypatch):
        checked: list[str] = []
        monkeypatch.setattr(
            conform, "check_zwnj_structure", lambda text, *args, **kwargs: checked.append(text)
        )
        monkeypatch.setattr(conform, "check_split_buffer", lambda text, *args, **kwargs: checked.append(text))
        result, _shaper = self._run(spec, decision, boundary_horizon=self.HORIZON)
        assert checked
        assert all(len(text) > self.HORIZON for text in checked)
        assert any("inherited from the green boundary gate" in mode for mode in result.modes)

    def test_without_the_boundary_green_every_text_keeps_its_checks(self, spec, decision, monkeypatch):
        checked: list[str] = []
        monkeypatch.setattr(
            conform, "check_zwnj_structure", lambda text, *args, **kwargs: checked.append(text)
        )
        monkeypatch.setattr(conform, "check_split_buffer", lambda *args, **kwargs: None)
        result, _shaper = self._run(spec, decision)
        assert any(len(text) <= self.HORIZON for text in checked)
        assert not any("inherited from the green boundary gate" in mode for mode in result.modes)


class TestRawLabelsLateFormation:
    """raw_labels delegates formation to settle.form_ligatures, so the section 5.7 guard shapes the replayed labels exactly as it shapes the kernel's stream — over the mini fixture spec's qsDay_qsUtter corner, which carries the guard's worked example."""

    def test_guard_keeps_the_pair_unformed_before_low(self, spec):
        day, utter, low = chr(0xE653), chr(0xE67A), chr(0xE667)
        assert conform.raw_labels(spec, day + utter + low, frozenset()) == [
            "qsDay",
            "qsUtter",
            "qsLow",
        ]
        assert conform.raw_labels(spec, day + utter, frozenset()) == ["qsDay_qsUtter"]


class _LazyContextIndex:
    """A stand-in for the table's `_DeepTokenIndex` on specs whose full fixpoint is too heavy for a unit test: fibers are derived lazily by the real `_DeepFiberDeriver` for exactly the contexts the sweep reaches, tokens minted content-addressed from the full pin-free fibers, and resolution is context-keyed rather than base-keyed — sound here because with no worklist pins every base of a context shares the full member sets."""

    def __init__(self, spec, features, engine):
        from rebuild.pipeline import table as table_module

        self._table = table_module
        options = table_module._WindowOptions(spec)
        self._deriver = table_module._DeepFiberDeriver(
            spec,
            engine,
            options,
            table_module._liveness_probe(spec, engine),
            table_module.fourth_slot_filter(spec, features, engine),
        )
        self.representatives: dict[str, str] = {}
        self.minted = 0

    def _token(self, members) -> str:
        letters = tuple(sorted(member.letter for member in members))
        if len(letters) == 1:
            return letters[0]
        token = self._table.deep_class_id(letters)
        if token not in self.representatives:
            self.representatives[token] = letters[0]
            self.minted += 1
        return token

    def resolve(self, label, left, right1, right2, right3, right4):
        boundaryish = self._table.BOUNDARYISH
        if right3 in boundaryish:
            return right3, right4
        ctxf = self._deriver.context(label.split(".")[0], right1.split(".")[0], right2.split(".")[0])
        member3 = right3.split(".")[0]
        fiber3 = next(
            (fiber for fiber in ctxf.fibers if any(m.letter == member3 for m in fiber.members)), None
        )
        if fiber3 is None:
            return right3, right4
        token3 = self._token(fiber3.members)
        if right4 in boundaryish:
            return token3, right4
        member4 = right4.split(".")[0]
        group = next(
            (
                candidate
                for candidate in fiber3.r4_groups
                if any(m.kind == "letter" and m.letter == member4 for m in candidate)
            ),
            None,
        )
        if group is None:
            return token3, right4
        return token3, self._token(group)


class TestSettledWindowWalk:
    """The memoized walk must be observationally identical to the pair it replaced — settle_with_engine for the stream, _matched_windows for the windows and first-matching rules. Over-normalizing the memo key is the one real bug class (a key that blanks a slot the kernel can still read replays a wrong hit somewhere), so both sweeps run exhaustively, the walk reusing its memo from the second text on while the reference path recomputes every text fresh."""

    def _assert_walk_matches(
        self, spec, features, rules_by_input, alphabet, max_length, decision=None, deep_index=None
    ):
        import itertools

        from rebuild.pipeline import settle as settle_module
        from rebuild.pipeline import table as table_module
        from rebuild.pipeline.emit_gsub import _raw_rename_map

        engine = settle_module.Engine(spec, features)
        deep = table_module.third_slot_inputs(spec, engine)
        deep3_live = table_module.third_slot_filter(spec, features, engine)
        deep4 = table_module.fourth_slot_inputs(spec, engine)
        deep4_live = table_module.fourth_slot_filter(spec, features, engine)
        if deep_index is None and decision is not None and getattr(decision, "deep_classes", None):
            deep_index = conform._DeepTokenIndex(decision, _raw_rename_map(spec, frozenset(features)))
        walker = conform._SettledWindowWalk(
            spec, engine, features, rules_by_input, deep, deep3_live, deep4, deep4_live, {}, deep_index
        )
        reference = settle_module.Engine(spec, features)
        for length in range(1, max_length + 1):
            for combo in itertools.product(alphabet, repeat=length):
                text = "".join(combo)
                settled, names, matched_rules = walker.walk(text)
                expected = settle_module.settle_with_engine(reference, [ord(ch) for ch in text])
                assert settled == expected, text
                assert names == conform.settled_names(spec, expected, None), text
                replayed = {
                    index: (window, matched)
                    for index, window, matched in conform._matched_windows(
                        spec,
                        text,
                        features,
                        names,
                        rules_by_input,
                        deep,
                        deep3_live,
                        deep4,
                        deep4_live,
                        deep_index,
                    )
                }
                for index in range(len(settled)):
                    if index in replayed:
                        window, matched = replayed[index]
                        assert matched_rules[index] == matched, (text, index)
                        assert window in walker.windows, (text, index)
                    else:
                        assert matched_rules[index] is None, (text, index)
        return walker

    @pytest.mark.parametrize(
        "features",
        [frozenset(), frozenset({"ss03"}), frozenset({"ss02", "ss03"})],
        ids=["default", "ss03", "ss02+ss03"],
    )
    def test_the_walk_matches_the_unmemoized_pair_over_the_mini_alphabet(self, spec, features):
        from rebuild.pipeline.table import build_tables

        decision = build_tables(spec, features)[0]
        rules_by_input = conform._renamed_rules_by_input(spec, features, decision)
        self._assert_walk_matches(
            spec, features, rules_by_input, conform.spec_alphabet(spec), 4, decision=decision
        )

    def test_deep_slot_keys_replay_the_real_chains(self):
        """The mini spec carries no depth-3 or depth-4 prefers, so the deep-slot arm of the key normalization runs against the real spec: the deep inputs' chain letters plus a boundary, swept to length 5 so right3 and right4 both open. Rules are omitted — the real fixpoint is far too heavy for a unit test — so `matched` is vacuously None on both paths; a lazily derived context index (the real fiber machinery, minted per context the sweep reaches) stands in for the table's transported map, so the assertion weight rides the settled stream and the fiber-token window keys — without it both paths would keep raw labels and prove nothing about the class collapse."""
        import warnings

        from rebuild.pipeline import settle as settle_module
        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            real_spec = load_default_spec()
        alphabet = tuple(chr(cp) for cp in (0x0020, 0xE652, 0xE653, 0xE665, 0xE666, 0xE679, 0xE67A))
        engine = settle_module.Engine(real_spec, frozenset())
        deep_index = _LazyContextIndex(real_spec, frozenset(), engine)
        walker = self._assert_walk_matches(real_spec, frozenset(), {}, alphabet, 5, deep_index=deep_index)
        assert deep_index.minted, "the sweep reached no fiber-collapsed window"
        assert any(key[4].startswith("#C") for key in walker.windows), "no class token reached the memo"

    def test_prospect_live_slots_agree_between_walk_and_replay(self, monkeypatch):
        """The issue-28 arm of the deep-slot filters, exercised end to end: under the simulated-prospect default, the `_prospect_spec` fixture's A-before-B-C windows carry a live third slot the table enumerates, and the memoized walk, the unmemoized replay, and the built rules must agree on the split — the same observational-identity bar as the chain-arm sweeps above, now with the table's own deep-token index carrying the class map into both paths."""
        from rebuild.pipeline import settle as settle_module
        from rebuild.pipeline.table import build_tables
        from rebuild.test_settle import _prospect_spec

        monkeypatch.setattr(settle_module, "SIMULATED_PROSPECT_DEFAULT", True)
        spec = _prospect_spec()
        decision = build_tables(spec, frozenset())[0]
        assert any(row.right3 != "#NA" for row in decision.transitions)
        assert any(rule.look3 for rule in decision.rules)
        assert decision.deep_classes
        rules_by_input = conform._renamed_rules_by_input(spec, frozenset(), decision)
        self._assert_walk_matches(
            spec, frozenset(), rules_by_input, conform.spec_alphabet(spec), 5, decision=decision
        )

    def test_synthetic_depth4_walk_carries_rules_and_a_genuine_index(self):
        """The class-grain depth-4 arm with real rules and a real transported index: the mini fixture plus a reach-3 chain on ·Tea, built in the shipping deep world, mints an r4 class at the ·Tea·May·May·May windows; the walk and the replay must agree on the fiber-token keys and the first-matching rules over an alphabet that realizes those windows at length 5."""
        import dataclasses

        from rebuild.pipeline import fixtures, model
        from rebuild.pipeline.table import build_tables

        spec = fixtures.mini_spec()
        tea = spec.runes["qsTea"]
        chain = model.Condition(
            family=("qsMay",),
            then=model.Condition(
                family=("qsMay",),
                then=model.Condition(
                    family=("qsMay",),
                    then=model.Condition(family=("qsIt",)),
                ),
            ),
        )
        record = model.PolicyRecord(
            kind="prefer", stance="half", mode="absolute", when=model.When(right=chain)
        )
        runes = dict(spec.runes)
        runes["qsTea"] = dataclasses.replace(tea, policy=dataclasses.replace(tea.policy, prefer=(record,)))
        spec = dataclasses.replace(spec, runes=runes)
        decision = build_tables(spec, frozenset())[0]
        assert any(row.right4 in decision.deep_classes for row in decision.transitions)
        rules_by_input = conform._renamed_rules_by_input(spec, frozenset(), decision)
        alphabet = tuple(
            chr(codepoint)
            for codepoint in (
                spec.runes["qsTea"].codepoint,
                spec.runes["qsMay"].codepoint,
                spec.runes["qsIt"].codepoint,
            )
            if codepoint is not None
        ) + (" ",)
        walker = self._assert_walk_matches(spec, frozenset(), rules_by_input, alphabet, 5, decision=decision)
        assert any(key[5].startswith("#C") for key in walker.windows), "no r4 class token reached the memo"


class TestDeepTokenIndex:
    """The transport's raw-vs-renamed contract: `_DeepTokenIndex` is built from the table's raw label space but queried with the walk's marker-folded labels, so every member combination of every class-bearing row must resolve to exactly the deep components of the row's renamed key. The walk-equivalence sweeps cannot see a one-sided rename slip — both paths share the index — so this arm checks resolution against the rows directly, on a config whose rename map touches the row shape that broke first: a bare (singleton-fiber) r3 the config renames, under a class-token r4."""

    def test_every_class_row_resolves_under_a_renaming_config(self):
        import dataclasses

        from rebuild.pipeline import model
        from rebuild.pipeline.emit_gsub import _raw_rename_map
        from rebuild.pipeline.table import build_tables

        spec = mini_spec()
        tea = spec.runes["qsTea"]
        chain = model.Condition(
            family=("qsMay",),
            then=model.Condition(
                family=("qsMay",),
                then=model.Condition(
                    family=("qsMay",),
                    then=model.Condition(family=("qsIt",)),
                ),
            ),
        )
        record = model.PolicyRecord(
            kind="prefer", stance="half", mode="absolute", when=model.When(right=chain)
        )
        runes = dict(spec.runes)
        runes["qsTea"] = dataclasses.replace(tea, policy=dataclasses.replace(tea.policy, prefer=(record,)))
        may = runes["qsMay"]
        stance_name, stance = next(iter(may.stances.items()))
        surface = dataclasses.replace(
            stance.surface, unlocks=stance.surface.unlocks + (model.Unlock(feature="ss03"),)
        )
        stances = dict(may.stances)
        stances[stance_name] = dataclasses.replace(stance, surface=surface)
        runes["qsMay"] = dataclasses.replace(may, stances=stances)
        spec = dataclasses.replace(spec, runes=runes)
        features = frozenset({"ss03"})
        decision = build_tables(spec, features)[0]
        renames = _raw_rename_map(spec, features)
        assert renames.get("qsMay") == "qsMay.ss03"
        index = conform._DeepTokenIndex(decision, renames)
        deep = decision.deep_classes
        assert deep
        checked = 0
        bare_renamed_r3_under_class_r4 = 0
        for row in decision.transitions:
            if row.right3 not in deep and row.right4 not in deep:
                continue
            if row.right4 in deep and row.right3 not in deep and row.right3 in renames:
                bare_renamed_r3_under_class_r4 += 1
            want = (
                row.right3 if row.right3 in deep else renames.get(row.right3, row.right3),
                row.right4 if row.right4 in deep else renames.get(row.right4, row.right4),
            )
            for member3 in decision.token_members(row.right3):
                for member4 in decision.token_members(row.right4):
                    resolved = index.resolve(
                        renames.get(row.input_glyph, row.input_glyph),
                        row.left,
                        renames.get(row.right1, row.right1),
                        renames.get(row.right2, row.right2),
                        renames.get(member3, member3),
                        renames.get(member4, member4),
                    )
                    assert resolved == want, (row.key, member3, member4, resolved, want)
                    checked += 1
        assert checked
        assert (
            bare_renamed_r3_under_class_r4
        ), "no row exercises the renamed-bare-r3 + class-r4 shape this arm exists for"
