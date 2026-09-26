"""Tests for the duplicate-group agreement rule, `review_queue.verdicts_agree`, and the two tools that use it: the duplicate fill and disagreement audit in duplicate_verdicts.py, and the conflicts list review_queue.py writes into the queue snapshot."""

import json

import pytest

from rebuild.tools import duplicate_verdicts as dv
from rebuild.tools import review_queue as rq

STAMP = "2026-07-10T00:00:00Z"


def unit(uid, duplicate_group, cls="live-class"):
    return {
        "id": uid,
        "batch": 1,
        "duplicate_group": duplicate_group,
        "cluster": f"c-{duplicate_group[2:]}",
        "class": cls,
        "configs": ["default"],
        "notation": "·Pea·Tea",
    }


def v(unit_id, verdict, note="", at="2026-07-10T01:00:00Z"):
    return {"unit": unit_id, "verdict": verdict, "note": note, "at": at}


def corpus_with(tmp_path, units, classes=()):
    corpus = tmp_path / "corpus"
    (corpus / "units").mkdir(parents=True)
    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": STAMP,
                "classes": [*classes, {"id": "shard", "status": None, "shards": ["units/shard.json"]}],
            }
        )
    )
    (corpus / "units" / "shard.json").write_text(json.dumps(units))
    return corpus


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
    assert rq.verdicts_agree(kinds) is agree


def test_an_approve_identical_group_fills_its_blanks_from_the_newest_member(tmp_path, monkeypatch, capsys):
    corpus = corpus_with(
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
        ["duplicate_verdicts.py", str(verdicts), "--corpus", str(corpus), "--out", str(out)],
    )
    dv.main()

    fills = json.loads(out.read_text())["verdicts"]
    assert [record["unit"] for record in fills] == ["u-0003"]
    assert fills[0]["verdict"] == "identical"
    assert fills[0]["note"] == "[duplicate-fill from u-0002] no visible change"
    assert "no duplicate group holds disagreeing verdicts" in capsys.readouterr().out


def test_a_real_split_still_reports_and_fills_nothing(tmp_path, monkeypatch, capsys):
    corpus = corpus_with(
        tmp_path, [unit("u-0001", "e-0001"), unit("u-0002", "e-0001"), unit("u-0003", "e-0001")]
    )
    verdicts = verdicts_file(
        tmp_path, [v("u-0001", "identical"), v("u-0002", "reject", note="stub too long")]
    )
    out = tmp_path / "fill.json"
    monkeypatch.setattr(
        "sys.argv",
        ["duplicate_verdicts.py", str(verdicts), "--corpus", str(corpus), "--out", str(out)],
    )
    dv.main()

    assert json.loads(out.read_text())["verdicts"] == []
    printed = capsys.readouterr().out
    assert "[warn] 1 duplicate groups hold disagreeing verdicts" in printed
    assert "e-0001  #units=u-0001,u-0002,u-0003" in printed


def test_duplicate_projection_matches_streamed_fill_and_reports(tmp_path, monkeypatch, capsys):
    units = [unit("u-0001", "e-0001"), unit("u-0002", "e-0001")]
    corpus = corpus_with(tmp_path, units)
    verdicts = verdicts_file(tmp_path, [v("u-0001", "approve")])
    out = tmp_path / "fill.json"
    argv = [str(verdicts), "--corpus", str(corpus), "--out", str(out)]
    dv.main(argv)
    expected_bytes, expected_report = out.read_bytes(), capsys.readouterr().out
    projection = [dv.duplicate_record(record) for record in units]
    assert all(set(record) == {"id", "duplicate_group", "notation"} for record in projection)

    def refuse_read(_corpus):
        raise AssertionError("a supplied duplicate projection must not reread the index")

    monkeypatch.setattr(dv.unit_index, "iter_human_units", refuse_read)
    for _round in range(2):
        dv.main(argv, units=projection)
        assert out.read_bytes() == expected_bytes
        assert capsys.readouterr().out == expected_report


def test_the_queue_snapshot_lists_the_split_group_and_not_the_approve_identical_one(
    tmp_path, monkeypatch, capsys
):
    corpus = corpus_with(
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
    data_out = tmp_path / "queue-data.json"
    monkeypatch.setattr(
        "sys.argv",
        ["review_queue.py", str(verdicts), "--corpus", str(corpus), "--data-out", str(data_out)],
    )
    rq.main()

    conflicts = json.loads(data_out.read_text())["conflicts"]
    assert [entry["duplicate_group"] for entry in conflicts] == ["e-0002"]
    assert conflicts[0]["verdicts"] == {"u-0003": "approve", "u-0004": "neither"}
    assert "1 duplicate groups disagree" in capsys.readouterr().out
