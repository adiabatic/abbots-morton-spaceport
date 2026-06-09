import json
import re
import sys
from pathlib import Path

import uharfbuzz as hb

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
    """Assert the rendered ``glyph`` matches the junction's per-side selector: the right base family, and a kind that agrees with the glyph's discrete-alternate trait (``alt``/``half``/``plain``) or with the explicit stance-prefix for table-derived ``stance`` selectors."""
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
    """·No·Utter must surface all three hidden alternate-stance combinations and reserve (alt, plain) as the isolated grid cell."""
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
    """Map each (left, right) glyph pair to the single lookup tag that kerns it, asserting no pair is claimed twice."""
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
    """The four quadrant lookups the page emits for an alt pair (plain/alt on either side, carved with both-sided except) must partition the family×family space without overlap."""
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

    # Every (qsNo*, qsUtter*) pair is covered exactly once across the four quadrants.
    no_glyphs = [g for g in all_glyph_names if g.startswith("qsNo")]
    utter_glyphs = [g for g in all_glyph_names if g.startswith("qsUtter")]
    for left in no_glyphs:
        for right in utter_glyphs:
            assert (left, right) in cover, f"{(left, right)} kerned by no quadrant"
