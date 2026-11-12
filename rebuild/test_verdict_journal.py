"""Tests for the verdict journal (`rebuild/review/journal.py`): the sets, clears, and base events a transition writes, a stamp change the land writes as sets and clears against the store it replaced (an event line alone when the records are the same) unless a base is due (`base_due`, which a marker that the journal may lack a store write and a last base with no `cleared` list also make due), the diff would be no shorter than a base, or there is no previous store or journal, the seed event that opens a journal over an existing store, its tombstones included, the clear lines that record a change to the tombstones alone, replay with and without an as-of cutoff, compaction in two phases that keep an append made between them, the event scan resumed from where an earlier one ended, a journal cut shorter and regrown between the phases, which neither resumes over, tolerance of a trailing line torn by a crashed append, which the next append cuts off unless only its newline is missing, a base event torn short of its set lines, which opens a span replay refuses until the next complete base and which compaction never starts at, a compaction that floors at a base before stamp changes written as sets and clears, the cut back to the length a dead land recorded, made only while the journal is the same file, and the store's `accepted` field, which every event line carries and replay gives back. The tail, the events at or after a moment read back from the journal's end, stops at a torn line and raises `JournalGap` for a torn base in it, or gives the catch-up the events after that base, which it applies to the store file the base's writer wrote; the catch-up that applies it to a store file (`landing.catch_up`) writes nothing for a tail the store holds, starts the store over at a base, splits the lines by the accepted list into the override section, and reads a store out of unit order whole to the same result. The cases for a diff, a seed base, a stamp change's base and the flip to a base when a diff reaches the new store's record count are journaled both from two stores held in memory and through a hand merge's walk over the store it replaced, read back through its file, in step with the store it wrote, held as record lines (`held_store.Transition`), which appends the same lines."""

import json
import os

import pytest

from rebuild.review import accepted, journal
from rebuild.tools import held_store


def v(unit, verdict="approve", note="", at="2026-07-10T00:00:00Z"):
    return {"unit": unit, "verdict": verdict, "note": note, "at": at}


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def transition(
    path, walked, *, old_verdicts=(), new_verdicts=(), old_cleared=None, new_cleared=None, **options
):
    """Record a transition as `journal.record_transition` does from two stores held in memory, or, when `walked`, as a hand merge does (`held_store.Transition`): the old store read back through a descriptor held on its file and the new one held as record lines, checking that the walk appends the lines the stores held in memory append to a copy of the journal."""
    if not walked:
        return journal.record_transition(
            path,
            old_verdicts=old_verdicts,
            new_verdicts=new_verdicts,
            old_cleared=old_cleared,
            new_cleared=new_cleared,
            **options,
        )
    reference = path.with_name(path.name + ".reference")
    if path.exists():
        reference.write_bytes(path.read_bytes())
    expected = journal.record_transition(
        reference,
        old_verdicts=old_verdicts,
        new_verdicts=new_verdicts,
        old_cleared=old_cleared,
        new_cleared=new_cleared,
        **options,
    )
    old_file = path.with_name(path.name + ".old.json")
    old_file.write_text(
        json.dumps(journal.payload_for("S", journal.latest_by_unit(old_verdicts), old_cleared))
    )
    records = journal.latest_by_unit(new_verdicts)
    builder = held_store.Builder()
    for unit in sorted(records):
        builder.add(records[unit])
    held = builder.held(
        "S",
        None,
        {unit: at for unit, at in (new_cleared or {}).items() if unit not in records},
        accepted.Overrides(),
    )
    with old_file.open("rb") as old:
        walk = held_store.Transition(
            held, lambda: held_store.own_records(old, "old"), old_cleared or {}, accepted.Overrides()
        )
        result = journal.record_transition(path, transition=walk, **options)
    assert result == expected
    assert path.read_bytes() == reference.read_bytes()
    return result


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


@pytest.mark.parametrize("walked", [False, True])
def test_same_stamp_transition_journals_sets_and_clears(tmp_path, walked):
    path = tmp_path / "journal.ndjson"
    transition(
        path,
        walked,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1"), v("u-2")],
        at="2026-07-10T01:00:00Z",
    )
    result = transition(
        path,
        walked,
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


@pytest.mark.parametrize("walked", [False, True])
def test_stamp_change_journals_a_base_event(tmp_path, walked):
    path = tmp_path / "journal.ndjson"
    transition(
        path,
        walked,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    result = transition(
        path,
        walked,
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


@pytest.mark.parametrize("walked", [False, True])
def test_seed_event_opens_a_journal_over_an_existing_store(tmp_path, walked):
    path = tmp_path / "journal.ndjson"
    transition(
        path,
        walked,
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


@pytest.mark.parametrize("walked", [False, True])
def test_a_seed_event_holds_the_tombstones_of_a_store_with_no_records(tmp_path, walked):
    """A journal opened over a store that holds tombstones and no records starts with a seed base event holding them, whether a whole-store transition or a server save's delta opens it, so replay gives them from the journal's first line."""
    cleared = {"u-2": "2026-07-10T00:30:00Z"}
    whole, delta = tmp_path / "transition.ndjson", tmp_path / "delta.ndjson"
    transition(
        whole,
        walked,
        source="merge",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        old_cleared=cleared,
        new_cleared=cleared,
        at="2026-07-10T01:00:00Z",
    )
    journal.record_delta(
        delta,
        source="autosave",
        stamp="S1",
        sets=[v("u-1")],
        clears={},
        seed_records={},
        seed_cleared=cleared,
        at="2026-07-10T01:00:00Z",
    )
    for path in (whole, delta):
        seed = next(journal.iter_events(path))
        assert seed["source"] == "seed" and seed["cleared"] == [{"unit": "u-2", "at": cleared["u-2"]}]
        assert journal.replay_store(path) == ("S1", {"u-1": v("u-1")}, cleared)


@pytest.mark.parametrize("walked", [False, True])
def test_no_op_transition_appends_nothing(tmp_path, walked):
    path = tmp_path / "journal.ndjson"
    transition(
        path,
        walked,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-1")],
        at="2026-07-10T01:00:00Z",
    )
    before = path.read_text()
    result = transition(
        path,
        walked,
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


def test_a_scan_state_saved_again_over_a_journal_that_has_not_grown_is_left_in_place(tmp_path):
    """Retention saves the scan state after every green pass. While the journal has not grown, the state comes out the same, so the file keeps its inode and its modification time, here set long ago so that any write would move it; once the journal grows, the new state replaces it."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1"], "2026-07-10T01:00:00Z")
    state_path = tmp_path / "cycle" / "journal-scan.json"
    journal.save_scan_state(state_path, journal.scan(path).state)
    os.utime(state_path, ns=(1_000_000_000, 1_000_000_000))
    inode = state_path.stat().st_ino
    resumed = journal.scan(path, resume=journal.load_scan_state(state_path)).state
    journal.save_scan_state(state_path, resumed)
    assert (state_path.stat().st_ino, state_path.stat().st_mtime_ns) == (inode, 1_000_000_000)
    _append_base(path, "S2", "S1", ["u-1"], ["u-2"], "2026-07-10T03:00:00Z")
    journal.save_scan_state(state_path, journal.scan(path).state)
    assert journal.load_scan_state(state_path) == journal.scan(path).state
    assert state_path.stat().st_mtime_ns != 1_000_000_000
    assert sorted(entry.name for entry in state_path.parent.iterdir()) == ["journal-scan.json"]


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


@pytest.mark.parametrize("walked", [False, True])
def test_a_stamp_change_given_no_base_journals_sets_and_clears_against_the_previous_store(tmp_path, walked):
    """The land passes `base=False` when the journal is not due a base. A unit's id is its content key, so the stamp change is written against the store it replaced: the units whose records changed or are new, and the units the new store lacks, under one event that names the new stamp and the stash. Replay reads the old store before it and the new store from it on."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2", "u-3", "u-5"], "2026-07-10T01:00:00Z")
    reject = v("u-2", verdict="reject", at="2026-07-10T02:00:00Z")
    result = transition(
        path,
        walked,
        source="land",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2"), v("u-3"), v("u-5")],
        new_verdicts=[v("u-1"), reject, v("u-4"), v("u-5")],
        stashed="verdicts-autosave-S1.json",
        at="2026-07-10T02:00:00Z",
        base=False,
    )
    assert result == {"base": False, "sets": 2, "clears": 1, "recorded": True}
    assert read_lines(path)[5:] == [
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
        {"kind": "clear", "unit": "u-3", "at": None},
    ]
    assert journal.replay(path, as_of="2026-07-10T01:30:00Z") == (
        "S1",
        {u: v(u) for u in ("u-1", "u-2", "u-3", "u-5")},
    )
    assert journal.replay(path) == (
        "S2",
        {"u-1": v("u-1"), "u-2": reject, "u-4": v("u-4"), "u-5": v("u-5")},
    )


@pytest.mark.parametrize("walked", [False, True])
def test_a_change_to_the_tombstones_alone_replays_to_the_stores_tombstones(tmp_path, walked):
    """A clear line records each tombstone that appears, changes or goes on a unit the store holds no record for, as a same-stamp restore or merge changes them and as a land that moves the stamp drops one off the new corpus, and a set line removes its unit's tombstone, so replay gives the store's tombstones before and after each change."""
    path = tmp_path / "journal.ndjson"
    records = [v("u-1"), v("u-2"), v("u-3")]
    first = {"u-4": "2026-07-10T00:30:00Z", "u-5": "2026-07-10T00:30:00Z"}
    second = {"u-5": "2026-07-10T01:30:00Z", "u-6": "2026-07-10T01:30:00Z"}
    third = {"u-5": "2026-07-10T01:30:00Z"}
    transition(
        path,
        walked,
        source="merge",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=records,
        new_cleared=first,
        at="2026-07-10T01:00:00Z",
    )
    restored = transition(
        path,
        walked,
        source="restore",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=records,
        new_verdicts=records,
        old_cleared=first,
        new_cleared=second,
        at="2026-07-10T02:00:00Z",
    )
    landed = transition(
        path,
        walked,
        source="land",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=records,
        new_verdicts=records,
        old_cleared=second,
        new_cleared=third,
        at="2026-07-10T03:00:00Z",
        base=False,
    )
    saved = transition(
        path,
        walked,
        source="autosave",
        stamp="S2",
        old_stamp="S2",
        old_verdicts=records,
        new_verdicts=[*records, v("u-5", at="2026-07-10T04:00:00Z")],
        old_cleared=third,
        new_cleared={},
        at="2026-07-10T04:00:00Z",
    )
    assert restored == {"base": False, "sets": 0, "clears": 3, "recorded": True}
    assert landed == {"base": False, "sets": 0, "clears": 1, "recorded": True}
    assert saved == {"base": False, "sets": 1, "clears": 0, "recorded": True}
    store = {record["unit"]: record for record in records}
    assert journal.replay_store(path, as_of="2026-07-10T01:30") == ("S1", store, first)
    assert journal.replay_store(path, as_of="2026-07-10T02:30") == ("S1", store, second)
    assert journal.replay_store(path, as_of="2026-07-10T03:30") == ("S2", store, third)
    assert journal.replay_store(path) == ("S2", {**store, "u-5": v("u-5", at="2026-07-10T04:00:00Z")}, {})


@pytest.mark.parametrize("walked", [False, True])
def test_a_stamp_change_whose_sets_and_clears_reach_the_new_stores_size_is_a_base(tmp_path, walked):
    """A diff whose sets and clears reach the new store's record count is no shorter than a base, so `base=False` writes the base instead, which also puts replay right after a store write the journal missed. A diff one line shorter is still written as the diff."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2", "u-3"], "2026-07-10T01:00:00Z")
    reject = v("u-2", verdict="reject")
    for stamp, old_verdicts, new_verdicts, base in (
        ("S2", [v("u-1"), v("u-2"), v("u-3")], [v("u-1"), v("u-2"), v("u-4")], False),
        ("S3", [v("u-1"), v("u-2"), v("u-4")], [v("u-1"), reject, v("u-5")], True),
    ):
        result = transition(
            path,
            walked,
            source="land",
            stamp=stamp,
            old_stamp=f"S{int(stamp[1]) - 1}",
            old_verdicts=old_verdicts,
            new_verdicts=new_verdicts,
            at=f"2026-07-10T0{stamp[1]}:00:00Z",
            base=False,
        )
        assert result["base"] is base
        assert journal.replay(path) == (stamp, {record["unit"]: record for record in new_verdicts})
    events = journal.iter_events(path)
    assert [(event["stamp"], event["base"], event["sets"], event["clears"]) for event in events] == [
        ("S1", True, 3, 0),
        ("S2", False, 1, 1),
        ("S3", True, 3, 0),
    ]


@pytest.mark.parametrize("walked", [False, True])
def test_a_stamp_change_onto_the_same_records_journals_its_event_line_alone(tmp_path, walked):
    """Most lands that move the stamp land the same records as the store they replace. When no base is due, such a land skips the base it would have written: it journals only its event line, with or without a stash, and replay moves to the new stamp with the same records."""
    path = tmp_path / "journal.ndjson"
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    for stamp, old_stamp, stashed, hour in (
        ("S2", "S1", "verdicts-autosave-S1.json", 2),
        ("S3", "S2", None, 3),
    ):
        result = transition(
            path,
            walked,
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


def test_a_base_is_due_once_the_last_one_is_a_day_old_or_torn_or_while_a_marker_exists(tmp_path):
    """A land that moves the stamp writes a base when the journal has none, when the journal's last base is torn, when its last base holds no `cleared` list, when its last base is at least `BASE_INTERVAL` older than the land, or while the marker that says the journal may lack a store write exists, and a diff otherwise. A diff after the last base does not restart the interval, and a complete base after a torn one does. A writer that finds the marker already there does not write it again, so it never removes a marker an earlier write left."""
    path = tmp_path / "journal.ndjson"
    assert journal.base_due(journal.scan(path).events, "2026-07-10T01:00:00Z") is True
    _append_base(path, "S1", None, [], ["u-1", "u-2"], "2026-07-10T01:00:00Z")
    journal.record_transition(
        path,
        source="land",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[v("u-1"), v("u-2")],
        new_verdicts=[v("u-1"), v("u-2"), v("u-3")],
        at="2026-07-10T20:00:00Z",
        base=False,
    )
    events = journal.scan(path).events
    assert [(event.stamp, event.base) for event in events] == [("S1", True), ("S2", False)]
    assert journal.base_due(events, "2026-07-11T00:59:59Z") is False
    assert journal.base_due(events, "2026-07-11T01:00:00Z") is True
    store = tmp_path / "verdicts-autosave.json"
    marker = journal.unjournaled_marker_for(store)
    assert journal.base_due(events, "2026-07-11T00:59:59Z", marker=marker) is False
    assert journal.mark_unjournaled(store, "a test") is True
    assert journal.mark_unjournaled(store, "another test") is False
    assert json.loads(marker.read_text())["writer"] == "a test"
    assert journal.base_due(events, "2026-07-11T00:59:59Z", marker=marker) is True
    _append_base(path, "S3", "S2", ["u-2"], ["u-3", "u-4"], "2026-07-11T02:00:00Z")
    path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[:-1]))
    assert journal.base_due(journal.scan(path).events, "2026-07-11T02:30:00Z") is True
    _append_base(path, "S4", "S3", ["u-3"], ["u-5"], "2026-07-11T03:00:00Z")
    assert journal.base_due(journal.scan(path).events, "2026-07-11T03:30:00Z") is False


def test_a_base_is_due_while_the_last_base_holds_no_cleared_list(tmp_path):
    """A base event with no `cleared` list holds no tombstones, so while the journal's last base is one, a land that moves the stamp writes a base however recent that base is. A last base with a `cleared` list, an empty one included, follows the interval. A scan resumed from a saved state reads the same as a full one."""
    path = tmp_path / "journal.ndjson"
    state_path = tmp_path / "journal-scan.json"

    def due(at):
        full = journal.scan(path)
        journal.save_scan_state(state_path, full.state)
        resumed = journal.scan(path, resume=journal.load_scan_state(state_path))
        assert resumed.start == full.state.end and resumed.events == full.events
        return journal.base_due(full.events, at)

    event = {
        "kind": "event",
        "source": "land",
        "at": "2026-07-10T01:00:00Z",
        "stamp": "S1",
        "base": True,
        "stashed": None,
        "sets": 1,
        "clears": 0,
    }
    path.write_text(json.dumps(event) + "\n" + json.dumps({"kind": "set", **v("u-1")}) + "\n")
    assert due("2026-07-10T01:00:01Z") is True
    _append_base(path, "S2", "S1", ["u-1"], ["u-1"], "2026-07-10T02:00:00Z")
    assert read_lines(path)[-2]["cleared"] == []
    assert due("2026-07-11T01:59:59Z") is False
    assert due("2026-07-11T02:00:00Z") is True


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


def test_every_event_line_carries_the_stores_accepted_field_and_replay_gives_it_back(tmp_path):
    """A writer passes the store's `accepted` field, the list it pairs with, and every event line it appends carries it, a seed and a diff as well as a base, so `replay_with_list` gives back the field the store named at any moment, even when the first event naming it is not a base: a base starts the field over from its own, none when it names none, and a diff that names one sets it while one that names none keeps it."""
    path = tmp_path / "journal.ndjson"
    first = {"sha256": "1" * 64, "closed_at": "2026-11-05T18:00:00Z"}
    second = {"sha256": "2" * 64, "closed_at": "2026-11-07T18:00:00Z"}
    journal.record_delta(
        path,
        source="autosave",
        stamp="S1",
        sets=[v("u-2")],
        clears={},
        seed_records={"u-1": v("u-1")},
        at="2026-11-06T00:00:00Z",
        accepted=first,
    )
    assert [line.get("accepted") for line in read_lines(path) if line["kind"] == "event"] == [first, first]
    journal.record_delta(
        path, source="autosave", stamp="S1", sets=[v("u-3")], clears={}, at="2026-11-06T01:00:00Z"
    )
    assert journal.replay_with_list(path)[3] == first
    journal.record_transition(
        path,
        source="close-out",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[],
        at="2026-11-07T18:00:00Z",
        base=True,
        accepted=second,
    )
    assert journal.replay_with_list(path) == ("S1", {}, {}, second)
    assert journal.replay_with_list(path, as_of="2026-11-06T01:00:00Z")[3] == first
    journal.record_transition(
        path,
        source="restore",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[v("u-4")],
        at="2026-11-08T00:00:00Z",
        base=True,
    )
    assert journal.replay_with_list(path)[3] is None
    assert journal.payload_for("S1", {}, accepted=second)["accepted"] == second
    assert "accepted" not in journal.payload_for("S1", {})


def _events_at(path, moment):
    return [event.entry["at"] for event in journal.tail(path, moment).events]


def test_the_tail_holds_exactly_the_events_at_or_after_a_moment(tmp_path):
    """The tail reader reads back from the journal's end to the last event older than the moment, across as many blocks as a long base takes, and returns every event after it, those in the moment's own second included, each with the set lines that follow it; no moment gives every event, and a moment after the last event gives none."""
    path = tmp_path / "journal.ndjson"
    journal.record_delta(
        path, source="autosave", stamp="S1", sets=[v("u-1")], clears={}, at="2026-07-10T01:00:00Z"
    )
    many = [v(f"u-{index:05d}", note="x" * 60) for index in range(2000)]
    _append_base(path, "S2", "S1", ["u-1"], [record["unit"] for record in many], "2026-07-10T02:00:00Z")
    journal.record_delta(
        path, source="autosave", stamp="S2", sets=[v("u-2")], clears={}, at="2026-07-10T03:00:00Z"
    )
    journal.record_delta(
        path,
        source="autosave",
        stamp="S2",
        sets=[],
        clears={"u-2": "2026-07-10T03:00:00Z"},
        at="2026-07-10T03:00:00Z",
    )
    journal.record_delta(
        path, source="autosave", stamp="S2", sets=[v("u-3")], clears={}, at="2026-07-10T04:00:00Z"
    )
    assert path.stat().st_size > 2 * journal._TAIL_BLOCK
    assert _events_at(path, "2026-07-10T03:00:00Z") == [
        "2026-07-10T03:00:00Z",
        "2026-07-10T03:00:00Z",
        "2026-07-10T04:00:00Z",
    ]
    tail = journal.tail(path, "2026-07-10T01:30:00Z")
    assert [(event.entry["at"], event.base, event.counted) for event in tail.events] == [
        ("2026-07-10T02:00:00Z", True, 2000),
        ("2026-07-10T03:00:00Z", False, 1),
        ("2026-07-10T03:00:00Z", False, 0),
        ("2026-07-10T04:00:00Z", False, 1),
    ]
    assert [entry["kind"] for entry in tail.entries(tail.events[2].offset)] == [
        "event",
        "clear",
        "event",
        "set",
    ]
    assert (
        _events_at(path, None)
        == _events_at(path, "2026-07-10T00:00:00Z")
        == [
            "2026-07-10T01:00:00Z",
            *_events_at(path, "2026-07-10T01:30:00Z"),
        ]
    )
    assert _events_at(path, "2026-07-10T05:00:00Z") == []
    assert journal.tail(tmp_path / "missing.ndjson", None).events == ()


def test_the_tail_stops_at_a_torn_final_line(tmp_path):
    """A final line a crashed append left half written is no line of the tail: the reader stops parsing there, as every reader does, so neither the set line nor the event line it began is counted."""
    path = tmp_path / "journal.ndjson"
    journal.record_delta(
        path, source="autosave", stamp="S1", sets=[v("u-1")], clears={}, at="2026-07-10T01:00:00Z"
    )
    with path.open("ab") as handle:
        handle.write(b'{"kind": "set", "unit": "u-2", "verd')
    [event] = journal.tail(path, "2026-07-10T01:00:00Z").events
    assert event.counted == 1 and [entry["kind"] for entry in journal.tail(path, None).entries(0)] == [
        "event",
        "set",
    ]
    path.write_bytes(
        path.read_bytes().rsplit(b"\n", 1)[0]
        + b'\n{"kind": "event", "source": "autosave", "at": "2026-07-10T0'
    )
    assert [event.entry["at"] for event in journal.tail(path, None).events] == ["2026-07-10T01:00:00Z"]


def test_a_torn_base_in_the_tail_raises_a_journal_gap_and_the_catch_up_applies_the_events_after_it(tmp_path):
    """A base event in the tail cut short of its set lines, with no complete base after it, leaves the store from it on unknown, so the tail reader raises `JournalGap` as replay does; a tail that starts after the torn base holds no gap. Its writer wrote the store file before it began the base, so a catch-up, which asks the reader for the events after the torn base, applies those to the file: here the save a review server journaled after a merge that died mid-base."""
    from rebuild.review import landing

    store = tmp_path / "verdicts-autosave.json"
    path = journal.journal_path_for(store)
    _torn_base_journal(path)
    with pytest.raises(journal.JournalGap) as gap:
        journal.tail(path, "2026-07-10T02:00:00Z")
    assert gap.value.torn_at == "2026-07-10T03:00:00Z"
    assert _events_at(path, "2026-07-10T03:30:00Z") == ["2026-07-10T04:00:00Z"]
    after = journal.tail(path, "2026-07-10T02:00:00Z", after_torn_base=True)
    assert [event.entry["at"] for event in after.events] == ["2026-07-10T04:00:00Z"]
    _store_file(store, "S2", [v("u-7"), v("u-8")], exported_at="2026-07-10T02:59:59Z")
    assert _caught_up(store, path, None) == landing.CatchUp(1, True)
    assert _read(store) == (
        "S2",
        {"u-7": v("u-7"), "u-8": v("u-8"), "u-9": v("u-9")},
        {},
        None,
        accepted.Overrides(),
    )
    _append_base(path, "S3", "S2", ["u-7"], ["u-5"], "2026-07-10T05:00:00Z")
    assert len(journal.tail(path, "2026-07-10T02:00:00Z").events) == 3


def _store_file(path, stamp, records, cleared=None, exported_at="2026-07-10T00:00:00Z", **fields):
    document = journal.payload_for(
        stamp, {record["unit"]: record for record in records}, cleared, exported_at, **fields
    )
    path.write_text(json.dumps(document))


def _caught_up(store, path, listed):
    from rebuild.review import landing

    caught = landing.catch_up(store, path, listed)
    assert caught is not None
    return caught


def _read(path):
    from rebuild.review import landing

    store = landing.read_store(path)
    assert store is not None
    return store.stamp, store.records, store.cleared, store.accepted, store.overrides


def test_a_catch_up_over_a_tail_the_store_holds_writes_nothing_and_one_with_a_base_starts_the_store_over(
    tmp_path,
):
    """The catch-up applies the journal's events at or after the store file's `exported_at`. A tail the file already holds, every set and clear line stating its unit's whole state, changes no record, so it writes nothing and the file keeps its bytes and inode; one past the file's last write gives the store the journal replays to. A tail holding a base event starts the store over from that base and the lines after it, whatever the file held."""
    from rebuild.review import landing

    store = tmp_path / "verdicts-autosave.json"
    path = journal.journal_path_for(store)
    _store_file(store, "S1", [v("u-1"), v("u-2", "reject")], {"u-3": "2026-07-09T00:00:00Z"})
    journal.record_delta(
        path,
        source="autosave",
        stamp="S1",
        sets=[v("u-2", "reject")],
        clears={"u-3": "2026-07-09T00:00:00Z"},
        at="2026-07-10T00:00:00Z",
    )
    before = store.read_bytes(), store.stat().st_ino
    assert landing.catch_up(store, path, None) == landing.CatchUp(1)
    assert (store.read_bytes(), store.stat().st_ino) == before
    journal.record_delta(
        path,
        source="autosave",
        stamp="S1",
        sets=[v("u-4", note="ñ")],
        clears={"u-1": "2026-07-11T00:00:00Z"},
        at="2026-07-11T00:00:00Z",
    )
    assert landing.catch_up(store, path, None) == landing.CatchUp(2, True)
    assert _read(store) == (
        "S1",
        {"u-2": v("u-2", "reject"), "u-4": v("u-4", note="ñ")},
        {"u-1": "2026-07-11T00:00:00Z", "u-3": "2026-07-09T00:00:00Z"},
        None,
        accepted.Overrides(),
    )
    _store_file(store, "S1", [v("u-1"), v("u-2")])
    _append_base(path, "S2", "S1", ["u-1"], ["u-5", "u-6"], "2026-07-12T00:00:00Z")
    journal.record_delta(
        path, source="autosave", stamp="S2", sets=[v("u-7")], clears={"u-5": None}, at="2026-07-12T00:00:01Z"
    )
    assert _caught_up(store, path, None).written
    stamp, records, cleared = journal.replay_store(path)
    assert _read(store) == (stamp, records, cleared, None, accepted.Overrides())
    assert set(records) == {"u-6", "u-7"} and stamp == "S2"


def test_a_catch_up_puts_the_lines_on_listed_units_into_the_override_section(tmp_path):
    """The journal holds the store's own records and its override section as one store, so the catch-up splits the tail's lines by the accepted list beside the store, as a restore splits a replay: a set or a clear on a listed unit goes to the override section, a set back to its accepted record removes its override, and every other line goes to the store's own records and tombstones. A base event's records and tombstones are split the same way."""
    from rebuild.review import landing

    A, B, C = "u-1AAAAAAAAAA", "u-1BBBBBBBBBB", "u-1CCCCCCCCCC"
    store = tmp_path / "verdicts-autosave.json"
    path = journal.journal_path_for(store)
    listed = accepted.path_for(store)
    listed.parent.mkdir()
    listed.write_bytes(accepted.render("2026-07-01T00:00:00Z", {A: "approve", B: "either", C: "approve"}))
    field = accepted.read(listed).field()
    section = accepted.Overrides({C: v(C, "reject")})
    _store_file(store, "S1", [v("u-1")], accepted=field, overrides=section.field())
    journal.record_delta(
        path,
        source="autosave",
        stamp="S1",
        sets=[v(A, "reject"), v("u-2")],
        clears={B: "2026-07-11T00:00:00Z", C: None, "u-1": "2026-07-11T00:00:00Z"},
        at="2026-07-11T00:00:00Z",
        accepted=field,
    )
    assert _caught_up(store, path, listed).written
    assert _read(store) == (
        "S1",
        {"u-2": v("u-2")},
        {"u-1": "2026-07-11T00:00:00Z"},
        field,
        accepted.Overrides({A: v(A, "reject")}, {B: "2026-07-11T00:00:00Z"}),
    )
    _store_file(store, "S1", [v("u-1")], accepted=field, overrides=section.field())
    journal.record_transition(
        path,
        source="land",
        stamp="S2",
        old_stamp=None,
        new_verdicts=[v(A, "reject"), v("u-3")],
        new_cleared={B: "2026-07-11T00:00:00Z", "u-4": "2026-07-12T00:00:00Z"},
        at="2026-07-12T00:00:00Z",
        accepted=field,
    )
    assert _caught_up(store, path, listed).written
    assert _read(store) == (
        "S2",
        {"u-3": v("u-3")},
        {"u-4": "2026-07-12T00:00:00Z"},
        field,
        accepted.Overrides({A: v(A, "reject")}, {B: "2026-07-11T00:00:00Z"}),
    )


def test_a_catch_up_reads_a_store_whose_records_are_out_of_unit_order_whole_to_the_same_store(tmp_path):
    """A store whose own records do not come in unit order cannot be streamed beside the tail, so the catch-up reads it whole, which leaves the store the stream leaves for the same records in order."""
    from rebuild.review import landing

    stores = []
    for order in ("sorted", "unsorted"):
        root = tmp_path / order
        root.mkdir()
        store = root / "verdicts-autosave.json"
        records = [v("u-1"), v("u-2"), v("u-3")]
        document = journal.payload_for(
            "S1", {record["unit"]: record for record in records}, None, "2026-07-10T00:00:00Z"
        )
        if order == "unsorted":
            document["verdicts"].reverse()
        store.write_text(json.dumps(document))
        journal.record_delta(
            journal.journal_path_for(store),
            source="autosave",
            stamp="S1",
            sets=[v("u-4")],
            clears={"u-2": "t9"},
            at="2026-07-11T00:00:00Z",
        )
        assert _caught_up(store, journal.journal_path_for(store), None).written
        stores.append(json.loads(store.read_bytes()) | {"exported_at": ""})
    assert stores[0] == stores[1]
