"""Tests for the review server's logic.

The /autosave receiver: payload validation, atomic overwrite, and the journal event appended on every accepted save. A whole-store save on another stamp than the store's is refused with 409 and moves nothing aside. A delta on another stamp is carried onto a store on the served corpus by unit id, with the `base_at`, `at` and tombstone tests deciding each unit, and the orphans and conflicts it cannot apply are kept in the orphan document; a delta stamped for the served corpus moves a store on another stamp onto it, moving the old file aside, and one made on neither corpus gets a retryable 503 and writes nothing.

The POST path takes the verdict store's lock without waiting, and answers a retryable 503 while another writer holds it. The resident store behind it (`rebuild.review.verdict_store`): the delta POST the app sends, the no-op delta that writes nothing, the failed write that leaves memory as the file holds it, the change token a sync GET returns, and the reload after an external rewrite of the file, which keeps tokens valid when the stamp is unchanged.

The headers every static file is served with. `static_headers_for` is a pure function so it can be tested without a server.

The reloads: the server's one livereload watch never fires, and `force_reload` sends an `ams:` path to /forcereload on the port it is given and returns False for anything but a server that took it.
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from rebuild.review import app_index, journal
from rebuild.review.serve import (
    DELTA_FORMAT,
    EXPORT_FORMAT,
    parse_autosave_payload,
    receive_autosave,
    receive_autosave_locked,
    register_dormant_watch,
    stash_path_for,
    static_headers_for,
)
from rebuild.tools.review_server import force_reload
from rebuild.review.store_lock import store_lock
from rebuild.review.verdict_store import (
    ServedCorpus,
    VerdictStore,
    orphan_path,
    orphans_dir_for,
    parse_delta_payload,
)


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
    assert body == {"ok": True, "saved": 2, "corpus_stamp": "2026-07-03T23:31:04Z"}
    assert path.read_bytes() == raw
    assert not (tmp_path / "verdicts-autosave.json.tmp").exists()


def test_same_stamp_overwrites_in_place(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    receive_autosave(payload("2026-07-03T23:31:04Z", [verdict("u-0001")]), path)
    raw = payload("2026-07-03T23:31:04Z", [verdict("u-0001"), verdict("u-0002", "reject")])
    status, _ = receive_autosave(raw, path)
    assert status == 200
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


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
    status, _ = receive_autosave(raw, path)
    assert status == 200
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_parse_rejects_non_export_documents():
    assert parse_autosave_payload(payload("2026-07-03T23:31:04Z")) is not None
    assert parse_autosave_payload(b"[]") is None
    assert parse_autosave_payload(b"{}") is None


def test_stash_path_sanitizes_the_stamp(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stash = stash_path_for(path, "2026-07-03T23:31:04Z")
    assert stash.name == "verdicts-autosave-2026-07-03T23.31.04Z.json"
    assert stash.parent == tmp_path


def test_a_whole_store_post_on_another_stamp_is_refused_with_409_and_stashes_nothing(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    raw = payload("2026-07-03T23:31:04Z", [verdict("u-6344")])
    receive_autosave(raw, path)
    for other in ("2026-07-03T06:13:47Z", "2026-07-04T00:00:00Z"):
        status, body = receive_autosave(payload(other, [verdict("u-1015")]), path)
        assert status == 409
        assert body["ok"] is False
        assert "reload" in body["error"]
    assert path.read_bytes() == raw
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
        "units/boundary-window.000.json",
        "fonts/after.otf",
        app_index.LOCATOR_ROWS_NAME,
        f"/{app_index.LOCATOR_ROWS_NAME}",
    ):
        assert static_headers_for(path) == {"Cache-Control": "no-store"}


def test_the_servers_watch_never_reloads_a_tab_when_the_corpus_changes(tmp_path):
    """A tab reloads only for an `ams:` path it is sent, so the server's one watch must never fire: not on the manifest's creation, its rewrite, its removal, or its return. Registering a path keeps livereload from watching the working directory instead."""
    from livereload import Server

    server = Server()
    register_dormant_watch(server, tmp_path)
    manifest = tmp_path / "manifest.json"
    assert server.watcher.examine() == (None, None)
    manifest.write_text("{}")
    assert server.watcher.examine() == (None, None)
    later = time.time() + 5
    manifest.write_text('{"generated_at": "2026-09-30T11:00:00Z"}')
    os.utime(manifest, (later, later))
    assert server.watcher.examine() == (None, None)
    manifest.unlink()
    assert server.watcher.examine() == (None, None)


def test_force_reload_sends_the_path_to_forcereload_and_reports_a_missing_server():
    """The cycle tells open tabs what changed through livereload's /forcereload. The path arrives whole, and with nothing listening the call returns False instead of raising."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        assert force_reload("ams:corpus/2026-09-30T11:00:00Z", port=httpd.server_address[1]) is True
    finally:
        httpd.shutdown()
        httpd.server_close()
    [path] = seen
    split = urlsplit(path)
    assert split.path == "/forcereload"
    assert parse_qs(split.query) == {"path": ["ams:corpus/2026-09-30T11:00:00Z"]}

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    assert force_reload("ams:assets/abc", port=free_port) is False


def test_force_reload_reports_a_listener_that_does_not_speak_http():
    """Something else on the port answers with a malformed status line, which http.client raises as an error outside OSError. The reload is best effort, so it returns False and the pass goes on."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def answer():
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b"not http\r\n\r\n")

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        assert force_reload("ams:assets/abc", port=listener.getsockname()[1]) is False
        thread.join(timeout=5)


def delta(stamp, sets=(), clears=(), replay=None):
    body = {
        "format": DELTA_FORMAT,
        "manifest_generated_at": stamp,
        "sets": list(sets),
        "clears": list(clears),
    }
    if replay is not None:
        body["replay"] = replay
    return json.dumps(body).encode()


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
    assert body["ok"] and body["saved"] == 2
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


def test_a_post_while_another_writer_holds_the_store_lock_gets_a_retryable_503(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    assert receive_autosave_locked(store, payload(stamp, [verdict("u-1")]))[0] == 200
    before = path.read_bytes()
    journal_before = journal_path.read_bytes()
    with store_lock(path):
        status, body = receive_autosave_locked(store, delta(stamp, [verdict("u-2")]))
    assert status == 503
    assert body["retry"] is True and body["ok"] is False
    assert path.read_bytes() == before
    assert journal_path.read_bytes() == journal_before
    assert "u-2" not in store.records
    status, _ = receive_autosave_locked(store, delta(stamp, [verdict("u-2")]))
    assert status == 200
    assert "u-2" in {record["unit"] for record in on_disk(path)["verdicts"]}


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


def test_a_delta_that_changes_nothing_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(payload(stamp, [verdict("u-1")]))
    os.utime(path, ns=(1, 1))
    before = path.read_bytes()
    status, body = store.receive(delta(stamp, [verdict("u-1")], ["u-9"]))
    assert status == 200 and body["ok"]
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == 1
    assert not store.refresh_if_changed()


def test_a_delta_whose_write_fails_is_written_and_journaled_when_it_is_resent(tmp_path, monkeypatch):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    store.receive(payload(stamp, [verdict("u-1")]))
    real_write_bytes = store._write_bytes

    def disk_full(raw):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_bytes", disk_full)
    with pytest.raises(OSError):
        store.receive(delta(stamp, [verdict("u-2")], []))
    assert "u-2" not in store.records
    monkeypatch.setattr(store, "_write_bytes", real_write_bytes)
    status, _ = store.receive(delta(stamp, [verdict("u-2")], []))
    assert status == 200
    assert set(journal.latest_by_unit(json.loads(path.read_bytes())["verdicts"])) == {"u-1", "u-2"}
    assert set(journal.replay(journal_path)[1]) == {"u-1", "u-2"}


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


def test_an_external_rewrite_onto_another_stamp_is_picked_up_and_invalidates_tokens(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    store.receive(payload("2026-07-03T06:13:47Z", [verdict("u-1")]))
    token = store.token
    os.utime(path, ns=(1, 1))
    path.write_bytes(payload("2026-07-03T23:31:04Z", [verdict("u-1"), verdict("u-2")]))
    os.utime(path, ns=(2, 2))
    assert store.refresh_if_changed()
    assert store.stamp == "2026-07-03T23:31:04Z"
    assert set(store.records) == {"u-1", "u-2"}
    assert store.changes_since(token) is None
    assert not store.refresh_if_changed()


def test_a_same_stamp_external_rewrite_keeps_the_token_and_hands_back_only_what_moved(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(payload(stamp, [verdict("u-1"), verdict("u-2"), verdict("u-3")]))
    token = store.token
    os.utime(path, ns=(1, 1))
    path.write_bytes(
        payload(stamp, [verdict("u-1"), verdict("u-2", "reject", at="2026-07-03T01:00:00Z"), verdict("u-4")])
    )
    os.utime(path, ns=(2, 2))
    assert store.refresh_if_changed()
    moved = changes(store, token)
    assert [record["unit"] for record in moved["sets"]] == ["u-2", "u-4"]
    assert moved["clears"] == ["u-3"]
    assert moved["token"] == store.token


def test_a_file_renamed_over_the_store_with_the_same_mtime_and_size_is_picked_up(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    path.write_bytes(payload(stamp, [verdict("u-1", "reject")]))
    os.utime(path, ns=(1, 1))
    store = VerdictStore(path)
    replacement = tmp_path / "merged.json"
    replacement.write_bytes(payload(stamp, [verdict("u-1", "either")]))
    os.utime(replacement, ns=(1, 1))
    assert replacement.stat().st_size == path.stat().st_size
    os.replace(replacement, path)
    assert store.refresh_if_changed()
    assert store.records["u-1"]["verdict"] == "either"


def test_a_rename_over_the_file_during_the_read_is_picked_up_on_the_next_refresh(tmp_path, monkeypatch):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    path.write_bytes(payload(stamp, [verdict("u-1")]))
    os.utime(path, ns=(1, 1))
    replacement = tmp_path / "merged.json"
    replacement.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    os.utime(replacement, ns=(2, 2))
    real_open = Path.open

    def open_then_rename_over(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if self == path and "r" in mode and replacement.exists():
            os.replace(replacement, path)
        return handle

    monkeypatch.setattr(Path, "open", open_then_rename_over)
    store = VerdictStore(path)
    assert set(store.records) == {"u-1"}
    assert store.refresh_if_changed()
    assert set(store.records) == {"u-1", "u-2"}


def test_a_file_renamed_into_place_after_a_failed_open_is_picked_up_on_the_next_refresh(
    tmp_path, monkeypatch
):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    replacement = tmp_path / "merged.json"
    replacement.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    real_open = Path.open

    def rename_into_place_after_a_failed_open(self, mode="r", *args, **kwargs):
        try:
            return real_open(self, mode, *args, **kwargs)
        except FileNotFoundError:
            if self == path and replacement.exists():
                os.replace(replacement, path)
            raise

    monkeypatch.setattr(Path, "open", rename_into_place_after_a_failed_open)
    store = VerdictStore(path)
    assert store.records == {}
    assert store.refresh_if_changed()
    assert set(store.records) == {"u-1", "u-2"}


def test_a_rename_over_the_file_right_after_a_save_is_picked_up_on_the_next_refresh(tmp_path, monkeypatch):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(payload(stamp, [verdict("u-1")]))
    replacement = tmp_path / "merged.json"
    replacement.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    os.utime(replacement, ns=(2, 2))
    real_replace = os.replace

    def replace_then_rename_over(src, dst):
        real_replace(src, dst)
        if Path(dst) == path and replacement.exists():
            real_replace(replacement, path)

    monkeypatch.setattr("rebuild.review.verdict_store.os.replace", replace_then_rename_over)
    store.receive(delta(stamp, [verdict("u-3")]))
    assert store.refresh_if_changed()
    assert set(store.records) == {"u-1", "u-2"}


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
        json.dumps(
            {"format": DELTA_FORMAT, "manifest_generated_at": stamp, "clears": [{"at": "t"}]}
        ).encode(),
        json.dumps(
            {"format": DELTA_FORMAT, "manifest_generated_at": stamp, "clears": [{"unit": "u-1", "at": 5}]}
        ).encode(),
        json.dumps(
            {
                "format": DELTA_FORMAT,
                "manifest_generated_at": stamp,
                "sets": [{**verdict("u-1"), "base_at": 1}],
            }
        ).encode(),
        json.dumps({"format": DELTA_FORMAT, "manifest_generated_at": stamp, "replay": "yes"}).encode(),
    ):
        status, body = store.receive(raw)
        assert status == 400 and body["ok"] is False
    assert not path.exists()


OLD = "2026-07-03T06:13:47Z"
NEW = "2026-07-03T23:31:04Z"


def served(*ids, stamp=NEW):
    return ServedCorpus(stamp, frozenset(ids))


def orphan_records(path, stamp):
    document = parse_autosave_payload(orphan_path(orphans_dir_for(path), stamp).read_bytes())
    assert document is not None and document["manifest_generated_at"] == stamp
    return document["verdicts"]


def new_store(tmp_path, records, journal_path=None):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path, journal_path)
    assert store.receive(payload(NEW, records))[0] == 200
    return path, store


def test_parse_delta_payload_accepts_both_clear_shapes():
    parsed = parse_delta_payload(delta(NEW, clears=["u-1", {"unit": "u-2", "base_at": "t1", "at": "t2"}]))
    assert parsed is not None
    assert parsed["clears"] == [
        {"unit": "u-1", "base_at": None, "at": None},
        {"unit": "u-2", "base_at": "t1", "at": "t2"},
    ]
    assert parsed["replay"] is False


def test_a_stale_stamp_delta_on_a_served_unit_is_carried_and_journaled_as_carried(tmp_path):
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path, store = new_store(tmp_path, [verdict("u-1", at="t1")], journal_path)
    status, body = store.receive(
        delta(OLD, [{**verdict("u-2", "reject", at="t5"), "base_at": None}]), served("u-1", "u-2")
    )
    assert status == 200
    assert body["carried"] == ["u-2"] and body["orphaned"] == [] and body["conflicts"] == []
    assert body["corpus_stamp"] == NEW
    written = on_disk(path)
    assert written["manifest_generated_at"] == NEW
    assert {record["unit"] for record in written["verdicts"]} == {"u-1", "u-2"}
    event = list(journal.iter_events(journal_path))[-1]
    assert event["source"] == "autosave-carried" and event["stamp"] == NEW
    assert set(journal.replay(journal_path)[1]) == {"u-1", "u-2"}


def test_a_stale_stamp_set_on_a_unit_the_served_corpus_lacks_is_orphaned(tmp_path):
    path, store = new_store(tmp_path, [verdict("u-1")])
    before = path.read_bytes()
    status, body = store.receive(delta(OLD, [verdict("u-gone", "reject", at="t5")]), served("u-1"))
    assert status == 200
    assert body["orphaned"] == ["u-gone"] and body["carried"] == []
    assert path.read_bytes() == before
    [kept] = orphan_records(path, OLD)
    assert kept["unit"] == "u-gone" and kept["verdict"] == "reject" and kept["reason"] == "orphan"
    assert isinstance(kept["recorded_at"], str)
    store.receive(delta(OLD, [verdict("u-gone", "reject", at="t5")]), served("u-1"))
    assert len(orphan_records(path, OLD)) == 1


def test_a_stale_set_applies_on_a_base_at_match_and_conflicts_on_an_older_mismatch(tmp_path):
    path, store = new_store(tmp_path, [verdict("u-1", at="t5"), verdict("u-2", at="t5")])
    status, body = store.receive(
        delta(
            OLD,
            [
                {**verdict("u-1", "reject", at="t3"), "base_at": "t5"},
                {**verdict("u-2", "reject", at="t3"), "base_at": "t1"},
            ],
        ),
        served("u-1", "u-2"),
    )
    assert status == 200
    assert body["carried"] == ["u-1"]
    assert body["conflicts"] == [{"unit": "u-2", "server": verdict("u-2", at="t5")}]
    assert store.records["u-1"]["verdict"] == "reject"
    assert store.records["u-2"]["verdict"] == "approve"
    [kept] = orphan_records(path, OLD)
    assert (kept["unit"], kept["verdict"], kept["reason"]) == ("u-2", "reject", "conflict")


def test_a_stale_set_newer_than_the_stores_record_applies_without_a_base_at_match(tmp_path):
    _, store = new_store(tmp_path, [verdict("u-1", at="t5")])
    status, body = store.receive(
        delta(OLD, [{**verdict("u-1", "reject", at="t7"), "base_at": "t1"}]), served("u-1")
    )
    assert status == 200 and body["carried"] == ["u-1"] and body["conflicts"] == []


def test_a_stale_set_with_the_stores_at_applies_and_a_stale_skip_is_dropped(tmp_path):
    _, store = new_store(tmp_path, [{**verdict("u-1", at="t5"), "note": "[carried u-0@old] ok"}])
    status, body = store.receive(
        delta(
            OLD,
            [
                {**verdict("u-1", at="t5"), "note": "ok", "base_at": None},
                {**verdict("u-2", "skip", at="t6"), "base_at": None},
            ],
        ),
        served("u-1", "u-2"),
    )
    assert status == 200
    assert body["carried"] == ["u-1"] and body["conflicts"] == [] and body["orphaned"] == []
    assert store.records["u-1"]["note"] == "ok"
    assert "u-2" not in store.records


def test_a_replayed_set_on_a_unit_whose_tombstone_is_newer_is_a_conflict(tmp_path):
    path, store = new_store(tmp_path, [verdict("u-1", at="t1")])
    assert store.receive(delta(NEW, clears=[{"unit": "u-1", "base_at": "t1", "at": "t5"}]))[0] == 200
    assert store.cleared == {"u-1": "t5"}
    assert on_disk(path)["cleared"] == [{"unit": "u-1", "at": "t5"}]
    status, body = store.receive(
        delta(NEW, [{**verdict("u-1", "reject", at="t3"), "base_at": "t1"}], replay=True), served("u-1")
    )
    assert status == 200
    assert body["conflicts"] == [{"unit": "u-1", "server": None}]
    assert "u-1" not in store.records
    status, body = store.receive(
        delta(NEW, [{**verdict("u-1", "reject", at="t6"), "base_at": None}], replay=True), served("u-1")
    )
    assert body["conflicts"] == [] and store.records["u-1"]["verdict"] == "reject"
    assert store.cleared == {}
    assert "cleared" not in on_disk(path)


def test_tombstones_survive_a_reload_of_the_file(tmp_path):
    path, store = new_store(tmp_path, [verdict("u-1", at="t1"), verdict("u-2", at="t1")])
    store.receive(delta(NEW, clears=[{"unit": "u-1", "base_at": "t1", "at": "t5"}, "u-2"]))
    reloaded = VerdictStore(path)
    assert reloaded.cleared["u-1"] == "t5"
    assert set(reloaded.cleared) == {"u-1", "u-2"}
    assert reloaded.records == {}


def test_a_stale_clear_follows_the_same_tests_and_a_bare_clear_is_orphaned(tmp_path):
    path, store = new_store(
        tmp_path, [verdict("u-1", at="t5"), verdict("u-2", at="t5"), verdict("u-3", at="t5")]
    )
    status, body = store.receive(
        delta(
            OLD,
            clears=[
                {"unit": "u-1", "base_at": "t5", "at": "t2"},
                {"unit": "u-2", "base_at": "t1", "at": "t2"},
                "u-3",
            ],
        ),
        served("u-1", "u-2", "u-3"),
    )
    assert status == 200
    assert body["carried"] == ["u-1"]
    assert body["conflicts"] == [{"unit": "u-2", "server": verdict("u-2", at="t5")}]
    assert body["orphaned"] == ["u-3"]
    assert set(store.records) == {"u-2", "u-3"}
    reasons = {(record["unit"], record["reason"]) for record in orphan_records(path, OLD)}
    assert reasons == {("u-2", "conflict"), ("u-3", "orphan")}


def test_a_delta_on_the_served_stamp_moves_a_store_left_on_another_stamp_onto_it(tmp_path):
    """A pass that carried nothing, or whose verdict update failed, leaves the store on the old corpus while the server serves the new one; the first save from a tab on the new corpus moves the old file aside and starts the store again from that save, so the tab keeps saving."""
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path, store = new_store(tmp_path, [verdict("u-1", at="t1")], journal_path)
    old_bytes = path.read_bytes()
    old_token = store.token
    newer = "2026-07-05T00:00:00Z"
    status, body = store.receive(
        delta(newer, [verdict("u-9", "reject", at="t9")]), served("u-9", stamp=newer)
    )
    assert status == 200
    assert body["corpus_stamp"] == newer and body["token"] == store.token != old_token
    assert stash_path_for(path, NEW).read_bytes() == old_bytes
    written = on_disk(path)
    assert written["manifest_generated_at"] == newer
    assert [record["unit"] for record in written["verdicts"]] == ["u-9"]
    event = list(journal.iter_events(journal_path))[-1]
    assert event["stamp"] == newer and event["stashed"] == stash_path_for(path, NEW).name
    assert set(journal.replay(journal_path)[1]) == {"u-9"}
    status, body = store.receive(delta(OLD, [verdict("u-9", at="t95")]), served("u-9", stamp=newer))
    assert status == 200 and body["carried"] == ["u-9"]


def test_a_stale_delta_while_the_store_is_off_the_served_corpus_gets_a_retryable_503(tmp_path):
    path, store = new_store(tmp_path, [verdict("u-1")])
    before = path.read_bytes()
    for corpus in (served("u-1", stamp="2026-07-05T00:00:00Z"), None):
        status, body = store.receive(delta(OLD, [verdict("u-2", at="t9")]), corpus)
        assert status == 503
        assert body["retry"] is True and body["reason"] == "store-not-on-served-corpus"
    assert path.read_bytes() == before
    assert not orphans_dir_for(path).exists()


def test_an_unstamped_store_takes_the_served_stamp(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    status, body = store.receive(delta(OLD, [verdict("u-1", at="t1")]), served("u-1"))
    assert status == 200
    assert body["corpus_stamp"] == NEW and body["carried"] == ["u-1"]
    assert on_disk(path)["manifest_generated_at"] == NEW
    other = VerdictStore(tmp_path / "other.json")
    assert other.receive(delta(NEW, [verdict("u-1")]), served("u-1"))[0] == 200
    assert other.stamp == NEW
