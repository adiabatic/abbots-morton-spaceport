"""Tests for the review server's logic.

The /autosave receiver: payload validation, atomic overwrite, and the journal event appended on every accepted save. An existing autosave stamped for an older manifest is moved aside to a stash file, because it may be the only copy of unexported work from before a surface rebuild and its unit ids must not be mixed into the new surface. A save stamped older than the store is refused with 409, so a stale tab cannot overwrite a newer store.

The resident store behind it (`rebuild.review.verdict_store`): the delta POST the app sends, the change token a sync GET returns, and the reload after an external rewrite of the file.

The headers every static file is served with. `static_headers_for` is a pure function so it can be tested without a server.
"""

import json
import os

from rebuild.review import app_index, journal
from rebuild.review.serve import (
    DELTA_FORMAT,
    EXPORT_FORMAT,
    parse_autosave_payload,
    receive_autosave,
    stash_path_for,
    static_headers_for,
)
from rebuild.review.verdict_store import VerdictStore


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


def test_the_precompressed_sidecars_go_out_gzip_encoded():
    """The app fetches these as NDJSON and lets the browser decompress them, so the encoding must be declared. A file served as `application/gzip` arrives as bytes the streaming parser cannot read, and the failure gives no useful error."""
    for name, _fmt in app_index.ARTIFACTS:
        headers = static_headers_for(f"/{name}")
        assert headers["Content-Encoding"] == "gzip"
        assert headers["Content-Type"] == "application/x-ndjson"
        assert headers["Cache-Control"] == "no-store"


def test_everything_else_is_served_uncompressed_and_uncached():
    """The shards must be served identity-encoded, because the app reads one record inside a part by byte range. The locator's rows file must be too, because the app fetches one gzip member of it by the span the locator table names and Chrome refuses a partial response that declares a content encoding. Every file gets `no-store`, because a rebuild reuses every name."""
    for path in (
        "index.html",
        "app.js",
        "manifest.json",
        "units/boundary-echo.000.json",
        "fonts/after.otf",
        app_index.LOCATOR_ROWS_NAME,
        f"/{app_index.LOCATOR_ROWS_NAME}",
    ):
        assert static_headers_for(path) == {"Cache-Control": "no-store"}


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


def delta(stamp, sets=(), clears=()):
    return json.dumps(
        {"format": DELTA_FORMAT, "manifest_generated_at": stamp, "sets": list(sets), "clears": list(clears)}
    ).encode()


def on_disk(path) -> dict:
    parsed = parse_autosave_payload(path.read_bytes())
    assert parsed is not None
    return parsed


def changes(store, token) -> dict:
    found = store.changes_since(token)
    assert found is not None
    return found


def test_a_delta_applies_sets_and_clears_in_place_and_journals_them(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    assert store.receive(payload(stamp, [verdict("u-1"), verdict("u-2")]))[0] == 200
    status, body = store.receive(
        delta(stamp, [verdict("u-2", "reject", at="2026-07-03T01:00:00Z"), verdict("u-3")], ["u-1"])
    )
    assert status == 200
    assert body["ok"] and body["saved"] == 2 and body["stashed"] is None
    assert body["token"] == store.token
    written = on_disk(path)
    assert written["manifest_generated_at"] == stamp
    assert [(record["unit"], record["verdict"]) for record in written["verdicts"]] == [
        ("u-2", "reject"),
        ("u-3", "approve"),
    ]
    _, records = journal.replay(journal_path)
    assert {unit: record["verdict"] for unit, record in records.items()} == {
        "u-2": "reject",
        "u-3": "approve",
    }
    events = list(journal.iter_events(journal_path))
    assert (events[-1]["sets"], events[-1]["clears"]) == (2, 1)


def test_a_delta_that_changes_nothing_writes_no_journal_event(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    store.receive(payload(stamp, [verdict("u-1")]))
    before = list(journal.iter_events(journal_path))
    status, body = store.receive(delta(stamp, [verdict("u-1")], ["u-9"]))
    assert status == 200
    assert list(journal.iter_events(journal_path)) == before
    assert body["token"] == store.token


def test_a_delta_seeds_a_journal_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    receive_autosave(payload(stamp, [verdict("u-1"), verdict("u-2")]), path)
    store = VerdictStore(path, journal_path)
    store.receive(delta(stamp, [verdict("u-3")], ["u-2"]))
    events = list(journal.iter_events(journal_path))
    assert [event["source"] for event in events] == ["seed", "autosave"]
    assert events[0]["sets"] == 1
    _, records = journal.replay(journal_path)
    assert set(records) == {"u-1", "u-3"}


def test_a_delta_stamped_older_than_the_store_is_refused(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    store.receive(payload("2026-07-03T23:31:04Z", [verdict("u-6344")]))
    status, body = store.receive(delta("2026-07-03T06:13:47Z", [verdict("u-1015")]))
    assert status == 409
    assert body["ok"] is False
    assert on_disk(path)["verdicts"] == [verdict("u-6344")]


def test_a_delta_stamped_newer_than_the_store_stashes_the_file_and_starts_over(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    store = VerdictStore(path, journal_path)
    old_raw = payload("2026-07-03T06:13:47Z", [verdict("u-1015")])
    store.receive(old_raw)
    old_token = store.token
    status, body = store.receive(delta("2026-07-03T23:31:04Z", [verdict("u-6344")], ["u-1015"]))
    assert status == 200
    assert body["stashed"] == "verdicts-autosave-2026-07-03T06.13.47Z.json"
    assert (tmp_path / body["stashed"]).read_bytes() == old_raw
    written = on_disk(path)
    assert written["manifest_generated_at"] == "2026-07-03T23:31:04Z"
    assert [record["unit"] for record in written["verdicts"]] == ["u-6344"]
    assert store.changes_since(old_token) is None
    replayed_stamp, records = journal.replay(journal_path)
    assert replayed_stamp == "2026-07-03T23:31:04Z" and set(records) == {"u-6344"}


def test_changes_since_hands_back_only_what_moved_after_the_token(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    token = store.token
    assert store.changes_since(token) == {
        "format": DELTA_FORMAT,
        "manifest_generated_at": stamp,
        "token": token,
        "sets": [],
        "clears": [],
    }
    store.receive(delta(stamp, [verdict("u-3")], ["u-1"]))
    moved = changes(store, token)
    assert moved["token"] == store.token
    assert [record["unit"] for record in moved["sets"]] == ["u-3"]
    assert moved["clears"] == ["u-1"]
    assert changes(store, store.token)["sets"] == []
    for bad in (None, "", "nope", "other:1", f"{token.split(':')[0]}:99"):
        assert store.changes_since(bad) is None


def test_a_whole_store_post_moves_the_token_by_what_it_changed(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    token = store.token
    store.receive(payload(stamp, [verdict("u-1"), verdict("u-3")]))
    moved = changes(store, token)
    assert [record["unit"] for record in moved["sets"]] == ["u-3"]
    assert moved["clears"] == ["u-2"]


def test_an_external_rewrite_of_the_file_is_picked_up_and_invalidates_tokens(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(payload(stamp, [verdict("u-1")]))
    token = store.token
    os.utime(path, ns=(1, 1))
    path.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    os.utime(path, ns=(2, 2))
    assert store.refresh_if_changed()
    assert set(store.records) == {"u-1", "u-2"}
    assert store.changes_since(token) is None
    assert not store.refresh_if_changed()


def test_the_full_document_carries_the_token_only_when_asked(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(delta(stamp, [verdict("u-2"), verdict("u-1")]))
    plain = json.loads(store.payload_bytes())
    assert "token" not in plain and [record["unit"] for record in plain["verdicts"]] == ["u-1", "u-2"]
    with_token = json.loads(store.payload_bytes(token=True))
    assert with_token["token"] == store.token
    assert parse_autosave_payload(store.payload_bytes(token=True)) is not None


def test_malformed_deltas_are_rejected(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    stamp = "2026-07-03T23:31:04Z"
    for raw in (
        json.dumps(
            {"format": DELTA_FORMAT, "manifest_generated_at": stamp, "sets": [{"verdict": "approve"}]}
        ).encode(),
        json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": stamp, "clears": [1]}).encode(),
        json.dumps({"format": DELTA_FORMAT, "sets": []}).encode(),
    ):
        status, body = store.receive(raw)
        assert status == 400 and body["ok"] is False
    assert not path.exists()
