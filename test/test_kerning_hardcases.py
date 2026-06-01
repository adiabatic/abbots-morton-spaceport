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
DATA_PATH = ROOT / "test" / "kerning-hardcases.json"

TOOLS_PATH = str(ROOT / "tools")
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)

from build_font import generate_kern_fea

ALLOWED_SKIP_REASONS = {
    "ligature",
    "entryless",
    "shared_kern_entangled",
    "no_context",
    "cluster_ambiguous",
    "not_hidden",
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


def _matches_form(glyph_name: str, form: str) -> bool:
    return glyph_name == form or glyph_name.startswith(form + ".")


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

            if _base_name(left_name) != left_family:
                failures.append(
                    f"{label}: left junction base {_base_name(left_name)!r} ({left_name!r}) "
                    f"!= {left_family!r}"
                )
            if _base_name(right_name) != right_family:
                failures.append(
                    f"{label}: right junction base {_base_name(right_name)!r} ({right_name!r}) "
                    f"!= {right_family!r}"
                )

            left_form = junction["leftForm"]
            if left_form is None:
                if left_name != left_family:
                    failures.append(
                        f"{label}: leftForm is null but left junction glyph {left_name!r} "
                        f"is not the bare family {left_family!r}"
                    )
            elif not _matches_form(left_name, left_form):
                failures.append(
                    f"{label}: left junction glyph {left_name!r} does not match leftForm {left_form!r}"
                )

            right_form = junction["rightForm"]
            if right_form is None:
                if right_name != right_family:
                    failures.append(
                        f"{label}: rightForm is null but right junction glyph {right_name!r} "
                        f"is not the bare family {right_family!r}"
                    )
            elif not _matches_form(right_name, right_form):
                failures.append(
                    f"{label}: right junction glyph {right_name!r} does not match rightForm {right_form!r}"
                )

    _assert_no_failures(failures, limit=20)


def test_skipped_reasons_are_intentional() -> None:
    data = _load_data()
    failures: list[str] = []
    for entry in data["_skipped"]:
        reason = entry["reason"]
        if reason not in ALLOWED_SKIP_REASONS:
            failures.append(f"unexpected skip reason {reason!r} for {entry!r}")
    _assert_no_failures(failures, limit=20)

    they_utter = [
        entry for entry in data["_skipped"] if entry["entry"].get("trigger_form") == "qsThey_qsUtter.noentry"
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
        "right_form": ["qsUtter.alt.ex-y0"],
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


def test_generate_kern_fea_left_form_and_except_left() -> None:
    all_glyph_names = [
        "qsNo",
        "qsNo.alt",
        "qsNo.alt.en-y0",
        "qsNo.en-ext-1",
        "qsUtter",
        "qsUtter.alt",
        "qsUtter.alt.ex-y0",
    ]
    left_form_def = {
        "left_form": ["qsNo.alt"],
        "right_family": ["qsUtter"],
        "value": -1,
    }
    except_left_def = {
        "left_family": ["qsNo"],
        "except_left": ["qsNo.alt"],
        "right_family": ["qsUtter"],
        "value": -1,
    }
    fea = generate_kern_fea({"lf": left_form_def, "el": except_left_def}, {}, all_glyph_names, 50)

    assert _left_set(fea, "lf") == {"qsNo.alt", "qsNo.alt.en-y0"}
    assert _left_set(fea, "el") == {"qsNo", "qsNo.en-ext-1"}
