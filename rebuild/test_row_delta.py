"""Tests for the segment format, rebuild/pipeline/row_delta.py, whose module docstring states the format: the head as the single commit point and its integrity checks, the parts no head lists, the positional encoding's generation check, the places a segment cuts between rows, and the merge readers stream. rebuild/test_unit_cache.py tests the unit store written in this format against the single file a whole write gives."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from rebuild.pipeline import row_delta

OUTPUT = "rows.ndjson.gz"


def _base(directory: Path, rows: list[bytes], generation: int = 0) -> row_delta.Part:
    def fill(stream) -> tuple[int, int]:
        stream.write(b'{"format": "rows"}\n')
        for row in rows:
            stream.write(row)
        return len(rows), sum(map(len, rows))

    return row_delta.write_part(directory, row_delta.base_name(OUTPUT, generation), fill)


def _materialized(directory: Path) -> list[tuple[row_delta.Place, bytes]]:
    head = row_delta.read_head(directory, OUTPUT)
    assert head is not None
    with row_delta.open_parts(directory, head, sha256=True) as opened:

        def base_rows():
            opened.handles[0].seek(0)
            with gzip.GzipFile(fileobj=opened.handles[0], mode="rb") as stream:
                next(stream)
                yield from stream

        generation = head.generation
        segments = [
            (lambda handle=handle, number=number: row_delta.segment_rows(handle, OUTPUT, generation, number))
            for number, handle in enumerate(opened.handles[1:], start=1)
        ]
        return list(row_delta.merge_placed(base_rows, segments))


def test_places_between_cut_ascending_places_strictly_inside_any_gap():
    """A run of rows takes as many places as it needs strictly between its neighbors, ascending, whether the neighbors are base rows, rows a segment placed, or the ends; and a gap a run filled still has room for the next run, since a fraction has no trailing zero."""
    gaps = [
        (None, (0, "")),
        ((0, ""), (1, "")),
        ((2, ""), None),
        ((3, "5"), (3, "51")),
        ((3, "zz"), (4, "")),
        ((-1, "h"), (0, "")),
    ]
    for low, high in gaps:
        for count in (1, 2, 37, 1000):
            places = row_delta.places_between(low, high, count)
            assert len(places) == count == len(set(places))
            assert places == sorted(places)
            assert all(not fraction.endswith("0") and fraction for _anchor, fraction in places)
            assert low is None or low < places[0]
            assert high is None or places[-1] < high
            inner = row_delta.places_between(places[0], places[1] if count > 1 else high, 3)
            assert places[0] < inner[0] and (count == 1 or inner[-1] < places[1])
    with pytest.raises(ValueError):
        row_delta.places_between((1, ""), (1, ""), 1)


def test_readers_merge_the_base_and_segments_by_place_and_a_later_part_supersedes_a_place(tmp_path):
    """The merge yields the live rows in place order, each part read once as a stream: at each place the row of the latest part that has one there is live, and a tombstone there removes it. An upsert at a base row's own place replaces it there, and a row that moves is a tombstone at its old place and an upsert at its new one."""
    rows = [b"a1\n", b"b1\n", b"c1\n", b"d1\n"]
    segments = [
        [
            row_delta.Row(row_delta.UPSERT, (-1, "i"), b"e1\n"),
            row_delta.Row(row_delta.TOMBSTONE, (0, ""), b"a\n"),
            row_delta.Row(row_delta.UPSERT, (1, ""), b"b2\n"),
            row_delta.Row(row_delta.TOMBSTONE, (2, ""), b"c\n"),
            row_delta.Row(row_delta.UPSERT, (3, "i"), b"a2\n"),
        ],
        [
            row_delta.Row(row_delta.UPSERT, (-1, "i"), b"e2\n"),
            row_delta.Row(row_delta.TOMBSTONE, (3, ""), b"d\n"),
            row_delta.Row(row_delta.UPSERT, (3, "r"), b"f1\n"),
        ],
    ]
    expected = [
        [((-1, "i"), b"e1\n"), ((1, ""), b"b2\n"), ((3, ""), b"d1\n"), ((3, "i"), b"a2\n")],
        [((-1, "i"), b"e2\n"), ((1, ""), b"b2\n"), ((3, "i"), b"a2\n"), ((3, "r"), b"f1\n")],
    ]
    head = row_delta.Head(OUTPUT, 0, 0, {}, _base(tmp_path, rows))
    row_delta.commit(tmp_path, head)
    assert _materialized(tmp_path) == [((index, ""), row) for index, row in enumerate(rows)]
    for number, (segment, materialized) in enumerate(zip(segments, expected, strict=True), start=1):
        part = row_delta.write_segment(tmp_path, OUTPUT, 0, number, segment)
        assert (part.rows, part.rows_bytes) == (len(segment), sum(map(len, map(row_delta.row_line, segment))))
        head = row_delta.with_segment(head, part, {"pass": number})
        row_delta.commit(tmp_path, head)
        assert _materialized(tmp_path) == materialized
    assert head.content == 2 and head.identity == f"{OUTPUT}@2"
    with pytest.raises(ValueError, match="out of place order"):
        row_delta.write_segment(tmp_path, OUTPUT, 0, 3, list(reversed(segments[0])))
    with pytest.raises(ValueError, match="out of place order"):
        row_delta.write_segment(tmp_path, OUTPUT, 0, 3, [segments[1][0], segments[1][0]])


def test_a_reader_refuses_a_part_that_is_not_the_one_its_head_records(tmp_path):
    """The head records each part's size and sha256. A reader that seeks checks the size, and one that reads every byte checks the sha256 too, so a part rewritten under its head, whatever its size, is refused rather than read."""
    base = _base(tmp_path, [b"a1\n", b"b1\n"])
    head = row_delta.Head(OUTPUT, 0, 0, {}, base)
    row_delta.commit(tmp_path, head)
    path = tmp_path / base.name
    raw = path.read_bytes()
    with row_delta.open_parts(tmp_path, head, sha256=True):
        pass
    path.write_bytes(raw + b"\0")
    with pytest.raises(row_delta.HeadError, match="bytes its head records"):
        row_delta.open_parts(tmp_path, head)
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with row_delta.open_parts(tmp_path, head):
        pass
    with pytest.raises(row_delta.HeadError, match="sha256"):
        row_delta.open_parts(tmp_path, head, sha256=True)
    path.unlink()
    with pytest.raises(row_delta.HeadError, match="missing"):
        row_delta.open_parts(tmp_path, head)


def test_a_part_no_head_lists_is_ignored_and_the_next_writer_deletes_it(tmp_path):
    """A failed pass can leave a written part that no head lists: a segment or a base of the next generation. A reader opens only the parts its head lists, and the next commit deletes every part of the output the new head does not list, the older generation's among them, and leaves a staging file, which is its writer's."""
    base = _base(tmp_path, [b"a1\n"])
    head = row_delta.Head(OUTPUT, 0, 0, {}, base)
    row_delta.commit(tmp_path, head)
    stray = row_delta.write_segment(
        tmp_path, OUTPUT, 0, 1, [row_delta.Row(row_delta.UPSERT, (0, ""), b"a9\n")]
    )
    _base(tmp_path, [b"z1\n"], generation=1)
    (tmp_path / f"{row_delta.segment_name(OUTPUT, 0, 2)}.partial").write_bytes(b"")
    assert _materialized(tmp_path) == [((0, ""), b"a1\n")]
    following = _base(tmp_path, [b"b1\n"], generation=1)
    removed = row_delta.commit(tmp_path, row_delta.Head(OUTPUT, 1, 1, {}, following))
    assert removed == sorted([base.name, stray.name])
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        [row_delta.head_name(OUTPUT), following.name, f"{row_delta.segment_name(OUTPUT, 0, 2)}.partial"]
    )
    assert _materialized(tmp_path) == [((0, ""), b"b1\n")]


def test_a_segment_whose_header_names_another_generation_is_refused(tmp_path):
    """A segment's places index its generation's base, so its header names the generation and its number, and a reader refuses a segment the head lists under another, as when an old head is paired with a refolded base, instead of placing its rows against the wrong base."""
    base = _base(tmp_path, [b"a1\n", b"b1\n"])
    forged = row_delta.write_segment(
        tmp_path, OUTPUT, 1, 1, [row_delta.Row(row_delta.UPSERT, (0, ""), b"a2\n")]
    )
    listed = row_delta.segment_name(OUTPUT, 0, 1)
    (tmp_path / forged.name).rename(tmp_path / listed)
    head = row_delta.Head(OUTPUT, 0, 0, {}, base, (row_delta.Part(listed, forged.size, forged.sha256, 1, 9),))
    row_delta.commit(tmp_path, head)
    with pytest.raises(row_delta.HeadError, match="generation 1"):
        _materialized(tmp_path)
    document = json.loads(row_delta.head_path(tmp_path, OUTPUT).read_text())
    document["segments"][0]["name"] = row_delta.segment_name(OUTPUT, 1, 1)
    row_delta.head_path(tmp_path, OUTPUT).write_text(json.dumps(document))
    with pytest.raises(row_delta.HeadError, match="as segment 1 of generation 0"):
        row_delta.read_head(tmp_path, OUTPUT)


def test_the_share_rule_and_the_fold_decide_a_new_base(monkeypatch):
    """The share rule writes a new base for a segment past half its base's row bytes or a pass whose stamps re-derive every row, and the fold is due once the segments' row bytes reach the base's or their count reaches the cap."""
    assert not row_delta.writes_base(50, 100, every_row_rederived=False)
    assert row_delta.writes_base(51, 100, every_row_rederived=False)
    assert row_delta.writes_base(0, 100, every_row_rederived=True)
    base = row_delta.Part(row_delta.base_name(OUTPUT, 0), 1, "", 10, 100)
    segment = row_delta.Part(row_delta.segment_name(OUTPUT, 0, 1), 1, "", 1, 10)
    assert not row_delta.fold_due(row_delta.Head(OUTPUT, 0, 0, {}, base))
    assert not row_delta.fold_due(row_delta.Head(OUTPUT, 0, 0, {}, base, (segment,) * 9))
    assert row_delta.fold_due(row_delta.Head(OUTPUT, 0, 0, {}, base, (segment,) * 10))
    assert row_delta.fold_due(row_delta.Head(OUTPUT, 0, 0, {}, base, (segment,) * 3), cap=3)
    monkeypatch.setattr(row_delta, "SEGMENT_CAP", 2)
    assert row_delta.fold_due(row_delta.Head(OUTPUT, 0, 0, {}, base, (segment,) * 2))


def _keyed_segment(directory: Path, number: int, rows: list[bytes], generation: int = 0) -> row_delta.Part:
    def fill(stream) -> tuple[int, int]:
        stream.write(row_delta.segment_header(OUTPUT, generation, number, row_delta.MERGE_KEY))
        for row in rows:
            stream.write(row)
        return len(rows), sum(map(len, rows))

    return row_delta.write_part(directory, row_delta.segment_name(OUTPUT, generation, number), fill)


def _key(row: bytes) -> list[bytes]:
    return row[: row.rindex(b"\t")].split(b"\t")


def _tombstone_key(payload: bytes) -> list[bytes]:
    return payload.rstrip(b"\n").split(b"\t")


def test_a_keyed_merge_lets_the_latest_part_win_at_each_key_and_says_so_in_every_header(tmp_path):
    """Parts that merge by key: the head and every segment header say so, a segment's rows are upserts and tombstones in key order, and the merge yields the live rows in key order, the latest part's row winning at each key, a tombstone removing it, and a key no earlier part holds added. Keys compare field by field, so `a\\tb` sorts before `ab`, as the crate sorts them, and a segment out of key order is refused."""
    base_rows = [b"a\tb\t1\n", b"ab\t\t1\n", b"c\tc\t1\n"]
    base = _base(tmp_path, base_rows)
    first = _keyed_segment(tmp_path, 1, [b"+\ta\tb\t2\n", b"-\tc\tc\n", b"+\td\td\t2\n"])
    second = _keyed_segment(tmp_path, 2, [b"+\ta\tb\t3\n", b"+\tc\tc\t3\n", b"-\td\td\n", b"-\tz\tz\n"])
    head = row_delta.Head(OUTPUT, 0, 2, {}, base, (first, second), row_delta.MERGE_KEY)
    row_delta.commit(tmp_path, head)
    assert json.loads(row_delta.head_path(tmp_path, OUTPUT).read_text())["merge"] == "key"
    read = row_delta.read_head(tmp_path, OUTPUT)
    assert read is not None and read == head
    with row_delta.open_parts(tmp_path, read, sha256=True) as opened:
        opened.handles[0].seek(0)
        with gzip.GzipFile(fileobj=opened.handles[0], mode="rb") as stream:
            next(stream)
            segments = [
                row_delta.keyed_segment_rows(handle, OUTPUT, 0, number)
                for number, handle in enumerate(opened.handles[1:], start=1)
            ]
            merged = list(row_delta.merge_keyed(stream, segments, _key, _tombstone_key))
    assert merged == [b"a\tb\t3\n", b"ab\t\t1\n", b"c\tc\t3\n"]
    backwards = [(row_delta.UPSERT, b"b\tb\t1\n"), (row_delta.UPSERT, b"a\ta\t1\n")]
    with pytest.raises(ValueError, match="out of key order"):
        list(row_delta.merge_keyed(iter(base_rows), [backwards], _key, _tombstone_key))


def test_a_segment_of_the_other_kind_of_merge_is_refused(tmp_path):
    """A head's merge, by place or by key, is repeated in each segment's header, so a reader refuses a segment written for the other: a placed segment under a keyed head, and a keyed one read as placed. A head by place writes no `merge` field, so the unit store's head reads as it did before outputs merged by key."""
    base = _base(tmp_path, [b"a\ta\t1\n"])
    placed = row_delta.write_segment(
        tmp_path, OUTPUT, 0, 1, [row_delta.Row(row_delta.UPSERT, (0, ""), b"a\ta\t2\n")]
    )
    keyed = row_delta.Head(OUTPUT, 0, 1, {}, base, (placed,), row_delta.MERGE_KEY)
    row_delta.commit(tmp_path, keyed)
    with row_delta.open_parts(tmp_path, keyed) as opened:
        with pytest.raises(
            row_delta.HeadError, match="merges by key, and segment 1's header says it merges by place"
        ):
            list(row_delta.keyed_segment_rows(opened.handles[1], OUTPUT, 0, 1))
    assert "merge" not in row_delta.Head(OUTPUT, 0, 0, {}, base).document()
    by_key = _keyed_segment(tmp_path, 1, [b"+\ta\ta\t2\n"])
    with open(tmp_path / by_key.name, "rb") as handle:
        with pytest.raises(
            row_delta.HeadError, match="merges by place, and segment 1's header says it merges by key"
        ):
            list(row_delta.segment_rows(handle, OUTPUT, 0, 1))


def test_one_output_folds_a_pass_and_one_at_its_overrun_folds_anyway(monkeypatch, tmp_path):
    """At most one output holds a pass's fold: under a cycle's run the claim is a file in the cycle's directory naming the run and the output, so a second output loses it for the rest of that run, the holder keeps it, and the next run's first claimant takes it over. Outside a cycle the process holds it, one a cycle directory. An output whose segments reach its cap plus `FOLD_OVERRUN` folds whatever the claim says, and one below that waits."""
    from rebuild.tools import cycle_paths, cycle_timings

    assert row_delta.CYCLE_RUN_ENV == cycle_timings.CYCLE_RUN_ENV
    monkeypatch.setattr(cycle_paths, "CYCLE_VAR", tmp_path / "process")
    assert row_delta.claim_fold("windows-default.tsv.gz")
    assert row_delta.claim_fold("windows-default.tsv.gz")
    assert not row_delta.claim_fold("windows-ss03.tsv.gz")
    monkeypatch.setattr(cycle_paths, "CYCLE_VAR", tmp_path / "cycle")
    monkeypatch.setenv(row_delta.CYCLE_RUN_ENV, "run-1")
    assert row_delta.claim_fold("windows-ss03.tsv.gz")
    assert not row_delta.claim_fold("unit-cache.ndjson.gz")
    assert row_delta.claim_fold("windows-ss03.tsv.gz")
    claim = tmp_path / "cycle" / cycle_paths.FOLD_CLAIM_NAME
    assert json.loads(claim.read_text()) == {"run": "run-1", "output": "windows-ss03.tsv.gz"}
    monkeypatch.setenv(row_delta.CYCLE_RUN_ENV, "run-2")
    assert row_delta.claim_fold("unit-cache.ndjson.gz")
    assert not row_delta.claim_fold("windows-ss03.tsv.gz")
    base = row_delta.Part(row_delta.base_name(OUTPUT, 0), 1, "", 10, 1000)
    segment = row_delta.Part(row_delta.segment_name(OUTPUT, 0, 1), 1, "", 1, 10)
    waiting = row_delta.Head(
        OUTPUT, 0, 0, {}, base, (segment,) * (row_delta.SEGMENT_CAP + row_delta.FOLD_OVERRUN - 1)
    )
    assert row_delta.fold_due(waiting) and not row_delta.fold_forced(waiting)
    at_overrun = row_delta.Head(
        OUTPUT, 0, 0, {}, base, (segment,) * (row_delta.SEGMENT_CAP + row_delta.FOLD_OVERRUN)
    )
    assert row_delta.fold_forced(at_overrun)
