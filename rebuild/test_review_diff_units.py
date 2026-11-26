"""Tests for the table-diff corpus (`rebuild/review/diff_units.py` and `build.build_table_diff`): the units an edit's visible changes make, folded across configurations and left neighbors and merged where they show one text, with their clusters, groups, pair bands and ids, the quiet and provenance units, a join row no text holds, the zero-unit corpus of an empty diff and of a machine with no snapshot, and the table-diff contract predicates.

Every corpus here is built from real builds of the pipeline's mini fixture spec (`fixtures.mini_spec`): its tables and windows heads from `run_m1.build_tables`, and its font compiled from them as the readback tests compile one, without the kern stage, whose ss10 rule the mini ·May does not meet. The edit removes ·Tea's x-height entry extension, so ·Day+Utter ~x~ ·Tea loses a pixel under every configuration whose ·Tea is half there, which is every settled configuration but those with ss03 on. The rows a synthetic test edits are copies of the unedited build's, which keep their windows heads.
"""

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from rebuild.pipeline import compile_font, emit_gpos, emit_gsub, fixtures, run_m1
from rebuild.review import app_index, build, diff_units, snapshot, tablediff, unit_cache
from rebuild.review.enrich import LETTERS
from rebuild.review.ink import IDENTITY_DIFF
from rebuild.validation.shaping import ShapeResult

CHANGED = ("default", "ss04", "ss05", "ss07", "ss08")


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
    tables, _digests = run_m1.build_tables(spec, out, inputs=inputs)
    cell_glyphs = run_m1.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_m1.mint_raw_glyphs(spec)
    curs = {**cell_glyphs, **bare, **copies}
    plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_copies=ss10_copies)
    compile_font.build_font(
        {**curs, **run_m1.namer_dot_glyphs()},
        plan.fea_text + "\n" + emit_gpos.emit_gpos(curs, spec=spec),
        out / "M1.otf",
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
    assert app_index.outputs_not_current(out) == []
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
    comparator = diff_units.Fonts(accepted / "M1.otf", edited / "M1.otf").comparator
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
    from rebuild.review.ink import delta_digest

    fonts = diff_units.Fonts(accepted / "M1.otf", edited / "M1.otf")
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
        assert unit["id"] == unit_cache.unit_id_for(unit["content_key"])
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
            ("ss08",),
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
    assert app_index.outputs_not_current(out) == []


def test_no_snapshot_builds_a_corpus_with_no_units_over_the_after_font_alone(builds, tmp_path):
    _accepted, edited, _taken = builds
    out = tmp_path / "out"
    manifest = build.build_table_diff(out, None, edited)
    assert manifest["baseline"] == snapshot.ABSENT_BASELINE
    assert manifest["totals"]["units"] == 0
    assert manifest["fonts"]["before"]["sha256"] == manifest["fonts"]["after"]["sha256"]
    assert build.check_output_dir(out) == []


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
    assert build.check_unit(unit, "table-diff") == []
    for broken, phrase in (
        ({"cluster": None}, "human units must carry a cluster"),
        ({"duplicate_group": "e-11111111111"}, "duplicate_group null"),
        ({"kinds": ["moved"]}, "kinds must name"),
        ({"class": "changed"}, "table-diff class"),
        ({"class": "settlement"}, "a unit that shows a join row is in joins"),
        ({"highlight": None}, "carries its pair_codepoints and highlight"),
        ({"codepoints": None, "notation_tokens": None}, "a unit with no text carries no"),
    ):
        assert any(phrase in error for error in build.check_unit({**unit, **broken}, "table-diff")), phrase


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
