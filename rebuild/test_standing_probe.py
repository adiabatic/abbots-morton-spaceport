"""Tests for the standing-approval probe, the read-only instrument the rule-writing skill works from: that every family and cell listing it prints comes out in code-point order rather than alphabetically, which is the order the rules file is written in and so the order a survey can be pasted from; that its two silent degradations now label the exact reading they invalidate — a verdicts file stamped for another manifest makes every verdict unknown rather than the positive claim BLANK, and a surface with no font pair says which columns and which composed line are missing and why; that `--shapes` is walked off `standing_verdicts.SHAPES` at runtime, one row per shape with the symptom its own matcher's docstring opens with, and that a run resolving no unit id prints that menu too; that `--find` is a plain substring match over notations, blanks first and capped with the total stated, because one letter pair reaches thousands of records; that `--coverage` re-runs a rule's own survey relaxed of everything the rule names and reports the followers, forms and cells it does not, once, as a docket rather than an instruction — and says plainly when the rule's shape has no enumeration to run; and that the redrawn trade a `--reading` line names is never truncated, since a redrawn rule is written from exactly that list. Everything here is hermetic: a synthetic surface under tmp_path, a rules file beside it, and no live build artifact anywhere, which is the standard every file of this suite is held to."""

import json

import pytest

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
    """Everything but the stale-stamp warning itself, which names BLANK to say what it is standing in for."""
    return "\n".join(line for line in out.splitlines() if "stamped for another manifest" not in line)


def _section(out, header):
    lines = out.splitlines()
    body = []
    for line in lines[lines.index(header) + 1 :]:
        if not line.startswith("    "):
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
    """·Vie before ·May before ·At before ·Ah, and a bare family before the ligature that leads with it — the order the rules file and the skill are written in, where sorted() would give ·Ah, ·At, ·May, ·Vie and every survey would need reordering by hand."""
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
    """The warning alone left every unit printing the positive claim BLANK off records the tool never read; now the reading it invalidates carries the label."""
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
    """Silently dropping the rendered-grain columns and the composed line would read as a window with nothing to say at that grain rather than as a surface that cannot be asked."""
    units = [tea_window("u-1", "qsVie", "qsVie/normal/baseline/None/", deltas={"default": DELTA})]
    out = _run(tmp_path, capsys, units, ["u-1"])
    assert probe.NO_FONTS in out
    assert "composed:" not in out


def test_shapes_lists_every_row_of_the_shapes_table(capsys):
    """A coverage assertion, not a restatement: each entry's text is sourced from that shape's own matcher docstring, so a shape entering SHAPES enters the menu without this test being touched."""
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
    """The cap is load-bearing rather than tidy — one letter pair matches thousands of records — so the total is stated and the blanks, which are the only ones a rule can fill, come first."""
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
    """No second reading of the data-expect grammar lives here: `parse_expect` in test/test_shaping.py is its authority, and a substring is all this needs to be."""
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
    assert "--extension-cells" in out and "--retarget-cells" in out
    assert "does not name" not in out


def test_coverage_of_an_unknown_rule_id_says_so(tmp_path, capsys):
    units = [tea_window("c-1", "qsVie", "qsVie/normal/baseline/None/")]
    out = _run(tmp_path, capsys, units, ["--coverage", "no-such-rule"])
    assert "no-such-rule: no rule by that id in this rules file" in out


class _Intern:
    def __init__(self, shapes):
        self._shapes = shapes

    def cells(self, key):
        return self._shapes[key]


@pytest.mark.parametrize("count", [3, 9])
def test_the_redrawn_trade_is_never_truncated(count):
    """A redrawn rule is written from exactly this dropped/added list, so a cap on it costs a hand re-derivation — and the only caller runs for units the reader named, so there is no bulk listing to protect."""
    painted = {(column, 0) for column in range(count)}
    kept = {(column, 1) for column in range(count)}
    intern = _Intern({"before": painted, "after": kept})
    reading = probe._reading(intern, ("qsX", "before", 0, 0, 0), ("qsX", "after", PIXEL_SIZE, 0, 0))
    for column in range(count):
        assert f"[{column}, 0]" in reading
        assert f"[{column}, 1]" in reading
