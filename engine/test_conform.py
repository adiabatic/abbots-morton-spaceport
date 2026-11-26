"""Tests for conform.py: label normalization, the raw GSUB replay, the isolated overlay, the conformance sweep's bookkeeping, and the memoized settle walk and its memo file, checked against settling the same texts without a memo. The conformance sweep at its full maximum length runs in run_build against the compiled font. Settlement comes from the Rust crate, so these tests need a built kernel: the formation-guard sweep and the settle walk both call it."""

import itertools
import json
import os
import pickle
import shutil
import struct
import subprocess
import sys
import threading
import zlib
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from engine.pipeline import (
    conform,
    kernel_exec,
    labels,
    row_delta,
    run_build,
    run_build_gates,
    settle,
    settle_memo_keys,
    witness,
)
from engine.pipeline.fixtures import mini_spec
from engine.pipeline.model import CellId

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
            junction = None

        assert conform.settled_names(spec, [WithCell()]) == ["qsMay.en-y0.ex-y5.en-ext-1"]

    def test_settled_names_uses_supplied_inventory(self, spec):
        cell = CellId("qsMay", "loop", None, "x-height", ())

        class WithCell:
            cell: CellId

        item = WithCell()
        item.cell = cell
        assert conform.settled_names(spec, [item], {cell: "qsMay"}) == ["qsMay"]

    def test_isolated_overlay_labels_render_one_copy_per_raw_token(self, spec):
        """Under the overlay each component of a ligature renders as its own copy, because the ss10 input substitution replaces every letter with its copy before formation. A boundary token renders as its own glyph."""
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
    """`OVERLAY_CONFIGS` is exactly the acceptance configurations whose features activate an `overlay: isolated` taste set, `COSMETIC_CONFIGS` exactly the rest that hold an `overlay: cosmetic` set, and `SETTLEMENT_CONFIGS` the others. For an overlay configuration the conformance sweep takes everything from the registry and the font's `hmtx`, and never calls the crate; a cosmetic configuration settles as its settlement configuration does. The live registry knows every acceptance configuration's features."""

    def test_the_rosters_partition_the_acceptance_set_by_the_registry(self, spec):
        from engine.pipeline.model import cosmetic_features, isolated_overlay_active
        from engine.pipeline.spec_load import load_default_spec

        assert (
            conform.SETTLEMENT_CONFIGS + conform.OVERLAY_CONFIGS + conform.COSMETIC_CONFIGS
            == conform.ACCEPTANCE_CONFIGS
        )
        assert not set(conform.SETTLEMENT_CONFIGS) & set(conform.OVERLAY_CONFIGS)
        for registry_spec in (spec, load_default_spec()):
            overlays = tuple(
                config
                for config in conform.ACCEPTANCE_CONFIGS
                if isolated_overlay_active(registry_spec, conform.features_for_config(config))
            )
            assert overlays == conform.OVERLAY_CONFIGS
        live = load_default_spec()
        for config in conform.ACCEPTANCE_CONFIGS:
            assert conform.features_for_config(config) <= set(live.registry.features), config
        cosmetic = tuple(
            config
            for config in conform.ACCEPTANCE_CONFIGS
            if conform.features_for_config(config) & cosmetic_features(live)
            and not isolated_overlay_active(live, conform.features_for_config(config))
        )
        assert cosmetic == conform.COSMETIC_CONFIGS
        inputs = settle_memo_keys.SettleMemoInputs(rune_digests={}, code="code", data="data")
        assert set(conform.settle_memo_files(Path("build"), spec, inputs)) == set(conform.SETTLEMENT_CONFIGS)

    def test_the_overlay_case_sweeps_two_letters_and_never_reaches_the_crate(self, spec, monkeypatch):
        """At any conformance-sweep maximum length, the overlay case shapes every text of one or two alphabet symbols and nothing longer, and it never forms, settles, memoizes or calls the crate."""

        def unreachable(*args, **kwargs):
            raise AssertionError("the overlay case reached the crate")

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
            conform.SWEEP_MAX_LENGTH + 3,
            None,
        )
        alphabet = len(conform.spec_alphabet(spec))
        assert result.sequences == alphabet + alphabet**2
        assert result.shaping_runs == result.sequences == len(shaper.shaped)
        assert all(len(text) <= conform.OVERLAY_MAX_LENGTH for text in shaper.shaped)
        assert set(result.divergences_by_kind) == {"length"}


class TestRawLabels:
    def test_formation_folds_the_ligature(self, spec, guard):
        assert conform.raw_labels(spec, TEA + OY, frozenset(), guard) == ["qsTea_qsOy"]

    def test_zwnj_locks_entry_bearing_followers(self, spec, guard):
        labels = conform.raw_labels(spec, ZWNJ + TEA + IT, frozenset(), guard)
        assert labels == ["uni200C", "qsTea.noentry", "qsIt"]

    def test_marker_renaming_applies_under_features(self, spec, guard):
        assert conform.raw_labels(spec, MAY + TEA, frozenset({"ss03"}), guard) == ["qsMay", "qsTea.ss03"]

    def test_marker_and_lock_compose(self, spec, guard):
        labels = conform.raw_labels(spec, ZWNJ + TEA, frozenset({"ss02", "ss03"}), guard)
        assert labels == ["uni200C", "qsTea.ss02_ss03.noentry"]

    def test_namer_dot_does_not_lock(self, spec, guard):
        assert conform.raw_labels(spec, DOT + IT, frozenset(), guard) == ["periodcentered", "qsIt"]


class TestConformanceMerge:
    def _result(
        self,
        config: str,
        sequences: int = 100,
        shaping_runs: int = 100,
        divergences: Sequence[conform.Divergence] = (),
        notes: Sequence[str] = (),
        modes: Sequence[str] = (),
        alphabet: Sequence[str] = (),
    ) -> conform.ConformanceConfigResult:
        result = conform.ConformanceConfigResult(
            config=config,
            sequences=sequences,
            shaping_runs=shaping_runs,
            notes=list(notes),
            modes=list(modes),
        )
        tally = conform.DivergenceTally(result, alphabet)
        for divergence in divergences:
            tally.append(divergence)
        return result

    def test_sequences_come_from_the_first_result_and_shaping_runs_sum(self):
        merged = conform.merge_conformance_results(
            Path("Senior-Regular.otf"),
            [self._result("default", shaping_runs=120), self._result("ss02", shaping_runs=110)],
        )
        assert merged.sequences == 100
        assert merged.shaping_runs == 230
        assert merged.passed is True

    def test_divergences_and_notes_concatenate_in_caller_order(self):
        divergence = conform.Divergence(
            text="", config="ss02", position=0, expected="qsPea", got="qsPea.alt", kind="name"
        )
        merged = conform.merge_conformance_results(
            Path("Senior-Regular.otf"),
            [
                self._result("default", notes=["default: first"]),
                self._result(
                    "ss02", notes=["ss02: second"], divergences=[divergence], alphabet=tuple(divergence.text)
                ),
            ],
        )
        assert merged.notes == ["default: first", "ss02: second"]
        assert [exemplar.divergence for exemplar in merged.exemplars] == [divergence]
        assert merged.divergences_by_kind == {"name": 1}
        assert merged.passed is False

    def test_more_divergences_than_the_limit_keep_the_concatenations_first_exemplars(self):
        """Each configuration's tally keeps only its own first `EXEMPLAR_LIMIT` divergences, and the merge concatenates those. With more divergences than the limit in one configuration and across all of them, the merged exemplars must be the first `EXEMPLAR_LIMIT` of every configuration's divergences concatenated in caller order, each tagged with its text's length and product-order rank, and the counts must cover every divergence, including the ones no exemplar holds."""
        alphabet = ("a", "b", "c")
        texts = [
            "".join(combo) for length in (1, 2, 3) for combo in itertools.product(alphabet, repeat=length)
        ]
        per_config = {
            config: [
                conform.Divergence(text, config, 0, "want", "got", ("name", "gap", "length")[index % 3])
                for index, text in enumerate(texts[:count])
            ]
            for config, count in (("default", 7), ("ss03", conform.EXEMPLAR_LIMIT + 5), ("ss04", 4))
        }
        concatenated = [divergence for divergences in per_config.values() for divergence in divergences]
        assert len(concatenated) > conform.EXEMPLAR_LIMIT
        merged = conform.merge_conformance_results(
            Path("Senior-Regular.otf"),
            [
                self._result(config, divergences=divergences, alphabet=alphabet)
                for config, divergences in per_config.items()
            ],
        )
        assert [exemplar.divergence for exemplar in merged.exemplars] == concatenated[
            : conform.EXEMPLAR_LIMIT
        ]
        ranks = {
            "".join(combo): rank
            for length in (1, 2, 3)
            for rank, combo in enumerate(itertools.product(alphabet, repeat=length))
        }
        for exemplar in merged.exemplars:
            text = exemplar.divergence.text
            assert (exemplar.length, exemplar.rank) == (len(text), ranks[text])
        by_kind: dict[str, int] = {}
        for divergence in concatenated:
            by_kind[divergence.kind] = by_kind.get(divergence.kind, 0) + 1
        assert list(merged.divergences_by_kind.items()) == list(by_kind.items())
        assert merged.divergence_count == len(concatenated)
        assert merged.passed is False

    def test_first_seen_orders_kinds_as_one_walk_whatever_the_split(self):
        """A tally records where each kind first appears. Results that split a configuration's texts by last symbol, merged by each kind's earliest `first_seen`, must give the kind order of one tally over every text in walk order, even when the split's own order (here the last symbol) disagrees with it."""
        alphabet = ("a", "b", "c")
        kinds_of = {"b": ("gap", "length"), "ba": ("name",), "cc": ("name",)}
        texts = ["".join(combo) for length in (1, 2) for combo in itertools.product(alphabet, repeat=length)]

        def tallied(selected: list[str]) -> conform.ConformanceConfigResult:
            divergences = [
                conform.Divergence(text, "default", 0, "want", "got", kind)
                for text in selected
                for kind in kinds_of.get(text, ())
            ]
            return self._result("default", divergences=divergences, alphabet=alphabet)

        whole = tallied(texts)
        assert whole.first_seen == {"gap": (1, 1, 0), "length": (1, 1, 1), "name": (2, 3, 0)}
        units = [tallied([text for text in texts if text[-1] == last]) for last in alphabet]
        assert list(units[0].divergences_by_kind) == ["name"]
        earliest: dict[str, tuple[int, int, int]] = {}
        for unit in units:
            for kind, seen in unit.first_seen.items():
                earliest[kind] = min(earliest.get(kind, seen), seen)
        assert sorted(earliest, key=earliest.__getitem__) == list(whole.divergences_by_kind)

    def test_modes_union_sorted_after_the_config_notes(self):
        merged = conform.merge_conformance_results(
            Path("Senior-Regular.otf"),
            [
                self._result("default", notes=["default: note"], modes=["mode-b"]),
                self._result("ss02", modes=["mode-a", "mode-b"]),
            ],
        )
        assert merged.notes == ["default: note", "mode-a", "mode-b"]
        assert merged.passed is True

    def test_empty_results_merge_to_an_empty_pass(self):
        merged = conform.merge_conformance_results(Path("Senior-Regular.otf"), [])
        assert merged.sequences == 0
        assert merged.shaping_runs == 0
        assert merged.passed is True

    def test_per_configuration_workers_merged_in_any_order_write_the_serial_report(
        self, spec, guard, tmp_path
    ):
        """The pooled conformance sweep against the serial one, with no pool: each configuration's worker runs as a pool would run it, the results are taken in reverse order, reordered by acceptance configuration and merged, and the merged report must write the same bytes as `run_conformance`. A worker that built its shaper, alphabet or splitters differently from the serial path, or a merge in completion order, would fail here. Both paths run with no glyph mapping and the worker receives the guard, so neither the anchor check nor a worker's own guard sweep is tested."""
        font = MINI / "Senior-Regular.otf"
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


def _tea_prefers_half_before_may(spec):
    """A rune edit that changes settlement: ·Tea gets an absolute preference for its half stance before ·May, which changes what the ·Tea·May windows settle to and nothing else."""
    import dataclasses

    from engine.pipeline import model

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

    from engine.pipeline import model

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


def _without_the_tea_oy_ligature(spec):
    """`spec` with the ·Tea+·Oy ligature gone, as deleting its rune file and its registry entry leaves it: no rune, no registry family and no predicate-class membership, so ·Tea followed by ·Oy settles as two letters and no cell of the ligature has a display name. ·May's condition that names the ligature among its left families names a family nothing forms."""
    import dataclasses

    name = "qsTea_qsOy"
    registry = dataclasses.replace(
        spec.registry,
        families={family: info for family, info in spec.registry.families.items() if family != name},
        predicate_classes={
            label: members - {name} for label, members in spec.registry.predicate_classes.items()
        },
    )
    runes = {rune_name: rune for rune_name, rune in spec.runes.items() if rune_name != name}
    return dataclasses.replace(spec, runes=runes, registry=registry)


class TestConformSummary:
    def test_a_conformance_summary_stays_in_its_established_shape(self, tmp_path):
        """The summary keeps its fields, and a second sweep whose summary comes out the same, as gate:conform's or either deep sweep's, leaves the file in place, keeping its inode and its modification time, here set long ago so that any write would move it."""
        import json
        import os

        report = conform.merge_conformance_results(Path("Senior-Regular.otf"), [])
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
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        inode = path.stat().st_ino
        report.write(path)
        assert (path.stat().st_ino, path.stat().st_mtime_ns) == (inode, 1_000_000_000)


class _SilentShaper:
    """A stand-in Shaper that records every text it is asked to shape and returns no glyphs. The conformance sweep then records one length divergence per text, and the split-buffer check finds no splitter slot. Tests read `shaped`."""

    def __init__(self):
        self.shaped: list[str] = []

    def shape(self, text: str, features: frozenset[str], kern: bool = True) -> list[dict]:
        self.shaped.append(text)
        return []

    def positions(self, text: str, features: frozenset[str], kern: bool = True) -> list[tuple[int, int, int]]:
        self.shaped.append(text)
        return []

    def outline_signature(self, glyph_name: str) -> tuple:
        return ()


class TestSweepEconomics:
    """What the conformance sweep does over a short maximum length with a stand-in font: every text of every length up to the maximum length is shaped once, and the split-buffer check runs on exactly the texts that contain a splitter."""

    MAX_LENGTH = 2

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
            self.MAX_LENGTH,
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
        assert all(len(text) <= self.MAX_LENGTH for text in shaper.shaped)

    def test_the_split_buffer_check_runs_on_the_texts_that_carry_a_splitter(self, spec, guard, monkeypatch):
        """The conformance sweep runs the split-buffer check on every text that contains a splitter, comparing it with its segments shaped separately, and on no other text, since a text without a splitter is its own single segment. The ZWNJ slot's zero advance and empty outline are checked by read-back's boundary-glyphs stage, not by the conformance sweep."""
        split_checked: list[str] = []
        monkeypatch.setattr(
            conform, "check_split_buffer", lambda text, *args, **kwargs: split_checked.append(text)
        )
        _result, shaper = self._run(spec, guard)
        splitters = conform.splitting_boundary_chars(spec)
        assert set(split_checked) == {text for text in shaper.shaped if set(text) & splitters}
        assert split_checked

    def test_the_units_of_every_last_symbol_partition_the_texts_and_merge_to_the_whole(
        self, spec, guard, monkeypatch
    ):
        """With `last`, the sweep runs one unit of its configuration. Over a toy alphabet of a boundary and three letters, two of which form a ligature, at maximum length 3, each symbol's unit shapes exactly the texts that end in it, in the whole sweep's order, so the units between them shape every text once, and each text's settled stream in its unit is the one the whole sweep settles for it, the length-3 texts included. Every text diverges against the stand-in font, and `merge_unit_results` over the units, taken in reverse, must give the whole sweep's result: its counts, its kinds in order, and its exemplars."""
        monkeypatch.setattr(conform, "check_split_buffer", lambda *args, **kwargs: None)
        alphabet = (" ", TEA, IT, OY)
        check_glyph_names = conform.check_glyph_names
        settled: list[tuple[str, tuple[str, ...]]] = []

        def recording_check_glyph_names(text, config, shaped, expected, divergences, modes) -> None:
            settled.append((text, tuple(expected)))
            check_glyph_names(text, config, shaped, expected, divergences, modes)

        monkeypatch.setattr(conform, "check_glyph_names", recording_check_glyph_names)

        def run(
            last: str | None,
        ) -> tuple[conform.ConformanceConfigResult, list[str], list[tuple[str, tuple[str, ...]]]]:
            settled.clear()
            shaper = _SilentShaper()
            result = conform._conformance_config(
                shaper,  # pyright: ignore[reportArgumentType]
                spec,
                "default",
                alphabet,
                conform.splitting_boundary_chars(spec),
                {},
                None,
                3,
                guard,
                last=last,
            )
            return result, shaper.shaped, list(settled)

        whole, shaped, whole_settled = run(None)
        texts = [
            "".join(combo) for length in (1, 2, 3) for combo in itertools.product(alphabet, repeat=length)
        ]
        assert shaped == texts
        assert [text for text, _ in whole_settled] == texts
        assert any(len(text) == 3 and names for text, names in whole_settled)
        units = [run(symbol) for symbol in alphabet]
        for symbol, (unit, unit_shaped, unit_settled) in zip(alphabet, units):
            assert unit_shaped == [text for text in texts if text[-1] == symbol]
            assert unit.sequences == unit.shaping_runs == len(unit_shaped)
            assert unit_settled == [(text, names) for text, names in whole_settled if text[-1] == symbol]
        merged = conform.merge_unit_results("default", [unit for unit, _, _ in reversed(units)])
        assert (merged.sequences, merged.shaping_runs) == (whole.sequences, whole.shaping_runs)
        assert whole.divergences_by_kind and len(whole.exemplars) == conform.EXEMPLAR_LIMIT
        assert list(merged.divergences_by_kind.items()) == list(whole.divergences_by_kind.items())
        assert merged.first_seen == whole.first_seen
        assert merged.exemplars == whole.exemplars

    def test_a_unit_with_trigger_letters_shapes_only_the_texts_that_contain_one(
        self, spec, guard, monkeypatch
    ):
        """With `triggers`, a unit shapes only the texts that end in its symbol and contain a trigger letter, in the whole sweep's order, and settles each as the whole sweep does; the unit of a trigger letter shapes every text that ends in it."""
        monkeypatch.setattr(conform, "check_split_buffer", lambda *args, **kwargs: None)
        alphabet = (" ", TEA, IT, OY)
        settled: list[tuple[str, tuple[str, ...]]] = []
        check_glyph_names = conform.check_glyph_names

        def recording_check_glyph_names(text, config, shaped, expected, divergences, modes) -> None:
            settled.append((text, tuple(expected)))
            check_glyph_names(text, config, shaped, expected, divergences, modes)

        monkeypatch.setattr(conform, "check_glyph_names", recording_check_glyph_names)

        def run(last: str | None, triggers: frozenset[str]) -> tuple[int, list[tuple[str, tuple[str, ...]]]]:
            settled.clear()
            result = conform._conformance_config(
                _SilentShaper(),  # pyright: ignore[reportArgumentType]
                spec,
                "ss03",
                alphabet,
                conform.splitting_boundary_chars(spec),
                {},
                None,
                3,
                guard,
                last=last,
                triggers=triggers,
            )
            return result.sequences, list(settled)

        _, whole = run(None, frozenset())
        for symbol in alphabet:
            sequences, unit = run(symbol, frozenset({TEA}))
            assert unit == [(text, names) for text, names in whole if text[-1] == symbol and TEA in text]
            assert sequences == len(unit) and unit
        assert len(run(TEA, frozenset({TEA}))[1]) == 1 + 4 + 16


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

    @pytest.mark.parametrize(
        "features",
        [frozenset(), frozenset({"ss03"}), frozenset({"ss02", "ss03"})],
        ids=["default", "ss03", "ss02+ss03"],
    )
    def test_a_horizon_walk_matches_the_plain_walk_and_keeps_no_pinned_window(self, spec, features):
        """A walk with `horizon` settles every text to the same (settled, names) as the plain walk and settles the same windows fresh, and its `windows` holds exactly the plain walk's windows less the pinned ones. The pinned set is computed here from `witness._matched_windows`, the replay's keys, by the definition in `_SettledWindowWalk`'s docstring."""
        import itertools

        max_length = 4
        guard = kernel_exec.guard_sweep(spec)
        plain = conform._SettledWindowWalk(spec, features, {}, guard)
        bounded = conform._SettledWindowWalk(spec, features, {}, guard, horizon=max_length)
        pinned: set[tuple[str, ...]] = set()
        for length in range(1, max_length + 1):
            stream = itertools.product(conform.spec_alphabet(spec), repeat=length)
            while True:
                texts = ["".join(combo) for combo in itertools.islice(stream, self.SWEEP_CHUNK)]
                if not texts:
                    break
                walked = plain.walk_many(texts)
                assert bounded.walk_many(texts) == walked
                for text, (_settled, names) in zip(texts, walked):
                    labels = conform.raw_labels(spec, text, features, guard)
                    last = len(labels) - 1
                    at_horizon = len(text) == max_length
                    for index, window, _matched in witness._matched_windows(
                        spec, text, features, guard, names, {}, None
                    ):
                        if index > 1 or (index == 1 and not at_horizon) or last > index + 4:
                            continue
                        if set(labels[index + 1 : last]) & conform._WINDOW_BOUNDARIES:
                            continue
                        if at_horizon or (
                            last < index + 4 and window[2 + last - index] == conform._EDGE_LABEL
                        ):
                            pinned.add(window)
        assert pinned
        assert bounded.fresh_windows == plain.fresh_windows
        assert set(bounded.windows) == set(plain.windows) - pinned
        assert not bounded._pinned

    def test_a_walk_with_a_window_ceiling_settles_the_same_and_never_holds_more(self, spec, monkeypatch):
        """A horizon walk with `max_windows` settles every text to the same (settled, names) as the plain walk, never holds more windows than the ceiling, even with a ceiling below one wave's batch, and settles again the windows it meets after a release, so it settles more fresh than the uncapped walk."""
        import itertools

        max_length, ceiling = 4, 37
        guard = kernel_exec.guard_sweep(spec)
        plain = conform._SettledWindowWalk(spec, frozenset(), {}, guard, horizon=max_length)
        capped = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, horizon=max_length, max_windows=ceiling
        )
        original = capped._record

        def record(window, item, state):
            original(window, item, state)
            assert len(capped.windows) <= ceiling

        monkeypatch.setattr(capped, "_record", record)
        for length in range(1, max_length + 1):
            stream = itertools.product(conform.spec_alphabet(spec), repeat=length)
            while True:
                texts = ["".join(combo) for combo in itertools.islice(stream, self.SWEEP_CHUNK)]
                if not texts:
                    break
                assert capped.walk_many(texts) == plain.walk_many(texts)
        assert len(plain.windows) > ceiling
        assert capped.releases > 0
        assert capped.fresh_windows > plain.fresh_windows

    def test_deep_slot_keys_replay_the_real_chains(self):
        """The mini spec has no depth-3 or depth-4 prefers, so this test uses the real spec: the chain letters of its deep inputs plus a space, swept to length 5 so both right3 and right4 are reachable. The walk keys its deep slots on raw labels, which is finer than the table's own grain, so some memo key must have its third slot open, not `#NA`."""
        import warnings

        from engine.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            real_spec = load_default_spec()
        alphabet = tuple(chr(cp) for cp in (0x0020, 0xE652, 0xE653, 0xE665, 0xE666, 0xE679, 0xE67A))
        walker, _replayed = self._sweep(real_spec, frozenset(), alphabet, 5)
        assert any(key[4] != "#NA" for key in walker.windows), "no window opened its third slot"

    def test_prospect_live_slots_agree_between_walk_and_replay(self, monkeypatch):
        """Under the simulated-prospect default, the windows of `fixtures.prospect_spec` where A precedes B and C have a live third slot that the table enumerates. The memoized walk and the unmemoized replay must agree on it, and the table's deep-token index supplies the class map for the replay's rule matching."""
        from engine.pipeline import fixtures
        from engine.pipeline.model import raw_rename_map
        from engine.pipeline.kernel_exec import build_tables

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
        """Class tokens at depth 4 with real rules and a real index: the mini spec plus a reach-3 chain on ·Tea, built under the default modes, creates an r4 class at the ·Tea·May·May·May windows. `witness._matched_windows`, which the witness stage uses, must resolve the labels to that class token and match rules against it, and the walk must still settle those texts correctly."""
        import dataclasses

        from engine.pipeline import fixtures, model
        from engine.pipeline.model import raw_rename_map
        from engine.pipeline.kernel_exec import build_tables

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
            results = original(asked_spec, cases, features, **rest)
            hits = [
                index
                for index, case in enumerate(cases)
                if case.split("\t")[kernel_exec.CASE_INPUT_FIELD] == refused
            ]
            if not hits:
                return results
            if rest.get("on_error") != "drop":
                raise settle.SettleError(f"{refused}: the injected refusal", "E-INCOMPARABLE")
            for index in hits:
                results[index] = None
            return results

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

        from engine.pipeline.spec_load import load_default_spec

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


def _fresh_blocks(fresh: conform.FreshMemo) -> list[conform._SettleMemoBlock]:
    """A walk's fresh windows decoded block by block into what `conform._memo_blocks` yields: the labels and records each block introduces, its six key columns and its value column."""
    blocks: list[conform._SettleMemoBlock] = []
    for at in range(0, len(fresh.blocks), 2):
        new_labels, new_items, count = pickle.loads(zlib.decompress(fresh.blocks[at]))
        flat = array("I")
        flat.frombytes(zlib.decompress(fresh.blocks[at + 1]))
        blocks.append(
            (
                new_labels,
                new_items,
                [flat[slot * count : (slot + 1) * count] for slot in range(6)],
                flat[6 * count :],
            )
        )
    return blocks


def _fresh_rows(fresh: conform.FreshMemo) -> set[tuple[str, ...]]:
    """Every window a walk's fresh windows hold, as the six-tuples a walk keys on."""
    rows: set[tuple[str, ...]] = set()
    labels: list[str] = []
    for new_labels, _items, columns, _values in _fresh_blocks(fresh):
        labels.extend(new_labels)
        rows.update(tuple(labels[label_id] for label_id in row) for row in zip(*columns))
    return rows


def _carry_and_append_absorb(
    memo: conform.SettleMemoFile,
    spec,
    parts: Sequence[conform.FreshMemo],
    path: Path,
    sweep: conform.SweepPrune | None = None,
    distinct: int | None = None,
) -> bool:
    """The carry-and-append absorb `conform.absorb_settle_memo`'s bytes are proven against, kept here as that algorithm, over the same blocks: decode every part's blocks, load the standing file unrestricted, mark the rows the sweep keeps, carry the kept rows into new columns (`_MemoStore.carry_into`), append each part's blocks with their ids shifted past the labels and records before them, and write it all through `_write_settle_memo` to `path`, passing the standing index when the standing memo is one part, no row was dropped and none is dead, and `distinct`, the count of distinct windows, when given. With no part and nothing dropped it writes nothing and returns False. Over a standing single file this is the file the whole-file absorb wrote before the segment format, and whenever the parts repeat no window across parts its bytes are what the materialized form of the memo an absorb leaves must equal; parts that do repeat one merge in the absorb's segment, so the materialized form is sized from the distinct windows, the reference written with `distinct`."""
    read = [_fresh_blocks(part) for part in parts]
    introduced_labels = sum(len(block[0]) for blocks in read for block in blocks)
    introduced_outcomes = sum(len(block[1]) for blocks in read for block in blocks)
    store = conform._MemoStore()
    store.load(memo, spec, None, lambda item: (item, "", ""))
    dropped = 0
    if sweep is not None and sweep.prune:
        flags = zlib.decompress(sweep.reached)
        if store.identity() == sweep.identity and len(flags) == len(store.reached):
            if sweep.discard:
                dropped = len(store)
            else:
                store.reached[:] = flags
                dropped = len(store) - store.reached_count()
    if not parts and not dropped:
        store.close()
        return False
    labels: list[str] = []
    outcomes: list = []
    columns = [array(conform._memo_typecode(len(store.labels) + introduced_labels)) for _ in range(6)]
    values = array(conform._memo_typecode(len(store.outcomes) + introduced_outcomes))
    store.carry_into(labels, outcomes, columns, values, reached=True if dropped else None)
    carried = len(values)
    standing = (
        (store.index_copy(), carried, len(labels), len(outcomes))
        if not dropped and store.dead.count(1) == 0 and len(store.parts) <= 1
        else None
    )
    store.close()
    for blocks in read:
        base_labels, base_outcomes = len(labels), len(outcomes)
        for new_labels, new_items, block_columns, block_values in blocks:
            labels.extend(new_labels)
            outcomes.extend(new_items)
            for column, block in zip(columns, block_columns):
                column.extend(map(base_labels.__add__, block))
            values.extend(map(base_outcomes.__add__, block_values))
    return conform._write_settle_memo(
        memo, labels, outcomes, columns, values, path=path, standing=standing, distinct=distinct
    )


def _memo_files(memo: conform.SettleMemoFile) -> dict[str, tuple[bytes, int]]:
    """Every file of `memo`'s output in its directory, its head, its parts and a single file, by name, with its bytes and modification time."""
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(memo.path.parent.glob(f"{memo.path.name}*"))
        if path.is_file()
    }


def _materialized(memo: conform.SettleMemoFile, spec, path: Path) -> bytes:
    """`memo`'s materialized form (`conform.materialize_settle_memo`), written to the single file at `path` and returned."""
    assert conform.materialize_settle_memo(
        memo, spec, lambda finished: conform._put_settle_memo(finished, path)
    )
    return path.read_bytes()


def _texts_to_depth(spec, max_length=3):
    """Every text of the fixture alphabet up to `max_length` characters, the text set the memo tests walk."""
    alphabet = conform.spec_alphabet(spec)
    return [
        "".join(combo)
        for length in range(1, max_length + 1)
        for combo in itertools.product(alphabet, repeat=length)
    ]


class TestCrateEmittedSettleMemo:
    """The string replay writes a window memo in the crate's label format, and the build's streaming absorb (`read_replay_memo`, then the finished file staged and moved into place) converts it into a settle memo file. The byte proof holds that absorb to the in-memory one (`absorb_replay_memo`) over the same dump. The main test sweeps a walk over a file the crate wrote: a key converted wrongly misses instead of returning a wrong outcome, so the check on the label conversion is that the walk settles nothing (`fresh_windows`), and the check on outcomes is the settled stream against the unmemoized reference. The other tests hold the converted file to the same rules as one the conformance sweep writes (stamp, family keys, per-family retirement) and check the conversion's refusals."""

    STAMP = "replay-stamp-a"

    @pytest.fixture(scope="class")
    def dumps_dir(self, tmp_path_factory, spec):
        out_dir = tmp_path_factory.mktemp("replay-memo")
        run_build.build_tables(spec, out_dir)
        kernel_exec.replay_strings(
            spec,
            out_dir,
            conform.SETTLEMENT_CONFIGS,
            max_length=4,
            families=None,
            threads=2,
            memo_dir=out_dir,
        )
        return out_dir

    @pytest.fixture(scope="class")
    def reduced_dumps_dir(self, tmp_path_factory, spec):
        """`dumps_dir` over the fixture spec without the ·Tea+·Oy ligature (`_without_the_tea_oy_ligature`)."""
        reduced = _without_the_tea_oy_ligature(spec)
        out_dir = tmp_path_factory.mktemp("replay-memo-reduced")
        run_build.build_tables(reduced, out_dir)
        kernel_exec.replay_strings(
            reduced,
            out_dir,
            conform.SETTLEMENT_CONFIGS,
            max_length=4,
            families=None,
            threads=2,
            memo_dir=out_dir,
        )
        return out_dir

    def _memo(self, tmp_path, config="default", stamp=STAMP, keys=None):
        return conform.SettleMemoFile(tmp_path / f"settle-memo-{config}.bin", stamp, dict(keys or {}))

    @staticmethod
    def _absorb(dump, memo, spec, config):
        """Absorb `dump` as a full replay does: `read_replay_memo` over its bytes, then the finished file staged beside `memo.path` and moved into place, returning the row count."""
        with dump.open("rb") as stream:
            rows = conform.read_replay_memo(stream, spec, config, dump)
        os.replace(rows.finish(memo).stage(memo.path), memo.path)
        return rows.rows

    @staticmethod
    def _through_pipe(dump, memo, spec, config, name):
        """Absorb `dump` as `_absorb` does, its bytes fed through a pipe by a thread of their own, as the crate writes a full replay's memo, with `name` naming the stream as the build's walk names it (`kernel_exec.replay_memo_dump`), and return the file's bytes."""
        source, sink = os.pipe()
        data = dump.read_bytes()

        def feed():
            with os.fdopen(sink, "wb") as stream:
                stream.write(data)

        writer = threading.Thread(target=feed, daemon=True)
        try:
            writer.start()
        except BaseException:
            os.close(sink)
            raise
        try:
            with os.fdopen(source, "rb") as stream:
                rows = conform.read_replay_memo(stream, spec, config, name)
        finally:
            writer.join()
        os.replace(rows.finish(memo).stage(memo.path), memo.path)
        return memo.path.read_bytes()

    def test_a_dump_through_a_pipe_absorbs_to_the_bytes_the_in_memory_absorb_writes(
        self, spec, dumps_dir, tmp_path, monkeypatch
    ):
        """The streaming absorb lays out the file `absorb_replay_memo` lays out from the same dump, byte for byte: over the crate's narrow dumps for `default` and `ss03`; over a wide dump with more than 65,536 labels, so the dump's rows and the file's id columns are both `I`; and over a dump whose rows use two pairs of record ids as their lefts, each pair sharing one display name that no raw label spells, which takes the row check over the same columns, where the second pair's merged ids lie past the first repeat, and merges each pair's rows into one, and still matches. Only that last dump reaches the row check."""
        checked: list[object] = []
        check = conform._check_respelled_rows
        monkeypatch.setattr(
            conform,
            "_check_respelled_rows",
            lambda *arguments: (checked.append(arguments[-1]), check(*arguments)),
        )

        def same(dump, config, name):
            in_memory = self._memo(tmp_path / "in-memory" / name, config)
            streamed = self._memo(tmp_path / "streamed" / name, config)
            conform.absorb_replay_memo(dump, in_memory, spec, config)
            assert self._through_pipe(
                dump, streamed, spec, config, kernel_exec.replay_memo_dump(tmp_path, config)
            ) == (in_memory.path.read_bytes()), name
            header = conform._settle_memo_header(streamed)
            assert header is not None, name
            return header

        for config in ("default", "ss03"):
            assert same(kernel_exec.replay_memo_dump(dumps_dir, config), config, config)["typecode"] == "H"
        head, labels, records, body = _dump_parts(kernel_exec.replay_memo_dump(dumps_dir, "default"))
        rows = list(struct.iter_unpack("<7H", body))
        filler = [f"qsFiller{index}".encode() for index in range(1 << 16)]
        wide = tmp_path / "wide.bin"
        _write_dump(
            wide,
            {**head, "width": 4},
            [*labels, *filler],
            records,
            [(row[0], row[1] + len(filler) if row[1] >= len(labels) else row[1], *row[2:]) for row in rows],
        )
        assert same(wide, "default", "wide")["typecode"] == "I"
        assert not checked

        def twin(line):
            record = json.loads(line)
            return json.dumps(
                {**record, "extension": record["extension"] + 1}, separators=(",", ":")
            ).encode()

        shown, _ = conform._replay_memo_tables(labels, records, spec, "default", "the fixture's dump")
        fresh: dict[str, bytes] = {}
        for name, line in zip(shown[len(labels) :], records):
            if name not in shown[: len(labels)]:
                fresh.setdefault(name, line)
        first, other = list(fresh.values())[:2]
        names = [line.decode() for line in labels]
        edge, na, pea, tea = (names.index(label) for label in ("#EDGE", "#NA", "qsPea", "qsTea"))
        repeated = tmp_path / "repeated.bin"
        _write_dump(
            repeated,
            head,
            labels,
            [first, twin(first), other, twin(other)],
            [
                (pea, len(labels), tea, edge, na, na, 0),
                (pea, len(labels) + 1, tea, edge, na, na, 0),
                (pea, len(labels) + 2, tea, edge, na, na, 2),
                (pea, len(labels) + 3, tea, edge, na, na, 2),
                (tea, len(labels), pea, edge, na, na, 1),
            ],
        )
        assert same(repeated, "default", "repeated")["rows"] == 3
        assert checked == [repeated, kernel_exec.replay_memo_dump(tmp_path, "default")]

    @pytest.mark.parametrize("config", ["default", "ss03"])
    def test_the_absorbed_memo_serves_every_window_the_walk_reaches_and_settles_alike(
        self, spec, dumps_dir, tmp_path, config
    ):
        """The sweep to the replay's maximum length, with a walk carrying the crate-written file, settles nothing (every key the walk forms is one the conversion wrote), and its streams and names equal the unmemoized reference. `ss03` exercises the marker renaming on the input and right slots, `default` the unrenamed labels."""
        memo = self._memo(tmp_path, config)
        entries = self._absorb(kernel_exec.replay_memo_dump(dumps_dir, config), memo, spec, config)
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
        """A walk built with `promote=False`, as the conformance sweep builds it, over the crate-written file settles every text to depth 3 from the store's columns, with the same streams and names as an unmemoized walk, no crate call and nothing added to `windows`. The rows it reached are exactly the distinct windows the reference walk memoized."""
        memo = self._memo(tmp_path)
        self._absorb(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
        texts = _texts_to_depth(spec, 3)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker._settle_calls == 0 and walker.fresh_windows == 0
        assert not walker.windows
        assert walker._cold.reached_count() == len(reference.windows)
        assert {window for window, _ in walker._cold.items(reached=True)} == set(reference.windows)

    def test_a_walk_that_keeps_whole_text_hits_in_the_store_answers_alike(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """A walk built with `promote_pinned=False` over the crate-written file settles every text to depth 3 with the same streams and names as an unmemoized walk and no crate call. A hit whose window spells out its whole text (`_is_pinned`) stays out of `windows` and every other hit enters it, and a text walked twice reads its whole-text window from the store both times."""
        memo = self._memo(tmp_path)
        self._absorb(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
        texts = _texts_to_depth(spec, 3)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote_pinned=False)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker._settle_calls == 0 and walker.fresh_windows == 0
        whole: dict[conform._Window, str] = {}
        for text in texts:
            state = walker._state(text)
            if state.tokens[0].kind == "letter" and walker._is_pinned(window := walker._window(state), state):
                whole[window] = text
        assert whole and len(whole) < len(reference.windows)
        assert set(walker.windows) == set(reference.windows) - set(whole)
        assert walker.promoted_hits == len(walker.windows) and walker.kept_hits == len(whole)
        assert walker._cold.reached_count() == len(reference.windows)
        window, text = max(whole.items(), key=lambda item: len(item[1]))
        assert walker.walk_many([text]) == reference.walk_many([text])
        assert walker.kept_hits == len(whole) + 1 and window not in walker.windows
        assert walker._settle_calls == 0 and walker.fresh_windows == 0

    def test_a_label_the_file_never_introduced_misses_before_the_crate_is_asked(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """A probe is served from the store or not at all: a window with a label missing from the file's table misses at the label lookup, a window of known labels that the file does not hold misses at its index slot, and neither calls the crate or marks a row reached."""
        memo = self._memo(tmp_path)
        self._absorb(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
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
        self._absorb(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
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
        before = _memo_files(memo)
        head, labels, records, body = _dump_parts(kernel_exec.replay_memo_dump(dumps_dir, "default"))
        other_format = tmp_path / "other-format.bin"
        other_format.write_bytes(b"# ams-replay-memo/0\t" + json.dumps(head).encode() + b"\n")
        truncated = tmp_path / "truncated.bin"
        truncated.write_bytes(kernel_exec.replay_memo_dump(dumps_dir, "default").read_bytes()[:-3])
        for dump, config in [
            (other_format, "default"),
            (kernel_exec.replay_memo_dump(dumps_dir, "ss03"), "default"),
            (truncated, "default"),
        ]:
            with pytest.raises(kernel_exec.KernelRunError):
                self._absorb(dump, memo, spec, config)
            assert _memo_files(memo) == before
        absent = self._memo(tmp_path / "absent")
        with pytest.raises(kernel_exec.KernelRunError):
            self._absorb(truncated, absent, spec, "default")
        assert not absent.path.exists()

    def test_the_absorbed_file_carries_its_stamp_and_keys_and_retires_by_family(
        self, spec, guard, dumps_dir, tmp_path
    ):
        """The converted file behaves under `StaleMask` like one the conformance sweep wrote: under the stamp and family keys it was written with every entry loads and none is stale, a change to one family's key retires exactly the entries whose windows name that family, and another stamp reads as no file."""
        keys = {name: f"{name}@0" for name in spec.registry.families}
        memo = self._memo(tmp_path, keys=keys)
        entries = self._absorb(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
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

    @staticmethod
    def _refill(dump, spec):
        """`dump` read as a full replay's absorb reads its window memo (`read_replay_memo`), the refill a compare takes."""
        with dump.open("rb") as stream:
            return conform.read_replay_memo(stream, spec, "default", dump)

    @staticmethod
    def _items(memo, spec) -> set:
        """What the memo at `memo.path` serves, as a set of windows and settled items."""
        store = conform._MemoStore()
        store.load(memo, spec, None, lambda item: (item, "", ""))
        try:
            return {(window, outcome[0]) for window, outcome in store.items()}
        finally:
            store.close()

    def test_a_refill_that_settles_every_window_alike_writes_the_head_alone(self, spec, dumps_dir, tmp_path):
        """The landing's transition: a tree holds today's single file, written under another stamp. The full replay's plan adopts it by hard link as generation 0's base, the refill compares equal column by column with it (`fast`), and the commit writes the head alone, every part recorded under the current stamp: the base is the single file's inode, modification time and sha256, the single file's own name is gone, the memo stands under the new stamp, and it serves what the single file served."""
        dump = kernel_exec.replay_memo_dump(dumps_dir, "default")
        before = self._memo(tmp_path, stamp="replay-stamp-before")
        self._absorb(dump, before, spec, "default")
        single = before.path.stat()
        digest = row_delta.file_sha256(before.path)
        expected = self._items(before, spec)
        memo = self._memo(tmp_path)
        assert not conform.settle_memo_standing(memo)
        plan = conform.plan_settle_memos({"default": memo})
        route = plan.routes["default"]
        assert route.route == conform.SEGMENT_ROUTE and route.adopted
        assert plan.tail() == "segment_routes=1 base_routes=0 first=0 unusable=0 fold=0 adopting=1"
        compared = conform.compare_replay(memo, spec, self._refill(dump, spec), route)
        assert isinstance(compared, conform.ReplayCompare)
        assert compared.segment is None and compared.fast and compared.named == 0
        assert compared.entries == len(expected)
        head = route.commit(memo, None, restamp=True)
        base = tmp_path / head.base.name
        assert not head.segments and not memo.path.exists()
        assert (base.stat().st_ino, base.stat().st_mtime_ns) == (single.st_ino, single.st_mtime_ns)
        assert row_delta.file_sha256(base) == digest
        assert cast(row_delta.PartStamp, head.base.settled).stamp == memo.stamp
        assert conform.settle_memo_standing(memo) and self._items(memo, spec) == expected

    def test_a_refill_over_a_memo_listing_an_outcome_the_spec_cannot_name_is_written_whole(
        self, spec, dumps_dir, reduced_dumps_dir, tmp_path, capsys
    ):
        """The first full replay after ·Tea+·Oy leaves the registry compares its refill with a memo whose tables list the formed ligature's outcomes. The compare returns None, warning, so the absorb writes the refill whole as an `unusable` base: its tables list no outcome the spec cannot name, and a walk over the spec without the ligature loads it and settles every text to depth 3 with no crate call and the results of a walk with no memo."""
        reduced = _without_the_tea_oy_ligature(spec)
        memo = self._memo(tmp_path)
        self._absorb(kernel_exec.replay_memo_dump(dumps_dir, "default"), memo, spec, "default")
        route = conform.plan_settle_memos({"default": memo}).routes["default"]
        assert route.route == conform.SEGMENT_ROUTE
        refill = self._refill(kernel_exec.replay_memo_dump(reduced_dumps_dir, "default"), reduced)
        capsys.readouterr()
        assert conform.compare_replay(memo, reduced, refill, route) is None
        assert (
            f"[warn] settle memo: {memo.path} lists an outcome the spec cannot name ('qsTea_qsOy'); the refill is written whole"
            in capsys.readouterr().err
        )
        unusable = route.unusable()
        head = unusable.commit(memo, unusable.write(refill.finish(memo)))
        assert head.generation == 1 and not head.segments
        store = conform._MemoStore()
        store.load(memo, reduced, None, lambda item: (item, "", ""))
        try:
            assert store.live and conform._unnamed_outcome(reduced, store.outcomes) is None
        finally:
            store.close()
        guard = kernel_exec.guard_sweep(reduced)
        texts = _texts_to_depth(reduced, 3)
        reference = conform._SettledWindowWalk(reduced, frozenset(), {}, guard)
        walker = conform._SettledWindowWalk(reduced, frozenset(), {}, guard, memo=memo)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker._settle_calls == 0 and walker.fresh_windows == 0

    @pytest.mark.parametrize("dropped", [False, True])
    def test_a_refill_that_changes_a_window_writes_a_segment_of_it_and_names_the_rows_it_replaces(
        self, spec, dumps_dir, tmp_path, dropped
    ):
        """A refill that settles one window to another outcome, and, with `dropped`, no longer holds another, writes one segment holding the changed window's row, with dead bitmaps naming the standing rows of both, and leaves the base in place; the memo then serves exactly what today's whole file of that refill serves. With the same windows in the same order the compare reads the value column alone (`fast`), and otherwise probes the memo row by row. A later refill of the original dump compares by probe against the base and the segment, and the memo serves the original's windows again."""
        dump = kernel_exec.replay_memo_dump(dumps_dir, "default")
        head, labels, records, body = _dump_parts(dump)
        code = "<" + {2: "H", 4: "I"}[head["width"]] * 7
        size = struct.calcsize(code)
        rows = [struct.unpack_from(code, body, at) for at in range(0, len(body), size)]
        settled = [kernel_exec.settled_of_row(json.loads(record)) for record in records]
        changed = list(rows)
        at = len(rows) // 3
        other = next(index for index, item in enumerate(settled) if item != settled[rows[at][6]])
        changed[at] = (*rows[at][:6], other)
        if dropped:
            del changed[2 * len(rows) // 3]
        modified = tmp_path / "modified.bin"
        _write_dump(modified, head, labels, records, changed)
        reference = self._memo(tmp_path / "reference")
        reference.path.parent.mkdir()
        self._absorb(modified, reference, spec, "default")
        memo = self._memo(tmp_path / "memo")
        memo.path.parent.mkdir()
        self._absorb(dump, memo, spec, "default")
        original = self._items(memo, spec)
        inode = memo.path.stat().st_ino
        route = conform.plan_settle_memos({"default": memo}).routes["default"]
        compared = conform.compare_replay(memo, spec, self._refill(modified, spec), route)
        assert isinstance(compared, conform.ReplayCompare) and compared.segment is not None
        assert compared.fast == (not dropped)
        assert (compared.rows, compared.named) == (1, 2 if dropped else 1)
        committed = route.commit(memo, route.write(compared.segment), restamp=True)
        assert len(committed.segments) == 1
        assert (memo.path.parent / committed.base.name).stat().st_ino == inode
        assert self._items(memo, spec) == self._items(reference, spec) != original
        route = conform.plan_settle_memos({"default": memo}).routes["default"]
        back = conform.compare_replay(memo, spec, self._refill(dump, spec), route)
        assert isinstance(back, conform.ReplayCompare) and back.segment is not None and not back.fast
        route.commit(memo, route.write(back.segment), restamp=True)
        assert self._items(memo, spec) == original

    def test_two_crate_keys_that_collapse_to_one_walk_key_must_agree(self, spec, guard, dumps_dir, tmp_path):
        """Two left-slot record ids whose cells have the same display name form one walk key. Rows keyed on either id absorb as one row when they agree (the dump counts two, the file holds one), a later refill of that dump compares with the file as its one window, writing the head alone with `entries` counting that window, and a dump where they disagree is refused whole."""
        head, labels, records, _ = _dump_parts(kernel_exec.replay_memo_dump(dumps_dir, "default"))
        record = json.loads(records[0])
        twin = json.dumps({**record, "extension": record["extension"] + 1}, separators=(",", ":")).encode()
        names = [line.decode() for line in labels]
        edge, na, pea, tea = (names.index(label) for label in ("#EDGE", "#NA", "qsPea", "qsTea"))
        record_id = len(labels)
        dump = tmp_path / "collapsing.bin"
        _write_dump(
            dump,
            head,
            labels,
            [records[0], twin],
            [(pea, record_id, tea, edge, na, na, 0), (pea, record_id + 1, tea, edge, na, na, 0)],
        )
        memo = self._memo(tmp_path)
        assert self._absorb(dump, memo, spec, "default") == 2
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker._load_memo()
        assert walker.memo_windows == 1 and len(walker._cold) == 1
        route = conform.plan_settle_memos({"default": memo}).routes["default"]
        compared = conform.compare_replay(memo, spec, self._refill(dump, spec), route)
        assert isinstance(compared, conform.ReplayCompare) and compared.segment is None
        assert (compared.entries, compared.named) == (1, 0)
        _write_dump(
            dump,
            head,
            labels,
            [records[0], twin],
            [(pea, record_id, tea, edge, na, na, 0), (pea, record_id + 1, tea, edge, na, na, 1)],
        )
        refused = self._memo(tmp_path / "refused")
        with pytest.raises(kernel_exec.KernelRunError, match="two ways"):
            self._absorb(dump, refused, spec, "default")
        assert not refused.path.exists()


class TestDeepTokenIndex:
    """`_DeepTokenIndex` is built from the table's raw labels but queried with the walk's marker-renamed labels, so every member combination of every class-bearing row must resolve to the deep slots of the row's renamed key. The walk-equivalence sweeps cannot catch a rename applied on one side only, because both paths use the same index, so this test checks resolution against the rows directly. The configuration renames a bare (singleton-fiber) r3 under a class-token r4, the row shape the final assertion requires."""

    def test_every_class_row_resolves_under_a_renaming_config(self):
        import dataclasses

        from engine.pipeline import model
        from engine.pipeline.model import raw_rename_map
        from engine.pipeline.kernel_exec import build_tables

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
        ), "no row exercises the renamed-bare-r3 + class-r4 shape this case exists for"


class TestSettleMemoFile:
    """Two walks over the same texts per configuration, and the memo file lets the second of them settle nothing. Whichever walk settled windows the file lacked writes it, the next walk loads it lazily, and the left slot is keyed on the display name, so a walk with a glyph inventory and a walk without one share every key. A file under another stamp, or one that does not decode, costs the walk only the windows it would have settled anyway."""

    STAMP = "tables-stamp-a"

    def _texts(self, spec, max_length=3):
        return _texts_to_depth(spec, max_length)

    def _memo(self, tmp_path, stamp=STAMP, keys=None):
        return conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", stamp, dict(keys or {}))

    def test_settle_memo_files_key_each_configuration_off_the_snapshot(self, spec, tmp_path):
        inputs = settle_memo_keys.SettleMemoInputs(rune_digests={"qsTea": "t0"}, code="code", data="data")
        files = conform.settle_memo_files(tmp_path, spec, inputs)
        assert set(files) == set(conform.SETTLEMENT_CONFIGS)
        assert files["default"].path == tmp_path / "settle-memo-default.bin"
        assert len({memo.stamp for memo in files.values()}) == len(files)
        assert files["default"].family_keys == settle_memo_keys.settle_family_keys(inputs, spec)
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

    def test_a_ligature_that_leaves_the_registry_leaves_its_memo_loading(self, spec, guard, tmp_path, capsys):
        """A memo written while ·Tea+·Oy was a ligature lists outcomes whose cell is the formed ligature's, which a spec without the ligature cannot name. A walk over that spec, under family keys that lack the ligature as that spec's own keys do, must load the memo rather than refuse it: no warning and no refusal, nothing served (the keys name the ligature as moved, and the registry cannot place it), and the results of a walk with no memo."""
        texts = self._texts(spec, 2)
        first = conform._SettledWindowWalk(
            spec,
            frozenset(),
            {},
            guard,
            memo=self._memo(tmp_path, keys={name: f"{name}@0" for name in spec.registry.families}),
        )
        first.walk_many(texts)
        assert first.save_memo()
        assert any(outcome[0].cell.rune == "qsTea_qsOy" for _, outcome in first._settled_items())
        reduced = _without_the_tea_oy_ligature(spec)
        reduced_guard = kernel_exec.guard_sweep(reduced)
        reference = conform._SettledWindowWalk(reduced, frozenset(), {}, reduced_guard)
        expected = reference.walk_many(texts)
        capsys.readouterr()
        second = conform._SettledWindowWalk(
            reduced,
            frozenset(),
            {},
            reduced_guard,
            memo=self._memo(tmp_path, keys={name: f"{name}@0" for name in reduced.registry.families}),
        )
        assert second.walk_many(texts) == expected
        assert "[warn] settle memo:" not in capsys.readouterr().err
        assert second._memo_loaded and not second._cold.refused and second._cold.parts
        assert second.memo_windows == 0 and second.fresh_windows == len(reference.windows)

    def test_a_fold_that_carries_a_dropped_ligatures_outcomes_still_serves_every_window(
        self, spec, guard, tmp_path, capsys, monkeypatch
    ):
        """The memo a pass leaves once ·Tea+·Oy has left the registry: the pooled walks settle every window again, and the absorb that folds carries the standing outcome tables whole into a base under the current keys, so the base lists the formed ligature's outcomes while no row holds one. A walk over the spec without the ligature must load that base, settle nothing and return what a walk with no memo returns."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path, keys={name: f"{name}@0" for name in spec.registry.families})
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[: len(texts) // 2])
        assert seed.save_memo()
        ranged = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
        )
        ranged.walk_many(texts)
        assert ranged.save_memo() and ranged.fresh_memo is not None
        assert conform.absorb_settle_memo(memo, spec, [ranged.fresh_memo]).wrote == "segment"

        reduced = _without_the_tea_oy_ligature(spec)
        reduced_guard = kernel_exec.guard_sweep(reduced)
        expected = conform._SettledWindowWalk(reduced, frozenset(), {}, reduced_guard).walk_many(texts)
        keyed = self._memo(tmp_path, keys={name: f"{name}@0" for name in reduced.registry.families})
        again = conform._SettledWindowWalk(
            reduced,
            frozenset(),
            {},
            reduced_guard,
            memo=replace(keyed, returns_fresh=True),
            promote_pinned=False,
        )
        assert again.walk_many(texts) == expected
        assert again.memo_windows == 0 and again.save_memo() and again.fresh_memo is not None
        monkeypatch.setattr(row_delta, "SEGMENT_CAP", 1)
        absorbed = conform.absorb_settle_memo(keyed, reduced, [again.fresh_memo])
        assert (absorbed.wrote, absorbed.reason, absorbed.segments) == ("base", "fold", 0)
        head = row_delta.read_head(tmp_path, keyed.path.name)
        assert head is not None and head.part_keys(head.base) == keyed.family_keys
        store = conform._MemoStore()
        store.load(keyed, reduced, None, lambda item: (item, "", ""))
        try:
            listed = {
                outcome_id
                for outcome_id, outcome in enumerate(store.outcomes)
                if outcome[0].cell.rune == "qsTea_qsOy"
            }
            assert listed and store.live == len(again.windows)
            assert listed.isdisjoint(store.parts[0].values)
        finally:
            store.close()

        capsys.readouterr()
        walker = conform._SettledWindowWalk(reduced, frozenset(), {}, reduced_guard, memo=keyed)
        assert walker.walk_many(texts) == expected
        assert "[warn] settle memo:" not in capsys.readouterr().err
        assert walker._settle_calls == 0 and walker.fresh_windows == 0
        assert walker.memo_windows == len(again.windows)

    def test_a_row_whose_outcome_the_spec_cannot_name_serves_nothing(self, spec, guard, tmp_path, capsys):
        """The load's own guard, which no walk on a real pass reaches, since a dropped ligature's formed label is never asked and a dropped stance moves its rune's key: a memo under the walk's stamp and keys whose rows hold outcomes the walk's spec cannot name, here the formed ·Tea+·Oy's under a spec without the ligature, loaded with the family keys it was written under. Those rows must be dead, out of `live`, `loaded`, `len`, `standing` and `items` and counted in `foreign`, and a probe of their window must miss, while every other row serves the outcome it holds."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path, keys={name: f"{name}@0" for name in spec.registry.families})
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        first.walk_many(texts)
        assert first.save_memo()
        settled = dict(first._settled_items())
        unnamed = {window for window, outcome in settled.items() if outcome[0].cell.rune == "qsTea_qsOy"}
        assert 0 < len(unnamed) < len(settled)
        reduced = _without_the_tea_oy_ligature(spec)
        walker = conform._SettledWindowWalk(
            reduced, frozenset(), {}, kernel_exec.guard_sweep(reduced), memo=memo
        )
        capsys.readouterr()
        walker._load_memo()
        assert "[warn] settle memo:" not in capsys.readouterr().err
        store = walker._cold
        try:
            served = len(settled) - len(unnamed)
            assert not store.refused and store.stale == 0
            assert (store.live, walker.memo_windows, len(store), store.standing(), store.foreign) == (
                served,
                served,
                served,
                served,
                len(unnamed),
            )
            items = dict(store.items())
            assert set(items) == set(settled) - unnamed
            for window, outcome in settled.items():
                probed = store.probe(window)
                if window in unnamed:
                    assert probed is None
                else:
                    assert probed is not None and probed[0] == outcome[0] and probed == items[window]
        finally:
            store.close()

    def test_the_sweep_prunes_what_no_text_reaches_and_a_partial_walk_carries_it(self, spec, guard, tmp_path):
        """Two walks over the same file with different responsibilities. A walk over a subset of the texts reaches only the windows of those texts, so it writes nothing when it settled nothing and would carry every loaded entry forward if it did. The conformance sweep's walk reaches every window any text produces, so it prunes what it never reached, and a window an edit has made unreachable leaves the file on the next sweep instead of staying in it indefinitely."""
        memo = self._memo(tmp_path)
        long_texts, short_texts = self._texts(spec, 3), self._texts(spec, 2)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        first.walk_many(long_texts)
        assert first.save_memo()
        total = len(first.windows)
        written = _memo_files(memo)

        partial = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        partial.walk_many(short_texts)
        touched = len(partial.windows)
        assert 0 < touched < total and partial.memo_windows == total
        assert not partial.save_memo()
        assert partial.pruned_windows == 0 and _memo_files(memo) == written

        sweep_side = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        sweep_side.walk_many(short_texts)
        assert not sweep_side.windows and sweep_side._cold.reached_count() == touched
        assert sweep_side.save_memo(prune=True)
        assert sweep_side.pruned_windows == total - touched

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

    def test_the_store_reads_the_file_as_a_dict_does_and_the_carry_forward_files_the_same_rows(
        self, spec, guard, tmp_path, monkeypatch
    ):
        """The store must read the file as a dict does: `items` iterates the live rows in the dict's order with the dict's outcomes, and `len` is the dict's length. Then a whole-file save without pruning (a walk whose memo is not `returns_fresh`, on a fold, which every save in this test takes), over a walk that reached some rows and settled others fresh, must write the same rows in the same order as a dict-backed walk: `windows` first, then every loaded entry `windows` does not hold, in file order, copied from the mapped columns. A walk that leaves its whole-text hits in the store (`promote_pinned=False`) carries every live row and merges each carried copy of a row it promoted onto its `windows` entry, so over the same texts its file holds the same windows and outcomes, one row a window, as `windows` and then every other loaded entry in file order. Its index has the slot count of the promoting walk's file, and so does the index such a walk writes over a file that lacks one window, where counting a carried copy of each promoted row would take the slot count past the next power of two."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        seed.walk_many(self._texts(spec, 2))
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
        )
        kept = replace(memo, path=tmp_path / "kept" / memo.path.name)
        kept.path.parent.mkdir()
        kept.path.write_bytes(memo.path.read_bytes())
        monkeypatch.setattr(row_delta, "fold_due", lambda head, cap=None: True)

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
        promoted_rows = self._ordered(memo, spec)
        assert promoted_rows == self._ordered(replace(memo, path=carried), spec)

        keeping = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=kept, promote_pinned=False)
        assert keeping.walk_many(texts) == reference.walk_many(texts)
        assert keeping.kept_hits and keeping.promoted_hits and keeping.save_memo()
        kept_rows = self._ordered(kept, spec)
        assert dict(kept_rows) == dict(promoted_rows) and len(kept_rows) == len(promoted_rows)
        assert self._header(kept)["slots"] == self._header(memo)["slots"]
        assert [window for window, _ in kept_rows] == list(keeping.windows) + [
            window for window in expected if window not in keeping.windows
        ]

        lacking = replace(memo, path=tmp_path / "lacking" / memo.path.name)
        lacking.path.parent.mkdir()
        every = cast(dict[conform._Window, conform._Outcome], walker.windows)
        assert conform._write_settle_memo(
            lacking, *conform._memo_columns(itertools.islice(every.items(), 1, None))
        )
        nearly = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=lacking, promote_pinned=False)
        assert nearly.walk_many(texts) == reference.walk_many(texts)
        assert nearly.fresh_windows == 1 and nearly.kept_hits
        assert 2 * (len(every) + nearly.promoted_hits) > self._header(memo)["slots"]
        assert nearly.save_memo()
        assert dict(self._ordered(lacking, spec)) == dict(promoted_rows)
        assert self._header(lacking)["slots"] == self._header(memo)["slots"]

    def test_a_walk_returns_its_fresh_windows_in_the_order_it_settled_them(self, spec, guard, tmp_path):
        """A walk whose memo is `returns_fresh` writes no file and hands back only its fresh windows, in the order it settled them, one block of `_memo_blocks` at a time; nothing from the loaded store goes into them, and the counts beside the blocks are what the blocks hold. A walk that leaves its whole-text hits in the store (`promote_pinned=False`) hands back the same blocks byte for byte."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(self._texts(spec, 2))
        assert seed.save_memo()
        standing = _memo_files(memo)
        walker = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
        )
        walker.walk_many(self._texts(spec, 3))
        order = list(walker._fresh)
        assert walker.fresh_windows and walker.save_memo() and walker.fresh_memo is not None
        assert _memo_files(memo) == standing
        expected = list(
            conform._memo_blocks((window, cast(conform._Outcome, walker.windows[window])) for window in order)
        )
        fresh = walker.fresh_memo
        assert [
            (labels, items, [list(column) for column in columns], list(values))
            for labels, items, columns, values in _fresh_blocks(fresh)
        ] == [
            (labels, items, [list(column) for column in columns], list(values))
            for labels, items, columns, values in expected
        ]
        assert (fresh.rows, fresh.labels, fresh.records) == (
            len(order),
            sum(len(block[0]) for block in expected),
            sum(len(block[1]) for block in expected),
        )
        assert not set(order) & set(seed.windows)
        keeping = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True), promote_pinned=False
        )
        keeping.walk_many(self._texts(spec, 3))
        assert keeping.kept_hits and keeping.promoted_hits
        assert keeping.save_memo() and keeping.fresh_memo is not None
        assert list(keeping.fresh_memo.blocks) == list(fresh.blocks)
        assert (keeping.fresh_memo.rows, keeping.fresh_memo.labels, keeping.fresh_memo.records) == (
            fresh.rows,
            fresh.labels,
            fresh.records,
        )

    def _ordered(self, memo, spec) -> list[tuple[tuple[str, ...], settle.Settled]]:
        """Every row of the file at `memo.path` in file order, as its window tuple and settled item."""
        return [(window, outcome[0]) for window, outcome in self._decoded(memo, spec)]

    def test_a_pruning_walk_that_promotes_nothing_files_what_a_promoting_one_files(
        self, spec, guard, tmp_path
    ):
        """Either way the conformance sweep writes one segment: the windows it settled fresh, in the order it settled them, and dead bitmaps naming the rows it never reached, which leaves the standing rows it reached in place. A walk that promoted every hit into `windows` and one that promoted nothing reach the same rows, so they write the same files byte for byte, the memo then reads as the reached standing rows in the file's own order followed by the fresh windows, and `pruned_windows` is the same."""
        seeded = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        seed.walk_many(self._texts(spec, 2))
        assert conform._write_settle_memo(
            seeded,
            *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], seed.windows).items()),
        )
        standing = self._ordered(seeded, spec)
        texts = [text for text in self._texts(spec, 3) if len(text) != 2 or text[0] == text[1]]
        files = {}
        for promote in (True, False):
            memo = conform.SettleMemoFile(tmp_path / f"promote-{promote}" / seeded.path.name, self.STAMP)
            memo.path.parent.mkdir()
            memo.path.write_bytes(seeded.path.read_bytes())
            walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=promote)
            walker.walk_many(texts)
            reached = {window for window, _ in walker._cold.items(reached=True)}
            fresh = list(walker._fresh)
            assert walker.fresh_windows and walker.save_memo(prune=True)
            assert 0 < walker.pruned_windows < len(standing)
            rows = self._ordered(memo, spec)
            assert [window for window, _ in rows] == [
                window for window, _ in standing if window in reached
            ] + fresh
            files[promote] = (walker, {name: data for name, (data, _mtime) in _memo_files(memo).items()})
        promoting, promoted_files = files[True]
        columnar, columnar_files = files[False]
        assert promoting.pruned_windows == columnar.pruned_windows
        assert promoted_files == columnar_files
        assert sorted(promoted_files) == sorted(
            [
                row_delta.base_name(seeded.path.name, 0),
                row_delta.segment_name(seeded.path.name, 0, 1),
                row_delta.head_name(seeded.path.name),
            ]
        )

    def test_the_second_walk_over_the_same_texts_never_reaches_the_crate(self, spec, guard, tmp_path):
        texts = self._texts(spec)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walked = first.walk_many(texts)
        assert first.memo_windows == 0
        assert first.fresh_windows == len(first.windows)
        assert first.save_memo()
        written = _memo_files(memo)

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
        assert _memo_files(memo) == written

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

    @pytest.mark.parametrize("damage", ["cut", "tables"])
    def test_a_truncated_file_is_refused_and_loads_nothing(self, spec, guard, tmp_path, capsys, damage):
        """A mapped file is read whole or not at all: a file shorter than its header's declared layout (`cut`), or one whose tables do not unpickle although its size, sha256 and header are what its head records (`tables`, which a code change that renames a pickled class leaves, and which the plan's checks pass onto the segment route), loads no row and prints a warning, the walk settles everything, and its save replaces the file with a whole one that the next walk serves from."""
        texts = self._texts(spec)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walked = first.walk_many(texts)
        assert first.save_memo()
        head = row_delta.read_head(memo.path.parent, memo.path.name)
        assert head is not None and not head.segments
        base = memo.path.parent / head.base.name
        whole = base.read_bytes()
        if damage == "cut":
            base.write_bytes(whole[: len(whole) // 2])
        else:
            (length,) = conform._MEMO_PREFIX.unpack_from(whole)
            tables = pickle.loads(whole[conform._MEMO_PREFIX.size : conform._MEMO_PREFIX.size + length])[
                "tables"
            ]
            start = conform._memo_aligned(conform._MEMO_PREFIX.size + length)
            base.write_bytes(whole[:start] + bytes(tables) + whole[start + tables :])
            row_delta.commit(
                memo.path.parent, replace(head, base=replace(head.base, sha256=row_delta.file_sha256(base)))
            )
            route = conform.plan_settle_memos({"default": memo}).routes["default"]
            assert route.route == conform.SEGMENT_ROUTE

        second = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        assert second.walk_many(texts) == walked
        assert second.memo_windows == 0 and second._cold.live == 0
        assert second.fresh_windows == len(first.windows)
        assert "[warn] settle memo:" in capsys.readouterr().err
        assert second.save_memo()
        rewritten = row_delta.read_head(memo.path.parent, memo.path.name)
        assert rewritten is not None and rewritten.generation == 1 and not rewritten.segments
        assert (memo.path.parent / rewritten.base.name).read_bytes() == whole and not base.exists()
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        third.walk_many(texts)
        assert third.memo_windows == len(first.windows)
        assert third._settle_calls == 0

    def test_a_file_cut_to_its_header_still_stands_and_loads_nothing(self, spec, guard, tmp_path, capsys):
        """`settle_memo_standing` reads only the header: a file cut off right after its header still stands under its own stamp and not under another, a walk over it loads nothing and settles everything, and a file cut inside the header stands under no stamp."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        first.walk_many(texts)
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], first.windows).items())
        )
        assert conform.settle_memo_standing(memo)
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
        assert len(store._mappings) == 1
        mapping = store._mappings[0]
        rows = store.items()
        next(rows)
        store.close()
        assert not store._mappings and not mapping.closed and len(store) == 0
        del rows
        mapping.close()
        assert mapping.closed

    def test_a_corrupt_index_retires_the_store_at_the_first_probe_that_finds_it(
        self, spec, guard, tmp_path, capsys
    ):
        """When no family key moved and no ask restriction applies, the load reads the header and tables and no column page, so a corrupt index (every slot naming one row, or naming a row past the columns) is found by a probe. The probe chain is bounded by the row count plus one and the id by the column length, and either finding retires every row with a warning. The walk then settles everything it has not already been served (the one row every slot names may serve its own window first), and its save replaces the file with a whole one."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        first = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walked = first.walk_many(texts)
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], first.windows).items())
        )
        whole = memo.path.read_bytes()
        header = self._header(memo)
        index_bytes = header["slots"] * 4
        for filler, problem in (
            (1, "a probe chain past its rows"),
            (header["rows"] + 5, "an id past its tables"),
        ):
            for name in _memo_files(memo):
                (memo.path.parent / name).unlink()
            memo.path.write_bytes(whole[:-index_bytes] + struct.pack("<I", filler) * header["slots"])
            walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
            assert walker.walk_many(texts) == walked
            assert walker.memo_windows == header["rows"] and walker._cold.live == 0
            assert len(first.windows) - walker.fresh_windows <= 1 and walker._cold.reached_count() == 0
            assert f"[warn] settle memo: {memo.path} retired ({problem})" in capsys.readouterr().err
            assert walker.save_memo()
            assert _materialized(memo, spec, tmp_path / "materialized.bin") == whole

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
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        seed.walk_many(texts)
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
        )
        mapped = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        mapped._load_memo()
        assert mapped.memo_windows == len(seed.windows)
        inode = os.fstat(mapped._cold._handles[0].fileno()).st_ino

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
                "from engine.pipeline import conform",
                "from engine.pipeline.fixtures import mini_spec",
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

    @pytest.mark.parametrize("form", ["whole", "returns_fresh", "restricted"])
    def test_a_refusal_is_not_written_and_is_asked_again(self, spec, guard, tmp_path, monkeypatch, form):
        """A refusal is memoized only for the tolerant walk that met it. The file holds outcomes only, so the next walk to reach that window asks the crate again and gets whatever the crate returns then. A walk that returns its fresh windows, as a sweep unit does, leaves the refusal out of its part, so the file the absorb writes holds none either. A walk restricted to its texts' asks, whose memo is read-only, memoizes the refusal the same way and raises at that key on its own walk, because a refusal is kept in `windows`, and it writes neither the file nor a part."""
        clean, refusing_text = chr(0xE665) + chr(0xE670), chr(0xE665) + chr(0xE652)
        original = kernel_exec.settle_windows

        def injecting(asked_spec, cases, features, **rest):
            results = original(asked_spec, cases, features, **rest)
            for index, case in enumerate(cases):
                if case.split("\t")[kernel_exec.CASE_INPUT_FIELD] == "qsTea":
                    results[index] = None
            return results

        monkeypatch.setattr(kernel_exec, "settle_windows", injecting)
        memo = self._memo(tmp_path)
        walker = conform._SettledWindowWalk(
            spec,
            frozenset(),
            {},
            guard,
            on_error="drop",
            memo={
                "whole": memo,
                "returns_fresh": replace(memo, returns_fresh=True),
                "restricted": replace(memo, read_only=True),
            }[form],
        )
        if form == "restricted":
            walker.load_only_asked_by([clean, refusing_text])
        walker.prefill([clean, refusing_text])
        refused = [key for key, value in walker.windows.items() if isinstance(value, conform._RefusedWindow)]
        assert refused
        with pytest.raises(settle.SettleError):
            walker.walk(refusing_text)
        if form == "restricted":
            assert not walker.save_memo() and walker.fresh_memo is None
            assert not memo.path.exists()
            return
        assert walker.save_memo()
        if form == "returns_fresh":
            assert walker.fresh_memo is not None and not memo.path.exists()
            assert walker.fresh_memo.rows == walker.fresh_windows - len(refused)
            assert conform.absorb_settle_memo(memo, spec, [walker.fresh_memo]).written

        monkeypatch.setattr(kernel_exec, "settle_windows", original)
        again = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        again.walk_many([clean])
        assert again.memo_windows == len(walker.windows) - len(refused)
        assert not any(key in again.windows for key in refused)
        settled, _names = again.walk(refusing_text)
        assert [item.cell.rune for item in settled] == ["qsMay", "qsTea"]
        assert again.fresh_windows == len(refused)

    def test_the_ranges_fresh_windows_absorb_into_the_file_without_dropping_each_others(
        self, spec, guard, tmp_path
    ):
        """Two walks over the same existing file, each settling windows the other does not and each returning only what it settled fresh. Neither touches the shared file, and the parent's absorb produces one file that a third walk reads whole: every existing window and both walks' fresh windows, with no crate call. A range that replaced the file whole would lose the other range's windows."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        half = len(texts) // 2
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:half])
        assert seed.save_memo()
        standing = _memo_files(memo)

        rest = texts[half:]
        returning = replace(memo, returns_fresh=True)
        one = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=returning)
        one.walk_many(rest[: len(rest) // 2])
        two = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=returning)
        two.walk_many(rest[len(rest) // 2 :])
        assert one.fresh_windows and two.fresh_windows
        only_one = set(one.windows) - set(two.windows) - set(seed.windows)
        only_two = set(two.windows) - set(one.windows) - set(seed.windows)
        assert only_one and only_two
        assert one.save_memo() and two.save_memo()
        assert _memo_files(memo) == standing
        assert one.fresh_memo is not None and two.fresh_memo is not None

        assert conform.absorb_settle_memo(memo, spec, [one.fresh_memo, two.fresh_memo]).written
        third = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert third.walk_many(texts) == reference.walk_many(texts)
        assert third._settle_calls == 0 and third.fresh_windows == 0
        assert set(third.windows) >= only_one | only_two | set(seed.windows)

    def test_a_restricted_walk_serves_only_its_asks_and_leaves_the_file_alone(self, spec, guard, tmp_path):
        """As in the witness stage, a walk that loads only the rows its texts can ask cannot tell a dropped row from an unreached one, so it never prunes or replaces the file, and since such a walk is the witness stage's, which writes nothing back, its memo is read-only: it serves exactly the rows whose five settlement-independent slots are among its asks, counts the rest as unasked, settles the windows the file lacks fresh, and then writes nothing, returns no fresh windows, and leaves the file's bytes as they were. A whole-file save from such a walk would drop every row its load dropped."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        half = len(texts) // 2
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:half])
        assert seed.save_memo()
        standing = _memo_files(memo)

        rest = texts[half:]
        restricted = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, read_only=True)
        )
        asks = restricted.load_only_asked_by(rest)
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert restricted.walk_many(rest) == reference.walk_many(rest)
        kept = {window for window in seed.windows if (window[0],) + tuple(window[2:]) in asks}
        assert 0 < len(kept) < len(seed.windows)
        assert restricted.memo_windows == len(kept)
        assert restricted.unasked_windows == len(seed.windows) - len(kept)
        fresh = set(restricted.windows) - set(seed.windows)
        assert fresh and restricted.fresh_windows == len(fresh)
        assert not restricted.save_memo() and restricted.fresh_memo is None
        assert _memo_files(memo) == standing

    def test_a_restricted_load_serves_the_rows_an_absorb_added_under_the_standing_spellings(
        self, spec, guard, tmp_path
    ):
        """The steady state of the file the witness stage reads. A walk's fresh windows carry their own label table, and the absorb maps each label onto the id the existing file already uses for it, so the file names each label once and a restricted load tests rows in one id space. Seeded whole, extended by a walk's fresh windows (an unrestricted walk that returns them), and read by a restricted walk over the same texts, the file serves every row that walk asks, existing and absorbed alike, and the walk settles nothing and writes nothing."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        half = len(texts) // 2
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:half])
        assert seed.save_memo()

        rest = texts[half:]
        standing_labels = set(itertools.chain.from_iterable(seed.windows))
        asks = conform._SettledWindowWalk(spec, frozenset(), {}, guard).load_only_asked_by(rest)
        first = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
        )
        first.walk_many(rest)
        assert first.fresh_windows and first.save_memo() and first.fresh_memo is not None
        part_labels = {
            label for new_labels, *_rest in _fresh_blocks(first.fresh_memo) for label in new_labels
        }
        assert part_labels & standing_labels & set(itertools.chain.from_iterable(asks))
        assert conform.absorb_settle_memo(memo, spec, [first.fresh_memo]).written
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
        absorbed = _memo_files(memo)
        again = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=replace(memo, read_only=True))
        assert again.load_only_asked_by(rest) == asks
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert again.walk_many(rest) == reference.walk_many(rest)
        assert again.memo_windows == len(kept)
        assert again.unasked_windows == len(rows) - len(kept)
        assert again._settle_calls == 0 and again.fresh_windows == 0
        assert not again.save_memo() and again.fresh_memo is None
        assert _memo_files(memo) == absorbed

    def _header(self, memo) -> dict:
        """The header of `memo`'s whole file: its single file, or the base of a head that lists no segment."""
        head = row_delta.read_head(memo.path.parent, memo.path.name)
        assert head is None or not head.segments
        with open(memo.path if head is None else memo.path.parent / head.base.name, "rb") as handle:
            (length,) = conform._MEMO_PREFIX.unpack(handle.read(conform._MEMO_PREFIX.size))
            return pickle.loads(handle.read(length))

    def test_the_absorbed_file_names_each_spelling_once_and_answers_every_row(self, spec, guard, tmp_path):
        """The memo's label table names each label once across its parts (the absorb maps the fresh windows' labels onto the ids the base already uses, and its segment lists only the labels the base lacks), so the store's id map is its label table inverted, and the probe returns for every window the outcome a dict of the memo holds, one row per distinct window. The absorb writes one segment beside the base and leaves the base's bytes and inode as they were, so the existing rows keep their positions."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(texts[:-3])
        assert seed.save_memo()
        standing = self._header(memo)
        standing_rows = self._ordered(memo, spec)
        head = row_delta.read_head(memo.path.parent, memo.path.name)
        assert head is not None
        base = memo.path.parent / head.base.name
        base_bytes, base_inode = base.read_bytes(), base.stat().st_ino
        first = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
        )
        first.walk_many(texts[-3:])
        assert first.fresh_windows and first.save_memo() and first.fresh_memo is not None
        absorbed = conform.absorb_settle_memo(memo, spec, [first.fresh_memo])
        assert absorbed.written and absorbed.wrote == "segment" and absorbed.index == "segment"
        assert absorbed.carried == standing["rows"] and absorbed.rows_written == first.fresh_windows
        assert base.read_bytes() == base_bytes and base.stat().st_ino == base_inode
        segment_labels = {
            label for new_labels, *_rest in _fresh_blocks(first.fresh_memo) for label in new_labels
        } - set(itertools.chain.from_iterable(window for window, _ in standing_rows))
        store = conform._MemoStore()
        store.load(memo, spec, None, lambda item: (item, "", ""))
        try:
            assert [part.listed_labels for part in store.parts] == [standing["labels"], len(segment_labels)]
        finally:
            store.close()
        assert self._ordered(memo, spec)[: len(standing_rows)] == standing_rows

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        store = walker._cold
        assert len(store.label_ids) == len(store.labels)
        assert all(store.labels[label_id] == label for label, label_id in store.label_ids.items())
        assert len(store) == len(expected) == len(store.dead)
        assert list(store.items()) == list(expected.items())
        for window, outcome in expected.items():
            assert store.probe(window) is outcome
        assert store.reached_count() == len(expected)

    def _windows(self, spec, guard) -> list[tuple[conform._Window, conform._Outcome]]:
        """Every window the mini alphabet's texts of up to three letters reach, with its outcome, in the order a walk settles them."""
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        walker.walk_many(self._texts(spec, 3))
        return list(cast(dict[conform._Window, conform._Outcome], walker.windows).items())

    @staticmethod
    def _restated(rows, copy: int) -> list[tuple[conform._Window, conform._Outcome]]:
        """`rows` under labels of their own, each label suffixed so it names the same family (`conform._label_family`) and no window of `rows`."""
        return [
            (cast(conform._Window, tuple(f"{label}.r{copy}" for label in window)), outcome)
            for window, outcome in rows
        ]

    @staticmethod
    def _wide(rows, count: int) -> list[tuple[conform._Window, conform._Outcome]]:
        """`count` windows of six labels no other window names, more labels than an `H` column can index once `count` passes 10,923, each with an outcome of `rows`."""
        prefixes = ("qsPea.w", "qsTea.w", "x", "y", "z", "v")
        return [
            (
                cast(
                    conform._Window,
                    tuple(f"{prefix}{6 * index + slot}" for slot, prefix in enumerate(prefixes)),
                ),
                rows[index % len(rows)][1],
            )
            for index in range(count)
        ]

    @pytest.mark.parametrize(
        "case",
        [
            "fast",
            "fresh-against-fresh",
            "duplicate",
            "stale",
            "prune",
            "discard",
            "fresh",
            "typecode-I",
            "widen",
            "canon",
        ],
    )
    def test_the_absorb_writes_the_bytes_the_carry_and_append_absorb_writes(
        self, spec, guard, tmp_path, case
    ):
        """Over a standing file, `absorb_settle_memo` writes one segment beside it, and the memo's materialized form is byte for byte the file the carry-and-append absorb writes from the same standing file and the same blocks in the same order (`_carry_and_append_absorb`, through `_write_settle_memo` with the standing index when no row dropped), which is the file the whole-file absorb wrote before the segment format, and `store.items()` reads the same windows and outcomes: with the standing index's room for new windows (`fast`, which the reference reuses), with every fresh row restating a standing window, its row in the segment winning by the probe's order and the materialized form patching the standing row's outcome in place (`duplicate`), with a moved family's rows named in the dead bitmaps (`stale`), with the sweep's prune and no fresh row (`prune`), with the sweep's discard after a retirement, every standing row named and the parts' rows alone kept (`discard`), over `I` standing columns (`typecode-I`), with `H` standing columns widened to `I` by labels the fresh rows bring (`widen`), and with labels repeated across the standing table and the parts merging through `canon` onto their first ids (`canon`). With two ranges restating each other's windows, the later outcome winning (`fresh-against-fresh`), the segment merges the repeats, so the materialized form is that reference sized from the distinct windows, which today's absorb, sized from every row passed, matches only while the repeats keep the slot count where it is. With no standing file (`fresh`) it takes the `first` route and writes that reference's file as generation 0's base."""
        rows = self._windows(spec, guard)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        memo = self._memo(tmp_path, keys=keys)
        outcomes = list(dict.fromkeys(outcome for _, outcome in rows))
        swapped = [
            (window, next(other for other in outcomes if other is not outcome))
            for window, outcome in rows[-50:]
        ]
        wide = self._wide(rows, 12_000)
        sweep = None
        standing: list = []
        parts: list = []
        if case == "fast":
            standing, parts = rows[:-60], [rows[-60:-30], rows[-30:]]
        elif case == "fresh-against-fresh":
            standing, parts = rows[:-60], [rows[-60:-20], [*swapped[-10:], *rows[-40:]]]
        elif case == "duplicate":
            standing, parts = rows, [swapped[:25], swapped[25:]]
        elif case == "stale":
            standing, parts = rows + self._restated(rows, 1), [rows[-40:], self._restated(rows, 2)[:30]]
        elif case == "fresh":
            parts = [self._restated(rows, copy) for copy in range(4)]
        elif case == "typecode-I":
            standing, parts = wide, [rows[:100], wide[:50]]
        elif case == "widen":
            standing, parts = rows, [wide[:6000], [*wide[6000:], *rows[:50]]]
        elif case == "canon":
            standing, parts = rows[:30], [rows[10:80], rows[60:120], []]
        elif case == "discard":
            standing, parts = rows[:-60], [rows[-60:-30], rows[-30:]]
        else:
            standing = rows + self._restated(rows, 1)
        if standing:
            assert conform._write_settle_memo(memo, *conform._memo_columns(standing))
        if case == "stale":
            memo = replace(memo, family_keys={**keys, "qsTea": "qsTea@1"})
        if case in ("prune", "discard"):
            store = conform._MemoStore()
            store.load(memo, spec, None, lambda item: (item, "", ""))
            identity, count = store.identity(), len(store.reached)
            store.close()
            if case == "prune":
                flags = bytes(int(row % 7 != 0) for row in range(count))
            else:
                flags = bytes(count)
            sweep = conform.SweepPrune(identity, zlib.compress(flags, 1), True, case == "discard")
        fresh = [conform._fresh_memo(part) for part in parts]
        reference, written = tmp_path / "reference.bin", tmp_path / "absorbed.bin"
        distinct = None
        if case == "fresh-against-fresh":
            distinct = len({window for window, _ in itertools.chain(standing, *parts)})
        assert _carry_and_append_absorb(memo, spec, fresh, reference, sweep, distinct)
        if case == "widen":
            assert self._header(memo)["typecode"] == "H"
        absorbed = conform.absorb_settle_memo(memo, spec, fresh, sweep)
        assert absorbed.written and absorbed.wrote == ("base" if case == "fresh" else "segment")
        assert absorbed.index == ("rebuilt" if case == "fresh" else "segment")
        assert not memo.path.exists()
        head = row_delta.read_head(memo.path.parent, memo.path.name)
        assert head is not None and len(head.segments) == (0 if case == "fresh" else 1)
        assert _materialized(memo, spec, written) == reference.read_bytes()
        if case == "fresh":
            assert (memo.path.parent / head.base.name).read_bytes() == reference.read_bytes()
        assert set(self._decoded(memo, spec)) == set(self._decoded(replace(memo, path=reference), spec))
        typecode = self._header(replace(memo, path=written))["typecode"]
        assert typecode == ("I" if case in ("typecode-I", "widen") else "H")
        if case in ("fresh-against-fresh", "duplicate"):
            assert absorbed.patched == 30 if case == "fresh-against-fresh" else absorbed.patched == 50
        if case == "discard":
            assert absorbed.named == absorbed.dropped == absorbed.standing == len(standing)
            assert absorbed.carried == 0 and absorbed.rows_written == 60

    @pytest.mark.parametrize("standing", [False, True])
    def test_an_absorb_whose_head_cannot_be_committed_warns_and_writes_nothing(
        self, spec, guard, tmp_path, capsys, standing
    ):
        """An absorb whose part is written and whose head the filesystem then refuses, here because a directory stands at the head's staging path, is a warning, as the replay's parent's is, on the segment route over an adopted single file and on the `first` route alike: it reports nothing written, the memo serves what it served, and the next plan deletes the part no head lists."""
        rows = self._windows(spec, guard)
        memo = self._memo(tmp_path)
        if standing:
            assert conform._write_settle_memo(memo, *conform._memo_columns(rows[:100]))
        served = self._decoded(memo, spec)
        route = conform.plan_settle_memos({"default": memo}).routes["default"]
        assert route.route == (conform.SEGMENT_ROUTE if standing else conform.BASE_ROUTE)
        head_path = row_delta.head_path(memo.path.parent, memo.path.name)
        blocked = head_path.with_name(head_path.name + ".partial")
        blocked.mkdir()
        capsys.readouterr()
        absorbed = conform.absorb_settle_memo(
            memo, spec, [conform._fresh_memo(self._restated(rows[:50], 1))], route=route
        )
        assert not absorbed.written and not head_path.exists()
        assert f"{memo.path} not written" in capsys.readouterr().err
        blocked.rmdir()
        assert self._decoded(memo, spec) == served
        stray = (
            row_delta.segment_name(memo.path.name, 0, 1)
            if standing
            else row_delta.base_name(memo.path.name, 0)
        )
        assert (tmp_path / stray).is_file()
        conform.plan_settle_memos({"default": memo})
        assert not (tmp_path / stray).exists()

    def test_a_window_a_segment_holds_misses_once_its_family_moves_in_a_pass_that_never_asks_it(
        self, spec, guard, tmp_path
    ):
        """A window an absorb settled into a segment in one pass, whose family's key moved in the next, which never asks that window again, is dead: before that pass's absorb because each part's rows are masked against the keys that part records, and after it because the absorb names it in its segment's dead bitmap, so it misses even under the keys the segment was settled under."""
        texts = self._texts(spec, 2)
        tea = chr(0xE652)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        memo = self._memo(tmp_path, keys=keys)
        plain = [text for text in texts if tea not in text]
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(plain[: len(plain) // 2])
        assert seed.save_memo()
        ranged = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
        )
        ranged.walk_many([text for text in texts if tea in text])
        naming = [
            window
            for window in ranged._fresh
            if any(conform._label_family(label) == "qsTea" for label in window)
        ]
        assert naming
        assert ranged.save_memo() and ranged.fresh_memo is not None
        assert conform.absorb_settle_memo(memo, spec, [ranged.fresh_memo]).wrote == "segment"

        def probed(keyed: conform.SettleMemoFile) -> list:
            store = conform._MemoStore()
            store.load(keyed, spec, None, lambda item: (item, "", ""))
            try:
                return [store.probe(window) for window in naming]
            finally:
                store.close()

        assert all(outcome is not None for outcome in probed(memo))
        moved = replace(memo, family_keys={**keys, "qsTea": "qsTea@1"})
        assert not any(probed(moved))
        later = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(moved, returns_fresh=True)
        )
        later.walk_many(plain[len(plain) // 2 :])
        assert later.stale_windows == len(set(naming)) and later.save_memo() and later.fresh_memo is not None
        absorbed = conform.absorb_settle_memo(moved, spec, [later.fresh_memo])
        assert absorbed.wrote == "segment" and absorbed.named == len(set(naming))
        assert not any(probed(moved)) and not any(probed(memo))
        head = row_delta.read_head(memo.path.parent, memo.path.name)
        assert head is not None and [head.part_keys(part) for part in head.parts] == [
            keys,
            keys,
            moved.family_keys,
        ]

    @pytest.mark.parametrize("shadowed", [False, True])
    def test_a_fold_writes_the_materialized_form_as_the_next_base_and_deletes_the_old_generation(
        self, spec, guard, tmp_path, monkeypatch, shadowed
    ):
        """An absorb that holds the pass's fold writes the materialized form of every part with its own changes as the next generation's base, which is byte for byte the materialized form the same absorbs give as segments, and the commit deletes the old generation. A segment after the fold leaves the memo serving what it serves without the fold, and, when no part holds a live row a later one shadows, its materialized form byte for byte what it is without the fold. With `shadowed` the first segment restates live base windows with other outcomes: the materialized form sizes its index from every live row, the shadowed ones included, and the fold has merged them into its base, so after the fold only the windows served, in order, are the same."""
        rows = self._windows(spec, guard)
        folded, segmented = self._memo(tmp_path / "folded"), self._memo(tmp_path / "segmented")
        parts = [conform._fresh_memo(self._restated(rows[:100], copy)) for copy in range(1, 5)]
        if shadowed:
            outcomes = list(dict.fromkeys(outcome for _, outcome in rows))
            parts[0] = conform._fresh_memo(
                [
                    (window, next(other for other in outcomes if other is not outcome))
                    for window, outcome in rows[:30]
                ]
            )
        for memo in (folded, segmented):
            memo.path.parent.mkdir()
            assert conform._write_settle_memo(memo, *conform._memo_columns(rows[:1000]))
        written = [conform.absorb_settle_memo(segmented, spec, [part]) for part in parts[:3]]
        assert [item.wrote for item in written] == ["segment"] * 3
        assert written[0].patched == (30 if shadowed else 0)
        for part in parts[:2]:
            assert conform.absorb_settle_memo(folded, spec, [part]).wrote == "segment"
        old = sorted(_memo_files(folded))
        monkeypatch.setattr(row_delta, "SEGMENT_CAP", 2)
        absorbed = conform.absorb_settle_memo(folded, spec, [parts[2]])
        assert (absorbed.wrote, absorbed.reason, absorbed.segments) == ("base", "fold", 0)
        head = row_delta.read_head(folded.path.parent, folded.path.name)
        assert head is not None and head.generation == 1 and not head.segments
        assert not set(old) - {row_delta.head_name(folded.path.name)} & set(_memo_files(folded))
        expected = _materialized(segmented, spec, tmp_path / "segmented.bin")
        assert (folded.path.parent / head.base.name).read_bytes() == expected
        monkeypatch.setattr(row_delta, "SEGMENT_CAP", 16)
        for memo in (folded, segmented):
            assert conform.absorb_settle_memo(memo, spec, [parts[3]]).wrote == "segment"
        assert self._decoded(folded, spec) == self._decoded(segmented, spec)
        if not shadowed:
            assert _materialized(folded, spec, tmp_path / "after-fold.bin") == _materialized(
                segmented, spec, tmp_path / "after-segments.bin"
            )

    def test_a_reader_reads_only_its_heads_parts_and_refuses_one_of_another_generation(
        self, spec, guard, tmp_path, capsys
    ):
        """A reader opens only the parts its head lists, so a segment a failed pass left under a name the head does not list is ignored, and the next writer deletes it. A segment whose header, or one of whose dead bitmaps, names another generation than its head's is refused: the memo loads nothing and the walk settles everything."""
        rows = self._windows(spec, guard)
        keys = {name: f"{name}@0" for name in spec.registry.families}
        memo = self._memo(tmp_path, keys=keys)
        assert conform._write_settle_memo(memo, *conform._memo_columns(rows[:500]))
        moved = replace(memo, family_keys={**keys, "qsTea": "qsTea@1"})
        absorbed = conform.absorb_settle_memo(
            moved, spec, [conform._fresh_memo(self._restated(rows[:50], 1))]
        )
        assert absorbed.wrote == "segment" and absorbed.named
        expected = set(self._decoded(moved, spec))
        segment = tmp_path / row_delta.segment_name(memo.path.name, 0, 1)
        stray = tmp_path / row_delta.segment_name(memo.path.name, 0, 2)
        shutil.copy2(segment, stray)
        assert set(self._decoded(moved, spec)) == expected
        whole = segment.read_bytes()
        (length,) = conform._MEMO_PREFIX.unpack_from(whole)
        header = pickle.loads(whole[conform._MEMO_PREFIX.size : conform._MEMO_PREFIX.size + length])
        for forged in (
            {**header, "generation": 1},
            {**header, "dead": [{**header["dead"][0], "generation": 1}]},
        ):
            pickled = pickle.dumps(forged, protocol=5)
            assert len(pickled) == length
            segment.write_bytes(
                whole[: conform._MEMO_PREFIX.size] + pickled + whole[conform._MEMO_PREFIX.size + length :]
            )
            capsys.readouterr()
            store = conform._MemoStore()
            store.load(moved, spec, None, lambda item: (item, "", ""))
            assert store.refused and not store.live and not store.parts
            assert "generation 1" in capsys.readouterr().err
        segment.write_bytes(whole)
        copied = row_delta.file_sha256(stray)
        assert conform.absorb_settle_memo(
            moved, spec, [conform._fresh_memo(self._restated(rows[:5], 2))]
        ).written
        head = row_delta.read_head(tmp_path, memo.path.name)
        assert head is not None and head.segments[1].name == stray.name
        assert row_delta.file_sha256(stray) == head.segments[1].sha256 != copied

    def test_a_part_under_another_stamp_serves_nothing_and_a_probe_takes_the_newest_live_row(
        self, spec, guard, tmp_path
    ):
        """Each part records the stamp it was settled under, and serves only a walk keyed with that stamp: a base under one stamp and a segment an absorb under another wrote each serve their own rows alone, and `settle_memo_standing` holds under neither. Under one stamp, a segment that restates a base row's window with another outcome wins by the probe's order, and the memo reads one row a window."""
        rows = self._windows(spec, guard)
        outcomes = list(dict.fromkeys(outcome for _, outcome in rows))
        swapped = [
            (window, next(other for other in outcomes if other is not outcome))
            for window, outcome in rows[:40]
        ]
        first = self._memo(tmp_path / "stamps")
        first.path.parent.mkdir()
        assert conform._write_settle_memo(first, *conform._memo_columns(rows[:300]))
        second = replace(first, stamp="tables-stamp-b")
        assert conform.absorb_settle_memo(second, spec, [conform._fresh_memo(swapped)]).wrote == "segment"
        assert {window for window, _ in self._decoded(first, spec)} == {window for window, _ in rows[:300]}
        assert {window: outcome[0] for window, outcome in self._decoded(second, spec)} == {
            window: outcome[0] for window, outcome in swapped
        }
        assert not conform.settle_memo_standing(first) and not conform.settle_memo_standing(second)
        same = self._memo(tmp_path / "same")
        same.path.parent.mkdir()
        assert conform._write_settle_memo(same, *conform._memo_columns(rows[:300]))
        absorbed = conform.absorb_settle_memo(same, spec, [conform._fresh_memo(swapped)])
        assert absorbed.wrote == "segment" and absorbed.patched == len(swapped)
        store = conform._MemoStore()
        store.load(same, spec, None, lambda item: (item, "", ""))
        try:
            for window, outcome in swapped:
                assert cast(conform._Outcome, store.probe(window))[0] == outcome[0]
            assert len(store) == 300 and len(list(store.items())) == 300
            assert dict((window, outcome[0]) for window, outcome in store.items()) == {
                **{window: outcome[0] for window, outcome in rows[:300]},
                **{window: outcome[0] for window, outcome in swapped},
            }
        finally:
            store.close()
        assert conform.settle_memo_standing(same)

    @pytest.mark.parametrize("piece", ["head", "rows"])
    def test_a_block_that_will_not_decode_warns_and_ends_its_part_there(
        self, spec, guard, tmp_path, capsys, piece
    ):
        """A block whose head or rows will not decode ends its part at that block with a warning: the part's blocks before it and the parts after it are absorbed, and the file is the one the part would give had it ended there, its tables merged without the labels the dropped blocks introduce."""
        rows = self._windows(spec, guard)
        memo = self._memo(tmp_path)
        assert conform._write_settle_memo(memo, *conform._memo_columns(rows[:100]))
        many = [window for copy in range(50) for window in self._restated(rows, copy)]
        first = conform._fresh_memo(many[: 2 * conform.SETTLE_MEMO_BLOCK])
        last = conform._fresh_memo(rows[100:200])
        assert len(first.blocks) == 4
        blocks = list(first.blocks)
        blocks[2 if piece == "head" else 3] = b"not a zlib stream"
        torn = replace(first, blocks=blocks)
        reference, written = tmp_path / "reference.bin", tmp_path / "absorbed.bin"
        assert _carry_and_append_absorb(
            memo, spec, [replace(first, blocks=first.blocks[:2]), last], reference
        )
        capsys.readouterr()
        absorbed = conform.absorb_settle_memo(memo, spec, [torn, last])
        assert f"stopped reading after {conform.SETTLE_MEMO_BLOCK} windows" in capsys.readouterr().err
        assert absorbed.parts == conform.SETTLE_MEMO_BLOCK + 100 and absorbed.wrote == "segment"
        assert _materialized(memo, spec, written) == reference.read_bytes()

    def test_a_window_listed_twice_is_written_as_one_row_with_the_later_outcome(self, spec, guard, tmp_path):
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

    @pytest.mark.parametrize("standing", [False, True])
    def test_two_ranges_settling_one_window_absorb_into_one_row(self, spec, guard, tmp_path, standing):
        """The production case of a repeated window: two row ranges that both settle a window both return it, and the absorb folds the pair onto one row, so the memo's row count equals the dict's and a walk over the memo reaches every window once. Over a standing memo from which no row drops, the absorb writes one segment beside it: the later range's copy of a window patches the row the earlier range's copy took in the segment, and every standing row keeps its place."""
        texts = self._texts(spec, 2)
        memo = self._memo(tmp_path)
        header: dict = {}
        standing_rows: list = []
        if standing:
            seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
            seed.walk_many(texts[:-6])
            assert seed.save_memo()
            header = self._header(memo)
            standing_rows = self._ordered(memo, spec)
            piles = (texts[-6:-2], texts[-4:])
        else:
            third = len(texts) // 3
            piles = (texts[: 2 * third], texts[third:])
        parts = []
        for pile in piles:
            walker = conform._SettledWindowWalk(
                spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
            )
            walker.walk_many(pile)
            assert walker.fresh_windows and walker.save_memo() and walker.fresh_memo is not None
            parts.append(walker.fresh_memo)
        filed = [_fresh_rows(part) for part in parts]
        assert filed[0] & filed[1] and filed[1] - filed[0]
        absorbed = conform.absorb_settle_memo(memo, spec, parts)
        assert absorbed.written and absorbed.patched == len(filed[0] & filed[1])
        if standing:
            assert absorbed.index == "segment" and absorbed.carried == header["rows"]
            assert absorbed.rows_written == len(filed[0] | filed[1])
            assert self._ordered(memo, spec)[: len(standing_rows)] == standing_rows

        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo, promote=False)
        expected = self._dict_of(walker, memo, spec)
        walker._load_memo()
        store = walker._cold
        rows = len(standing_rows) + len(filed[0] | filed[1])
        assert len(store.dead) == len(store) == len(expected) == rows
        assert list(store.items()) == list(expected.items())
        reference = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        assert walker.walk_many(texts) == reference.walk_many(texts)
        assert walker._settle_calls == 0 and walker.fresh_windows == 0
        assert store.reached_count() == len(expected)

    def _swept(self, spec, guard, memo, last=None, max_length=2) -> conform.ConformanceConfigResult:
        """`default`'s conformance sweep over the mini alphabet with a stand-in font, whole or, with `last`, one unit."""
        return conform._conformance_config(
            _SilentShaper(),  # pyright: ignore[reportArgumentType]
            spec,
            "default",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            {},
            None,
            max_length,
            guard,
            settle_memo=memo,
            last=last,
        )

    def _unit_fold(self, spec, guard, memo) -> tuple[conform.MemoAbsorb, list[conform.FreshMemo]]:
        """`default`'s units over every symbol, each returning its fresh windows, folded as the pooled sweep's parent folds them: the units' reaches OR-ed into one mask and absorbed with every unit's fresh windows in unit-slot order, discarding the file's rows when any unit retired its store. Return the absorb and the units' fresh windows."""
        reaches = []
        for symbol in conform.spec_alphabet(spec):
            reach = self._swept(spec, guard, replace(memo, returns_fresh=True), last=symbol).memo_reach
            assert reach is not None
            reaches.append(reach)
        assert len({(reach.identity, reach.rows) for reach in reaches}) == 1
        union = 0
        for reach in reaches:
            union |= int.from_bytes(zlib.decompress(reach.reached), "little")
        mask = zlib.compress(union.to_bytes(reaches[0].rows, "little"), 1)
        retired = any(reach.retired for reach in reaches)
        parts = [reach.fresh for reach in reaches if reach.fresh is not None]
        prune = conform.SweepPrune(reaches[0].identity, mask, True, retired)
        return conform.absorb_settle_memo(memo, spec, parts, prune), parts

    @pytest.mark.parametrize("start", ["absent", "current", "unreached", "segments"])
    def test_the_units_fold_files_the_rows_one_pruning_walk_files(self, spec, guard, tmp_path, start):
        """The pooled conformance sweep's units each walk the texts that end in one symbol, return what they settled fresh, and return the rows they reached; the parent ORs the reaches and absorbs the units' fresh windows. That must leave the memo serving the rows one pruning walk over every text leaves, from a memo that is absent, current (nothing is written, so its files and their modification times stand), current plus rows no text reaches (exactly those are pruned), or a base and a segment, each holding rows no text reaches (those are pruned from both parts)."""
        whole, units = tmp_path / "whole", tmp_path / "units"
        whole.mkdir()
        units.mkdir()
        reference, memo = self._memo(whole), self._memo(units)
        if start != "absent":
            seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
            seed.walk_many(self._texts(spec, 2 if start == "current" else 3))
            seeded_windows = list(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
            cut = 3 * len(seeded_windows) // 4 if start == "segments" else len(seeded_windows)
            assert conform._write_settle_memo(reference, *conform._memo_columns(seeded_windows[:cut]))
            if start == "segments":
                restated = self._restated(seeded_windows, 1)
                part = conform._fresh_memo([*seeded_windows[cut:], *restated[: len(restated) // 10]])
                assert conform.absorb_settle_memo(reference, spec, [part]).wrote == "segment"
            for name in _memo_files(reference):
                shutil.copy2(whole / name, units / name)
        before = _memo_files(memo) if start == "current" else None
        seeded = len(self._rows(reference, spec)) if start != "absent" else 0

        self._swept(spec, guard, reference)
        absorbed, _parts = self._unit_fold(spec, guard, memo)
        expected = {window: outcome[0] for window, outcome in self._decoded(reference, spec)}
        assert {window: outcome[0] for window, outcome in self._decoded(memo, spec)} == expected
        if start == "absent":
            assert absorbed.written and absorbed.pruned == 0 and absorbed.fresh == len(expected)
        elif start == "current":
            assert (absorbed.written, absorbed.pruned, absorbed.fresh) == (False, 0, 0)
            assert _memo_files(memo) == before
        else:
            assert absorbed.written and absorbed.fresh == 0 and absorbed.pruned == seeded - len(expected) > 0
            assert absorbed.index == "segment" and absorbed.dropped == absorbed.pruned == absorbed.named
            if start == "segments":
                store = conform._MemoStore()
                store.load(memo, spec, None, lambda item: (item, "", ""))
                try:
                    assert len(store.parts) == 3
                    dropped = [part.rows - part.live for part in store.parts[:2]]
                finally:
                    store.close()
                assert all(dropped) and sum(dropped) == absorbed.pruned

    def test_a_fan_out_that_submits_no_absorb_still_prints_its_plan_once(self, tmp_path, capsys):
        """A pooled sweep plans every configuration's route as its fan-out starts (`run_build_gates._PlannedRoutes`), whether or not a range or unit then settles anything fresh: a fan-out that submits no absorb prints its `[t] settle_memo_plan` line when it closes, and one that asks for a route prints it then and not again."""
        memo = self._memo(tmp_path)
        for asks in (False, True):
            routes = run_build_gates._PlannedRoutes({"default": memo})
            if asks:
                assert routes.route("default").reason == "first"
            routes.close()
            lines = [
                line
                for line in capsys.readouterr().out.splitlines()
                if line.startswith("[t] settle_memo_plan ")
            ]
            assert len(lines) == 1 and lines[0].endswith("base_routes=1 first=1 unusable=0 fold=0 adopting=0")

    def test_a_unit_returns_only_its_fresh_windows_and_leaves_the_file_and_the_line_to_the_parent(
        self, spec, guard, tmp_path, capsys
    ):
        """A unit walk returns the windows it settled fresh on its reach and writes nothing: the shared file keeps its bytes, the unit prints no `[t] settle_memo` line, and its reach names the version of the file it mapped, which the parent's absorb checks before it prunes."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        seed.walk_many(self._texts(spec, 1))
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
        )
        standing = memo.path.read_bytes()
        stat = memo.path.stat()
        capsys.readouterr()

        reach = self._swept(
            spec, guard, replace(memo, returns_fresh=True), last=conform.spec_alphabet(spec)[-1]
        ).memo_reach
        assert reach is not None and reach.fresh is not None and not reach.retired
        assert memo.path.read_bytes() == standing
        assert _fresh_rows(reach.fresh) and reach.fresh.rows == len(_fresh_rows(reach.fresh))
        assert reach.identity == ("file", stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        assert reach.rows == reach.live == reach.loaded == len(seed.windows)
        assert "[t] settle_memo" not in capsys.readouterr().err

    def test_a_sweep_absorb_over_another_version_of_the_file_keeps_every_row(
        self, spec, guard, tmp_path, capsys
    ):
        """When the file on disk is not the version the units mapped, their reached flags index another file's rows, so the absorb warns, prunes nothing, and appends the units' fresh windows to every live row."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        seed.walk_many(self._texts(spec, 1))
        assert seed.save_memo()
        standing = self._rows(memo, spec)
        reach = self._swept(
            spec, guard, replace(memo, returns_fresh=True), last=conform.spec_alphabet(spec)[0]
        ).memo_reach
        assert reach is not None and reach.identity is not None and reach.fresh is not None
        capsys.readouterr()

        nothing = zlib.compress(bytes(reach.rows), 1)
        absorbed = conform.absorb_settle_memo(
            memo, spec, [reach.fresh], conform.SweepPrune(("file", 0, 0, 0, 0), nothing, True, False)
        )
        fresh = _fresh_rows(reach.fresh)
        assert absorbed.written and absorbed.pruned == 0 and absorbed.fresh == len(fresh)
        assert self._rows(memo, spec) == standing | fresh
        assert "changed after the conformance sweep's units mapped it" in capsys.readouterr().err

    def test_a_sweep_over_a_corrupt_file_rewrites_it_from_the_fresh_windows_alone(
        self, spec, guard, tmp_path, capsys
    ):
        """A row whose value id runs past every outcome table retires the store of each unit whose texts reach it. That unit still names the file's version, marks itself retired, and settles its windows fresh, and the absorb then writes only the units' fresh windows, as the serial sweep's pruning save does after a retirement: the corrupt row is not carried into the rewrite, every live row of the old file counts as pruned, and the next walk loads the file without retiring it."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        seed.walk_many(self._texts(spec, 2))
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
        )
        header = self._header(memo)
        whole = bytearray(memo.path.read_bytes())
        (length,) = conform._MEMO_PREFIX.unpack_from(whole)
        offset = conform._memo_aligned(conform._MEMO_PREFIX.size + length)
        offset = conform._memo_aligned(offset + header["tables"])
        for _ in range(6):
            offset = conform._memo_aligned(offset + header["rows"] * array(header["typecode"]).itemsize)
        bad = array(header["value_typecode"], [(1 << (8 * array(header["value_typecode"]).itemsize)) - 1])
        whole[offset : offset + bad.itemsize] = bad.tobytes()
        memo.path.write_bytes(bytes(whole))
        capsys.readouterr()

        absorbed, parts = self._unit_fold(spec, guard, memo)
        assert "retired (an id past its tables)" in capsys.readouterr().err
        assert absorbed.written and absorbed.pruned == header["rows"]
        assert self._rows(memo, spec) == set().union(*map(_fresh_rows, parts))
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker.walk_many(self._texts(spec, 2))
        assert not walker._cold.retired
        assert "[warn] settle memo:" not in capsys.readouterr().err

    def test_a_corrupt_standing_id_travels_to_the_probe_that_retires_the_store(
        self, spec, guard, tmp_path, capsys
    ):
        """The absorb copies standing ids from the mapping unchecked, so a value id past every outcome table reaches the new file instead of raising in the absorb, and the first probe that reaches the row there retires the store, as it would have in the old file."""
        memo = self._memo(tmp_path)
        seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard)
        texts = self._texts(spec, 2)
        seed.walk_many(texts[:-3])
        assert conform._write_settle_memo(
            memo, *conform._memo_columns(cast(dict[conform._Window, conform._Outcome], seed.windows).items())
        )
        header = self._header(memo)
        whole = bytearray(memo.path.read_bytes())
        (length,) = conform._MEMO_PREFIX.unpack_from(whole)
        offset = conform._memo_aligned(conform._MEMO_PREFIX.size + length)
        offset = conform._memo_aligned(offset + header["tables"])
        for _ in range(6):
            offset = conform._memo_aligned(offset + header["rows"] * array(header["typecode"]).itemsize)
        bad = array(header["value_typecode"], [(1 << (8 * array(header["value_typecode"]).itemsize)) - 1])
        whole[offset : offset + bad.itemsize] = bad.tobytes()
        memo.path.write_bytes(bytes(whole))
        ranged = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, returns_fresh=True)
        )
        ranged.walk_many(texts[-3:])
        assert ranged.save_memo() and ranged.fresh_memo is not None
        assert conform.absorb_settle_memo(memo, spec, [ranged.fresh_memo]).written
        capsys.readouterr()
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
        walker.walk_many(texts)
        assert walker._cold.retired
        assert "retired (an id past its tables)" in capsys.readouterr().err

    def test_a_unit_whose_memo_would_replace_the_file_is_refused(self, spec, guard, tmp_path):
        """A unit walks a share of the texts, so a whole-file save from it would leave the shared file holding only that share's rows. A unit whose memo does not return its fresh windows fails its assertion before it walks anything."""
        with pytest.raises(AssertionError, match="a unit sweep returns its fresh windows"):
            self._swept(spec, guard, self._memo(tmp_path), last=conform.spec_alphabet(spec)[0])

    def test_a_restriction_takes_a_read_only_memo_and_never_prunes(self, spec, guard, tmp_path):
        """The checks that tie restricted loading to a read-only memo: a walk whose memo would replace the shared file whole, or return its fresh windows for an absorb, may not restrict its load; a memo is never both read-only and returning; and a restricted walk may not prune, since after a restricted load a dropped row and an unreached window look the same."""
        texts = self._texts(spec, 1)
        memo = self._memo(tmp_path)
        for refused in (memo, replace(memo, returns_fresh=True)):
            walk = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=refused)
            with pytest.raises(AssertionError, match="a restricted walk's memo is read-only"):
                walk.load_only_asked_by(texts)
        with pytest.raises(AssertionError, match="a read-only memo returns no fresh windows"):
            replace(memo, returns_fresh=True, read_only=True)
        restricted = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(memo, read_only=True)
        )
        restricted.load_only_asked_by(texts)
        with pytest.raises(AssertionError, match="a restricted walk never prunes"):
            restricted.save_memo(prune=True)

    def test_fresh_windows_alone_become_the_file_and_a_restamped_file_contributes_nothing(
        self, spec, guard, tmp_path
    ):
        texts = self._texts(spec, 2)
        absent = self._memo(tmp_path)
        walker = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(absent, returns_fresh=True)
        )
        walker.walk_many(texts)
        assert walker.save_memo() and not absent.path.exists() and walker.fresh_memo is not None
        assert conform.absorb_settle_memo(absent, spec, [walker.fresh_memo]).written
        again = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=absent)
        again.walk_many(texts)
        assert again._settle_calls == 0 and again.memo_windows == len(walker.windows)

        restamped = self._memo(tmp_path, "tables-stamp-b")
        fresh = conform._SettledWindowWalk(
            spec, frozenset(), {}, guard, memo=replace(restamped, returns_fresh=True)
        )
        fresh.walk_many(texts[: len(texts) // 2])
        assert fresh.save_memo() and fresh.fresh_memo is not None
        absorbed = conform.absorb_settle_memo(restamped, spec, [fresh.fresh_memo])
        assert absorbed.written and absorbed.standing == absorbed.carried == 0
        loaded = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=restamped)
        loaded.walk_many(texts[: len(texts) // 2])
        assert loaded._settle_calls == 0 and loaded.memo_windows == len(fresh.windows)
        files = _memo_files(restamped)
        assert not conform.absorb_settle_memo(restamped, spec, []).written
        assert _memo_files(restamped) == files

    def test_absorbing_fresh_windows_restamps_the_file_and_retires_what_the_new_keys_retire(
        self, spec, guard, tmp_path
    ):
        """A walk's fresh windows, settled under keys naming ·Tea as moved, are absorbed into a file written under the old keys. The result must carry the new keys without the entries those keys retire. Otherwise a stale settlement would sit under a header whose keys say it is current, and no later run could retire it."""
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
        ranged = conform._SettledWindowWalk(
            edited, frozenset(), {}, edited_guard, memo=replace(moved, returns_fresh=True)
        )
        ranged.walk_many(texts[: len(texts) // 3])
        assert ranged.stale_windows == len(naming) and ranged.fresh_windows
        assert ranged.save_memo() and ranged.fresh_memo is not None
        absorbed = conform.absorb_settle_memo(moved, edited, [ranged.fresh_memo])
        assert absorbed.written and absorbed.wrote == "segment" and absorbed.dropped == len(naming)
        assert absorbed.named == len(naming)

        reference = conform._SettledWindowWalk(edited, frozenset(), {}, edited_guard)
        expected = reference.walk_many(texts)
        third = conform._SettledWindowWalk(edited, frozenset(), {}, edited_guard, memo=moved)
        assert third.walk_many(texts) == expected
        assert third.stale_windows == 0
        assert third.memo_windows == len(first.windows) - len(naming) + ranged.fresh_windows


class _KernShaper:
    """A stand-in Shaper for the kern checks: two letters `a` and `b` with locked copies, the space glyph HarfBuzz shows a ZWNJ or a space as, `hmtx` advances from `ADVANCES`, outlines named by the glyph's base name (a locked copy has its bare letter's bitmap), and each segment's shaped run looked up in `segments`."""

    ADVANCES = {"a": 250, "a.locked": 250, "b": 300, "b.locked": 300, "space": 350}

    def __init__(self, segments: Mapping[str, list[dict]] | None = None):
        self.segments = dict(segments or {})

    def advance(self, name: str) -> int:
        return self.ADVANCES[name]

    def outline_signature(self, name: str) -> tuple:
        return (name.split(".")[0],)

    def shape(self, text: str, features: frozenset[str], kern: bool = True) -> list[dict]:
        return [dict(glyph) for glyph in self.segments[text]]


def _slot(name: str, cluster: int, advance: int, offset: int = 0) -> dict:
    return {"name": name, "cluster": cluster, "x_advance": advance, "x_offset": offset, "y_offset": 0}


class TestTheKernChecks:
    """The conformance checks that read the kern assignment the font was compiled from: `a` before `b` is a facing pair kerned by one pixel (-50 units), `b` before `a` is a facing pair with no kern, and `a` before `a` and `b` before `b` are no facing pair, though each glyph has both sides."""

    ZWNJ = conform.ZWNJ

    @pytest.fixture
    def sides(self, tmp_path):
        path = tmp_path / conform.KERN_CLASSES_JSON
        path.write_text(
            json.dumps(
                {
                    "format": conform.KERN_CLASSES_FORMAT,
                    "leading": {"a": "lead-a", "a.locked": "lead-a", "b": "lead-b", "b.locked": "lead-b"},
                    "following": {
                        "a": "follow-a",
                        "a.locked": "follow-a",
                        "b": "follow-b",
                        "b.locked": "follow-b",
                    },
                    "values": [["lead-a", "follow-b", -1], ["lead-b", "follow-a", 0]],
                }
            )
        )
        return conform.load_kern_sides(path)

    def test_the_format_is_the_one_the_kern_stage_writes(self):
        from engine.pipeline import kern

        assert conform.KERN_CLASSES_FORMAT == kern.KERN_CLASSES_FORMAT
        assert conform.KERN_CLASSES_JSON == kern.KERN_CLASSES_JSON

    def test_a_document_in_another_format_does_not_load(self, tmp_path):
        path = tmp_path / "kern-classes.json"
        path.write_text(
            json.dumps({"format": "something-else/1", "leading": {}, "following": {}, "values": []})
        )
        with pytest.raises(ValueError, match="not a"):
            conform.load_kern_sides(path)

    def test_a_value_is_the_facing_pairs_kern_in_font_units_and_none_for_sides_that_never_face(self, sides):
        assert sides.value("a", "b") == -50
        assert sides.value("a.locked", "b.locked") == -50
        assert sides.value("b", "a") == 0
        assert sides.value("a", "a") is None
        assert sides.value("b", "b.locked") is None
        assert sides.value("x", "b") is None
        assert sides.value("a", "x") is None

    @pytest.mark.parametrize(
        ("text", "shaped", "kinds"),
        [
            ("pb", [_slot("a", 0, 200), _slot("b", 1, 300)], []),
            ("pb", [_slot("a", 0, 250), _slot("b", 1, 300)], ["kern"]),
            ("bp", [_slot("b", 0, 300), _slot("a", 1, 250)], []),
            ("bp", [_slot("b", 0, 250), _slot("a", 1, 250)], ["kern"]),
            ("p‌b", [_slot("a", 0, 200), _slot("space", 1, 0), _slot("b.locked", 2, 300)], []),
            ("p‌b", [_slot("a", 0, 250), _slot("space", 1, 0), _slot("b.locked", 2, 300)], ["kern"]),
            ("p b", [_slot("a", 0, 250), _slot("space", 1, 350), _slot("b", 2, 300)], []),
            ("p b", [_slot("a", 0, 200), _slot("space", 1, 350), _slot("b", 2, 300)], ["kern"]),
            ("p‌", [_slot("a", 0, 250), _slot("space", 1, 0)], []),
            ("px", [_slot("a", 0, 250), _slot("x", 1, 300)], ["kern-side"]),
            ("pp", [_slot("a", 0, 250), _slot("a", 1, 250)], ["kern-side"]),
        ],
        ids=[
            "kerned",
            "kern-missing",
            "unkerned",
            "unkerned-moved",
            "across-zwnj",
            "across-zwnj-missing",
            "across-space",
            "across-space-kerned",
            "zwnj-at-the-end",
            "no-side",
            "sides-that-never-face",
        ],
    )
    def test_every_slot_outside_a_join_advances_by_hmtx_plus_its_kern(self, sides, text, shaped, kinds):
        divergences: list = []
        conform.check_break_advances(
            text, "default", cast(conform.Shaper, _KernShaper()), shaped, None, sides, divergences
        )
        assert [divergence.kind for divergence in divergences] == kinds

    def test_a_slot_that_exits_into_a_join_is_left_to_the_gap_check(self, sides):
        """An `a` whose exit meets `b`'s entry at one height is a join, so its advance is the cursive attachment's, whatever the kern assignment says."""
        anchors = {"a": {"exit": (250, 0)}, "b": {"entry": (0, 0)}}
        divergences: list = []
        shaped = [_slot("a", 0, 250), _slot("b", 1, 250, -50)]
        conform.check_break_advances(
            "pb", "default", cast(conform.Shaper, _KernShaper()), shaped, anchors.get, sides, divergences
        )
        assert divergences == []

    def test_a_split_compares_the_slot_before_a_zwnj_with_its_kern_taken_off(self, sides):
        """The kern reaches across the ZWNJ in the whole buffer and not in the segment shaped alone, so the comparison takes it off; without the assignment the same buffer is a split divergence."""
        shaper = cast(conform.Shaper, _KernShaper({"p": [_slot("a", 0, 250)], "b": [_slot("b", 0, 300)]}))
        shaped = [_slot("a", 0, 200), _slot("space", 1, 0), _slot("b.locked", 2, 300)]
        kept: list = []
        conform.check_split_buffer("p‌b", "default", frozenset(), shaper, shaped, kept, kern_sides=sides)
        assert kept == []
        blind: list = []
        conform.check_split_buffer("p‌b", "default", frozenset(), shaper, shaped, blind)
        assert [divergence.kind for divergence in blind] == ["split"]

    @pytest.mark.parametrize(
        ("text", "shaped", "kinds"),
        [
            ("pb", [_slot("a", 0, 200), _slot("b", 1, 300)], []),
            ("pb", [_slot("a", 0, 250), _slot("b", 1, 300)], ["overlay-position"]),
            ("p‌b", [_slot("a", 0, 200), _slot("space", 1, 0), _slot("b", 2, 300)], []),
            ("px", [_slot("a", 0, 250), _slot("x", 1, 300)], ["kern-side"]),
        ],
        ids=["kerned", "kern-missing", "across-zwnj", "no-side"],
    )
    def test_an_overlay_slot_sits_at_its_hmtx_advance_plus_its_kern(self, sides, text, shaped, kinds):
        divergences: list = []
        conform.check_isolated_positions(
            text, "ss10", cast(conform.Shaper, _KernShaper()), shaped, divergences, sides
        )
        assert [divergence.kind for divergence in divergences] == kinds
