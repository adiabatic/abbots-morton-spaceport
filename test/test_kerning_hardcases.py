import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import uharfbuzz as hb
import yaml

from quikscript_shaping_helpers import (
    _assert_no_failures,
    _compiled_meta,
    _font,
    _gid_to_full_name,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "site" / "kerning-hardcases.json"

TOOLS_PATH = str(ROOT / "tools")
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)

from build_font import generate_kern_fea
from build_kerning_hardcases import _glyph_kind

ALLOWED_SKIP_REASONS = {
    "ligature",
    "entryless",
    "shared_kern_entangled",
    "no_context",
    "cluster_ambiguous",
    "not_hidden",
    "superseded_by_alt_axis",
}


def _load_data() -> dict:
    with DATA_PATH.open() as f:
        return json.load(f)


def _shape_with_clusters(text: str) -> list[tuple[int, str]]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [(info.cluster, _gid_to_full_name(info.codepoint)) for info in buf.glyph_infos]


def _base_name(glyph_name: str) -> str:
    meta = _compiled_meta().get(glyph_name)
    return meta.base_name if meta is not None else glyph_name


def _matches_stance(glyph_name: str, stance: str) -> bool:
    return glyph_name == stance or glyph_name.startswith(stance + ".")


def test_context_reshapes_to_junction() -> None:
    data = _load_data()
    failures: list[str] = []
    for key, junctions in data.items():
        if key == "_skipped":
            continue
        left_family, right_family = key.split("|")
        for index, junction in enumerate(junctions):
            label = f"{key}[{index}]"
            context = junction["context"]
            before_end = junction["beforeEnd"]
            junction_end = junction["junctionEnd"]
            shaped = _shape_with_clusters(context)

            left_pos = next(
                (i for i, (cluster, _name) in enumerate(shaped) if cluster >= before_end),
                None,
            )
            if left_pos is None:
                failures.append(f"{label}: no output glyph with cluster >= beforeEnd={before_end}")
                continue
            if left_pos + 1 >= len(shaped):
                failures.append(f"{label}: junction left glyph at {left_pos} has no following glyph")
                continue

            left_cluster, left_name = shaped[left_pos]
            right_cluster, right_name = shaped[left_pos + 1]

            if left_cluster != before_end:
                failures.append(
                    f"{label}: left junction cluster {left_cluster} != beforeEnd {before_end} "
                    f"(shaped {shaped})"
                )
                continue
            if not (before_end < right_cluster <= junction_end):
                failures.append(
                    f"{label}: right junction cluster {right_cluster} not in "
                    f"({before_end}, {junction_end}] (shaped {shaped})"
                )
                continue
            if left_pos + 2 < len(shaped):
                after_cluster = shaped[left_pos + 2][0]
                if after_cluster < junction_end:
                    failures.append(
                        f"{label}: glyph after junction has cluster {after_cluster} < "
                        f"junctionEnd {junction_end} (shaped {shaped})"
                    )
                    continue

            _check_side(failures, label, "left", junction["left"], left_family, left_name)
            _check_side(failures, label, "right", junction["right"], right_family, right_name)

    _assert_no_failures(failures, limit=20)


def _check_side(failures: list[str], label: str, side: str, selector: dict, family: str, glyph: str) -> None:
    """Append a failure unless the shaped ``glyph`` belongs to ``family`` and matches the side's selector: its stance prefix for a ``stance`` selector, or its alternate kind from ``_glyph_kind`` otherwise."""
    if selector["family"] != family:
        failures.append(f"{label} {side}: selector family {selector['family']!r} != key family {family!r}")
    if _base_name(glyph) != family:
        failures.append(f"{label} {side}: rendered base {_base_name(glyph)!r} ({glyph!r}) != {family!r}")

    kind = selector["kind"]
    stance = selector["stance"]
    if kind == "stance":
        if not stance or not _matches_stance(glyph, stance):
            failures.append(f"{label} {side}: glyph {glyph!r} does not match stance selector {stance!r}")
    elif kind == "plain":
        if stance is not None:
            failures.append(f"{label} {side}: plain selector unexpectedly carries stance {stance!r}")
        if _glyph_kind(glyph) != "plain":
            failures.append(f"{label} {side}: glyph {glyph!r} is {_glyph_kind(glyph)!r}, not plain")
    else:  # an alternate axis, e.g. "alt"
        if stance != f"{family}.{kind}":
            failures.append(f"{label} {side}: {kind} selector stance {stance!r} != {family}.{kind!r}")
        if _glyph_kind(glyph) != kind or not _matches_stance(glyph, f"{family}.{kind}"):
            failures.append(
                f"{label} {side}: glyph {glyph!r} ({_glyph_kind(glyph)!r}) is not {kind} of {family!r}"
            )


def test_no_utter_alt_combos() -> None:
    """·No·Utter lists the three hidden alternate-stance combinations, and (alt, plain) is its one isolated grid cell."""
    data = _load_data()
    junctions = data["qsNo|qsUtter"]
    hidden = {(j["left"]["kind"], j["right"]["kind"]) for j in junctions if not j["isolated"]}
    assert hidden == {("plain", "plain"), ("plain", "alt"), ("alt", "alt")}, hidden
    isolated = [j for j in junctions if j["isolated"]]
    assert len(isolated) == 1, isolated
    assert (isolated[0]["left"]["kind"], isolated[0]["right"]["kind"]) == ("alt", "plain")


def test_skipped_reasons_are_intentional() -> None:
    data = _load_data()
    failures: list[str] = []
    for entry in data["_skipped"]:
        reason = entry["reason"]
        if reason not in ALLOWED_SKIP_REASONS:
            failures.append(f"unexpected skip reason {reason!r} for {entry!r}")
    _assert_no_failures(failures, limit=20)

    they_utter = [
        entry
        for entry in data["_skipped"]
        if entry["entry"].get("trigger_stance") == "qsThey_qsUtter.noentry"
    ]
    assert they_utter, "expected qsThey_qsUtter.noentry ligature trigger in _skipped"
    assert all(
        entry["reason"] == "ligature" for entry in they_utter
    ), f"qsThey_qsUtter.noentry skip must use reason 'ligature', got {they_utter!r}"


def _right_set(fea: str, tag: str) -> set[str]:
    pattern = re.compile(
        rf"lookup kern_{re.escape(tag)} \{{\s*pos \[(?P<left>[^\]]*)\] \[(?P<right>[^\]]*)\] (?P<value>-?\d+);"
    )
    match = pattern.search(fea)
    assert match is not None, f"no pos lookup found for tag {tag!r} in:\n{fea}"
    return set(match.group("right").split())


def _left_set(fea: str, tag: str) -> set[str]:
    pattern = re.compile(
        rf"lookup kern_{re.escape(tag)} \{{\s*pos \[(?P<left>[^\]]*)\] \[(?P<right>[^\]]*)\] (?P<value>-?\d+);"
    )
    match = pattern.search(fea)
    assert match is not None, f"no pos lookup found for tag {tag!r} in:\n{fea}"
    return set(match.group("left").split())


def _value(fea: str, tag: str) -> int:
    pattern = re.compile(
        rf"lookup kern_{re.escape(tag)} \{{\s*pos \[(?P<left>[^\]]*)\] \[(?P<right>[^\]]*)\] (?P<value>-?\d+);"
    )
    match = pattern.search(fea)
    assert match is not None, f"no pos lookup found for tag {tag!r} in:\n{fea}"
    return int(match.group("value"))


def test_generate_kern_fea_carve_out_and_override_are_disjoint() -> None:
    all_glyph_names = [
        "qsNo",
        "qsNo.alt",
        "qsNo.alt.en-y0",
        "qsNo.en-ext-1",
        "qsUtter",
        "qsUtter.alt",
        "qsUtter.alt.ex-y0",
    ]
    carve = {
        "left_family": ["qsNo"],
        "right_family": ["qsUtter"],
        "except_right": ["qsUtter.alt.ex-y0"],
        "value": -1,
    }
    override = {
        "left_family": ["qsNo"],
        "right_stance": ["qsUtter.alt.ex-y0"],
        "value": -2,
    }
    fea = generate_kern_fea({"carve": carve, "override": override}, {}, all_glyph_names, 50)

    carve_right = _right_set(fea, "carve")
    override_right = _right_set(fea, "override")

    assert "qsUtter.alt.ex-y0" not in carve_right
    assert not any(g.startswith("qsUtter.alt.ex-y0.") for g in carve_right)
    assert "qsUtter" in carve_right
    assert override_right == {"qsUtter.alt.ex-y0"}
    assert carve_right.isdisjoint(override_right)

    assert _value(fea, "carve") == -50
    assert _value(fea, "override") == -100


def test_generate_kern_fea_left_stance_and_except_left() -> None:
    all_glyph_names = [
        "qsNo",
        "qsNo.alt",
        "qsNo.alt.en-y0",
        "qsNo.en-ext-1",
        "qsUtter",
        "qsUtter.alt",
        "qsUtter.alt.ex-y0",
    ]
    left_stance_def = {
        "left_stance": ["qsNo.alt"],
        "right_family": ["qsUtter"],
        "value": -1,
    }
    except_left_def = {
        "left_family": ["qsNo"],
        "except_left": ["qsNo.alt"],
        "right_family": ["qsUtter"],
        "value": -1,
    }
    fea = generate_kern_fea({"lf": left_stance_def, "el": except_left_def}, {}, all_glyph_names, 50)

    assert _left_set(fea, "lf") == {"qsNo.alt", "qsNo.alt.en-y0"}
    assert _left_set(fea, "el") == {"qsNo", "qsNo.en-ext-1"}


def _coverage(fea: str) -> dict[tuple[str, str], str]:
    """Map each (left, right) glyph pair to the lookup tag that kerns it, and assert that no pair is kerned by two lookups."""
    pattern = re.compile(
        r"lookup kern_(?P<tag>\w+) \{\s*pos \[(?P<left>[^\]]*)\] \[(?P<right>[^\]]*)\] -?\d+;"
    )
    cover: dict[tuple[str, str], str] = {}
    for match in pattern.finditer(fea):
        tag = match.group("tag")
        for left in match.group("left").split():
            for right in match.group("right").split():
                assert (
                    left,
                    right,
                ) not in cover, f"{(left, right)} kerned by both {cover[(left, right)]} and {tag}"
                cover[(left, right)] = tag
    return cover


def test_generate_kern_fea_both_sides_partition_is_disjoint() -> None:
    """The four quadrant rules `site/kerning.html` writes for an alt pair (plain or alt on each side, using `except_left` and `except_right`) kern every family × family glyph pair exactly once."""
    all_glyph_names = [
        "qsNo",
        "qsNo.alt",
        "qsNo.alt.en-y0",
        "qsNo.en-ext-1",
        "qsUtter",
        "qsUtter.alt",
        "qsUtter.alt.ex-y0",
    ]
    quadrants = {
        "pp": {
            "left_family": ["qsNo"],
            "except_left": ["qsNo.alt"],
            "right_family": ["qsUtter"],
            "except_right": ["qsUtter.alt"],
            "value": -1,
        },
        "pa": {
            "left_family": ["qsNo"],
            "except_left": ["qsNo.alt"],
            "right_stance": ["qsUtter.alt"],
            "value": -3,
        },
        "aa": {"left_stance": ["qsNo.alt"], "right_stance": ["qsUtter.alt"], "value": -1},
        "ap": {
            "left_stance": ["qsNo.alt"],
            "right_family": ["qsUtter"],
            "except_right": ["qsUtter.alt"],
            "value": -1,
        },
    }
    fea = generate_kern_fea(quadrants, {}, all_glyph_names, 50)
    cover = _coverage(fea)

    no_glyphs = [g for g in all_glyph_names if g.startswith("qsNo")]
    utter_glyphs = [g for g in all_glyph_names if g.startswith("qsUtter")]
    for left in no_glyphs:
        for right in utter_glyphs:
            assert (left, right) in cover, f"{(left, right)} kerned by no quadrant"


KERNING_PAGE_GLYPHS = [
    "qsExcite",
    "qsExcite.en-y0",
    "qsExcite.en-y0.noexit",
    "qsGay",
    "qsGay.ex-y0",
    "qsGay.ex-y0.ex-ext-1",
    "qsGay.ex-y5",
]

KERNING_PAGE_OVERRIDES = [
    {"left": "qsExcite.en-y0.noexit", "right": None, "value": -3},
    {"left": "qsExcite.en-y0.noexit", "right": "qsGay.ex-y0", "value": 1},
    {"left": "qsExcite.en-y0.noexit", "right": "qsGay.ex-y0.ex-ext-1", "value": 2},
    {"left": None, "right": "qsGay.ex-y5", "value": -2},
]


def _kerning_page(call: str, payload: object) -> Any:
    script = textwrap.dedent("""
        import { readFileSync } from 'node:fs';
        import { inheritedValue, overrideIsRedundant, partitionPair, reconstructOverrides } from './site/kerning-rules.js';
        const input = JSON.parse(readFileSync(0, 'utf8'));
        const excite = { family: 'qsExcite', stance: null, except: null, glyph: false };
        const gay = { family: 'qsGay', stance: null, except: null, glyph: false };
        process.stdout.write(JSON.stringify(%s));
        """) % call
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def _partition_docs(cell_value: int) -> list[dict]:
    bodies = _kerning_page(
        "partitionPair(excite, gay, input.cell, input.overrides)",
        {"cell": cell_value, "overrides": KERNING_PAGE_OVERRIDES},
    )
    return [yaml.safe_load(body) for body in bodies]


def _prefix_depth(prefix: str | None) -> int:
    return -1 if prefix is None else len(prefix)


def _intended_value(left: str, right: str, cell_value: int) -> int:
    matching = [
        o
        for o in KERNING_PAGE_OVERRIDES
        if (o["left"] is None or _matches_stance(left, o["left"]))
        and (o["right"] is None or _matches_stance(right, o["right"]))
    ]
    if not matching:
        return cell_value
    best = max(matching, key=lambda o: (_prefix_depth(o["left"]), _prefix_depth(o["right"])))
    return best["value"]


def test_kerning_page_partition_gives_each_glyph_pair_one_intended_value() -> None:
    """The kerning page writes a pair's cell and its nested, left-only and right-only junction overrides as rules that kern every glyph pair once, with the most specific override's value or the cell value."""
    docs = _partition_docs(-1)
    fea = generate_kern_fea({f"d{i}": doc for i, doc in enumerate(docs)}, {}, KERNING_PAGE_GLYPHS, 50)
    cover = _coverage(fea)
    for left in (g for g in KERNING_PAGE_GLYPHS if g.startswith("qsExcite")):
        for right in (g for g in KERNING_PAGE_GLYPHS if g.startswith("qsGay")):
            assert (left, right) in cover, f"{(left, right)} kerned by no rule"
            assert _value(fea, cover[(left, right)]) == 50 * _intended_value(
                left, right, -1
            ), f"{(left, right)} kerned by {cover[(left, right)]}"


def test_kerning_page_reads_its_partition_back_as_the_same_overrides() -> None:
    """The kerning page reads the carved rules it wrote back into the overrides it wrote them from, ignoring a carve-out that names another family's stance, as a rule written for several families at once does."""
    docs = _partition_docs(-1)
    cell_docs = [d for d in docs if "left_family" in d and "right_family" in d]
    assert [d["value"] for d in cell_docs] == [-1]

    def side(doc: dict, which: str) -> dict:
        node = doc.get(f"{which}_stance", [None])[0]
        foreign = ["qsTea.alt"] if node is None else []
        return {"node": node, "except": doc.get(f"except_{which}", []) + foreign}

    rules = [
        {"left": side(d, "left"), "right": side(d, "right"), "value": d["value"]}
        for d in docs
        if d not in cell_docs
    ]
    overrides = _kerning_page(
        "reconstructOverrides('qsExcite', 'qsGay', input.cell, input.rules)", {"cell": -1, "rules": rules}
    )
    key = lambda o: (str(o["left"]), str(o["right"]))
    assert sorted(overrides, key=key) == sorted(KERNING_PAGE_OVERRIDES, key=key)


def test_kerning_page_writes_and_reads_back_a_zero_override() -> None:
    """The kerning page writes a junction override of 0 inside a nonzero cell and a nonzero enclosing override as explicit `value: 0` rules, and reads them back as the same zero override."""
    overrides = [
        {"left": "qsExcite.en-y0.noexit", "right": "qsGay.ex-y0", "value": 1},
        {"left": "qsExcite.en-y0.noexit", "right": "qsGay.ex-y0.ex-ext-1", "value": 0},
    ]
    bodies = _kerning_page(
        "partitionPair(excite, gay, input.cell, input.overrides)", {"cell": -1, "overrides": overrides}
    )
    docs = [yaml.safe_load(body) for body in bodies]
    fea = generate_kern_fea({f"d{i}": doc for i, doc in enumerate(docs)}, {}, KERNING_PAGE_GLYPHS, 50)
    cover = _coverage(fea)
    assert ("qsExcite.en-y0.noexit", "qsGay.ex-y0.ex-ext-1") in cover
    assert _value(fea, cover[("qsExcite.en-y0.noexit", "qsGay.ex-y0.ex-ext-1")]) == 0

    rules = [
        {
            "left": {"node": d.get("left_stance", [None])[0], "except": d.get("except_left", [])},
            "right": {"node": d.get("right_stance", [None])[0], "except": d.get("except_right", [])},
            "value": d["value"],
        }
        for d in docs
        if not ("left_family" in d and "right_family" in d)
    ]
    read_back = _kerning_page(
        "reconstructOverrides('qsExcite', 'qsGay', input.cell, input.rules)", {"cell": -1, "rules": rules}
    )
    key = lambda o: (str(o["left"]), str(o["right"]))
    assert sorted(read_back, key=key) == sorted(overrides, key=key)


def test_kerning_page_junction_inherits_its_enclosing_override_or_the_cell() -> None:
    """A junction without an override of its own takes the value of the most specific override that holds it, or the cell value when none does."""
    inherited = _kerning_page(
        "[inheritedValue(-1, input, 'qsExcite.en-y0.noexit', 'qsGay.ex-y0.ex-ext-1'), inheritedValue(-1, input, 'qsExcite.en-y0', 'qsGay.ex-y5')]",
        [
            {"left": "qsExcite.en-y0.noexit", "right": None, "value": -3},
            {"left": "qsExcite.en-y0.noexit", "right": "qsGay.ex-y0", "value": 0},
        ],
    )
    assert inherited == [0, -1]


def test_kerning_page_keeps_an_override_that_decides_a_deeper_glyph_pair() -> None:
    """A junction override that equals the value it inherits is still kept when it decides a deeper glyph pair that another override would take over without it, and is droppable when it decides none."""
    enclosing = {"left": "qsThey.en-y5", "right": "qsGay.ex-y0", "value": -1}
    deeper_right = {"left": "qsThey.en-y5", "right": "qsGay.ex-y0.ex-ext-1", "value": -3}
    junction = {"left": "qsThey.en-y5.en-ext-1", "right": "qsGay.ex-y0", "value": -1}
    redundant = _kerning_page(
        "input.map((overrides) => overrideIsRedundant(0, overrides, 'qsThey.en-y5.en-ext-1', 'qsGay.ex-y0'))",
        [[enclosing, deeper_right, junction], [enclosing, junction]],
    )
    assert redundant == [False, True]
