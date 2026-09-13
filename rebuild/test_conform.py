"""Conformance-module helper tests: normalization, the raw-pipeline replay, alias/ledger plumbing, kern evaluation, and the memoized settled-window walk's equivalence to settling the same texts unmemoized. The font-facing sweep itself runs in run_m1 (it needs the compiled mini-font). Settlement here is the crate's, so these arms need a built kernel: the guard sweep and the walk both invoke it, once per module for the sweep and in waves for the walk."""

import gzip
import hashlib
import inspect
import json
import os
import struct
import subprocess
import sys
import zlib
from collections.abc import Sequence
from dataclasses import asdict, replace
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest

from rebuild.pipeline import (
    baseline_subset,
    conform,
    kernel_exec,
    labels,
    oracle,
    oracle_cache,
    run_m1,
    settle,
)
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId

MINI = Path(__file__).resolve().parent / "review" / "fixtures" / "mini"
REPO_ROOT = Path(__file__).resolve().parent.parent


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

    def test_isolated_overlay_labels_render_one_twin_per_raw_token(self, spec):
        """Under the overlay a ligature's components stand as two twins — the pre-empt substitutes before formation, so nothing forms — and a boundary token is its own glyph."""
        tokens = conform.isolated_overlay_tokens(spec, IT + TEA + OY + ZWNJ)
        assert conform.isolated_overlay_labels(spec, tokens) == [
            "qsIt.ss10",
            "qsTea.ss10",
            "qsOy.ss10",
            "uni200C",
        ]


TEA, MAY, IT, OY = chr(0xE652), chr(0xE665), chr(0xE670), chr(0xE679)
ZWNJ = chr(0x200C)
DOT = chr(0x00B7)


class TestIsolatedOverlay:
    """The overlay configuration has no settlement table: `OVERLAY_CONFIGS` names exactly the acceptance configurations whose features activate a registered `overlay: isolated` taste set, the settlement roster is the rest, and everything the oracle and the belt do for it comes from the registry and the font's `hmtx` rather than from the crate."""

    def test_the_rosters_partition_the_acceptance_set_by_the_registry(self, spec):
        from rebuild.pipeline.model import isolated_overlay_active
        from rebuild.pipeline.spec_load import load_default_spec

        assert conform.SETTLEMENT_CONFIGS + conform.OVERLAY_CONFIGS == conform.ACCEPTANCE_CONFIGS
        assert not set(conform.SETTLEMENT_CONFIGS) & set(conform.OVERLAY_CONFIGS)
        for registry_spec in (spec, load_default_spec()):
            overlays = tuple(
                config
                for config in conform.ACCEPTANCE_CONFIGS
                if isolated_overlay_active(registry_spec, conform.features_for_config(config))
            )
            assert overlays == conform.OVERLAY_CONFIGS
        inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
        assert set(conform.settle_memo_files(Path("m1"), spec, inputs)) == set(conform.SETTLEMENT_CONFIGS)

    def test_the_overlay_walk_answers_the_bare_stream_from_the_registry(self, spec, monkeypatch):
        crate_reached: list = []
        monkeypatch.setattr(kernel_exec, "settle_windows", lambda *a, **k: crate_reached.append(a))
        monkeypatch.setattr(kernel_exec, "settle_cases", lambda *a, **k: crate_reached.append(a))
        walker = conform.IsolatedOverlayWalk(spec)
        settled, names = walker.walk_many([TEA + OY + ZWNJ + IT])[0]
        assert names == ["qsTea.ss10", "qsOy.ss10", "uni200C", "qsIt.ss10"]
        assert [item.cell.rune for item in settled] == ["qsTea", "qsOy", "zwnj", "qsIt"]
        assert all(item.seam is None and item.extension == 0 for item in settled)
        for item in settled:
            if item.cell.stance != settle.BOUNDARY_STANCE:
                assert item.cell == CellId(
                    item.cell.rune, spec.runes[item.cell.rune].default_stance, None, None, ()
                )
        assert walker.walk(TEA + OY) == walker.walk_many([TEA + OY])[0]
        assert walker.save_memo() is False and walker.memo_line("ss10", False) is None
        assert not crate_reached

    def test_a_row_the_old_font_ligated_diverges_at_ligation_grain_through_the_overlay_walk(self, spec):
        from rebuild.validation.rowmodel import Row

        row = Row(
            codepoints=(0xE652, 0xE679),
            glyphs=("qsTea_qsOy",),
            clusters=(0, 0),
            seams=("lig",),
            positions=((0, 0, 300),),
        )
        settled, _names = conform.IsolatedOverlayWalk(spec).walk_many([row.text])[0]
        divergent = conform._compare_row(spec, {}, "ss10", frozenset({"ss10"}), row, settled)
        assert divergent is not None
        assert divergent.kinds == ("ligation",)
        assert divergent.new_cells == (
            f"qsTea/{spec.runes['qsTea'].default_stance}/None/None/",
            f"qsOy/{spec.runes['qsOy'].default_stance}/None/None/",
        )
        assert divergent.new_seams == ("break",)

    def test_the_overlay_arm_sweeps_two_letters_and_never_reaches_the_crate(self, spec, monkeypatch):
        """Whatever horizon the belt runs at, the overlay arm sweeps every letter and every pair and no more, and it forms, settles and memoizes nothing — the crate is unreachable for the whole of it."""

        def unreachable(*args, **kwargs):
            raise AssertionError("the overlay arm reached the crate")

        for name in ("settle_windows", "settle_cases", "guard_sweep", "settle_sequences"):
            monkeypatch.setattr(kernel_exec, name, unreachable)
        monkeypatch.setattr(conform, "check_split_buffer", lambda *args, **kwargs: None)
        shaper = _SilentShaper()
        result = conform._conformance_config(
            shaper,  # pyright: ignore[reportArgumentType]
            spec,
            "ss10",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            {},
            None,
            conform.BELT_HORIZON + 3,
            None,
        )
        alphabet = len(conform.spec_alphabet(spec))
        assert result.sequences == alphabet + alphabet**2
        assert result.shaping_runs == result.sequences == len(shaper.shaped)
        assert all(len(text) <= conform.OVERLAY_HORIZON for text in shaper.shaped)
        assert result.divergences and {divergence.kind for divergence in result.divergences} == {"length"}


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
        aliases = labels.load_alias_map(path)
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
        assert oracle._match_ledger(ledger, row) == ["boundary-echo"]
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
        assert oracle._match_ledger(ledger, namer_dot_row) == ["zwnj-word-initial-unification"]

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
            (("-en-ext-2:same-seam",), "same-seam-extension-non-summing"),
            (("-en-ext-2",), None),
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
            assert oracle.classify_divergence(row) == expected, phenomena

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
                assert oracle.classify_divergence(row) == "boundary-echo", (codepoints, phenomena)
            position_row = replace(base, kinds=("position",), phenomena=("position-kern-attributable",))
            assert oracle.classify_divergence(position_row) is None

    def test_zoo_contraction_class_requires_the_old_tea_zoo_trim(self):
        old_zoo = CellId("qsZoo", "full", "x-height", None, ("en-trim-1",))
        new_zoo = replace(old_zoo, adjustments=("en-con-1",))
        old_names = ("qsTea.half.ex-y5.ex-con-1", "qsZoo.en-trim-1")
        phenomena = tuple(sorted(conform._cell_deltas(old_zoo, new_zoo, old_names, 1)))
        row = conform.DivergentRow(
            config="default",
            codepoints="E652:E65B",
            kinds=("cell",),
            position=1,
            baseline_glyphs=old_names,
            baseline_seams=("y5",),
            new_cells=("qsTea/half/None/x-height/", "qsZoo/full/x-height/None/en-con-1"),
            new_seams=("y5",),
            phenomena=phenomena,
        )
        assert oracle.classify_divergence(row) == "zoo-entry-contraction-respelled"
        for other in (
            replace(row, codepoints="E650:E65B"),
            replace(row, baseline_glyphs=("qsTea.half.ex-y5", "qsZoo.en-trim-1")),
            replace(row, baseline_glyphs=("qsTea.half.en-y8.ex-y5.ex-con-1", "qsZoo.en-trim-1")),
            replace(row, baseline_glyphs=(old_names[0], "qsZoo.en-con-2")),
            replace(row, new_cells=("qsTea/half/top/x-height/", row.new_cells[1])),
            replace(row, new_cells=(row.new_cells[0], "qsZoo/full/x-height/None/en-con-2")),
            replace(row, phenomena=("+en-con-1",)),
            replace(row, phenomena=phenomena + ("+ex-ext-1",)),
            replace(row, kinds=("cell", "position"), phenomena=phenomena + ("position-drift",)),
        ):
            assert oracle.classify_divergence(other) is None

    def test_zoo_contraction_class_excludes_the_unchanged_position_it_roe_redraw(self):
        """The old ·It·Roe pixels differ before ·Tea·Zoo even when every origin and advance matches. That residue must stay visible instead of riding the downstream entry-contraction identity."""
        row = conform.DivergentRow(
            config="default",
            codepoints="E670:E668:E652:E65B",
            kinds=("cell",),
            position=3,
            baseline_glyphs=(
                "qsIt.ex-y5",
                "qsRoe.en-ext-1-at-5",
                "qsTea.half.ex-y5.ex-con-1",
                "qsZoo.en-trim-1",
            ),
            baseline_seams=("y5", "break", "y5"),
            new_cells=(
                "qsIt/hapax/None/x-height/",
                "qsRoe/hapax/x-height/None/en-ext-1",
                "qsTea/half/None/x-height/",
                "qsZoo/full/x-height/None/en-con-1",
            ),
            new_seams=("y5", "break", "y5"),
            phenomena=("+en-con-1", "-en-trim-1"),
        )
        for config in ("default", "ss03", "ss04", "ss05", "ss02+ss03+ss05"):
            assert oracle.classify_divergence(replace(row, config=config)) is None


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
        evaluator = oracle.KernEvaluator(sidecar)
        assert evaluator.value_for("qsBay.en-y0", "qsTea") == -1
        assert evaluator.value_for("qsBay", "qsTea.half.ex-y5") == -1
        assert evaluator.value_for("qsNo.alt.en-y5", "qsPea") == -2
        assert evaluator.value_for("qsNo", "qsPea") == 0
        assert evaluator.value_for("qsHe", "qsMay.noentry") == -3
        assert evaluator.value_for("qsHe", "qsMay") == 0

    def test_global_record(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nglobal: {value: -1}\n")
        evaluator = oracle.KernEvaluator(sidecar)
        assert evaluator.value_for("qsPea", "qsTea") == -1

    def test_real_sidecar_parses(self):
        evaluator = oracle.KernEvaluator(
            Path(__file__).resolve().parents[1] / "glyph_data" / "senior_quikscript_kerning.yaml"
        )
        assert isinstance(evaluator.value_for("qsBay", "qsTea"), int)


class TestAliasCompleteness:
    def _names(self, tmp_path, names):
        """The sidecar the refilter writes, which is now the alias check's whole input — so these arms stand up a names document rather than a pile of subset tables."""
        path = tmp_path / baseline_subset.NAMES_NAME
        path.write_text(json.dumps({"format": baseline_subset.NAMES_FORMAT, "names": names}) + "\n")
        return path

    def _aliases(self, tmp_path):
        path = tmp_path / "aliases.yaml"
        path.write_text("qsIt: {rune: qsIt, stance: hapax}\nqsTea.noentry: pending\n")
        return path

    def test_known_pending_and_boundary_names_resolve(self, tmp_path):
        self._names(tmp_path, {"default": ["qsIt", "qsTea.noentry", "space"]})
        assert oracle.unaliased_subset_names(tmp_path, self._aliases(tmp_path)) == {}

    def test_missing_names_are_reported_with_their_configs(self, tmp_path):
        self._names(tmp_path, {"default": ["qsIt", "qsPea.ex-y0"], "ss03": ["qsPea.ex-y0"]})
        assert oracle.unaliased_subset_names(tmp_path, self._aliases(tmp_path)) == {
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
        kern = oracle.KernEvaluator(sidecar)
        row = self._row([0xE679, 0xE650], ["qsOy", "qsPea"], [(0, 0, 300), (0, 0, 250)])
        expected, attributable = oracle._kern_normalized_positions(kern, row, 50)
        assert expected == ((0, 0, 450), (0, 0, 250))
        assert attributable == (True, False)

    def test_kern_partner_skips_the_zwnj_slot(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nleft_family: [qsOy]\nright_family: [qsPea]\nvalue: -3\n")
        kern = oracle.KernEvaluator(sidecar)
        row = self._row(
            [0xE679, 0x200C, 0xE650],
            ["qsOy", "space", "qsPea.noentry"],
            [(0, 0, 300), (0, 0, 0), (0, 0, 250)],
        )
        expected, attributable = oracle._kern_normalized_positions(kern, row, 50)
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
        assert oracle.classify_divergence(self._row("ss03", phenomena)) == "ss03-chain-join-gains"

    def test_unentered_it_gain_outside_ss03_matches_nothing(self):
        phenomena = ("seam-gain:qsIt", "seam-gain-unentered:qsIt")
        assert oracle.classify_divergence(self._row("default", phenomena)) is None

    def test_entered_it_gain_keeps_its_class(self):
        assert (
            oracle.classify_divergence(self._row("default", ("seam-gain:qsIt", "exit-added")))
            == "entered-it-baseline-join-gain"
        )

    def test_position_drift_never_rides_a_cell_grain_class(self):
        assert oracle.classify_divergence(self._row("default", ("exit-dropped", "position-drift"))) is None

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
            assert oracle.PREDICATES["ss10_isolation_completed"](row) is False, boundary
            assert oracle.classify_divergence(row) == "boundary-echo", boundary

    def test_ss10_ligation_routes_to_ligature_suppressed(self):
        pairs = oracle.ss10_formable_pairs()
        assert {
            "E653:E67A",
            "E652:E679",
            "E67B:E652",
            "E659:E67A",
            "E65A:E67A",
            "E65D:E67A",
            "E657:E67A",
        } <= pairs
        for pair in sorted(pairs):
            row = self._row("ss10", ("ligation",), codepoints=f"E650:{pair}")
            assert oracle.classify_divergence(row) == "ss10-ligature-suppressed", pair

    def test_ss10_formable_pairs_are_the_registry_sequences(self, tmp_path):
        registry = tmp_path / "script.yaml"
        registry.write_text(
            "families:\n"
            "  qsPea: {codepoint: 0xE650}\n"
            "  qsTea: {codepoint: 0xE652}\n"
            "  qsOy: {codepoint: 0xE679}\n"
            "  qsTea_qsOy: {sequence: [qsTea, qsOy]}\n"
            "  qsPea_qsTea_qsOy: {sequence: [qsPea, qsTea, qsOy]}\n",
            encoding="utf-8",
        )
        assert oracle.ss10_formable_pairs(registry) == frozenset({"E652:E679", "E650:E652:E679"})

    def test_ss10_predicate_needs_a_member_on_every_lost_seam(self):
        def loss(left_cp, left, right_cp, right):
            return conform.DivergentRow(
                config="ss10",
                codepoints=f"{left_cp}:{right_cp}",
                kinds=("seam",),
                position=0,
                baseline_glyphs=(left.split("/")[0], right.split("/")[0]),
                baseline_seams=("y0",),
                new_cells=(left, right),
                new_seams=("break",),
                phenomena=("seam-loss",),
            )

        bare_carrier = loss("E650", "qsPea/full/None/None/", "E659", "qsVie/normal/None/None/")
        assert oracle.PREDICATES["ss10_isolation_completed"](bare_carrier) is True
        for rune in ("qsI", "qsEt", "qsSee", "qsRoe", "qsVie"):
            assert rune in oracle.SS10_UNCOVERED_BY_OLD_FONT, rune
        non_members = loss("E650", "qsPea/full/None/None/", "E665", "qsMay/loop/None/None/")
        assert oracle.PREDICATES["ss10_isolation_completed"](non_members) is False

    def test_ss10_namer_dot_ligation_outranks_marker_staging(self):
        row = self._row("ss10", ("ligation",), codepoints="00B7:E653:E67A")
        assert oracle.classify_divergence(row) == "ss10-ligature-suppressed"

    def test_ss10_ligation_boundary_rows_stay_on_the_blanket(self):
        row = self._row("ss10", ("ligation",), codepoints="200C:E653:E67A")
        assert oracle.classify_divergence(row) == "boundary-echo"

    def test_ss10_ligation_without_a_formable_pair_matches_nothing(self):
        row = self._row("ss10", ("ligation",), codepoints="E650:E665:E652")
        assert oracle.classify_divergence(row) is None

    def test_non_ss10_ligation_keeps_marker_staging(self):
        row = self._row("ss03", ("ligation",), codepoints="E665:E652:E679")
        assert oracle.classify_divergence(row) == "marker-staging-ligature-formation"


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


_AUDIT_SHAPES = (
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
)


class TestOracleAudit:
    """`divergence-audit.tsv` is a fingerprinted artifact its readers parse straight off disk — the review surface's unit assembly, the census, the rebuild suite's filtered load — so the file's bytes are the contract, and they no longer come from one `"\n".join` in the parent: each configuration's rows are written where they are produced and the parent concatenates the shards behind the header. Pin the new assembly against the old formula over the shapes the audit can take, an empty configuration and an empty audit included, because those are where a hand-held layout drifts first — and pin the refusals, because the way this goes wrong is a short audit that reads as a complete one."""

    def _shard(self, scratch: Path, config: str, lines: Sequence[str], segment: int | None = None) -> None:
        shard = oracle.oracle_audit_shard(scratch, config, segment)
        shard.parent.mkdir(parents=True, exist_ok=True)
        with shard.open("w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line + "\n")

    @pytest.mark.parametrize("per_config", _AUDIT_SHAPES)
    def test_shards_concatenate_to_the_bytes_the_join_used_to_write(self, tmp_path, per_config):
        scratch = oracle.oracle_audit_scratch(tmp_path)
        for config, lines in per_config.items():
            self._shard(scratch, config, lines)
        every = [line for lines in per_config.values() for line in lines]
        oracle.join_oracle_audit(tmp_path, scratch, per_config, len(every))
        joined = "\n".join([oracle.ORACLE_AUDIT_HEADER, *every]) + "\n"
        assert (tmp_path / "divergence-audit.tsv").read_bytes() == joined.encode("utf-8")

    @pytest.mark.parametrize("per_config", _AUDIT_SHAPES)
    def test_a_cut_configurations_segments_concatenate_in_row_order_to_the_same_bytes(
        self, tmp_path, per_config
    ):
        """A configuration cut into row ranges arrives as one segment per range, and the join has to land on the bytes the uncut shard lands on however many pieces the rows arrived in — an empty second segment included, which is what a range holding no divergent row writes."""
        scratch = oracle.oracle_audit_scratch(tmp_path)
        for config, lines in per_config.items():
            half = len(lines) // 2
            self._shard(scratch, config, lines[:half], segment=0)
            self._shard(scratch, config, lines[half:], segment=1)
        every = [line for lines in per_config.values() for line in lines]
        segments = {config: 2 for config in per_config}
        oracle.join_oracle_audit(tmp_path, scratch, per_config, len(every), segments=segments)
        joined = "\n".join([oracle.ORACLE_AUDIT_HEADER, *every]) + "\n"
        assert (tmp_path / "divergence-audit.tsv").read_bytes() == joined.encode("utf-8")

    def test_a_cut_configuration_missing_one_segment_is_named_rather_than_joined_short(self, tmp_path):
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        scratch = oracle.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"], segment=0)
        with pytest.raises(FileNotFoundError, match="default"):
            oracle.join_oracle_audit(tmp_path, scratch, ("default",), 1, segments={"default": 2})
        assert standing.read_bytes() == b"the audit of the last green run\n"

    def test_the_frozen_mini_audit_reassembles_byte_for_byte(self, tmp_path):
        """The same pin over a real audit instead of hand-made rows: the mini bundle's audit.tsv is a live one filtered to four letters, still written by the old formula in `fixtures/mini/regenerate.py`, and its configuration runs are contiguous and in ACCEPTANCE_CONFIGS order — so splitting it back into shards and concatenating them has to land on the file it came from."""
        source = MINI / "audit.tsv"
        rows = source.read_text(encoding="utf-8").splitlines()
        assert rows[0] == oracle.ORACLE_AUDIT_HEADER
        per_config: dict[str, list[str]] = {config: [] for config in conform.ACCEPTANCE_CONFIGS}
        for row in rows[1:]:
            per_config[row.split("\t")[0]].append(row)
        scratch = oracle.oracle_audit_scratch(tmp_path)
        for config, lines in per_config.items():
            self._shard(scratch, config, lines)
        oracle.join_oracle_audit(tmp_path, scratch, conform.ACCEPTANCE_CONFIGS, len(rows) - 1)
        assert (tmp_path / "divergence-audit.tsv").read_bytes() == source.read_bytes()

        cut = tmp_path / "cut"
        cut.mkdir()
        scratch = oracle.oracle_audit_scratch(cut)
        for config, lines in per_config.items():
            half = len(lines) // 2
            self._shard(scratch, config, lines[:half], segment=0)
            self._shard(scratch, config, lines[half:], segment=1)
        oracle.join_oracle_audit(
            cut,
            scratch,
            conform.ACCEPTANCE_CONFIGS,
            len(rows) - 1,
            segments={config: 2 for config in conform.ACCEPTANCE_CONFIGS},
        )
        assert (cut / "divergence-audit.tsv").read_bytes() == source.read_bytes()

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
        in_process = oracle.compare_against_baseline(
            spec, tables, aliases, ledger, configs=configs, out_dir=serial
        )
        assert in_process.divergent_rows == 3
        assert [path.name for path in serial.iterdir()] == ["divergence-audit.tsv"]

        fanned = tmp_path / "fanned"
        fanned.mkdir()
        scratch = oracle.oracle_audit_scratch(fanned)
        merged = oracle.merge_oracle_results(
            oracle.oracle_config_worker(spec, tables, aliases, ledger, config, None, None, audit_dir=scratch)
            for config in configs
        )
        oracle.join_oracle_audit(fanned, scratch, configs, merged.divergent_rows)
        assert merged.divergent_rows == in_process.divergent_rows
        assert (fanned / "divergence-audit.tsv").read_bytes() == (
            serial / "divergence-audit.tsv"
        ).read_bytes()
        oracle.discard_oracle_audit_scratch(fanned)
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

        def compare(spec, tables, config, *rest, **_cache):
            audit = rest[-1]
            assert audit is not None
            audit.write(f"{config}\tE650\tcell\tpea-half\tqsPea\tqsPea.half\n")
            if config == "ss03":
                raise RuntimeError("ss03 fell over")
            return oracle.OracleConfigResult(config=config, divergent_rows=1)

        monkeypatch.setattr(oracle, "_compare_config", compare)
        with pytest.raises(RuntimeError):
            oracle.compare_against_baseline(
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
        scratch = oracle.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        with pytest.raises(FileNotFoundError, match="ss03"):
            oracle.join_oracle_audit(tmp_path, scratch, ("default", "ss03"), 1)
        assert standing.read_bytes() == b"the audit of the last green run\n"

    def test_an_audit_short_of_the_rows_its_workers_counted_is_not_promoted(self, tmp_path):
        """The counts come home through the pipe and the bytes come home on disk, so comparing them is the one cross-check the parent can make — and it is what catches a shard that was truncated but still closed clean, which is the shape no amount of stat-ing finds."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        scratch = oracle.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        with pytest.raises(ValueError, match="2 divergent"):
            oracle.join_oracle_audit(tmp_path, scratch, ("default",), 2)
        assert standing.read_bytes() == b"the audit of the last green run\n"

    def test_the_scratch_goes_whether_or_not_the_oracle_got_that_far(self, tmp_path):
        scratch = oracle.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        self._shard(scratch, "ss03+ss05", [])
        oracle.discard_oracle_audit_scratch(tmp_path)
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
        oracle.discard_oracle_audit_scratch(tmp_path)
        assert not stale.exists()
        assert (live / "default.part").is_file()

    def test_a_scratch_name_is_not_mistaken_for_an_artifact(self):
        """The scratch directory sits beside the artifacts in rebuild/out/m1, so its name has to miss everything that reads that directory: the cycle's artifact list, its subset-table glob, and the table readers' own patterns. Configuration names carry `+`, which the baseline tables already prove is safe in a filename here."""
        from rebuild.tools.artifact_cycle import M1_ARTIFACT_NAMES

        scratch = oracle.oracle_audit_scratch(Path("m1"))
        assert scratch.name not in set(M1_ARTIFACT_NAMES)
        for name in [scratch.name] + [
            oracle.oracle_audit_shard(scratch, config).name for config in conform.ACCEPTANCE_CONFIGS
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
        names = [oracle.oracle_audit_shard(scratch, config).name for config in conform.ACCEPTANCE_CONFIGS]
        assert len(set(names)) == len(conform.ACCEPTANCE_CONFIGS)


class TestOracleUnmatchedTally:
    """What a configuration sends home about its unmatched rows. Every one of them is already on disk in that configuration's audit shard, and `oracle_summary.json` asks the result object for two things only — how many there were, and `ORACLE_UNMATCHED_EXEMPLARS` of them to quote — so the result carries a count and that first slice rather than the whole list, which on a live run is a six-figure pile of `DivergentRow` objects pickled across a process pipe to be counted. Pin the count, the cap, the stream order the exemplars have to keep for the summary to quote the same ones, and the gate verdict's dependence on the count rather than on the sample."""

    def _tables(self, tmp_path: Path, per_config: dict[str, list[tuple[int, int]]]) -> Path:
        tables = tmp_path / "tables"
        tables.mkdir()
        for config, pairs in per_config.items():
            with gzip.open(tables / f"baseline-{config}.subset.tsv.gz", "wt", encoding="utf-8") as fh:
                fh.write(f"# config: {config}\n")
                for left, right in pairs:
                    fh.write(
                        f"{left:04X}:{right:04X}\told{left:04X}|old{right:04X}\t0,1\tbreak"
                        "\t0,0,150|150,0,150\n"
                    )
        return tables

    def test_a_configuration_sends_home_a_count_and_the_first_twenty_rows(self, spec, tmp_path):
        letters = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667, 0xE670, 0xE679, 0xE67A)
        pairs = [(left, right) for left in letters for right in letters]
        wide = pairs[:25]
        narrow = pairs[25:28]
        tables = self._tables(tmp_path, {"default": wide, "ss03": narrow})
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("".join(f"old{code:04X}: pending\n" for code in letters))
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        configs = ("default", "ss03")

        serial = tmp_path / "serial"
        report = oracle.compare_against_baseline(
            spec, tables, aliases, ledger, configs=configs, out_dir=serial
        )
        assert report.unmatched_count == 28
        assert len(report.unmatched_exemplars) == oracle.ORACLE_UNMATCHED_EXEMPLARS + 3
        quoted = report.unmatched_exemplars[: oracle.ORACLE_UNMATCHED_EXEMPLARS]
        assert [row.codepoints for row in quoted] == [
            f"{left:04X}:{right:04X}" for left, right in wide[: oracle.ORACLE_UNMATCHED_EXEMPLARS]
        ]
        assert all(row.phenomena for row in quoted)
        audit = (serial / "divergence-audit.tsv").read_text(encoding="utf-8").splitlines()
        assert len(audit) == 29
        assert sum(line.split("\t")[3] == "UNMATCHED" for line in audit[1:]) == 28

        fanned = tmp_path / "fanned"
        fanned.mkdir()
        scratch = oracle.oracle_audit_scratch(fanned)
        merged = oracle.merge_oracle_results(
            oracle.oracle_config_worker(spec, tables, aliases, ledger, config, None, None, audit_dir=scratch)
            for config in configs
        )
        oracle.discard_oracle_audit_scratch(fanned)
        assert merged.unmatched_count == report.unmatched_count
        assert [row.codepoints for row in merged.unmatched_exemplars] == [
            row.codepoints for row in report.unmatched_exemplars
        ]

    def test_the_exemplar_cap_survives_the_fold_of_a_cut_configuration(self, spec, tmp_path):
        """The wide configuration cut so its first range holds fewer unmatched rows than the cap: the fold has to quote the first twenty in table order across the ranges, which is what `oracle_summary.json` prints, and count every unmatched row whichever range it fell in."""
        letters = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667, 0xE670, 0xE679, 0xE67A)
        pairs = [(left, right) for left in letters for right in letters]
        wide = pairs[:25]
        tables = self._tables(tmp_path, {"default": wide})
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("".join(f"old{code:04X}: pending\n" for code in letters))
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        scratch = oracle.oracle_audit_scratch(tmp_path)
        shards = [oracle.OracleShard("default", 0, 10, 0, 2), oracle.OracleShard("default", 10, None, 1, 2)]
        ranged = [
            oracle.oracle_config_worker(
                spec, tables, aliases, ledger, "default", None, None, audit_dir=scratch, shard=shard
            )
            for shard in shards
        ]
        assert [result.unmatched_count for result in ranged] == [10, 15]
        assert [len(result.unmatched_exemplars) for result in ranged] == [10, 15]
        merged = oracle.merge_config_shards(ranged)
        assert merged.unmatched_count == 25
        assert [row.codepoints for row in merged.unmatched_exemplars] == [
            f"{left:04X}:{right:04X}" for left, right in wide[: oracle.ORACLE_UNMATCHED_EXEMPLARS]
        ]
        assert merged.rows_compared == 25 and merged.divergent_rows == 25
        oracle.discard_oracle_audit_scratch(tmp_path)


CACHE_LETTERS = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667)


_INK_IDENTICAL_LEDGER = "- id: ink-identical\n  ink_identical: true\n  match: {}\n"


def _cache_subset_table(directory: Path, config: str, rows: Sequence[tuple[int, ...]]) -> Path:
    """One hand-made subset table in the shape `iter_rows` reads, over rows whose old glyph names are minted from their own codepoints so an all-pending alias map makes every row diverge and an empty ledger leaves every divergence UNMATCHED. The names deliberately miss the `qs` prefix `unreachable_glyph_heads` looks for, so no row here is refused service for citing a family its codepoints cannot reach."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"baseline-{config}.subset.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(f"# config: {config}\n")
        for codepoints in rows:
            handle.write(
                "\t".join(
                    (
                        ":".join(f"{codepoint:04X}" for codepoint in codepoints),
                        "|".join(f"old{codepoint:04X}" for codepoint in codepoints),
                        ",".join(str(index) for index in range(len(codepoints))),
                        ",".join(["break"] * (len(codepoints) - 1)),
                        "|".join(["0,0,150"] * len(codepoints)),
                    )
                )
                + "\n"
            )
    return path


def _cache_stamp(config: str, table: Path) -> oracle_cache.EnvironmentStamp:
    """A stamp in the shape `run_m1` cuts one, reduced to what a store's identity turns on here: the format, the configuration it belongs to, a stand-in for the code closure this lane does not vary, and the `subset` line `open_row_cache` reads the table's digest off."""
    return oracle_cache.EnvironmentStamp(
        lines=(
            f"format\t{oracle_cache.STORE_FORMAT}",
            f"config\t{config}",
            "oracle_code\tpinned-by-the-test",
            f"subset\t{hashlib.sha256(table.read_bytes()).hexdigest()}",
        )
    )


def _cache_ages(path: Path) -> list[int]:
    """Every record's row `derived_at_pass`, read straight out of the store rather than through `load_store`, so what a pass recomputed is observed independently of the reader that decides what a pass may serve. A record whose age is this pass's ordinal was derived here; one carrying an older ordinal was served."""
    body = gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    return [int(line.rsplit("\t", 2)[1]) for line in body[1:-1]]


def _cache_position_ages(path: Path) -> list[int]:
    """The same read over the position verdict's own pass, the last field of every record."""
    body = gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    return [int(line.rsplit("\t", 1)[1]) for line in body[1:-1]]


def _cache_position_tags(path: Path) -> list[str]:
    """Each record's position tag — `?` never shaped, `-` shaped clean, `D` drifted — so a test can see which rows a pass carried a position for without loading the store."""
    tags: list[str] = []
    for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()[1:-1]:
        fields = line.split("\t")
        tags.append(fields[2] if fields[1] == "-" else fields[7])
    return tags


def _excluded_from_the_channel(audit: Path, rows: int) -> set[int]:
    """The rows the position channel skips whatever the ledger says — a ligation or seam divergence — read off an audit every row of which is divergent, so its line order is the table's."""
    lines = audit.read_text().splitlines()[1:]
    assert len(lines) == rows
    return {
        index
        for index, line in enumerate(lines)
        if {"ligation", "seam"} & set(line.split("\t")[2].split(","))
    }


def _tea_prefers_half_before_may(spec):
    """A real rune edit that moves settlement: ·Tea gains an absolute preference for its half stance before ·May, which changes what the ·Tea·May windows settle to and nothing else."""
    import dataclasses

    from rebuild.pipeline import model

    tea = spec.runes["qsTea"]
    prefer = model.PolicyRecord(
        kind="prefer",
        stance="half",
        mode="absolute",
        when=model.When(right=model.Condition(family=("qsMay",))),
    )
    runes = dict(spec.runes)
    runes["qsTea"] = dataclasses.replace(tea, policy=dataclasses.replace(tea.policy, prefer=(prefer,)))
    return dataclasses.replace(spec, runes=runes)


def _tea_oy_refuses_its_exit(spec):
    """A real edit to the ligature rune and to no component's file: ·Tea+Oy refuses its baseline exit before every letter, so every window whose left slot is a formed ·Tea+Oy settles to a break where it joined, while ·Tea's and ·Oy's own windows stand."""
    import dataclasses

    from rebuild.pipeline import model

    ligature = spec.runes["qsTea_qsOy"]
    letters = tuple(
        sorted(name for name, info in spec.registry.families.items() if info.codepoint is not None)
    )
    refuse = model.PolicyRecord(
        kind="refuse", exit="baseline", when=model.When(right=model.Condition(family=letters))
    )
    runes = dict(spec.runes)
    runes["qsTea_qsOy"] = dataclasses.replace(
        ligature, policy=dataclasses.replace(ligature.policy, refuse=(refuse,))
    )
    return dataclasses.replace(spec, runes=runes)


def _font_edited(source: Path, target: Path, touches) -> Path:
    """A copy of `source` whose glyphs `touches` names are advanced by a pixel-odd amount: a family's compiled digest moves through its metrics alone, every outline stays, and every row that shapes one of those glyphs draws its followers somewhere else."""
    from fontTools.ttLib import TTFont

    font = TTFont(str(source))
    metrics = font["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
    for name in list(metrics):
        if touches(name):
            advance, bearing = metrics[name]
            metrics[name] = (advance + 37, bearing)
    font.save(str(target))
    return target


def _cache_renewed(rows: int, pass_ordinal: int) -> set[int]:
    """The rows this pass re-derives whatever their families did — `RowStore.due`'s ordinal clause, which retires one record in `MAX_RECORD_AGE` every pass so no verdict can stand that many passes unproven. It is why no arm below asserts a served fraction of exactly one."""
    current = pass_ordinal + 1
    return {
        index
        for index in range(rows)
        if current % oracle_cache.MAX_RECORD_AGE == index % oracle_cache.MAX_RECORD_AGE
    }


def _position_bench(spec, tmp_path: Path, ledger_entries: str = _INK_IDENTICAL_LEDGER):
    """The position channel's bench: the frozen mini bundle's default rows that fall inside the mini alphabet — real old-font positions, real old glyph names — under an all-pending alias map, so every row diverges at name grain and stays topology-clean, and a ledger whose one ink-identical entry admits every row to the channel. The font is the bundle's frozen M1.otf, which is the after font those rows were extracted against. Three hand-made ·Tea·May rows ride at the end, because the bundle's four-letter slice holds none and the rune edit the arms share moves exactly that pair."""
    letters = {rune.codepoint for rune in spec.runes.values() if rune.codepoint is not None}
    boundaries = {token.codepoint for token in spec.registry.boundary_tokens.values()}
    rows: list[str] = []
    names: set[str] = set()
    with gzip.open(MINI / "baseline-default.subset.tsv.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            codepoints = {int(item, 16) for item in line.split("\t")[0].split(":")}
            if codepoints <= letters | boundaries and codepoints & letters:
                rows.append(line.rstrip("\n"))
                names.update(line.split("\t")[1].split("|"))
    assert rows
    for text in ("E652:E665", "E665:E652", "E650:E652:E665"):
        glyphs = [{"E650": "qsPea", "E652": "qsTea", "E665": "qsMay"}[item] for item in text.split(":")]
        names.update(glyphs)
        rows.append(
            "\t".join(
                (
                    text,
                    "|".join(glyphs),
                    ",".join(str(index) for index in range(len(glyphs))),
                    ",".join(["break"] * (len(glyphs) - 1)),
                    "|".join(["0,0,150"] * len(glyphs)),
                )
            )
        )
    tables = tmp_path / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    table = tables / "baseline-default.subset.tsv.gz"
    with gzip.open(table, "wt", encoding="utf-8") as handle:
        handle.write("# config: default\n")
        for row in rows:
            handle.write(row + "\n")
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("".join(f"{name}: pending\n" for name in sorted(names - labels.BOUNDARY_GLYPH_NAMES)))
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(ledger_entries)
    stamps = {"default": _cache_stamp("default", table)}
    rows_named = [tuple(int(item, 16) for item in row.split("\t")[0].split(":")) for row in rows]
    return tables, aliases, ledger, stamps, ("default",), rows_named


class TestOracleRowCache:
    """The persisted per-row oracle cache, at the grain the audit's bytes are the contract at. Everything here runs the real `compare_against_baseline` over hand-made subset tables and synthetic family keys: a served pass and a cold one have to land on the same file, an edit to any number of runes has to re-derive the rows naming those runes and no others, and every way a store can be wrong about the table under it has to cost one full pass rather than one wrong audit. There is no k threshold anywhere in the cache and so none in these arms either — the parametrized edit runs to four moved families and still expects a union."""

    LETTER_ROWS: Sequence[tuple[int, ...]] = tuple((letter,) for letter in CACHE_LETTERS) + tuple(
        (left, right) for left in CACHE_LETTERS for right in CACHE_LETTERS
    )
    CONFIGS = ("default", "ss03")

    def _bench(self, tmp_path: Path, rows: Sequence[tuple[int, ...]] | None = None, configs=None):
        rows = self.LETTER_ROWS if rows is None else rows
        configs = self.CONFIGS if configs is None else configs
        tables = tmp_path / "tables"
        stamps = {
            config: _cache_stamp(config, _cache_subset_table(tables, config, rows)) for config in configs
        }
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("".join(f"old{letter:04X}: pending\n" for letter in CACHE_LETTERS))
        return tables, aliases, stamps, configs

    def _keys(self, spec, generation: int = 0, moved: Sequence[str] = ()) -> dict[str, str]:
        bumped = set(moved)
        return {
            name: f"{name}@{generation + 1 if name in bumped else generation}"
            for name in spec.registry.families
        }

    def _pass(
        self,
        spec,
        tmp_path: Path,
        name: str,
        *,
        tables: Path,
        aliases: Path,
        ledger: Path,
        stamps,
        keys,
        configs,
        read_dir: Path | None = None,
        write: bool = True,
        cached: bool = True,
        font: Path | None = None,
        kern: Path | None = None,
        position=None,
    ):
        out = tmp_path / name
        scratch = tmp_path / f"{name}-scratch"
        stores = tmp_path / f"{name}-stores"
        position_keys, position_stamp = (None, None) if position is None else position
        row_cache = (
            oracle.OracleRowCache(
                stamps,
                keys,
                read_dir=read_dir,
                write_dir=scratch if write else None,
                position_environment=position_stamp,
                position_keys=position_keys,
            )
            if cached
            else None
        )
        report = oracle.compare_against_baseline(
            spec,
            tables,
            aliases,
            ledger,
            configs=configs,
            out_dir=out,
            font_path=font,
            kern_sidecar_path=kern,
            row_cache=row_cache,
        )
        if cached and write:
            assert oracle_cache.promote_stores(scratch, stores, configs) == list(configs)
        return report, out / "divergence-audit.tsv", stores

    def _position_bench(self, spec, tmp_path: Path, ledger_entries: str = _INK_IDENTICAL_LEDGER):
        return _position_bench(spec, tmp_path, ledger_entries)

    def test_a_served_position_channel_writes_the_audit_a_cold_one_writes(self, spec, tmp_path):
        """The position store's whole correctness claim in one arm, the shape of the row store's: a pass that took its position verdicts off the previous pass's store writes the byte-identical `divergence-audit.tsv` a cold pass writes over the same font, with the uncached path as the third witness, and served every position but the renewal slice. The bench drifts for real — the old font's positions against the frozen after font — so the audit carries position rows and the equality is over drift descriptions, not over an empty channel."""
        tables, aliases, ledger, stamps, configs, rows = self._position_bench(spec, tmp_path)
        keys = self._keys(spec)
        position = oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", None)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )
        shared.update(font=MINI / "M1.otf", position=position)

        cold_report, cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", **shared)
        served_report, served_audit, served_stores = self._pass(
            spec, tmp_path, "served", read_dir=cold_stores, **shared
        )
        _uncached, uncached_audit, _ = self._pass(spec, tmp_path, "uncached", cached=False, **shared)

        excluded = _excluded_from_the_channel(cold_audit, len(rows))
        assert 0 < len(excluded) < len(rows)
        assert cold_report.positions_compared == len(rows) - len(excluded)
        assert cold_report.positions_served == 0
        assert any("position" in line.split("\t")[2] for line in cold_audit.read_text().splitlines()[1:])
        assert served_audit.read_bytes() == cold_audit.read_bytes() == uncached_audit.read_bytes()
        assert asdict(served_report) == {
            **asdict(cold_report),
            "positions_served": served_report.positions_served,
        }
        renewed = _cache_renewed(len(rows), 0)
        assert served_report.positions_served == len(rows) - len(renewed | excluded)
        store = oracle_cache.store_path(served_stores, "default")
        assert {
            index for index, age in enumerate(_cache_position_ages(store)) if age == 1
        } == renewed | excluded
        assert {index for index, age in enumerate(_cache_ages(store)) if age == 1} == renewed
        assert {index for index, tag in enumerate(_cache_position_tags(store)) if tag == "?"} == excluded

    def test_a_glyph_edit_re_shapes_exactly_the_rows_that_reach_its_family(self, spec, tmp_path):
        """The position key's grain end to end: the after font's ·Tea glyphs gain an advance, so every row that shapes one draws its followers elsewhere. A pass carrying the previous store across that font has to write the audit a pass that never saw a store writes over the edited font, and the store it leaves shows exactly the rows naming ·Tea re-shaped — every other position served, every row verdict served, so a font edit costs the shaping it moved and nothing settled."""
        tables, aliases, ledger, stamps, configs, rows = self._position_bench(spec, tmp_path)
        keys = self._keys(spec)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )
        cold_position = oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", None)
        _cold, cold_audit, cold_stores = self._pass(
            spec, tmp_path, "cold", font=MINI / "M1.otf", position=cold_position, **shared
        )

        edited = _font_edited(
            MINI / "M1.otf", tmp_path / "tea.otf", lambda name: name.split(".")[0] == "qsTea"
        )
        position = oracle_cache.position_keys(REPO_ROOT, keys, edited, None)
        assert oracle_cache.moved_families(cold_position[0], position[0]) == frozenset({"qsTea"})
        carried_report, carried_audit, carried_stores = self._pass(
            spec, tmp_path, "carried", read_dir=cold_stores, font=edited, position=position, **shared
        )
        _fresh, fresh_audit, _ = self._pass(spec, tmp_path, "fresh", cached=False, font=edited, **shared)

        assert carried_audit.read_bytes() == fresh_audit.read_bytes()
        assert fresh_audit.read_bytes() != cold_audit.read_bytes(), "the glyph edit moved no row"
        naming = {index for index, codepoints in enumerate(rows) if 0xE652 in codepoints}
        excluded = _excluded_from_the_channel(cold_audit, len(rows))
        expected = naming | _cache_renewed(len(rows), 0)
        store = oracle_cache.store_path(carried_stores, "default")
        assert {
            index for index, age in enumerate(_cache_position_ages(store)) if age == 1
        } == expected | excluded
        assert {index for index, age in enumerate(_cache_ages(store)) if age == 1} == _cache_renewed(
            len(rows), 0
        )
        assert carried_report.positions_served == len(rows) - len(expected | excluded)

    def test_a_rune_edit_re_shapes_the_rows_it_re_derives(self, spec, tmp_path):
        """The row key inside the position key, end to end: the same ·Tea edit the row arm uses moves ·Tea's row key, so its rows re-derive and re-shape together — a served position over a fresh settlement would be shaping the previous pass's cells — and the audit a carried pass writes over the edited spec is the one a from-scratch pass writes."""
        tables, aliases, ledger, stamps, configs, rows = self._position_bench(spec, tmp_path)
        shared: dict[str, Any] = dict(
            tables=tables,
            aliases=aliases,
            ledger=ledger,
            stamps=stamps,
            configs=configs,
            font=MINI / "M1.otf",
        )
        keys = self._keys(spec)
        _cold, cold_audit, cold_stores = self._pass(
            spec,
            tmp_path,
            "cold",
            keys=keys,
            position=oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", None),
            **shared,
        )
        edited = _tea_prefers_half_before_may(spec)
        moved_keys = self._keys(spec, moved=("qsTea",))
        position = oracle_cache.position_keys(REPO_ROOT, moved_keys, MINI / "M1.otf", None)
        _carried, carried_audit, carried_stores = self._pass(
            edited, tmp_path, "carried", keys=moved_keys, position=position, read_dir=cold_stores, **shared
        )
        _fresh, fresh_audit, _ = self._pass(
            edited, tmp_path, "fresh", keys=moved_keys, cached=False, **shared
        )
        assert carried_audit.read_bytes() == fresh_audit.read_bytes()
        assert fresh_audit.read_bytes() != cold_audit.read_bytes(), "the rune edit moved no row"
        expected = {index for index, codepoints in enumerate(rows) if 0xE652 in codepoints} | _cache_renewed(
            len(rows), 0
        )
        excluded = _excluded_from_the_channel(fresh_audit, len(rows))
        store = oracle_cache.store_path(carried_stores, "default")
        assert {index for index, age in enumerate(_cache_ages(store)) if age == 1} == expected
        assert {
            index for index, age in enumerate(_cache_position_ages(store)) if age == 1
        } == expected | excluded

    def test_a_kern_sidecar_edit_re_shapes_every_row_and_serves_every_verdict(self, spec, tmp_path):
        """The position stamp end to end: a sidecar edit moves what every row's old positions normalize to, so every position re-shapes while every row verdict is still served, and the carried audit is the from-scratch one over the edited sidecar."""
        tables, aliases, ledger, stamps, configs, rows = self._position_bench(spec, tmp_path)
        keys = self._keys(spec)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )
        shared.update(font=MINI / "M1.otf")
        kern = tmp_path / "kern.yaml"
        kern.write_text("global:\n  value: 0\n")
        _cold, cold_audit, cold_stores = self._pass(
            spec,
            tmp_path,
            "cold",
            kern=kern,
            position=oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", kern),
            **shared,
        )
        kern.write_text("global:\n  value: 5\n")
        position = oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", kern)
        carried_report, carried_audit, carried_stores = self._pass(
            spec, tmp_path, "carried", kern=kern, position=position, read_dir=cold_stores, **shared
        )
        _fresh, fresh_audit, _ = self._pass(spec, tmp_path, "fresh", kern=kern, cached=False, **shared)
        assert carried_audit.read_bytes() == fresh_audit.read_bytes()
        assert fresh_audit.read_bytes() != cold_audit.read_bytes(), "the sidecar edit moved no row"
        assert carried_report.positions_served == 0
        store = oracle_cache.store_path(carried_stores, "default")
        assert set(_cache_position_ages(store)) == {1}
        assert {index for index, age in enumerate(_cache_ages(store)) if age == 1} == _cache_renewed(
            len(rows), 0
        )

    def test_a_ledger_edit_that_admits_a_row_shapes_it_and_one_that_excludes_it_keeps_its_verdict(
        self, spec, tmp_path
    ):
        """The verdict is stored raw, ahead of the ledger's eligibility test: a pass whose ledger admits no row to the channel records every position as never shaped, and the next pass under an admitting ledger shapes them all and serves none — the audit still the from-scratch one — while a pass whose ledger excludes a row it once shaped carries that verdict forward unread, so the pass after it, admitting the row again, finds it served."""
        tables, aliases, admitting, stamps, configs, rows = self._position_bench(spec, tmp_path)
        excluding = tmp_path / "excluding.yaml"
        excluding.write_text("[]\n")
        keys = self._keys(spec)
        position = oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", None)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, stamps=stamps, keys=keys, configs=configs
        )
        shared.update(font=MINI / "M1.otf", position=position)

        excluded_report, _audit, excluded_stores = self._pass(
            spec, tmp_path, "excluded", ledger=excluding, **shared
        )
        assert excluded_report.positions_compared == 0
        assert set(_cache_position_tags(oracle_cache.store_path(excluded_stores, "default"))) == {"?"}

        admitted_report, admitted_audit, admitted_stores = self._pass(
            spec, tmp_path, "admitted", ledger=admitting, read_dir=excluded_stores, **shared
        )
        _fresh, fresh_audit, _ = self._pass(spec, tmp_path, "fresh", ledger=admitting, cached=False, **shared)
        assert admitted_audit.read_bytes() == fresh_audit.read_bytes()
        excluded = _excluded_from_the_channel(fresh_audit, len(rows))
        assert admitted_report.positions_compared == len(rows) - len(excluded)
        assert admitted_report.positions_served == 0
        tags = _cache_position_tags(oracle_cache.store_path(admitted_stores, "default"))
        assert {index for index, tag in enumerate(tags) if tag == "?"} == excluded

        carried_report, _audit, carried_stores = self._pass(
            spec, tmp_path, "carried", ledger=excluding, read_dir=admitted_stores, **shared
        )
        assert carried_report.positions_compared == 0
        tags = _cache_position_tags(oracle_cache.store_path(carried_stores, "default"))
        assert {index for index, tag in enumerate(tags) if tag == "?"} == _cache_renewed(
            len(rows), 1
        ) | excluded

        again_report, again_audit, _ = self._pass(
            spec, tmp_path, "again", ledger=admitting, read_dir=carried_stores, **shared
        )
        assert again_audit.read_bytes() == fresh_audit.read_bytes()
        assert again_report.positions_served == len(rows) - len(
            _cache_renewed(len(rows), 1) | _cache_renewed(len(rows), 2) | excluded
        )

    def test_a_served_oracle_writes_the_audit_a_cold_one_writes(self, spec, tmp_path):
        """The whole correctness claim in one arm: a pass that took its verdicts off the previous pass's store writes the byte-identical `divergence-audit.tsv` a cold pass writes, and sends home a tally equal in every field — the counts, the exemplars and the unmatched rows they quote included. The uncached path runs beside them as the third witness, because the claim is not that the two cached passes agree with each other but that neither of them moved the file."""
        tables, aliases, stamps, configs = self._bench(tmp_path)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        keys = self._keys(spec)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )

        cold_report, cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", **shared)
        served_report, served_audit, served_stores = self._pass(
            spec, tmp_path, "served", read_dir=cold_stores, **shared
        )
        _uncached_report, uncached_audit, _ = self._pass(spec, tmp_path, "uncached", cached=False, **shared)

        assert cold_report.unmatched_count == len(self.LETTER_ROWS) * len(configs)
        assert served_audit.read_bytes() == cold_audit.read_bytes()
        assert uncached_audit.read_bytes() == cold_audit.read_bytes()
        assert asdict(served_report) == asdict(cold_report)

        for config in configs:
            ages = _cache_ages(oracle_cache.store_path(served_stores, config))
            assert len(ages) == len(self.LETTER_ROWS)
            assert set(ages) == {0, 1}
            assert {index for index, age in enumerate(ages) if age == 1} == _cache_renewed(len(ages), 0)

    @pytest.mark.parametrize("k", (1, 2, 3, 4))
    def test_an_edit_to_k_runes_re_derives_exactly_the_rows_that_name_them(self, spec, tmp_path, k):
        """The arm that pins away the fallback clause issue 24 proposed. However many runes moved, the rows re-derived are exactly those naming one of them — a union, never a threshold and never a whole-store drop — so a four-rune edit still serves every row that reaches none of the four. The renewal clause is added to the expectation rather than subtracted from the cache, because it is a property of the store and not of the edit."""
        tables, aliases, stamps, configs = self._bench(tmp_path)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        family_of = {
            info.codepoint: name
            for name, info in spec.registry.families.items()
            if info.codepoint is not None
        }
        moved = sorted(family_of[letter] for letter in CACHE_LETTERS[:k])
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, configs=configs
        )

        _cold, cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", keys=self._keys(spec), **shared)
        edited_report, edited_audit, edited_stores = self._pass(
            spec,
            tmp_path,
            "edited",
            keys=self._keys(spec, moved=moved),
            read_dir=cold_stores,
            **shared,
        )
        assert edited_audit.read_bytes() == cold_audit.read_bytes()
        assert edited_report.rows_compared == len(self.LETTER_ROWS) * len(configs)

        naming = {
            index
            for index, codepoints in enumerate(self.LETTER_ROWS)
            if {family_of[codepoint] for codepoint in codepoints} & set(moved)
        }
        expected = naming | _cache_renewed(len(self.LETTER_ROWS), 0)
        for config in configs:
            ages = _cache_ages(oracle_cache.store_path(edited_stores, config))
            assert {index for index, age in enumerate(ages) if age == 1} == expected
        assert naming, "the edit reached no row at all"
        assert len(expected) < len(self.LETTER_ROWS), "a k-rune edit dropped the whole store"

    def test_incremental_equals_from_scratch_after_a_real_rune_edit(self, spec, tmp_path):
        """The served claim end to end, over an edit that genuinely moves settlement: ·Tea gains a preference for its half stance before ·May, which changes what the ·Tea·May row settles to and nothing else. A pass that carries the previous store across that edit has to write the audit a pass that never saw a store writes over the same edited spec — so a store that served the changed row, or a rune edit whose reach the mask under-counts, fails here rather than shipping a stale verdict into a fingerprinted artifact."""
        tables, aliases, stamps, configs = self._bench(tmp_path)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, configs=configs
        )
        _cold, cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", keys=self._keys(spec), **shared)

        edited = _tea_prefers_half_before_may(spec)

        keys = self._keys(spec, moved=("qsTea",))
        _carried, carried_audit, _stores = self._pass(
            edited, tmp_path, "carried", keys=keys, read_dir=cold_stores, **shared
        )
        _fresh, fresh_audit, _ = self._pass(edited, tmp_path, "fresh", keys=keys, cached=False, **shared)

        assert carried_audit.read_bytes() == fresh_audit.read_bytes()
        assert fresh_audit.read_bytes() != cold_audit.read_bytes(), "the rune edit moved no row"

    def test_a_ledger_edit_serves_every_row_and_still_rewrites_the_matches(self, spec, tmp_path):
        """The workflow the cache exists for. `rebuild/m1-divergences.yaml` is outside the key by construction — `_match_ledger` runs on every row on every pass, served or not — so replacing the ledger re-derives nothing beyond the pass's own renewal, and every `matched_entry` in the audit still moves exactly as it moves for a pass that compared every row from scratch."""
        tables, aliases, stamps, configs = self._bench(tmp_path)
        empty = tmp_path / "empty-ledger.yaml"
        empty.write_text("[]\n")
        adjudicated = tmp_path / "ledger.yaml"
        adjudicated.write_text("- id: every-unaliased-row\n  match: {}\n")
        keys = self._keys(spec)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, stamps=stamps, keys=keys, configs=configs
        )

        cold_report, cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", ledger=empty, **shared)
        served_report, served_audit, served_stores = self._pass(
            spec, tmp_path, "served", ledger=adjudicated, read_dir=cold_stores, **shared
        )
        _fresh, fresh_audit, _ = self._pass(
            spec, tmp_path, "fresh", ledger=adjudicated, cached=False, **shared
        )

        for config in configs:
            ages = _cache_ages(oracle_cache.store_path(served_stores, config))
            assert {index for index, age in enumerate(ages) if age == 1} == _cache_renewed(len(ages), 0)

        assert served_audit.read_bytes() == fresh_audit.read_bytes()
        assert served_audit.read_bytes() != cold_audit.read_bytes()
        matched = [line.split("\t")[3] for line in served_audit.read_text().splitlines()[1:]]
        assert set(matched) == {"every-unaliased-row"}
        assert set(line.split("\t")[3] for line in cold_audit.read_text().splitlines()[1:]) == {"UNMATCHED"}
        assert cold_report.unmatched_count and not served_report.unmatched_count

    @pytest.mark.parametrize(
        "damage",
        (
            "truncated",
            "corrupt-body",
            "garbled-header",
            "short-count",
            "another-table",
            "misaligned-record",
        ),
    )
    def test_a_corrupt_or_short_or_misaligned_store_costs_a_full_pass(self, spec, tmp_path, damage):
        """Every doubt about a store costs one cold oracle and nothing else — a store that will not load is not a store, and a pass that finds one starts its own at ordinal zero. The misaligned record is the exception the design draws on purpose: an anchor that disagrees with the row under it does not mean this record is wrong, it means the table beneath the whole store was replaced, so it aborts loudly instead of degrading into a miss that would serve every other record just as wrongly."""
        tables, aliases, stamps, configs = self._bench(tmp_path, configs=("default",))
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        keys = self._keys(spec)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )
        _cold, cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", **shared)

        store = oracle_cache.store_path(cold_stores, "default")
        if damage == "truncated":
            raw = store.read_bytes()
            store.write_bytes(raw[: len(raw) // 2])
        elif damage == "corrupt-body":
            # A flipped bit inside the deflate stream, which is what bit rot and a store copied half-written actually look like. It raises out of the compression layer rather than as an `OSError`, so a reader that catches only file errors takes the whole build down over a file whose only job is to save time — and a truncation, which is the arm above, never produces it.
            raw = bytearray(store.read_bytes())
            for offset in range(len(raw) // 2, len(raw) - 8):
                candidate = bytearray(raw)
                candidate[offset] ^= 0x01
                try:
                    gzip.decompress(bytes(candidate))
                except zlib.error:
                    store.write_bytes(bytes(candidate))
                    break
                except Exception:
                    continue
            else:
                pytest.fail("no single-bit flip in this store produced a zlib-level corruption")
        else:
            body = gzip.decompress(store.read_bytes()).decode("utf-8").splitlines()
            if damage == "garbled-header":
                body[0] = "{ this was a header once"
            elif damage == "short-count":
                body[-1] = f"{oracle_cache.ROW_COUNT_TRAILER}\t{len(body) - 2 + 1}"
            elif damage == "another-table":
                body[0] = body[0].replace(stamps["default"].labels["subset"], "0" * 64)
            else:
                body[1] = "0" * oracle_cache.ANCHOR_WIDTH + body[1][oracle_cache.ANCHOR_WIDTH :]
            store.write_bytes(gzip.compress(("\n".join(body) + "\n").encode("utf-8"), mtime=0))

        if damage == "misaligned-record":
            with pytest.raises(SystemExit, match="the oracle row cache is misaligned"):
                self._pass(spec, tmp_path, "after", read_dir=cold_stores, **shared)
            return

        _report, audit, stores = self._pass(spec, tmp_path, "after", read_dir=cold_stores, **shared)
        assert audit.read_bytes() == cold_audit.read_bytes()
        header = oracle_cache.read_header(oracle_cache.store_path(stores, "default"))
        assert header is not None and header["pass_ordinal"] == 0
        assert set(_cache_ages(oracle_cache.store_path(stores, "default"))) == {0}

    def test_order_survives_the_interleave(self, spec, tmp_path):
        """The audit's line order is the subset table's row order, and the partition must not be able to reach it. Here the two halves alternate row by row — every even row names the moved family and walks, every odd row is served — and the file still reads out in table order, configuration by configuration in the order the caller asked for them."""
        clean = CACHE_LETTERS[1:]
        rows: list[tuple[int, ...]] = []
        for letter in clean:
            rows.append((CACHE_LETTERS[0], letter))
            rows.append((letter, letter))
        tables, aliases, stamps, configs = self._bench(tmp_path, rows=rows)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, configs=configs
        )

        _cold, _cold_audit, cold_stores = self._pass(spec, tmp_path, "cold", keys=self._keys(spec), **shared)
        _served, audit, stores = self._pass(
            spec,
            tmp_path,
            "served",
            keys=self._keys(spec, moved=("qsPea",)),
            read_dir=cold_stores,
            **shared,
        )

        ages = _cache_ages(oracle_cache.store_path(stores, configs[0]))
        assert {index for index, age in enumerate(ages) if age == 0} == {
            index for index in range(1, len(rows), 2)
        } - _cache_renewed(len(rows), 0)
        assert 0 in ages and 1 in ages, "the pass did not interleave served and fresh rows"

        lines = [line.split("\t") for line in audit.read_text().splitlines()[1:]]
        wanted = [":".join(f"{codepoint:04X}" for codepoint in row) for row in rows]
        assert [line[0] for line in lines] == [config for config in configs for _ in rows]
        for offset, config in enumerate(configs):
            block = lines[offset * len(rows) : (offset + 1) * len(rows)]
            assert [line[1] for line in block] == wanted


class TestOracleRowRanges:
    """The oracle's unit of fan-out is a row range of one configuration's table, and the claim the split rests on is that it changes no number and no byte: `_compare_config` is addressed by absolute row index throughout, so a range is a self-contained segment of the same audit, the same store and the same verification draw. Every arm here runs the real compare over the mini bundle's default rows once whole and once cut, through the worker, the folds and the joins `run_m1.run_oracle` uses, and holds the cut run to the whole one."""

    def _ranged(
        self,
        spec,
        tmp_path: Path,
        name: str,
        shards: Sequence[oracle.OracleShard],
        *,
        tables: Path,
        aliases: Path,
        ledger: Path,
        stamps,
        keys,
        configs,
        read_dir: Path | None = None,
        write: bool = True,
        cached: bool = True,
        font: Path | None = None,
        position=None,
    ):
        """One pass through the fan-out's own pieces without the pool: a worker per range writing its own segments, the parent's fold and joins, and the joined store promoted. A single whole-table shard is the uncut shape and the reference."""
        scratch = tmp_path / f"{name}-scratch"
        stores = tmp_path / f"{name}-stores"
        out = tmp_path / name
        out.mkdir(exist_ok=True)
        position_keys, position_stamp = (None, None) if position is None else position
        row_cache = (
            oracle.OracleRowCache(
                stamps,
                keys,
                read_dir=read_dir,
                write_dir=scratch if write else None,
                position_environment=position_stamp,
                position_keys=position_keys,
            )
            if cached
            else None
        )
        guard = kernel_exec.guard_sweep(spec)
        landed = [
            (
                shard,
                oracle.oracle_config_worker(
                    spec,
                    tables,
                    aliases,
                    ledger,
                    shard.config,
                    font,
                    None,
                    audit_dir=scratch,
                    row_cache=row_cache,
                    guard_verdicts=guard,
                    shard=shard,
                ),
            )
            for shard in shards
        ]
        by_config: dict[str, list[oracle.OracleConfigResult]] = {}
        for shard, result in sorted(landed, key=lambda pair: pair[0].first_row):
            by_config.setdefault(shard.config, []).append(result)
        merged = [oracle.merge_config_shards(by_config[config]) for config in configs]
        segments = {shard.config: shard.of for shard in shards}
        report = oracle.merge_oracle_results(merged)
        oracle.join_oracle_audit(out, scratch, configs, report.divergent_rows, segments=segments)
        if cached and write:
            for result in merged:
                if segments[result.config] > 1:
                    assert result.pass_ordinal is not None
                    stamp = stamps[result.config]
                    assert oracle_cache.join_store_segments(
                        scratch,
                        result.config,
                        segments[result.config],
                        stamp,
                        stamp.labels["subset"],
                        result.pass_ordinal,
                        keys,
                        result.rows_compared,
                        position_stamp,
                        position_keys,
                    )
            assert oracle_cache.promote_stores(scratch, stores, configs) == list(configs)
        return merged, out / "divergence-audit.tsv", stores

    def _bench(self, spec, tmp_path: Path):
        tables, aliases, ledger, stamps, configs, rows = _position_bench(spec, tmp_path)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        position = oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", None)
        shared: dict[str, Any] = dict(
            tables=tables,
            aliases=aliases,
            ledger=ledger,
            stamps=stamps,
            keys=keys,
            configs=configs,
            font=MINI / "M1.otf",
            position=position,
        )
        return shared, rows

    def test_the_plan_cuts_the_bench_into_contiguous_ranges(self, spec, tmp_path):
        _shared, rows = self._bench(spec, tmp_path)
        shards = oracle.oracle_shard_plan(3, {"default": len(rows)}, ("default",))
        assert [shard.of for shard in shards] == [3, 3, 3]
        by_row = sorted(shards, key=lambda shard: shard.first_row)
        assert by_row[0].first_row == 0 and by_row[-1].stop_row is None
        assert all(left.stop_row == right.first_row for left, right in zip(by_row, by_row[1:]))
        assert [shard.label for shard in by_row] == ["default 1/3", "default 2/3", "default 3/3"]

    def test_the_ranges_write_the_audit_the_whole_table_writes_and_fold_to_its_tally(self, spec, tmp_path):
        """Cold and uncached, so every row is compared and every eligible position shaped in whichever range holds it: the joined audit is the whole-table shard's byte for byte, which is the serial path's byte for byte, and the fold reproduces the whole-table result field for field."""
        shared, rows = self._bench(spec, tmp_path)
        whole, whole_audit, _ = self._ranged(
            spec, tmp_path, "whole", [oracle.OracleShard("default")], cached=False, **shared
        )
        shards = oracle.oracle_shard_plan(3, {"default": len(rows)}, ("default",))
        cut, cut_audit, _ = self._ranged(spec, tmp_path, "cut", shards, cached=False, **shared)
        serial = tmp_path / "serial"
        oracle.compare_against_baseline(
            spec,
            shared["tables"],
            shared["aliases"],
            shared["ledger"],
            configs=shared["configs"],
            out_dir=serial,
            font_path=shared["font"],
        )
        assert (
            cut_audit.read_bytes()
            == whole_audit.read_bytes()
            == (serial / "divergence-audit.tsv").read_bytes()
        )
        assert whole[0].divergent_rows > 0 and whole[0].positions_compared > 0
        assert asdict(cut[0]) == {**asdict(whole[0]), "peak_rss_bytes": cut[0].peak_rss_bytes}

    def test_a_cut_cold_pass_stages_the_store_a_whole_cold_pass_stages(self, spec, tmp_path):
        """The joined store's framing differs from the single-member one — it is several gzip members — and its payload must not: header line, every record in table order, the trailer counting all of them. `load_store` reads across the members, and a joined store short its last segment's tail loads as None, which is the truncation refusal the trailer exists for."""
        shared, rows = self._bench(spec, tmp_path)
        _whole, _, whole_stores = self._ranged(
            spec, tmp_path, "whole", [oracle.OracleShard("default")], **shared
        )
        shards = oracle.oracle_shard_plan(3, {"default": len(rows)}, ("default",))
        _cut, _, cut_stores = self._ranged(spec, tmp_path, "cut", shards, **shared)
        whole_path = oracle_cache.store_path(whole_stores, "default")
        cut_path = oracle_cache.store_path(cut_stores, "default")
        assert gzip.decompress(cut_path.read_bytes()) == gzip.decompress(whole_path.read_bytes())
        assert cut_path.read_bytes() != whole_path.read_bytes()
        stamp = shared["stamps"]["default"]
        loaded = oracle_cache.load_store(
            cut_path, stamp, stamp.labels["subset"], spec, shared["keys"], 0, *reversed(shared["position"])
        )
        assert loaded is not None and loaded.rows == len(rows)
        reference = oracle_cache.load_store(
            whole_path, stamp, stamp.labels["subset"], spec, shared["keys"], 0, *reversed(shared["position"])
        )
        assert reference is not None
        for index, codepoints in enumerate(rows):
            assert loaded.serve(index, codepoints) == reference.serve(index, codepoints)
        payload = gzip.decompress(cut_path.read_bytes())
        short = cut_path.with_name("short.tsv.gz")
        short.write_bytes(gzip.compress(payload[: len(payload) - 40]))
        assert oracle_cache.load_store(short, stamp, stamp.labels["subset"], spec, shared["keys"]) is None

    def test_a_cut_warm_pass_serves_what_a_whole_warm_pass_serves(self, spec, tmp_path):
        """The serve path, the renewal slice and the verification draw are all keyed on the row's ordinal in the table, so a warm pass cut into ranges serves the rows a whole warm pass serves, re-derives the same renewal slice, writes the same audit and stages the same store — with `positions_served` summing to the whole pass's figure and a verification sample drawn per range, which is a superset of the whole pass's draw."""
        shared, rows = self._bench(spec, tmp_path)
        _cold, _, cold_stores = self._ranged(
            spec, tmp_path, "cold", [oracle.OracleShard("default")], **shared
        )
        whole, whole_audit, whole_stores = self._ranged(
            spec, tmp_path, "whole", [oracle.OracleShard("default")], read_dir=cold_stores, **shared
        )
        shards = oracle.oracle_shard_plan(3, {"default": len(rows)}, ("default",))
        cut, cut_audit, cut_stores = self._ranged(
            spec, tmp_path, "cut", shards, read_dir=cold_stores, **shared
        )
        assert whole[0].positions_served > 0
        assert cut_audit.read_bytes() == whole_audit.read_bytes()
        assert asdict(cut[0]) == {**asdict(whole[0]), "peak_rss_bytes": cut[0].peak_rss_bytes}
        assert gzip.decompress(
            oracle_cache.store_path(cut_stores, "default").read_bytes()
        ) == gzip.decompress(oracle_cache.store_path(whole_stores, "default").read_bytes())
        assert cut[0].pass_ordinal == whole[0].pass_ordinal == 1

    def test_the_fold_refuses_ranges_that_disagree_on_the_store_ordinal(self):
        results = [
            oracle.OracleConfigResult(config="default", rows_compared=1, pass_ordinal=1),
            oracle.OracleConfigResult(config="default", rows_compared=1, pass_ordinal=2),
        ]
        with pytest.raises(ValueError, match="pass ordinals"):
            oracle.merge_config_shards(results)
        with pytest.raises(ValueError, match="one configuration"):
            oracle.merge_config_shards(
                [oracle.OracleConfigResult(config="default"), oracle.OracleConfigResult(config="ss03")]
            )

    def test_the_fold_notes_a_missing_table_once(self):
        results = [
            oracle.OracleConfigResult(config="default", notes=["default: subset table missing at x"]),
            oracle.OracleConfigResult(config="default", notes=["default: subset table missing at x"]),
        ]
        assert oracle.merge_config_shards(results).notes == ["default: subset table missing at x"]


class TestFontBlindComparison:
    """The two signatures the whole cache rests on, and the one mutation the position channel is allowed to make. None of it is written down anywhere else: if the comparison channel ever takes a font, or the position channel ever takes a settled stream, or a drift starts rewriting a row rather than appending to it, the store's key is silently wrong about what it covers and every arm above goes on passing."""

    def test_the_comparison_channel_takes_no_font_and_the_position_channel_takes_no_settlement(self):
        comparison = list(inspect.signature(conform._compare_row).parameters)
        assert comparison == ["spec", "aliases", "config", "features", "row", "settled"]
        position = list(inspect.signature(oracle._position_drift).parameters)
        assert position == ["shaper", "kern", "features", "row"]

    def test_the_position_channel_only_appends_position_to_kinds(self, spec, tmp_path, monkeypatch):
        """A constructed drift over two rows — one the alias map settles clean and one it leaves unaliased — watched through the ledger match the channel makes before and after it fires. The clean row's drift mints a row of its own whose kinds are exactly `position`; the divergent row's drift leaves every field it already carried alone and appends `position` to the kinds and `position-drift` to the phenomena."""
        tables = tmp_path / "tables"
        _cache_subset_table(tables, "default", [(0xE650,), (0xE652,)])
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("oldE650: ignore\noldE652: pending\n")
        ledger = [{"id": "ink-identical", "ink_identical": True, "match": {}}]

        seen: list[conform.DivergentRow] = []
        real = oracle._match_ledger

        def spy(entries, row):
            seen.append(row)
            return real(entries, row)

        monkeypatch.setattr(oracle, "_match_ledger", spy)
        result = oracle._compare_config(
            spec,
            tables,
            "default",
            frozenset(),
            labels.load_alias_map(aliases),
            ledger,
            {"ink-identical"},
            _SilentShaper(),  # pyright: ignore[reportArgumentType]
            None,
            kernel_exec.guard_sweep(spec),
            None,
        )
        assert result.positions_compared == 2

        minted = seen[0]
        assert minted.codepoints == "E650"
        assert minted.kinds == ("position",)
        assert minted.phenomena == ("position-drift",)
        assert minted.position == -1

        before, after = seen[1], seen[2]
        assert before.codepoints == after.codepoints == "E652"
        assert before.kinds == ("unaliased",)
        assert after.kinds == before.kinds + ("position",)
        assert after.phenomena == before.phenomena + ("position-drift",)
        assert replace(after, kinds=before.kinds, phenomena=before.phenomena) == before


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
    """Enough of a Shaper for the belt's bookkeeping, which never reads shaped output: every text shapes to nothing, so the oracle records one length divergence per text and the split-buffer check sees no splitter slot. The texts it was asked to shape are the observable."""

    def __init__(self):
        self.shaped: list[str] = []

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        self.shaped.append(text)
        return []

    def outline_signature(self, glyph_name: str) -> tuple:
        return ()


class TestBeltEconomics:
    """What the per-edit belt does and does not spend over a short horizon with the font faked out: every text of every length up to the horizon shapes exactly once, and the split-buffer check runs on exactly the texts it can say anything about."""

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

    def test_the_split_buffer_check_runs_on_the_texts_that_carry_a_splitter(self, spec, guard, monkeypatch):
        """The retired boundary gate's charter, now the belt's: every splitter-bearing text is held against its own segments shaped alone, and no other text pays for it, since a text with no splitter in it is trivially identical to its own single segment. The ZWNJ slot's own structure — zero advance, no ink — is read-back's static boundary-glyphs stage over the font bytes, not the belt's."""
        split_checked: list[str] = []
        monkeypatch.setattr(
            conform, "check_split_buffer", lambda text, *args, **kwargs: split_checked.append(text)
        )
        _result, shaper = self._run(spec, guard)
        splitters = conform.splitting_boundary_chars(spec)
        assert set(split_checked) == {text for text in shaper.shaped if set(text) & splitters}
        assert split_checked


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
    """The memo keys on the raw window — every slot one settlement can read, none of them blanked — so the bar is two things at once: observational identity with an unmemoized settlement of the same tokens, and key agreement with `_matched_windows`, which reads the same raw slots. Over-keying was never the risk; under-keying was (a key that blanks a slot the kernel can still read replays a wrong outcome somewhere), and both paths run exhaustively here, the walk reusing its memo from the second text on while the reference path settles every text in a sequence of its own. The rule replay itself does not ride the walk — `_matched_windows` and `_DeepTokenIndex` keep it, for the build's certificate check — so the arms that need rules exercise them there."""

    SWEEP_CHUNK = 4096

    def _sweep(self, spec, features, alphabet, max_length, rules_by_input=None, deep_index=None, memo=None):
        """Sweep every text up to `max_length`: the walk's settled stream and names against an unmemoized settlement of the very same formed tokens, and its memo keys against the raw-grain replay both sides share `_window_rights` for. What the first arm alarms is the memo's keying rather than one engine against another — both sides are the crate now — because the walk answers a window once and replays it wherever the key recurs while the reference settles every text's positions in a sequence of its own, so a key that blanked a slot settlement can still read would show up here as a wrong outcome somewhere. Texts stream through in chunks so the reference's decoded traces stay a bounded pile; the walk keeps its memo across them. With `rules_by_input` supplied, the replay also runs through `deep_index` and its (window, first-matching rule) pairs come back for the class-grain arms to assert on. With `memo`, the walk loads that settle memo file first, which is how a file another producer wrote is held to the same reference."""
        import itertools

        guard = kernel_exec.guard_sweep(spec)
        walker = conform._SettledWindowWalk(spec, features, {}, guard, memo=memo)
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
        from rebuild.pipeline.model import raw_rename_map
        from rebuild.pipeline.kernel_exec import build_tables

        monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", True)
        spec = fixtures.prospect_spec()
        decision = build_tables(spec, frozenset())[0]
        assert any(row.right3 != "#NA" for row in decision.transitions)
        assert any(rule.look3 for rule in decision.rules)
        assert decision.deep_classes
        rules_by_input = conform._renamed_rules_by_input(spec, frozenset(), decision)
        index = conform._DeepTokenIndex(decision, raw_rename_map(spec, frozenset()))
        _walker, replayed = self._sweep(
            spec, frozenset(), conform.spec_alphabet(spec), 5, rules_by_input, index
        )
        assert any(matched is not None for _window, matched in replayed)

    def test_synthetic_depth4_replay_carries_rules_and_a_genuine_index(self):
        """The class-grain depth-4 arm with real rules and a real transported index: the mini fixture plus a reach-3 chain on ·Tea, built in the shipping deep world, mints an r4 class at the ·Tea·May·May·May windows, and `_matched_windows` must resolve the realized labels to that class token and match rules against it — the replay half of the pair, which the witness gate leans on, while the walk beside it keeps settling those same texts right."""
        import dataclasses

        from rebuild.pipeline import fixtures, model
        from rebuild.pipeline.model import raw_rename_map
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
        index = conform._DeepTokenIndex(decision, raw_rename_map(spec, frozenset()))
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

    def test_one_memo_key_settles_its_distinct_case_lines_alike(self, spec, guard):
        """The dedupe's own premise, checked rather than argued: `_window_rights`' `#NA` cascade blanks slots the key does not carry, so several distinct raw case lines land on one memo key, and the walk asks the crate about only the first of them. Under `audit_dedupe` every later one is asked too and held to the memoized outcome — the mini alphabet at depth 4 is where those collisions are dense enough to be worth the extra invocations."""
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
        """The certificate check's pairing, which is why `on_error` exists at all: the prefill is eager over every certificate, so a window the crate refuses in one of them must not take the whole check down before the other rules are read. Under `on_error="drop"` it is memoized as a refusal and the text carrying it stops advancing; the walk that later reaches that key is where the refusal surfaces, against the one rule whose certificate carried it. The default stays strict, and the same prefill raises there."""
        refused = "qsTea"
        clean, refusing_text = chr(0xE665) + chr(0xE670), chr(0xE665) + chr(0xE652)
        original = kernel_exec.settle_windows

        def injecting(asked_spec, cases, features, **rest):
            answers = original(asked_spec, cases, features, **rest)
            hits = [
                index
                for index, case in enumerate(cases)
                if case.split("\t")[kernel_exec.CASE_INPUT_FIELD] == refused
            ]
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
    def test_the_real_alphabet_keys_its_distinct_case_lines_alike(self):
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


def _dump_parts(dump: Path) -> tuple[dict, list[bytes], list[bytes], bytes]:
    """A crate-filed window memo taken apart: the head, the label lines, the record lines and the row bytes."""
    data = dump.read_bytes()
    head_line, _, _ = data.partition(b"\n")
    head = json.loads(head_line.decode().partition("\t")[2])
    parts = data.split(b"\n", 1 + head["labels"] + head["records"])
    return head, parts[1 : 1 + head["labels"]], parts[1 + head["labels"] : -1], parts[-1]


def _write_dump(
    dump: Path, head: dict, labels: list[bytes], records: list[bytes], rows: list[tuple[int, ...]]
) -> None:
    """A window memo in the crate's own layout, with the head's counts taken from what is written."""
    head = {**head, "rows": len(rows), "labels": len(labels), "records": len(records)}
    code = "<" + {2: "H", 4: "I"}[head["width"]] * 7
    body = b"".join(struct.pack(code, *row) for row in rows)
    text = f"# {kernel_exec.REPLAY_MEMO_FORMAT}\t{json.dumps(head, separators=(',', ':'))}\n".encode()
    dump.write_bytes(text + b"".join(line + b"\n" for line in [*labels, *records]) + body)


class TestCrateEmittedSettleMemo:
    """The settle memo the string replay files is the belt's memo in the crate's spelling, and `absorb_replay_memo` is the seam that respells it. The headline arm is the walk-equivalence sweep over a file the crate wrote: a mis-spelled key misses rather than mismatches, so the alarm for the label conversion is a walk that settles anything at all (`fresh_windows`), and the alarm for a wrong outcome is the settled stream against the unmemoized reference. The rest holds the file to what a belt-written one is held to — stamp, family keys, the per-family retirement — and the conversion's own refusals."""

    STAMP = "replay-stamp-a"

    @pytest.fixture(scope="class")
    def dumps_dir(self, tmp_path_factory, spec):
        out_dir = tmp_path_factory.mktemp("replay-memo")
        run_m1.build_tables(spec, out_dir)
        kernel_exec.replay_strings(
            spec, out_dir, conform.SETTLEMENT_CONFIGS, horizon=4, families=None, threads=2, memo_dir=out_dir
        )
        return out_dir

    def _memo(self, tmp_path, config="default", stamp=STAMP, keys=None):
        return conform.SettleMemoFile(tmp_path / f"settle-memo-{config}.gz", stamp, dict(keys or {}))

    @pytest.mark.parametrize("config", ["default", "ss03"])
    def test_the_absorbed_memo_serves_every_window_the_walk_reaches_and_settles_alike(
        self, spec, dumps_dir, tmp_path, config
    ):
        """The equivalence arm: the sweep to the replay's horizon over a walk carrying the crate-emitted file settles nothing — every key the walk spells is one the conversion spelled — and its streams and names equal the unmemoized reference. `ss03` exercises the marker fold on the input and right slots, `default` the bare spelling."""
        memo = self._memo(tmp_path, config)
        entries = conform.absorb_replay_memo(
            kernel_exec.replay_memo_dump(dumps_dir, config), memo, spec, config
        )
        assert entries > 0 and conform.settle_memo_standing(memo)
        walker, _ = TestSettledWindowWalk._sweep(
            TestSettledWindowWalk(),
            spec,
            conform.features_for_config(config),
            conform.spec_alphabet(spec),
            4,
            memo=memo,
        )
        assert walker.fresh_windows == 0, "a key the conversion spelled differently from the walk"
        assert walker.stale_windows == 0
        assert walker._settle_calls == 0
        assert walker.memo_windows == entries == len(walker.windows)

    def test_a_dump_under_another_head_or_configuration_or_short_of_its_rows_is_refused(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """Each refusal writes nothing: a standing file is left as it was, and where none stood none appears."""
        memo = self._memo(tmp_path)
        standing = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        standing.walk_many(["\ue652"])
        assert standing.save_memo()
        before = memo.path.read_bytes()
        head, labels, records, body = _dump_parts(kernel_exec.replay_memo_dump(dumps_dir, "default"))
        other_format = tmp_path / "other-format.bin"
        other_format.write_bytes(b"# ams-m1-replay-memo/0\t" + json.dumps(head).encode() + b"\n")
        truncated = tmp_path / "truncated.bin"
        truncated.write_bytes(kernel_exec.replay_memo_dump(dumps_dir, "default").read_bytes()[:-3])
        for dump, config in [
            (other_format, "default"),
            (kernel_exec.replay_memo_dump(dumps_dir, "ss03"), "default"),
            (truncated, "default"),
        ]:
            with pytest.raises(kernel_exec.KernelRunError):
                conform.absorb_replay_memo(dump, memo, spec, config)
            assert memo.path.read_bytes() == before
        absent = self._memo(tmp_path / "absent")
        with pytest.raises(kernel_exec.KernelRunError):
            conform.absorb_replay_memo(truncated, absent, spec, "default")
        assert not absent.path.exists()

    def test_the_absorbed_file_carries_its_stamp_and_keys_and_retires_by_family(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """The converted file behaves under `StaleMask` like one the belt wrote: it reads back under exactly the stamp and family keys it was given, a rune edit to one key retires exactly the entries whose windows name that family, and another stamp reads as no file."""
        keys = {name: f"{name}@0" for name in spec.registry.families}
        memo = self._memo(tmp_path, keys=keys)
        entries = conform.absorb_replay_memo(
            kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default"
        )
        loaded = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        loaded._load_memo()
        assert loaded.memo_windows == entries and loaded.stale_windows == 0
        naming = sum(
            any(conform._label_family(label) == "qsTea" for label in window) for window in loaded._cold
        )
        assert 0 < naming < entries
        moved = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=self._memo(tmp_path, keys={**keys, "qsTea": "qsTea@1"})
        )
        moved._load_memo()
        assert moved.memo_windows == entries and moved.stale_windows == naming
        assert len(moved._cold) == entries - naming
        restamped = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=self._memo(tmp_path, stamp="b")
        )
        restamped._load_memo()
        assert restamped.memo_windows == 0 and not conform.settle_memo_standing(
            self._memo(tmp_path, stamp="b")
        )

    def test_two_crate_keys_that_collapse_to_one_walk_key_must_agree(self, spec, guard, dumps_dir, tmp_path):
        """Two seats whose cells spell one display name are one walk key: rows keyed on either seat absorb as one entry when they agree, and a dump where they disagree is refused whole."""
        head, labels, records, _ = _dump_parts(kernel_exec.replay_memo_dump(dumps_dir, "default"))
        record = json.loads(records[0])
        twin = json.dumps({**record, "extension": record["extension"] + 1}, separators=(",", ":")).encode()
        names = [line.decode() for line in labels]
        edge, na, pea, tea = (names.index(label) for label in ("#EDGE", "#NA", "qsPea", "qsTea"))
        seat = len(labels)
        dump = tmp_path / "collapsing.bin"
        _write_dump(
            dump,
            head,
            labels,
            [records[0], twin],
            [(pea, seat, tea, edge, na, na, 0), (pea, seat + 1, tea, edge, na, na, 0)],
        )
        memo = self._memo(tmp_path)
        assert conform.absorb_replay_memo(dump, memo, spec, "default") == 2
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker._load_memo()
        assert walker.memo_windows == 2 and len(walker._cold) == 1
        _write_dump(
            dump,
            head,
            labels,
            [records[0], twin],
            [(pea, seat, tea, edge, na, na, 0), (pea, seat + 1, tea, edge, na, na, 1)],
        )
        refused = self._memo(tmp_path / "refused")
        with pytest.raises(kernel_exec.KernelRunError, match="two ways"):
            conform.absorb_replay_memo(dump, refused, spec, "default")
        assert not refused.path.exists()


class TestDeepTokenIndex:
    """The transport's raw-vs-renamed contract: `_DeepTokenIndex` is built from the table's raw label space but queried with the walk's marker-folded labels, so every member combination of every class-bearing row must resolve to exactly the deep components of the row's renamed key. The walk-equivalence sweeps cannot see a one-sided rename slip — both paths share the index — so this arm checks resolution against the rows directly, on a config whose rename map touches the row shape that broke first: a bare (singleton-fiber) r3 the config renames, under a class-token r4."""

    def test_every_class_row_resolves_under_a_renaming_config(self):
        import dataclasses

        from rebuild.pipeline import model
        from rebuild.pipeline.model import raw_rename_map
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
        renames = raw_rename_map(spec, features)
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


class TestSettleMemoFile:
    """The belt and the oracle walk the same texts per configuration, and the memo file is how the second of them settles nothing: written by whichever walk settled anything the file lacked, read lazily by the next, keyed on the display name so a walk with a minted inventory and a walk with none share every key. A file under another stamp, or one that will not decode, costs the walk only the windows it would have settled anyway."""

    STAMP = "tables-stamp-a"

    def _texts(self, spec, max_length=3):
        import itertools

        alphabet = conform.spec_alphabet(spec)
        return [
            "".join(combo)
            for length in range(1, max_length + 1)
            for combo in itertools.product(alphabet, repeat=length)
        ]

    def _memo(self, tmp_path, stamp=STAMP, keys=None):
        return conform.SettleMemoFile(tmp_path / "settle-memo-default.gz", stamp, dict(keys or {}))

    def test_settle_memo_files_key_each_configuration_off_the_snapshot(self, spec, tmp_path):
        inputs = oracle_cache.SettleMemoInputs(rune_digests={"qsTea": "t0"}, oracle_code="code", data="data")
        files = conform.settle_memo_files(tmp_path, spec, inputs)
        assert set(files) == set(conform.SETTLEMENT_CONFIGS)
        assert files["default"].path == tmp_path / "settle-memo-default.gz"
        assert len({memo.stamp for memo in files.values()}) == len(files)
        assert files["default"].family_keys == oracle_cache.settle_family_keys(inputs, spec)
        assert conform.settle_memo_files(tmp_path, spec, None) == {}

    def test_a_rune_edit_retires_only_the_entries_naming_it(self, spec, guard, tmp_path):
        """The acceptance test for the per-family memo: a walk over the edited spec that loads the file the unedited walk wrote — under keys that name ·Tea as moved — answers exactly what a walk with no file answers, settles nothing the file still vouches for, and retires exactly the entries whose windows name ·Tea. The edit genuinely moves settlement, so a memo that served across it would answer wrong, which the first assertion is the proof of."""
        texts = self._texts(spec)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=self._memo(tmp_path, keys=keys))
        before = first.walk_many(texts)
        assert first.save_memo()

        def names_tea(window) -> bool:
            return any(conform._label_family(label) == "qsTea" for label in window)

        naming = {window for window in first.windows if names_tea(window)}
        assert 0 < len(naming) < len(first.windows)

        edited = _tea_prefers_half_before_may(spec)
        edited_guard = kernel_exec.guard_sweep(edited)
        reference = conform._SettledWindowWalk(edited, frozenset(), {}, edited_guard)
        expected = reference.walk_many(texts)
        assert expected != before, "the rune edit moved no settlement"

        second = conform._SettledWindowWalk(
            edited,
            frozenset(),
            {},
            edited_guard,
            memo=self._memo(tmp_path, keys={**keys, "qsTea": "qsTea@1"}),
        )
        assert second.walk_many(texts) == expected
        assert second.memo_windows == len(first.windows)
        assert second.stale_windows == len(naming)
        served = set(first.windows) - naming
        fresh = set(second.windows) - served
        assert len(fresh) == second.fresh_windows
        assert 0 < second.fresh_windows < len(reference.windows)
        assert all(names_tea(window) or window not in first.windows for window in fresh)
        assert second.save_memo()
        third = conform._SettledWindowWalk(
            edited,
            frozenset(),
            {},
            edited_guard,
            memo=self._memo(tmp_path, keys={**keys, "qsTea": "qsTea@1"}),
        )
        assert third.walk_many(texts) == expected
        assert third._settle_calls == 0 and third.stale_windows == 0

    def test_a_ligature_rune_edit_retires_the_entries_naming_its_formed_label(self, spec, guard, tmp_path):
        """The persisted-memo regression for issue 202. A memo window holds formed labels, so the windows a ligature rune's edit reaches are the ones carrying its own label — `qsTea_qsOy` at any slot — beside the ones the order-blind component clause already retires for carrying both ·Tea and ·Oy unformed; the formed-label windows carry no component label that clause could fire on. Under keys naming only the ligature as moved, the loaded file must retire exactly those windows and answer what a walk with no file answers; a second save under the new keys then serves everything, which is the property the issue's repro lost: a rewrite that stamps a stale entry with the current keys leaves no ordinary run able to retire it."""
        texts = self._texts(spec)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=self._memo(tmp_path, keys=keys))
        before = first.walk_many(texts)
        assert first.save_memo()

        def reaches_ligature(window) -> bool:
            families = {conform._label_family(label) for label in window}
            return "qsTea_qsOy" in families or {"qsTea", "qsOy"} <= families

        def names_only_the_formed_label(window) -> bool:
            families = {conform._label_family(label) for label in window}
            return "qsTea_qsOy" in families and not families & {"qsTea", "qsOy"}

        naming = {window for window in first.windows if reaches_ligature(window)}
        assert 0 < len(naming) < len(first.windows)
        assert any(names_only_the_formed_label(window) for window in naming)

        edited = _tea_oy_refuses_its_exit(spec)
        edited_guard = kernel_exec.guard_sweep(edited)
        reference = conform._SettledWindowWalk(edited, frozenset(), {}, edited_guard)
        expected = reference.walk_many(texts)
        assert expected != before, "the ligature rune edit moved no settlement"

        moved = {**keys, "qsTea_qsOy": "qsTea_qsOy@1"}
        second = conform._SettledWindowWalk(
            edited, frozenset(), {}, edited_guard, memo=self._memo(tmp_path, keys=moved)
        )
        assert second.walk_many(texts) == expected
        assert second.memo_windows == len(first.windows)
        assert second.stale_windows == len(naming)
        served = set(first.windows) - naming
        fresh = set(second.windows) - served
        assert len(fresh) == second.fresh_windows
        assert 0 < second.fresh_windows < len(reference.windows)
        assert all(reaches_ligature(window) or window not in first.windows for window in fresh)
        assert second.save_memo()
        third = conform._SettledWindowWalk(
            edited, frozenset(), {}, edited_guard, memo=self._memo(tmp_path, keys=moved)
        )
        assert third.walk_many(texts) == expected
        assert third._settle_calls == 0 and third.stale_windows == 0

    def test_a_moved_family_the_registry_cannot_place_reads_as_no_file(self, spec, guard, tmp_path):
        texts = self._texts(spec, 2)
        first = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=self._memo(tmp_path, keys={"qsTea": "a"})
        )
        first.walk_many(texts)
        assert first.save_memo()
        second = conform._SettledWindowWalk(
            spec,
            frozenset(),
            {},
            guard,
            memo=self._memo(tmp_path, keys={"qsTea": "a", "qsNotInTheRegistry": "b"}),
        )
        second.walk_many(texts)
        assert second.memo_windows == 0 and second.fresh_windows == len(second.windows)

    def test_the_belt_prunes_what_no_text_reaches_and_the_oracle_carries_it(self, spec, guard, tmp_path):
        """Two walks over the same file with different charters. The oracle's, which reaches only the windows of the rows it walks, writes nothing when it settled nothing and would carry every loaded entry forward if it did; the belt's, which reaches every window any text produces, prunes what it never reached — so a window an edit has orphaned leaves the file on the next sweep rather than riding it forever."""
        memo = self._memo(tmp_path)
        long_texts, short_texts = self._texts(spec, 3), self._texts(spec, 2)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        first.walk_many(long_texts)
        assert first.save_memo()
        total = len(first.windows)
        written = memo.path.read_bytes()

        oracle_side = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        oracle_side.walk_many(short_texts)
        touched = len(oracle_side.windows)
        assert 0 < touched < total and oracle_side.memo_windows == total
        assert not oracle_side.save_memo()
        assert oracle_side.pruned_windows == 0 and memo.path.read_bytes() == written

        belt_side = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        belt_side.walk_many(short_texts)
        assert belt_side.save_memo(prune=True)
        assert belt_side.pruned_windows == total - touched

        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        third.walk_many(short_texts)
        assert third.memo_windows == touched and third._settle_calls == 0
        fourth = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert fourth.walk_many(long_texts) == first.walk_many(long_texts)
        assert fourth.memo_windows == touched and fourth.fresh_windows == total - touched

    def test_the_second_walk_over_the_same_texts_never_reaches_the_crate(self, spec, guard, tmp_path):
        texts = self._texts(spec)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walked = first.walk_many(texts)
        assert first.memo_windows == 0
        assert first.fresh_windows == len(first.windows)
        assert first.save_memo()
        written = memo.path.read_bytes()

        minted = {item.cell: f"minted.{name}" for item, name, _left in first._outcomes.values()}
        second = conform._SettledWindowWalk(spec, frozenset(), minted, guard, memo=memo)
        again = second.walk_many(texts)
        assert second._settle_calls == 0
        assert second.fresh_windows == 0
        assert second.memo_windows == len(first.windows)
        assert second.windows.keys() == first.windows.keys()
        boundaries = set(labels._BOUNDARY_KIND_LABELS.values())
        for (settled, names), (expected, expected_names) in zip(again, walked):
            assert settled == expected
            assert names == [name if name in boundaries else f"minted.{name}" for name in expected_names]
        assert not second.save_memo()
        assert memo.path.read_bytes() == written

    def test_another_stamp_reads_as_no_file_and_is_overwritten(self, spec, guard, tmp_path):
        texts = self._texts(spec, 2)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=self._memo(tmp_path))
        first.walk_many(texts)
        assert first.save_memo()

        restamped = self._memo(tmp_path, "tables-stamp-b")
        second = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=restamped)
        assert second.walk_many(texts) == first.walk_many(texts)
        assert second.memo_windows == 0
        assert second.fresh_windows == len(second.windows)
        assert second.save_memo()

        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=restamped)
        third.walk_many(texts)
        assert third.memo_windows == len(second.windows)
        assert third._settle_calls == 0

    def test_a_walk_with_nothing_to_settle_never_opens_the_file(self, spec, guard, tmp_path):
        memo = self._memo(tmp_path)
        memo.path.write_bytes(b"not a memo")
        splitter = sorted(conform.splitting_boundary_chars(spec))[0]
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker.walk_many([splitter, splitter * 2])
        assert walker.memo_windows == 0
        assert walker.memo_seconds == 0.0
        assert not walker.save_memo()
        assert memo.path.read_bytes() == b"not a memo"

    def test_a_file_that_will_not_decode_costs_a_warning_and_nothing_else(
        self, spec, guard, tmp_path, capsys
    ):
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        with gzip.open(memo.path, "wb") as handle:
            handle.write(b"\x80\x05not a pickle stream")
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker.memo_windows == 0
        assert walker.fresh_windows == len(walker.windows)
        assert "[warn] settle memo:" in capsys.readouterr().err
        assert walker.save_memo()
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        third.walk_many(texts)
        assert third._settle_calls == 0

    def test_a_truncated_file_yields_the_whole_blocks_it_kept(self, spec, guard, tmp_path, monkeypatch):
        monkeypatch.setattr(conform, "SETTLE_MEMO_BLOCK", 64)
        texts = self._texts(spec)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walked = first.walk_many(texts)
        assert first.save_memo()
        assert len(first.windows) > 3 * conform.SETTLE_MEMO_BLOCK
        stream = gzip.decompress(memo.path.read_bytes())
        with gzip.open(memo.path, "wb") as handle:
            handle.write(stream[: len(stream) // 2])

        second = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert second.walk_many(texts) == walked
        assert 0 < second.memo_windows < len(first.windows)
        assert second.memo_windows % conform.SETTLE_MEMO_BLOCK == 0
        assert second.fresh_windows == len(first.windows) - second.memo_windows
        assert second.save_memo()
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        third.walk_many(texts)
        assert third.memo_windows == len(first.windows)
        assert third._settle_calls == 0

    def test_a_refusal_is_not_written_and_is_asked_again(self, spec, guard, tmp_path, monkeypatch):
        """A refusal is memoized for the tolerant walk that met it and for nobody else: the file holds outcomes only, so the next walk to reach that window asks the crate and gets whatever the crate says then."""
        clean, refusing_text = chr(0xE665) + chr(0xE670), chr(0xE665) + chr(0xE652)
        original = kernel_exec.settle_windows

        def injecting(asked_spec, cases, features, **rest):
            answers = original(asked_spec, cases, features, **rest)
            for index, case in enumerate(cases):
                if case.split("\t")[kernel_exec.CASE_INPUT_FIELD] == "qsTea":
                    answers[index] = None
            return answers

        monkeypatch.setattr(kernel_exec, "settle_windows", injecting)
        memo = self._memo(tmp_path)
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, on_error="drop", memo=memo)
        walker.prefill([clean, refusing_text])
        refused = [key for key, value in walker.windows.items() if isinstance(value, conform._RefusedWindow)]
        assert refused
        assert walker.save_memo()

        monkeypatch.setattr(kernel_exec, "settle_windows", original)
        again = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        again.walk_many([clean])
        assert again.memo_windows == len(walker.windows) - len(refused)
        assert not any(key in again.windows for key in refused)
        settled, _names = again.walk(refusing_text)
        assert [item.cell.rune for item in settled] == ["qsMay", "qsTea"]
        assert again.fresh_windows == len(refused)

    def test_the_belt_writes_the_file_the_oracle_reads(self, spec, guard, tmp_path, monkeypatch, capsys):
        """The two phases end to end, in the order a cycle runs them reversed — the belt over the mini alphabet at horizon 2 with the font faked out, then the oracle over rows whose texts that belt swept, with the crate taken away: every window the oracle needs is already in the file, and the `[t]` line each phase prints says which of them wrote it."""
        memo = self._memo(tmp_path)
        belt = conform._conformance_config(
            _SilentShaper(),  # pyright: ignore[reportArgumentType]
            spec,
            "default",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            {},
            None,
            2,
            guard,
            settle_memo=memo,
        )
        assert belt.sequences and memo.path.is_file()
        belt_line = [
            line for line in capsys.readouterr().err.splitlines() if line.startswith("[t] settle_memo")
        ]
        assert len(belt_line) == 1 and belt_line[0].endswith("written=yes")

        tables = tmp_path / "tables"
        tables.mkdir()
        rows = [
            "E652\tqsTea.noentry\t0\t\t0,0,150",
            "0020:E652\tspace|qsTea\t0,1\tbreak\t0,0,150|0,0,150",
            "E652:E652\tqsTea|qsTea\t0,1\tbreak\t0,0,150|0,0,150",
        ]
        with gzip.open(tables / "baseline-default.subset.tsv.gz", "wt", encoding="utf-8") as handle:
            handle.write("# config: default\n")
            for row in rows:
                handle.write(row + "\n")

        def crate_is_gone(*args, **kwargs):
            raise AssertionError("the oracle reached the crate for a window the belt had already settled")

        monkeypatch.setattr(conform.kernel_exec, "settle_windows", crate_is_gone)
        result = oracle._compare_config(
            spec, tables, "default", frozenset(), {}, [], set(), None, None, guard, None, settle_memo=memo
        )
        assert result.rows_compared == len(rows)
        oracle_line = [
            line for line in capsys.readouterr().err.splitlines() if line.startswith("[t] settle_memo")
        ]
        assert len(oracle_line) == 1 and oracle_line[0].endswith("fresh=0 pruned=0 written=no")

    def test_the_ranges_parts_absorb_into_the_file_without_dropping_each_others_windows(
        self, spec, guard, tmp_path
    ):
        """Two walks over the same standing file, each settling windows the other does not and each filing only what it settled as a part beside the file: neither touches the shared file, and the parent's absorb lands one file a third walk reads whole — every standing window and both parts' windows, no crate call — which is the failure a range that replaced the file whole would have produced."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        half = len(texts) // 2
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:half])
        assert seed.save_memo()
        standing = memo.path.read_bytes()

        rest = texts[half:]
        parts = [tmp_path / "part.0.gz", tmp_path / "part.1.gz"]
        one = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=parts[0])
        )
        one.walk_many(rest[: len(rest) // 2])
        two = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=parts[1])
        )
        two.walk_many(rest[len(rest) // 2 :])
        assert one.fresh_windows and two.fresh_windows
        only_one = set(one.windows) - set(two.windows) - set(seed.windows)
        only_two = set(two.windows) - set(one.windows) - set(seed.windows)
        assert only_one and only_two
        assert one.save_memo() and two.save_memo()
        assert memo.path.read_bytes() == standing
        assert all(part.is_file() for part in parts)

        assert conform.absorb_settle_memo_parts(memo, parts, spec)
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert third.walk_many(texts) == reference.walk_many(texts)
        assert third._settle_calls == 0 and third.fresh_windows == 0
        assert set(third.windows) >= only_one | only_two | set(seed.windows)

    def test_a_part_alone_becomes_the_file_and_a_restamped_file_contributes_nothing(
        self, spec, guard, tmp_path
    ):
        texts = self._texts(spec, 2)
        absent = self._memo(tmp_path)
        part = tmp_path / "part.0.gz"
        walker = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(absent, write_path=part)
        )
        walker.walk_many(texts)
        assert walker.save_memo() and not absent.path.exists()
        assert conform.absorb_settle_memo_parts(absent, [part], spec)
        again = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=absent)
        again.walk_many(texts)
        assert again._settle_calls == 0 and again.memo_windows == len(walker.windows)

        restamped = self._memo(tmp_path, "tables-stamp-b")
        fresh = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(restamped, write_path=part)
        )
        fresh.walk_many(texts[: len(texts) // 2])
        assert fresh.save_memo()
        assert conform.absorb_settle_memo_parts(restamped, [part], spec)
        loaded = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=restamped)
        loaded.walk_many(texts[: len(texts) // 2])
        assert loaded._settle_calls == 0 and loaded.memo_windows == len(fresh.windows)
        assert not conform.absorb_settle_memo_parts(restamped, [tmp_path / "nowhere.gz"], spec)

    def test_absorbing_parts_restamps_the_file_and_retires_what_the_new_keys_retire(
        self, spec, guard, tmp_path
    ):
        """The issue 202 shape across the absorb: a part written under keys naming ·Tea as moved is folded into a file written under the old keys, and the result must carry the new keys without carrying the entries those keys retire — otherwise a stale settlement rides a header that vouches for it, and no ordinary run can retire it again."""
        texts = self._texts(spec)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=self._memo(tmp_path, keys=keys))
        first.walk_many(texts)
        assert first.save_memo()
        naming = {
            window
            for window in first.windows
            if any(conform._label_family(label) == "qsTea" for label in window)
        }

        edited = _tea_prefers_half_before_may(spec)
        edited_guard = kernel_exec.guard_sweep(edited)
        moved = self._memo(tmp_path, keys={**keys, "qsTea": "qsTea@1"})
        part = tmp_path / "part.0.gz"
        ranged = conform._SettledWindowWalk(
            edited, frozenset(), {}, edited_guard, memo=replace(moved, write_path=part)
        )
        ranged.walk_many(texts[: len(texts) // 3])
        assert ranged.stale_windows == len(naming) and ranged.fresh_windows
        assert ranged.save_memo()
        assert conform.absorb_settle_memo_parts(moved, [part], edited)

        reference = conform._SettledWindowWalk(edited, frozenset(), {}, edited_guard)
        expected = reference.walk_many(texts)
        third = conform._SettledWindowWalk(edited, frozenset(), {}, edited_guard, memo=moved)
        assert third.walk_many(texts) == expected
        assert third.stale_windows == 0
        assert third.memo_windows == len(first.windows) - len(naming) + ranged.fresh_windows
