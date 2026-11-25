"""Tests for the kern stage, `rebuild/pipeline/kern.py` (rebuild/KERN-PLAN.md sections 2 to 4).

Every test loads a small hand-written alphabet through the real loader (`spec_load.load_spec` over YAML written under `tmp_path`) and hands the stage join tables built in the test, so the stage's inputs are exactly the cells each test names and no test reads a live artifact. The glyphs are realized from those cells as `run_m1.mint_cell_glyphs` realizes them, named by their cell labels.

The alphabet: ·It is a one-pixel bar; ·No has a `loop` and a `flipped` stance whose right-hand edges differ; ·Tea has a `full` and a `half` stance, and its half has a `pad_left` record, so a half ·Tea can be drawn from the `padded-left-1` copy; ·Day redraws itself through a `joined:` binding when entered at the baseline, and has a `low` drawing a `bind` can name; ·Utter is the trailing component of the ·Day+Utter ligature, whose `full` stance maps to ·Utter's `sole` and whose `bare` stance opts out.
"""

from __future__ import annotations

import dataclasses
import json
import os
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml

from rebuild.pipeline import conform, emit_gpos, geometry, kern, kernel_io, spec_load, surface
from rebuild.pipeline.model import CellId, ResolvedSpec, ss10_copy_name
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.table import DecisionTable, JoinRow, JoinTable

REGISTRY = """\
heights: {baseline: 0, x-height: 5, y6: 6, top: 8}
boundary_tokens:
  space: {codepoint: 0x0020, splits_runs: true}
features:
  ss10: {kind: taste, description: "isolated forms overlay", overlay: isolated}
predicate_classes:
  can-enter-at-baseline: {can_enter_at: baseline}
families:
  qsTea: {codepoint: 0xE652}
  qsDay: {codepoint: 0xE653}
  qsDay_qsUtter: {sequence: [qsDay, qsUtter]}
  qsNo: {codepoint: 0xE666}
  qsIt: {codepoint: 0xE670}
  qsUtter: {codepoint: 0xE67A}
"""

REGISTRY_WITH_ZWNJ = REGISTRY.replace(
    "  space: {codepoint: 0x0020, splits_runs: true}\n",
    "  space: {codepoint: 0x0020, splits_runs: true}\n  zwnj: {codepoint: 0x200C, splits_runs: true}\n",
)


def _rune(name: str, codepoint: int | None, stances: dict, **rest) -> dict:
    head: dict = {"rune": name}
    if codepoint is not None:
        head["codepoint"] = codepoint
    else:
        head["sequence"] = name.split("_")
    head["ductus"] = {stance: f"The {stance} motion." for stance in stances}
    for stance_name, stance in stances.items():
        stance.setdefault("motion", stance_name)
    return {**head, "stances": stances, **rest}


def _alphabet() -> dict[str, dict]:
    return {
        "qsIt": _rune(
            "qsIt",
            0xE670,
            {
                "sole": {
                    "bitmap": ["#"] * 6,
                    "surface": {
                        "entries": {"baseline": {"x": 0}},
                        "exits": {"baseline": {"x": 1, "unjoined": "safe"}},
                    },
                }
            },
        ),
        "qsNo": _rune(
            "qsNo",
            0xE666,
            {
                "loop": {
                    "bitmap": ["###"] + ["  #"] * 5,
                    "surface": {
                        "entries": {"baseline": {"x": 2}},
                        "exits": {"baseline": {"x": 3, "unjoined": "safe"}},
                    },
                },
                "flipped": {
                    "bitmap": ["###"] + ["#  "] * 5,
                    "surface": {
                        "entries": {"baseline": {"x": 0}},
                        "exits": {"x-height": {"x": 3, "unjoined": "safe"}},
                    },
                },
            },
        ),
        "qsTea": _rune(
            "qsTea",
            0xE652,
            {
                "full": {
                    "bitmap": ["#"] * 9,
                    "surface": {
                        "entries": {"baseline": {"x": 0}},
                        "exits": {"baseline": {"x": 1, "unjoined": "safe"}},
                    },
                },
                "half": {
                    "traits": ["half"],
                    "bitmap": ["#"] * 4 + [" "] * 2,
                    "surface": {"exits": {"x-height": {"x": 1, "unjoined": "safe"}}},
                },
            },
            policy={"pad_left": [{"stance": "half", "by": 1, "when": {"right": {"family": "qsIt"}}}]},
        ),
        "qsDay": _rune(
            "qsDay",
            0xE653,
            {
                "sole": {
                    "bitmap": ["#"] * 6,
                    "bitmaps": {
                        "reach": {"bitmap": ["# "] * 5 + ["##"]},
                        "low": {"bitmap": ["##"] * 6},
                    },
                    "surface": {
                        "entries": {"baseline": {"x": 0, "joined": "reach"}},
                        "exits": {"baseline": {"x": 1, "unjoined": "safe"}},
                    },
                }
            },
        ),
        "qsUtter": _rune(
            "qsUtter",
            0xE67A,
            {
                "sole": {
                    "bitmap": ["###"] + ["#  "] * 4 + ["###"],
                    "surface": {"entries": {"baseline": {"x": 0}}},
                }
            },
        ),
        "qsDay_qsUtter": _rune(
            "qsDay_qsUtter",
            None,
            {
                "full": {"outgoing": {"stance": "sole"}, "bitmap": ["####"] * 6},
                "bare": {
                    "outgoing": {"exception": "The bare drawing ends without the trailing stroke."},
                    "bitmap": ["#   "] * 6,
                },
            },
        ),
    }


def load(
    tmp_path: Path,
    kerns: dict[str, list[dict]] | None = None,
    alphabet: dict[str, dict] | None = None,
    registry_text: str = REGISTRY,
) -> ResolvedSpec:
    """Load the alphabet with each named rune's `kern:` block set to the given records, over `registry_text` (the test registry, which has no ZWNJ token, by default)."""
    runes_dir = tmp_path / "runes"
    runes_dir.mkdir(exist_ok=True)
    for name, document in (alphabet or _alphabet()).items():
        records = (kerns or {}).get(name)
        body = {**document, "kern": records} if records else document
        (runes_dir / f"{name}.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    registry = tmp_path / "script.yaml"
    registry.write_text(registry_text)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", spec_load.SpecWarning)
        return spec_load.load_spec(runes_dir, registry, spec_load.DEFAULT_SCHEMA_DIR)


def cell(
    rune: str, stance: str, entry: str | None = None, exit: str | None = None, *adjustments: str
) -> CellId:
    return CellId(rune, stance, entry, exit, tuple(adjustments))


IT = cell("qsIt", "sole")
NO_LOOP = cell("qsNo", "loop")
NO_FLIPPED = cell("qsNo", "flipped")
TEA_FULL = cell("qsTea", "full")
TEA_HALF = cell("qsTea", "half")
UTTER = cell("qsUtter", "sole")
LIGATURE_FULL = cell("qsDay_qsUtter", "full")
LIGATURE_BARE = cell("qsDay_qsUtter", "bare")


def tables(spec: ResolvedSpec, rows: dict[str, list[tuple[CellId, CellId, str]]]) -> dict[str, JoinTable]:
    return {
        config: JoinTable(
            config=config,
            rows=tuple(
                JoinRow(cell_label(spec, left), cell_label(spec, right), junction, 0)
                for left, right, junction in config_rows
            ),
        )
        for config, config_rows in rows.items()
    }


def glyphs(spec: ResolvedSpec, rows: dict[str, list[tuple[CellId, CellId, str]]]):
    cells = {
        side
        for config_rows in rows.values()
        for left, right, _junction in config_rows
        for side in (left, right)
    }
    return {
        one: geometry.realize(spec, surface.resolve_cell(spec, one), name=cell_label(spec, one))
        for one in cells
    }


def breaks(
    *pairs: tuple[CellId, CellId], config: str = "default"
) -> dict[str, list[tuple[CellId, CellId, str]]]:
    return {config: [(left, right, "break") for left, right in pairs]}


def resolve(
    spec: ResolvedSpec, rows: dict[str, list[tuple[CellId, CellId, str]]], ss10: bool = False
) -> kern.KernResolution:
    copies = (
        {name: ss10_copy_name(name) for name, rune in spec.runes.items() if rune.codepoint is not None}
        if ss10
        else None
    )
    return kern.resolve(spec, tables(spec, rows), glyphs(spec, rows), copies)


def values(resolution: kern.KernResolution) -> dict[tuple[str, str], int]:
    return {(pair.leading.name, pair.following.name): pair.by for pair in resolution.pairs}


def deciders(resolution: kern.KernResolution) -> dict[tuple[str, str], list[str]]:
    return {
        (pair.leading.name, pair.following.name): [str(decider) for decider in pair.deciders]
        for pair in resolution.pairs
    }


def unmatched(spec: ResolvedSpec, rows) -> list[str]:
    with pytest.raises(kern.KernError) as caught:
        resolve(spec, rows)
    return [message for code, message in caught.value.issues if code == "E-KERN-UNMATCHED"]


class TestTheBreakInvariant:
    def test_a_break_row_whose_left_cell_has_a_live_exit_is_refused(self, tmp_path):
        spec = load(tmp_path)
        with pytest.raises(kern.KernInternalError, match="qsIt.sole.ex-y0"):
            resolve(spec, breaks((cell("qsIt", "sole", None, "baseline"), TEA_FULL)))

    def test_a_join_row_with_a_declined_facing_side_is_refused(self, tmp_path):
        spec = load(tmp_path)
        with pytest.raises(kern.KernInternalError, match="exactly when"):
            resolve(spec, {"default": [(cell("qsIt", "sole", None, "baseline"), TEA_FULL, "baseline")]})

    def test_a_join_row_naming_no_minted_cell_is_refused(self, tmp_path):
        spec = load(tmp_path)
        table = {"default": JoinTable("default", (JoinRow("qsIt.sole", "qsTea.nope", "break", 0),))}
        with pytest.raises(kern.KernInternalError, match="qsTea.nope"):
            kern.resolve(spec, table, glyphs(spec, breaks((IT, TEA_FULL))))


class TestBreakOnlyMatching:
    def test_a_record_kerns_the_break_and_never_the_join_of_the_same_two_runes(self, tmp_path):
        spec = load(tmp_path, {"qsIt": [{"right": {"family": "qsTea"}, "by": -1}]})
        joined = (cell("qsIt", "sole", None, "baseline"), cell("qsTea", "full", "baseline"), "baseline")
        rows = {"default": [(IT, TEA_FULL, "break"), joined]}
        resolution = resolve(spec, rows)
        assert values(resolution) == {("qsIt", "qsTea"): -1}
        members = {member for pair in resolution.pairs for member in pair.members}
        assert members == {(IT, TEA_FULL)}
        assert joined[0] not in resolution.leading and joined[1] not in resolution.following


class TestSides:
    def test_a_rune_whose_cells_share_one_edge_is_one_side_named_by_the_family(self, tmp_path):
        spec = load(tmp_path)
        resolution = resolve(spec, breaks((IT, TEA_FULL), (cell("qsIt", "sole", "baseline"), TEA_FULL)))
        assert {side.name for side in resolution.leading.values()} == {"qsIt"}

    def test_stances_with_different_edges_are_sides_named_by_stance(self, tmp_path):
        spec = load(tmp_path)
        resolution = resolve(spec, breaks((NO_LOOP, IT), (NO_FLIPPED, IT), (IT, NO_LOOP), (IT, NO_FLIPPED)))
        assert {resolution.leading[NO_LOOP].name, resolution.leading[NO_FLIPPED].name} == {
            "qsNo[loop]",
            "qsNo[flipped]",
        }
        assert {resolution.following[NO_LOOP].name, resolution.following[NO_FLIPPED].name} == {
            "qsNo[loop]",
            "qsNo[flipped]",
        }

    def test_a_state_that_redraws_the_facing_edge_names_its_side(self, tmp_path):
        spec = load(tmp_path)
        entered = cell("qsDay", "sole", "baseline")
        resolution = resolve(spec, breaks((cell("qsDay", "sole"), IT), (entered, IT)))
        assert resolution.leading[cell("qsDay", "sole")].name == "qsDay[sole entry=none]"
        assert resolution.leading[entered].name == "qsDay[sole entry=baseline]"

    def test_a_bound_drawing_that_redraws_the_facing_edge_names_its_side(self, tmp_path):
        spec = load(tmp_path)
        bound = cell("qsDay", "sole", "baseline", None, "en-bind-low")
        resolution = resolve(
            spec, breaks((cell("qsDay", "sole"), IT), (cell("qsDay", "sole", "baseline"), IT), (bound, IT))
        )
        assert resolution.leading[bound].name == "qsDay[sole entry=baseline bitmap=low]"
        assert resolution.leading[cell("qsDay", "sole", "baseline")].name == "qsDay[sole entry=baseline]"

    def test_a_padded_copy_names_a_following_side_by_its_bound_drawing(self, tmp_path):
        spec = load(tmp_path)
        padded = cell("qsTea", "half", None, "x-height", "ex-bind-padded-left-1")
        plain = cell("qsTea", "half", None, "x-height")
        resolution = resolve(spec, breaks((IT, TEA_FULL), (IT, TEA_HALF), (IT, plain), (IT, padded)))
        names = {one: resolution.following[one].name for one in (TEA_FULL, TEA_HALF, plain, padded)}
        assert names == {
            TEA_FULL: "qsTea[full]",
            TEA_HALF: "qsTea[half exit=none]",
            plain: "qsTea[half exit=x-height]",
            padded: "qsTea[half exit=x-height bitmap=padded-left-1]",
        }

    def test_cells_that_differ_only_by_a_trim_are_one_mixed_side(self, tmp_path):
        spec = load(tmp_path)
        entered = cell("qsIt", "sole", "baseline")
        trimmed = cell("qsIt", "sole", "baseline", None, "en-trim-1")
        resolution = resolve(spec, breaks((IT, TEA_FULL), (entered, TEA_FULL), (trimmed, TEA_FULL)))
        side = resolution.leading[trimmed]
        assert side == resolution.leading[entered] and side.name == "qsIt[sole entry=baseline]" and side.mixed
        assert not resolution.leading[IT].mixed
        assert [pair.mixed for pair in resolution.pairs if pair.leading == side] == ["leading"]

    def test_the_edges_are_measured_against_the_compiled_advance(self, tmp_path):
        spec = load(tmp_path)
        record = glyphs(spec, breaks((NO_FLIPPED, IT)))[NO_FLIPPED]
        assert kern.advance(record) == 4
        assert kern.leading_edge(record) == ((0, 2), (1, 2), (2, 2), (3, 2), (4, 2), (5, 0))
        assert kern.following_edge(record) == tuple((y, 1) for y in range(6))


class TestPrecedence:
    def test_the_narrower_record_decides_and_a_zero_cancels_the_broader_one(self, tmp_path):
        spec = load(
            tmp_path,
            {
                "qsNo": [
                    {"right": {"family": "qsTea"}, "by": -2},
                    {"stance": "flipped", "right": {"family": "qsTea"}, "by": 0},
                    {"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -1},
                ]
            },
        )
        resolution = resolve(
            spec, breaks((NO_LOOP, TEA_FULL), (NO_FLIPPED, TEA_FULL), (NO_FLIPPED, TEA_HALF))
        )
        assert values(resolution) == {
            ("qsNo[loop]", "qsTea[full]"): -2,
            ("qsNo[flipped]", "qsTea[full]"): 0,
            ("qsNo[flipped]", "qsTea[half]"): -1,
        }
        assert deciders(resolution)[("qsNo[flipped]", "qsTea[full]")][0].endswith("qsNo.yaml:kern[1]")

    def test_overlapping_records_that_do_not_nest_and_disagree_are_incomparable(self, tmp_path):
        spec = load(
            tmp_path,
            {
                "qsNo": [
                    {"stance": "flipped", "right": {"family": ["qsTea", "qsIt"]}, "by": -1},
                    {"right": {"family": "qsTea"}, "by": -2},
                ]
            },
        )
        with pytest.raises(kern.KernError) as caught:
            resolve(spec, breaks((NO_LOOP, TEA_FULL), (NO_FLIPPED, TEA_FULL), (NO_FLIPPED, IT)))
        (issue,) = caught.value.issues
        assert issue[0] == "E-KERN-INCOMPARABLE"
        assert "kern[0]" in issue[1] and "kern[1]" in issue[1] and "qsNo.flipped | qsTea.full" in issue[1]

    def test_records_with_the_same_match_set_and_different_values_are_incomparable(self, tmp_path):
        spec = load(
            tmp_path,
            {
                "qsIt": [
                    {"right": {"family": "qsTea"}, "by": -1},
                    {"right": {"family": "qsTea", "class": "can-enter-at-baseline"}, "by": -2},
                ]
            },
        )
        with pytest.raises(kern.KernError, match="exactly the same breaks"):
            resolve(spec, breaks((IT, TEA_FULL)))

    def test_overlapping_records_that_agree_never_conflict(self, tmp_path):
        spec = load(
            tmp_path,
            {
                "qsNo": [
                    {"stance": "flipped", "right": {"family": ["qsTea", "qsIt"]}, "by": -1},
                    {"right": {"family": "qsTea"}, "by": -1},
                ]
            },
        )
        resolution = resolve(spec, breaks((NO_LOOP, TEA_FULL), (NO_FLIPPED, TEA_FULL), (NO_FLIPPED, IT)))
        assert set(values(resolution).values()) == {-1}
        assert len(deciders(resolution)[("qsNo[flipped]", "qsTea")]) == 2

    def test_an_except_entry_carves_its_followers_out(self, tmp_path):
        spec = load(
            tmp_path,
            {"qsIt": [{"right": {"family": "qsTea", "except": [{"stance": "half"}]}, "by": -1}]},
        )
        resolution = resolve(spec, breaks((IT, TEA_FULL), (IT, TEA_HALF)))
        assert values(resolution) == {("qsIt", "qsTea[full]"): -1, ("qsIt", "qsTea[half]"): 0}

    def test_a_record_narrower_than_a_side_is_refused(self, tmp_path):
        spec = load(tmp_path, {"qsIt": [{"entry": "baseline", "right": {"family": "qsTea"}, "by": -1}]})
        with pytest.raises(kern.KernError, match="never narrower") as caught:
            resolve(spec, breaks((IT, TEA_FULL), (cell("qsIt", "sole", "baseline"), TEA_FULL)))
        assert [code for code, _message in caught.value.issues] == ["E-KERN-NARROWER"]


class TestUnmatched:
    def test_a_record_whose_only_pairs_join_is_unmatched(self, tmp_path):
        spec = load(
            tmp_path,
            {"qsIt": [{"right": {"family": "qsTea"}, "by": -1}, {"right": {"family": "qsNo"}, "by": -1}]},
        )
        joined = (cell("qsIt", "sole", None, "baseline"), cell("qsTea", "full", "baseline"), "baseline")
        (message,) = unmatched(spec, {"default": [joined, (IT, NO_LOOP, "break")]})
        assert "qsIt.yaml:kern[0]" in message and "{right: {family: qsTea}, by: -1}" in message

    # ·Day is a rune the narrowed alphabet drops, and ·Tea one it keeps whose half stance meets no break, as fault-kit row 01's alphabet keeps ·Pea but never settles a half ·Pea exiting at the x-height.
    STALE_ON_A_SMALL_ALPHABET = {
        "qsIt": [{"right": {"family": "qsDay"}, "by": -1}],
        "qsNo": [
            {
                "stance": "flipped",
                "right": {"family": "qsTea", "stance": "half", "exit": "x-height"},
                "by": -2,
            }
        ],
    }
    SMALL_ALPHABET_BREAKS = ((IT, NO_LOOP), (NO_FLIPPED, TEA_FULL))

    def test_a_narrowed_alphabet_reports_no_record_unmatched(self, tmp_path):
        spec = spec_load.narrowed(load(tmp_path, self.STALE_ON_A_SMALL_ALPHABET), ["qsIt", "qsNo", "qsTea"])
        assert spec.narrowed
        assert values(resolve(spec, breaks(*self.SMALL_ALPHABET_BREAKS))) == {
            ("qsIt", "qsNo"): 0,
            ("qsNo", "qsTea"): 0,
        }

    def test_the_whole_alphabet_reports_the_same_records_unmatched(self, tmp_path):
        spec = load(tmp_path, self.STALE_ON_A_SMALL_ALPHABET)
        assert not spec.narrowed
        messages = unmatched(spec, breaks(*self.SMALL_ALPHABET_BREAKS))
        assert len(messages) == 2
        assert any("qsIt.yaml:kern[0]" in message for message in messages)
        assert any(
            "qsNo.yaml:kern[0] {stance: flipped, right: {family: qsTea, stance: half, exit: x-height}, by: -2}"
            in message
            for message in messages
        )

    def test_a_narrowed_alphabet_still_refuses_incomparable_records(self, tmp_path):
        """The third record names ·Tea, which the narrowed alphabet keeps, and matches no break: the narrowed spec reports the two incomparable records and not it, and the same spec unmarked reports all three."""
        spec = spec_load.narrowed(
            load(
                tmp_path,
                {
                    "qsIt": [
                        {"right": {"family": ["qsTea", "qsNo"]}, "by": -1},
                        {"right": {"family": ["qsTea", "qsIt"]}, "by": -2},
                        {"right": {"family": "qsTea", "stance": "half"}, "by": -1},
                    ]
                },
            ),
            ["qsIt", "qsNo", "qsTea"],
        )
        rows = breaks((IT, TEA_FULL), (IT, NO_LOOP), (IT, IT))
        with pytest.raises(kern.KernError) as caught:
            resolve(spec, rows)
        assert [code for code, _message in caught.value.issues] == ["E-KERN-INCOMPARABLE"]
        with pytest.raises(kern.KernError) as caught:
            resolve(dataclasses.replace(spec, narrowed=False), rows)
        assert [code for code, _message in caught.value.issues] == ["E-KERN-INCOMPARABLE", "E-KERN-UNMATCHED"]
        assert "qsIt.yaml:kern[2]" in caught.value.issues[1][1]

    def test_a_record_whose_class_holds_none_of_its_families_is_not_unmatched_on_the_whole_alphabet(
        self, tmp_path
    ):
        """On a spec holding the whole store, every family a record names is a rune of the spec, so a record whose `right` names no rune of the spec is one whose `class` has no member among its families. Such a record matches no break and is not reported unmatched (`_Matcher.nameable`)."""
        registry = REGISTRY.replace(
            "  can-enter-at-baseline: {can_enter_at: baseline}\n",
            "  can-enter-at-baseline: {can_enter_at: baseline}\n  can-enter-at-x-height: {can_enter_at: x-height}\n",
        )
        spec = load(
            tmp_path,
            {"qsIt": [{"right": {"family": "qsTea", "class": "can-enter-at-x-height"}, "by": -1}]},
            registry_text=registry,
        )
        assert not spec.narrowed
        assert "qsTea" not in spec.registry.predicate_classes["can-enter-at-x-height"]
        assert values(resolve(spec, breaks((IT, TEA_FULL)))) == {("qsIt", "qsTea"): 0}


class TestLigatureInheritance:
    UTTER_KERN = [
        {"right": {"family": "qsIt"}, "by": -1},
        {"stance": "sole", "right": {"family": "qsNo"}, "by": -3},
        {"entry": "none", "right": {"family": "qsTea"}, "by": -2},
    ]

    def test_the_mapped_stance_takes_the_records_naming_its_source_stance_or_none(self, tmp_path):
        spec = load(tmp_path, {"qsUtter": self.UTTER_KERN})
        assert dict(spec.runes["qsDay_qsUtter"].kern.inherits) == {"full": "sole"}
        rows = breaks(
            (UTTER, IT),
            (UTTER, NO_LOOP),
            (UTTER, TEA_FULL),
            (LIGATURE_FULL, IT),
            (LIGATURE_FULL, NO_LOOP),
            (LIGATURE_FULL, TEA_FULL),
        )
        resolution = resolve(spec, rows)
        assert values(resolution)[("qsDay_qsUtter", "qsIt")] == -1
        assert values(resolution)[("qsDay_qsUtter", "qsNo")] == -3
        assert deciders(resolution)[("qsDay_qsUtter", "qsIt")] == [
            str(spec.runes["qsUtter"].kern.records[0].provenance) + kern.INHERITED_MARK
        ]

    def test_entry_and_bitmap_records_stay_with_the_component(self, tmp_path):
        spec = load(tmp_path, {"qsUtter": self.UTTER_KERN})
        resolution = resolve(
            spec, breaks((UTTER, IT), (UTTER, NO_LOOP), (UTTER, TEA_FULL), (LIGATURE_FULL, TEA_FULL))
        )
        assert values(resolution)[("qsUtter", "qsTea")] == -2
        assert values(resolution)[("qsDay_qsUtter", "qsTea")] == 0

    def test_an_opted_out_stance_inherits_nothing(self, tmp_path):
        spec = load(tmp_path, {"qsUtter": self.UTTER_KERN})
        rows = breaks(
            (UTTER, IT), (UTTER, NO_LOOP), (UTTER, TEA_FULL), (LIGATURE_FULL, IT), (LIGATURE_BARE, IT)
        )
        found = values(resolve(spec, rows))
        assert found[("qsDay_qsUtter[full]", "qsIt")] == -1
        assert found[("qsDay_qsUtter[bare]", "qsIt")] == 0

    def test_every_record_of_the_ligatures_own_block_outranks_every_inherited_one(self, tmp_path):
        spec = load(
            tmp_path,
            {
                "qsUtter": self.UTTER_KERN,
                "qsDay_qsUtter": [{"right": {"class": "can-enter-at-baseline"}, "by": 2}],
            },
        )
        rows = breaks(
            (UTTER, IT), (UTTER, NO_LOOP), (UTTER, TEA_FULL), (LIGATURE_FULL, IT), (LIGATURE_FULL, NO_LOOP)
        )
        found = values(resolve(spec, rows))
        assert found[("qsDay_qsUtter", "qsIt")] == 2
        assert found[("qsDay_qsUtter", "qsNo")] == 2

    def test_a_component_record_reached_only_through_the_ligature_is_matched(self, tmp_path):
        spec = load(tmp_path, {"qsUtter": [{"right": {"family": "qsIt"}, "by": -1}]})
        assert values(resolve(spec, breaks((LIGATURE_FULL, IT)))) == {("qsDay_qsUtter", "qsIt"): -1}

    def test_an_unmatched_component_record_names_the_ligature_stances_that_inherit_it(self, tmp_path):
        spec = load(tmp_path, {"qsUtter": [{"right": {"family": "qsTea"}, "by": -1}]})
        (message,) = unmatched(spec, breaks((UTTER, IT), (LIGATURE_FULL, IT)))
        assert "qsDay_qsUtter.full" in message


class TestTheIsolatedOverlay:
    def test_two_letters_that_always_join_get_a_facing_pair_only_ss10_reaches(self, tmp_path):
        spec = load(tmp_path, {"qsIt": [{"right": {"family": "qsTea"}, "by": -1}]})
        joined = (cell("qsIt", "sole", None, "baseline"), cell("qsTea", "full", "baseline"), "baseline")
        resolution = resolve(spec, {"default": [joined, (IT, NO_LOOP, "break")]}, ss10=True)
        (pair,) = [
            pair for pair in resolution.pairs if (pair.leading.name, pair.following.name) == ("qsIt", "qsTea")
        ]
        assert pair.configs == ("ss10",) and pair.by == -1 and pair.members == ((IT, TEA_FULL),)

    def test_an_ss10_pair_joins_the_facing_pair_its_source_cells_already_meet_in(self, tmp_path):
        spec = load(tmp_path)
        resolution = resolve(spec, breaks((IT, NO_LOOP)), ss10=True)
        (pair,) = [
            pair for pair in resolution.pairs if (pair.leading.name, pair.following.name) == ("qsIt", "qsNo")
        ]
        assert pair.configs == ("default", "ss10")

    def test_ss10_covers_every_ordered_pair_of_letters_and_no_ligature(self, tmp_path):
        spec = load(tmp_path)
        resolution = resolve(spec, {}, ss10=True)
        letters = [name for name, rune in spec.runes.items() if rune.codepoint is not None]
        reached = {(left.rune, right.rune) for pair in resolution.pairs for left, right in pair.members}
        assert reached == {(left, right) for left in letters for right in letters}

    def test_each_copy_takes_the_sides_of_the_cell_it_copies(self, tmp_path):
        spec = load(tmp_path)
        rows = breaks((NO_FLIPPED, IT), (NO_LOOP, IT))
        copies = {
            name: ss10_copy_name(name) for name, rune in spec.runes.items() if rune.codepoint is not None
        }
        realized = glyphs(spec, rows)
        resolution = kern.resolve(spec, tables(spec, rows), realized, copies)
        assert resolution.ss10_sources["qsNo.ss10"] == NO_LOOP
        leading, following = resolution.glyph_sides(realized)
        assert leading["qsNo.ss10"] == resolution.leading[NO_LOOP]
        assert following["qsNo.ss10"] == resolution.following[NO_LOOP]
        assert leading["qsNo.flipped"].name == "qsNo[flipped]"

    def test_a_record_whose_only_matches_are_ss10_pairs_is_not_unmatched(self, tmp_path):
        spec = load(tmp_path, {"qsNo": [{"right": {"family": "qsUtter"}, "by": -2}]})
        resolution = resolve(spec, breaks((NO_LOOP, IT)), ss10=True)
        assert values(resolution)[("qsNo", "qsUtter")] == -2


class TestTheCoverageTable:
    def test_rows_follow_code_point_order_with_the_ligature_after_its_lead(self, tmp_path):
        spec = load(tmp_path, {"qsUtter": [{"right": {"family": "qsIt"}, "by": -1}]})
        rows = breaks(
            (LIGATURE_FULL, IT), (cell("qsDay", "sole"), IT), (UTTER, IT), (NO_LOOP, TEA_FULL), (IT, NO_LOOP)
        )
        resolution = kern.run_stage(spec, tables(spec, rows), glyphs(spec, rows), tmp_path)
        lines = (tmp_path / kern.KERN_TSV).read_text().splitlines()
        assert lines[0] == kern.TSV_COMMENT
        assert lines[1].split("\t") == list(kern.TSV_COLUMNS)
        assert [line.split("\t")[0] for line in lines[2:]] == [
            "qsDay",
            "qsDay_qsUtter",
            "qsNo",
            "qsIt",
            "qsUtter",
        ]
        assert [line.split("\t")[0] for line in lines[2:]] == [pair.leading.rune for pair in resolution.pairs]
        inherited = next(line.split("\t") for line in lines[2:] if line.startswith("qsDay_qsUtter"))
        assert inherited[4] == "-1" and inherited[5].endswith("qsUtter.yaml:kern[0]" + kern.INHERITED_MARK)
        assert inherited[6] == "default" and inherited[7] == ""

    def test_the_rune_order_puts_each_ligature_after_its_lead(self, tmp_path):
        spec = load(tmp_path)
        ordered = sorted(spec.runes, key=lambda name: kern.rune_order_key(spec, name))
        assert ordered == ["qsTea", "qsDay", "qsDay_qsUtter", "qsNo", "qsIt", "qsUtter"]

    def test_an_unchanged_table_is_left_in_place(self, tmp_path):
        spec = load(tmp_path)
        rows = breaks((IT, NO_LOOP))
        kern.run_stage(spec, tables(spec, rows), glyphs(spec, rows), tmp_path)
        written = tmp_path / kern.KERN_TSV
        os.utime(written, ns=(1_000_000_000, 1_000_000_000))
        kern.run_stage(spec, tables(spec, rows), glyphs(spec, rows), tmp_path)
        assert written.stat().st_mtime_ns == 1_000_000_000

    def test_a_failing_stage_writes_no_table(self, tmp_path):
        spec = load(tmp_path, {"qsIt": [{"right": {"family": "qsTea"}, "by": -1}]})
        rows = breaks((IT, NO_LOOP))
        with pytest.raises(kern.KernError):
            kern.run_stage(spec, tables(spec, rows), glyphs(spec, rows), tmp_path)
        assert not (tmp_path / kern.KERN_TSV).exists()


class TestKerningStaysOutOfTheKernel:
    def test_a_kern_block_leaves_the_spec_dump_byte_for_byte(self, tmp_path):
        bare = load(tmp_path)
        kerned = load(
            tmp_path,
            {
                "qsNo": [{"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -1}],
                "qsUtter": [{"right": {"family": "qsIt"}, "by": -1}],
            },
        )
        assert kerned.runes["qsNo"].kern.records and not bare.runes["qsNo"].kern.records
        assert kernel_io.spec_bytes(kerned) == kernel_io.spec_bytes(bare)

    def test_a_parsed_dump_carries_no_kern_records(self, tmp_path):
        kerned = load(tmp_path, {"qsIt": [{"right": {"family": "qsTea"}, "by": -1}]})
        parsed = kernel_io.spec_of(kernel_io.spec_json(kerned))
        assert parsed.runes["qsIt"].kern.records == ()
        assert kernel_io.spec_json(parsed) == kernel_io.spec_json(kerned)


def zwnj_tables(
    spec: ResolvedSpec,
    rows: dict[str, list[tuple[CellId, CellId, str]]],
    before: dict[str, list[CellId]],
    after: dict[str, list[CellId]],
) -> dict[str, tuple[DecisionTable, JoinTable]]:
    """Each configuration's join table beside a decision table whose head lists `before` and `after` as the cells settled beside a ZWNJ."""
    joins = tables(spec, {config: rows.get(config, []) for config in {*rows, *before, *after}})
    return {
        config: (
            DecisionTable(
                config=config,
                zwnj_before=frozenset(cell_label(spec, one) for one in before.get(config, ())),
                zwnj_after=frozenset(cell_label(spec, one) for one in after.get(config, ())),
            ),
            table,
        )
        for config, table in joins.items()
    }


def realized(spec: ResolvedSpec, cells) -> dict:
    return {
        one: geometry.realize(spec, surface.resolve_cell(spec, one), name=cell_label(spec, one))
        for one in cells
    }


TEA_FULL_LOCKED = cell("qsTea", "full", None, None, "locked")


class TestTheZwnjPairs:
    def test_every_cell_before_a_zwnj_meets_every_cell_after_one(self, tmp_path):
        spec = load(tmp_path, {"qsNo": [{"right": {"family": "qsTea"}, "by": -1}]})
        rows = breaks((IT, TEA_FULL))
        built = zwnj_tables(spec, rows, {"default": [NO_LOOP, IT]}, {"default": [TEA_FULL_LOCKED]})
        resolution = kern.resolve(spec, built, realized(spec, {IT, TEA_FULL, NO_LOOP, TEA_FULL_LOCKED}))
        members = {member for pair in resolution.pairs for member in pair.members}
        assert {(NO_LOOP, TEA_FULL_LOCKED), (IT, TEA_FULL_LOCKED), (IT, TEA_FULL)} <= members
        assert values(resolution)[("qsNo", "qsTea")] == -1
        (pair,) = [pair for pair in resolution.pairs if pair.leading.rune == "qsNo"]
        assert pair.configs == ("default",) and pair.members == ((NO_LOOP, TEA_FULL_LOCKED),)

    def test_a_locked_follower_takes_the_side_of_its_twin(self, tmp_path):
        spec = load(tmp_path)
        built = zwnj_tables(spec, breaks((IT, TEA_FULL)), {"default": [IT]}, {"default": [TEA_FULL_LOCKED]})
        resolution = kern.resolve(spec, built, realized(spec, {IT, TEA_FULL, TEA_FULL_LOCKED}))
        assert resolution.following[TEA_FULL_LOCKED] == resolution.following[TEA_FULL]

    @pytest.mark.parametrize("empty", ["before", "after", "both"])
    def test_a_head_that_lists_no_cell_on_one_side_of_a_zwnj_is_refused_when_a_letter_can_settle_there(
        self, tmp_path, empty
    ):
        """A registry with the ZWNJ token and an alphabet with a letter settle a cell on each side of a ZWNJ, so an empty list means the crate missed them, and every pair across a ZWNJ would go unkerned."""
        spec = load(tmp_path, registry_text=REGISTRY_WITH_ZWNJ)
        assert kern.zwnj_reachable(spec)
        rows = breaks((IT, TEA_FULL))
        before = {"default": [] if empty in ("before", "both") else [IT]}
        after = {"default": [] if empty in ("after", "both") else [TEA_FULL_LOCKED]}
        built = zwnj_tables(spec, rows, before, after)
        with pytest.raises(kern.KernInternalError, match="lists no cell"):
            kern.resolve(spec, built, realized(spec, {IT, TEA_FULL, TEA_FULL_LOCKED}))

    def test_an_empty_zwnj_list_stands_when_the_registry_has_no_zwnj_token(self, tmp_path):
        """The test registry has no ZWNJ token, so no letter can settle beside one, and a head with no ZWNJ cells adds no breaks and raises nothing."""
        spec = load(tmp_path)
        assert not kern.zwnj_reachable(spec)
        rows = breaks((IT, TEA_FULL))
        built = zwnj_tables(spec, rows, {"default": []}, {"default": []})
        resolution = kern.resolve(spec, built, realized(spec, {IT, TEA_FULL}))
        assert {member for pair in resolution.pairs for member in pair.members} == {(IT, TEA_FULL)}

    def test_a_head_that_lists_no_zwnj_cells_is_refused(self, tmp_path):
        spec = load(tmp_path)
        rows = breaks((IT, TEA_FULL))
        built = {"default": (DecisionTable(config="default"), tables(spec, rows)["default"])}
        with pytest.raises(kern.KernInternalError, match="lists no ZWNJ cells"):
            kern.resolve(spec, built, glyphs(spec, rows))

    def test_a_cell_beside_a_zwnj_with_a_live_side_facing_it_is_refused(self, tmp_path):
        spec = load(tmp_path)
        exiting = cell("qsIt", "sole", None, "baseline")
        built = zwnj_tables(spec, breaks((IT, TEA_FULL)), {"default": [exiting]}, {"default": [TEA_FULL]})
        with pytest.raises(kern.KernInternalError, match="live exit"):
            kern.resolve(spec, built, realized(spec, {IT, TEA_FULL, exiting}))

    def test_member_values_hold_each_kerned_break(self, tmp_path):
        spec = load(tmp_path, {"qsNo": [{"right": {"family": "qsTea"}, "by": -1}]})
        built = zwnj_tables(
            spec, breaks((IT, TEA_FULL)), {"default": [NO_LOOP]}, {"default": [TEA_FULL_LOCKED]}
        )
        resolution = kern.resolve(spec, built, realized(spec, {IT, TEA_FULL, NO_LOOP, TEA_FULL_LOCKED}))
        assert resolution.member_values() == {(NO_LOOP, TEA_FULL_LOCKED): -1}


class TestTheKernPlan:
    def _resolution(self, tmp_path, kerns):
        spec = load(tmp_path, kerns)
        rows = breaks((NO_LOOP, IT), (NO_FLIPPED, IT), (NO_LOOP, TEA_FULL), (IT, TEA_FULL), (IT, NO_LOOP))
        cells = realized(spec, {IT, NO_LOOP, NO_FLIPPED, TEA_FULL})
        return spec, kern.resolve(spec, tables(spec, rows), cells), cells

    def test_leading_sides_with_one_row_share_a_rule_pair_and_the_relation_is_every_member(self, tmp_path):
        spec, resolution, cells = self._resolution(
            tmp_path,
            {"qsNo": [{"right": {"family": "qsIt"}, "by": -1}]},
        )
        plan = emit_gpos.kern_plan(resolution, cells)
        (rule,) = plan.rules
        assert rule.value == -50
        assert set(rule.lead) == {"qsNo.loop", "qsNo.flipped"}
        assert rule.follow == ("qsIt.sole",)
        assert plan.relation() == {
            (cells[left].name, cells[right].name): pair.by * emit_gpos.PIXEL
            for pair in resolution.pairs
            if pair.by
            for left, right in pair.members
        }
        text = emit_gpos.emit_kern(plan)
        assert f"pos {rule.lead_class}' lookup m1_kern_value_m50 {rule.follow_class};" in text
        assert f"pos {rule.lead_class}' lookup m1_kern_value_m50 uni200C {rule.follow_class};" in text
        assert "uni200C uni200C" not in text
        assert plan.behavior_classes() == emit_gpos.KERN_BEHAVIOR_CLASSES

    def test_a_plan_with_no_kerned_pair_emits_no_kern_feature(self, tmp_path):
        _spec, resolution, cells = self._resolution(tmp_path, {})
        plan = emit_gpos.kern_plan(resolution, cells)
        assert plan.rules == () and plan.behavior_classes() == ()
        assert emit_gpos.emit_kern(plan) == ""
        assert emit_gpos.emit_gpos(cells, kern=plan) == emit_gpos.emit_gpos(cells)

    def test_a_follower_with_a_curs_entry_anchor_is_refused_with_what_to_change(self, tmp_path):
        _spec, resolution, cells = self._resolution(
            tmp_path, {"qsNo": [{"right": {"family": "qsIt"}, "by": -1}]}
        )
        anchored = {
            one: dataclasses.replace(record, entry_curs_only=(0, 0)) if one == IT else record
            for one, record in cells.items()
        }
        with pytest.raises(emit_gpos.KernPlanError, match="selectable: true"):
            emit_gpos.kern_plan(resolution, anchored)

    def test_a_leading_glyph_with_an_exit_anchor_is_refused(self, tmp_path):
        _spec, resolution, cells = self._resolution(
            tmp_path, {"qsNo": [{"right": {"family": "qsIt"}, "by": -1}]}
        )
        exiting = {
            one: dataclasses.replace(record, exit=(3, 0)) if one == NO_LOOP else record
            for one, record in cells.items()
        }
        with pytest.raises(emit_gpos.KernPlanError, match="live exit"):
            emit_gpos.kern_plan(resolution, exiting)


class TestTheClassesDocument:
    def test_the_document_names_every_glyph_s_sides_and_each_kerned_pair(self, tmp_path):
        spec = load(tmp_path, {"qsNo": [{"right": {"family": "qsIt"}, "by": -1}]})
        rows = breaks((NO_LOOP, IT), (IT, NO_LOOP))
        cells = realized(spec, {IT, NO_LOOP})
        resolution = kern.resolve(spec, tables(spec, rows), cells)
        kern.write_classes(resolution, cells, tmp_path)
        document = json.loads((tmp_path / kern.KERN_CLASSES_JSON).read_text())
        assert document["format"] == kern.KERN_CLASSES_FORMAT == conform.KERN_CLASSES_FORMAT
        assert document["leading"] == {"qsIt.sole": "qsIt", "qsNo.loop": "qsNo"}
        assert document["values"] == [["qsNo", "qsIt", -1], ["qsIt", "qsNo", 0]]
        sides = conform.load_kern_sides(tmp_path / kern.KERN_CLASSES_JSON)
        assert sides.value("qsNo.loop", "qsIt.sole") == -50
        assert sides.value("qsIt.sole", "qsNo.loop") == 0
        assert sides.value("qsIt.sole", "qsTea.full") is None

    def test_a_break_missing_from_the_stage_s_list_is_a_kern_side_divergence_though_both_glyphs_have_sides(
        self, tmp_path
    ):
        """The stage is handed `·No ·It` and `·It ·No` but not `·It ·It`, so ·It has a leading side and a following side, each from another break, and the two never face. HarfBuzz shaping `·It ·It` reaches that break, which the conformance sweep reports as `kern-side` instead of reading it as an unkerned facing pair."""
        spec = load(tmp_path, {"qsNo": [{"right": {"family": "qsIt"}, "by": -1}]})
        rows = breaks((NO_LOOP, IT), (IT, NO_LOOP))
        cells = realized(spec, {IT, NO_LOOP})
        kern.write_classes(kern.resolve(spec, tables(spec, rows), cells), cells, tmp_path)
        sides = conform.load_kern_sides(tmp_path / kern.KERN_CLASSES_JSON)
        assert "qsIt.sole" in sides.leading and "qsIt.sole" in sides.following
        assert sides.value("qsIt.sole", "qsIt.sole") is None

        advances = {"qsIt.sole": 100, "qsNo.loop": 250}
        shaper = cast(conform.Shaper, SimpleNamespace(advance=advances.__getitem__))

        def slot(name: str, index: int) -> dict:
            return {"name": name, "cluster": index, "x_advance": advances[name], "x_offset": 0, "y_offset": 0}

        divergences: list = []
        shaped = [slot("qsIt.sole", 0), slot("qsIt.sole", 1)]
        conform.check_break_advances("\ue670\ue670", "default", shaper, shaped, None, sides, divergences)
        assert [divergence.kind for divergence in divergences] == ["kern-side"]
        kept: list = []
        unkerned = [slot("qsIt.sole", 0), slot("qsNo.loop", 1)]
        conform.check_break_advances("\ue670\ue666", "default", shaper, unkerned, None, sides, kept)
        assert kept == []
