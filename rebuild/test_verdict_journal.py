"""Tests for the verdict journal (`rebuild/review/journal.py`): the sets, clears, and base events a transition writes, a stamp change the land writes as sets and clears against the store it replaced (an event line alone when the records are the same) unless a base is due (`base_due`) or there is no previous store or journal, the seed event that opens a journal over an existing store, replay with and without an as-of cutoff, compaction in two phases that keep an append made between them, the event scan resumed from where an earlier one ended, a journal cut shorter and regrown between the phases, which neither resumes over, tolerance of a trailing line torn by a crashed append, which the next append cuts off unless only its newline is missing, a base event torn short of its set lines, which opens a span replay refuses until the next complete base and which compaction never starts at, a compaction that floors at a base before stamp changes written as sets and clears, and the cut back to the length a dead land recorded, made only while the journal is the same file."""

import json
import os

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


def test_a_resumed_scan_reads_only_what_was_appended_since(tmp_path):
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    first = journal.scan(path)
    assert [event.at for event in first.events] == ["2026-07-10T01:00:00Z"]
    _append_base(path, "S2", "S1", ["u-1"], ["u-2", "u-3"], "2026-07-10T02:00:00Z")
    tail = journal.scan(path, resume=first.state)
    assert tail.start == first.state.end
    assert tail.state == journal.scan(path).state
    assert [(event.at, event.counted) for event in tail.events] == [
        ("2026-07-10T01:00:00Z", 1),
        ("2026-07-10T02:00:00Z", 2),
    ]
    replacement = tmp_path / "replacement.ndjson"
    _append_base(replacement, "S3", None, [], ["u-3"], "2026-07-10T05:00:00Z")
    replacement.replace(path)
    rescanned = journal.scan(path, resume=tail.state)
    assert rescanned.start == 0
    assert [event.at for event in rescanned.events] == ["2026-07-10T05:00:00Z"]


def test_a_journal_cut_shorter_and_regrown_between_the_phases_is_rescanned_and_never_compacted(tmp_path):
    """A writer holding the store's lock can cut the journal shorter (`rekey_verdicts --undo`) and later appends can grow it past where the unlocked phase stopped, keeping the inode. The locked phase then sees different bytes before that offset, so the scan starts over and the compaction leaves the journal alone instead of resuming mid-line."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    cut = path.stat().st_size
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T03:00:00Z")
    inode = path.stat().st_ino
    first = journal.scan(path)
    prepared = journal.compact_prepare(path, cutoff="2026-07-10T09:00:00Z")
    assert prepared.tmp is not None
    with path.open("r+b") as handle:
        handle.truncate(cut)
    _append_base(path, "S3", "S1", ["u-1"], ["u-3", "u-4", "u-5"], "2026-07-10T05:00:00Z")
    assert path.stat().st_ino == inode and path.stat().st_size >= first.state.end
    rescanned = journal.scan(path, resume=first.state)
    assert rescanned.start == 0
    assert [event.at for event in rescanned.events] == ["2026-07-10T01:00:00Z", "2026-07-10T05:00:00Z"]
    after = path.read_bytes()
    result = journal.compact_finish(prepared)
    assert result["compacted"] is False and result["replaced"] is True
    assert path.read_bytes() == after
    assert not path.with_name(path.name + ".tmp").exists()


def test_a_scan_resumed_after_a_compaction_reads_as_a_full_scan_of_the_compacted_journal(tmp_path):
    """`compact_finish` hands back the state `compact_prepare`'s scan read, rebased onto the compacted file: every offset and line index less the floor's, on the new inode. A scan resumed from it reads only the lines appended since and agrees with a full scan, whether the rebased end sits more or less than the check's span past the floor."""
    for padding in ("", "x" * 70_000):
        path = tmp_path / f"journal-{len(padding)}.ndjson"
        _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
        _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T03:00:00Z")
        journal.record_transition(
            path,
            source="autosave",
            stamp="S2",
            old_stamp="S2",
            old_verdicts=[v("u-2")],
            new_verdicts=[v("u-2"), v("u-3", note=padding)],
            stashed="verdicts-autosave-x.json",
            at="2026-07-10T04:00:00Z",
        )
        first = journal.scan(path)
        prepared = journal.compact_prepare(path, cutoff="2026-07-10T09:00:00Z", resume=first.state)
        assert prepared.read == first.state
        result = journal.compact_finish(prepared)
        assert result["compacted"] is True and result["floor_at"] == "2026-07-10T03:00:00Z"
        assert result["resume"] == journal.scan(path).state
        assert result["resume"].inode == path.stat().st_ino != first.state.inode
        journal.record_transition(
            path,
            source="autosave",
            stamp="S2",
            old_stamp="S2",
            old_verdicts=[v("u-2"), v("u-3", note=padding)],
            new_verdicts=[v("u-2")],
            at="2026-07-10T05:00:00Z",
        )
        resumed = journal.scan(path, resume=result["resume"])
        assert resumed.start == result["resume"].end > 0
        assert resumed.state == journal.scan(path).state
        assert [(event.at, event.base, event.stashed) for event in resumed.events] == [
            ("2026-07-10T03:00:00Z", True, None),
            ("2026-07-10T04:00:00Z", False, "verdicts-autosave-x.json"),
            ("2026-07-10T05:00:00Z", False, None),
        ]


@pytest.mark.parametrize("regrowth", ["other-lines", "same-lines"])
@pytest.mark.parametrize("cut", ["rekey-undo", "land-recovery"])
def test_a_journal_cut_shorter_between_passes_is_scanned_from_the_start(tmp_path, cut, regrowth):
    """A saved scan state can outlive what it read. `rekey_verdicts --undo` cuts the journal back to the length it had before the re-key, and a land recovery's `truncate_to` to the length the land recorded; either can drop lines the state read, and the appends after it can grow the file past the state's end on the same inode. A re-key undone and run again appends the same set lines, so even the bytes just before that end come out the same, but its event line at the cut has a later `at`. Either way the resumed scan starts over and agrees with a full scan, and a compaction never starts at the base that was cut."""
    path = tmp_path / "journal.ndjson"
    units = [f"u-{n:04d}" for n in range(1_000)]
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    length, inode = path.stat().st_size, path.stat().st_ino
    _append_base(path, "S2", "S1", ["u-1"], units, "2026-07-10T03:00:00Z")
    before = path.read_bytes()
    state_path = tmp_path / "cycle" / "journal-scan.json"
    journal.save_scan_state(state_path, journal.scan(path).state)
    saved = journal.load_scan_state(state_path)
    assert saved is not None and saved == journal.scan(path).state
    if cut == "rekey-undo":
        with path.open("r+b") as handle:
            handle.truncate(length)
    else:
        assert journal.truncate_to(path, length, inode) is True
    if regrowth == "same-lines":
        _append_base(path, "S2", "S1", ["u-1"], units, "2026-07-10T05:00:00Z")
        window = slice(saved.end - journal._RESUME_CHECK_BYTES, saved.end)
        assert window.start > length and path.read_bytes()[window] == before[window]
    else:
        _append_base(
            path, "S3", "S1", ["u-1"], [f"u-{n:04d}" for n in range(1_000, 2_001)], "2026-07-10T05:00:00Z"
        )
    assert path.stat().st_ino == saved.inode and path.stat().st_size >= saved.end
    rescanned = journal.scan(path, resume=saved)
    assert rescanned.start == 0
    assert rescanned.state == journal.scan(path).state
    assert [event.at for event in rescanned.events] == ["2026-07-10T01:00:00Z", "2026-07-10T05:00:00Z"]
    prepared = journal.compact_prepare(path, cutoff="2026-07-10T04:00:00Z", resume=saved)
    assert prepared.tmp is None and prepared.read == rescanned.state


def test_a_scan_counts_a_final_line_with_no_newline_but_resumes_before_it(tmp_path):
    """A final line that lost only its newline counts, as `_iter_entries` counts it: a set line toward its base's sets, an event as an event. The state stops before it, so a resumed scan reads it again once an append ends it, and counts it once."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    torn = journal.scan(path)
    assert torn.pending is not None and torn.state.marks[0].counted == 1
    assert [(event.at, event.counted) for event in torn.events] == [("2026-07-10T01:00:00Z", 2)]
    prepared = journal.compact_prepare(path, cutoff="2026-07-10T09:00:00Z", resume=torn.state)
    assert prepared.tmp is None and prepared.untouched["kept_lines"] == 3
    _append_base(path, "S2", "S1", ["u-1", "u-2"], ["u-3"], "2026-07-10T02:00:00Z")
    resumed = journal.scan(path, resume=torn.state)
    assert resumed.start == torn.state.end > 0
    assert resumed.state == journal.scan(path).state
    assert [(event.at, event.counted) for event in resumed.events] == [
        ("2026-07-10T01:00:00Z", 2),
        ("2026-07-10T02:00:00Z", 1),
    ]


def test_a_scan_that_meets_a_line_still_being_appended_stops_before_it(tmp_path, monkeypatch):
    """Retention's first scan runs without the store's lock, so it can read the start of a line a writer is still appending, and then, on its next read, the rest of that line. The scan stops at the first line with no newline, so the state it saves ends before that line and a scan resumed from it agrees with a full one."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    whole = path.read_bytes()
    path.write_bytes(whole[:-1])
    real = journal._read_lines

    def racing(handle):
        for item in real(handle):
            if not item[0].endswith(b"\n"):
                with path.open("ab") as tail:
                    tail.write(b"\n")
            yield item

    monkeypatch.setattr(journal, "_read_lines", racing)
    raced = journal.scan(path)
    monkeypatch.setattr(journal, "_read_lines", real)
    assert path.read_bytes() == whole
    assert raced.state.end == whole.rstrip(b"\n").rfind(b"\n") + 1
    assert [(event.at, event.counted) for event in raced.events] == [("2026-07-10T01:00:00Z", 2)]
    assert journal.scan(path, resume=raced.state).state == journal.scan(path).state


def test_a_saved_scan_state_in_another_format_or_unreadable_is_not_resumed(tmp_path):
    state_path = tmp_path / "journal-scan.json"
    assert journal.load_scan_state(state_path) is None
    state_path.write_text("{not json")
    assert journal.load_scan_state(state_path) is None
    state_path.write_text(json.dumps({"format": "ams-journal-scan/0", "inode": 1}))
    assert journal.load_scan_state(state_path) is None
    journal.save_scan_state(state_path, None)
    assert not state_path.exists()


def test_truncate_to_cuts_only_the_file_whose_length_was_taken(tmp_path):
    """A land records the journal's length and inode before it appends; its recovery cuts the journal back to that length only when it is still the same file, and removes a journal the land itself created."""
    path = tmp_path / "journal.ndjson"
    journal.record_transition(
        path, source="merge", stamp="S1", old_stamp=None, old_verdicts=[], new_verdicts=[v("u-1")]
    )
    length, inode = path.stat().st_size, path.stat().st_ino
    with path.open("ab") as handle:
        handle.write(b'{"kind": "event", "source": "land", "base": true, "sets": 3}\n{"kind": "set"')
    assert journal.truncate_to(path, length, inode) is True
    assert path.stat().st_size == length
    assert journal.truncate_to(path, length, inode) is False

    replaced = tmp_path / "replaced.ndjson"
    replaced.write_bytes(path.read_bytes() + b"\n")
    os.replace(replaced, path)
    assert journal.truncate_to(path, length, inode) is False
    assert path.stat().st_size == length + 1

    fresh = tmp_path / "fresh.ndjson"
    journal.record_transition(
        fresh, source="land", stamp="S2", old_stamp=None, old_verdicts=[], new_verdicts=[v("u-1")]
    )
    assert journal.truncate_to(fresh, None, None) is True
    assert not fresh.exists()


def test_a_stamp_change_given_no_base_journals_sets_and_clears_against_the_previous_store(tmp_path):
    """The land passes `base=False` when the journal is not due a base. A unit's id is its content key, so the stamp change is written against the store it replaced: the units whose records changed or are new, and the units the new store lacks, under one event that names the new stamp and the stash. Replay reads the old store before it and the new store from it on."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2", "u-3"], "2026-07-10T01:00:00Z")
    reject = v("u-2", verdict="reject", at="2026-07-10T02:00:00Z")
    result = journal.record_transition(
        path,
        source="land",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2"), v("u-3")],
        new_verdicts=[v("u-1"), reject, v("u-4")],
        stashed="verdicts-autosave-S1.json",
        at="2026-07-10T02:00:00Z",
        base=False,
    )
    assert result == {"base": False, "sets": 2, "clears": 1, "recorded": True}
    assert read_lines(path)[4:] == [
        {
            "kind": "event",
            "source": "land",
            "at": "2026-07-10T02:00:00Z",
            "stamp": "S2",
            "base": False,
            "stashed": "verdicts-autosave-S1.json",
            "sets": 2,
            "clears": 1,
        },
        {"kind": "set", **reject},
        {"kind": "set", **v("u-4")},
        {"kind": "clear", "unit": "u-3"},
    ]
    assert journal.replay(path, as_of="2026-07-10T01:30:00Z") == (
        "S1",
        {u: v(u) for u in ("u-1", "u-2", "u-3")},
    )
    assert journal.replay(path) == ("S2", {"u-1": v("u-1"), "u-2": reject, "u-4": v("u-4")})


def test_a_stamp_change_onto_the_same_records_journals_its_event_line_alone(tmp_path):
    """Most lands that move the stamp land the same records as the store they replace. When no base is due, such a land skips the base it would have written: it journals only its event line, with or without a stash, and replay moves to the new stamp with the same records."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    for stamp, old_stamp, stashed, hour in (
        ("S2", "S1", "verdicts-autosave-S1.json", 2),
        ("S3", "S2", None, 3),
    ):
        result = journal.record_transition(
            path,
            source="land",
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=[v("u-1"), v("u-2")],
            new_verdicts=[v("u-2"), v("u-1")],
            stashed=stashed,
            at=f"2026-07-10T0{hour}:00:00Z",
            base=False,
        )
        assert result == {"base": False, "sets": 0, "clears": 0, "recorded": True}
        assert journal.replay(path) == (stamp, {"u-1": v("u-1"), "u-2": v("u-2")})
    assert [line["kind"] for line in read_lines(path)] == ["event", "set", "set", "event", "event"]


def test_a_stamp_change_from_no_store_or_onto_no_journal_is_a_base_even_when_none_is_due(tmp_path):
    """A diff needs a previous store that the journal replays to. With no journal yet, or no previous store, `base=False` still writes a base event, and never a seed holding the previous store under the new stamp."""
    path = tmp_path / "journal.ndjson"
    result = journal.record_transition(
        path,
        source="land",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T01:00:00Z",
        base=False,
    )
    assert result["base"] is True
    assert [(event["source"], event["stamp"], event["base"]) for event in journal.iter_events(path)] == [
        ("land", "S2", True)
    ]
    result = journal.record_transition(
        path,
        source="land",
        stamp="S3",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-3")],
        at="2026-07-10T02:00:00Z",
        base=False,
    )
    assert result["base"] is True
    assert journal.replay(path) == ("S3", {"u-3": v("u-3")})


def test_a_base_is_due_once_the_last_one_is_a_day_old_or_torn(tmp_path):
    """A land that moves the stamp writes a base when the journal has none, when the journal's last base is torn, or when its last base is at least `BASE_INTERVAL` older than the land, and a diff otherwise. A diff after the last base does not restart the interval, and a complete base after a torn one does."""
    path = tmp_path / "journal.ndjson"
    assert journal.base_due(journal.scan(path).events, "2026-07-10T01:00:00Z") is True
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    journal.record_transition(
        path,
        source="land",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1")],
        new_verdicts=[v("u-2")],
        at="2026-07-10T20:00:00Z",
        base=False,
    )
    events = journal.scan(path).events
    assert [(event.stamp, event.base) for event in events] == [("S1", True), ("S2", False)]
    assert journal.base_due(events, "2026-07-11T00:59:59Z") is False
    assert journal.base_due(events, "2026-07-11T01:00:00Z") is True
    _append_base(path, "S3", "S2", ["u-2"], ["u-3", "u-4"], "2026-07-11T02:00:00Z")
    path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[:-1]))
    assert journal.base_due(journal.scan(path).events, "2026-07-11T02:30:00Z") is True
    _append_base(path, "S4", "S3", ["u-3"], ["u-5"], "2026-07-11T03:00:00Z")
    assert journal.base_due(journal.scan(path).events, "2026-07-11T03:30:00Z") is False


def test_compaction_floors_at_a_base_and_replays_the_stamp_changes_journaled_after_it(tmp_path):
    """A stamp change journaled as sets and clears is never a compaction floor, so the journal is compacted to the newest base at or before the cutoff and every moment from that base on replays as it did before."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    _append_base(path, "S2", "S1", ["u-1"], ["u-1", "u-2"], "2026-07-11T01:00:00Z")
    for stamp, old_stamp, old_units, new_units, hour in (
        ("S3", "S2", ["u-1", "u-2"], ["u-1", "u-2", "u-3"], 5),
        ("S4", "S3", ["u-1", "u-2", "u-3"], ["u-2", "u-3"], 9),
    ):
        journal.record_transition(
            path,
            source="land",
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=[v(unit) for unit in old_units],
            new_verdicts=[v(unit) for unit in new_units],
            at=f"2026-07-11T0{hour}:00:00Z",
            base=False,
        )
    as_ofs = ["2026-07-11T01:00:00Z", "2026-07-11T05:00:00Z", "2026-07-11T08:00:00Z", None]
    before = {as_of: journal.replay(path, as_of=as_of) for as_of in as_ofs}
    result = journal.compact(path, cutoff="2026-07-11T08:00:00Z")
    assert result["compacted"] is True and result["floor_at"] == "2026-07-11T01:00:00Z"
    assert [(event["stamp"], event["base"]) for event in journal.iter_events(path)] == [
        ("S2", True),
        ("S3", False),
        ("S4", False),
    ]
    assert {as_of: journal.replay(path, as_of=as_of) for as_of in as_ofs} == before
    assert before[None] == ("S4", {"u-2": v("u-2"), "u-3": v("u-3")})
