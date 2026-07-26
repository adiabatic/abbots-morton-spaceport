"""Tests for the echo-group agreement rule shared by the two tools that report it: `review_docket.verdicts_agree` itself, the echo-fill and disagreement audit in echo_verdicts.py, and the conflicts list review_docket.py bakes into the docket data."""

import json

import pytest

from rebuild.tools import echo_verdicts as ev
from rebuild.tools import review_docket as rd

STAMP = "2026-07-10T00:00:00Z"


def unit(uid, echo, cls="live-class"):
    return {
        "id": uid,
        "batch": 1,
        "echo": echo,
        "cluster": f"c-{echo[2:]}",
        "class": cls,
        "configs": ["default"],
        "notation": "·Pea·Tea",
    }


def v(unit_id, verdict, note="", at="2026-07-10T01:00:00Z"):
    return {"unit": unit_id, "verdict": verdict, "note": note, "at": at}


def surface_with(tmp_path, units, classes=()):
    surface = tmp_path / "surface"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP, "classes": list(classes)}))
    (surface / "units" / "shard.json").write_text(json.dumps(units))
    return surface


def verdicts_file(tmp_path, records, name="verdicts.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"manifest_generated_at": STAMP, "verdicts": list(records)}))
    return path


@pytest.mark.parametrize(
    "kinds,agree",
    [
        (set(), True),
        ({"approve"}, True),
        ({"identical"}, True),
        ({"approve", "identical"}, True),
        ({"approve", "either"}, False),
        ({"identical", "either"}, False),
        ({"approve", "reject"}, False),
        ({"approve", "identical", "neither"}, False),
    ],
)
def test_verdicts_agree_admits_only_unanimity_and_the_approve_identical_mix(kinds, agree):
    assert rd.verdicts_agree(kinds) is agree


def test_an_approve_identical_group_fills_its_blanks_from_the_newest_member(tmp_path, monkeypatch, capsys):
    surface = surface_with(
        tmp_path, [unit("u-0001", "e-0001"), unit("u-0002", "e-0001"), unit("u-0003", "e-0001")]
    )
    verdicts = verdicts_file(
        tmp_path,
        [
            v("u-0001", "approve", note="looks right", at="2026-07-10T01:00:00Z"),
            v("u-0002", "identical", note="no visible change", at="2026-07-10T02:00:00Z"),
        ],
    )
    out = tmp_path / "fill.json"
    monkeypatch.setattr(
        "sys.argv",
        ["echo_verdicts.py", str(verdicts), "--surface", str(surface), "--out", str(out)],
    )
    ev.main()

    fills = json.loads(out.read_text())["verdicts"]
    assert [record["unit"] for record in fills] == ["u-0003"]
    assert fills[0]["verdict"] == "identical"
    assert fills[0]["note"] == "[echo-fill from u-0002] no visible change"
    assert "no echo group holds disagreeing verdicts" in capsys.readouterr().out


def test_a_real_split_still_reports_and_fills_nothing(tmp_path, monkeypatch, capsys):
    surface = surface_with(
        tmp_path, [unit("u-0001", "e-0001"), unit("u-0002", "e-0001"), unit("u-0003", "e-0001")]
    )
    verdicts = verdicts_file(
        tmp_path, [v("u-0001", "identical"), v("u-0002", "reject", note="stub too long")]
    )
    out = tmp_path / "fill.json"
    monkeypatch.setattr(
        "sys.argv",
        ["echo_verdicts.py", str(verdicts), "--surface", str(surface), "--out", str(out)],
    )
    ev.main()

    assert json.loads(out.read_text())["verdicts"] == []
    printed = capsys.readouterr().out
    assert "1 echo groups hold disagreeing verdicts" in printed
    assert "e-0001  #units=u-0001,u-0002,u-0003" in printed


def test_the_baked_docket_lists_the_split_group_and_not_the_approve_identical_one(
    tmp_path, monkeypatch, capsys
):
    surface = surface_with(
        tmp_path,
        [
            unit("u-0001", "e-0001"),
            unit("u-0002", "e-0001"),
            unit("u-0003", "e-0002"),
            unit("u-0004", "e-0002"),
        ],
    )
    verdicts = verdicts_file(
        tmp_path,
        [
            v("u-0001", "approve"),
            v("u-0002", "identical"),
            v("u-0003", "approve"),
            v("u-0004", "neither"),
        ],
    )
    data_out = tmp_path / "docket-data.json"
    monkeypatch.setattr(
        "sys.argv",
        ["review_docket.py", str(verdicts), "--surface", str(surface), "--data-out", str(data_out)],
    )
    rd.main()

    conflicts = json.loads(data_out.read_text())["conflicts"]
    assert [entry["echo"] for entry in conflicts] == ["e-0002"]
    assert conflicts[0]["verdicts"] == {"u-0003": "approve", "u-0004": "neither"}
    assert "1 echo groups disagree" in capsys.readouterr().out
