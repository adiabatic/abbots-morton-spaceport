"""Tests for the kern stage, `rebuild/pipeline/kern.py` (rebuild/KERN-PLAN.md sections 2 to 4).

Every test loads a small hand-written alphabet through the real loader (`spec_load.load_spec` over YAML written under `tmp_path`) and hands the stage join tables built in the test, so the stage's inputs are exactly the cells each test names and no test reads a live artifact. The glyphs are realized from those cells as `run_m1.mint_cell_glyphs` realizes them, named by their cell labels.

The alphabet: ·It is a one-pixel bar; ·No has a `loop` and a `flipped` stance whose right-hand edges differ; ·Tea has a `full` and a `half` stance, and its half has a `pad_left` record, so a half ·Tea can be drawn from the `padded-left-1` copy; ·Day redraws itself through a `joined:` binding when entered at the baseline, and has a `low` drawing a `bind` can name; ·Utter is the trailing component of the ·Day+Utter ligature, whose `full` stance maps to ·Utter's `sole` and whose `bare` stance opts out.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import geometry, kern, kernel_io, spec_load, surface
from rebuild.pipeline.model import CellId, ResolvedSpec, ss10_copy_name
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.table import JoinRow, JoinTable

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
    tmp_path: Path, kerns: dict[str, list[dict]] | None = None, alphabet: dict[str, dict] | None = None
) -> ResolvedSpec:
    """Load the alphabet with each named rune's `kern:` block set to the given records."""
    runes_dir = tmp_path / "runes"
    runes_dir.mkdir(exist_ok=True)
    for name, document in (alphabet or _alphabet()).items():
        records = (kerns or {}).get(name)
        body = {**document, "kern": records} if records else document
        (runes_dir / f"{name}.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    registry = tmp_path / "script.yaml"
    registry.write_text(REGISTRY)
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

    def test_a_record_naming_only_runes_a_narrowed_alphabet_drops_is_not_unmatched(self, tmp_path):
        spec = spec_load.narrowed(
            load(tmp_path, {"qsIt": [{"right": {"family": "qsTea"}, "by": -1}]}), ["qsIt", "qsNo"]
        )
        assert values(resolve(spec, breaks((IT, NO_LOOP)))) == {("qsIt", "qsNo"): 0}


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
