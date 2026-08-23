"""Conformance-module helper tests: normalization, the raw-pipeline replay, alias/ledger plumbing, kern evaluation, the subset-identity assertion, and the memoized settled-window walk's equivalence to settling the same texts unmemoized. The font-facing sweep itself runs in run_m1 (it needs the compiled mini-font). Settlement here is the crate's, so these arms need a built kernel: the guard sweep and the walk both invoke it, once per module for the sweep and in waves for the walk."""

import gzip
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from fnmatch import fnmatch
from pathlib import Path

import pytest

from rebuild.pipeline import conform, kernel_exec, settle
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId

MINI = Path(__file__).resolve().parent / "review" / "fixtures" / "mini"


@pytest.fixture(scope="module")
def spec():
    return mini_spec()


@pytest.fixture(scope="module")
def guard(spec):
    """The crate's complete section 5.7 verdict surface for the fixture spec, swept once for the whole module — every formation call below takes it as an argument rather than sweeping for itself."""
    return kernel_exec.guard_sweep(spec)


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


TEA, MAY, IT, OY = chr(0xE652), chr(0xE665), chr(0xE670), chr(0xE679)
ZWNJ = chr(0x200C)
DOT = chr(0x00B7)


class TestRawLabels:
    def test_formation_folds_the_ligature(self, spec, guard):
        assert conform.raw_labels(spec, TEA + OY, frozenset(), guard) == ["qsTea_qsOy"]

    def test_zwnj_locks_entry_bearing_followers(self, spec, guard):
        labels = conform.raw_labels(spec, ZWNJ + TEA + IT, frozenset(), guard)
        assert labels == ["uni200C", "qsTea.noentry", "qsIt"]

    def test_marker_fold_renames_under_features(self, spec, guard):
        assert conform.raw_labels(spec, MAY + TEA, frozenset({"ss03"}), guard) == ["qsMay", "qsTea.ss03"]

    def test_marker_and_lock_compose(self, spec, guard):
        labels = conform.raw_labels(spec, ZWNJ + TEA, frozenset({"ss02", "ss03"}), guard)
        assert labels == ["uni200C", "qsTea.ss02_ss03.noentry"]

    def test_namer_dot_does_not_lock(self, spec, guard):
        assert conform.raw_labels(spec, DOT + IT, frozenset(), guard) == ["periodcentered", "qsIt"]


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

    def test_pending_alias_reads_as_unaliased_in_the_comparison(self, spec, guard):
        from rebuild.validation.rowmodel import Row

        row = Row(codepoints=(0xE652,), glyphs=("qsTea",), clusters=(0,), seams=(), positions=((0, 0, 150),))
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        ((settled, _names),) = walker.walk_many([row.text])
        divergent = conform._compare_row(spec, {"qsTea": "pending"}, "default", frozenset(), row, settled)
        assert divergent is not None
        assert "unaliased" in divergent.kinds
        assert "unaliased:qsTea" in divergent.phenomena
        assert divergent == conform._compare_row(spec, {}, "default", frozenset(), row, settled)


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
        notes: Sequence[str] = (),
        modes: Sequence[str] = (),
    ) -> conform.ConformanceConfigResult:
        return conform.ConformanceConfigResult(
            config=config,
            sequences=sequences,
            shaping_runs=shaping_runs,
            divergences=list(divergences),
            notes=list(notes),
            modes=list(modes),
        )

    def test_sequences_come_from_the_first_result_and_shaping_runs_sum(self):
        merged = conform.merge_conformance_results(
            Path("M1.otf"),
            [self._result("default", shaping_runs=120), self._result("ss02", shaping_runs=110)],
        )
        assert merged.sequences == 100
        assert merged.shaping_runs == 230
        assert merged.passed is True

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


class TestOracleAudit:
    """`divergence-audit.tsv` is a fingerprinted artifact its readers parse straight off disk — the review surface's unit assembly, the census, the lanes' filtered load — so the file's bytes are the contract, and they no longer come from one `"\n".join` in the parent: each configuration's rows are written where they are produced and the parent concatenates the shards behind the header. Pin the new assembly against the old formula over the shapes the audit can take, an empty configuration and an empty audit included, because those are where a hand-held layout drifts first — and pin the refusals, because the way this goes wrong is a short audit that reads as a complete one."""

    def _shard(self, scratch: Path, config: str, lines: Sequence[str]) -> None:
        shard = conform.oracle_audit_shard(scratch, config)
        shard.parent.mkdir(parents=True, exist_ok=True)
        with shard.open("w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line + "\n")

    @pytest.mark.parametrize(
        "per_config",
        (
            {},
            {"default": []},
            {config: [] for config in conform.ACCEPTANCE_CONFIGS},
            {
                "default": ["default\tE668:E665\tcell\tmay-utter\tqsRoe|qsMay\tqsRoe.alt|qsMay"],
                "ss03": [],
                "ss10": [
                    "ss10\tE652:E679\tligation,seam\tUNMATCHED\tqsTea_qsOy\tqsTea|qsOy",
                    "ss10\tE650:0020\tcell\ta+b\tqsPea\tqsPea.half",
                ],
            },
            {
                "ss04": [
                    "ss04\tE670:E653\tcell\t·It~b~·Day.half\tqsIt|qsDay\tqsIt|qsDay.half",
                    "ss04\tE676:E677\tposition\tdrift\tqsAh|qsAwe\tslot 1 (qsAwe): origin want (7, 0)\t\ttrailing",
                ],
            },
        ),
    )
    def test_shards_concatenate_to_the_bytes_the_join_used_to_write(self, tmp_path, per_config):
        scratch = conform.oracle_audit_scratch(tmp_path)
        for config, lines in per_config.items():
            self._shard(scratch, config, lines)
        every = [line for lines in per_config.values() for line in lines]
        conform.join_oracle_audit(tmp_path, scratch, per_config, len(every))
        joined = "\n".join([conform.ORACLE_AUDIT_HEADER, *every]) + "\n"
        assert (tmp_path / "divergence-audit.tsv").read_bytes() == joined.encode("utf-8")

    def test_the_frozen_mini_audit_reassembles_byte_for_byte(self, tmp_path):
        """The same pin over a real audit instead of hand-made rows: the mini bundle's audit.tsv is a live one filtered to four letters, still written by the old formula in `fixtures/mini/regenerate.py`, and its configuration runs are contiguous and in ACCEPTANCE_CONFIGS order — so splitting it back into shards and concatenating them has to land on the file it came from."""
        source = MINI / "audit.tsv"
        rows = source.read_text(encoding="utf-8").splitlines()
        assert rows[0] == conform.ORACLE_AUDIT_HEADER
        per_config: dict[str, list[str]] = {config: [] for config in conform.ACCEPTANCE_CONFIGS}
        for row in rows[1:]:
            per_config[row.split("\t")[0]].append(row)
        scratch = conform.oracle_audit_scratch(tmp_path)
        for config, lines in per_config.items():
            self._shard(scratch, config, lines)
        conform.join_oracle_audit(tmp_path, scratch, conform.ACCEPTANCE_CONFIGS, len(rows) - 1)
        assert (tmp_path / "divergence-audit.tsv").read_bytes() == source.read_bytes()

    def test_the_two_oracle_paths_write_the_same_file(self, spec, tmp_path):
        """The claim the shards exist to keep true: `--jobs 1` writes the audit as it goes and the pool writes shards the parent concatenates, and the two have to land on the same bytes. Both are run here over the same hand-made subset tables — a pending alias makes every row diverge, an empty ledger leaves every divergence UNMATCHED — so a row reaches the file through each path in turn."""
        tables = tmp_path / "tables"
        tables.mkdir()
        for config, rows in (
            (
                "default",
                ["E652\tqsTea.noentry\t0\t\t0,0,150", "0020:E652\tspace|qsTea\t0,1\tbreak\t0,0,150|0,0,150"],
            ),
            ("ss03", ["E652:E652\tqsTea|qsTea\t0,1\tbreak\t0,0,150|0,0,150"]),
        ):
            with gzip.open(tables / f"baseline-{config}.subset.tsv.gz", "wt", encoding="utf-8") as fh:
                fh.write(f"# config: {config}\n")
                for row in rows:
                    fh.write(row + "\n")
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("qsTea: pending\nqsTea.noentry: pending\n")
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        configs = ("default", "ss03")

        serial = tmp_path / "serial"
        in_process = conform.compare_against_baseline(
            spec, tables, aliases, ledger, configs=configs, out_dir=serial
        )
        assert in_process.divergent_rows == 3
        assert [path.name for path in serial.iterdir()] == ["divergence-audit.tsv"]

        fanned = tmp_path / "fanned"
        fanned.mkdir()
        scratch = conform.oracle_audit_scratch(fanned)
        merged = conform.merge_oracle_results(
            conform.oracle_config_worker(spec, tables, aliases, ledger, config, None, None, audit_dir=scratch)
            for config in configs
        )
        conform.join_oracle_audit(fanned, scratch, configs, merged.divergent_rows)
        assert merged.divergent_rows == in_process.divergent_rows
        assert (fanned / "divergence-audit.tsv").read_bytes() == (
            serial / "divergence-audit.tsv"
        ).read_bytes()
        conform.discard_oracle_audit_scratch(fanned)
        assert [path.name for path in fanned.iterdir()] == ["divergence-audit.tsv"]

    def test_a_serial_oracle_that_dies_partway_leaves_the_audit_it_found_standing(
        self, monkeypatch, spec, tmp_path
    ):
        """The failure that has to stay loud. A truncated audit hashes differently rather than reading as stale, so it comes back to the surface build as a fresh, smaller, entirely self-consistent one — which is why `--jobs 1` writes through a staging copy and promotes it only after the last configuration, and why an oracle that dies on its second leaves the previous run's file exactly where it was."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("qsTea: pending\n")
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")

        def compare(spec, tables, config, *rest):
            audit = rest[-1]
            assert audit is not None
            audit.write(f"{config}\tE650\tcell\tpea-half\tqsPea\tqsPea.half\n")
            if config == "ss03":
                raise RuntimeError("ss03 fell over")
            return conform.OracleConfigResult(config=config, divergent_rows=1)

        monkeypatch.setattr(conform, "_compare_config", compare)
        with pytest.raises(RuntimeError):
            conform.compare_against_baseline(
                spec, tmp_path, aliases, ledger, configs=("default", "ss03"), out_dir=tmp_path
            )
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "aliases.yaml",
            "divergence-audit.tsv",
            "ledger.yaml",
        ]

    def test_a_missing_shard_is_named_rather_than_quietly_skipped(self, tmp_path):
        """Every shard is found before a byte is copied, so the concatenation cannot write a short audit out of whatever happened to be on disk — and the audit already there survives the refusal, which matters because the caller sweeps the scratch directory afterward and the shards are then gone too."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        scratch = conform.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        with pytest.raises(FileNotFoundError, match="ss03"):
            conform.join_oracle_audit(tmp_path, scratch, ("default", "ss03"), 1)
        assert standing.read_bytes() == b"the audit of the last green run\n"

    def test_an_audit_short_of_the_rows_its_workers_counted_is_not_promoted(self, tmp_path):
        """The counts come home through the pipe and the bytes come home on disk, so comparing them is the one cross-check the parent can make — and it is what catches a shard that was truncated but still closed clean, which is the shape no amount of stat-ing finds."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        scratch = conform.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        with pytest.raises(ValueError, match="2 divergent"):
            conform.join_oracle_audit(tmp_path, scratch, ("default",), 2)
        assert standing.read_bytes() == b"the audit of the last green run\n"

    def test_the_scratch_goes_whether_or_not_the_oracle_got_that_far(self, tmp_path):
        scratch = conform.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        self._shard(scratch, "ss03+ss05", [])
        conform.discard_oracle_audit_scratch(tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_a_sweep_takes_a_dead_run_s_shards_and_leaves_a_live_one_s(self, tmp_path):
        """The pid in the scratch name is load-bearing in both directions. A kill skips the `finally`, so a run that never came back would otherwise strand a whole audit's worth of disk until someone noticed; a run still going is another oracle over the same out_dir — a `--gates-only` pass beside a cycle — and sweeping it would pull the shards out from under its concatenation."""
        finished = subprocess.Popen([sys.executable, "-c", ""])
        finished.wait()
        stale = tmp_path / f"divergence-audit.parts.{finished.pid}"
        stale.mkdir()
        (stale / "default.part").write_text("")
        live = tmp_path / f"divergence-audit.parts.{os.getppid()}"
        live.mkdir()
        (live / "default.part").write_text("")
        conform.discard_oracle_audit_scratch(tmp_path)
        assert not stale.exists()
        assert (live / "default.part").is_file()

    def test_a_scratch_name_is_not_mistaken_for_an_artifact(self):
        """The scratch directory sits beside the artifacts in rebuild/out/m1, so its name has to miss everything that reads that directory: the cycle's artifact list, its subset-table glob, and the table readers' own patterns. Configuration names carry `+`, which the baseline tables already prove is safe in a filename here."""
        from rebuild.tools.artifact_cycle import M1_ARTIFACT_NAMES

        scratch = conform.oracle_audit_scratch(Path("m1"))
        assert scratch.name not in set(M1_ARTIFACT_NAMES)
        for name in [scratch.name] + [
            conform.oracle_audit_shard(scratch, config).name for config in conform.ACCEPTANCE_CONFIGS
        ]:
            assert not any(
                fnmatch(name, pattern)
                for pattern in (
                    "baseline-*.subset.tsv.gz",
                    "settlement-*.tsv",
                    "treaties-*.tsv",
                    "windows-*.tsv.gz",
                    "transitions-*.ndjson",
                    "*.json",
                )
            )
        names = [conform.oracle_audit_shard(scratch, config).name for config in conform.ACCEPTANCE_CONFIGS]
        assert len(set(names)) == len(conform.ACCEPTANCE_CONFIGS)


class TestConformSummary:
    def test_a_conformance_summary_stays_in_its_established_shape(self, tmp_path):
        import json

        report = conform.merge_conformance_results(Path("M1.otf"), [])
        path = tmp_path / "conform_summary.json"
        report.write(path)
        recorded = json.loads(path.read_text())
        assert set(recorded) == {
            "font",
            "sequences",
            "shaping_runs",
            "divergences",
            "divergences_by_kind",
            "pass",
            "notes",
        }


class _SilentShaper:
    """Enough of a Shaper for the belt's bookkeeping, which never reads shaped output: every text shapes to nothing, so the oracle records one length divergence per text and the structural checks see no slots. The texts it was asked to shape are the observable."""

    def __init__(self):
        self.shaped: list[str] = []

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        self.shaped.append(text)
        return []

    def has_ink(self, glyph_name: str) -> bool:
        return False

    def outline_signature(self, glyph_name: str) -> tuple:
        return ()


class TestBeltEconomics:
    """What the per-edit belt does and does not spend over a short horizon with the font faked out: every text of every length up to the horizon shapes exactly once, and the two structural checks run on exactly the texts they can say anything about."""

    HORIZON = 2

    def _run(self, spec, guard):
        shaper = _SilentShaper()
        result = conform._conformance_config(
            shaper,  # pyright: ignore[reportArgumentType]
            spec,
            "default",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            {},
            None,
            self.HORIZON,
            guard,
        )
        return result, shaper

    def test_the_sweep_shapes_each_enumerated_text_exactly_once(self, spec, guard, monkeypatch):
        monkeypatch.setattr(conform, "check_split_buffer", lambda *args, **kwargs: None)
        result, shaper = self._run(spec, guard)
        alphabet = len(conform.spec_alphabet(spec))
        assert result.sequences == alphabet + alphabet**2
        assert result.shaping_runs == result.sequences
        assert len(shaper.shaped) == len(set(shaper.shaped)) == result.sequences
        assert all(len(text) <= self.HORIZON for text in shaper.shaped)

    def test_the_structural_checks_run_on_the_texts_that_carry_a_boundary(self, spec, guard, monkeypatch):
        """The retired boundary gate's charter, now the belt's: every ZWNJ-bearing text is weighed for zero-advance inkless slots and every splitter-bearing one against its own segments — and no other text pays for either, since a text with no boundary in it satisfies both by construction."""
        zwnj_checked: list[str] = []
        split_checked: list[str] = []
        monkeypatch.setattr(
            conform, "check_zwnj_structure", lambda text, *args, **kwargs: zwnj_checked.append(text)
        )
        monkeypatch.setattr(
            conform, "check_split_buffer", lambda text, *args, **kwargs: split_checked.append(text)
        )
        _result, shaper = self._run(spec, guard)
        splitters = conform.splitting_boundary_chars(spec)
        assert set(zwnj_checked) == {text for text in shaper.shaped if conform.ZWNJ in text}
        assert set(split_checked) == {text for text in shaper.shaped if set(text) & splitters}
        assert zwnj_checked and split_checked


class TestRawLabelsLateFormation:
    """raw_labels delegates formation to settle.form_ligatures, so the section 5.7 guard shapes the replayed labels exactly as it shapes the kernel's stream — over the mini fixture spec's qsDay_qsUtter corner, which carries the guard's worked example."""

    def test_guard_keeps_the_pair_unformed_before_low(self, spec, guard):
        day, utter, low = chr(0xE653), chr(0xE67A), chr(0xE667)
        assert conform.raw_labels(spec, day + utter + low, frozenset(), guard) == [
            "qsDay",
            "qsUtter",
            "qsLow",
        ]
        assert conform.raw_labels(spec, day + utter, frozenset(), guard) == ["qsDay_qsUtter"]


class TestSettledWindowWalk:
    """The memo keys on the raw window — every slot one settlement can read, none of them blanked — so the bar is two things at once: observational identity with an unmemoized settlement of the same tokens, and key agreement with `_matched_windows`, which reads the same raw slots. Over-keying was never the risk; under-keying was (a key that blanks a slot the kernel can still read replays a wrong outcome somewhere), and both paths run exhaustively here, the walk reusing its memo from the second text on while the reference path settles every text in a sequence of its own. The rule replay itself no longer rides the walk — `_matched_windows` and `_DeepTokenIndex` keep it, for the font-free witness gate — so the arms that need rules exercise them there."""

    SWEEP_CHUNK = 4096

    def _sweep(self, spec, features, alphabet, max_length, rules_by_input=None, deep_index=None):
        """Sweep every text up to `max_length`: the walk's settled stream and names against an unmemoized settlement of the very same formed tokens, and its memo keys against the raw-grain replay both sides share `_window_rights` for. What the first arm alarms is the memo's keying rather than one engine against another — both sides are the crate now — because the walk answers a window once and replays it wherever the key recurs while the reference settles every text's positions in a sequence of its own, so a key that blanked a slot settlement can still read would show up here as a wrong outcome somewhere. Texts stream through in chunks so the reference's decoded traces stay a bounded pile; the walk keeps its memo across them. With `rules_by_input` supplied, the replay also runs through `deep_index` and its (window, first-matching rule) pairs come back for the class-grain arms to assert on."""
        import itertools

        guard = kernel_exec.guard_sweep(spec)
        walker = conform._SettledWindowWalk(spec, features, {}, guard)
        replayed: list[tuple[tuple[str, ...], int | None]] = []
        for length in range(1, max_length + 1):
            stream = itertools.product(alphabet, repeat=length)
            while True:
                texts = ["".join(combo) for combo in itertools.islice(stream, self.SWEEP_CHUNK)]
                if not texts:
                    break
                walked = walker.walk_many(texts)
                reference = kernel_exec.settle_sequences(
                    spec,
                    [
                        (
                            settle.form_ligatures(
                                spec,
                                settle.tokens_from_codepoints(spec, [ord(ch) for ch in text]),
                                guard,
                            ),
                            features,
                        )
                        for text in texts
                    ],
                )
                for text, (settled, names), traces in zip(texts, walked, reference):
                    assert traces is not None
                    expected = [trace.settled for trace in traces]
                    assert settled == expected, text
                    assert names == conform.settled_names(spec, expected, None), text
                    for _index, window, _matched in conform._matched_windows(
                        spec, text, features, guard, names, {}, None
                    ):
                        assert window in walker.windows, (text, window)
                    if rules_by_input is not None:
                        replayed += [
                            (window, matched)
                            for _index, window, matched in conform._matched_windows(
                                spec, text, features, guard, names, rules_by_input, deep_index
                            )
                        ]
        return walker, replayed

    @pytest.mark.parametrize(
        "features",
        [frozenset(), frozenset({"ss03"}), frozenset({"ss02", "ss03"})],
        ids=["default", "ss03", "ss02+ss03"],
    )
    def test_the_walk_matches_the_unmemoized_pair_over_the_mini_alphabet(self, spec, features):
        self._sweep(spec, features, conform.spec_alphabet(spec), 4)

    def test_deep_slot_keys_replay_the_real_chains(self):
        """The mini spec carries no depth-3 or depth-4 prefers, so the deep-slot arm of the key normalization runs against the real spec: the deep inputs' chain letters plus a boundary, swept to length 5 so right3 and right4 both open. The assertion weight rides the settled stream and the window keys — the walk keeps raw labels in its deep slots now, which is strictly finer than the table's own grain, so a live slot must show up in the memo as a real label rather than as #NA."""
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            real_spec = load_default_spec()
        alphabet = tuple(chr(cp) for cp in (0x0020, 0xE652, 0xE653, 0xE665, 0xE666, 0xE679, 0xE67A))
        walker, _replayed = self._sweep(real_spec, frozenset(), alphabet, 5)
        assert any(key[4] != "#NA" for key in walker.windows), "no window opened its third slot"

    def test_prospect_live_slots_agree_between_walk_and_replay(self, monkeypatch):
        """The issue-28 arm of the deep-slot filters, exercised end to end: under the simulated-prospect default, `fixtures.prospect_spec`'s A-before-B-C windows carry a live third slot the table enumerates, and the memoized walk and the unmemoized replay must agree on the split — the same observational-identity bar as the chain-arm sweeps above, with the table's own deep-token index carrying the class map into the replay's rule matching."""
        from rebuild.pipeline import fixtures
        from rebuild.pipeline.emit_gsub import _raw_rename_map
        from rebuild.pipeline.kernel_exec import build_tables

        monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", True)
        spec = fixtures.prospect_spec()
        decision = build_tables(spec, frozenset())[0]
        assert any(row.right3 != "#NA" for row in decision.transitions)
        assert any(rule.look3 for rule in decision.rules)
        assert decision.deep_classes
        rules_by_input = conform._renamed_rules_by_input(spec, frozenset(), decision)
        index = conform._DeepTokenIndex(decision, _raw_rename_map(spec, frozenset()))
        _walker, replayed = self._sweep(
            spec, frozenset(), conform.spec_alphabet(spec), 5, rules_by_input, index
        )
        assert any(matched is not None for _window, matched in replayed)

    def test_synthetic_depth4_replay_carries_rules_and_a_genuine_index(self):
        """The class-grain depth-4 arm with real rules and a real transported index: the mini fixture plus a reach-3 chain on ·Tea, built in the shipping deep world, mints an r4 class at the ·Tea·May·May·May windows, and `_matched_windows` must resolve the realized labels to that class token and match rules against it — the replay half of the pair, which the witness gate leans on, while the walk beside it keeps settling those same texts right."""
        import dataclasses

        from rebuild.pipeline import fixtures, model
        from rebuild.pipeline.emit_gsub import _raw_rename_map
        from rebuild.pipeline.kernel_exec import build_tables

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
        index = conform._DeepTokenIndex(decision, _raw_rename_map(spec, frozenset()))
        alphabet = tuple(
            chr(codepoint)
            for codepoint in (
                spec.runes["qsTea"].codepoint,
                spec.runes["qsMay"].codepoint,
                spec.runes["qsIt"].codepoint,
            )
            if codepoint is not None
        ) + (" ",)
        _walker, replayed = self._sweep(spec, frozenset(), alphabet, 5, rules_by_input, index)
        assert any(
            window[5].startswith("#C") for window, _matched in replayed
        ), "no r4 class token reached the replay"
        assert any(matched is not None for _window, matched in replayed)

    def test_prefill_then_walk_matches_walk_with_misses(self, spec, guard):
        """`prefill` and `walk` answer alike; what differs is the bill. A walker handed its texts up front answers each of them out of the memo, so `single_settles` stays at zero, while a walker asked one text at a time spends a whole kernel invocation on every miss — which is exactly what the counter exists to make visible to a caller who forgot to prefill."""
        import itertools

        features = frozenset()
        alphabet = conform.spec_alphabet(spec)
        texts = ["".join(pair) for pair in itertools.islice(itertools.product(alphabet, repeat=2), 6)]
        prefilled = conform._SettledWindowWalk(spec, features, {}, guard)
        prefilled.prefill(texts)
        assert prefilled.single_settles == 0
        lazy = conform._SettledWindowWalk(spec, features, {}, guard)
        assert [prefilled.walk(text) for text in texts] == [lazy.walk(text) for text in texts]
        assert prefilled.single_settles == 0
        assert lazy.single_settles > 0

    def test_one_memo_key_settles_its_distinct_case_rows_alike(self, spec, guard):
        """The dedupe's own premise, checked rather than argued: `_window_rights`' `#NA` cascade blanks slots the key does not carry, so several distinct raw case rows land on one memo key, and the walk asks the crate about only the first of them. Under `audit_dedupe` every later one is asked too and held to the memoized outcome — the mini alphabet at depth 4 is where those collisions are dense enough to be worth the extra invocations."""
        import itertools

        features = frozenset()
        alphabet = conform.spec_alphabet(spec)
        walker = conform._SettledWindowWalk(spec, features, {}, guard, audit_dedupe=True)
        walker.walk_many(
            ["".join(combo) for length in range(1, 5) for combo in itertools.product(alphabet, repeat=length)]
        )
        assert walker.audit_multi_keys, "no memo key carried a second distinct raw window"
        assert walker.audit_extra_rows

    def test_a_dropping_walk_prefills_past_a_refusal_and_raises_only_when_it_is_walked(
        self, spec, guard, monkeypatch
    ):
        """The witness gate's pairing, which is why `on_error` exists at all: the prefill is eager over candidates the lazy first-witness loop may never read, so a window the crate refuses in one of them must not take the whole gate down. Under `on_error="drop"` it is memoized as a refusal and the text carrying it stops advancing; the walk that later reaches that key is where the refusal surfaces — the semantics the per-candidate settle had before any prefill existed. The default stays strict, and the same prefill raises there."""
        refused = "qsTea"
        clean, refusing_text = chr(0xE665) + chr(0xE670), chr(0xE665) + chr(0xE652)
        original = kernel_exec.settle_windows

        def injecting(asked_spec, cases, features, **rest):
            answers = original(asked_spec, cases, features, **rest)
            hits = [index for index, case in enumerate(cases) if case["input"] == refused]
            if not hits:
                return answers
            if rest.get("on_error") != "drop":
                raise settle.SettleError(f"{refused}: the injected refusal", "E-INCOMPARABLE")
            for index in hits:
                answers[index] = None
            return answers

        monkeypatch.setattr(kernel_exec, "settle_windows", injecting)
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, on_error="drop")
        walker.prefill([clean, refusing_text])
        settled, _names = walker.walk(clean)
        assert [item.cell.rune for item in settled] == ["qsMay", "qsIt"]
        with pytest.raises(settle.SettleError):
            walker.walk(refusing_text)
        with pytest.raises(settle.SettleError):
            walker.walk_many([clean, refusing_text])
        strict = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        with pytest.raises(settle.SettleError):
            strict.prefill([clean, refusing_text])

    @pytest.mark.slow
    def test_the_real_alphabet_keys_its_distinct_case_rows_alike(self):
        """The same audit over the live rune files at depth 3, where the alphabet is the shipping one and the collisions are the ones `gate:conform` actually rides on. Marked slow: it settles every distinct raw window the depth-3 sweep reaches, not merely one per memo key."""
        import itertools
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            real_spec = load_default_spec()
        features = frozenset()
        alphabet = conform.spec_alphabet(real_spec)
        walker = conform._SettledWindowWalk(
            real_spec, features, {}, kernel_exec.guard_sweep(real_spec), audit_dedupe=True
        )
        for length in range(1, 4):
            stream = itertools.product(alphabet, repeat=length)
            while True:
                texts = ["".join(combo) for combo in itertools.islice(stream, self.SWEEP_CHUNK)]
                if not texts:
                    break
                walker.walk_many(texts)
        assert walker.audit_multi_keys
        assert walker.audit_extra_rows


class TestDeepTokenIndex:
    """The transport's raw-vs-renamed contract: `_DeepTokenIndex` is built from the table's raw label space but queried with the walk's marker-folded labels, so every member combination of every class-bearing row must resolve to exactly the deep components of the row's renamed key. The walk-equivalence sweeps cannot see a one-sided rename slip — both paths share the index — so this arm checks resolution against the rows directly, on a config whose rename map touches the row shape that broke first: a bare (singleton-fiber) r3 the config renames, under a class-token r4."""

    def test_every_class_row_resolves_under_a_renaming_config(self):
        import dataclasses

        from rebuild.pipeline import model
        from rebuild.pipeline.emit_gsub import _raw_rename_map
        from rebuild.pipeline.kernel_exec import build_tables

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


class TestWitnessRowCap:
    """The witness search reads a bounded sample of each rule's first-matching windows. What must survive the bound is the alarm: a rule no window can realize still comes back unwitnessed, and the sample never invents a witness for one."""

    def _tables(self):
        from rebuild.pipeline import kernel_exec

        return kernel_exec.build_tables(mini_spec(), frozenset())

    def test_the_cap_bounds_the_rows_kept_per_rule(self):
        spec = mini_spec()
        decision, _treaty = self._tables()
        rows = conform._first_match_rows(decision)
        assert rows
        assert all(len(kept) <= conform.WITNESS_ROW_CAP for kept in rows.values())
        assert not conform.find_rule_witnesses(spec, frozenset(), decision).unwitnessed

    def test_a_dead_rule_is_still_reported_under_the_cap(self, monkeypatch):
        import dataclasses

        from rebuild.pipeline.table import Rule

        spec = mini_spec()
        decision, _treaty = self._tables()
        dead = Rule(
            input_glyph="qsMay",
            backtrack=("qsNever.loop",),
            look1=None,
            look2=None,
            look3=None,
            look4=None,
            outcome="qsMay",
            provenance=(),
            joint=False,
        )
        poisoned = dataclasses.replace(decision, rules=decision.rules + (dead,))
        monkeypatch.setattr(conform, "WITNESS_ROW_CAP", 1)
        report = conform.find_rule_witnesses(spec, frozenset(), poisoned)
        assert report.unwitnessed == [len(decision.rules)]
