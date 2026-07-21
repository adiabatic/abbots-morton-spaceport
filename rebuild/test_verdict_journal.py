"""Tests for the verdict journal: transition diffs (sets, clears, base events on stamp changes), the seed event that opens a journal over a pre-existing store, and replay — including the as-of cutoff and tolerance of a truncated trailing line from a crashed append."""

import json

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
