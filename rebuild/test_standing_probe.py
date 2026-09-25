"""Tests for rebuild/tools/standing_probe.py, the read-only tool used to write standing-approval rules. They check that every family and cell list prints in code-point order, the order the rules file is written in, so a survey can be pasted from directly, and that an `--extension-cells` header for a contraction names only the contraction it lists. They check that a verdicts file stamped for another manifest prints every verdict as UNKNOWN_VERDICT instead of BLANK, and that a surface with no font pair says which columns and which composed line are missing and why. They check that `--shapes` lists every entry of `standing_verdicts.SHAPES` with the opening words of its matcher's docstring, that a run whose unit ids all miss the surface prints the same list, and that a listing run such as `--extension-cells` does not. They check that `--find` is a substring match over notations that lists blanks first and is capped with the total stated. They check that `--survey` groups every position under a before-glyph prefix by before form, after cell, left family, seam changes, and follower, each with a verdict tally and in code-point order, that it can narrow to one after cell, that it counts the windows it cannot place, and that it says when no window carries the glyph. They check that `--coverage` re-runs a rule's enumeration without the rule's named lists and prints, once, the followers, forms, and cells the rule does not name, for every shape except ligature and ink-delta, and that it says so when a rule's shape has no enumeration. They also check `_cell_glyph_name` against `cell_label` over the mini spec, and that the dropped and added pixels in a redrawn reading are never truncated. Every test uses a synthetic surface and rules file under tmp_path, or the frozen mini bundle, and reads no live build artifact."""

import json

import pytest

from rebuild.pipeline.geometry import HEIGHT_Y
from rebuild.pipeline.model import CellId
from rebuild.pipeline.settle import cell_label
from rebuild.review import enrich
from rebuild.tools import standing_probe as probe
from rebuild.tools import standing_verdicts as sv
from rebuild.validation.classify import PIXEL_SIZE

STAMP = "2026-08-01T00:00:00Z"
OTHER_STAMP = "2026-01-01T00:00:00Z"
DELTA = "d-abcdefabcdef"

EXT_RULE = {
    "id": "tea-vie-exit-extension-dropped",
    "verdict": "approve",
    "note": "·Vie sits a pixel closer to ·Tea",
    "match": {
        "before": {
            "pivot": "qsTea",
            "exit_extension": "ex-ext-1",
            "seam_out": "y0",
            "follower": "qsVie",
        },
        "after": {
            "pivot_cells": ["qsTea/full/None/baseline/"],
            "follower_cells": ["qsVie/normal/baseline/None/"],
        },
        "except_left": [],
    },
}

RETARGET_RULE = {
    "id": "tea-no-xheight-join-retargeted",
    "verdict": "approve",
    "note": "·Tea sits as the full bar joining ·No at the baseline",
    "match": {
        "before": {"pivot": "qsTea.half", "seam_out": "y5", "follower": "qsNo"},
        "after": {
            "retarget": "y0",
            "pivot_cells": ["qsTea/full/None/baseline/"],
            "receiver_cells": ["qsNo/flipped/baseline/None/"],
            "shift": -1,
            "follower_shift": 0,
        },
        "except_left": [],
    },
}

INK_RULE = {
    "id": "i-smaller-loop-after-baseline-entry",
    "verdict": "approve",
    "note": "·I loops more tightly after a baseline entry",
    "match": {"after": {"ink_deltas": [DELTA]}, "except_left": []},
}

GAP_RULE = {
    "id": "no-gay-baseline-join-dropped",
    "verdict": "approve",
    "note": "·Gay sits two columns further from a raised ·No",
    "match": {
        "before": {"pivot": "qsNo", "seam_out": "y0", "follower": "qsGay"},
        "after": {
            "gap": 2,
            "pivot_cells": ["qsNo/loop/x-height/None/"],
            "receiver_cells": ["qsGay/hapax/None/None/"],
        },
        "except_left": [],
    },
}

GAP_RULE_BARE = {
    "id": "at-it-xheight-join-dropped",
    "verdict": "approve",
    "note": "·It sits a column further from ·At",
    "match": {
        "before": {"pivot": "qsAt", "seam_out": "y5", "follower": "qsIt"},
        "after": {"gap": 1},
        "except_left": [],
    },
}

REDRAWN_RULE = {
    "id": "utter-gay-exit-extension-dropped",
    "verdict": "approve",
    "note": "·Gay sits a pixel closer to ·Utter",
    "match": {
        "before": {"pivots": ["qsUtter.ex-ext-1"]},
        "after": {
            "pivots": ["qsUtter.mono.ex-y5", "qsUtter.mono.en-y0.ex-y5"],
            "dropped": [[5, 5]],
            "added": [],
            "shift": -1,
        },
        "except_left": [],
    },
}

ENTRY_RULE = {
    "id": "gay-entry-contracted",
    "verdict": "approve",
    "note": "·Gay's baseline entry pulls in one pixel after ·Bay",
    "match": {
        "before": {"left": ["qsBay"], "pivots": ["qsGay.en-y0.ex-y5"]},
        "after": {
            "pivots": ["qsGay.hapax.en-y0.en-con-1", "qsGay.hapax.en-y0.ex-y5.en-con-1"],
            "entry_contraction": 1,
        },
        "except_left": [],
    },
}


def unit(uid, glyphs, seams, cells, after_seams, *, codepoints, notation="·X ~b~ ·Y", deltas=None):
    return {
        "id": uid,
        "batch": 0,
        "no_verdict": False,
        "render_groups": [{"configs": ["default"]}],
        "class": "c-1",
        "echo": None,
        "notation": notation,
        "codepoints": codepoints,
        "configs": ["default"],
        "ink_deltas": deltas,
        "before": {"glyphs": list(glyphs), "seams": list(seams)},
        "after": {"cells": list(cells), "seams": list(after_seams)},
        "pair": None,
        "secondary_seams": [],
    }


def _codepoints(follower):
    return ":".join(["E652"] + ["E000"] * sv._components(follower))


def tea_window(uid, follower, follower_cell, *, pivot_cell="qsTea/full/None/baseline/", **kwargs):
    """One window where ·Tea gives up its one-column exit extension into a follower at the baseline."""
    return unit(
        uid,
        ["qsTea.ex-ext-1", follower],
        ["y0"],
        [pivot_cell, follower_cell],
        ["y0"],
        codepoints=_codepoints(follower),
        **kwargs,
    )


def retarget_window(uid, follower, follower_cell, **kwargs):
    """One window where half-·Tea's x-height join into a follower comes down to the baseline."""
    return unit(
        uid,
        ["qsTea.half", follower],
        ["y5"],
        ["qsTea/full/None/baseline/", follower_cell],
        ["y0"],
        codepoints=_codepoints(follower),
        **kwargs,
    )


def window(uid, glyphs, cells, seams, after_seams, **kwargs):
    """One window spelled out on both sides, its codepoints counted off the before glyphs."""
    codepoints = ":".join(["E000"] * sum(sv._components(sv._family(name)) for name in glyphs))
    return unit(uid, glyphs, seams, cells, after_seams, codepoints=codepoints, **kwargs)


def _surface(tmp_path, units):
    surface = tmp_path / "review"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": STAMP, "classes": [{"id": "all", "shards": ["units/all.json"]}]})
    )
    (surface / "units" / "all.json").write_text(json.dumps(units))
    return surface


def _run(tmp_path, capsys, units, argv, *, rules=(EXT_RULE,), records=(), stamp=STAMP):
    surface = _surface(tmp_path, units)
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(json.dumps({"format": sv.FORMAT, "rules": list(rules)}))
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        json.dumps(
            {
                "format": "ams-review-verdicts/1",
                "manifest_generated_at": stamp,
                "verdicts": list(records),
            }
        )
    )
    probe.main(
        [
            *argv,
            "--surface",
            str(surface),
            "--rules",
            str(rules_path),
            "--verdicts",
            str(verdicts_path),
        ]
    )
    return capsys.readouterr().out


def _line(out, prefix):
    return next(line for line in out.splitlines() if line.startswith(prefix))


def _past_the_warning(out):
    """Returns the output without the stale-stamp warning line, which itself mentions BLANK."""
    return "\n".join(line for line in out.splitlines() if "stamped for another manifest" not in line)


def _section(out, header):
    lines = out.splitlines()
    body = []
    for line in lines[lines.index(header) + 1 :]:
        if not line.startswith("    "):
            break
        body.append(line.strip())
    return body


def _survey_groups(out):
    """Returns the survey's group lines, each a before form and the after cell it settles into. Group lines are indented two spaces and their rows eight."""
    return [
        line.strip() for line in out.splitlines() if line.startswith("  ") and not line.startswith("        ")
    ]


def _survey_rows(out, group):
    lines = out.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == group)
    body = []
    for line in lines[start + 1 :]:
        if not line.startswith("        "):
            break
        body.append(line.strip())
    return body


CODE_POINT_WINDOWS = [
    ("e-1", "qsAh", "qsAh/hapax/baseline/None/"),
    ("e-2", "qsAt", "qsAt/rising/baseline/None/"),
    ("e-3", "qsMay", "qsMay/loop/baseline/None/"),
    ("e-4", "qsVie", "qsVie/normal/baseline/None/"),
    ("e-5", "qsVie_qsUtter", "qsVie_qsUtter/hapax/baseline/None/"),
]


def test_family_and_cell_listings_come_out_in_code_point_order(tmp_path, capsys):
    """Lists print in code-point order (·Vie, ·May, ·At, ·Ah), with a bare family before a ligature that starts with it. The rules file and the skill are written in this order; sorted() would give ·Ah, ·At, ·May, ·Vie."""
    units = [tea_window(uid, follower, cell) for uid, follower, cell in CODE_POINT_WINDOWS]
    units.append(
        tea_window("e-6", "qsVie", "qsVie/normal/baseline/None/", pivot_cell="qsTea/full/x-height/baseline/")
    )
    out = _run(tmp_path, capsys, units, ["--extension-cells", "qsTea", "ex-ext-1", "y0"])
    assert _line(out, "followers:") == "followers: ['qsVie', 'qsVie_qsUtter', 'qsMay', 'qsAt', 'qsAh']"
    assert _line(out, "follower cells:") == (
        "follower cells: ['qsVie/normal/baseline/None/', 'qsVie_qsUtter/hapax/baseline/None/', "
        "'qsMay/loop/baseline/None/', 'qsAt/rising/baseline/None/', 'qsAh/hapax/baseline/None/']"
    )
    assert _line(out, "pivot cells:") == (
        "pivot cells: ['qsTea/full/None/baseline/', 'qsTea/full/x-height/baseline/']"
    )


def test_a_contraction_survey_header_names_only_the_contraction_it_lists(tmp_path, capsys):
    """An `ex-con-N` survey lists only pivot cells carrying exactly that contraction after a glyph with no exit extension, and its header says just that."""
    units = [
        window(
            "c-1",
            ["qsEt", "qsVie"],
            ["qsEt/full/None/baseline/ex-con-1", "qsVie/normal/baseline/None/"],
            ["y0"],
            ["y0"],
        ),
        window(
            "c-2",
            ["qsEt", "qsVie"],
            ["qsEt/full/None/baseline/ex-con-2", "qsVie/normal/baseline/None/"],
            ["y0"],
            ["y0"],
        ),
        window(
            "c-3",
            ["qsEt.ex-ext-1", "qsVie"],
            ["qsEt/full/None/baseline/ex-con-1", "qsVie/normal/baseline/None/"],
            ["y0"],
            ["y0"],
        ),
    ]
    out = _run(tmp_path, capsys, units, ["--extension-cells", "qsEt", "ex-con-1", "y0"])
    assert _line(out, "windows where") == (
        "windows where a qsEt glyph with no exit extension exits at y0 on both sides into a cell carrying ex-con-1:"
    )
    assert _line(out, "      1  qsEt/full/None/baseline/ex-con-1  →  qsVie") == (
        "      1  qsEt/full/None/baseline/ex-con-1  →  qsVie  qsVie/normal/baseline/None/  {'BLANK': 1}"
    )
    assert _line(out, "pivot cells:") == "pivot cells: ['qsEt/full/None/baseline/ex-con-1']"


def test_the_retarget_survey_orders_its_cells_the_same_way(tmp_path, capsys):
    units = [
        retarget_window("r-1", "qsNo", "qsNo/flipped/baseline/None/"),
        retarget_window("r-2", "qsNo", "qsNo/flipped/baseline/baseline/"),
    ]
    out = _run(tmp_path, capsys, units, ["--retarget-cells", "qsTea.half", "y5", "qsNo", "y0"])
    assert _line(out, "follower cells:") == (
        "follower cells: ['qsNo/flipped/baseline/None/', 'qsNo/flipped/baseline/baseline/']"
    )


def test_a_stale_verdicts_stamp_labels_every_verdict_it_invalidates(tmp_path, capsys):
    """A verdicts file stamped for another manifest says nothing about this surface, so every verdict prints as UNKNOWN_VERDICT, and BLANK appears only in the warning."""
    units = [
        tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/", deltas={"default": DELTA}),
        tea_window("u-2", "qsMay", "qsMay/loop/baseline/None/", deltas={"default": DELTA}),
    ]
    out = _run(tmp_path, capsys, units, ["u-1"], stamp=OTHER_STAMP)
    assert "is stamped for another manifest" in out
    assert f"verdict {probe.UNKNOWN_VERDICT}" in out
    assert f"2 human units — {{'{probe.UNKNOWN_VERDICT}': 2}}" in out
    assert "BLANK" not in _past_the_warning(out)


def test_a_stale_stamp_labels_the_survey_tallies_too(tmp_path, capsys):
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["--extension-cells", "qsTea", "ex-ext-1", "y0"], stamp=OTHER_STAMP)
    assert f"{{'{probe.UNKNOWN_VERDICT}': 1}}" in out
    assert "BLANK" not in _past_the_warning(out)


def test_blank_only_says_it_cannot_be_answered_under_a_stale_stamp(tmp_path, capsys):
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/", notation="·Tea ~b~ ·Vie")]
    out = _run(tmp_path, capsys, units, ["--find", "·Vie", "--blank-only"], stamp=OTHER_STAMP)
    assert "--blank-only cannot be answered from a stale verdicts stamp" in out
    assert "  u-1  " in out


def test_a_missing_font_pair_says_what_it_costs(tmp_path, capsys):
    """Without a font pair, the rendered-grain columns and the composed line are left out and NO_FONTS says so, so the output is not mistaken for a window with nothing to report at that grain."""
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/", deltas={"default": DELTA})]
    out = _run(tmp_path, capsys, units, ["u-1"])
    assert probe.NO_FONTS in out
    assert "composed:" not in out


def test_shapes_lists_every_row_of_the_shapes_table(capsys):
    """Each entry's text comes from its matcher's docstring, so a shape added to SHAPES appears in the menu and is checked here without editing this test."""
    probe.main(["--shapes"])
    out = capsys.readouterr().out
    for name, shape in sv.SHAPES.items():
        assert f"  {name}  — declared by match.after.{shape.keyed_by}" in out
        assert " ".join((shape.matcher.__doc__ or "").split()[:8]) in out


def test_a_mistyped_unit_still_yields_the_shape_menu(tmp_path, capsys):
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["u-nope"])
    assert "u-nope: not a human unit on this surface" in out
    for name in sv.SHAPES:
        assert f"  {name}  — declared by" in out


def test_a_survey_run_is_not_interrupted_by_the_menu(tmp_path, capsys):
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["--extension-cells", "qsTea", "ex-ext-1", "y0"])
    assert "declared by match.after." not in out


FIND_TOTAL = 25
FIND_VERDICTED = 20


def _find_units():
    units = [
        tea_window(
            f"f-{index:02}",
            "qsVie",
            "qsVie/normal/baseline/None/",
            notation="·Tea ~b~ ·Vie",
        )
        for index in range(FIND_TOTAL)
    ]
    units.append(tea_window("other", "qsMay", "qsMay/loop/baseline/None/", notation="·Tea ~b~ ·May"))
    return units


def test_find_states_the_total_caps_the_listing_and_puts_blanks_first(tmp_path, capsys):
    """The listing is capped because one letter pair matches thousands of records. The total is stated, and blanks, the only units a rule can fill, come first."""
    records = [
        {"unit": f"f-{index:02}", "verdict": "approve", "note": "", "at": STAMP}
        for index in range(FIND_VERDICTED)
    ]
    out = _run(tmp_path, capsys, _find_units(), ["--find", "·Vie"], records=records)
    assert (
        f"{FIND_TOTAL} human units whose notation contains '·Vie' ({FIND_TOTAL - FIND_VERDICTED} blank)"
        in out
    )
    assert f"showing {probe.FIND_LIMIT}, blanks first" in out
    rows = [line for line in out.splitlines() if line.startswith("  f-")]
    assert len(rows) == probe.FIND_LIMIT
    assert [row.split()[1] for row in rows[: FIND_TOTAL - FIND_VERDICTED]] == ["BLANK"] * (
        FIND_TOTAL - FIND_VERDICTED
    )
    assert all(row.split()[1] == "approve" for row in rows[FIND_TOTAL - FIND_VERDICTED :])
    assert "  other  " not in out


def test_find_takes_its_own_limit_and_a_blank_only_filter(tmp_path, capsys):
    records = [
        {"unit": f"f-{index:02}", "verdict": "approve", "note": "", "at": STAMP}
        for index in range(FIND_VERDICTED)
    ]
    out = _run(
        tmp_path, capsys, _find_units(), ["--find", "·Vie", "--blank-only", "--limit", "2"], records=records
    )
    rows = [line for line in out.splitlines() if line.startswith("  f-")]
    assert len(rows) == 2
    assert all(row.split()[1] == "BLANK" for row in rows)
    assert f"{FIND_TOTAL - FIND_VERDICTED} human units whose notation contains" in out


def test_find_matches_the_notation_as_plain_text(tmp_path, capsys):
    """`--find` does not parse the data-expect grammar, whose authority is `parse_expect` in test/test_shaping.py. A substring match is enough."""
    units = [
        tea_window("f-1", "qsVie", "qsVie/normal/baseline/None/", notation="·Tea ~b~ ·Vie"),
        tea_window("f-2", "qsVie", "qsVie/normal/baseline/None/", notation="·Tea | ·Vie"),
    ]
    out = _run(tmp_path, capsys, units, ["--find", "~b~"])
    assert "1 human units whose notation contains '~b~'" in out
    assert "  f-1  " in out
    assert "  f-2  " not in out


COVERAGE_WINDOWS = [
    ("c-1", "qsVie", "qsVie/normal/baseline/None/"),
    ("c-2", "qsMay", "qsMay/loop/baseline/None/"),
    ("c-3", "qsAt", "qsAt/rising/baseline/None/"),
]


def test_coverage_names_the_followers_forms_and_cells_a_rule_does_not(tmp_path, capsys):
    units = [tea_window(uid, follower, cell) for uid, follower, cell in COVERAGE_WINDOWS]
    units.append(
        tea_window("c-4", "qsVie", "qsVie/normal/baseline/None/", pivot_cell="qsTea/full/x-height/baseline/")
    )
    records = [{"unit": "c-2", "verdict": "approve", "note": "", "at": STAMP}]
    out = _run(tmp_path, capsys, units, ["--coverage", EXT_RULE["id"]], records=records)
    assert _section(out, "  follower families the rule does not name:") == [
        "1  qsMay  {'approve': 1}",
        "1  qsAt  {'BLANK': 1}",
    ]
    assert _section(out, "  follower cells the rule does not name:") == [
        "1  qsMay/loop/baseline/None/  {'approve': 1}",
        "1  qsAt/rising/baseline/None/  {'BLANK': 1}",
    ]
    assert _section(out, "  pivot forms the rule does not name:") == [
        "1  qsTea/full/x-height/baseline/  {'BLANK': 1}"
    ]
    assert out.count(probe.DOCKET_NOTE) == 1


def test_coverage_reports_a_rule_that_already_names_everything(tmp_path, capsys):
    units = [tea_window("c-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["--coverage", EXT_RULE["id"]])
    assert out.count("the rule names every one this enumeration reaches") == 3
    assert probe.DOCKET_NOTE not in out


def test_coverage_dispatches_to_the_retarget_enumeration(tmp_path, capsys):
    units = [
        retarget_window("r-1", "qsNo", "qsNo/flipped/baseline/None/"),
        retarget_window("r-2", "qsMay", "qsMay/loop/baseline/None/"),
    ]
    out = _run(tmp_path, capsys, units, ["--coverage", RETARGET_RULE["id"]], rules=(EXT_RULE, RETARGET_RULE))
    assert _section(out, "  follower families the rule does not name:") == ["1  qsMay  {'BLANK': 1}"]
    assert _section(out, "  follower cells the rule does not name:") == [
        "1  qsMay/loop/baseline/None/  {'BLANK': 1}"
    ]


def test_coverage_says_plainly_when_a_shape_has_no_enumeration(tmp_path, capsys):
    units = [tea_window("c-1", "qsVie", "qsVie/normal/baseline/None/", deltas={"default": DELTA})]
    out = _run(tmp_path, capsys, units, ["--coverage", INK_RULE["id"]], rules=(EXT_RULE, INK_RULE))
    assert "declares the ink-delta shape, which has no relaxed enumeration to run" in out
    assert "--extension-cells" in out and "--retarget-cells" in out and "--survey" in out
    assert "does not name" not in out


def test_coverage_of_an_unknown_rule_id_says_so(tmp_path, capsys):
    units = [tea_window("c-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["--coverage", "no-such-rule"])
    assert "no-such-rule: no rule by that id in this rules file" in out


def test_coverage_answers_for_every_shape_but_ligature_and_ink_delta():
    """A shape added to SHAPES fails this test until COVERAGE_SHAPES covers it or it is added to the exemptions here. Ligature is exempt because it names no forms, and ink-delta because it names digests."""
    assert set(probe.COVERAGE_SHAPES) == set(sv.SHAPES) - {"ligature", "ink-delta"}


GAP_WINDOWS = [
    ("g-1", "qsGay", "qsGay/hapax/None/None/"),
    ("g-2", "qsThaw", "qsThaw/hapax/None/None/"),
    ("g-3", "qsGay", "qsGay/hapax/None/baseline/"),
]


def test_coverage_dispatches_to_the_gap_enumeration(tmp_path, capsys):
    units = [
        window(uid, ["qsNo", follower], ["qsNo/loop/x-height/None/", cell], ["y0"], ["break"])
        for uid, follower, cell in GAP_WINDOWS
    ]
    out = _run(tmp_path, capsys, units, ["--coverage", GAP_RULE["id"]], rules=(EXT_RULE, GAP_RULE))
    assert "(join-dropped shape)" in out
    assert _section(out, "  follower families the rule does not name:") == ["1  qsThaw  {'BLANK': 1}"]
    assert _section(out, "  follower cells the rule does not name:") == [
        "1  qsGay/hapax/None/baseline/  {'BLANK': 1}",
        "1  qsThaw/hapax/None/None/  {'BLANK': 1}",
    ]
    assert "pivot forms: the rule names every one this enumeration reaches" in out


def test_coverage_of_a_gap_rule_without_cell_lists_reads_only_its_followers(tmp_path, capsys):
    """A join-dropped rule without cell lists does not let the pivot redraw, so it cannot be missing any cells, and coverage reports only the follower families it does not name."""
    units = [
        window(
            "g-1",
            ["qsAt", "qsIt"],
            ["qsAt/rising/x-height/None/", "qsIt/normal/None/None/"],
            ["y5"],
            ["break"],
        ),
        window(
            "g-2",
            ["qsAt", "qsMay"],
            ["qsAt/rising/x-height/None/", "qsMay/loop/None/None/"],
            ["y5"],
            ["break"],
        ),
    ]
    out = _run(tmp_path, capsys, units, ["--coverage", GAP_RULE_BARE["id"]], rules=(EXT_RULE, GAP_RULE_BARE))
    assert _section(out, "  follower families the rule does not name:") == ["1  qsMay  {'BLANK': 1}"]
    assert "pivot forms" not in out
    assert "follower cells" not in out


def utter_window(uid, pivot, pivot_cell):
    """One window where an ·Utter form settles into a cell in front of ·Gay at the x-height."""
    return window(uid, [pivot, "qsGay"], [pivot_cell, "qsGay/hapax/x-height/None/"], ["y5"], ["y5"])


def test_coverage_names_the_forms_a_redrawn_rule_does_not(tmp_path, capsys):
    """Coverage reports a before form the rule does not name that settles into an after form it names, and an after form it does not name that a named before form settles into. A position the rule names on neither side is not reported."""
    units = [
        utter_window("r-1", "qsUtter.ex-ext-1", "qsUtter/mono/None/x-height/"),
        utter_window("r-2", "qsUtter.en-y0.ex-ext-1", "qsUtter/mono/baseline/x-height/"),
        utter_window("r-3", "qsUtter.ex-ext-1", "qsUtter/alternate/None/baseline/"),
        utter_window("r-4", "qsUtter", "qsUtter/mono/None/None/"),
    ]
    records = [{"unit": "r-3", "verdict": "approve", "note": "", "at": STAMP}]
    out = _run(
        tmp_path,
        capsys,
        units,
        ["--coverage", REDRAWN_RULE["id"]],
        rules=(EXT_RULE, REDRAWN_RULE),
        records=records,
    )
    assert "(redrawn shape), enumerated over every qsUtter position" in out
    assert _section(out, "  before forms the rule does not name:") == [
        "1  qsUtter.en-y0.ex-ext-1  {'BLANK': 1}"
    ]
    assert _section(out, "  after forms the rule does not name:") == [
        "1  qsUtter.alternate.ex-y0  {'approve': 1}"
    ]
    assert "qsUtter.mono  " not in out and "qsUtter/mono/None/None/" not in out
    assert out.count(probe.DOCKET_NOTE) == 1


def gay_window(uid, left, pivot, pivot_cell):
    """One window where a ·Gay form stands after a left letter at the baseline and joins ·No at the x-height."""
    return window(
        uid,
        [left, pivot, "qsNo"],
        [f"{left}/hapax/None/baseline/", pivot_cell, "qsNo/loop/x-height/None/"],
        ["y0", "y5"],
        ["y0", "y5"],
    )


def test_coverage_names_the_left_families_an_entry_contracted_rule_does_not(tmp_path, capsys):
    units = [
        gay_window("e-1", "qsBay", "qsGay.en-y0.ex-y5", "qsGay/hapax/baseline/x-height/en-con-1"),
        gay_window("e-2", "qsDay", "qsGay.en-y0.ex-y5", "qsGay/hapax/baseline/x-height/en-con-1"),
        gay_window("e-3", "qsDay", "qsGay.en-y0", "qsGay/hapax/baseline/None/en-con-1"),
    ]
    out = _run(tmp_path, capsys, units, ["--coverage", ENTRY_RULE["id"]], rules=(EXT_RULE, ENTRY_RULE))
    assert _section(out, "  left families the rule does not name:") == ["1  qsDay  {'BLANK': 1}"]
    assert "before forms: the rule names every one this enumeration reaches" in out
    assert "after forms: the rule names every one this enumeration reaches" in out


def test_coverage_leaves_a_declined_entry_contraction_form_to_its_companion(tmp_path, capsys):
    rule = json.loads(json.dumps(ENTRY_RULE))
    rule["match"]["before"]["except_pivots"] = ["qsGay.en-y0.ex-y5.en-con-1"]
    units = [
        gay_window("e-1", "qsBay", "qsGay.en-y0.ex-y5", "qsGay/hapax/baseline/x-height/en-con-1"),
        gay_window(
            "e-2",
            "qsDay",
            "qsGay.en-y0.ex-y5.en-con-1",
            "qsGay/hapax/baseline/x-height/en-con-1",
        ),
    ]
    out = _run(tmp_path, capsys, units, ["--coverage", rule["id"]], rules=(EXT_RULE, rule))
    assert "left families: the rule names every one this enumeration reaches" in out
    assert "qsDay" not in out


SURVEY_UNITS = [
    window(
        "s-1",
        ["qsAh", "qsKey", "qsIt"],
        ["qsAh/hapax/None/None/", "qsKey/hapax/None/baseline/ex-con-1", "qsIt/normal/baseline/None/"],
        ["break", "y0"],
        ["break", "y0"],
    ),
    window(
        "s-2",
        ["qsAh", "qsKey", "qsNo"],
        ["qsAh/hapax/None/None/", "qsKey/hapax/None/baseline/ex-con-1", "qsNo/loop/baseline/None/"],
        ["break", "y0"],
        ["break", "y0"],
    ),
    window(
        "s-3",
        ["qsKey.en-y8", "qsMay"],
        ["qsKey/hapax/top/None/", "qsMay/loop/None/None/"],
        ["break"],
        ["break"],
    ),
    window(
        "s-4", ["qsAh", "qsKey"], ["qsAh/hapax/None/None/", "qsKey/hapax/None/None/"], ["break"], ["break"]
    ),
]


def test_survey_groups_positions_by_form_cell_seams_and_follower(tmp_path, capsys):
    """Groups are ordered by before form, then after cell, in code-point order with the token breaking ties. Rows in a group are ordered by left family, seams, and follower in the same order (·No before ·It). A pivot at either end of the window prints EDGE in place of the missing seam and neighbor."""
    records = [{"unit": "s-2", "verdict": "approve", "note": "", "at": STAMP}]
    out = _run(tmp_path, capsys, SURVEY_UNITS, ["--survey", "qsKey"], records=records)
    assert _line(out, "survey of qsKey: 4 positions").endswith("follower family and cell:")
    assert _survey_groups(out) == [
        "1  qsKey  →  qsKey/hapax/None/None/  {'BLANK': 1}",
        "2  qsKey  →  qsKey/hapax/None/baseline/ex-con-1  {'BLANK': 1, 'approve': 1}",
        "1  qsKey.en-y8  →  qsKey/hapax/top/None/  {'BLANK': 1}",
    ]
    assert _survey_rows(
        out, "2  qsKey  →  qsKey/hapax/None/baseline/ex-con-1  {'BLANK': 1, 'approve': 1}"
    ) == [
        "1  left qsAh break→break   out y0→y0 → qsNo qsNo/loop/baseline/None/  {'approve': 1}",
        "1  left qsAh break→break   out y0→y0 → qsIt qsIt/normal/baseline/None/  {'BLANK': 1}",
    ]
    assert _survey_rows(out, "1  qsKey  →  qsKey/hapax/None/None/  {'BLANK': 1}") == [
        f"1  left qsAh break→break   out {probe.EDGE} → {probe.EDGE} {probe.EDGE}  {{'BLANK': 1}}"
    ]
    assert _survey_rows(out, "1  qsKey.en-y8  →  qsKey/hapax/top/None/  {'BLANK': 1}") == [
        f"1  left {probe.EDGE} {probe.EDGE}   out break→break → qsMay qsMay/loop/None/None/  {{'BLANK': 1}}"
    ]


def test_survey_narrows_to_one_after_cell(tmp_path, capsys):
    out = _run(tmp_path, capsys, SURVEY_UNITS, ["--survey", "qsKey", "qsKey/hapax/top/None/"])
    assert "survey of qsKey settling into qsKey/hapax/top/None/: 1 positions" in out
    assert "ex-con-1" not in out


def test_survey_counts_the_windows_it_cannot_place(tmp_path, capsys):
    """A window whose before and after sides do not line up letter for letter has no position the survey can key, so it is counted in the header. A window without the glyph is neither placed nor counted."""
    units = [
        SURVEY_UNITS[3],
        window(
            "s-5",
            ["qsKey", "qsTea", "qsOy"],
            ["qsKey/hapax/None/None/", "qsTea_qsOy/hapax/None/None/"],
            ["break", "y5"],
            ["break"],
        ),
        window(
            "s-6", ["qsAh", "qsMay"], ["qsAh/hapax/None/None/", "qsMay/loop/None/None/"], ["break"], ["break"]
        ),
    ]
    out = _run(tmp_path, capsys, units, ["--survey", "qsKey"])
    header = _line(out, "survey of qsKey: 1 positions")
    assert header.endswith(
        "; 1 windows carry it but do not line up letter for letter, so they are not placed:"
    )


def test_survey_says_when_no_window_carries_the_glyph(tmp_path, capsys):
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["--survey", "qsKey"])
    assert "no human unit carries a glyph under qsKey" in out
    assert "declared by match.after." not in out


def test_a_stale_stamp_labels_the_survey_groups_too(tmp_path, capsys):
    out = _run(tmp_path, capsys, SURVEY_UNITS, ["--survey", "qsKey"], stamp=OTHER_STAMP)
    assert f"{{'{probe.UNKNOWN_VERDICT}': 2}}" in out
    assert "BLANK" not in _past_the_warning(out)


AFTER_CELLS = [
    CellId("qsTea", "full", "x-height", "baseline", ()),
    CellId("qsKey", "hapax", None, "baseline", ("ex-con-1",)),
    CellId("qsMay", "loop", "baseline", "x-height", ("en-con-1", "ex-ext-1")),
    CellId("qsUtter", "mono", None, None, ()),
    *(CellId("qsNo", "loop", height, None, ()) for height in HEIGHT_Y),
]


def test_a_cell_names_its_after_glyph_the_way_settle_labels_it(mini_bundle):
    """`_cell_glyph_name`, which reads no spec, must agree with `cell_label` over every field a cell carries, including each registry height, so a naming change in the pipeline fails this test."""
    spec = enrich.load_spec(mini_bundle.spec_root)
    for cell in AFTER_CELLS:
        assert probe._cell_glyph_name(enrich.cell_token(cell)) == cell_label(spec, cell)


class _Intern:
    def __init__(self, shapes):
        self._shapes = shapes

    def cells(self, key):
        return self._shapes[key]


@pytest.mark.parametrize("count", [3, 9])
def test_the_redrawn_trade_is_never_truncated(count):
    """A redrawn rule's dropped and added lists are copied from this output, so it is never truncated. `_reading` runs only for units the user named, so no bulk listing needs a cap."""
    painted = {(column, 0) for column in range(count)}
    kept = {(column, 1) for column in range(count)}
    intern = _Intern({"before": painted, "after": kept})
    reading = probe._reading(intern, ("qsX", "before", 0, 0, 0), ("qsX", "after", PIXEL_SIZE, 0, 0))
    for column in range(count):
        assert f"[{column}, 0]" in reading
        assert f"[{column}, 1]" in reading
