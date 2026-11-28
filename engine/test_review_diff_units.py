"""Tests for the table-diff corpus (`engine/review/diff_units.py` and `build.build_table_diff`): the units an edit's visible changes make, folded across configurations and left neighbors and merged where they show one text, with their clusters, groups, pair bands and ids, the quiet and provenance units, a join row no text holds, the zero-unit corpus of an empty diff and of a machine with no snapshot, and the table-diff contract predicates.

Every corpus here is built from real builds of the pipeline's mini fixture spec (`fixtures.mini_spec`): its tables and windows heads from `run_build.build_tables`, and its font compiled from them as the readback tests compile one, without the kern stage, whose ss10 rule the mini ·May does not meet. The edit removes ·Tea's x-height entry extension, so ·Day+Utter ~x~ ·Tea loses a pixel under every configuration whose ·Tea is half there, which is every settled configuration but those with ss03 on. The rows a synthetic test edits are copies of the unedited build's, which keep their windows heads.
"""

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from engine.pipeline import compile_font, emit_gpos, emit_gsub, fixtures, run_build
from engine.review import build, diff_units, snapshot, sweep_record, tablediff, unit_ids
from engine.review.enrich import LETTERS
from engine.review.ink import IDENTITY_DIFF
from engine.validation.shaping import ShapeResult, Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGED = ("default", "ss04", "ss05", "ss07")


def _without_tea_extension(spec):
    tea = spec.runes["qsTea"]
    return dataclasses.replace(
        spec,
        runes={
            **spec.runes,
            "qsTea": dataclasses.replace(tea, policy=dataclasses.replace(tea.policy, extend=())),
        },
    )


def _mini_build(spec, out: Path, inputs: str) -> Path:
    """Build `spec`'s tables and windows heads into `out` and compile its font there, with an empty kern table beside it."""
    out.mkdir(parents=True, exist_ok=True)
    tables, _digests = run_build.build_tables(spec, out, inputs=inputs)
    cell_glyphs = run_build.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_build.mint_raw_glyphs(spec)
    curs = {**cell_glyphs, **bare, **copies}
    plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_copies=ss10_copies)
    compile_font.build_font(
        {**curs, **run_build.namer_dot_glyphs()},
        plan.fea_text + "\n" + emit_gpos.emit_gpos(curs, spec=spec),
        out / "Senior-Regular.otf",
    )
    (out / "kern.tsv").write_text("# kern table\n", encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    root = tmp_path_factory.mktemp("diff-units")
    accepted = _mini_build(fixtures.mini_spec(), root / "accepted", "fp-accepted")
    edited = _mini_build(_without_tea_extension(fixtures.mini_spec()), root / "edited", "fp-edited")
    taken = snapshot.take(accepted, root / "home", spec=fixtures.mini_spec())
    return accepted, edited, taken


@pytest.fixture(scope="module")
def corpus(builds, tmp_path_factory):
    _accepted, edited, taken = builds
    out = tmp_path_factory.mktemp("diff-corpus")
    manifest = build.build_table_diff(
        out, taken.directory, edited, spec=_without_tea_extension(fixtures.mini_spec())
    )
    shards = {
        meta["id"]: json.loads((out / meta["shards"][0]).read_text(encoding="utf-8"))
        for meta in manifest["classes"]
    }
    return out, manifest, shards


def _units(shards):
    return [unit for shard in shards.values() for unit in shard]


TEXT = "\ue653\ue67a\ue652"
JUNCTION = "join qsDay_qsUtter.full.ex-y5 + qsTea.half.en-y5"


def _junction_unit(shards):
    (unit,) = [unit for unit in shards["joins"] if unit["codepoints"] == "E653:E67A:E652"]
    return unit


def test_the_corpus_passes_the_contract_check_and_needs_no_unit_store(corpus):
    out, manifest, _shards = corpus
    assert build.check_output_dir(out) == []
    assert manifest["configs"] == list(tablediff.SETTLED_CONFIGS)
    assert manifest["baseline"]["present"] is True and manifest["baseline"]["digest"]
    assert [meta["id"] for meta in manifest["classes"]] == ["joins", "provenance-only"]


def test_the_edited_junction_is_one_unit_for_its_join_rows_and_its_cell_change(corpus, builds):
    """·Day+Utter ~x~ ·Tea, the edited junction, is one unit: the extended pair the accepted build had, the pair the edit settles instead and ·Tea's cell change show the same text under the same configurations, so they merge, in the `joins` class, carrying all three changes' kinds and rows, and the picture differs in the two fonts."""
    _out, _manifest, shards = corpus
    accepted, edited, _taken = builds
    unit = _junction_unit(shards)
    assert unit["row_key"].split("; ") == [
        f"{JUNCTION}: none -> x-height+0",
        f"{JUNCTION}.en-ext-1: x-height+1 -> none",
        "settlement qsTea: qsTea.half.en-y5.en-ext-1 -> qsTea.half.en-y5",
    ]
    assert unit["class"] == "joins" and unit["kinds"] == ["added", "changed", "removed"]
    assert unit["configs"] == list(CHANGED)
    assert not unit["ink_identical"] and not unit["picture_identical"]
    comparator = diff_units.Fonts(accepted / "Senior-Regular.otf", edited / "Senior-Regular.otf").comparator
    assert all(comparator.config_diff(TEXT, config) != IDENTITY_DIFF for config in CHANGED)
    assert unit["before"]["glyphs"] == ["qsDay_qsUtter.full.ex-y5", "qsTea.half.en-y5.en-ext-1"]
    assert unit["after"]["cells"] == ["qsDay_qsUtter.full.ex-y5", "qsTea.half.en-y5"]
    assert unit["summary"].endswith("(one picture for 2 join rows and 1 cell change)")
    gate, note = build.config_badge(CHANGED, tablediff.SETTLED_CONFIGS)
    assert (unit["config_gate"], unit["config_note"]) == (gate, note) and note == "only when ss03 is off"


def test_a_cell_change_folds_its_left_neighbors_into_one_change_they_are_evidence_of(corpus):
    """·Tea settles to the same new cell after every ·Day+Utter cell that reaches it at the x-height, so there is one cell change, which names the left glyphs it was seen after in its explain."""
    _out, _manifest, shards = corpus
    carrying = [unit for unit in _units(shards) if "settlement qsTea:" in unit["row_key"]]
    assert carrying == [_junction_unit(shards)]
    explain = carrying[0]["explain"]
    seen = next(line for line in explain.splitlines() if line.startswith("  seen after: "))
    lefts = seen.removeprefix("  seen after: ").split(", ")
    assert lefts == ["qsDay_qsUtter.full.en-y5.ex-y5", "qsDay_qsUtter.full.ex-y5"]


def test_units_that_show_the_same_pixels_share_a_cluster(corpus, builds):
    """Units over other texts that show the junction's pixels share its Queue card, whose id is the digest of the ink deltas; units of another picture are on other cards."""
    _out, _manifest, shards = corpus
    accepted, edited, _taken = builds
    from engine.review.ink import delta_digest

    fonts = diff_units.Fonts(accepted / "Senior-Regular.otf", edited / "Senior-Regular.otf")
    digests = [delta_digest(fonts.comparator.config_diff(TEXT, config)) for config in CHANGED]
    unit = _junction_unit(shards)
    assert unit["cluster"] == diff_units.cluster_id(digests)
    sharing = [other for other in shards["joins"] if other["cluster"] == unit["cluster"]]
    assert len(sharing) > 1 and {other["codepoints"] for other in sharing} > {unit["codepoints"]}
    assert len({other["cluster"] for other in _units(shards) if other["cluster"]}) > 1


def test_every_join_row_of_the_edit_finds_a_text_and_twins_merge(corpus):
    """The rows the bounded search alone cannot reach, a ligature's left cell before another letter, take the prefix that settles the left cell followed by the right letter. Each removed row and its added twin show one text, so the 58 changed rows are 29 units."""
    _out, manifest, shards = corpus
    assert all(unit["codepoints"] for unit in shards["joins"])
    assert manifest["table_rows"]["default"]["joins"] == {"added": 29, "removed": 29}
    assert manifest["classes"][0]["unit_count"] == 29
    assert all({"added", "removed"} <= set(unit["kinds"]) for unit in shards["joins"])


def test_groups_are_family_pairs_and_the_band_covers_the_change(corpus):
    _out, _manifest, shards = corpus
    families = set(LETTERS.values())
    for unit in _units(shards):
        assert all(part in families for part in unit["group"].split(":")), unit["group"]
    unit = _junction_unit(shards)
    assert unit["group"] == "qsUtter:qsTea"
    assert unit["pair"] == {"left": 0, "right": 1}
    assert unit["pair_codepoints"] == [0, 2]
    assert unit["notation_tokens"] == ["·Day", "·Utter", "·Tea"]
    assert unit["highlight"]["before"]["x_max"] > unit["highlight"]["after"]["x_max"]


def test_ids_are_their_identity_digests_and_move_with_what_a_unit_shows(corpus):
    _out, _manifest, shards = corpus
    ids = [unit["id"] for unit in _units(shards)]
    assert len(ids) == len(set(ids))
    for unit in _units(shards):
        assert unit["id"] == unit_ids.unit_id_for(unit["content_key"])
    cell = diff_units.CellChange("qsTea", "qsTea.x", "qsTea.y")
    unit = diff_units.Unit(
        diff_units.SETTLEMENT, cell, ("default",), "\ue652", ("changed",), "a test", lefts=(("a", "a"),)
    )
    first = diff_units.unit_identity(unit, "E652", ["qsTea.x"], ["qsTea.y"])
    for moved in (
        dataclasses.replace(unit, configs=("default", "ss03")),
        dataclasses.replace(unit, subject=diff_units.CellChange("qsTea", "qsTea.x", "qsTea.z")),
    ):
        assert first != diff_units.unit_identity(moved, "E652", ["qsTea.x"], ["qsTea.y"])
    assert first != diff_units.unit_identity(unit, "E652", ["qsTea.x"], ["qsTea.z"])
    for evidence in (
        dataclasses.replace(unit, found_by="another search"),
        dataclasses.replace(unit, lefts=(("a", "a"), ("b", "b")), left="b"),
    ):
        assert first == diff_units.unit_identity(evidence, "E652", ["qsTea.x"], ["qsTea.y"])
    join = diff_units.Unit(
        diff_units.JOIN,
        diff_units.JoinChange("qsIt.x", "qsTea.x", None, ("x-height", 0)),
        ("default",),
        "\ue652",
        ("added",),
        "a test",
    )
    (merged,) = diff_units.merge([unit, join])
    assert first != diff_units.unit_identity(merged, "E652", ["qsTea.x"], ["qsTea.y"])


def test_merge_joins_units_that_show_one_text_under_one_configuration_set_and_no_others():
    def unit(kind, subject, configs=("default",), text: str | None = "ab", kinds=("changed",)):
        return diff_units.Unit(kind, subject, configs, text, kinds, "a test", {configs[0]: ()})

    cell = unit(
        diff_units.SETTLEMENT, diff_units.CellChange("qsTea", "qsTea.x", "qsTea.y"), kinds=("regrouped",)
    )
    removed = unit(
        diff_units.JOIN, diff_units.JoinChange("qsIt.a", "qsTea.x", ("x-height", 1), None), kinds=("removed",)
    )
    added = unit(
        diff_units.JOIN, diff_units.JoinChange("qsIt.a", "qsTea.y", None, ("x-height", 0)), kinds=("added",)
    )
    elsewhere = unit(
        diff_units.JOIN, diff_units.JoinChange("qsIt.a", "qsTea.y", None, ("x-height", 0)), configs=("ss04",)
    )
    textless = unit(
        diff_units.JOIN, diff_units.JoinChange("qsIt.b", "qsTea.y", None, ("x-height", 0)), text=None
    )
    quiet = unit(diff_units.QUIET, "qsTea")
    merged = diff_units.merge([cell, removed, added, elsewhere, textless, quiet])
    assert [len(item.parts) for item in merged] == [3, 1, 1, 1]
    first, *rest = merged
    assert first.kind == diff_units.JOIN and first.subject == removed.subject
    assert [part.kind for part in first.parts] == [diff_units.JOIN, diff_units.JOIN, diff_units.SETTLEMENT]
    assert first.kinds == ("added", "regrouped", "removed")
    assert rest == [elsewhere, textless, quiet]


def test_rows_that_move_only_provenance_are_a_unit_that_takes_no_verdict(corpus):
    """Under ss03 ·Tea is full at the x-height, so the removed extension changes only which records ·Tea's rules credit."""
    _out, manifest, shards = corpus
    (unit,) = shards["provenance-only"]
    assert unit["configs"] == ["ss03", "ss03+ss05"]
    assert unit["no_verdict"] is True and unit["cluster"] is None
    assert unit["kinds"] == ["provenance-only"]
    assert unit["id"] not in manifest["human_unit_ids"]
    (meta,) = [meta for meta in manifest["classes"] if meta["id"] == "provenance-only"]
    assert meta["section"] == "no-review-needed" and meta["no_verdict"] is True


def test_the_human_index_holds_every_human_unit(corpus):
    _out, manifest, shards = corpus
    assert sorted(manifest["human_unit_ids"]) == sorted(unit["id"] for unit in shards["joins"])
    for unit in _units(shards):
        assert isinstance(unit["cluster"], str) is (unit["id"] in manifest["human_unit_ids"])


def _copy(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def test_rows_that_change_with_nothing_visible_are_a_machine_approved_quiet_unit(builds, tmp_path):
    """A rule whose joint flag flips settles the same cells, so no candidate text draws differently: the letter's unit is machine-approved over a text that draws alike."""
    accepted, _edited, taken = builds
    new = _copy(accepted, tmp_path / "new")
    path = new / "settlement-default.tsv"
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(number for number, line in enumerate(lines) if line.startswith("qsMay\t"))
    fields = lines[index].split("\t")
    fields[-2] = "-" if fields[-2] == "joint" else "joint"
    lines[index] = "\t".join(fields)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    manifest = build.build_table_diff(out, taken.directory, new, spec=fixtures.mini_spec())
    assert build.check_output_dir(out) == []
    (meta,) = manifest["classes"]
    assert (meta["id"], meta["unit_count"], meta["machine_approved_count"]) == ("settlement", 1, 1)
    (unit,) = json.loads((out / meta["shards"][0]).read_text(encoding="utf-8"))
    assert unit["configs"] == ["default"] and unit["kinds"] == ["changed"]
    assert unit["ink_identical"] is True and unit["cluster"] is None
    assert "no candidate text shows it settled differently" in unit["summary"]
    assert manifest["human_unit_ids"] == []


def test_a_join_row_no_text_holds_is_a_unit_shown_by_its_notation(builds, tmp_path):
    accepted, _edited, taken = builds
    new = _copy(accepted, tmp_path / "new")
    with open(new / "joins-ss04.tsv", "a", encoding="utf-8") as handle:
        handle.write("qsTea.full\tqsMay.loop.en-y9\tx-height\t0\n")
    out = tmp_path / "out"
    manifest = build.build_table_diff(out, taken.directory, new, spec=fixtures.mini_spec())
    assert build.check_output_dir(out) == []
    (unit,) = json.loads((out / manifest["classes"][0]["shards"][0]).read_text(encoding="utf-8"))
    assert unit["codepoints"] is None and unit["text_entities"] is None and unit["pair"] is None
    assert unit["notation"] == unit["row_key"] == "join qsTea.full + qsMay.loop.en-y9: none -> x-height+0"
    assert unit["configs"] == ["ss04"] and unit["kinds"] == ["added"]
    assert unit["cluster"] == diff_units.structural_cluster(
        diff_units.Unit(
            diff_units.JOIN,
            diff_units.JoinChange("a", "b", None, ("x-height", 0)),
            ("ss07",),
            None,
            ("added",),
            "",
        )
    )
    assert manifest["human_unit_ids"] == [unit["id"]]


def test_an_empty_diff_builds_a_corpus_with_no_units(builds, tmp_path):
    accepted, _edited, taken = builds
    out = tmp_path / "out"
    manifest = build.build_table_diff(out, taken.directory, accepted, spec=fixtures.mini_spec())
    assert manifest["totals"] == {"units": 0, "rows": 0, "batches": 0}
    assert manifest["classes"] == [] and manifest["human_unit_ids"] == [] and manifest["table_rows"] == {}
    assert manifest["baseline"] == taken.baseline()
    assert build.check_output_dir(out) == []


def test_no_snapshot_builds_a_corpus_with_no_units_over_the_after_font_alone(builds, tmp_path):
    _accepted, edited, _taken = builds
    out = tmp_path / "out"
    manifest = build.build_table_diff(out, None, edited)
    assert manifest["baseline"] == snapshot.ABSENT_BASELINE
    assert manifest["totals"]["units"] == 0
    assert manifest["fonts"]["before"]["sha256"] == manifest["fonts"]["after"]["sha256"]
    assert build.check_output_dir(out) == []


def test_two_builds_of_the_same_inputs_write_the_same_bytes(corpus, builds, tmp_path):
    """The determinism gate of doc/review-design.md §6: a second build of the same snapshot and tables writes a byte-identical manifest and shards, `generated_at` included, because the stamp is the latest input mtime (`build._generated_at`)."""
    out, _manifest, _shards = corpus
    _accepted, edited, taken = builds
    again = tmp_path / "again"
    build.build_table_diff(again, taken.directory, edited, spec=_without_tea_extension(fixtures.mini_spec()))
    first = {path.relative_to(out): path.read_bytes() for path in out.rglob("*") if path.is_file()}
    second = {path.relative_to(again): path.read_bytes() for path in again.rglob("*") if path.is_file()}
    assert sorted(first) == sorted(second)
    assert [name for name in first if first[name] != second[name]] == []


def test_the_build_writes_a_corpus_the_app_and_the_contract_read(corpus):
    """The whole corpus contract over the build (`check_output_dir`, the only place the manifest-shape and file predicates run over a real build, with the copied fonts held to their sources), and the files the app fetches at boot: each class's one shard, `units/<class-id>.json`, a JSON array of the class's fragments in id order, and nothing else under `units/`."""
    out, manifest, _shards = corpus
    assert build.check_output_dir(out, REPO_ROOT) == []
    for meta in manifest["classes"]:
        assert meta["shards"] == [f"units/{meta['id']}.json"]
        ids = [
            fragment["id"] for fragment in json.loads((out / meta["shards"][0]).read_text(encoding="ascii"))
        ]
        assert ids == sorted(ids)
    assert sorted(path.name for path in (out / "units").iterdir()) == sorted(
        f"{meta['id']}.json" for meta in manifest["classes"]
    )


def test_the_manifest_predicates_hold_the_baseline_the_universe_and_the_classes(corpus):
    _out, manifest, _shards = corpus
    assert build.check_manifest(manifest) == []
    for broken, phrase in (
        ({"baseline": {"commit": None, "taken_at": None, "digest": None}}, "baseline must map"),
        ({"baseline": dict(snapshot.ABSENT_BASELINE)}, "no snapshot holds no units"),
        ({"configs": ["default"]}, "every settled configuration"),
        ({"classes": [{**manifest["classes"][0], "id": "changed"}]}, "is not a table-diff class"),
        ({"classes": [{**manifest["classes"][0], "no_verdict": True}]}, "no_verdict must be False"),
    ):
        assert any(phrase in error for error in build.check_manifest({**manifest, **broken})), phrase


def test_the_unit_predicates_hold_the_table_diff_shape(corpus):
    _out, _manifest, shards = corpus
    unit = shards["joins"][0]
    assert build.check_unit(unit) == []
    for broken, phrase in (
        ({"cluster": None}, "human units must carry a cluster"),
        ({"kinds": ["moved"]}, "kinds must name"),
        ({"class": "changed"}, "table-diff class"),
        ({"class": "settlement"}, "a unit that shows a join row is in joins"),
        ({"highlight": None}, "carries its pair_codepoints and highlight"),
        ({"codepoints": None, "notation_tokens": None}, "a unit with no text carries no"),
    ):
        assert any(phrase in error for error in build.check_unit({**unit, **broken})), phrase


def _run(names, clusters, advances=None):
    positions = tuple((0, 0, advance) for advance in (advances or [100] * len(names)))
    return ShapeResult(tuple(names), tuple(clusters), positions)


def test_substitutions_name_every_position_whose_glyph_differs():
    before = _run(["qsDay.full", "qsTea.half.en-y5.en-ext-1", "qsMay.loop"], [0, 1, 2])
    after = _run(["qsDay.full", "qsTea.half.en-y5", "qsMay.loop"], [0, 1, 2])
    assert diff_units.substitutions("abc", before, after) == [
        (
            1,
            diff_units.Substitution(
                "qsTea", "qsDay.full", "qsDay.full", "qsTea.half.en-y5.en-ext-1", "qsTea.half.en-y5"
            ),
        )
    ]
    assert diff_units.substitutions("abc", before, before) == []
    formed = _run(["qsDay_qsUtter.full", "qsTea.half"], [0, 2])
    apart = _run(["qsDay.full", "qsUtter.mono", "qsTea.half"], [0, 1, 2])
    assert diff_units.substitutions("abc", apart, formed) == [
        (
            0,
            diff_units.Substitution(
                "qsDay_qsUtter", diff_units.EDGE, diff_units.EDGE, "qsDay.full", "qsDay_qsUtter.full"
            ),
        )
    ]


def test_the_sweep_holds_every_text_of_at_most_three_letters_around_the_input():
    alphabet = ("a", "b", " ")
    texts = list(diff_units.sweep_texts(alphabet, "X"))
    assert texts[0] == "X" and len(texts) == len(set(texts)) == 1 + 2 * 3 + 3 * 9
    assert all(len(text) <= 3 and "X" in text for text in texts)
    assert [len(text) for text in texts] == sorted(len(text) for text in texts)
    pairs = list(diff_units.pair_texts(alphabet, "L", "R"))
    assert pairs[0] == "LR" and len(pairs) == 1 + 3 + 3 + 9


def test_labels_name_their_families_and_spell_their_letters():
    spec = fixtures.mini_spec()
    assert diff_units.label_families("qsDay_qsUtter.full.ex-y5") == ("qsDay", "qsUtter")
    assert diff_units.label_families("uni200C") == () and diff_units.label_families(diff_units.EDGE) == ()
    assert diff_units.spelling(spec, "qsTea.noentry") == (0xE652,)
    assert diff_units.spelling(spec, "qsDay_qsUtter") == (0xE653, 0xE67A)
    assert diff_units.spelling(spec, "qsNowhere") is None


def test_the_fold_takes_the_text_that_shows_the_change_under_the_most_configurations():
    finder = diff_units.UnitFinder.__new__(diff_units.UnitFinder)
    per_config = {"default": {"ab"}, "ss03": {"cd"}, "ss04": {"cd"}, "ss05": {"abc"}}
    shows = {("ab", "ss05"), ("cd", "default")}
    covered = list(finder._cover(per_config, lambda text, config: (text, config) in shows))
    assert covered == [("cd", frozenset({"default", "ss03", "ss04"})), ("abc", frozenset({"ss05"}))]


# --- the glyph and kern buckets ------------------------------------------------------

MOVED_GLYPH = "qsDay.full.ex-y0"


def _edited_font(source: Path, target: Path) -> None:
    """Copy the font at `source` to `target` with ·Day's full baseline exit a pixel further right and the namer dot's advance a pixel wider, two edits that leave every table as it was."""
    from fontTools.ttLib import TTFont

    font = TTFont(str(source))
    for lookup in font["GPOS"].table.LookupList.Lookup:
        for subtable in lookup.SubTable:
            subtable = subtable.ExtSubTable if lookup.LookupType == 9 else subtable
            if getattr(subtable, "LookupType", lookup.LookupType) != 3:
                continue
            for name, record in zip(subtable.Coverage.glyphs, subtable.EntryExitRecord):
                if name == MOVED_GLYPH and record.ExitAnchor is not None:
                    record.ExitAnchor.XCoordinate += 50
    advance, lsb = font["hmtx"].metrics["periodcentered"]
    font["hmtx"].metrics["periodcentered"] = (advance + 50, lsb)
    font.save(str(target))


@pytest.fixture(scope="module")
def glyph_corpus(builds, tmp_path_factory):
    accepted, _edited, taken = builds
    root = tmp_path_factory.mktemp("glyph-corpus")
    new = _copy(accepted, root / "new")
    _edited_font(accepted / "Senior-Regular.otf", new / "Senior-Regular.otf")
    out = root / "out"
    manifest = build.build_table_diff(out, taken.directory, new, spec=fixtures.mini_spec())
    (meta,) = manifest["classes"]
    return out, manifest, json.loads((out / meta["shards"][0]).read_text(encoding="utf-8"))


def test_a_drawing_edit_that_leaves_the_tables_is_a_glyph_unit_over_a_certificate(glyph_corpus):
    """Moving a glyph's exit anchor changes no table row, so only the glyph bucket sees it: one unit in `glyphs`, over the shortest certificate whose rule settles to the glyph, under every configuration whose run of that text holds it, with the follower moved a pixel."""
    out, manifest, units = glyph_corpus
    assert build.check_output_dir(out) == []
    assert manifest["table_rows"] == {}
    assert manifest["font_changes"] == {"glyphs": {"anchors": 1, "metrics": 1}, "kern": {}}
    (moved,) = [unit for unit in units if unit["row_key"] == f"glyph {MOVED_GLYPH}: anchors"]
    assert moved["class"] == "glyphs" and moved["kinds"] == ["anchors"] and moved["group"] == "qsDay"
    assert moved["kerning"] is False and moved["no_verdict"] is False
    assert MOVED_GLYPH in moved["after"]["cells"] and len(moved["codepoints"].split(":")) >= 2
    assert not moved["ink_identical"] and not moved["picture_identical"]
    assert moved["cluster"] and moved["id"] in manifest["human_unit_ids"]
    assert "the shortest certificate of the accepted" in moved["explain"]
    assert "cursive anchors changed" in moved["summary"]


def test_a_glyph_outside_quikscript_is_shown_by_its_code_point_alone_in_its_own_group(glyph_corpus):
    """The namer dot is in no Quikscript family, so its unit is in the non-Quikscript group, over U+00B7 alone; its wider advance moves no ink in that text, so the unit is machine-approved."""
    _out, manifest, units = glyph_corpus
    (dot,) = [unit for unit in units if unit["row_key"] == "glyph periodcentered: metrics"]
    assert dot["codepoints"] == "00B7" and dot["group"] == diff_units.NON_QUIKSCRIPT_GROUP
    assert "its code point alone" in dot["explain"]
    assert dot["ink_identical"] is True and dot["cluster"] is None
    assert dot["id"] not in manifest["human_unit_ids"]


def test_glyph_changes_read_outline_metrics_and_anchors_and_leave_out_the_lookup_an_anchor_sits_in(
    builds, tmp_path
):
    accepted, _edited, _taken = builds
    assert diff_units.glyph_changes(accepted / "Senior-Regular.otf", accepted / "Senior-Regular.otf") == []
    edited = tmp_path / "Senior-Regular.otf"
    _edited_font(accepted / "Senior-Regular.otf", edited)
    changes = diff_units.glyph_changes(accepted / "Senior-Regular.otf", edited)
    assert [(change.name, change.differs) for change in changes] == [
        ("periodcentered", ("metrics",)),
        (MOVED_GLYPH, ("anchors",)),
    ]
    assert all(change.old != change.new for change in changes)
    records = [(0, [1, 2], None), (3, None, [4, 5])]
    assert diff_units._anchor_pairs(records) == diff_units._anchor_pairs(
        [(7, [1, 2], None), (9, None, [4, 5])]
    )


def test_a_glyph_unit_merges_with_a_join_unit_over_the_same_text_and_a_kern_unit_merges_with_neither():
    def unit(kind, subject, text="ab", kinds=("changed",)):
        return diff_units.Unit(kind, subject, ("default",), text, kinds, "a test")

    join = unit(
        diff_units.JOIN, diff_units.JoinChange("qsIt.a", "qsTea.x", ("x-height", 1), None), kinds=("removed",)
    )
    glyph = unit(
        diff_units.GLYPH, diff_units.GlyphChange("qsTea.x", "a", "b", ("outline",)), kinds=("outline",)
    )
    other = unit(
        diff_units.GLYPH, diff_units.GlyphChange("qsTea.y", "a", "c", ("anchors",)), kinds=("anchors",)
    )
    kern = unit(diff_units.KERN, diff_units.KernChange("qsIt", "qsTea[half]", "qsIt", "qsTea", -1, 0))
    kern_too = unit(diff_units.KERN, diff_units.KernChange("qsIt", "qsTea[full]", "qsIt", "qsTea", None, -1))
    merged = diff_units.merge([glyph, kern, join, other, kern_too])
    assert [[part.kind for part in item.parts] for item in merged] == [
        [diff_units.JOIN, diff_units.GLYPH, diff_units.GLYPH],
        [diff_units.KERN, diff_units.KERN],
    ]
    assert merged[0].kinds == ("anchors", "outline", "removed")
    assert diff_units.CLASS_OF_KIND[merged[0].kind] == "joins"
    assert merged[1].kerning and not merged[0].kerning
    assert "1 join row and 2 redrawn glyphs" in diff_units._summary(merged[0])
    (glyphs,) = diff_units.merge([glyph, other])
    assert diff_units.CLASS_OF_KIND[glyphs.kind] == "glyphs"
    assert diff_units._label(glyphs) == "glyph qsTea.x: outline; glyph qsTea.y: anchors"


def _kern_table(rows) -> str:
    header = "# kern table\nleading\tleading_side\tfollowing\tfollowing_side\tby\trecord\tconfigs\tmixed\n"
    return header + "".join("\t".join(map(str, row)) + "\n" for row in rows)


def test_kern_changes_diff_by_side_pair_and_pass_over_a_pair_kerned_by_nothing(tmp_path):
    every = ",".join(tablediff.SETTLED_CONFIGS)
    old = tmp_path / "old.tsv"
    new = tmp_path / "new.tsv"
    old.write_text(
        _kern_table(
            [
                ("qsIt", "qsIt", "qsTea", "qsTea[half]", -1, "glyph_data/runes/qsIt.yaml:kern[0]", every, ""),
                ("qsIt", "qsIt", "qsMay", "qsMay", 0, "", every, ""),
                ("qsIt", "qsIt", "qsDay", "qsDay", -1, "glyph_data/runes/qsIt.yaml:kern[1]", every, ""),
            ]
        ),
        encoding="utf-8",
    )
    new.write_text(
        _kern_table(
            [
                ("qsIt", "qsIt", "qsTea", "qsTea[half]", -2, "glyph_data/runes/qsIt.yaml:kern[0]", every, ""),
                ("qsIt", "qsIt", "qsPea", "qsPea", 1, "glyph_data/runes/qsIt.yaml:kern[1]", "default", ""),
                ("qsIt", "qsIt", "qsLow", "qsLow", 0, "", every, ""),
            ]
        ),
        encoding="utf-8",
    )
    changes = diff_units.kern_changes(diff_units.load_kern(old), diff_units.load_kern(new))
    assert [
        (change.following_side, change.old, change.new, change.bucket, index)
        for change, _row, index in changes
    ] == [
        ("qsDay", -1, None, "removed", None),
        ("qsPea", None, 1, "added", 1),
        ("qsTea[half]", -1, -2, "changed", 0),
    ]
    units = diff_units.kern_units(changes, tablediff.SETTLED_CONFIGS)
    assert [unit.text for unit in units] == ["", "", ""]
    assert units[1].configs == ("default",) and units[2].configs == tablediff.SETTLED_CONFIGS
    assert units[2].records == ("glyph_data/runes/qsIt.yaml:kern[0]",)
    assert all(unit.kind == diff_units.KERN and unit.found_by == "the two letters alone" for unit in units)


def test_a_moved_kern_value_is_a_unit_drawn_with_kerning_on_that_takes_no_verdict(builds, tmp_path):
    """A kern edit leaves the tables and every glyph as they were and moves a row of `kern.tsv`: one unit in `kerning`, drawn with kerning on over the kerning inventory's witness of the pair, outside the triage index."""
    accepted, _edited, taken = builds
    new = _copy(accepted, tmp_path / "new")
    every = ",".join(tablediff.SETTLED_CONFIGS)
    (new / "kern.tsv").write_text(
        _kern_table(
            [("qsTea", "qsTea", "qsMay", "qsMay[loop]", -1, "glyph_data/runes/qsTea.yaml:kern[0]", every, "")]
        ),
        encoding="utf-8",
    )
    inventory = {
        "sides": [{"name": "qsTea"}, {"name": "qsMay[loop]"}],
        "pairs": [{"lead": 0, "follow": 1, "witness": 0, "at": 1}],
        "texts": [{"cp": [0xE670, 0xE652, 0xE665], "config": 0, "glyphs": []}],
        "configs": ["default"],
        "glyphs": [],
    }
    (new / "kern-inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    out = tmp_path / "out"
    manifest = build.build_table_diff(out, taken.directory, new, spec=fixtures.mini_spec())
    assert build.check_output_dir(out) == []
    assert manifest["font_changes"] == {"glyphs": {}, "kern": {"added": 1}}
    (meta,) = manifest["classes"]
    assert (meta["id"], meta["section"], meta["no_verdict"]) == ("kerning", "no-review-needed", True)
    (unit,) = json.loads((out / meta["shards"][0]).read_text(encoding="utf-8"))
    assert unit["row_key"] == "kern qsTea + qsMay[loop]: none -> -1"
    assert unit["codepoints"] == "E670:E652:E665" and unit["kerning"] is True and unit["no_verdict"] is True
    assert unit["cluster"] is None and manifest["human_unit_ids"] == []
    assert unit["provenance"] == ["glyph_data/runes/qsTea.yaml:kern[0]"]
    assert "the kerning inventory's witness of the pair" in unit["explain"]
    assert any("kerning must be true" in error for error in build.check_unit({**unit, "kerning": False}))


def test_the_kerned_comparator_shapes_with_kerning_on_where_the_other_turns_it_off(builds):
    accepted, _edited, _taken = builds
    seen: list[dict] = []

    class Recording(Shaper):
        def __init__(self, shaper):
            self.shaper = shaper

        def shape(self, text, features=None):
            seen.append(dict(features or {}))
            return self.shaper.shape(text, features)

    fonts = diff_units.Fonts(accepted / "Senior-Regular.otf", accepted / "Senior-Regular.otf")
    for kerning in (False, True):
        comparator = fonts.comparator_for(kerning)
        shaper, outlines = comparator._sides["before"]
        comparator._sides["before"] = (Recording(shaper), outlines)
        comparator.run_ink("before", TEXT, {"ss03": True})
    assert seen == [{"ss03": True, "kern": False}, {"ss03": True, "kern": True}]
    assert fonts.comparator_for(True) is fonts.comparator_for(True)
    assert isinstance(fonts.comparator_for(True), diff_units.KernedInkComparator)


def test_the_manifest_carries_the_table_diff_fingerprint(corpus, builds):
    from engine.pipeline import fingerprint

    _out, manifest, _shards = corpus
    _accepted, _edited, taken = builds
    fp = manifest["inputs_fingerprint"]
    assert tuple(fp) == fingerprint.TABLE_DIFF_COMPONENTS
    assert fp["snapshot"] == fingerprint.file_sha256(taken.directory / snapshot.RECORD_NAME)
    assert fp["tables"] == "fp-edited"
    assert fp["accept_sweep"] == sweep_record.NO_FINDS
    for broken, phrase in (
        ({"inputs_fingerprint": {**fp, "fonts": "x"}}, "table-diff input components"),
        ({"inputs_fingerprint": {**fp, "snapshot": None}}, "a hash exactly when a snapshot is present"),
        ({"font_changes": {"glyphs": {}}}, "font_changes must map"),
    ):
        assert any(phrase in error for error in build.check_manifest({**manifest, **broken})), phrase
