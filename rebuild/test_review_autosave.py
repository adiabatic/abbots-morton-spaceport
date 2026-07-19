"""Tests for the review server's /autosave receiving logic: payload validation, atomic overwrite, the stash-aside of an existing autosave whose manifest generation is older than the incoming one (a stale-manifest autosave may be the only copy of un-exported work from before a surface rebuild, and its unit ids must never be silently joined to the new surface), the 409 refusal of the reverse direction (a stale tab must not clobber a newer store), and the delta journal appended on every accepted save."""

import json

from rebuild.review import journal
from rebuild.review.serve import EXPORT_FORMAT, parse_autosave_payload, receive_autosave, stash_path_for


def payload(stamp, verdicts=(), fmt=EXPORT_FORMAT):
    return json.dumps(
        {
            "format": fmt,
            "manifest_generated_at": stamp,
            "exported_at": stamp,
            "verdicts": list(verdicts),
        }
    ).encode()


def verdict(unit, kind="approve", at="2026-07-03T00:00:00Z"):
    return {"unit": unit, "verdict": kind, "note": "", "at": at}


def test_valid_payload_writes_the_file(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    raw = payload("2026-07-03T23:31:04Z", [verdict("u-0001"), verdict("u-0002")])
    status, body = receive_autosave(raw, path)
    assert status == 200
    assert body == {"ok": True, "saved": 2, "stashed": None}
    assert path.read_bytes() == raw
    assert not (tmp_path / "verdicts-autosave.json.tmp").exists()


def test_same_stamp_overwrites_in_place(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    receive_autosave(payload("2026-07-03T23:31:04Z", [verdict("u-0001")]), path)
    raw = payload("2026-07-03T23:31:04Z", [verdict("u-0001"), verdict("u-0002", "reject")])
    status, body = receive_autosave(raw, path)
    assert status == 200
    assert body["stashed"] is None
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_different_stamp_stashes_the_old_file(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    old_raw = payload("2026-07-03T06:13:47Z", [verdict("u-1015")])
    receive_autosave(old_raw, path)
    new_raw = payload("2026-07-03T23:31:04Z", [verdict("u-6344")])
    status, body = receive_autosave(new_raw, path)
    assert status == 200
    assert body["stashed"] == "verdicts-autosave-2026-07-03T06.13.47Z.json"
    assert path.read_bytes() == new_raw
    assert (tmp_path / body["stashed"]).read_bytes() == old_raw


def test_stash_survives_an_empty_incoming_store(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    old_raw = payload("2026-07-03T06:13:47Z", [verdict("u-1015")])
    receive_autosave(old_raw, path)
    status, body = receive_autosave(payload("2026-07-03T23:31:04Z"), path)
    assert status == 200
    assert body["saved"] == 0
    assert (tmp_path / body["stashed"]).read_bytes() == old_raw


def test_invalid_payloads_are_rejected_without_touching_the_file(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    good = payload("2026-07-03T23:31:04Z", [verdict("u-0001")])
    receive_autosave(good, path)
    for raw in (
        b"not json",
        b'"a string"',
        payload("2026-07-03T23:31:04Z", fmt="something-else/9"),
        json.dumps({"format": EXPORT_FORMAT, "manifest_generated_at": None, "verdicts": []}).encode(),
        json.dumps({"format": EXPORT_FORMAT, "manifest_generated_at": "x", "verdicts": "nope"}).encode(),
    ):
        status, body = receive_autosave(raw, path)
        assert status == 400
        assert body["ok"] is False
    assert path.read_bytes() == good
    assert list(tmp_path.iterdir()) == [path]


def test_corrupt_existing_file_is_overwritten_not_stashed(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(b"garbage from a crashed write")
    raw = payload("2026-07-03T23:31:04Z", [verdict("u-0001")])
    status, body = receive_autosave(raw, path)
    assert status == 200
    assert body["stashed"] is None
    assert path.read_bytes() == raw


def test_parse_rejects_non_export_documents():
    assert parse_autosave_payload(payload("2026-07-03T23:31:04Z")) is not None
    assert parse_autosave_payload(b"[]") is None
    assert parse_autosave_payload(b"{}") is None


def test_stash_path_sanitizes_the_stamp(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stash = stash_path_for(path, "2026-07-03T23:31:04Z")
    assert stash.name == "verdicts-autosave-2026-07-03T23.31.04Z.json"
    assert stash.parent == tmp_path


def test_older_stamped_incoming_is_refused_with_409(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    new_raw = payload("2026-07-03T23:31:04Z", [verdict("u-6344")])
    receive_autosave(new_raw, path)
    status, body = receive_autosave(payload("2026-07-03T06:13:47Z", [verdict("u-1015")]), path)
    assert status == 409
    assert body["ok"] is False
    assert "reload" in body["error"]
    assert path.read_bytes() == new_raw
    assert list(tmp_path.iterdir()) == [path]


def test_accepted_saves_append_to_the_journal(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    receive_autosave(payload(stamp, [verdict("u-1")]), path, journal_path)
    receive_autosave(
        payload(stamp, [verdict("u-1", "reject", at="2026-07-03T01:00:00Z"), verdict("u-2")]),
        path,
        journal_path,
    )
    receive_autosave(payload(stamp, [verdict("u-2")]), path, journal_path)
    replayed_stamp, records = journal.replay(journal_path)
    assert replayed_stamp == stamp
    assert set(records) == {"u-2"}
    events = list(journal.iter_events(journal_path))
    assert [event["source"] for event in events] == ["autosave", "autosave", "autosave"]
    assert events[-1]["clears"] == 1


def test_journal_seeds_from_a_store_that_predates_it(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    receive_autosave(payload(stamp, [verdict("u-1"), verdict("u-2")]), path)
    assert not journal_path.exists()
    receive_autosave(payload(stamp, [verdict("u-1"), verdict("u-2"), verdict("u-3")]), path, journal_path)
    events = list(journal.iter_events(journal_path))
    assert [event["source"] for event in events] == ["seed", "autosave"]
    _, records = journal.replay(journal_path)
    assert set(records) == {"u-1", "u-2", "u-3"}


def test_stash_is_recorded_in_the_journal_event(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    receive_autosave(payload("2026-07-03T06:13:47Z", [verdict("u-1015")]), path, journal_path)
    status, body = receive_autosave(payload("2026-07-03T23:31:04Z", [verdict("u-6344")]), path, journal_path)
    assert status == 200
    events = list(journal.iter_events(journal_path))
    assert events[-1]["base"] is True
    assert events[-1]["stashed"] == body["stashed"]
    _, records = journal.replay(journal_path)
    assert set(records) == {"u-6344"}
