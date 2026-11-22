"""Tests for the kern inventory, `rebuild/pipeline/kern_inventory.py` (rebuild/KERN-PLAN.md sections 4 and 6).

Every test runs over rebuild/test_kern.py's small hand-written alphabet, loaded through the real loader, with join tables and ZWNJ cells the test names, and settles its texts through a stand-in for the kernel that answers the texts the test lists and refuses the rest, so the witness ladder's choices are the test's. The build's own run of the inventory settles through the kernel and checks every text against HarfBuzz on the compiled font (`check_positions`), which these tests drive through a stand-in shaper. The app's fixture, `rebuild/review/fixtures/kern-inventory.json`, is a slice of a built inventory, and the last test holds it to the inventory's shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rebuild.pipeline import geometry, kern, kern_inventory
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.model import CellId, ss10_copy_name
from rebuild.pipeline.settle import cell_label
from rebuild.test_kern import IT, NO_LOOP, TEA_FULL, TEA_HALF, cell, load, realized, zwnj_tables

FIXTURE = Path(__file__).resolve().parent / "review" / "fixtures" / "kern-inventory.json"

IT_OUT = cell("qsIt", "sole", None, "baseline")
NO_IN = cell("qsNo", "loop", "baseline", None)
NO_FLIPPED_IN = cell("qsNo", "flipped", "baseline", None)
UTTER = cell("qsUtter", "sole")

NO = 0xE666
TEA = 0xE652
IT_LETTER = 0xE670
UTTER_LETTER = 0xE67A
ZWNJ = 0x200C


def copies(spec) -> dict[str, str]:
    return {name: ss10_copy_name(name) for name, rune in spec.runes.items() if rune.codepoint is not None}


class World:
    """The alphabet with default's join rows: ·No before full ·Tea and flipped ·No before half ·Tea break, ·It joins ·No at the baseline, and full ·Tea before a ZWNJ meets ·It after one."""

    def __init__(self, tmp_path: Path, overlay: bool = True, extra_breaks: tuple = ()) -> None:
        self.spec = load(tmp_path)
        rows = {
            "default": [
                (NO_LOOP, TEA_FULL, "break"),
                (NO_IN, TEA_FULL, "break"),
                (NO_FLIPPED_IN, TEA_HALF, "break"),
                (IT_OUT, NO_IN, "baseline"),
                (IT_OUT, NO_FLIPPED_IN, "baseline"),
                *((left, right, "break") for left, right in extra_breaks),
            ]
        }
        self.tables = zwnj_tables(self.spec, rows, {"default": [TEA_FULL]}, {"default": [IT]})
        cells = {one for left, right, _junction in rows["default"] for one in (left, right)} | {IT}
        self.glyphs = realized(self.spec, cells)
        self.copies = copies(self.spec) if overlay else None
        self.resolution = kern.resolve(self.spec, self.tables, self.glyphs, self.copies)

    def pair(self, lead: str, follow: str) -> int:
        for index, pair in enumerate(self.resolution.pairs):
            if (pair.leading.name, pair.following.name) == (lead, follow):
                return index
        raise AssertionError(f"no facing pair {lead} | {follow}")

    def pair_of(self, left: CellId, right: CellId) -> int:
        for index, pair in enumerate(self.resolution.pairs):
            if (left, right) in pair.members:
                return index
        raise AssertionError(f"no facing pair holds {left} | {right}")


def settler(answers: dict[tuple[int, ...], list], asked: list | None = None):
    """A stand-in for the kernel: each text it knows settles to its slots under any configuration, and every other text is refused."""

    def settle(texts, config):
        if asked is not None:
            asked.extend((text, config) for text in texts)
        return [list(answers[text]) if text in answers else None for text in texts]

    return settle


ZWNJ_ANSWERS = {
    (TEA, ZWNJ): [TEA_FULL, "uni200C"],
    (ZWNJ, IT_LETTER): ["uni200C", IT],
    (TEA, ZWNJ, IT_LETTER): [TEA_FULL, "uni200C", IT],
}


class TestPlace:
    def test_a_break_advances_by_the_left_glyph_s_compiled_advance_and_a_zwnj_by_nothing(self, tmp_path):
        spec = load(tmp_path)
        records = realized(spec, {TEA_FULL, IT, NO_LOOP})
        advance = kern.advance(records[TEA_FULL])
        assert kern_inventory.place([TEA_FULL, IT], records) == [0, advance]
        assert kern_inventory.place([TEA_FULL, "uni200C", IT], records) == [0, advance, advance]
        assert kern_inventory.place([NO_LOOP], records) == [0]

    def test_a_join_puts_the_right_glyph_s_entry_on_the_left_glyph_s_exit(self, tmp_path):
        spec = load(tmp_path)
        records = realized(spec, {IT_OUT, NO_IN})
        exit_x = records[IT_OUT].exit[0]  # pyright: ignore[reportOptionalSubscript]
        entry_x = records[NO_IN].entry[0]  # pyright: ignore[reportOptionalSubscript]
        assert kern_inventory.place([IT_OUT, NO_IN], records) == [0, exit_x - entry_x]

    def test_a_pair_that_breaks_the_join_invariant_or_a_boundary_other_than_a_zwnj_is_refused(self, tmp_path):
        spec = load(tmp_path)
        records = realized(spec, {IT_OUT, IT, TEA_FULL})
        with pytest.raises(kern_inventory.KernInventoryError, match="joins exactly when"):
            kern_inventory.place([IT_OUT, IT], records)
        with pytest.raises(kern_inventory.KernInventoryError, match="space"):
            kern_inventory.place([TEA_FULL, "space", IT], records)


class TestTheManualWords:
    def test_each_run_of_letters_is_a_word_with_its_references_decoded_and_its_end_zwnjs_dropped(self):
        page = "<p>&#xE666;&#xE652; and ‌, ‌‌ &#59012;</p>".encode()
        words = kern_inventory.manual_words({"site/the-manual.html": page}, [NO, TEA, IT_LETTER])
        assert words == [(TEA,), (NO, TEA), (IT_LETTER, ZWNJ, NO)]

    def test_a_word_with_a_letter_the_alphabet_lacks_is_left_out_whole(self):
        page = " ".encode()
        assert kern_inventory.manual_words({"site/index.html": page}, [NO, TEA]) == [(NO,)]

    def test_the_stamp_names_each_page_or_its_absence(self):
        missing = kern_inventory.manual_digest({})
        assert kern_inventory.manual_digest({"site/the-manual.html": b"x"}) != missing
        assert kern_inventory.inventory_stamp("t", {}) == {"tables": "t", "manual": missing}


class TestTheWitnessLadder:
    def test_a_manual_word_comes_first_and_every_manual_word_is_kept(self, tmp_path):
        world = World(tmp_path)
        answers = {(NO, TEA): [NO_LOOP, TEA_FULL], (IT_LETTER,): [IT], **ZWNJ_ANSWERS}
        found = kern_inventory.find_witnesses(
            world.spec,
            world.resolution,
            world.tables,
            world.glyphs,
            world.copies,
            [(IT_LETTER,), (NO, TEA)],
            settler(answers),
        )
        assert [(text.codepoints, text.manual) for text in found.texts[:2]] == [
            ((NO, TEA), True),
            ((IT_LETTER,), True),
        ]
        index = world.pair_of(NO_LOOP, TEA_FULL)
        assert found.pairs[index] == (0, 0)
        assert found.texts[0].source == "manual"
        assert found.representative[("qsNo", "qsTea")] == (index, (NO_LOOP, TEA_FULL))

    def test_the_two_letters_then_one_letter_of_context_witness_mid_word_breaks(self, tmp_path):
        world = World(tmp_path)
        answers = {
            (NO, TEA): [NO_LOOP, TEA_FULL],
            (IT_LETTER, NO): [IT_OUT, NO_IN],
            (IT_LETTER, NO, TEA): [IT_OUT, NO_FLIPPED_IN, TEA_HALF],
            **ZWNJ_ANSWERS,
        }
        found = kern_inventory.find_witnesses(
            world.spec, world.resolution, world.tables, world.glyphs, world.copies, [], settler(answers)
        )
        flipped = world.pair_of(NO_FLIPPED_IN, TEA_HALF)
        text, gap = found.pairs[flipped]
        assert (found.texts[text].codepoints, found.texts[text].source, gap) == (
            (IT_LETTER, NO, TEA),
            "context",
            1,
        )
        assert found.pairs[world.pair_of(NO_LOOP, TEA_FULL)][1] == 0
        groups = {(group.lead_stance, group.follow_stance): index for index, group in enumerate(found.joined)}
        assert found.texts[found.groups[groups[("sole", "loop")]][0]].codepoints == (IT_LETTER, NO)
        assert set(found.groups) == set(groups.values())

    def test_a_pair_only_a_zwnj_reaches_is_witnessed_by_an_ending_and_a_starting_word_composed(
        self, tmp_path
    ):
        world = World(tmp_path)
        asked: list = []
        found = kern_inventory.find_witnesses(
            world.spec,
            world.resolution,
            world.tables,
            world.glyphs,
            world.copies,
            [],
            settler(ZWNJ_ANSWERS, asked),
        )
        index = world.pair_of(TEA_FULL, IT)
        assert kern_inventory.ACROSS_ZWNJ in found.reach[index]
        text, gap = found.pairs[index]
        assert (
            found.texts[text].codepoints == (TEA, ZWNJ, IT_LETTER)
            and found.texts[text].source == "zwnj"
            and gap == 0
        )
        assert ((TEA, ZWNJ, IT_LETTER), "default") in asked

    def test_a_mid_word_pair_takes_a_zwnj_witness_only_when_no_mid_word_text_is_found(self, tmp_path):
        world = World(tmp_path, extra_breaks=((TEA_FULL, IT),))
        index = world.pair_of(TEA_FULL, IT)
        assert kern_inventory.WORD in world_reach(world, index)
        with_word = {(TEA, IT_LETTER): [TEA_FULL, IT], **ZWNJ_ANSWERS}
        found = kern_inventory.find_witnesses(
            world.spec, world.resolution, world.tables, world.glyphs, world.copies, [], settler(with_word)
        )
        assert found.texts[found.pairs[index][0]].codepoints == (TEA, IT_LETTER)
        found = kern_inventory.find_witnesses(
            world.spec, world.resolution, world.tables, world.glyphs, world.copies, [], settler(ZWNJ_ANSWERS)
        )
        assert found.texts[found.pairs[index][0]].codepoints == (TEA, ZWNJ, IT_LETTER)

    def test_a_pair_only_the_overlay_reaches_takes_its_two_letters_under_ss10(self, tmp_path):
        world = World(tmp_path)
        source = geometry.isolated_cell(world.spec, "qsUtter")
        index = world.pair_of(source, source)
        assert world_reach(world, index) == (kern_inventory.OVERLAY,)
        found = kern_inventory.find_witnesses(
            world.spec, world.resolution, world.tables, world.glyphs, world.copies, [], settler({})
        )
        text = found.texts[found.pairs[index][0]]
        assert (text.codepoints, text.config, text.slots, text.source) == (
            (UTTER_LETTER, UTTER_LETTER),
            "ss10",
            (source, source),
            "ss10",
        )

    def test_a_pair_nothing_witnesses_has_no_witness_text(self, tmp_path):
        world = World(tmp_path, overlay=False)
        found = kern_inventory.find_witnesses(
            world.spec, world.resolution, world.tables, world.glyphs, None, [], settler({})
        )
        assert found.pairs == {} and found.groups == {}
        assert found.sources() == {"bare": len(world.resolution.pairs)}
        document = kern_inventory.inventory_document(
            world.spec, world.resolution, world.glyphs, found, {"tables": "t", "manual": "m"}, ["default"]
        )
        assert kern_inventory.check_document(document) == []
        index = world.pair_of(NO_FLIPPED_IN, TEA_HALF)
        names = [document["glyphs"][glyph]["name"] for glyph in document["pairs"][index]["member"]]
        assert names == [cell_label(world.spec, NO_FLIPPED_IN), cell_label(world.spec, TEA_HALF)]
        assert document["pairs"][index]["witness"] is None and document["pairs"][index]["at"] is None

    def test_a_settled_break_no_facing_pair_holds_is_refused(self, tmp_path):
        world = World(tmp_path, overlay=False)
        answers = {(NO, TEA): [NO_LOOP, TEA_HALF]}
        with pytest.raises(kern_inventory.KernInventoryError, match="no facing pair holds"):
            kern_inventory.find_witnesses(
                world.spec, world.resolution, world.tables, world.glyphs, None, [(NO, TEA)], settler(answers)
            )


def world_reach(world: World, index: int) -> tuple[str, ...]:
    found = kern_inventory.find_witnesses(
        world.spec, world.resolution, world.tables, world.glyphs, world.copies, [], settler({})
    )
    return found.reach[index]


def full_answers() -> dict:
    return {
        (NO, TEA): [NO_LOOP, TEA_FULL],
        (IT_LETTER, NO): [IT_OUT, NO_IN],
        (IT_LETTER, NO, TEA): [IT_OUT, NO_FLIPPED_IN, TEA_HALF],
        **ZWNJ_ANSWERS,
    }


class TestTheDocument:
    def document(self, world: World, words=()) -> dict:
        found = kern_inventory.find_witnesses(
            world.spec,
            world.resolution,
            world.tables,
            world.glyphs,
            world.copies,
            list(words),
            settler(full_answers()),
        )
        return kern_inventory.inventory_document(
            world.spec,
            world.resolution,
            world.glyphs,
            found,
            {"tables": "t", "manual": "m"},
            ["default", "ss10"],
        )

    def test_it_holds_its_shape_and_every_witness_holds_its_pair_at_its_gap(self, tmp_path):
        document = self.document(World(tmp_path), [(NO, TEA)])
        assert kern_inventory.check_document(document) == []
        for index, pair in enumerate(document["pairs"]):
            assert document["texts"][pair["witness"]]["gaps"][pair["at"]] == index

    def test_the_texts_carry_their_positions_their_gaps_and_their_notation(self, tmp_path):
        world = World(tmp_path)
        document = self.document(world)
        records = world.glyphs
        by_cp = {tuple(text["cp"]): text for text in document["texts"]}
        context = by_cp[(IT_LETTER, NO, TEA)]
        assert context["x"] == kern_inventory.place([IT_OUT, NO_FLIPPED_IN, TEA_HALF], records)
        assert context["gaps"] == [-1, world.pair_of(NO_FLIPPED_IN, TEA_HALF)]
        assert context["notation"] == "·It ~b~ ·No | ·Tea.half"
        across = by_cp[(TEA, ZWNJ, IT_LETTER)]
        assert across["zwnj"] == [0] and across["x"] == [0, kern.advance(records[TEA_FULL])]
        assert across["notation"] == "·Tea | ◊ZWNJ | ·It"

    def test_each_rune_pair_names_its_facing_pairs_its_representative_its_joins_and_its_manual_texts(
        self, tmp_path
    ):
        world = World(tmp_path)
        document = self.document(world, [(IT_LETTER, NO, TEA)])
        runes = {rune["name"]: index for index, rune in enumerate(document["runes"])}
        entries = {(entry["lead"], entry["follow"]): entry for entry in document["rune_pairs"]}
        no_tea = entries[(runes["qsNo"], runes["qsTea"])]
        assert no_tea["rep"] == world.pair_of(NO_LOOP, TEA_FULL)
        assert set(no_tea["pairs"]) == {
            world.pair_of(NO_LOOP, TEA_FULL),
            world.pair_of(NO_FLIPPED_IN, TEA_HALF),
        }
        manual = [index for index, text in enumerate(document["texts"]) if text["manual"]]
        assert no_tea["manual"] == manual
        it_no = entries[(runes["qsIt"], runes["qsNo"])]
        assert it_no["joined"] and it_no["manual"] == manual
        assert [rune["name"] for rune in document["runes"]] == sorted(
            world.spec.runes, key=lambda name: kern.rune_order_key(world.spec, name)
        )
        assert document["runes"][runes["qsDay_qsUtter"]]["label"] == "·Day+Utter"

    def test_the_check_names_a_witness_that_does_not_hold_its_pair(self, tmp_path):
        document = self.document(World(tmp_path))
        pair = document["pairs"][0]
        text = document["texts"][pair["witness"]]
        text["gaps"][pair["at"]] = len(document["pairs"]) - 1 if len(document["pairs"]) > 1 else -1
        assert any("does not hold it" in error for error in kern_inventory.check_document(document))
        assert kern_inventory.check_document({"format": "other"})


class TestThePositionCheck:
    def shaped(self, document: dict, copies: dict[str, str], *, move: int = 0):
        """A stand-in for HarfBuzz with kern off that gives what the inventory says, every pen position after the first glyph moved by `move` pixels."""
        by_text = {}
        for text in document["texts"]:
            config = document["configs"][text["config"]]
            slots = []
            for position, (glyph, x) in enumerate(zip(text["glyphs"], text["x"])):
                name = document["glyphs"][glyph]["name"]
                if config == "ss10":
                    name = copies[document["runes"][document["glyphs"][glyph]["rune"]]["name"]]
                slots.append((name, x))
                if position in text["zwnj"]:
                    slots.append(("space", text["x"][position + 1]))
            by_text[("".join(chr(codepoint) for codepoint in text["cp"]), features_for_config(config))] = (
                slots
            )

        def shape(text, features):
            return [
                {
                    "name": name,
                    "x_offset": (x + (move if index else 0)) * geometry.PIXEL,
                    "y_offset": 0,
                    "x_advance": 0,
                }
                for index, (name, x) in enumerate(by_text[(text, frozenset(features))])
            ]

        return shape

    def test_harfbuzz_agreeing_with_the_inventory_passes_and_a_moved_glyph_is_named(self, tmp_path):
        world = World(tmp_path)
        found = kern_inventory.find_witnesses(
            world.spec,
            world.resolution,
            world.tables,
            world.glyphs,
            world.copies,
            [],
            settler(full_answers()),
        )
        document = kern_inventory.inventory_document(
            world.spec,
            world.resolution,
            world.glyphs,
            found,
            {"tables": "t", "manual": "m"},
            ["default", "ss10"],
        )
        names = copies(world.spec)
        assert kern_inventory.check_positions(document, self.shaped(document, names), names, ["ss10"]) == []
        problems = kern_inventory.check_positions(
            document, self.shaped(document, names, move=1), names, ["ss10"]
        )
        assert len(problems) == sum(1 for text in document["texts"] if len(text["glyphs"]) > 1)


class TestWriteInventory:
    def test_a_file_already_stamped_for_these_tables_and_pages_is_kept_and_nothing_is_settled(self, tmp_path):
        (tmp_path / "spec").mkdir()
        world = World(tmp_path / "spec")
        out = tmp_path / "out"
        out.mkdir()
        asked: list = []

        def write(inputs: str):
            return kern_inventory.write_inventory(
                world.spec,
                world.tables,
                world.glyphs,
                world.resolution,
                world.copies,
                out,
                inputs,
                repo_root=tmp_path,
                settle=settler(full_answers(), asked),
            )

        built = write("tables-1")
        assert not built.kept and asked
        path = out / kern_inventory.KERN_INVENTORY_JSON
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["stamp"] == kern_inventory.inventory_stamp(
            "tables-1", kern_inventory.read_manual_pages(tmp_path)
        )
        before = path.stat().st_mtime_ns
        asked.clear()
        assert write("tables-1").kept and asked == []
        assert path.stat().st_mtime_ns == before
        (tmp_path / "site").mkdir()
        (tmp_path / "site" / "the-manual.html").write_text("")
        assert not write("tables-1").kept
        assert not write("tables-2").kept
        assert "letters=" in built.tokens() and write("tables-2").tokens() == "kept=true"


def test_the_app_fixture_is_an_inventory():
    """rebuild/review/fixtures/kern-inventory.json, which the app's node tests read, is a slice of a built inventory to ·No, ·Tea and ·It and what their witnesses need, and must keep the inventory's shape."""
    assert kern_inventory.check_document(json.loads(FIXTURE.read_text(encoding="utf-8"))) == []


def test_the_command_line_mints_and_copies_as_the_build_does(tmp_path):
    """The command line cannot import the build's driver (rebuild/test_build_code_closure.py), so it mints the tables' cells and names the letters' ss10 copies itself, and must agree with `run_m1.mint_cell_glyphs` and `run_m1.mint_raw_glyphs`."""
    from rebuild.pipeline import run_m1
    from rebuild.pipeline.table import DecisionTable

    spec = load(tmp_path)
    cells = frozenset({IT, IT_OUT, NO_LOOP, NO_IN, TEA_FULL, TEA_HALF})
    built = {"default": (DecisionTable(config="default", _cells=cells),)}
    assert kern_inventory.mint_cells(spec, built) == run_m1.mint_cell_glyphs(spec, built)
    assert kern_inventory.letter_copies(spec) == run_m1.mint_raw_glyphs(spec)[2]


def test_two_rune_pairs_typed_with_the_same_letters_each_read_the_one_settlement(tmp_path):
    """·Day before ·Utter+It and ·Day+Utter before ·It are both typed ·Day·Utter·It. The text is settled once, and each rune pair reads it for itself, so the one whose cells it settles to, here ·Day before ·Utter+It, takes it as its representative, and the other does not."""
    from rebuild.test_kern import REGISTRY, _alphabet, _rune

    alphabet = {
        **_alphabet(),
        "qsUtter_qsIt": _rune(
            "qsUtter_qsIt",
            None,
            {
                "sole": {
                    "outgoing": {"exception": "The ligature ends without ·It's trailing stroke."},
                    "bitmap": ["####"] * 6,
                }
            },
        ),
    }
    registry = REGISTRY.replace(
        "  qsDay_qsUtter: {sequence: [qsDay, qsUtter]}\n",
        "  qsDay_qsUtter: {sequence: [qsDay, qsUtter]}\n  qsUtter_qsIt: {sequence: [qsUtter, qsIt]}\n",
    )
    spec = load(tmp_path, alphabet=alphabet, registry_text=registry)
    day, utter_it, day_utter = (
        cell("qsDay", "sole"),
        cell("qsUtter_qsIt", "sole"),
        cell("qsDay_qsUtter", "full"),
    )
    rows = {"default": [(day, utter_it, "break"), (day_utter, IT, "break")]}
    built = zwnj_tables(spec, rows, {"default": []}, {"default": []})
    records = realized(spec, {day, utter_it, day_utter, IT})
    resolution = kern.resolve(spec, built, records)
    found = kern_inventory.find_witnesses(
        spec,
        resolution,
        built,
        records,
        None,
        [],
        settler({(0xE653, UTTER_LETTER, IT_LETTER): [day, utter_it]}),
    )
    index = next(number for number, pair in enumerate(resolution.pairs) if (day, utter_it) in pair.members)
    assert found.representative[("qsDay", "qsUtter_qsIt")] == (index, (day, utter_it))
    assert ("qsDay_qsUtter", "qsIt") not in found.representative


def test_a_rune_pair_draws_the_cells_its_two_letters_settle_to_alone_whatever_its_witness_shows(tmp_path):
    """·It·No·Tea from The Manual witnesses ·No before full ·Tea first, with ·No's entry; the two letters alone settle to ·No without one, and that is what the rune pair's `rep_member` names, the cells the grid draws."""
    world = World(tmp_path)
    answers = {**full_answers(), (IT_LETTER, NO, TEA): [IT_OUT, NO_IN, TEA_FULL]}
    found = kern_inventory.find_witnesses(
        world.spec,
        world.resolution,
        world.tables,
        world.glyphs,
        world.copies,
        [(IT_LETTER, NO, TEA)],
        settler(answers),
    )
    document = kern_inventory.inventory_document(
        world.spec, world.resolution, world.glyphs, found, {"tables": "t", "manual": "m"}, ["default", "ss10"]
    )
    assert kern_inventory.check_document(document) == []
    runes = {rune["name"]: index for index, rune in enumerate(document["runes"])}
    entry = next(
        found
        for found in document["rune_pairs"]
        if (found["lead"], found["follow"]) == (runes["qsNo"], runes["qsTea"])
    )
    names = lambda member: [document["glyphs"][glyph]["name"] for glyph in member]
    assert names(entry["rep_member"]) == [cell_label(world.spec, NO_LOOP), cell_label(world.spec, TEA_FULL)]
    assert names(document["pairs"][entry["rep"]]["member"]) == [
        cell_label(world.spec, NO_IN),
        cell_label(world.spec, TEA_FULL),
    ]


def test_a_text_names_the_joined_group_at_each_join(tmp_path):
    world = World(tmp_path)
    document = TestTheDocument().document(world)
    by_cp = {tuple(text["cp"]): text for text in document["texts"]}
    text = by_cp[(IT_LETTER, NO, TEA)]
    group = document["joined"][text["joins"][0]]
    assert (group["lead_stance"], group["follow_stance"], group["height"]) == ("sole", "flipped", "baseline")
    assert text["joins"][1] is None
    text["joins"][0] = None
    assert any("joins" in error for error in kern_inventory.check_document(document))


def test_a_mixed_side_s_facing_pair_carries_one_member_for_each_pair_of_facing_edges(tmp_path):
    """A side `kern.refine_sides` flags mixed holds cells whose facing edges differ although no record tells them apart, so its facing pair carries a member for each distinct pair of edges (`distinct_drawings`), the first in label order, and only such a pair may carry them."""
    world = World(tmp_path)
    records = realized(world.spec, {NO_LOOP, NO_IN, TEA_FULL, TEA_HALF})
    pair = world.resolution.pairs[world.pair_of(NO_LOOP, TEA_FULL)]
    members = ((NO_LOOP, TEA_FULL), (NO_IN, TEA_FULL), (NO_LOOP, TEA_HALF))
    mixed = kern.FacingPair(pair.leading, pair.following, 0, (), ("default",), members)
    edges = {}
    for left, right in members:
        edges.setdefault(
            (kern.leading_edge(records[left]), kern.following_edge(records[right])), (left, right)
        )
    assert kern_inventory.distinct_drawings(mixed, records) == list(edges.values())
    assert (NO_LOOP, TEA_HALF) in kern_inventory.distinct_drawings(mixed, records)
    document = TestTheDocument().document(world)
    document["pairs"][0]["drawings"] = [document["pairs"][0]["member"], document["pairs"][0]["member"]]
    assert any("no mixed side" in error for error in kern_inventory.check_document(document))
    document["sides"][document["pairs"][0]["lead"]]["mixed"] = True
    assert kern_inventory.check_document(document) == []


def test_the_inventory_is_renamed_into_place_so_no_reader_sees_it_torn(tmp_path):
    """The review server reads the inventory from the directory the build writes it in, so a rewrite puts a new file in place by a rename (`output_sink.replace_output`) rather than writing through the one a reader may have open."""
    (tmp_path / "spec").mkdir()
    world = World(tmp_path / "spec")
    out = tmp_path / "out"
    out.mkdir()

    def write(inputs: str):
        kern_inventory.write_inventory(
            world.spec,
            world.tables,
            world.glyphs,
            world.resolution,
            world.copies,
            out,
            inputs,
            repo_root=tmp_path,
            settle=settler(full_answers()),
        )

    write("tables-1")
    path = out / kern_inventory.KERN_INVENTORY_JSON
    before = path.stat().st_ino
    write("tables-2")
    assert path.stat().st_ino != before
    assert not list(out.glob("*.partial"))
