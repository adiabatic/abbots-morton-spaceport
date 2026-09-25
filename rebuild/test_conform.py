"""Tests for conform.py and the oracle stages that consume it: label normalization, the raw GSUB replay, the isolated overlay, alias and ledger matching, kern evaluation and the position channel, the oracle's audit shards, row cache and row ranges, the belt's bookkeeping, and the memoized settle walk and its memo file, checked against settling the same texts without a memo. The belt at its full horizon runs in run_m1 against the compiled M1 font. Settlement comes from the Rust crate, so these tests need a built kernel: the formation-guard sweep and the settle walk both call it."""

import gzip
import hashlib
import inspect
import itertools
import json
import os
import pickle
import struct
import subprocess
import sys
import zlib
from collections.abc import Sequence
from dataclasses import asdict, replace
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

import pytest

from rebuild.pipeline import (
    baseline_subset,
    conform,
    kernel_exec,
    labels,
    oracle,
    oracle_cache,
    oracle_positions,
    run_m1,
    settle,
    witness,
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
    """The crate's section 5.7 formation-guard verdicts for the fixture spec, swept once for the module and passed to the formation calls below."""
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
        """Under the overlay each component of a ligature renders as its own twin, because the ss10 pre-empt substitutes the twins before formation. A boundary token renders as its own glyph."""
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
    """`OVERLAY_CONFIGS` is exactly the acceptance configurations whose features activate an `overlay: isolated` taste set, and `SETTLEMENT_CONFIGS` is the rest. For an overlay configuration the oracle and the belt take everything from the registry and the font's `hmtx`, and never call the crate."""

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
        """At any belt horizon, the overlay branch shapes every text of one or two alphabet symbols and nothing longer, and it never forms, settles, memoizes or calls the crate."""

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

    @staticmethod
    def _walk_raw_ledger(ledger, row):
        """The reference result: each entry's `match` evaluated against the row in ledger order, without the compiled buckets. The compiled ledger must return the same ids in the same order."""
        classified = oracle.classify_divergence(row)
        matches = []
        for entry in ledger:
            match = entry.get("match", {})
            entry_configs = match.get("configs", "all")
            if entry_configs != "all" and row.config not in entry_configs:
                continue
            predicate_name = match.get("predicate")
            if predicate_name is not None:
                class_id = oracle.CLASS_PREDICATE_IDS.get(predicate_name)
                if class_id is not None:
                    if classified != class_id:
                        continue
                else:
                    function = oracle.PREDICATES.get(predicate_name)
                    if function is None or not function(row):
                        continue
            else:
                window = match.get("window")
                if window is not None and window not in row.codepoints:
                    continue
                seam_change = match.get("seam_change")
                if seam_change is not None and "seam" not in row.kinds:
                    continue
            matches.append(entry.get("id", "<unnamed>"))
        return matches

    @staticmethod
    def _rows_for_every_arm():
        def row(
            config,
            codepoints,
            kinds,
            phenomena,
            baseline_glyphs=(),
            baseline_seams=(),
            new_cells=(),
            new_seams=(),
        ):
            return conform.DivergentRow(
                config=config,
                codepoints=codepoints,
                kinds=kinds,
                position=0,
                baseline_glyphs=baseline_glyphs,
                baseline_seams=baseline_seams,
                new_cells=new_cells,
                new_seams=new_seams,
                phenomena=phenomena,
            )

        boundary_echo = row("default", "200C:E652:E670", ("cell",), ("+locked", "old-noentry"))
        ss10_seam_loss = row(
            "ss10",
            "E650:E659",
            ("seam",),
            ("seam-loss",),
            baseline_glyphs=("qsPea", "qsVie"),
            baseline_seams=("y0",),
            new_cells=("qsPea/full/None/None/", "qsVie/normal/None/None/"),
            new_seams=("break",),
        )
        position_kern = row(
            "ss04", "E650:E652", ("position",), ("position-kern-attributable", "position-drift")
        )
        unclassified = row("default", "E650:E652", ("cell",), ("+ex-bind-1",))
        scoped_out = row("ss05", "E650:E665:E652", ("cell",), ("exit-dropped",))
        seamed_scoped = row("ss03", "E652:E679", ("cell", "seam"), ("seam-gain:qsTea",))
        return [boundary_echo, ss10_seam_loss, position_kern, unclassified, scoped_out, seamed_scoped]

    _LEDGER_FOR_EVERY_ARM = [
        {"id": "boundary-echo", "match": {"predicate": "boundary_echo", "configs": "all"}},
        {"id": "kern-out-of-scope", "match": {"predicate": "kern_channel_out_of_scope", "configs": "all"}},
        {"id": "ss10-isolation", "match": {"predicate": "ss10_isolation_completed", "configs": ["ss10"]}},
        {"id": "dangling-on-ss04", "match": {"predicate": "dangling_anchor_dropped", "configs": ["ss04"]}},
        {"id": "nobody-knows-this", "match": {"predicate": "no_such_predicate", "configs": "all"}},
        {"id": "everything", "match": {}},
        {"id": "pre-ligature-window", "match": {"window": "E652:E679"}},
        {"id": "seams-only", "match": {"seam_change": True}},
        {"match": {"predicate": "dangling_anchor_dropped", "configs": "all"}},
        {
            "id": "cleanup-on-ss03",
            "match": {"predicate": "pre_ligature_cleanup_regularized", "configs": ["ss03"]},
        },
    ]

    def test_the_compiled_ledger_answers_what_a_walk_of_the_raw_ledger_answers(self):
        """The compiled ledger against the reference walk, over a ledger with one entry of each kind: a class entry open to every configuration, a class entry scoped to one configuration, a function predicate, a predicate in neither map, an empty `match`, a `window` test, a `seam_change` test, and an entry with no `id`. The rows reach each of them. List equality checks both the ids and their order."""
        ledger = self._LEDGER_FOR_EVERY_ARM
        compiled = oracle.compile_ledger(ledger)
        answers = {}
        for row in self._rows_for_every_arm():
            expected = self._walk_raw_ledger(ledger, row)
            assert oracle._match_compiled(compiled, row) == expected, row
            assert oracle._match_ledger(ledger, row) == expected, row
            answers[(row.config, row.codepoints)] = expected
        assert answers == {
            ("default", "200C:E652:E670"): ["boundary-echo", "everything"],
            ("ss10", "E650:E659"): ["ss10-isolation", "everything", "seams-only"],
            ("ss04", "E650:E652"): ["kern-out-of-scope", "everything"],
            ("default", "E650:E652"): ["everything"],
            ("ss05", "E650:E665:E652"): ["everything", "<unnamed>"],
            ("ss03", "E652:E679"): ["everything", "pre-ligature-window", "seams-only", "cleanup-on-ss03"],
        }

    def test_a_two_plus_match_comes_back_in_ledger_order_from_either_end(self):
        """A row that matches a class entry and an unconditional entry takes its two hits from two buckets, and the list still comes back in ledger order whichever entry the ledger lists first. The run_m1 gate requires zero multi-matched rows on the live ledger, so no other test checks this order."""
        row = conform.DivergentRow(
            config="default",
            codepoints="E650:E665",
            kinds=("cell",),
            position=0,
            baseline_glyphs=(),
            baseline_seams=(),
            new_cells=(),
            new_seams=(),
            phenomena=("exit-dropped",),
        )
        classed = {"id": "dangling-anchor-dropped", "match": {"predicate": "dangling_anchor_dropped"}}
        blanket = {"id": "blanket", "match": {}}
        kern = {"id": "kern", "match": {"predicate": "kern_channel_out_of_scope"}}
        for ledger, expected in (
            ([blanket, classed], ["blanket", "dangling-anchor-dropped"]),
            ([classed, blanket], ["dangling-anchor-dropped", "blanket"]),
            ([kern, blanket, classed], ["blanket", "dangling-anchor-dropped"]),
        ):
            assert oracle._match_compiled(oracle.compile_ledger(ledger), row) == expected
            assert oracle._match_ledger(ledger, row) == expected

    def test_an_empty_match_takes_every_row_and_an_unknown_predicate_takes_none(self):
        """An empty `match` matches every row, including the unclassified one. An entry whose predicate is in neither `CLASS_PREDICATE_IDS` nor `PREDICATES` matches no row and goes into no bucket, which is also what the reference walk returns for it."""
        ledger = [
            {"id": "unknown", "match": {"predicate": "no_such_predicate"}},
            {"id": "everything", "match": {}},
        ]
        compiled = oracle.compile_ledger(ledger)
        assert compiled.by_class == {}
        assert compiled.functions == ()
        assert compiled.unconditional == ((1, "everything", None, None, False),)
        for row in self._rows_for_every_arm():
            assert oracle._match_compiled(compiled, row) == ["everything"], row
        unclassified = self._rows_for_every_arm()[3]
        assert oracle.classify_divergence(unclassified) is None
        assert oracle._match_compiled(compiled, unclassified) == ["everything"]

    def test_a_bare_string_configs_keeps_the_raw_walk_s_test_and_a_missing_value_is_refused(self):
        """A bare-string `configs` keeps the reference walk's substring `in` test: the ss05 row matches `ss03+ss05` under it and would not under an exact match. A `configs:` with no value fails when the ledger compiles instead of matching every configuration."""
        bare = [{"id": "bare", "match": {"predicate": "dangling_anchor_dropped", "configs": "ss03+ss05"}}]
        compiled = oracle.compile_ledger(bare)
        for row in self._rows_for_every_arm():
            assert oracle._match_compiled(compiled, row) == self._walk_raw_ledger(bare, row), row
        scoped_out = self._rows_for_every_arm()[4]
        assert scoped_out.config == "ss05"
        assert oracle._match_compiled(compiled, scoped_out) == ["bare"]
        with pytest.raises(TypeError):
            oracle.compile_ledger([{"id": "null", "match": {"predicate": "boundary_echo", "configs": None}}])

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
        """The boundary-equals-word-boundary rule: in a window that contains a run-splitting boundary (space or ZWNJ), a cell or seam divergence classifies as `boundary-echo` ahead of every other class, whatever its phenomena. A position-only row gets no class here; it goes to the kern-attribution predicate."""
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
        """In the window ·It·Roe·Tea·Zoo the old ·It·Roe pixels differ from the new ones even though every origin and advance matches, so the position channel cannot catch the change. The ·Zoo entry-contraction class must exclude that window so the difference stays visible."""
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
        evaluator = oracle_positions.KernEvaluator(sidecar)
        assert evaluator.value_for("qsBay.en-y0", "qsTea") == -1
        assert evaluator.value_for("qsBay", "qsTea.half.ex-y5") == -1
        assert evaluator.value_for("qsNo.alt.en-y5", "qsPea") == -2
        assert evaluator.value_for("qsNo", "qsPea") == 0
        assert evaluator.value_for("qsHe", "qsMay.noentry") == -3
        assert evaluator.value_for("qsHe", "qsMay") == 0

    def test_global_record(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nglobal: {value: -1}\n")
        evaluator = oracle_positions.KernEvaluator(sidecar)
        assert evaluator.value_for("qsPea", "qsTea") == -1

    def test_real_sidecar_parses(self):
        evaluator = oracle_positions.KernEvaluator(
            Path(__file__).resolve().parents[1] / "glyph_data" / "senior_quikscript_kerning.yaml"
        )
        assert isinstance(evaluator.value_for("qsBay", "qsTea"), int)


class TestAliasCompleteness:
    def _names(self, tmp_path, names):
        """Write the subset-names sidecar that the refilter writes. The alias check reads only this file, so these tests need no subset tables."""
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
        kern = oracle_positions.KernEvaluator(sidecar)
        row = self._row([0xE679, 0xE650], ["qsOy", "qsPea"], [(0, 0, 300), (0, 0, 250)])
        expected, attributable = oracle_positions._kern_normalized_positions(kern, row, 50)
        assert expected == ((0, 0, 450), (0, 0, 250))
        assert attributable == (True, False)

    def test_kern_partner_skips_the_zwnj_slot(self, tmp_path):
        sidecar = tmp_path / "kern.yaml"
        sidecar.write_text("---\nleft_family: [qsOy]\nright_family: [qsPea]\nvalue: -3\n")
        kern = oracle_positions.KernEvaluator(sidecar)
        row = self._row(
            [0xE679, 0x200C, 0xE650],
            ["qsOy", "space", "qsPea.noentry"],
            [(0, 0, 300), (0, 0, 0), (0, 0, 250)],
        )
        expected, attributable = oracle_positions._kern_normalized_positions(kern, row, 50)
        assert expected == ((0, 0, 450), (0, 0, 0), (0, 0, 250))
        assert attributable == (True, True, False)


class TestPositionProjection:
    """`Shaper.positions` must equal the (x_offset, y_offset, x_advance) projection of `Shaper.shape`, over the first 400 rows of each frozen mini-bundle table and the checked-in smoke sequences, under every acceptance configuration's features. The shaper reuses one HarfBuzz buffer for its whole life, so the corpus goes through both projections alternately. A buffer that kept state between calls, or a projection that returned zeros of the right length, fails here."""

    @pytest.fixture(scope="class")
    def corpus(self) -> list[tuple[frozenset[str], str]]:
        from rebuild.pipeline import coretext_smoke
        from rebuild.validation.rowmodel import iter_rows

        with coretext_smoke.DEFAULT_SEQUENCES.open(encoding="utf-8") as handle:
            smoke = [
                "".join(map(chr, codepoints)) for _label, codepoints in coretext_smoke.parse_sequences(handle)
            ]
        assert smoke
        corpus: list[tuple[frozenset[str], str]] = []
        for config in conform.ACCEPTANCE_CONFIGS:
            features = conform.features_for_config(config)
            texts = [row.text for row in iter_rows(MINI / f"baseline-{config}.subset.tsv.gz", 0, 400)]
            assert texts, config
            corpus.extend((features, text) for text in texts + smoke)
        return corpus

    @staticmethod
    def _triples(shaped: list[dict]) -> list[tuple[int, int, int]]:
        return [(g["x_offset"], g["y_offset"], g["x_advance"]) for g in shaped]

    def test_positions_is_the_triple_projection_of_shape(self, corpus):
        shaper = conform.Shaper(MINI / "M1.otf")
        offsets = 0
        advances: set[int] = set()
        for features, text in corpus:
            full = self._triples(shaper.shape(text, features))
            assert shaper.positions(text, features) == full, (sorted(features), text)
            offsets += sum(1 for x, y, _advance in full if x or y)
            advances.update(advance for _x, _y, advance in full)
        assert offsets > 0
        assert len(advances) > 1

    def test_the_kept_buffer_carries_nothing_between_calls(self):
        shaper = conform.Shaper(MINI / "M1.otf")
        features = conform.features_for_config("default")
        first = "\ue650\ue652\ue665"
        second = "\ue665\ue652"
        before = shaper.shape(first, features)
        other = shaper.positions(second, features)
        after = shaper.shape(first, features)
        assert before == after
        assert shaper.positions(first, features) == self._triples(after)
        assert len(other) == 2


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

    def test_per_configuration_workers_merged_in_any_order_write_the_serial_report(
        self, spec, guard, tmp_path
    ):
        """The pooled belt against the serial one, with no pool: each configuration's worker runs as a pool would run it, the results are taken in reverse order, reordered by acceptance configuration and merged, and the merged report must write the same bytes as `run_conformance`. A worker that built its shaper, alphabet or splitters differently from the serial path, or a merge in completion order, would fail here. Both paths run with no glyph mapping and the worker receives the guard, so neither the anchor check nor a worker's own guard sweep is tested."""
        font = MINI / "M1.otf"
        finished = [
            conform.conformance_config_worker(spec, font, config, 2, None, guard, None)
            for config in reversed(conform.ACCEPTANCE_CONFIGS)
        ]
        by_config = {result.config: result for result in finished}
        merged = conform.merge_conformance_results(
            font, [by_config[config] for config in conform.ACCEPTANCE_CONFIGS]
        )
        assert merged.shaping_runs > 0
        merged.write(tmp_path / "a.json")
        conform.run_conformance(font, spec, max_length=2, out_dir=tmp_path, summary_name="b.json")
        assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


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
    """`divergence-audit.tsv` is fingerprinted and later stages parse it from disk, so its bytes must not change. Each configuration's rows are written to a shard where they are produced, and the parent concatenates the shards after the header. These tests check that the concatenation equals the header and all rows joined with newlines, for every shape the audit can take, including an empty configuration and an empty audit. They also check that a missing or short shard fails instead of producing a short audit that looks complete."""

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
        """A configuration cut into row ranges arrives as one segment per range. The join must produce the same bytes as the uncut shard however the rows were split, including an empty second segment, which is what a range with no divergent row writes."""
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
        """The same check over a real audit. The mini bundle's `audit.tsv` is a filtered live audit that `fixtures/mini/regenerate.py` writes by joining its rows with newlines, and its configuration blocks are contiguous and in `ACCEPTANCE_CONFIGS` order. Splitting it into shards and joining them must reproduce the file."""
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
        """`--jobs 1` writes the audit as it goes, and the pool writes shards that the parent concatenates. Both must produce the same bytes. The two paths run here over the same hand-made subset tables: a pending alias makes every row diverge and an empty ledger leaves every divergence UNMATCHED, so every row reaches the file through each path."""
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
        """A truncated audit hashes differently instead of reading as stale, so the surface build would take it as a new, smaller, self-consistent audit. That is why `--jobs 1` writes through a staging copy and promotes it only after the last configuration, and why an oracle that fails on its second configuration must leave the previous file in place."""
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
        """Every shard is checked for before any byte is copied, so the join cannot write a short audit from whatever is on disk. The existing audit must survive the failure, because the caller deletes the scratch directory afterward and the shards are then gone too."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        scratch = oracle.oracle_audit_scratch(tmp_path)
        self._shard(scratch, "default", ["default\tE650\tcell\ta\tqsPea\tqsPea.half"])
        with pytest.raises(FileNotFoundError, match="ss03"):
            oracle.join_oracle_audit(tmp_path, scratch, ("default", "ss03"), 1)
        assert standing.read_bytes() == b"the audit of the last green run\n"

    def test_an_audit_short_of_the_rows_its_workers_counted_is_not_promoted(self, tmp_path):
        """The workers' row counts come back through the pipe and the rows come back on disk, so comparing the two is the only cross-check the parent can make. It catches a shard that was truncated but closed cleanly, which checking for the file cannot find."""
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
        """The pid in the scratch directory name matters in both directions. A killed run skips its `finally`, so without this sweep its shards would stay on disk indefinitely. A running pid is another oracle using the same out_dir (a `--gates-only` pass beside a cycle), and deleting its directory would remove shards it is about to concatenate."""
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
        """The scratch directory sits beside the artifacts in `rebuild/out/m1`, so its name and its shard names must match nothing that reads that directory: the cycle's artifact list, its subset-table glob, and the table readers' patterns. Configuration names contain `+`, which the baseline table filenames already use."""
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


def _divergent_subset_tables(root: Path, per_config: dict[str, list[tuple[int, int]]]) -> Path:
    """Write hand-made subset tables under `root / "tables"` in which every row diverges against an all-pending alias map."""
    tables = root / "tables"
    tables.mkdir(parents=True)
    for config, pairs in per_config.items():
        with gzip.open(tables / f"baseline-{config}.subset.tsv.gz", "wt", encoding="utf-8") as fh:
            fh.write(f"# config: {config}\n")
            for left, right in pairs:
                fh.write(
                    f"{left:04X}:{right:04X}\told{left:04X}|old{right:04X}\t0,1\tbreak"
                    "\t0,0,150|150,0,150\n"
                )
    return tables


class TestOracleUnmatchedTally:
    """What a configuration's result carries about its unmatched rows. Every unmatched row is already written to that configuration's audit shard, and `oracle_summary.json` needs only their count and the first `ORACLE_UNMATCHED_EXEMPLARS` of them to quote. So the result carries the count and that first slice instead of pickling the whole list across the process pipe. These tests check the count, the cap, and the table order the exemplars must keep so the summary quotes the same rows."""

    def test_a_configuration_sends_home_a_count_and_the_first_twenty_rows(self, spec, tmp_path):
        letters = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667, 0xE670, 0xE679, 0xE67A)
        pairs = [(left, right) for left in letters for right in letters]
        wide = pairs[:25]
        narrow = pairs[25:28]
        tables = _divergent_subset_tables(tmp_path, {"default": wide, "ss03": narrow})
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
        """The wide configuration is cut so its first range holds fewer unmatched rows than the cap. The fold must quote the first `ORACLE_UNMATCHED_EXEMPLARS` rows in table order across the ranges, as `oracle_summary.json` prints them, and count every unmatched row in either range."""
        letters = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667, 0xE670, 0xE679, 0xE67A)
        pairs = [(left, right) for left in letters for right in letters]
        wide = pairs[:25]
        tables = _divergent_subset_tables(tmp_path, {"default": wide})
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


class TestOracleMultiMatchedTally:
    """What a configuration's result carries about rows that two or more ledger entries match. The run_m1 gate reads only `multi_matched` in `oracle_summary.json`, which must be zero, and each such row is already an audit line with its matched ids joined by `+`. So a range returns only a count, and an overlapping ledger entry fails the build at the cost of one integer per range instead of one pickled `DivergentRow` per row. These tests check that the count partitions the divergent rows with the other two tallies on every path, that it sums through both folds, and that the result crossing the pipe does not grow with it."""

    LETTERS = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667, 0xE670, 0xE679, 0xE67A)
    PAIRS: list[tuple[int, int]] = list(itertools.product(LETTERS, LETTERS))

    def _aliases(self, tmp_path: Path) -> Path:
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("".join(f"old{code:04X}: pending\n" for code in self.LETTERS))
        return aliases

    @staticmethod
    def _accounted_for(tally: oracle.BaselineReport | oracle.OracleConfigResult) -> int:
        return sum(tally.counts_by_entry.values()) + tally.unmatched_count + tally.multi_matched_count

    def test_a_row_two_entries_match_travels_home_as_a_count_on_every_path(self, spec, tmp_path):
        wide = self.PAIRS[:25]
        narrow = self.PAIRS[25:28]
        tables = _divergent_subset_tables(tmp_path, {"default": wide, "ss03": narrow})
        aliases = self._aliases(tmp_path)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text('- id: every-row\n  match: {}\n- id: pea-rows\n  match: {window: "E650"}\n')
        configs = ("default", "ss03")
        doubled = {
            config: [f"{left:04X}:{right:04X}" for left, right in rows if 0xE650 in (left, right)]
            for config, rows in (("default", wide), ("ss03", narrow))
        }
        assert len(doubled["default"]) == 11
        assert sum(0xE650 in pair for pair in wide[:10]) == 10
        assert sum(0xE650 in pair for pair in wide[10:]) == 1
        assert len(doubled["ss03"]) == 1

        serial = tmp_path / "serial"
        report = oracle.compare_against_baseline(
            spec, tables, aliases, ledger, configs=configs, out_dir=serial
        )
        assert report.multi_matched_count == 12
        assert report.unmatched_count == 0
        assert report.counts_by_entry == {"every-row": 16}
        assert self._accounted_for(report) == report.divergent_rows == 28
        audit = (serial / "divergence-audit.tsv").read_text(encoding="utf-8").splitlines()
        assert [
            line.split("\t")[1] for line in audit[1:] if line.split("\t")[3] == "every-row+pea-rows"
        ] == doubled["default"] + doubled["ss03"]

        fanned = tmp_path / "fanned"
        fanned.mkdir()
        results = [
            oracle.oracle_config_worker(
                spec,
                tables,
                aliases,
                ledger,
                config,
                None,
                None,
                audit_dir=oracle.oracle_audit_scratch(fanned),
            )
            for config in configs
        ]
        assert [result.multi_matched_count for result in results] == [11, 1]
        merged = oracle.merge_oracle_results(results)
        assert merged.multi_matched_count == 12

        cut = tmp_path / "cut"
        cut.mkdir()
        shards = [oracle.OracleShard("default", 0, 10, 0, 2), oracle.OracleShard("default", 10, None, 1, 2)]
        ranged = [
            oracle.oracle_config_worker(
                spec,
                tables,
                aliases,
                ledger,
                "default",
                None,
                None,
                audit_dir=oracle.oracle_audit_scratch(cut),
                shard=shard,
            )
            for shard in shards
        ]
        assert [result.multi_matched_count for result in ranged] == [10, 1]
        folded = oracle.merge_config_shards(ranged)
        assert folded.multi_matched_count == 11
        rejoined = oracle.merge_oracle_results([folded, results[1]])
        assert rejoined.multi_matched_count == 12
        for tally in (*results, merged, *ranged, folded, rejoined):
            assert self._accounted_for(tally) == tally.divergent_rows
        oracle.discard_oracle_audit_scratch(fanned)
        oracle.discard_oracle_audit_scratch(cut)

    def test_what_a_range_sends_home_does_not_grow_with_its_multi_matched_rows(self, spec, tmp_path):
        """Every divergent row matches both entries, so every tally except the multi-matched count stays empty or small. Every integer in the result is below 256 and pickles to the same width, so the pickled result is the same length for ten such rows as for twenty-five."""
        aliases = self._aliases(tmp_path)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("- id: first\n  match: {}\n- id: second\n  match: {}\n")
        pickled = []
        for rows in (self.PAIRS[:10], self.PAIRS[:25]):
            root = tmp_path / str(len(rows))
            tables = _divergent_subset_tables(root, {"default": rows})
            scratch = oracle.oracle_audit_scratch(root)
            result = oracle.oracle_config_worker(
                spec, tables, aliases, ledger, "default", None, None, audit_dir=scratch
            )
            assert result.multi_matched_count == len(rows)
            assert result.counts_by_entry == {}
            pickled.append(len(pickle.dumps(replace(result, peak_rss_bytes=0))))
            oracle.discard_oracle_audit_scratch(root)
        assert pickled[0] == pickled[1]


CACHE_LETTERS = (0xE650, 0xE652, 0xE653, 0xE65A, 0xE665, 0xE667)


_INK_IDENTICAL_LEDGER = "- id: ink-identical\n  ink_identical: true\n  match: {}\n"


def _cache_subset_table(directory: Path, config: str, rows: Sequence[tuple[int, ...]]) -> Path:
    """Write one hand-made subset table in the shape `iter_rows` reads. Each row's old glyph names are made from its codepoints, so an all-pending alias map makes every row diverge and an empty ledger leaves every divergence UNMATCHED. The names lack the `qs` prefix that `unreachable_glyph_heads` checks, so no row is refused service for naming a family its codepoints cannot reach."""
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
    """A stamp in the shape `run_m1` builds, reduced to the lines that decide a store's identity here: the format, the configuration, a placeholder for the code closure, and the `subset` line that `open_row_cache` reads the table's digest from."""
    return oracle_cache.EnvironmentStamp(
        lines=(
            f"format\t{oracle_cache.STORE_FORMAT}",
            f"config\t{config}",
            "oracle_code\tpinned-by-the-test",
            f"subset\t{hashlib.sha256(table.read_bytes()).hexdigest()}",
        )
    )


def _cache_ages(path: Path) -> list[int]:
    """Every record's row-verdict `derived_at_pass`, read directly from the store file instead of through `load_store`, so the test sees what a pass recomputed independently of the reader that decides what to serve. A record whose age is this pass's ordinal was derived in this pass; an older ordinal means it was served."""
    body = gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    return [int(line.rsplit("\t", 2)[1]) for line in body[1:-1]]


def _cache_position_ages(path: Path) -> list[int]:
    """The pass each record's position verdict was derived at, the last field of every record."""
    body = gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    return [int(line.rsplit("\t", 1)[1]) for line in body[1:-1]]


def _cache_position_tags(path: Path) -> list[str]:
    """Each record's position tag, read without loading the store: `?` never shaped, `-` shaped with no drift, `D` drifted."""
    tags: list[str] = []
    for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()[1:-1]:
        fields = line.split("\t")
        tags.append(fields[2] if fields[1] == "-" else fields[7])
    return tags


def _excluded_from_the_channel(audit: Path, rows: int) -> set[int]:
    """The rows the position channel skips whatever the ledger says, those with a ligation or seam divergence. Read from an audit in which every row diverges, so its line order is the table's row order."""
    lines = audit.read_text().splitlines()[1:]
    assert len(lines) == rows
    return {
        index
        for index, line in enumerate(lines)
        if {"ligation", "seam"} & set(line.split("\t")[2].split(","))
    }


def _tea_prefers_half_before_may(spec):
    """A rune edit that changes settlement: ·Tea gets an absolute preference for its half stance before ·May, which changes what the ·Tea·May windows settle to and nothing else."""
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
    """An edit to the ligature rune and to neither component's file: `·Tea+·Oy` refuses its baseline exit before every letter, so every window whose left slot is a formed `·Tea+·Oy` settles to a break where the unedited spec joins, while ·Tea's and ·Oy's own windows are unchanged."""
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
    """Copy `source`, widening the advance of every glyph `touches` selects by 37 units. The family's compiled-glyph digest changes through the metrics alone, every outline stays the same, and every row that shapes one of those glyphs places its followers differently."""
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
    """The rows this pass re-derives regardless of their families: the ordinal clause of `RowStore.due`, which re-derives one row in every `MAX_RECORD_AGE` rows on each pass, so no verdict goes that many passes without being recomputed. This is why no test below expects every row to be served."""
    current = pass_ordinal + 1
    return {
        index
        for index in range(rows)
        if current % oracle_cache.MAX_RECORD_AGE == index % oracle_cache.MAX_RECORD_AGE
    }


def _position_bench(spec, tmp_path: Path, ledger_entries: str = _INK_IDENTICAL_LEDGER):
    """The position channel's test bench. Its rows are the frozen mini bundle's default-table rows that use only mini-spec letters and boundaries, with real old-font positions and glyph names, plus three hand-made ·Tea·May rows at the end: the bundle has no adjacent ·Tea·May pair, and the rune edit these tests share changes that pair. The alias map is all-pending, so every row diverges, and the ledger's one ink-identical entry matches every row, so every row without a ligation or seam divergence enters the channel. The font is the bundle's frozen `M1.otf`, the after font the rows were extracted against."""
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
    """The persisted per-row oracle cache, checked against the audit's bytes. Every test runs the real `compare_against_baseline` over hand-made subset tables and synthetic family keys. A served pass and a cold one must write the same file; an edit to any number of runes must re-derive the rows naming those runes and no others; and any way a store can disagree with the table under it must cost one full pass, never a wrong audit. The cache has no threshold on how many runes an edit touches, so the parametrized edit goes up to four moved families and still expects exactly the union of their rows."""

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
        """A pass that took its position verdicts from the previous pass's store writes the same `divergence-audit.tsv` as a cold pass over the same font, and so does the uncached path, while serving every position except the renewal slice. The bench drifts for real (old-font positions against the frozen after font), so the audit has position rows and the equality covers drift descriptions, not an empty channel."""
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

    def test_a_served_position_channel_that_disagrees_with_harfbuzz_is_a_hard_stop(
        self, spec, tmp_path, monkeypatch
    ):
        """The served-position verifier must stop the run: a served pass whose sampled positions re-shape to something other than what the store holds aborts instead of writing them into the audit. No other test triggers this check. The test replaces `_position_drift` in the position channel's module, which `oracle._compare_config` calls through the module and not through an imported name. The renewal slice's fresh shaping therefore stores the same wrong answer (the abort prevents that store from being promoted), and the sampled served rows, re-shaped through the replaced function, disagree with the records they were served from. A verifier with nothing to re-shape would let this pass."""
        tables, aliases, ledger, stamps, configs, _rows = self._position_bench(spec, tmp_path)
        keys = self._keys(spec)
        position = oracle_cache.position_keys(REPO_ROOT, keys, MINI / "M1.otf", None)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )
        shared.update(font=MINI / "M1.otf", position=position)
        _cold, _, cold_stores = self._pass(spec, tmp_path, "cold", **shared)

        real = oracle_positions._position_drift

        def poisoned(shaper, kern, features, row):
            drift = real(shaper, kern, features, row)
            return (("poisoned",), False) if drift is None else (drift[0] + ("poisoned",), drift[1])

        monkeypatch.setattr(oracle_positions, "_position_drift", poisoned)
        with pytest.raises(SystemExit, match="the oracle position store served a stale verdict"):
            self._pass(spec, tmp_path, "served", read_dir=cold_stores, **shared)

    def test_a_glyph_edit_re_shapes_exactly_the_rows_that_reach_its_family(self, spec, tmp_path):
        """The position key's per-family grain end to end: ·Tea's glyphs in the after font get a wider advance, so every row that shapes one places its followers differently. A pass that carries the previous store across that font edit must write the same audit as a pass with no store over the edited font. The store it leaves must show that exactly the rows naming ·Tea were re-shaped, with every other position and every row verdict served, so a font edit costs only the shaping it changed."""
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
        """The row key inside the position key: the ·Tea rune edit moves ·Tea's row key, so its rows re-derive and re-shape together (a served position over a fresh settlement would shape the previous pass's cells). A pass carrying the store across the edit must write the same audit as a pass with no store."""
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
        """The position stamp end to end: a kern sidecar edit changes what every row's old positions normalize to, so every position re-shapes while every row verdict is still served, and the carried audit equals the audit written from scratch with the edited sidecar."""
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
        """The position verdict is stored before the ledger's eligibility test. A pass whose ledger admits no row to the channel records every position as never shaped, and the next pass under an admitting ledger shapes them all and serves none, still writing the from-scratch audit. A pass whose ledger excludes a row it shaped earlier carries that verdict forward unread, so the next pass that admits the row again finds it served."""
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
        """A pass that took its verdicts from the previous pass's store writes the same `divergence-audit.tsv` as a cold pass and returns a tally equal in every field, including the counts, the exemplars and the unmatched rows they quote. The uncached path runs as a third reference, because the requirement is that neither cached pass changed the file, not only that the two agree with each other."""
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

    def test_a_served_verdict_that_disagrees_with_a_fresh_comparison_is_a_hard_stop(
        self, spec, tmp_path, monkeypatch
    ):
        """The served-row verifier must stop the run: a served pass whose sampled rows compare differently from what the store holds aborts instead of writing them into the audit. No other test triggers this check. `oracle.py` imports `_compare_row` by name, so replacing it in `conform` reaches only `_verify_served_sample` and not the main loop's fresh rows. The store is right about every row it holds and only the re-derivation disagrees, which is how a stale record looks to the verifier. A verifier with nothing to re-derive would let this pass."""
        tables, aliases, stamps, configs = self._bench(tmp_path)
        ledger = tmp_path / "ledger.yaml"
        ledger.write_text("[]\n")
        keys = self._keys(spec)
        shared: dict[str, Any] = dict(
            tables=tables, aliases=aliases, ledger=ledger, stamps=stamps, keys=keys, configs=configs
        )
        cold_report, _, cold_stores = self._pass(spec, tmp_path, "cold", **shared)
        assert cold_report.unmatched_count == len(self.LETTER_ROWS) * len(configs)

        real = conform._compare_row

        def poisoned(spec, aliases, config, features, row, settled):
            verdict = real(spec, aliases, config, features, row, settled)
            assert verdict is not None
            return replace(verdict, phenomena=verdict.phenomena + ("poisoned",))

        monkeypatch.setattr(conform, "_compare_row", poisoned)
        with pytest.raises(SystemExit, match="the oracle row cache served a stale verdict"):
            self._pass(spec, tmp_path, "served", read_dir=cold_stores, **shared)

    @pytest.mark.parametrize("k", (1, 2, 3, 4))
    def test_an_edit_to_k_runes_re_derives_exactly_the_rows_that_name_them(self, spec, tmp_path, k):
        """However many runes moved, the re-derived rows are exactly those that name one of them: a union, with no threshold and no whole-store drop, so a four-rune edit still serves every row that names none of the four. The renewal slice is added to the expected set, because it is a property of the store and not of the edit."""
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
        """Incremental equals from-scratch across an edit that changes settlement: ·Tea gains a preference for its half stance before ·May, which changes what the ·Tea·May row settles to and nothing else. A pass that carries the previous store across that edit must write the same audit as a pass with no store over the edited spec, so a store that served the changed row, or a key that misses part of the edit's reach, fails here."""
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
        """The main use of the cache. `rebuild/m1-divergences.yaml` is outside the key because `_match_compiled` runs on every row on every pass, served or not. Replacing the ledger therefore re-derives only the pass's renewal slice, and every `matched_entry` in the audit changes as it does for a pass that compared every row from scratch."""
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
        """Any doubt about a store costs one cold oracle pass and nothing else: a store that does not load is ignored, and the pass starts its own store at ordinal zero. The misaligned record is the exception: an anchor that disagrees with its row means the table under the whole store was replaced, so the pass aborts instead of treating the record as a miss and serving every other record just as wrongly."""
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
            # A single flipped bit inside the deflate stream, which is what bit rot or a half-written copy produces. It raises from the compression layer, not as an `OSError`, so a reader that catches only file errors would fail the build over a file that only saves time. A truncation (the case above) never produces it.
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
        """The audit's line order is the subset table's row order, and the split into served and fresh rows must not affect it. Here the two alternate row by row (every even row names the moved family and is re-derived, every odd row is served), and the file still lists rows in table order, configuration by configuration in the order the caller gave."""
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
    """The oracle's unit of fan-out is a row range of one configuration's table, and cutting a table into ranges must change no count and no byte. `_compare_config` addresses rows by absolute index throughout, so a range is a self-contained segment of the same audit and the same store. Each test runs the real compare over the mini bundle's default rows once whole and once cut, through the worker, folds and joins that `run_m1.run_oracle` uses, and requires the cut run to equal the whole one."""

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
        """One pass through the fan-out's pieces without a pool: a worker per range writing its own segments, the parent's fold and joins, and the joined store promoted. A single whole-table shard is the uncut reference."""
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
        """Cold and uncached, so every row is compared and every eligible position shaped in whichever range holds it. The joined audit must match the whole-table shard's byte for byte, which must match the serial path's, and the fold must reproduce the whole-table result in every field."""
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
        """A joined store is several gzip members where a single-segment store is one, but the decompressed content must be the same: the header line, every record in table order, and the trailer counting them. `load_store` reads across the members, and a joined store missing the tail of its last segment loads as None, which is the truncation check the trailer exists for."""
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
        middle = sorted(shards, key=lambda shard: shard.first_row)[1]
        sliced = oracle_cache.load_store(
            cut_path,
            stamp,
            stamp.labels["subset"],
            spec,
            shared["keys"],
            0,
            *reversed(shared["position"]),
            first_row=middle.first_row,
            stop_row=middle.stop_row,
        )
        assert sliced is not None and sliced.rows == len(rows) and middle.stop_row is not None
        assert (sliced.first_row, sliced.stop_row) == (middle.first_row, middle.stop_row)
        assert 0 < middle.first_row < middle.stop_row < len(rows)
        for index in range(middle.first_row, middle.stop_row):
            assert sliced.serve(index, rows[index]) == reference.serve(index, rows[index])
        payload = gzip.decompress(cut_path.read_bytes())
        short = cut_path.with_name("short.tsv.gz")
        short.write_bytes(gzip.compress(payload[: len(payload) - 40]))
        assert oracle_cache.load_store(short, stamp, stamp.labels["subset"], spec, shared["keys"]) is None

    def test_a_cut_warm_pass_serves_what_a_whole_warm_pass_serves(self, spec, tmp_path):
        """The serve path, the renewal slice and the verification draw are all keyed on the row's ordinal in the table, so a warm pass cut into ranges serves the same rows as a whole warm pass, re-derives the same renewal slice, writes the same audit and stages the same store. `positions_served` sums to the whole pass's figure, and each range draws its own verification sample, which is a superset of the whole pass's draw."""
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
    """Two signatures and one mutation rule that the row cache's keys depend on: the comparison takes no font, the position channel takes no settled stream, and a drift only appends to a row. If any of these changed, the store's key would silently cover the wrong inputs and the cache tests above would still pass."""

    def test_the_comparison_channel_takes_no_font_and_the_position_channel_takes_no_settlement(self):
        comparison = list(inspect.signature(conform._compare_row).parameters)
        assert comparison == ["spec", "aliases", "config", "features", "row", "settled"]
        position = list(inspect.signature(oracle_positions._position_drift).parameters)
        assert position == ["shaper", "kern", "features", "row"]

    def test_the_position_channel_only_appends_position_to_kinds(self, spec, tmp_path, monkeypatch):
        """A constructed drift over two rows, one the alias map leaves clean and one it leaves unaliased, observed through the ledger matches the channel makes before and after the drift. The clean row's drift creates a new divergent row whose kinds are exactly `position`. The divergent row's drift keeps every field it already had and appends `position` to its kinds and `position-drift` to its phenomena."""
        tables = tmp_path / "tables"
        _cache_subset_table(tables, "default", [(0xE650,), (0xE652,)])
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text("oldE650: ignore\noldE652: pending\n")
        ledger = [{"id": "ink-identical", "ink_identical": True, "match": {}}]

        seen: list[conform.DivergentRow] = []
        real = oracle._match_compiled

        def spy(compiled, row):
            seen.append(row)
            return real(compiled, row)

        monkeypatch.setattr(oracle, "_match_compiled", spy)
        result = oracle._compare_config(
            spec,
            tables,
            "default",
            frozenset(),
            labels.load_alias_map(aliases),
            oracle.compile_ledger(ledger),
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
    """A stand-in Shaper that records every text it is asked to shape and returns no glyphs. The belt then records one length divergence per text, and the split-buffer check finds no splitter slot. Tests read `shaped`."""

    def __init__(self):
        self.shaped: list[str] = []

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        self.shaped.append(text)
        return []

    def positions(self, text: str, features: frozenset[str]) -> list[tuple[int, int, int]]:
        self.shaped.append(text)
        return []

    def outline_signature(self, glyph_name: str) -> tuple:
        return ()


class TestBeltEconomics:
    """What the belt does over a short horizon with a stand-in font: every text of every length up to the horizon is shaped once, and the split-buffer check runs on exactly the texts that contain a splitter."""

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
        """The belt runs the split-buffer check on every text that contains a splitter, comparing it with its segments shaped separately, and on no other text, since a text without a splitter is its own single segment. The ZWNJ slot's zero advance and empty outline are checked by read-back's boundary-glyphs stage, not by the belt."""
        split_checked: list[str] = []
        monkeypatch.setattr(
            conform, "check_split_buffer", lambda text, *args, **kwargs: split_checked.append(text)
        )
        _result, shaper = self._run(spec, guard)
        splitters = conform.splitting_boundary_chars(spec)
        assert set(split_checked) == {text for text in shaper.shaped if set(text) & splitters}
        assert split_checked


class TestRawLabelsLateFormation:
    """`raw_labels` forms ligatures through `settle.form_ligatures`, so the section 5.7 guard applies to the replayed labels as it does to the kernel's stream. The mini spec's qsDay_qsUtter case carries the guard's worked example."""

    def test_guard_keeps_the_pair_unformed_before_low(self, spec, guard):
        day, utter, low = chr(0xE653), chr(0xE67A), chr(0xE667)
        assert conform.raw_labels(spec, day + utter + low, frozenset(), guard) == [
            "qsDay",
            "qsUtter",
            "qsLow",
        ]
        assert conform.raw_labels(spec, day + utter, frozenset(), guard) == ["qsDay_qsUtter"]


class TestSettledWindowWalk:
    """The memo keys on the raw window from `_window_rights`: every slot one settlement can read, with `#NA` only past a boundary or the edge. So the walk must match an unmemoized settlement of the same tokens, and its keys must agree with `witness._matched_windows`, which reads the same raw slots. The risk is a key that leaves out a slot the kernel can read, which would replay a wrong outcome somewhere. Both paths run exhaustively here: the walk reuses its memo from the second text on, while the reference settles every text as its own sequence. Rule matching is not part of the walk; `witness._matched_windows` and `_DeepTokenIndex` do it for the build's certificate check, so the tests that need rules exercise them there."""

    SWEEP_CHUNK = 4096

    def _sweep(self, spec, features, alphabet, max_length, rules_by_input=None, deep_index=None, memo=None):
        """Sweep every text up to `max_length`, comparing the walk's settled stream and names with an unmemoized settlement of the same formed tokens, and checking that every window `witness._matched_windows` reads is in the walk's memo. Both sides use the crate, so what this tests is the memo's keying: the walk settles a window once and replays it wherever the key recurs, while the reference settles each text on its own, so a key that dropped a slot settlement can read shows up as a wrong outcome. Texts go through in chunks so the reference's decoded traces stay bounded in memory; the walk keeps its memo across chunks. With `rules_by_input`, the replay also runs through `deep_index` and returns its (window, first-matching rule) pairs for the class-token tests to check. With `memo`, the walk loads that settle memo file first, so a file another producer wrote is checked against the same reference."""
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
                    for _index, window, _matched in witness._matched_windows(
                        spec, text, features, guard, names, {}, None
                    ):
                        assert window in walker.windows, (text, window)
                    if rules_by_input is not None:
                        replayed += [
                            (window, matched)
                            for _index, window, matched in witness._matched_windows(
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
        """The mini spec has no depth-3 or depth-4 prefers, so this test uses the real spec: the chain letters of its deep inputs plus a space, swept to length 5 so both right3 and right4 are reachable. The walk keys its deep slots on raw labels, which is finer than the table's own grain, so some memo key must have its third slot open, not `#NA`."""
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            real_spec = load_default_spec()
        alphabet = tuple(chr(cp) for cp in (0x0020, 0xE652, 0xE653, 0xE665, 0xE666, 0xE679, 0xE67A))
        walker, _replayed = self._sweep(real_spec, frozenset(), alphabet, 5)
        assert any(key[4] != "#NA" for key in walker.windows), "no window opened its third slot"

    def test_prospect_live_slots_agree_between_walk_and_replay(self, monkeypatch):
        """Under the simulated-prospect default, the windows of `fixtures.prospect_spec` where A precedes B and C have a live third slot that the table enumerates. The memoized walk and the unmemoized replay must agree on it, and the table's deep-token index supplies the class map for the replay's rule matching."""
        from rebuild.pipeline import fixtures
        from rebuild.pipeline.model import raw_rename_map
        from rebuild.pipeline.kernel_exec import build_tables

        monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", True)
        spec = fixtures.prospect_spec()
        decision = build_tables(spec, frozenset())[0]
        assert any(row.right3 != "#NA" for row in decision.transitions)
        assert any(rule.look3 for rule in decision.rules)
        assert decision.deep_classes
        rules_by_input = witness._renamed_rules_by_input(spec, frozenset(), decision)
        index = conform._DeepTokenIndex(decision, raw_rename_map(spec, frozenset()))
        _walker, replayed = self._sweep(
            spec, frozenset(), conform.spec_alphabet(spec), 5, rules_by_input, index
        )
        assert any(matched is not None for _window, matched in replayed)

    def test_synthetic_depth4_replay_carries_rules_and_a_genuine_index(self):
        """Class tokens at depth 4 with real rules and a real index: the mini spec plus a reach-3 chain on ·Tea, built in the shipping world, creates an r4 class at the ·Tea·May·May·May windows. `witness._matched_windows`, which the witness stage uses, must resolve the labels to that class token and match rules against it, and the walk must still settle those texts correctly."""
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
        rules_by_input = witness._renamed_rules_by_input(spec, frozenset(), decision)
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
        """`prefill` and `walk` return the same results but differ in cost. A walker given its texts up front serves each from the memo, so `single_settles` stays zero, while a walker asked one text at a time spends a kernel invocation on every miss. The counter shows a caller that forgot to prefill what that costs."""
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
        """`_window_rights` writes `#NA` past a boundary, so several distinct raw case lines share one memo key, and the walk asks the crate only about the first. With `audit_dedupe` on, the walk also settles each later line and asserts it matches the memoized outcome. The mini alphabet at depth 4 has enough such collisions to test this."""
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
        """`on_error` exists for the certificate check. Its prefill covers every certificate at once, so a window the crate refuses in one certificate must not stop the check before the other rules are read. With `on_error="drop"` the refusal is memoized and the text that reached it stops advancing; the refusal is raised when a later walk reaches that key, against the one rule whose certificate carried it. The default mode stays strict, and the same prefill raises."""
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
        """The same audit over the live runes at depth 3, with the shipping alphabet and the key collisions `gate:conform` depends on. Marked slow because it settles every distinct raw window the depth-3 sweep reaches, not one per memo key."""
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
    """Split a window memo the crate wrote into its head, label lines, record lines and row bytes."""
    data = dump.read_bytes()
    head_line, _, _ = data.partition(b"\n")
    head = json.loads(head_line.decode().partition("\t")[2])
    parts = data.split(b"\n", 1 + head["labels"] + head["records"])
    return head, parts[1 : 1 + head["labels"]], parts[1 + head["labels"] : -1], parts[-1]


def _write_dump(
    dump: Path, head: dict, labels: list[bytes], records: list[bytes], rows: list[tuple[int, ...]]
) -> None:
    """Write a window memo in the crate's layout, with the head's counts taken from what is written."""
    head = {**head, "rows": len(rows), "labels": len(labels), "records": len(records)}
    code = "<" + {2: "H", 4: "I"}[head["width"]] * 7
    body = b"".join(struct.pack(code, *row) for row in rows)
    text = f"# {kernel_exec.REPLAY_MEMO_FORMAT}\t{json.dumps(head, separators=(',', ':'))}\n".encode()
    dump.write_bytes(text + b"".join(line + b"\n" for line in [*labels, *records]) + body)


def _texts_to_depth(spec, max_length=3):
    """Every text of the fixture alphabet up to `max_length` characters, the universe the memo tests walk."""
    alphabet = conform.spec_alphabet(spec)
    return [
        "".join(combo)
        for length in range(1, max_length + 1)
        for combo in itertools.product(alphabet, repeat=length)
    ]


class TestCrateEmittedSettleMemo:
    """The string replay writes a window memo in the crate's label format, and `absorb_replay_memo` converts it into a settle memo file. The main test sweeps a walk over a file the crate wrote: a key converted wrongly misses instead of returning a wrong outcome, so the check on the label conversion is that the walk settles nothing (`fresh_windows`), and the check on outcomes is the settled stream against the unmemoized reference. The other tests hold the converted file to the same rules as one the belt writes (stamp, family keys, per-family retirement) and check the conversion's refusals."""

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
        return conform.SettleMemoFile(tmp_path / f"settle-memo-{config}.bin", stamp, dict(keys or {}))

    @pytest.mark.parametrize("config", ["default", "ss03"])
    def test_the_absorbed_memo_serves_every_window_the_walk_reaches_and_settles_alike(
        self, spec, dumps_dir, tmp_path, config
    ):
        """The sweep to the replay's horizon, with a walk carrying the crate-written file, settles nothing (every key the walk forms is one the conversion wrote), and its streams and names equal the unmemoized reference. `ss03` exercises the marker fold on the input and right slots, `default` the unrenamed labels."""
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

    def test_a_walk_that_promotes_nothing_answers_out_of_the_columns_alike(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """A walk built with `promote=False`, as the belt builds it, over the crate-written file settles every text to depth 3 from the store's columns, with the same streams and names as an unmemoized walk, no crate call and nothing added to `windows`. The rows it reached are exactly the distinct windows the reference walk memoized."""
        memo = self._memo(tmp_path)
        conform.absorb_replay_memo(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
        texts = _texts_to_depth(spec, 3)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker._settle_calls == 0 and walker.fresh_windows == 0
        assert not walker.windows
        assert walker._cold.reached_count() == len(reference.windows)
        assert {window for window, _ in walker._cold.items(reached=True)} == set(reference.windows)

    def test_a_label_the_file_never_introduced_misses_before_the_crate_is_asked(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """A probe is served from the store or not at all: a window with a label missing from the file's table misses at the label lookup, a window of known labels that the file does not hold misses at its index slot, and neither calls the crate or marks a row reached."""
        memo = self._memo(tmp_path)
        conform.absorb_replay_memo(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker._load_memo()
        store = walker._cold
        held = next(window for window, _ in store.items())
        assert "qsNotALabel" not in store.label_ids
        assert store.probe(("qsNotALabel",) + held[1:]) is None
        edge = conform._EDGE_LABEL
        assert store.probe((edge, edge, edge, edge, edge, edge)) is None
        assert store.reached_count() == 0 and walker._settle_calls == 0
        assert store.probe(held) is not None and store.reached_count() == 1

    def test_a_walk_that_promotes_nothing_audits_the_dedupe_against_the_columns(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """`audit_dedupe` with `promote=False`: a memo key that several distinct raw case lines share is served from the store without entering `windows`, and `_drain_audit` compares each later line's settlement with the outcome the store's probe returns. The only crate calls are the audit's own extra rows, and the walk's streams still equal the unmemoized reference."""
        memo = self._memo(tmp_path)
        conform.absorb_replay_memo(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
        texts = _texts_to_depth(spec, 4)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walker = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=memo, promote=False, audit_dedupe=True
        )
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker.fresh_windows == 0 and not walker.windows
        assert walker.audit_multi_keys and walker.audit_extra_rows and walker._settle_calls
        assert all(walker._cold.probe(window) is not None for window in walker.audit_multi_keys)

    def test_a_dump_under_another_head_or_configuration_or_short_of_its_rows_is_refused(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """Each refusal writes nothing: an existing file is left unchanged, and where none existed none appears."""
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
        """The converted file behaves under `StaleMask` like one the belt wrote: under the stamp and family keys it was written with every entry loads and none is stale, a change to one family's key retires exactly the entries whose windows name that family, and another stamp reads as no file."""
        keys = {name: f"{name}@0" for name in spec.registry.families}
        memo = self._memo(tmp_path, keys=keys)
        entries = conform.absorb_replay_memo(
            kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default"
        )
        loaded = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        loaded._load_memo()
        assert loaded.memo_windows == entries and loaded.stale_windows == 0
        naming = sum(
            any(conform._label_family(label) == "qsTea" for label in window)
            for window, _outcome in loaded._cold.items()
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
        """Two left-slot seats whose cells have the same display name form one walk key. Rows keyed on either seat absorb as one row when they agree (the dump counts two, the file holds one), and a dump where they disagree is refused whole."""
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
        assert walker.memo_windows == 1 and len(walker._cold) == 1
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
    """`_DeepTokenIndex` is built from the table's raw labels but queried with the walk's marker-folded labels, so every member combination of every class-bearing row must resolve to the deep slots of the row's renamed key. The walk-equivalence sweeps cannot catch a rename applied on one side only, because both paths use the same index, so this test checks resolution against the rows directly. The configuration renames a bare (singleton-fiber) r3 under a class-token r4, the row shape the final assertion requires."""

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
    """The belt and the oracle walk the same texts per configuration, and the memo file lets the second of them settle nothing. Whichever walk settled windows the file lacked writes it, the next walk loads it lazily, and the left slot is keyed on the display name, so a walk with a glyph inventory and a walk without one share every key. A file under another stamp, or one that does not decode, costs the walk only the windows it would have settled anyway."""

    STAMP = "tables-stamp-a"

    def _texts(self, spec, max_length=3):
        return _texts_to_depth(spec, max_length)

    def _memo(self, tmp_path, stamp=STAMP, keys=None):
        return conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", stamp, dict(keys or {}))

    def test_settle_memo_files_key_each_configuration_off_the_snapshot(self, spec, tmp_path):
        inputs = oracle_cache.SettleMemoInputs(rune_digests={"qsTea": "t0"}, oracle_code="code", data="data")
        files = conform.settle_memo_files(tmp_path, spec, inputs)
        assert set(files) == set(conform.SETTLEMENT_CONFIGS)
        assert files["default"].path == tmp_path / "settle-memo-default.bin"
        assert len({memo.stamp for memo in files.values()}) == len(files)
        assert files["default"].family_keys == oracle_cache.settle_family_keys(inputs, spec)
        assert conform.settle_memo_files(tmp_path, spec, None) == {}

    def test_a_rune_edit_retires_only_the_entries_naming_it(self, spec, guard, tmp_path):
        """A walk over the edited spec that loads the file the unedited walk wrote, under keys naming ·Tea as moved, must return what a walk with no file returns, settle nothing the file still covers, and retire exactly the entries whose windows name ·Tea. The edit changes settlement, so a memo that served across it would return wrong results, which the first assertion checks."""
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
        """A memo window holds formed labels, so an edit to a ligature rune must retire the windows that carry its formed label `qsTea_qsOy` at any slot, as well as those the component clause already retires for carrying both ·Tea and ·Oy unformed. The windows with only the formed label carry no component label for that clause to catch. Under keys naming only the ligature as moved, the loaded file must retire exactly those windows and return what a walk with no file returns. A second save under the new keys must then serve everything: a save that wrote a stale entry under the current keys would leave no later run able to retire it."""
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
        """Two walks over the same file with different responsibilities. The oracle's walk reaches only the windows of its rows, so it writes nothing when it settled nothing and would carry every loaded entry forward if it did. The belt's walk reaches every window any text produces, so it prunes what it never reached, and a window an edit has made unreachable leaves the file on the next sweep instead of staying in it indefinitely."""
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

        belt_side = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        belt_side.walk_many(short_texts)
        assert not belt_side.windows and belt_side._cold.reached_count() == touched
        assert belt_side.save_memo(prune=True)
        assert belt_side.pruned_windows == total - touched

        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        third.walk_many(short_texts)
        assert third.memo_windows == touched and third._settle_calls == 0
        fourth = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert fourth.walk_many(long_texts) == first.walk_many(long_texts)
        assert fourth.memo_windows == touched and fourth.fresh_windows == total - touched

    def _decoded(self, memo, spec, outcome_of=None) -> list[tuple[conform._Window, conform._Outcome]]:
        """Every live row of the file at `memo.path` in file order, read through a separate store, with each outcome passed through `outcome_of` (a placeholder outcome around the settled item when None)."""
        store = conform._MemoStore()
        store.load(memo, spec, None, outcome_of or (lambda item: (item, "", "")))
        try:
            return list(store.items())
        finally:
            store.close()

    def _dict_of(self, walker, memo, spec) -> dict[conform._Window, conform._Outcome]:
        """The file at `memo.path` as a dict of window tuples, with every outcome through `walker._outcome` so the objects are the ones the walk shares with `windows`. The store's `items` order and `len` are checked against it."""
        return dict(self._decoded(memo, spec, walker._outcome))

    def _stream(self, path) -> bytes:
        return gzip.decompress(Path(path).read_bytes())

    def test_the_store_reads_the_file_as_a_dict_does_and_the_carry_forward_files_the_same_rows(
        self, spec, guard, tmp_path
    ):
        """The store must read the file as a dict does: `items` iterates the live rows in the dict's order with the dict's outcomes, and `len` is the dict's length. Then a whole-file save without pruning (a walk with no `write_path`, as in the `--jobs 1` oracle), over a walk that reached some rows and settled others fresh, must write the same rows in the same order as a dict-backed walk: `windows` first, then every loaded entry `windows` does not hold, in file order, copied from the mapped columns."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(self._texts(spec, 2))
        assert seed.save_memo()

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        assert list(walker._cold.items()) == list(expected.items())
        assert len(walker._cold) == len(expected)

        texts = self._texts(spec, 3)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker.fresh_windows and walker._cold.reached_count() == len(seed.windows)
        assert walker.save_memo()
        carried = tmp_path / "carried.bin"
        assert conform._write_settle_memo(
            memo,
            *conform._memo_columns(
                itertools.chain(
                    cast(dict[conform._Window, conform._Outcome], walker.windows).items(),
                    (
                        (window, outcome)
                        for window, outcome in expected.items()
                        if window not in walker.windows
                    ),
                )
            ),
            carried,
        )
        assert self._ordered(memo, spec) == self._ordered(replace(memo, path=carried), spec)

    def test_a_part_holds_the_fresh_windows_in_the_order_they_were_settled(self, spec, guard, tmp_path):
        """A part holds only the fresh windows, in the order they were settled, as a gzip stream of pickles; nothing from the loaded store goes into it."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(self._texts(spec, 2))
        assert seed.save_memo()
        part = tmp_path / "part.gz"
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=replace(memo, write_path=part))
        walker.walk_many(self._texts(spec, 3))
        assert walker.fresh_windows and walker.save_memo()
        expected = tmp_path / "expected.gz"
        assert conform._write_settle_memo_part(
            memo,
            conform._memo_blocks(
                (window, cast(conform._Outcome, walker.windows[window])) for window in walker._fresh
            ),
            expected,
        )
        assert self._stream(part) == self._stream(expected)

    def _ordered(self, memo, spec) -> list[tuple[tuple[str, ...], settle.Settled]]:
        """Every row of the file at `memo.path` in file order, as its window tuple and settled item."""
        return [(window, outcome[0]) for window, outcome in self._decoded(memo, spec)]

    def test_a_pruning_walk_that_promotes_nothing_files_what_a_promoting_one_files(
        self, spec, guard, tmp_path
    ):
        """Either way the belt writes one window-to-outcome map: a pruning walk that promoted every hit into `windows` writes `windows` in the order it reached them, and one that promoted nothing writes its fresh windows first and then the reached rows in the file's own order. The rows, the outcomes and `pruned_windows` are the same; only the order differs, and a load does not depend on order."""
        seeded = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=seeded)
        seed.walk_many(self._texts(spec, 2))
        assert seed.save_memo()
        standing = self._ordered(seeded, spec)
        texts = [text for text in self._texts(spec, 3) if len(text) != 2 or text[0] == text[1]]
        files = {}
        for promote in (True, False):
            memo = conform.SettleMemoFile(tmp_path / f"promote-{promote}.bin", self.STAMP)
            memo.path.write_bytes(seeded.path.read_bytes())
            walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=promote)
            walker.walk_many(texts)
            reached = {window for window, _ in walker._cold.items(reached=True)}
            assert walker.fresh_windows and walker.save_memo(prune=True)
            assert 0 < walker.pruned_windows < len(standing)
            files[promote] = (walker, reached, self._ordered(memo, spec))
        promoting, _promoted_reached, promoted_rows = files[True]
        columnar, reached, columnar_rows = files[False]
        assert promoting.pruned_windows == columnar.pruned_windows
        assert dict(promoted_rows) == dict(columnar_rows) and len(promoted_rows) == len(columnar_rows)
        assert [window for window, _ in promoted_rows] == list(promoting.windows)
        assert [window for window, _ in columnar_rows] == list(columnar.windows) + [
            window for window, _ in standing if window in reached
        ]

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
        memo.path.write_bytes(conform._MEMO_PREFIX.pack(24) + b"\x80\x05not a pickle stream at all")
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

    def test_a_truncated_file_is_refused_and_loads_nothing(self, spec, guard, tmp_path, capsys):
        """A mapped file is read whole or not at all: a file shorter than its header's declared layout loads no row and prints a warning, the walk settles everything, and its save replaces the truncated file with a whole one that the next walk serves from."""
        texts = self._texts(spec)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walked = first.walk_many(texts)
        assert first.save_memo()
        whole = memo.path.read_bytes()
        memo.path.write_bytes(whole[: len(whole) // 2])

        second = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert second.walk_many(texts) == walked
        assert second.memo_windows == 0 and second._cold.live == 0
        assert second.fresh_windows == len(first.windows)
        assert "[warn] settle memo:" in capsys.readouterr().err
        assert second.save_memo()
        assert memo.path.read_bytes() == whole
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        third.walk_many(texts)
        assert third.memo_windows == len(first.windows)
        assert third._settle_calls == 0

    def test_a_file_cut_to_its_header_still_stands_and_loads_nothing(self, spec, guard, tmp_path, capsys):
        """`settle_memo_standing` reads only the header: a file cut off right after its header still stands under its own stamp and not under another, a walk over it loads nothing and settles everything, and a file cut inside the header stands under no stamp."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        first.walk_many(texts)
        assert first.save_memo() and conform.settle_memo_standing(memo)
        whole = memo.path.read_bytes()
        (length,) = conform._MEMO_PREFIX.unpack_from(whole)
        header_end = conform._MEMO_PREFIX.size + length
        assert header_end < len(whole) // 4
        memo.path.write_bytes(whole[:header_end])
        assert conform.settle_memo_standing(memo)
        assert not conform.settle_memo_standing(self._memo(tmp_path, "tables-stamp-b"))
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker.walk_many(texts)
        assert walker.memo_windows == 0 and walker.fresh_windows == len(first.windows)
        assert "[warn] settle memo:" in capsys.readouterr().err
        memo.path.write_bytes(whole[: header_end - 1])
        assert not conform.settle_memo_standing(memo)

    def test_a_store_closes_under_a_live_iterator_and_the_mapping_goes_with_it(self, spec, guard, tmp_path):
        """`close` never raises: a view still held by an `items` iterator keeps the mapping open after the close, and the mapping is released with the iterator, so a partial reader in a save's cleanup path costs only the file's pages until it is released."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        first.walk_many(texts)
        assert first.save_memo()
        store = conform._MemoStore()
        store.load(memo, spec, None, lambda item: (item, "", ""))
        mapping = store._mapping
        assert mapping is not None
        rows = store.items()
        next(rows)
        store.close()
        assert store._mapping is None and not mapping.closed and len(store) == 0
        del rows
        mapping.close()
        assert mapping.closed

    def test_a_corrupt_index_retires_the_store_at_the_first_probe_that_finds_it(
        self, spec, guard, tmp_path, capsys
    ):
        """When no family key moved and no ask restriction applies, the load reads the header and tables and no column page, so a corrupt index (every slot naming one row, or naming a row past the columns) is found by a probe. The probe chain is bounded by the row count plus one and the id by the column length, and either finding retires every row with a warning. The walk then settles everything it has not already been served (the one row every slot names may serve its own window first), and its save replaces the file with a whole one."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walked = first.walk_many(texts)
        assert first.save_memo()
        whole = memo.path.read_bytes()
        header = self._header(memo)
        index_bytes = header["slots"] * 4
        for filler, problem in (
            (1, "a probe chain past its rows"),
            (header["rows"] + 5, "an id past its tables"),
        ):
            memo.path.write_bytes(whole[:-index_bytes] + struct.pack("<I", filler) * header["slots"])
            walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
            assert walker.walk_many(texts) == walked
            assert walker.memo_windows == header["rows"] and walker._cold.live == 0
            assert len(first.windows) - walker.fresh_windows <= 1 and walker._cold.reached_count() == 0
            assert f"[warn] settle memo: {memo.path} retired ({problem})" in capsys.readouterr().err
            assert walker.save_memo()
            assert memo.path.read_bytes() == whole

    def test_a_miss_through_a_full_run_of_rows_does_not_retire_a_valid_store(
        self, spec, guard, tmp_path, capsys
    ):
        """A one-row file has two slots, so a miss that hashes to the occupied slot reads the row and then the empty slot: two reads for one row. Every window over the file's labels that the file does not hold must be a plain miss, leaving the row live and printing no warning."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(self._texts(spec, 2))
        row = next(iter(cast(dict[conform._Window, conform._Outcome], seed.windows).items()))
        assert conform._write_settle_memo(memo, *conform._memo_columns([row]))

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker._load_memo()
        store = walker._cold
        assert len(store.index) == 2 and len(store) == 1
        misses = [
            cast(conform._Window, window)
            for window in itertools.product(store.labels, repeat=6)
            if window != row[0]
        ]
        assert all(store.probe(window) is None for window in misses)
        assert len(store) == 1 and store.probe(row[0]) is not None
        assert "[warn] settle memo:" not in capsys.readouterr().err

    def test_a_file_replaced_under_an_open_mapping_keeps_serving_the_mapped_rows(self, spec, guard, tmp_path):
        """The guarantee `_write_settle_memo` gives, seen from the reader: a walk that mapped the file keeps the inode it mapped after a writer replaces the file and serves every window it loaded from it with no crate call, and the next walk maps the new file. The replacement holds fewer rows (only the depth-1 windows), so a walk that read the new file through its old mapping would have settled the rest."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts)
        assert seed.save_memo()
        mapped = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        mapped._load_memo()
        assert mapped.memo_windows == len(seed.windows)
        handle = mapped._cold._handle
        assert handle is not None
        inode = os.fstat(handle.fileno()).st_ino

        smaller = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        smaller.walk_many(self._texts(spec, 1))
        assert 0 < len(smaller.windows) < len(seed.windows)
        assert conform._write_settle_memo(
            memo,
            *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], smaller.windows).items()),
        )
        assert memo.path.stat().st_ino != inode
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert mapped.walk_many(texts) == reference.walk_many(texts)
        assert mapped._settle_calls == 0 and mapped.fresh_windows == 0
        assert mapped._cold.reached_count() == len(seed.windows)
        mapped._cold.close()

        after = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert after.walk_many(texts) == reference.walk_many(texts)
        assert after.memo_windows == len(smaller.windows)
        assert after.fresh_windows == len(seed.windows) - len(smaller.windows)

    def test_the_index_a_writer_builds_answers_a_reader_in_another_interpreter(self, spec, guard, tmp_path):
        """The index hash depends on the file, not the interpreter: a file written here is probed in a subprocess under a different hash seed, and every row is found at the slot the writer put it in, which a slot computed from Python's salted or version-specific hash would not guarantee."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        first.walk_many(texts)
        assert first.save_memo()
        script = "\n".join(
            [
                "from pathlib import Path",
                "from rebuild.pipeline import conform",
                "from rebuild.pipeline.fixtures import mini_spec",
                f"memo = conform.SettleMemoFile(Path({str(memo.path)!r}), {self.STAMP!r})",
                "store = conform._MemoStore()",
                "store.load(memo, mini_spec(), None, lambda item: (item, '', ''))",
                "rows = list(store.items())",
                "hits = sum(store.probe(window) is outcome for window, outcome in rows)",
                "print(len(rows), hits, store.live)",
            ]
        )
        seed = "4242" if os.environ.get("PYTHONHASHSEED") != "4242" else "17"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        rows, hits, live = map(int, completed.stdout.split())
        assert rows == hits == live == len(first.windows) > 0

    def _rows(self, memo, spec) -> set[tuple[str, ...]]:
        """Every window the file at `memo.path` holds, as the six-tuples a walk keys on."""
        return {window for window, _outcome in self._decoded(memo, spec)}

    def _part_rows(self, part, memo, spec) -> set[tuple[str, ...]]:
        """Every window the part at `part` holds, as the six-tuples a walk keys on."""
        rows: set[tuple[str, ...]] = set()
        labels: list[str] = []
        for (new_labels, _items, columns, _values), _retired in conform._read_settle_memo(
            replace(memo, path=part), spec
        ):
            labels.extend(new_labels)
            rows.update(tuple(labels[label_id] for label_id in row) for row in zip(*columns))
        return rows

    @pytest.mark.parametrize("restricted", [False, True])
    def test_a_refusal_is_not_written_and_is_asked_again(
        self, spec, guard, tmp_path, monkeypatch, restricted
    ):
        """A refusal is memoized only for the tolerant walk that met it. The file holds outcomes only, so the next walk to reach that window asks the crate again and gets whatever the crate returns then. A walk restricted to its texts' asks memoizes the refusal the same way and raises at that key on its own walk, because a refusal is kept in `windows` and never written to the file the restriction filters."""
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
        part = tmp_path / "part.gz"
        walker = conform._SettledWindowWalk(
            spec,
            frozenset(),
            {},
            guard,
            on_error="drop",
            memo=replace(memo, write_path=part) if restricted else memo,
        )
        if restricted:
            walker.load_only_asked_by([clean, refusing_text])
        walker.prefill([clean, refusing_text])
        refused = [key for key, value in walker.windows.items() if isinstance(value, conform._RefusedWindow)]
        assert refused
        with pytest.raises(settle.SettleError):
            walker.walk(refusing_text)
        assert walker.save_memo()
        if restricted:
            assert conform.absorb_settle_memo_parts(memo, [part], spec)

        monkeypatch.setattr(kernel_exec, "settle_windows", original)
        again = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        again.walk_many([clean])
        assert again.memo_windows == len(walker.windows) - len(refused)
        assert not any(key in again.windows for key in refused)
        settled, _names = again.walk(refusing_text)
        assert [item.cell.rune for item in settled] == ["qsMay", "qsTea"]
        assert again.fresh_windows == len(refused)

    def test_the_belt_writes_the_file_the_oracle_reads(self, spec, guard, tmp_path, monkeypatch, capsys):
        """Both phases end to end, in the reverse of the cycle's order: the belt over the mini alphabet at horizon 2 with a stand-in font, then the oracle over rows the belt swept, with `kernel_exec.settle_windows` replaced by a failure. Every window the oracle needs is already in the file, and each phase's `[t]` line shows which one wrote it."""
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
            spec,
            tables,
            "default",
            frozenset(),
            {},
            oracle.compile_ledger([]),
            set(),
            None,
            None,
            guard,
            None,
            settle_memo=memo,
        )
        assert result.rows_compared == len(rows)
        oracle_line = [
            line for line in capsys.readouterr().err.splitlines() if line.startswith("[t] settle_memo")
        ]
        assert len(oracle_line) == 1 and oracle_line[0].endswith("fresh=0 pruned=0 written=no")

    def test_the_ranges_parts_absorb_into_the_file_without_dropping_each_others_windows(
        self, spec, guard, tmp_path
    ):
        """Two walks over the same existing file, each settling windows the other does not and each writing only what it settled as a part beside the file. Neither touches the shared file, and the parent's absorb produces one file that a third walk reads whole: every existing window and both parts' windows, with no crate call. A range that replaced the file whole would lose the other range's windows."""
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

    def test_a_restricted_walk_files_only_its_fresh_windows_and_the_absorb_keeps_the_standing_ones(
        self, spec, guard, tmp_path
    ):
        """As in the witness stage, a walk that loads only the rows its texts can ask cannot tell a dropped row from an unreached one, so it writes a part holding exactly the windows it settled fresh and leaves the shared file alone, and the absorb produces the existing rows plus the fresh ones. A whole-file save from such a walk would drop every row its load dropped."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        half = len(texts) // 2
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:half])
        assert seed.save_memo()
        standing = memo.path.read_bytes()

        rest = texts[half:]
        part = tmp_path / "part.gz"
        restricted = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=part)
        )
        asks = restricted.load_only_asked_by(rest)
        restricted.walk_many(rest)
        kept = {window for window in seed.windows if (window[0],) + tuple(window[2:]) in asks}
        assert 0 < len(kept) < len(seed.windows)
        assert restricted.memo_windows == len(kept)
        assert restricted.unasked_windows == len(seed.windows) - len(kept)
        fresh = set(restricted.windows) - set(seed.windows)
        assert fresh and restricted.fresh_windows == len(fresh)
        assert restricted.save_memo()
        assert memo.path.read_bytes() == standing
        assert self._part_rows(part, memo, spec) == fresh

        assert conform.absorb_settle_memo_parts(memo, [part], spec)
        assert self._rows(memo, spec) == set(seed.windows) | fresh
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert third.walk_many(texts) == reference.walk_many(texts)
        assert third._settle_calls == 0 and third.fresh_windows == 0

    def test_a_restricted_load_serves_the_rows_a_part_absorbed_under_the_standing_spellings(
        self, spec, guard, tmp_path
    ):
        """The steady state of the file the witness stage reads. A part writes its own label table, and the absorb maps each label onto the id the existing file already uses for it, so the file names each label once and a restricted load tests rows in one id space. Seeded whole, extended by one restricted walk's part, and read by a second restricted walk over the same texts, the file serves every row that walk asks, existing and absorbed alike, and the walk settles nothing."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        half = len(texts) // 2
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:half])
        assert seed.save_memo()

        rest = texts[half:]
        part = tmp_path / "part.gz"
        standing_labels = set(itertools.chain.from_iterable(seed.windows))
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=replace(memo, write_path=part))
        asks = first.load_only_asked_by(rest)
        first.walk_many(rest)
        assert first.fresh_windows and first.save_memo()
        part_labels = {
            label
            for (new_labels, *_rest), _retired in conform._read_settle_memo(replace(memo, path=part), spec)
            for label in new_labels
        }
        assert part_labels & standing_labels & set(itertools.chain.from_iterable(asks))
        assert conform.absorb_settle_memo_parts(memo, [part], spec)
        store = conform._MemoStore()
        store.load(memo, spec, None, lambda item: (item, "", ""))
        try:
            assert len(store.label_ids) == len(store.labels)
            assert part_labels | standing_labels <= set(store.labels)
        finally:
            store.close()

        rows = self._rows(memo, spec)
        kept = {window for window in rows if (window[0],) + tuple(window[2:]) in asks}
        assert len(kept) > first.fresh_windows
        again = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=tmp_path / "again.gz")
        )
        assert again.load_only_asked_by(rest) == asks
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert again.walk_many(rest) == reference.walk_many(rest)
        assert again.memo_windows == len(kept)
        assert again.unasked_windows == len(rows) - len(kept)
        assert again._settle_calls == 0 and again.fresh_windows == 0
        assert not again.save_memo()

    def _header(self, memo) -> dict:
        with open(memo.path, "rb") as handle:
            (length,) = conform._MEMO_PREFIX.unpack(handle.read(conform._MEMO_PREFIX.size))
            return pickle.loads(handle.read(length))

    def test_the_absorbed_file_names_each_spelling_once_and_answers_every_row(self, spec, guard, tmp_path):
        """The absorbed file's label table names each label once (the writer maps the part's labels onto the existing ids), so the store's id map is its label table inverted, and the index returns for every row the outcome a dict of the file holds, one row per distinct window. No existing row was retired, so the absorb extends the existing index in place: the file keeps the existing slot count, the existing rows keep their positions, and the existing columns are copied whole at the file's own typecode."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:-3])
        assert seed.save_memo()
        standing = self._header(memo)
        standing_rows = self._ordered(memo, spec)
        part = tmp_path / "part.gz"
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=replace(memo, write_path=part))
        first.load_only_asked_by(texts[-3:])
        first.walk_many(texts[-3:])
        assert first.fresh_windows and first.save_memo()
        assert standing["slots"] >= 2 * (standing["rows"] + first.fresh_windows), "no room to extend in place"
        assert conform.absorb_settle_memo_parts(memo, [part], spec)
        absorbed = self._header(memo)
        assert (
            absorbed["slots"] == standing["slots"]
            and absorbed["rows"] == standing["rows"] + first.fresh_windows
        )
        assert self._ordered(memo, spec)[: len(standing_rows)] == standing_rows
        assert absorbed["typecode"] == standing["typecode"] == "H"

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        store = walker._cold
        assert len(store.label_ids) == len(store.labels)
        assert all(store.labels[label_id] == label for label, label_id in store.label_ids.items())
        assert len(store) == len(expected) == len(store.values)
        assert list(store.items()) == list(expected.items())
        for window, outcome in expected.items():
            assert store.probe(window) is outcome
        assert store.reached_count() == len(expected)

    def test_a_window_filed_twice_lands_as_one_row_with_the_later_outcome(self, spec, guard, tmp_path):
        """The writer's fold, on a memo that lists one window twice with two outcomes: the file holds one row at the first entry's position with the later entry's outcome, as a dict of the entries would. So `len`, `items` order and every probe match the dict, and a pruning save counts and writes only the distinct windows."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(self._texts(spec, 2))
        rows = list(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
        outcomes = list(dict.fromkeys(outcome for _, outcome in rows))
        assert len(outcomes) > 1
        repeated = [
            (window, next(other for other in outcomes if other is not outcome))
            for window, outcome in rows[::3]
        ]
        assert conform._write_settle_memo(memo, *conform._memo_columns(itertools.chain(rows, repeated)))

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        store = walker._cold
        assert len(store.values) == len(store) == len(expected) == len(rows)
        assert list(store.items()) == list(expected.items())
        assert dict(rows) != expected
        for window, outcome in repeated:
            assert expected[window] == outcome and store.probe(window) is expected[window]
        assert store.reached_count() == len(repeated)
        assert walker.save_memo(prune=True) and walker.pruned_windows == len(rows) - len(repeated)
        assert self._ordered(memo, spec) == [(window, outcome[0]) for window, outcome in repeated]

    def test_two_parts_filing_one_window_absorb_into_one_row(self, spec, guard, tmp_path):
        """The production case of a repeated window: two row ranges that both settle a window both write it, and the absorb folds the pair onto one row, so the file's row count equals the dict's and a walk over the file reaches every window once."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        third = len(texts) // 3
        piles = (texts[: 2 * third], texts[third:])
        parts = [tmp_path / f"part-{index}.gz" for index in range(2)]
        for pile, part in zip(piles, parts):
            walker = conform._SettledWindowWalk(
                spec, frozenset(), {}, guard, memo=replace(memo, write_path=part)
            )
            walker.load_only_asked_by(pile)
            walker.walk_many(pile)
            assert walker.fresh_windows and walker.save_memo()
        filed = [self._part_rows(part, memo, spec) for part in parts]
        assert filed[0] & filed[1]
        assert conform.absorb_settle_memo_parts(memo, parts, spec)

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        store = walker._cold
        assert len(store.values) == len(store) == len(expected) == len(filed[0] | filed[1])
        assert list(store.items()) == list(expected.items())
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker.fresh_windows == 0 and store.reached_count() == len(expected)

    def test_a_range_part_filed_before_the_witness_fold_absorbs_onto_the_rows_that_fold_added(
        self, spec, guard, tmp_path, monkeypatch
    ):
        """The oracle runs beside the witness stage, so a row range can map the file before the witness stage's absorb takes effect, settle windows that absorb then adds to the existing rows, and have its part absorbed after it. No existing row was retired, so the writer extends the existing index with the part's rows instead of rebuilding it. Each window the file already holds maps onto its existing row and the rest are appended, so every existing row keeps its place, the file holds one row per window and reads as a dict of it does, and a walk over every text settles nothing."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:-6])
        assert seed.save_memo()

        ranged_part, witness_part = tmp_path / "range.gz", tmp_path / "witness.gz"
        ranged = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=ranged_part)
        )
        ranged.walk_many(texts[-4:])
        assert ranged.fresh_windows and ranged.save_memo()
        witness_walk = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=witness_part)
        )
        witness_walk.load_only_asked_by(texts[-6:-2])
        witness_walk.walk_many(texts[-6:-2])
        assert witness_walk.fresh_windows and witness_walk.save_memo()
        filed = self._part_rows(ranged_part, memo, spec)
        folded = self._part_rows(witness_part, memo, spec)
        assert filed & folded and filed - folded

        assert conform.absorb_settle_memo_parts(memo, [witness_part], spec)
        standing = self._header(memo)
        standing_rows = self._ordered(memo, spec)
        assert folded <= {window for window, _ in standing_rows}
        extended: list[tuple[bool, int]] = []
        indexing = conform._memo_index

        def recording(columns, values, index=None, indexed=0):
            extended.append((index is not None and len(index) >= 2 * len(values), indexed))
            return indexing(columns, values, index, indexed)

        monkeypatch.setattr(conform, "_memo_index", recording)
        assert conform.absorb_settle_memo_parts(memo, [ranged_part], spec)
        assert extended == [(True, standing["rows"])]
        absorbed = self._header(memo)
        assert absorbed["slots"] == standing["slots"]
        assert absorbed["rows"] == standing["rows"] + len(filed - folded)
        assert self._ordered(memo, spec)[: len(standing_rows)] == standing_rows

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        store = walker._cold
        assert len(store.values) == len(store) == len(expected) == absorbed["rows"]
        assert list(store.items()) == list(expected.items())
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker._settle_calls == 0 and walker.fresh_windows == 0

    def test_a_restriction_needs_a_part_and_never_prunes(self, spec, guard, tmp_path):
        """Two checks that tie restricted loading to part writing: a walk whose memo would replace the shared file whole may not restrict its load, and a restricted walk may not prune, since after a restricted load a dropped row and an unreached window look the same."""
        texts = self._texts(spec, 1)
        memo = self._memo(tmp_path)
        whole = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        with pytest.raises(AssertionError):
            whole.load_only_asked_by(texts)
        restricted = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, write_path=tmp_path / "part.gz")
        )
        restricted.load_only_asked_by(texts)
        with pytest.raises(AssertionError):
            restricted.save_memo(prune=True)

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
        """A part written under keys naming ·Tea as moved is absorbed into a file written under the old keys. The result must carry the new keys without the entries those keys retire. Otherwise a stale settlement would sit under a header whose keys say it is current, and no later run could retire it."""
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
