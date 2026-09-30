"""Tests for the verdict journal (`rebuild/review/journal.py`): the sets, clears, and base events a transition writes, the seed event that opens a journal over an existing store, replay with and without an as-of cutoff, compaction in two phases that keep an append made between them, the event scan resumed from where an earlier one ended, a journal cut shorter and regrown between the phases, which neither resumes over, tolerance of a trailing line torn by a crashed append, which the next append cuts off unless only its newline is missing, and a base event torn short of its set lines, which opens a span replay refuses until the next complete base and which compaction never starts at."""

import json

import pytest

from rebuild.review import journal


def v(unit, verdict="approve", note="", at="2026-07-10T00:00:00Z"):
    return {"unit": unit, "verdict": verdict, "note": note, "at": at}


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_first_write_over_empty_store_is_a_single_event(tmp_path):
    path = tmp_path / "journal.ndjson"
    result = journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T01:00:00Z",
    )
    assert result == {"base": True, "sets": 2, "clears": 0, "recorded": True}
    lines = read_lines(path)
    assert [line["kind"] for line in lines] == ["event", "set", "set"]
    assert lines[0]["base"] is True
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1", "u-2"}


def test_same_stamp_transition_journals_sets_and_clears(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T01:00:00Z",
    )
    result = journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2")],
        new_verdicts=[v("u-1", verdict="reject", at="2026-07-10T02:00:00Z"), v("u-3")],
        at="2026-07-10T02:00:00Z",
    )
    assert result == {"base": False, "sets": 2, "clears": 1, "recorded": True}
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1", "u-3"}
    assert records["u-1"]["verdict"] == "reject"


def test_stamp_change_journals_a_base_event(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    result = journal.record_transition(
        path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-9")],
        stashed="verdicts-autosave-S1.json",
        at="2026-07-10T03:00:00Z",
    )
    assert result["base"] is True
    stamp, records = journal.replay(path)
    assert stamp == "S2"
    assert set(records) == {"u-9"}
    events = list(journal.iter_events(path))
    assert events[-1]["stashed"] == "verdicts-autosave-S1.json"


def test_seed_event_opens_a_journal_over_an_existing_store(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2")],
        new_verdicts=[v("u-1"), v("u-2"), v("u-3")],
        at="2026-07-10T01:00:00Z",
    )
    events = list(journal.iter_events(path))
    assert [event["source"] for event in events] == ["seed", "autosave"]
    assert events[0]["base"] is True
    assert events[0]["sets"] == 2
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1", "u-2", "u-3"}


def test_no_op_transition_appends_nothing(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    before = path.read_text()
    result = journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-1")],
        at="2026-07-10T02:00:00Z",
    )
    assert result["recorded"] is False
    assert path.read_text() == before


def test_replay_as_of_stops_at_the_cutoff(tmp_path):
    path = tmp_path / "journal.ndjson"
    for hour, units in ((1, ["u-1"]), (2, ["u-1", "u-2"]), (3, ["u-1", "u-2", "u-3"])):
        journal.record_transition(
            path,
            source="autosave",
            stamp="S1",
            old_stamp="S1" if hour > 1 else None,
            old_verdicts=[v(f"u-{n}") for n in range(1, hour)],
            new_verdicts=[v(unit) for unit in units],
            at=f"2026-07-10T0{hour}:00:00Z",
        )
    stamp, records = journal.replay(path, as_of="2026-07-10T02:30")
    assert stamp == "S1"
    assert set(records) == {"u-1", "u-2"}
    stamp, records = journal.replay(path, as_of="2026-07-10T00:30")
    assert stamp is None
    assert records == {}
    stamp, records = journal.replay(path)
    assert set(records) == {"u-1", "u-2", "u-3"}


def test_replay_tolerates_a_truncated_trailing_line(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "event", "source": "autosa')
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1"}


def test_replay_tolerates_a_tail_torn_mid_character(tmp_path):
    """Notes are written with `ensure_ascii=False`, so a letter name in a note is multi-byte and a crash between writes can cut one in half. The journal is read as bytes and decoded one line at a time, so the torn tail ends the scan and every line before it still replays. Decoding the whole file as text would raise `UnicodeDecodeError` in every reader, the restore path included."""
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    torn = '{"kind": "set", "unit": "u-2", "note": "·'.encode()[:-1]
    assert torn[-1:] == b"\xc2"
    with path.open("ab") as handle:
        handle.write(torn)
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1"}
    assert [event["stamp"] for event in journal.iter_events(path)] == ["S1"]


def test_replay_reads_past_a_note_carrying_a_unicode_line_separator(tmp_path):
    """A note can contain U+2028, for example when pasted from a PDF. `json.dumps` writes it raw, and `str.splitlines` treats it as a line break, which would split the entry and drop every entry after it. The journal is read a line at a time and splits on newlines only, so the note and the rest of the journal replay intact."""
    note = "the junction splits\u2028here"
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1", note=note)],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1", note=note)],
        new_verdicts=[v("u-1", note=note), v("u-2")],
        at="2026-07-10T02:00:00Z",
    )
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1", "u-2"}
    assert records["u-1"]["note"] == note


def test_compact_finds_a_floor_past_a_note_carrying_a_unicode_line_separator(tmp_path):
    """`compact` also splits the file on newlines only. Splitting on every Unicode line break would stop the scan at the first half of a U+2028 note, so the newest base event after it would never be found."""
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1", note="the junction splits\u2028here")],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1", note="the junction splits\u2028here")],
        new_verdicts=[v("u-2")],
        at="2026-07-10T03:00:00Z",
    )
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is True
    assert result["floor_at"] == "2026-07-10T03:00:00Z"
    assert result["dropped_lines"] == 2
    assert result["kept_lines"] == 2
    events = list(journal.iter_events(path))
    assert [event["stamp"] for event in events] == ["S2"]


def test_payload_for_sorts_and_stamps(tmp_path):
    records = {"u-2": v("u-2"), "u-1": v("u-1", verdict="either")}
    payload = journal.payload_for("S1", records, exported_at="2026-07-10T05:00:00Z")
    assert payload["format"] == "ams-review-verdicts/1"
    assert payload["manifest_generated_at"] == "S1"
    assert payload["exported_at"] == "2026-07-10T05:00:00Z"
    assert [record["unit"] for record in payload["verdicts"]] == ["u-1", "u-2"]


def test_compact_rewrites_to_the_newest_base_at_or_before_cutoff(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T02:00:00Z",
    )
    journal.record_transition(
        path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2")],
        new_verdicts=[v("u-3")],
        at="2026-07-10T03:00:00Z",
    )
    journal.record_transition(
        path,
        source="autosave",
        stamp="S2",
        old_stamp="S2",
        old_verdicts=[v("u-3")],
        new_verdicts=[v("u-3"), v("u-4")],
        at="2026-07-10T04:00:00Z",
    )

    entries = read_lines(path)
    floor_index = next(i for i, e in enumerate(entries) if e.get("base") and e.get("stamp") == "S2")
    as_ofs = ["2026-07-10T03:00:00Z", "2026-07-10T03:30:00Z", "2026-07-10T04:00:00Z", None]
    before = {as_of: journal.replay(path, as_of=as_of) for as_of in as_ofs}

    result = journal.compact(path, cutoff="2026-07-10T03:30:00Z")
    assert result["compacted"] is True
    assert result["floor_at"] == "2026-07-10T03:00:00Z"
    assert result["dropped_lines"] == floor_index
    assert result["kept_lines"] == len(entries) - floor_index

    for as_of in as_ofs:
        assert journal.replay(path, as_of=as_of) == before[as_of]

    events = list(journal.iter_events(path))
    assert events[0]["base"] is True
    assert events[0]["stamp"] == "S2"
    assert events[0]["at"] == "2026-07-10T03:00:00Z"


def test_compact_leaves_the_journal_untouched_when_cutoff_precedes_every_base(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T02:00:00Z",
    )
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T03:00:00Z",
    )
    before = path.read_bytes()
    result = journal.compact(path, cutoff="2026-07-10T01:00:00Z")
    assert result["compacted"] is False
    assert path.read_bytes() == before


def test_compact_leaves_the_journal_untouched_when_already_at_the_only_base(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T02:00:00Z",
    )
    before = path.read_bytes()
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is False
    assert path.read_bytes() == before


def test_compact_missing_journal_is_a_no_op(tmp_path):
    path = tmp_path / "missing.ndjson"
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is False
    assert not path.exists()


def test_compact_preserves_a_torn_trailing_line_after_the_floor(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-2")],
        at="2026-07-10T03:00:00Z",
    )
    torn = '{"kind": "event", "source": "autosa'
    with path.open("a", encoding="utf-8") as handle:
        handle.write(torn)
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is True
    assert result["floor_at"] == "2026-07-10T03:00:00Z"
    assert path.read_text().endswith(torn)
    events = list(journal.iter_events(path))
    assert events[0]["stamp"] == "S2"


def test_compact_stops_scanning_at_a_torn_line_before_the_last_base(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-2")],
        at="2026-07-10T03:00:00Z",
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "event", "sour\n')
    journal.record_transition(
        path,
        source="merge",
        stamp="S3",
        old_stamp="S2",
        old_verdicts=[v("u-2")],
        new_verdicts=[v("u-3")],
        at="2026-07-10T05:00:00Z",
    )
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is True
    assert result["floor_at"] == "2026-07-10T03:00:00Z"
    assert result["dropped_lines"] == 2
    assert "S3" in path.read_text()
    events = list(journal.iter_events(path))
    assert events[0]["stamp"] == "S2"
    assert len(events) == 1


def test_an_append_cuts_off_a_torn_tail_so_the_lines_after_it_replay(tmp_path):
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    intact = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"kind": "set", "unit": "u-9", "verd')
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T02:00:00Z",
    )
    assert path.read_bytes().startswith(intact + b'{"kind": "event"')
    stamp, records = journal.replay(path)
    assert stamp == "S1"
    assert set(records) == {"u-1", "u-2"}


def _torn_base_journal(path):
    """Write complete S0 and S1 bases, then an S2 base event whose set lines stop one short, then a later S2 diff appended after it."""
    journal.record_transition(
        path,
        source="autosave",
        stamp="S0",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-0")],
        at="2026-07-10T00:30:00Z",
    )
    journal.record_transition(
        path,
        source="merge",
        stamp="S1",
        old_stamp="S0",
        old_verdicts=[v("u-0")],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-7"), v("u-8")],
        at="2026-07-10T03:00:00Z",
    )
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-1]))
    journal.record_transition(
        path,
        source="autosave",
        stamp="S2",
        old_stamp="S2",
        old_verdicts=[v("u-7")],
        new_verdicts=[v("u-7"), v("u-9")],
        at="2026-07-10T04:00:00Z",
    )


def _append_base(path, stamp, old_stamp, old_units, new_units, at):
    journal.record_transition(
        path,
        source="merge",
        stamp=stamp,
        old_stamp=old_stamp,
        old_verdicts=[v(unit) for unit in old_units],
        new_verdicts=[v(unit) for unit in new_units],
        at=at,
    )


def test_a_base_event_cut_short_of_its_sets_is_never_replayed_as_a_whole_store(tmp_path):
    path = tmp_path / "journal.ndjson"
    _torn_base_journal(path)
    assert journal.replay(path, as_of="2026-07-10T02:00:00Z") == ("S1", {"u-1": v("u-1")})
    for as_of in ("2026-07-10T03:00:00Z", "2026-07-10T03:30:00Z", None):
        with pytest.raises(journal.JournalGap) as gap:
            journal.replay(path, as_of=as_of)
        assert (gap.value.torn_at, gap.value.resumes_at) == ("2026-07-10T03:00:00Z", None)
    path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[:6]))
    with pytest.raises(journal.JournalGap):
        journal.replay(path)


def test_replay_is_exact_again_from_the_next_complete_base_after_a_torn_one(tmp_path):
    path = tmp_path / "journal.ndjson"
    _torn_base_journal(path)
    _append_base(path, "S3", "S2", ["u-7", "u-9"], ["u-3", "u-4"], "2026-07-10T05:00:00Z")
    journal.record_transition(
        path,
        source="autosave",
        stamp="S3",
        old_stamp="S3",
        old_verdicts=[v("u-3"), v("u-4")],
        new_verdicts=[v("u-3")],
        at="2026-07-10T06:00:00Z",
    )
    assert journal.replay(path, as_of="2026-07-10T02:00:00Z") == ("S1", {"u-1": v("u-1")})
    with pytest.raises(journal.JournalGap) as gap:
        journal.replay(path, as_of="2026-07-10T04:30:00Z")
    assert (gap.value.torn_at, gap.value.resumes_at) == ("2026-07-10T03:00:00Z", "2026-07-10T05:00:00Z")
    assert journal.replay(path, as_of="2026-07-10T05:30:00Z") == ("S3", {"u-3": v("u-3"), "u-4": v("u-4")})
    assert journal.replay(path) == ("S3", {"u-3": v("u-3")})


def test_a_torn_first_base_leaves_replay_exact_from_the_next_complete_base(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[:2]))
    _append_base(path, "S2", "S1", ["u-1"], ["u-5"], "2026-07-10T02:00:00Z")
    with pytest.raises(journal.JournalGap) as gap:
        journal.replay(path, as_of="2026-07-10T01:30:00Z")
    assert gap.value.resumes_at == "2026-07-10T02:00:00Z"
    assert journal.replay(path, as_of="2026-07-10T00:30:00Z") == (None, {})
    assert journal.replay(path) == ("S2", {"u-5": v("u-5")})
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is True and result["floor_at"] == "2026-07-10T02:00:00Z"
    assert journal.replay(path) == ("S2", {"u-5": v("u-5")})


def test_compact_never_chooses_a_torn_base_as_its_floor(tmp_path):
    path = tmp_path / "journal.ndjson"
    _torn_base_journal(path)
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is True
    assert result["floor_at"] == "2026-07-10T01:00:00Z"
    assert result["dropped_lines"] == 2
    assert [event["stamp"] for event in journal.iter_events(path)] == ["S1", "S2", "S2"]
    assert journal.replay(path, as_of="2026-07-10T02:00:00Z") == ("S1", {"u-1": v("u-1")})


def test_compact_floors_at_a_complete_base_after_a_torn_one(tmp_path):
    path = tmp_path / "journal.ndjson"
    _torn_base_journal(path)
    _append_base(path, "S3", "S2", ["u-7", "u-9"], ["u-3"], "2026-07-10T05:00:00Z")
    before = journal.replay(path)
    result = journal.compact(path, cutoff="2026-07-10T09:00:00Z")
    assert result["compacted"] is True and result["floor_at"] == "2026-07-10T05:00:00Z"
    assert [event["stamp"] for event in journal.iter_events(path)] == ["S3"]
    assert journal.replay(path) == before == ("S3", {"u-3": v("u-3")})


def test_an_append_keeps_a_final_line_that_lost_only_its_newline(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    assert journal.replay(path) == ("S1", {"u-1": v("u-1"), "u-2": v("u-2")})
    journal.record_transition(
        path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2")],
        new_verdicts=[v("u-1"), v("u-2"), v("u-3")],
        at="2026-07-10T02:00:00Z",
    )
    assert journal.replay(path) == ("S1", {"u-1": v("u-1"), "u-2": v("u-2"), "u-3": v("u-3")})


def test_repair_tail_makes_a_recorded_length_mark_where_the_next_append_begins(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    intact = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"kind": "set", "unit": "u-9", "verd')
    journal.repair_tail(path)
    assert path.read_bytes() == intact
    size = path.stat().st_size
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T02:00:00Z")
    assert path.read_bytes()[:size] == intact
    journal.repair_tail(tmp_path / "absent.ndjson")
    assert not (tmp_path / "absent.ndjson").exists()


def test_compact_keeps_every_append_made_between_prepare_and_finish(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T03:00:00Z")
    with path.open("ab") as handle:
        handle.write(b'{"kind": "event", "source": "autosa')
    prepared = journal.compact_prepare(path, cutoff="2026-07-10T09:00:00Z")
    journal.record_transition(
        path,
        source="autosave",
        stamp="S2",
        old_stamp="S2",
        old_verdicts=[v("u-2")],
        new_verdicts=[v("u-2"), v("u-3")],
        at="2026-07-10T06:00:00Z",
    )
    before = journal.replay(path)
    result = journal.compact_finish(prepared)
    assert result["compacted"] is True and result["floor_at"] == "2026-07-10T03:00:00Z"
    assert result["kept_lines"] == len(path.read_bytes().splitlines())
    assert journal.replay(path) == before == ("S2", {"u-2": v("u-2"), "u-3": v("u-3")})
    assert [event["at"] for event in journal.iter_events(path)] == [
        "2026-07-10T03:00:00Z",
        "2026-07-10T06:00:00Z",
    ]
    assert not path.with_name(path.name + ".tmp").exists()


def test_compact_finish_leaves_a_journal_replaced_since_the_scan_alone(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T03:00:00Z")
    prepared = journal.compact_prepare(path, cutoff="2026-07-10T09:00:00Z")
    replacement = tmp_path / "replacement.ndjson"
    _append_base(replacement, "S3", None, [], ["u-3"], "2026-07-10T05:00:00Z")
    replacement.replace(path)
    after = path.read_bytes()
    result = journal.compact_finish(prepared)
    assert result["compacted"] is False and result["replaced"] is True
    assert path.read_bytes() == after
    assert not path.with_name(path.name + ".tmp").exists()


def test_a_resumed_event_scan_reads_only_what_was_appended_since(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    first = journal.scan_events(path)
    assert [event["stamp"] for event in first.events] == ["S1"]
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T02:00:00Z")
    tail = journal.scan_events(path, resume=first)
    assert tail.start == first.end
    assert [event["stamp"] for event in tail.events] == ["S2"]
    replacement = tmp_path / "replacement.ndjson"
    _append_base(replacement, "S3", None, [], ["u-3"], "2026-07-10T05:00:00Z")
    replacement.replace(path)
    rescanned = journal.scan_events(path, resume=tail)
    assert rescanned.start == 0
    assert [event["stamp"] for event in rescanned.events] == ["S3"]


def test_a_journal_cut_shorter_and_regrown_between_the_phases_is_rescanned_and_never_compacted(tmp_path):
    """A writer holding the store's lock can cut the journal shorter (`rekey_verdicts --undo`) and later appends can grow it past where the unlocked phase stopped, keeping the inode. The locked phase then sees different bytes before that offset, so the scan starts over and the compaction leaves the journal alone instead of resuming mid-line."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    cut = path.stat().st_size
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T03:00:00Z")
    inode = path.stat().st_ino
    first = journal.scan_events(path)
    prepared = journal.compact_prepare(path, cutoff="2026-07-10T09:00:00Z")
    assert prepared.tmp is not None
    with path.open("r+b") as handle:
        handle.truncate(cut)
    _append_base(path, "S3", "S1", ["u-1"], ["u-3", "u-4", "u-5"], "2026-07-10T05:00:00Z")
    assert path.stat().st_ino == inode and path.stat().st_size >= first.end
    rescanned = journal.scan_events(path, resume=first)
    assert rescanned.start == 0
    assert [event["stamp"] for event in rescanned.events] == ["S1", "S3"]
    after = path.read_bytes()
    result = journal.compact_finish(prepared)
    assert result["compacted"] is False and result["replaced"] is True
    assert path.read_bytes() == after
    assert not path.with_name(path.name + ".tmp").exists()
