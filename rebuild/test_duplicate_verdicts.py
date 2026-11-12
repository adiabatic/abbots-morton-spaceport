"""Tests for the duplicate-group agreement rule, `review_queue.verdicts_agree`, and the two tools that use it: the duplicate fill and disagreement audit in duplicate_verdicts.py, and the conflicts list review_queue.py writes into the queue snapshot."""

import json
import os
import re

import pytest

from rebuild.review import accepted, unit_cache, unit_index
from rebuild.tools import duplicate_verdicts as dv
from rebuild.tools import merge_verdicts as mv
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
                "classes": [
                    *classes,
                    {
                        "id": "shard",
                        "status": None,
                        "section": "open-questions",
                        "shards": ["units/shard.json"],
                    },
                ],
            }
        )
    )
    (corpus / "units" / "shard.json").write_text(json.dumps(units))
    return corpus


def verdicts_file(tmp_path, records, name="verdicts-autosave.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"manifest_generated_at": STAMP, "verdicts": list(records)}))
    return path


def verdicts_layouts(stamp, records):
    """Return a verdicts file's text in three layouts: its stamp ahead of `verdicts`, which the file's head settles, and `verdicts` first and more than the head's bytes ahead of `verdicts`, which leave the stamp to the whole parse (`review_queue.verdicts_stamp`)."""
    members = {"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "exported_at": stamp}
    texts = [
        json.dumps({**members, "verdicts": records}),
        json.dumps({"verdicts": records, **members}),
        json.dumps({**members, "exported_at": "x" * accepted.HEAD_BYTES, "verdicts": records}),
    ]
    assert [accepted.head_stamp(text.encode()[: accepted.HEAD_BYTES]) for text in texts] == [
        stamp,
        None,
        None,
    ]
    return texts


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


def test_an_accepted_member_is_judged_under_its_mark_with_no_note_at_the_close_out(tmp_path):
    """A member the accepted list names, with no override in the store, is judged under its mark with an empty note and the list's `closed_at`: a blank member is filled from it with the fill's marker alone and that time, a store record dated after the close-out is the newer source, and a listed member the store holds an override tombstone for is blank and filled."""
    closed_at = "2026-11-05T18:00:00Z"
    a, b, c, d, e = (f"u-1{letter * 10}" for letter in "ABCDE")
    corpus = corpus_with(
        tmp_path,
        [unit(a, "e-0001"), unit(b, "e-0001"), unit(c, "e-0002"), unit(d, "e-0002"), unit(e, "e-0002")],
    )
    listed = tmp_path / "rebuild" / "accepted-units.txt"
    listed.parent.mkdir()
    listed.write_bytes(accepted.render(closed_at, {a: "approve", c: "identical", d: "identical"}))
    newer = v(e, "approve", note="the join reads as one stroke", at="2026-11-06T09:00:00.000Z")
    store = tmp_path / "verdicts-autosave.json"
    store.write_text(
        json.dumps(
            {
                "manifest_generated_at": STAMP,
                "verdicts": [newer],
                "overrides": {"verdicts": [], "cleared": [{"unit": d, "at": "2026-11-06T08:00:00.000Z"}]},
                "accepted": accepted.read(listed).field(),
            }
        )
    )
    out = tmp_path / "fill.json"
    dv.main([str(store), "--corpus", str(corpus), "--out", str(out)])

    assert json.loads(out.read_text())["verdicts"] == [
        {"unit": b, "verdict": "approve", "note": f"[duplicate-fill from {a}]", "at": closed_at},
        {
            "unit": d,
            "verdict": "approve",
            "note": f"[duplicate-fill from {e}] the join reads as one stroke",
            "at": newer["at"],
        },
    ]


def test_a_fill_chained_from_an_accepted_member_leaves_a_member_cleared_before_the_close_out_blank(tmp_path):
    """A fill copied from an accepted member carries the close-out's time, and so does a fill copied from that fill, which the store holds as an ordinary record: when the filled member comes first in corpus order, the next round's source is that record, tied with the accepted member at `closed_at`, and the member cleared before the close-out is still left out, so the merge keeps it blank."""
    closed_at = "2026-11-05T18:00:00Z"
    first, listed_unit, cleared = "u-1CCCCCCCCCC", "u-1AAAAAAAAAA", "u-1BBBBBBBBBB"
    corpus = corpus_with(
        tmp_path, [unit(first, "e-0001"), unit(listed_unit, "e-0001"), unit(cleared, "e-0001")]
    )
    path = tmp_path / "rebuild" / "accepted-units.txt"
    path.parent.mkdir()
    path.write_bytes(accepted.render(closed_at, {listed_unit: "approve"}))
    listed = accepted.read(path)
    records: dict = {}
    tombstones = {cleared: "2026-11-01T09:00:00.000Z"}
    store = tmp_path / "verdicts-autosave.json"
    out = tmp_path / "fill.json"
    for _round in range(2):
        store.write_text(
            json.dumps(
                {
                    "manifest_generated_at": STAMP,
                    "verdicts": list(records.values()),
                    "cleared": [{"unit": unit_id, "at": at} for unit_id, at in tombstones.items()],
                    "accepted": listed.field(),
                }
            )
        )
        dv.main([str(store), "--corpus", str(corpus), "--out", str(out)])
        mv.merge_into(records, json.loads(out.read_text())["verdicts"], tombstones, listed)

    assert records[first]["at"] == closed_at
    assert cleared not in records and tombstones == {cleared: "2026-11-01T09:00:00.000Z"}


@pytest.mark.parametrize("note", ["missing", None], ids=["missing-note", "null-note"])
def test_a_record_without_a_note_fills_and_reports_as_an_empty_note(tmp_path, capsys, note):
    """`verdict_store.well_formed` accepts a record whose note is missing or false-valued, so the fill and the disagreement report read its note as empty (`verdict_store.note_of`), as the carry and the merge write it: the fill's note is its marker alone, and the report's line for such a record ends at its verdict."""
    corpus = corpus_with(
        tmp_path,
        [
            unit("u-0001", "e-0001"),
            unit("u-0002", "e-0001"),
            unit("u-0003", "e-0002"),
            unit("u-0004", "e-0002"),
        ],
    )
    noteless = [v("u-0001", "approve"), v("u-0003", "approve"), v("u-0004", "reject")]
    for record in noteless:
        if note == "missing":
            del record["note"]
        else:
            record["note"] = note
    out = tmp_path / "fill.json"
    dv.main([str(verdicts_file(tmp_path, noteless)), "--corpus", str(corpus), "--out", str(out)])

    fills = json.loads(out.read_text())["verdicts"]
    assert [(record["unit"], record["note"]) for record in fills] == [
        ("u-0002", "[duplicate-fill from u-0001]")
    ]
    out = capsys.readouterr().out
    report = {line.split()[0]: line.split()[1:] for line in out.splitlines() if line.startswith("    u-")}
    assert report == {"u-0003": ["·Pea·Tea", "approve"], "u-0004": ["·Pea·Tea", "reject"]}


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
    """A fill handed the verdict update's projection, a group ordinal and an id word a unit in arrays of 4 and 8 bytes, writes the fill file and prints the report the fill that streams the corpus does, as often as it is handed it, and with no group in disagreement reads the corpus no more."""
    units = [unit("u-0001", "e-0001"), unit("u-0002", "e-0001")]
    corpus = corpus_with(tmp_path, units)
    verdicts = verdicts_file(tmp_path, [v("u-0001", "approve")])
    out = tmp_path / "fill.json"
    argv = [str(verdicts), "--corpus", str(corpus), "--out", str(out)]
    dv.main(argv)
    expected_bytes, expected_report = out.read_bytes(), capsys.readouterr().out
    projection = dv.Projection.of(units)
    columns = (projection.group_of, projection.words)
    assert [column.itemsize for column in columns] == [4, 8]

    def refuse_read(_corpus):
        raise AssertionError("a supplied duplicate projection must not reread the index")

    monkeypatch.setattr(dv.unit_index, "iter_human_units", refuse_read)
    for _round in range(2):
        dv.main(argv, projection=projection)
        assert out.read_bytes() == expected_bytes
        assert capsys.readouterr().out == expected_report


def test_the_conflict_report_rebuilds_ids_and_reads_the_index_notations(tmp_path, monkeypatch, capsys):
    """The projection keeps an id as its 64-bit word, and the conflict report rebuilds each member's id from it through `unit_cache.base58_64`, which `id_word` inverts, so it reads as the index's own; an id `id_word` refuses keeps its string. Each member's caption is the notation the index holds, whatever its code points, from one more walk of the human units, the same whether the fill streams the corpus or is handed the projection."""
    rebuilt = [unit_cache.unit_id_for(f"{index:02x}" * 32) for index in range(3)]
    members = [
        {**unit(rebuilt[0], "e-0009"), "codepoints": "E659:E658:E652:E653", "notation": "·Vie·Fee·Tea·Day"},
        {**unit("u-0002", "e-0009"), "codepoints": "E650:E652", "notation": "·Pea·Tea"},
        {**unit(rebuilt[1], "e-0009"), "codepoints": "E650:E651:E652:E653:E654", "notation": "kept-five"},
        {**unit(rebuilt[2], "e-0009"), "codepoints": "0000:E650", "notation": "kept-leading-zero"},
    ]
    corpus = corpus_with(tmp_path, members)
    records = [v(rebuilt[0], "approve", note="reads as one stroke"), v("u-0002", "reject")]
    out = tmp_path / "fill.json"
    argv = [str(verdicts_file(tmp_path, records)), "--corpus", str(corpus), "--out", str(out)]
    dv.main(argv)
    report = capsys.readouterr().out.splitlines()
    projection = dv.Projection.of(unit_index.iter_human_units(corpus))
    assert set(projection.ids) == {1}
    walk = unit_index.iter_human_units
    walks = []

    def counted(*args, **options):
        walks.append(options)
        return walk(*args, **options)

    monkeypatch.setattr(dv.unit_index, "iter_human_units", counted)
    dv.main(argv, projection=projection)
    assert capsys.readouterr().out.splitlines() == report
    assert walks == [{"fields": ("id", "notation")}]
    verdicts = {record["unit"]: record for record in records}
    expected = [f"  e-0009  #units={','.join(member['id'] for member in members)}"]
    for member in members:
        if member["id"] in verdicts:
            record = verdicts[member["id"]]
            expected.append(
                f"    {member['id']:9s} {member['notation']:30s} {record['verdict']:9s} {record['note']}"
            )
    expected += [f"    {member['id']:9s} {member['notation']:30s} (blank)" for member in members[2:]]
    start = report.index(expected[0])
    assert report[start : start + len(expected)] == expected


@pytest.mark.parametrize("reader", ["duplicate-fill", "queue"])
def test_a_verdicts_file_stamped_for_another_corpus_is_refused_on_its_heads_stamp(tmp_path, capsys, reader):
    """The duplicate fill and the queue snapshot refuse a verdicts file stamped for another corpus on the stamp its head settles, naming it, and parse nothing past the head, so a file whose records are not JSON is refused as one whose records are. A head that does not settle the stamp, its `verdicts` key first or more than the head's bytes ahead of that key, leaves it to the whole parse, which refuses the file, or fails as `json.loads` fails on text that is not JSON. A file stamped for the corpus gives the same fills, queue snapshot and printed lines in every layout. A path that is not a regular file, a pipe, is parsed whole with no head read, and a `/dev/fd` path to the file, whose descriptor shares its offset on macOS, is read with its offset left where it was, so each is refused or read in every layout as the file is, the queue snapshot naming the file it read."""
    corpus = corpus_with(
        tmp_path,
        [
            unit("u-0001", "e-0001"),
            unit("u-0002", "e-0001"),
            unit("u-0003", "e-0002"),
            unit("u-0004", "e-0002"),
        ],
    )
    path = tmp_path / "verdicts-autosave.json"
    out = tmp_path / "out.json"
    main, flag = (dv.main, "--out") if reader == "duplicate-fill" else (rq.main, "--data-out")
    argv = [str(path), "--corpus", str(corpus), flag, str(out)]
    old = "2026-07-01T00:00:00Z"
    records = [v("u-0001", "approve", note="looks right"), v("u-0003", "approve"), v("u-0004", "reject")]
    refusal = re.escape(
        f"{path} is stamped {old} but the corpus is {STAMP}, so it has not been carried onto this corpus"
    )
    for text in (
        f'{{"format": "ams-review-verdicts/1", "manifest_generated_at": "{old}", "verdicts": [not json',
        *verdicts_layouts(old, records)[1:],
    ):
        path.write_text(text)
        with pytest.raises(SystemExit, match=refusal):
            main(argv)
    path.write_text('{"verdicts": [not json')
    with pytest.raises(json.JSONDecodeError):
        main(argv)
    assert not out.exists()
    written = set()
    for text in verdicts_layouts(STAMP, records):
        path.write_text(text)
        main(argv)
        written.add((out.read_bytes(), *capsys.readouterr()))
    assert len(written) == 1
    if reader == "duplicate-fill":
        assert [record["unit"] for record in json.loads(out.read_text())["verdicts"]] == ["u-0002"]
    else:
        assert [entry["duplicate_group"] for entry in json.loads(out.read_text())["conflicts"]] == ["e-0002"]
    expected = {**json.loads(out.read_text()), "verdicts_file": None}
    for stamp in (old, STAMP):
        for text in verdicts_layouts(stamp, records):
            path.write_text(text)
            read_end, write_end = os.pipe()
            os.write(write_end, text.encode())
            os.close(write_end)
            descriptors = [read_end, os.open(path, os.O_RDONLY)]
            try:
                for descriptor in descriptors:
                    named = f"/dev/fd/{descriptor}"
                    named_argv = [named, "--accepted", str(accepted.path_for(path)), *argv[1:]]
                    if stamp == old:
                        with pytest.raises(
                            SystemExit, match=re.escape(f"{named} is stamped {old} but the corpus")
                        ):
                            main(named_argv)
                    else:
                        out.unlink()
                        main(named_argv)
                        assert {**json.loads(out.read_text()), "verdicts_file": None} == expected
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)


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
