"""Tests for the review server's logic.

The /autosave receiver: delta validation, atomic overwrite, and the journal event appended on every accepted save. A whole-store POST is refused without changing the store, journal, token, or tombstones, regardless of either stamp or whether the store exists. Export parsing and full-store GET payloads remain available. A delta on another stamp is carried onto a store on the served corpus by unit id, with the `base_at`, `at` and tombstone tests deciding each unit, and the orphans and conflicts it cannot apply are kept in the orphan document; a stale skip is dropped and clears the record it replaced; a delta stamped for the served corpus carries a store on another stamp onto it by unit id, keeping the old file as its stash, and one made on neither corpus gets a retryable 503 and writes nothing.

The POST path takes the verdict store's lock without waiting, and answers a retryable 503 while another writer holds it. The resident store behind it (`rebuild.review.verdict_store`): the delta POST the app sends, journaled first and left pending, whose pending write writes the bytes a store with no journal writes after the same saves, the no-op delta that writes nothing, the idle write that runs once after a burst of saves, in a real server's POST handler too, and tries again shortly while another writer holds the lock and only after the whole delay when its write fails, the save whose append and write both fail, which is put back and cut off the journal, the marker that says the journal may lack a store write, which a save writes, with the store, only when its append fails, and a move writes before its store write and removes once its append succeeds, the server killed before its idle write, whose saves a lock holder and the next server's boot read from the journal, the change token a sync GET returns, the reload after an external rewrite of the file, which keeps tokens valid when the stamp is unchanged, and the store file holding a record the carry refuses, which serves no verdict and refuses every save until it is replaced, as a request made while a dead land's recovery refuses gets that refusal.

The headers every static file is served with. `static_headers_for` is a pure function so it can be tested without a server.

The reloads: the server's one livereload watch never fires, and `force_reload` sends an `ams:` path to /forcereload on the port it is given and returns False for anything but a server that took it.

The accepted list beside the store (`rebuild.review.accepted`): the full GET's `accepted` field, which the shared samples pin, and the store file's, which names the list; a set on an accepted unit stored as an override, a clear of one kept as a tombstone, and a set equal to its accepted record returning it to the list, journaled as a removal and reported back flagged; a stale save that differs from the mark answered with the accepted record; a stale skip on one leaving a tombstone; a move onto the served corpus leaving a tombstone on each accepted unit it lacks that the store held a record or tombstone for; a new list invalidating every token; a malformed or unpaired list refusing every save and GET; and the empty store the server writes at boot beside a list.
"""

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from rebuild.review import accepted, app_index, journal, landing, verdict_store
from rebuild.review.serve import (
    DELTA_FORMAT,
    EXPORT_FORMAT,
    IdleWrite,
    missing_autosave_reply,
    orphaned_land_reply,
    parse_autosave_payload,
    receive_autosave,
    receive_autosave_locked,
    register_dormant_watch,
    stash_path_for,
    static_headers_for,
    stream_full_store,
    write_pending_store,
)
from rebuild.tools.review_server import force_reload
from rebuild.review.store_lock import store_lock
from rebuild.review.verdict_store import (
    MalformedSet,
    ServedCorpus,
    VerdictStore,
    orphan_path,
    orphans_dir_for,
    parse_delta_payload,
    well_formed,
)

VERDICT_RECORD_SAMPLES = (
    Path(__file__).resolve().parent / "review" / "fixtures" / "verdict-record-samples.json"
)
DELTA_SET_SAMPLES = Path(__file__).resolve().parent / "review" / "fixtures" / "delta-set-samples.json"
ACCEPTED_SAMPLES = Path(__file__).resolve().parent / "review" / "fixtures" / "accepted-samples.json"


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


def test_valid_delta_writes_the_export_file_atomically(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    records = [verdict("u-0001"), verdict("u-0002")]
    raw = delta(stamp, records)
    status, body = receive_autosave(raw, path)
    assert status == 200
    assert body["ok"] is True and body["saved"] == 2 and body["corpus_stamp"] == stamp
    written = on_disk(path)
    assert written["format"] == EXPORT_FORMAT
    assert written["manifest_generated_at"] == stamp
    assert written["verdicts"] == records
    assert not (tmp_path / "verdicts-autosave.json.tmp").exists()


@pytest.mark.parametrize("existing", ["missing", "corrupt", "unstamped", "same-stamp", "other-stamp"])
@pytest.mark.parametrize("incoming_stamp", ["2026-07-03T23:31:04Z", "2026-07-03T06:13:47Z"])
@pytest.mark.parametrize("has_journal", [False, True])
def test_a_whole_store_post_is_refused_without_changing_any_state(
    tmp_path, existing, incoming_stamp, has_journal
):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    if existing == "corrupt":
        path.write_bytes(b"garbage from a crashed write")
    elif existing != "missing":
        stamp = "2026-07-03T06:13:47Z" if existing == "other-stamp" else "2026-07-03T23:31:04Z"
        document = json.loads(payload(stamp, [verdict("u-1")]))
        document["cleared"] = [{"unit": "u-deleted", "at": "t5"}]
        if existing == "unstamped":
            document["manifest_generated_at"] = None
        path.write_bytes(json.dumps(document).encode())
    if path.exists():
        os.utime(path, ns=(1, 1))
    if has_journal:
        journal_path.write_bytes(b'{"existing-journal": true}\n')
        os.utime(journal_path, ns=(2, 2))
    store = VerdictStore(path, journal_path)
    before = {
        "files": {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in tmp_path.iterdir()},
        "stamp": store.stamp,
        "records": {unit: dict(record) for unit, record in store.records.items()},
        "cleared": dict(store.cleared),
        "token": store.token,
    }

    status, body = store.receive(
        payload(incoming_stamp, [verdict("u-incoming", "reject")]), served("u-1", "u-incoming")
    )

    assert status == 400
    assert body["ok"] is False and body["reason"] == "whole-store-post-unsupported"
    assert not {"corpus_stamp", "token", "saved"}.intersection(body)
    error = body["error"].lower()
    for instruction in (
        "export",
        "pending",
        "reload",
        "current",
        "import",
        "file",
        "reapply",
        "clear",
        "note",
    ):
        assert instruction in error
    assert "close" in error or "closing" in error
    after_files = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in tmp_path.iterdir()}
    assert after_files == before["files"]
    assert store.stamp == before["stamp"]
    assert store.records == before["records"]
    assert store.cleared == before["cleared"]
    assert store.token == before["token"]


def test_invalid_payloads_are_rejected_without_touching_the_file(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    good = payload("2026-07-03T23:31:04Z", [verdict("u-0001")])
    path.write_bytes(good)
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


def test_a_delta_replaces_a_corrupt_store_without_stashing_it(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(b"garbage from a crashed write")
    stamp = "2026-07-03T23:31:04Z"
    records = [verdict("u-0001")]
    status, body = receive_autosave(delta(stamp, records), path)
    assert status == 200 and body["ok"] is True
    written = on_disk(path)
    assert written["manifest_generated_at"] == stamp
    assert written["verdicts"] == records
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


def test_an_old_tabs_whole_store_post_cannot_erase_another_tabs_verdict_or_tombstone(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    assert store.receive(delta(stamp, [verdict("u-1", at="t1"), verdict("u-3", at="t1")]))[0] == 200
    stale_snapshot = payload(stamp, list(store.records.values()))
    assert (
        store.receive(delta(stamp, [verdict("u-2", "reject", at="t5")], [{"unit": "u-3", "at": "t5"}]))[0]
        == 200
    )
    before = path.read_bytes(), journal_path.read_bytes(), store.token
    status, body = store.receive(stale_snapshot)
    assert status == 400 and body["reason"] == "whole-store-post-unsupported"
    assert (path.read_bytes(), journal_path.read_bytes(), store.token) == before
    assert set(store.records) == {"u-1", "u-2"}
    assert store.records["u-2"]["verdict"] == "reject"
    assert store.cleared == {"u-3": "t5"}
    assert set(journal.replay(journal_path)[1]) == {"u-1", "u-2"}


def test_accepted_saves_append_to_the_journal(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    receive_autosave(delta(stamp, [verdict("u-1")]), path, journal_path)
    receive_autosave(
        delta(stamp, [verdict("u-1", "reject", at="2026-07-03T01:00:00Z"), verdict("u-2")]),
        path,
        journal_path,
    )
    receive_autosave(delta(stamp, clears=["u-1"]), path, journal_path)
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
    path.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    assert not journal_path.exists()
    receive_autosave(delta(stamp, [verdict("u-3")]), path, journal_path)
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
        "units/split-by-space-or-zwnj.000.json",
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


def blank_exported_at(raw: bytes) -> bytes:
    return re.sub(rb'"exported_at": "[^"]*"', b'"exported_at": ""', raw)


def changes(store, token) -> dict:
    found = store.changes_since(token)
    assert found is not None
    return found


def test_a_delta_applies_sets_and_clears_in_place_and_journals_them(tmp_path):
    """A save on a store with a journal applies its delta in memory and appends its set and clear lines, and leaves the file's bytes, inode and mtime as they were (the first save, which stamps the unstamped store, writes it at once). The pending write then writes the bytes a store with no journal writes after the same saves, its `exported_at` aside."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    (tmp_path / "today").mkdir()
    today = VerdictStore(tmp_path / "today" / "verdicts-autosave.json")
    saves = [
        delta(stamp, [verdict("u-1"), verdict("u-2")]),
        delta(stamp, [verdict("u-2", "reject", at="2026-07-03T01:00:00Z"), verdict("u-3")], ["u-1"]),
    ]
    assert store.receive(saves[0])[0] == 200 and today.receive(saves[0])[0] == 200
    os.utime(path, ns=(1, 1))
    before = path.read_bytes(), path.stat().st_ino
    status, body = store.receive(saves[1])
    assert status == 200 and today.receive(saves[1])[0] == 200
    assert body["ok"] and body["saved"] == 2
    assert body["token"] == store.token
    assert store.pending and (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == (*before, 1)
    assert [event["sets"] for event in journal.iter_events(journal_path)] == [2, 2]
    store.write()
    assert not store.pending
    assert blank_exported_at(path.read_bytes()) == blank_exported_at(today.path.read_bytes())
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
    assert receive_autosave_locked(store, delta(stamp, [verdict("u-1")]))[0] == 200
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
    assert status == 200 and write_pending_store(store)
    assert "u-2" in {record["unit"] for record in on_disk(path)["verdicts"]}


def test_a_delta_that_changes_nothing_writes_no_journal_event(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    store.receive(delta(stamp, [verdict("u-1")]))
    before = list(journal.iter_events(journal_path))
    status, body = store.receive(delta(stamp, [verdict("u-1")], ["u-9"]))
    assert status == 200
    assert list(journal.iter_events(journal_path)) == before
    assert body["token"] == store.token


def test_a_delta_that_changes_nothing_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(delta(stamp, [verdict("u-1")]))
    os.utime(path, ns=(1, 1))
    before = path.read_bytes()
    status, body = store.receive(delta(stamp, [verdict("u-1")], ["u-9"]))
    assert status == 200 and body["ok"]
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == 1
    assert not store.refresh_if_changed()


class FakeTimers:
    """A stand-in for the server's IOLoop timers that runs a callback only when the test moves its clock past it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.pending: list[tuple[float, Callable[[], None], object]] = []

    def call_later(self, delay, callback):
        handle = object()
        self.pending.append((self.now + delay, callback, handle))
        return handle

    def remove_timeout(self, timeout) -> None:
        self.pending = [entry for entry in self.pending if entry[2] is not timeout]

    def advance(self, seconds: float) -> None:
        self.now += seconds
        while due := sorted(
            (entry for entry in self.pending if entry[0] <= self.now), key=lambda entry: entry[0]
        ):
            self.pending.remove(due[0])
            due[0][1]()


def test_the_idle_write_writes_once_after_a_burst_of_saves_and_tries_again_while_the_lock_is_held(
    tmp_path, monkeypatch
):
    """The server's idle write, its minute cut short: each save that leaves the store pending puts the write off again, so none runs while saves keep coming and one runs once the delay passes with no save, writing every save of the burst; while another writer holds the store's lock it writes nothing and tries again after the retry delay, and a write that fails, which can have written most of a store first, is tried again only after the whole delay."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    assert receive_autosave_locked(store, delta(stamp, [verdict("u-0")]))[0] == 200
    writes: list[int] = []
    real_write_bytes = store._write_bytes

    def counted(chunks):
        writes.append(1)
        real_write_bytes(chunks)

    monkeypatch.setattr(store, "_write_bytes", counted)
    timers = FakeTimers()
    idle = IdleWrite(store, lambda: timers, delay=60, retry=5)
    for index in range(1, 6):
        assert receive_autosave_locked(store, delta(stamp, [verdict(f"u-{index}")]))[0] == 200
        assert store.pending
        idle.saved()
        timers.advance(59)
    assert writes == [] and len(timers.pending) == 1
    timers.advance(1)
    assert writes == [1] and not store.pending and not timers.pending
    assert {record["unit"] for record in on_disk(path)["verdicts"]} == {f"u-{index}" for index in range(6)}
    assert receive_autosave_locked(store, delta(stamp, [verdict("u-6")]))[0] == 200
    idle.saved()
    with store_lock(path):
        timers.advance(60)
        timers.advance(5)
        assert writes == [1] and store.pending and len(timers.pending) == 1
    timers.advance(5)
    assert writes == [1, 1] and not store.pending and not timers.pending
    assert "u-6" in {record["unit"] for record in on_disk(path)["verdicts"]}

    def disk_full(chunks):
        writes.append(0)
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_bytes", disk_full)
    assert receive_autosave_locked(store, delta(stamp, [verdict("u-7")]))[0] == 200
    idle.saved()
    timers.advance(60)
    assert writes == [1, 1, 0] and store.pending
    timers.advance(59)
    assert writes == [1, 1, 0] and len(timers.pending) == 1
    monkeypatch.setattr(store, "_write_bytes", counted)
    timers.advance(1)
    assert writes == [1, 1, 0, 1] and not store.pending and not timers.pending
    assert "u-7" in {record["unit"] for record in on_disk(path)["verdicts"]}


REPO = Path(__file__).resolve().parent.parent
SERVER_CHILD = """
import sys
from pathlib import Path
from rebuild.review import accepted, journal, serve
root = Path(sys.argv[1])
serve.REPO_ROOT = root
serve.REVIEW_DIR = root / "rebuild" / "out" / "review"
serve.M1_OUT = root / "rebuild" / "out" / "m1"
serve.CYCLE_SUMMARY_PATH = root / "rebuild" / "out" / "cycle_summary.json"
serve.AUTOSAVE_PATH = root / "verdicts-autosave.json"
serve.JOURNAL_PATH = root / journal.JOURNAL_NAME
serve.ACCEPTED_PATH = accepted.path_for(serve.AUTOSAVE_PATH)
serve.PORT = int(sys.argv[2])
if len(sys.argv) > 3:
    serve.IDLE_WRITE_SECONDS = float(sys.argv[3])
serve.main()
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def http(port: int, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST" if body is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def start_server(root: Path, idle: float | None = None) -> tuple[subprocess.Popen, int]:
    """Run `serve.main` in a child process with its paths pointed at `root`, as the readings' harness does, its idle write after `idle` seconds when given (`serve.IDLE_WRITE_SECONDS`), and return it once it answers /capabilities."""
    port = free_port()
    child = subprocess.Popen(
        [sys.executable, "-c", SERVER_CHILD, str(root), str(port), *([] if idle is None else [str(idle)])],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while True:
        try:
            if http(port, "/capabilities")[0] == 200:
                return child, port
        except OSError:
            pass
        if child.poll() is not None or time.monotonic() > deadline:
            child.kill()
            raise AssertionError("the review server did not start")
        time.sleep(0.1)


def test_saves_a_killed_server_journaled_and_never_wrote_reach_every_lock_holder_and_the_next_server(
    tmp_path,
):
    """A review server takes saves, journaling each and writing no store, and is killed with SIGKILL before its idle write. A lock holder on a copy of the store and its journal then reads the store the code that wrote every save at once leaves after the same saves, its `exported_at` aside, and a server booted on the lagging store itself catches it up at its boot, serving every save, writing the store a lock holder reads and leaving the journal's bytes as they were."""
    stamp = "2026-07-03T23:31:04Z"
    units = ["u-1", "u-2", "u-3", "u-4"]
    corpus = tmp_path / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": stamp, "human_unit_ids": units}))
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    (tmp_path / "today").mkdir()
    today = VerdictStore(tmp_path / "today" / "verdicts-autosave.json")
    today.path.write_bytes(path.read_bytes())
    today.reload()
    original = path.read_bytes()
    saves = [
        delta(stamp, [verdict("u-3", "reject", at="2026-07-04T00:00:00Z")]),
        delta(
            stamp,
            [verdict("u-1", "either", at="2026-07-04T00:01:00Z")],
            [{"unit": "u-2", "at": "2026-07-04T00:02:00Z"}],
        ),
        delta(stamp, [{**verdict("u-4", at="2026-07-04T00:03:00Z"), "note": "ñ"}]),
    ]
    server, port = start_server(tmp_path)
    try:
        for save in saves:
            assert http(port, "/autosave", save)[0] == 200
            assert today.receive(save)[0] == 200
        assert path.read_bytes() == original
    finally:
        server.send_signal(signal.SIGKILL)
        server.wait(timeout=30)
    journal_path = tmp_path / "verdicts-journal.ndjson"
    assert [event["source"] for event in journal.iter_events(journal_path)] == ["seed", *["autosave"] * 3]
    journal_bytes = journal_path.read_bytes()
    holder = tmp_path / "holder" / path.name
    holder.parent.mkdir()
    holder.write_bytes(path.read_bytes())
    journal.journal_path_for(holder).write_bytes(journal_bytes)
    with landing.locked_store(holder):
        pass
    assert blank_exported_at(holder.read_bytes()) == blank_exported_at(today.path.read_bytes())
    assert path.read_bytes() == original
    server, port = start_server(tmp_path)
    try:
        status, raw = http(port, "/autosave")
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)
    assert status == 200
    assert json.loads(raw)["verdicts"] == [today.records[unit] for unit in sorted(today.records)]
    assert json.loads(raw)["cleared"] == [{"unit": "u-2", "at": "2026-07-04T00:02:00Z"}]
    assert blank_exported_at(path.read_bytes()) == blank_exported_at(today.path.read_bytes())
    assert journal_path.read_bytes() == journal_bytes


def test_a_real_server_writes_its_store_once_after_a_burst_of_saves(tmp_path):
    """The server's POST handler schedules its idle write, here after a second and a half: a burst of saves leaves the file as it was, and once the delay passes with no save the server writes it, holding every save of the burst, the bytes the code that wrote every save at once leaves, its `exported_at` aside, and writes nothing more."""
    stamp = "2026-07-03T23:31:04Z"
    corpus = tmp_path / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": stamp, "human_unit_ids": ["u-1"]}))
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(payload(stamp, [verdict("u-1")]))
    (tmp_path / "today").mkdir()
    today = VerdictStore(tmp_path / "today" / "verdicts-autosave.json")
    today.path.write_bytes(path.read_bytes())
    today.reload()
    original = path.read_bytes()
    saves = [delta(stamp, [verdict(f"u-{index}", at=f"2026-07-04T00:0{index}:00Z")]) for index in range(2, 6)]
    server, port = start_server(tmp_path, idle=1.5)
    try:
        for save in saves:
            assert http(port, "/autosave", save)[0] == 200
            assert today.receive(save)[0] == 200
        assert path.read_bytes() == original
        deadline = time.monotonic() + 30
        while path.read_bytes() == original and time.monotonic() < deadline:
            time.sleep(0.1)
        written = path.stat().st_ino, path.stat().st_mtime_ns
        time.sleep(2)
        assert (path.stat().st_ino, path.stat().st_mtime_ns) == written
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)
    assert blank_exported_at(path.read_bytes()) == blank_exported_at(today.path.read_bytes())


def test_a_failed_idle_write_leaves_the_store_pending_and_a_save_whose_append_and_write_fail_is_put_back(
    tmp_path, monkeypatch
):
    """An idle write that fails leaves the store pending, reloading nothing, since memory holds a save the file lacks, and the next attempt writes it. A save whose journal append fails is written at once, and when that write fails too the save is put back in memory, the pending saves the journal holds kept, the lines a short append wrote before it failed are cut off the journal, and the marker the failed write wrote is removed (#618's decision 82), since the file and the journal are then as they were, so a resend of the same delta is applied and journaled first again."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path, journal_path)
    store.receive(delta(stamp, [verdict("u-1")]))
    store.receive(delta(stamp, [verdict("u-2")]))
    real_write_bytes = store._write_bytes

    def disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_bytes", disk_full)
    assert not write_pending_store(store)
    assert store.pending and set(store.records) == {"u-1", "u-2"}
    assert set(journal.latest_by_unit(on_disk(path)["verdicts"])) == {"u-1"}
    real_write_lines = journal._write_lines

    def short_write(journal_path, lines):
        real_write_lines(journal_path, [next(iter(lines)), b'{"kind": "set", "unit": "u-3"'])
        raise OSError("disk full")

    journal_before = journal_path.read_bytes()
    monkeypatch.setattr(journal, "_write_lines", short_write)
    with pytest.raises(OSError):
        store.receive(delta(stamp, [verdict("u-3")], ["u-1"]))
    assert set(store.records) == {"u-1", "u-2"} and store.pending
    assert journal_path.read_bytes() == journal_before
    monkeypatch.undo()
    assert write_pending_store(store) and not store.pending
    assert set(journal.latest_by_unit(on_disk(path)["verdicts"])) == {"u-1", "u-2"}
    assert not journal.unjournaled_marker_for(path).exists()
    status, _ = store.receive(delta(stamp, [verdict("u-3")], ["u-1"]))
    assert status == 200 and set(store.records) == {"u-2", "u-3"} and store.pending
    assert set(journal.replay(journal_path)[1]) == {"u-2", "u-3"}


def test_a_delta_seeds_a_journal_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    path.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    store = VerdictStore(path, journal_path)
    store.receive(delta(stamp, [verdict("u-3")], ["u-2"]))
    events = list(journal.iter_events(journal_path))
    assert [event["source"] for event in events] == ["seed", "autosave"]
    assert events[0]["sets"] == 1
    _, records = journal.replay(journal_path)
    assert set(records) == {"u-1", "u-3"}


def test_a_delta_seeds_a_journal_with_the_tombstones_of_a_store_that_holds_no_records(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    stamp = "2026-07-03T23:31:04Z"
    document = json.loads(payload(stamp))
    document["cleared"] = [{"unit": "u-2", "at": "t5"}]
    path.write_text(json.dumps(document))
    VerdictStore(path, journal_path).receive(delta(stamp, [verdict("u-1")]))
    assert [event["source"] for event in journal.iter_events(journal_path)] == ["seed", "autosave"]
    assert journal.replay_store(journal_path) == (stamp, {"u-1": verdict("u-1")}, {"u-2": "t5"})


def test_changes_since_hands_back_only_what_moved_after_the_token(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(delta(stamp, [verdict("u-1"), verdict("u-2")]))
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


def held_at_parse(monkeypatch, store) -> list[tuple]:
    """Record, at each parse a reload makes, the records, lines, tombstones and override section the store holds then and how many signatures it has digested since this was called."""
    seen: list[tuple] = []
    digested: list[object] = []
    real_parse, real_digest = verdict_store.read_store_document, verdict_store._signature_digest
    monkeypatch.setattr(
        verdict_store, "_signature_digest", lambda record: digested.append(record) or real_digest(record)
    )

    def parse(raw, name):
        seen.append(
            (
                store.records,
                store._lines,
                store.cleared,
                store.overrides.records,
                store.overrides.cleared,
                len(digested),
            )
        )
        return real_parse(raw, name)

    monkeypatch.setattr(verdict_store, "read_store_document", parse)
    return seen


def test_an_external_rewrite_onto_another_stamp_is_picked_up_and_invalidates_tokens(tmp_path, monkeypatch):
    """A reload onto another stamp frees the records it holds before it parses the file, with no diff, since it invalidates every token."""
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    store.receive(delta("2026-07-03T06:13:47Z", [verdict("u-1")]))
    token = store.token
    os.utime(path, ns=(1, 1))
    path.write_bytes(payload("2026-07-03T23:31:04Z", [verdict("u-1"), verdict("u-2")]))
    os.utime(path, ns=(2, 2))
    seen = held_at_parse(monkeypatch, store)
    assert store.refresh_if_changed()
    assert seen == [({}, {}, {}, {}, {}, 0)]
    assert store.stamp == "2026-07-03T23:31:04Z"
    assert set(store.records) == {"u-1", "u-2"}
    assert store.changes_since(token) is None
    assert not store.refresh_if_changed()


def test_a_same_stamp_external_rewrite_keeps_the_token_and_hands_back_only_what_moved(tmp_path, monkeypatch):
    """A reload onto the same stamp beside the same list reduces the generation it holds to a digest a record, own and override apart, frees the rest before it parses the file, and reports each unit whose own record or override record or tombstone changed, came or went."""
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    path.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2"), verdict("u-3")]))
    store = VerdictStore(path)
    token = store.token
    os.utime(path, ns=(1, 1))
    path.write_bytes(
        payload(stamp, [verdict("u-1"), verdict("u-2", "reject", at="2026-07-03T01:00:00Z"), verdict("u-4")])
    )
    os.utime(path, ns=(2, 2))
    seen = held_at_parse(monkeypatch, store)
    assert store.refresh_if_changed()
    assert seen == [({}, {}, {}, {}, {}, 3)]
    moved = changes(store, token)
    assert [record["unit"] for record in moved["sets"]] == ["u-2", "u-4"]
    assert moved["clears"] == ["u-3"]
    assert moved["token"] == store.token

    path, store = listed_store(tmp_path / "listed")
    document = on_disk(path)
    document["verdicts"] = [verdict("u-1"), verdict("u-2"), verdict("u-3")]
    document["overrides"] = {
        "verdicts": [verdict(ACCEPT_A, "reject", at=AFTER), verdict(ACCEPT_C, "neither", at=AFTER)],
        "cleared": [{"unit": ACCEPT_B, "at": AFTER}],
    }
    path.write_text(json.dumps(document))
    assert store.refresh_if_changed()
    token = store.token
    document["verdicts"] = [verdict("u-1"), verdict("u-2", "reject", at=AFTER), verdict("u-4")]
    document["overrides"] = {
        "verdicts": [verdict(ACCEPT_A, "reject", at=AFTER), verdict(ACCEPT_B, "approve", at=AFTER)],
        "cleared": [],
    }
    path.write_text(json.dumps(document))
    seen = held_at_parse(monkeypatch, store)
    assert store.refresh_if_changed()
    assert seen == [({}, {}, {}, {}, {}, 5)]
    moved = changes(store, token)
    assert [record["unit"] for record in moved["sets"]] == [ACCEPT_B, ACCEPT_C, "u-2", "u-4"]
    assert moved["sets"][1] == flagged(ACCEPT_C, "identical")
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
    store.receive(delta(stamp, [verdict("u-1")]))
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


class StreamedReply:
    """A request handler's `write` and `flush` as `stream_full_store` uses them: it keeps each chunk written and calls `between` with how many it holds at each flush."""

    def __init__(self, between=None) -> None:
        self.chunks: list[bytes] = []
        self.between = between

    def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    async def flush(self) -> None:
        if self.between is not None:
            self.between(len(self.chunks))


def test_the_full_get_streams_the_bytes_payload_bytes_gives_and_a_save_writes_them_too(tmp_path, monkeypatch):
    """The full GET is written a chunk at a time, the head with its token first, and the chunks are `payload_bytes(token=True, within=...)` byte for byte, the override section listed with the store's own; the store file a save writes is `payload_bytes()` byte for byte, written the same way."""
    monkeypatch.setattr(journal, "now_stamp", lambda: "2026-11-06T10:00:00Z")
    monkeypatch.setattr(verdict_store, "_CHUNK_CHARS", 64)
    path, store = listed_store(tmp_path)
    sets = [verdict(f"u-{index}", at=AFTER) for index in range(2, 9)] + [
        verdict(ACCEPT_A, "reject", at=AFTER)
    ]
    assert store.receive(delta(NEW, sets, [ACCEPT_B, "u-9"]))[0] == 200
    assert path.read_bytes() == store.payload_bytes()
    within = frozenset({ACCEPT_A, ACCEPT_B, ACCEPT_C, "u-2"})
    reply = StreamedReply()
    asyncio.run(stream_full_store(reply, store, within))
    assert len(reply.chunks) > 2 and json.loads(reply.chunks[0] + b"]}")["token"] == store.token
    assert b"".join(reply.chunks) == store.payload_bytes(token=True, within=within)


def test_a_save_between_two_chunks_of_the_full_get_leaves_a_body_that_parses_and_a_token_that_syncs(tmp_path):
    """A save can land while the full GET waits on a flush. The body still parses, the record a clear removed is left out, and the token the head carried hands the tab the save on its next sync."""
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    store.receive(delta(NEW, [verdict(f"u-{index}") for index in range(1, 6)]))

    def save(written: int) -> None:
        if written == 1:
            assert (
                store.receive(delta(NEW, [verdict("u-0"), verdict("u-3", "reject", at=AFTER)], ["u-2"]))[0]
                == 200
            )

    reply = StreamedReply(save)
    asyncio.run(stream_full_store(reply, store, None))
    body = json.loads(b"".join(reply.chunks))
    assert [record["unit"] for record in body["verdicts"]] == ["u-1", "u-3", "u-4", "u-5"]
    assert body["verdicts"][1]["verdict"] == "reject" and [entry["unit"] for entry in body["cleared"]] == [
        "u-2"
    ]
    synced = changes(store, body["token"])
    assert [record["unit"] for record in synced["sets"]] == ["u-0", "u-3"] and synced["clears"] == ["u-2"]


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
    assert store.receive(delta(NEW, records))[0] == 200
    return path, store


def test_parse_delta_payload_accepts_both_clear_shapes():
    parsed = parse_delta_payload(delta(NEW, clears=["u-1", {"unit": "u-2", "base_at": "t1", "at": "t2"}]))
    assert parsed is not None
    assert parsed["clears"] == [
        {"unit": "u-1", "base_at": None, "at": None},
        {"unit": "u-2", "base_at": "t1", "at": "t2"},
    ]
    assert parsed["replay"] is False


def test_well_formed_gives_each_shared_sample_its_recorded_answer():
    """`verdicts.test.js` checks the review app's strict Import against the same samples, so the app's JavaScript mirror of `well_formed` cannot drift from it."""
    samples = json.loads(VERDICT_RECORD_SAMPLES.read_text(encoding="utf-8"))
    assert {sample["well_formed"] for sample in samples} == {True, False}
    for sample in samples:
        assert well_formed(sample["record"]) is sample["well_formed"], sample["record"]


def test_parse_delta_payload_gives_each_shared_set_sample_its_recorded_answer():
    """`verdicts.test.js` checks the review app's `wellFormedSet`, by which the outbox replay leaves a set this rule refuses out of its body, against the same samples, so the app's JavaScript mirror of the rule cannot drift from it."""
    samples = json.loads(DELTA_SET_SAMPLES.read_text(encoding="utf-8"))
    assert {sample["well_formed"] for sample in samples} == {True, False}
    for sample in samples:
        raw = delta(NEW, [sample["set"]])
        if sample["well_formed"]:
            parsed = parse_delta_payload(raw)
            assert parsed is not None and parsed["sets"] == [sample["set"]], sample["set"]
        else:
            with pytest.raises(MalformedSet):
                parse_delta_payload(raw)


@pytest.mark.parametrize("verdict_field", [{}, {"verdict": 5}], ids=["no-verdict", "numeric-verdict"])
def test_a_set_whose_record_the_carry_refuses_is_refused_before_any_write(tmp_path, verdict_field):
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path, store = new_store(tmp_path, [verdict("u-0")], journal_path)
    before = path.read_bytes(), journal_path.read_bytes(), sorted(tmp_path.rglob("*"))
    status, body = store.receive(delta(NEW, [{"unit": "u-1", "at": "2026-07-10T00:00:00Z", **verdict_field}]))
    assert status == 400 and body["ok"] is False
    assert body["error"].startswith(f"the {DELTA_FORMAT} document holds a malformed set on u-1:")
    assert (path.read_bytes(), journal_path.read_bytes(), sorted(tmp_path.rglob("*"))) == before
    assert set(store.records) == {"u-0"}
    assert store.receive(delta(NEW, [verdict("u-1", at="2026-07-10T00:00:00Z")]))[0] == 200
    assert write_pending_store(store)
    assert on_disk(path)["verdicts"] == [verdict("u-0"), verdict("u-1", at="2026-07-10T00:00:00Z")]
    assert set(journal.replay(journal_path)[1]) == {"u-0", "u-1"}


@pytest.mark.parametrize(
    ("malformed", "named"),
    [
        ({"verdict": "approve"}, "at index 1 of its `sets` list"),
        ({"unit": 5, "verdict": "approve"}, "at index 1 of its `sets` list"),
        ({**verdict("u-1"), "base_at": 1}, "on u-1"),
    ],
    ids=["no-unit", "numeric-unit", "numeric-base-at"],
)
def test_a_delta_holding_a_malformed_set_is_refused_naming_the_first_such_set(tmp_path, malformed, named):
    """The 400 names the first malformed set by its unit, or by its index when it names no unit or a unit that is not a string, rather than calling the delta something other than an ams-review-verdicts-delta/1 document."""
    store = VerdictStore(tmp_path / "verdicts-autosave.json")
    status, body = store.receive(delta(NEW, [verdict("u-0"), malformed, {"unit": "u-2"}]))
    assert status == 400 and body["ok"] is False
    assert body["error"].startswith(f"the {DELTA_FORMAT} document holds a malformed set {named}:")
    assert not (tmp_path / "verdicts-autosave.json").exists()


@pytest.mark.parametrize(
    ("malformed", "named"),
    [
        ({"at": "t"}, "at index 1 of its `clears` list"),
        ({"unit": 5}, "at index 1 of its `clears` list"),
        ({"unit": "u-1", "at": 5}, "on u-1"),
    ],
    ids=["no-unit", "numeric-unit", "numeric-at"],
)
def test_a_delta_holding_a_malformed_clear_is_refused_naming_the_first_such_clear(tmp_path, malformed, named):
    """The 400 names the first malformed clear by its unit, or by its index when it names no unit or a unit that is not a string, as it names a malformed set."""
    store = VerdictStore(tmp_path / "verdicts-autosave.json")
    status, body = store.receive(delta(NEW, [verdict("u-0")], ["u-0", malformed, {"unit": "u-2", "at": 6}]))
    assert status == 400 and body["ok"] is False
    assert body["error"].startswith(f"the {DELTA_FORMAT} document holds a malformed clear {named}:")
    assert not (tmp_path / "verdicts-autosave.json").exists()


@pytest.mark.parametrize(
    ("record", "named"),
    [
        ({**verdict("u-B"), "at": 5}, "on u-B"),
        ({"verdict": "approve", "at": "t1"}, "at index 2 of its `verdicts` list"),
    ],
    ids=["numeric-at", "no-unit"],
)
@pytest.mark.parametrize(
    ("stamp", "replay", "sets", "clears"),
    [
        (OLD, None, [{**verdict("u-B", "reject", at="2026-07-11T00:00:00Z"), "base_at": "x"}], []),
        (OLD, None, [], [{"unit": "u-B", "base_at": "x", "at": "2026-07-12T00:00:00Z"}]),
        (NEW, True, [{**verdict("u-B", "reject", at="2026-07-11T00:00:00Z"), "base_at": "x"}], []),
        (NEW, True, [], [{"unit": "u-B", "base_at": "x", "at": "2026-07-12T00:00:00Z"}]),
    ],
    ids=["carried-set", "carried-clear", "replayed-set", "replayed-clear"],
)
def test_a_store_holding_a_record_the_carry_refuses_is_read_as_unreadable_and_refuses_every_save(
    tmp_path, record, named, stamp, replay, sets, clears
):
    """A store file holding a record `well_formed` refuses serves no verdict, as an unreadable one does, and every save, which would otherwise weigh its `at` against the record's or write over the file, gets a 422 naming the file and the record, by its unit or by its index when it names none, and writes nothing, until the file is replaced. A GET's 404 names them under the same reason until then, and says there is no autosave once no file is there."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path.write_bytes(payload(NEW, [verdict("u-A"), verdict("u-C"), record]))
    before = path.read_bytes()
    store = VerdictStore(path, journal_path)
    assert store.stamp is None and store.records == {}
    status, body = store.receive(delta(stamp, sets, clears, replay), served("u-A", "u-B", "u-C"))
    assert status == 422 and body["ok"] is False and body["reason"] == "store-malformed"
    assert body["error"].startswith(f"the store {path} holds a verdict record {named} that is not an object")
    assert missing_autosave_reply(store) == {"ok": False, "reason": "store-malformed", "error": body["error"]}
    assert path.read_bytes() == before
    assert sorted(tmp_path.rglob("*")) == [path]
    path.write_bytes(payload(NEW, [verdict("u-A"), verdict("u-B"), verdict("u-C")]))
    assert store.receive(delta(stamp, sets, clears, replay), served("u-A", "u-B", "u-C"))[0] == 200
    path.unlink()
    store.refresh_if_changed()
    assert missing_autosave_reply(store) == {"ok": False, "error": "no autosave yet"}


def test_a_reload_onto_two_records_on_one_unit_one_with_an_at_that_is_not_a_string_refuses_the_file(tmp_path):
    """Comparing the two records' `at`s would raise TypeError, so a reload of a file another writer replaced with such a pair refuses it before it compares them, as the land does (`verdict_store.read_store_document`): the store serves no verdict, a save gets the 422 naming the file and the record, and the file is left byte for byte as it was."""
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(payload(NEW, [verdict("u-A")]))
    store = VerdictStore(path, tmp_path / "verdicts-journal.ndjson")
    assert store.stamp == NEW
    path.write_bytes(payload(NEW, [verdict("u-A"), {**verdict("u-A", "reject"), "at": 5}]))
    before = path.read_bytes()
    status, body = store.receive(delta(NEW, [verdict("u-B")]))
    assert status == 422 and body["reason"] == "store-malformed"
    assert body["error"].startswith(f"the store {path} holds a verdict record on u-A that is not an object")
    assert store.stamp is None and store.records == {}
    assert path.read_bytes() == before and sorted(tmp_path.rglob("*")) == [path]


def test_a_request_while_a_dead_lands_recovery_refuses_gets_the_refusal_until_the_file_is_replaced(tmp_path):
    """Before it answers a request the server finishes a land whose holder died (`orphaned_land_reply`). While that land's result holds two records on one unit, one with an `at` that is not a string, the recovery refuses (`landing.LandUnrecoverable`), and the request gets the status its handler names with the refusal under `reason: store-malformed`, which the app shows, instead of a 500 from a traceback, and every file is left byte for byte as it was. Once the result is replaced the next request finishes the land and goes on."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path.write_bytes(payload(NEW, [verdict("u-A")]))
    run_dir = tmp_path / "var" / "cycle" / "run"
    run_dir.mkdir(parents=True)
    result = run_dir / landing.LANDING_NAME
    result.write_bytes(payload(NEW, [verdict("u-A"), {**verdict("u-A", "reject"), "at": 5}]))
    intent = landing.intent_path_for(path)
    intent.write_text(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "landing": str(result),
                "staged": None,
                "live": None,
                "old_stamp": NEW,
                "new_stamp": NEW,
                "stash": None,
                "journal": str(journal_path),
                "journal_inode": None,
                "journal_length": None,
            }
        )
    )
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    reply = orphaned_land_reply(path, 404)
    assert reply is not None
    status, body = reply
    assert status == 404 and body["ok"] is False and body["reason"] == "store-malformed"
    assert body["error"].startswith(
        f"the result {result} holds a verdict record on u-A that is not an object"
    )
    assert orphaned_land_reply(path, 422) == (422, body)
    assert {item: item.read_bytes() for item in before} == before
    result.write_bytes(payload(NEW, [verdict("u-B")]))
    assert orphaned_land_reply(path, 404) is None
    assert not intent.exists() and [
        record["unit"] for record in json.loads(path.read_text())["verdicts"]
    ] == ["u-B"]


def test_a_stale_stamp_delta_on_a_served_unit_is_carried_and_journaled_as_carried(tmp_path):
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path, store = new_store(tmp_path, [verdict("u-1", at="t1")], journal_path)
    status, body = store.receive(
        delta(OLD, [{**verdict("u-2", "reject", at="t5"), "base_at": None}]), served("u-1", "u-2")
    )
    assert status == 200
    assert body["carried"] == ["u-2"] and body["orphaned"] == [] and body["conflicts"] == []
    assert body["corpus_stamp"] == NEW
    assert write_pending_store(store)
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
    assert body["dropped"] == ["u-2"]
    assert store.records["u-1"]["note"] == "ok"
    assert "u-2" not in store.records


def test_a_stale_skip_clears_the_record_it_replaced_so_the_unit_is_asked_again(tmp_path):
    """A tab loaded on the old corpus skips a unit the land left holding the verdict the skip replaced. The skip is dropped, as the carry drops every skip, but it clears that record under the same tests as a set, so the unit comes back blank rather than holding the superseded verdict; the reply lists it under `dropped`, and the clear is journaled. A skip older than a record the tab never saw is a conflict instead."""
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path, store = new_store(tmp_path, [verdict("u-1", at="t1"), verdict("u-2", at="t5")], journal_path)
    status, body = store.receive(
        delta(
            OLD,
            [
                {**verdict("u-1", "skip", at="t3"), "base_at": "t1"},
                {**verdict("u-2", "skip", at="t3"), "base_at": "t1"},
            ],
        ),
        served("u-1", "u-2"),
    )
    assert status == 200
    assert body["dropped"] == ["u-1"] and body["carried"] == ["u-1"]
    assert body["conflicts"] == [{"unit": "u-2", "server": verdict("u-2", at="t5")}]
    assert "u-1" not in store.records and store.cleared == {"u-1": "t3"}
    assert write_pending_store(store)
    assert "u-1" not in {record["unit"] for record in on_disk(path)["verdicts"]}
    assert "u-1" not in journal.replay(journal_path)[1]
    status, body = store.receive(
        delta(OLD, [{**verdict("u-1", at="t2"), "base_at": None}]), served("u-1", "u-2")
    )
    assert body["conflicts"] == [{"unit": "u-1", "server": None}] and "u-1" not in store.records


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


def test_a_delta_on_the_served_stamp_carries_a_store_left_on_another_stamp_onto_it(tmp_path):
    """A first pass over a deleted corpus builds it in place and lands no store, and a restore under --yes can put back a store stamped for an earlier corpus, so the store can sit on another stamp than the served corpus. The first save from a tab on the served corpus carries the store onto it by unit id, keeping its verdicts and tombstones on the served corpus's human units, less its skips, each of which leaves a tombstone at its `at` so that a skipped accepted unit stays blank, keeps the old file as the stash, and then applies the save and journals the move, the kept tombstones included, so the tab keeps saving and no verdict is left behind in the stash. A store with saves journaled and not yet written writes them first, so the stash holds them too."""
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(
        json.dumps(
            {
                "format": EXPORT_FORMAT,
                "manifest_generated_at": NEW,
                "exported_at": NEW,
                "verdicts": [
                    verdict("u-1", at="t1"),
                    verdict("u-2", at="t1"),
                    verdict("u-3", "skip", at="t1"),
                ],
                "cleared": [{"unit": "u-4", "at": "t2"}, {"unit": "u-gone", "at": "t2"}],
            }
        ).encode()
    )
    store = VerdictStore(path, journal_path)
    assert store.receive(delta(NEW, [verdict("u-2", "reject", at="t3")]))[0] == 200
    assert store.pending and on_disk(path)["verdicts"][1] == verdict("u-2", at="t1")
    old_token = store.token
    newer = "2026-07-05T00:00:00Z"
    status, body = store.receive(
        delta(newer, [verdict("u-9", "reject", at="t9")]), served("u-1", "u-3", "u-4", "u-9", stamp=newer)
    )
    assert status == 200
    assert body["corpus_stamp"] == newer and body["token"] == store.token != old_token
    assert on_disk(stash_path_for(path, NEW))["verdicts"][1] == verdict("u-2", "reject", at="t3")
    written = on_disk(path)
    assert written["manifest_generated_at"] == newer
    assert [record["unit"] for record in written["verdicts"]] == ["u-1", "u-9"]
    assert written["cleared"] == [{"unit": "u-3", "at": "t1"}, {"unit": "u-4", "at": "t2"}]
    event = list(journal.iter_events(journal_path))[-1]
    assert event["stamp"] == newer and event["stashed"] == stash_path_for(path, NEW).name
    _, records, cleared = journal.replay_store(journal_path)
    assert set(records) == {"u-1", "u-9"} and cleared == {"u-3": "t1", "u-4": "t2"}
    status, body = store.receive(
        delta(OLD, [verdict("u-9", at="t95")]), served("u-1", "u-3", "u-4", "u-9", stamp=newer)
    )
    assert status == 200 and body["carried"] == ["u-9"]


def test_a_save_writes_the_store_and_the_unjournaled_marker_only_when_its_journal_append_fails(
    tmp_path, monkeypatch
):
    """A save that carries the store onto the served stamp writes the store at once, writing the marker that says the journal may lack a store write before it and removing it once the journal append succeeds. A save applied in place whose append succeeds writes no marker and no store, and is left pending. A save whose append fails is never refused: the store writes the marker and then the whole store at once, its pending saves with it, and leaves the marker, and a later save leaves it too, writing the store at once while it is there, because only a land's base puts the failed save in the journal (#618's decision 82)."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path.write_bytes(payload(OLD, [verdict("u-1")]))
    store = VerdictStore(path, journal_path)
    marker = journal.unjournaled_marker_for(path)
    marked_at_write: list[bool] = []
    real_write_bytes = store._write_bytes

    def spy(raw):
        marked_at_write.append(marker.exists())
        real_write_bytes(raw)

    monkeypatch.setattr(store, "_write_bytes", spy)
    corpus = served("u-1", "u-2", "u-3", "u-4")
    assert store.receive(delta(NEW, [verdict("u-2")]), corpus)[0] == 200
    assert next(journal.iter_events(journal_path))["stashed"] == stash_path_for(path, OLD).name
    assert marked_at_write == [True] and not marker.exists()
    moved = path.read_bytes()
    assert store.receive(delta(NEW, [verdict("u-3")]), corpus)[0] == 200
    assert marked_at_write == [True] and not marker.exists()
    assert store.pending and path.read_bytes() == moved

    def disk_full(journal_path, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr(journal, "record_delta", disk_full)
        status, body = store.receive(delta(NEW, [verdict("u-4")]), corpus)
    assert status == 200 and body["journal_error"] == "disk full" and marker.exists()
    assert marked_at_write == [True, True] and not store.pending
    assert set(journal.latest_by_unit(on_disk(path)["verdicts"])) == {"u-1", "u-2", "u-3", "u-4"}
    assert store.receive(delta(NEW, [verdict("u-1", "reject", at="t9")]), corpus)[0] == 200
    assert marker.exists() and marked_at_write == [True, True, True] and not store.pending
    assert "u-4" in store.records and "u-4" not in journal.replay(journal_path)[1]


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


CLOSED_AT = "2026-11-05T18:00:00Z"
AFTER = "2026-11-06T09:00:00Z"
ACCEPT_A, ACCEPT_B, ACCEPT_C = "u-1AAAAAAAAAA", "u-1BBBBBBBBBB", "u-1CCCCCCCCCC"


def write_list(store_path, verdicts, closed_at=CLOSED_AT) -> Path:
    listed = accepted.path_for(store_path)
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_bytes(accepted.render(closed_at, verdicts))
    return listed


def listed_store(tmp_path, journal_path=None):
    """A store stamped NEW, holding nothing, beside a list that accepts ACCEPT_A as approve, ACCEPT_B as either and ACCEPT_C as identical; its first write records the list in its `accepted` field."""
    path = tmp_path / "verdicts-autosave.json"
    write_list(path, {ACCEPT_A: "approve", ACCEPT_B: "either", ACCEPT_C: "identical"})
    store = VerdictStore(path, journal_path)
    assert store.receive(delta(NEW))[0] == 200
    assert on_disk(path)["accepted"] == store.accepted.field() is not None
    return path, store


def flagged(unit, verdict_kind):
    return {"unit": unit, "verdict": verdict_kind, "note": "", "at": CLOSED_AT, "accepted": True}


def test_the_full_get_lists_the_served_accepted_units_and_the_file_names_the_list(tmp_path):
    """The shared sample's store and list: the full GET's `accepted` field lists, by verdict, the listed units of the served corpus's human units that hold no record or tombstone, as the sample's `field` says, and the server reports each as the record the review app makes of it; the store file names the list and lists no unit."""
    sample = json.loads(ACCEPTED_SAMPLES.read_text(encoding="utf-8"))["view"]
    path = tmp_path / "verdicts-autosave.json"
    path.write_text(json.dumps(sample["store"]))
    listed = accepted.path_for(path)
    listed.parent.mkdir()
    listed.write_bytes(sample["list"].encode("ascii"))
    store = VerdictStore(path)
    get = json.loads(store.payload_bytes(token=True, within=frozenset(sample["human_unit_ids"])))
    assert get["token"] == store.token and get["accepted"] == sample["field"]
    reported = [
        store.reported(unit) for verdict_kind in accepted.MARKS for unit in sample["field"][verdict_kind]
    ]
    assert reported == sample["records"]
    file = json.loads(store.payload_bytes())
    assert file["accepted"] == sample["store"]["accepted"] and "token" not in file


def test_a_set_clear_and_return_to_the_list_on_accepted_units_are_stored_journaled_and_reported(tmp_path):
    """A set on an accepted unit is stored as an override record, a clear of one with no override leaves an override tombstone, which blanks it, both in the store's override section and never among its own records and tombstones, and a set equal to a unit's accepted record dated `closed_at`, a tab's undo back to it, removes its override and stores nothing, so it reads as the list names it; the journal records that as a removal with no tombstone, a sync reports each unit as it reads, the accepted ones flagged, and every reply names the list. The accepted mark picked again with an empty note and the tab's own time is stored as an override, so another tab holding an older override adopts it."""
    journal_path = tmp_path / "verdicts-journal.ndjson"
    path, store = listed_store(tmp_path, journal_path)
    token = store.token
    status, body = store.receive(
        delta(NEW, [{**verdict(ACCEPT_A, "reject", at=AFTER), "base_at": CLOSED_AT}])
    )
    assert status == 200 and body["accepted"] == {"sha256": store.accepted.sha256, "closed_at": CLOSED_AT}
    assert store.overrides.records[ACCEPT_A]["verdict"] == "reject"
    store.receive(delta(NEW, clears=[{"unit": ACCEPT_B, "base_at": CLOSED_AT, "at": AFTER}]))
    assert store.overrides.cleared == {ACCEPT_B: AFTER} and ACCEPT_B not in store.view
    assert write_pending_store(store)
    written = on_disk(path)
    assert (written["verdicts"], "cleared" in written) == ([], False)
    assert written["overrides"] == {
        "verdicts": [store.overrides.records[ACCEPT_A]],
        "cleared": [{"unit": ACCEPT_B, "at": AFTER}],
    }
    synced = changes(store, token)
    assert (synced["sets"], synced["clears"]) == ([store.overrides.records[ACCEPT_A]], [ACCEPT_B])
    store.receive(delta(NEW, [verdict(ACCEPT_A, at=CLOSED_AT), verdict(ACCEPT_B, "either", at=CLOSED_AT)]))
    assert store.records == {} and store.cleared == {} and not store.overrides
    assert write_pending_store(store)
    assert (
        on_disk(path)["verdicts"] == []
        and "cleared" not in on_disk(path)
        and "overrides" not in on_disk(path)
    )
    synced = changes(store, token)
    assert (
        synced["sets"] == [flagged(ACCEPT_A, "approve"), flagged(ACCEPT_B, "either")]
        and synced["clears"] == []
    )
    assert journal.replay_with_list(journal_path) == (NEW, {}, {}, store.accepted.field())
    later = "2026-11-06T12:05:00.000Z"
    store.receive(delta(NEW, [verdict(ACCEPT_A, "reject", at=AFTER)]))
    token = store.token
    store.receive(delta(NEW, [verdict(ACCEPT_A, at=later)]))
    assert store.overrides.records[ACCEPT_A] == verdict(ACCEPT_A, at=later)
    assert changes(store, token)["sets"] == [verdict(ACCEPT_A, at=later)]


def test_a_stale_save_that_differs_from_an_accepted_mark_is_answered_with_the_accepted_record(tmp_path):
    """A save made before the close-out that never reached the store meets the accepted record, dated the close-out, under rule 2: a replay that differs from the mark is a conflict, answered with the accepted record flagged, and kept in the orphan document, while one made after the close-out applies. A stale skip on an accepted unit leaves a tombstone at the skip's `at`, so the unit is asked again."""
    path, store = listed_store(tmp_path)
    before = "2026-11-04T12:00:00.000Z"
    status, body = store.receive(
        delta(
            NEW, [{**verdict(ACCEPT_A, "reject", at=before), "base_at": "2026-11-01T00:00:00Z"}], replay=True
        )
    )
    assert status == 200 and body["conflicts"] == [{"unit": ACCEPT_A, "server": flagged(ACCEPT_A, "approve")}]
    assert ACCEPT_A not in store.overrides
    assert [record["reason"] for record in orphan_records(path, NEW)] == ["conflict"]
    store.receive(delta(NEW, [{**verdict(ACCEPT_A, "reject", at=AFTER), "base_at": None}], replay=True))
    assert store.overrides.records[ACCEPT_A]["verdict"] == "reject"
    status, body = store.receive(
        delta(OLD, [{**verdict(ACCEPT_C, "skip", at=AFTER), "base_at": CLOSED_AT}]),
        served(ACCEPT_A, ACCEPT_C),
    )
    assert body["dropped"] == [ACCEPT_C] and store.overrides.cleared == {ACCEPT_C: AFTER}


def test_a_move_onto_the_served_corpus_keeps_the_override_section_whole_on_the_accepted_units_it_lacks(
    tmp_path,
):
    """The store holds a reject override on one accepted unit, a tombstone on a second and a skip on a third, and nothing on a fourth. A tab's save on a served corpus that lacks all four moves the store onto it (rule 3): the move drops the store's own records off the corpus but keeps its override section whole, the skip there becoming a tombstone at its `at`, so when a later move brings the windows back before the next close-out the first reads as the reject, the second and third blank, and the fourth as its accepted record."""
    path = tmp_path / "verdicts-autosave.json"
    write_list(
        path, {ACCEPT_A: "approve", ACCEPT_B: "either", ACCEPT_C: "identical", "u-1DDDDDDDDDD": "approve"}
    )
    store = VerdictStore(path)
    store.receive(delta(NEW, [verdict(ACCEPT_A, "reject", at=AFTER), verdict(ACCEPT_C, "skip", at=AFTER)]))
    cleared_at = "2026-11-06T12:10:00.000Z"
    store.receive(delta(NEW, clears=[{"unit": ACCEPT_B, "base_at": CLOSED_AT, "at": cleared_at}]))
    newer = "2026-11-07T00:00:00Z"
    status, _body = store.receive(delta(newer, [verdict("u-9", at=AFTER)]), served("u-9", stamp=newer))
    assert status == 200
    written = on_disk(path)
    assert written["manifest_generated_at"] == newer and [
        record["unit"] for record in written["verdicts"]
    ] == ["u-9"]
    assert journal.parse_cleared(written) == {}
    assert written["overrides"] == {
        "verdicts": [verdict(ACCEPT_A, "reject", at=AFTER)],
        "cleared": [{"unit": ACCEPT_B, "at": cleared_at}, {"unit": ACCEPT_C, "at": AFTER}],
    }
    back = "2026-11-08T00:00:00Z"
    status, _body = store.receive(
        delta(back, [verdict("u-9", at=back)]),
        served("u-9", ACCEPT_A, ACCEPT_B, ACCEPT_C, "u-1DDDDDDDDDD", stamp=back),
    )
    assert status == 200
    kinds = {
        unit: (store.view.verdict_of(unit) or {}).get("verdict") for unit in (ACCEPT_A, ACCEPT_B, ACCEPT_C)
    }
    assert kinds == {ACCEPT_A: "reject", ACCEPT_B: None, ACCEPT_C: None}
    assert store.reported("u-1DDDDDDDDDD") == flagged("u-1DDDDDDDDDD", "approve")


def test_a_same_stamp_rewrite_that_moves_only_a_tombstone_on_an_accepted_unit_is_reported(tmp_path):
    """Another writer, a land among them, can add or remove an override tombstone on an accepted unit, which blanks it or returns it to its accepted record. The same-stamp reload keeps the token and reports the unit as it now reads, so a tab's sync and a new tab's `accepted` field both follow it."""
    path, store = listed_store(tmp_path)
    token = store.token
    document = on_disk(path)
    document["overrides"] = {"verdicts": [], "cleared": [{"unit": ACCEPT_A, "at": AFTER}]}
    path.write_text(json.dumps(document))
    assert store.refresh_if_changed()
    synced = changes(store, token)
    assert (synced["sets"], synced["clears"]) == ([], [ACCEPT_A])
    assert json.loads(store.payload_bytes(token=True, within=None))["accepted"]["approve"] == []
    token = store.token
    del document["overrides"]
    path.write_text(json.dumps(document))
    assert store.refresh_if_changed()
    synced = changes(store, token)
    assert (synced["sets"], synced["clears"]) == ([flagged(ACCEPT_A, "approve")], [])
    assert json.loads(store.payload_bytes(token=True, within=None))["accepted"]["approve"] == [ACCEPT_A]


def test_a_new_list_beside_the_store_invalidates_every_token(tmp_path):
    """A close-out or a re-key changes the answer for every unit the list names, more than the change log holds, so a reload beside another list sends every tab the whole store and the new field, even on the same stamp."""
    path, store = listed_store(tmp_path)
    token = store.token
    write_list(path, {ACCEPT_A: "approve"}, closed_at="2026-11-07T00:00:00Z")
    document = on_disk(path)
    document["accepted"] = accepted.read(accepted.path_for(path)).field()
    path.write_text(json.dumps(document))
    assert store.refresh_if_changed()
    assert store.stamp == NEW and store.refusal is None and store.changes_since(token) is None
    assert json.loads(store.payload_bytes(token=True, within=None))["accepted"]["approve"] == [ACCEPT_A]


@pytest.mark.parametrize("how", ["malformed", "mismatch"])
def test_a_list_the_store_cannot_read_or_pair_with_refuses_every_save_and_get(tmp_path, how):
    """A list that is not in the format (`accepted-malformed`), or one the store was not folded against (`accepted-mismatch`), as a checkout, stash or revert leaves, serves no verdict and refuses every save under its reason, writing nothing, until the two pair again."""
    path, store = listed_store(tmp_path)
    listed = accepted.path_for(path)
    if how == "malformed":
        listed.write_bytes(listed.read_bytes() + b"<<<<<<< HEAD\n")
    else:
        write_list(path, {ACCEPT_A: "approve"}, closed_at="2026-11-07T00:00:00Z")
    before = path.read_bytes()
    status, body = store.receive(delta(NEW, [verdict(ACCEPT_A, "reject", at=AFTER)]))
    reason = f"accepted-{how}"
    assert status == 422 and body["reason"] == reason and str(listed) in body["error"]
    assert store.stamp is None and missing_autosave_reply(store)["reason"] == reason
    assert path.read_bytes() == before


def test_the_server_writes_an_empty_store_naming_the_list_where_there_is_no_store(tmp_path):
    """A fresh clone, a worktree or a machine whose store was deleted has a list and no store: at boot the server writes an empty store stamped for the served corpus that names the list and journals it as a base, so the full GET lists the accepted units instead of a 404. Beside an existing store, or with no list, it writes nothing."""
    path = tmp_path / "verdicts-autosave.json"
    journal_path = tmp_path / "verdicts-journal.ndjson"
    assert VerdictStore(path, journal_path).stamp_empty_store(NEW) is None and not path.exists()
    write_list(path, {ACCEPT_A: "approve", ACCEPT_B: "either"})
    store = VerdictStore(path, journal_path)
    assert store.stamp_empty_store(NEW) == {"stamp": NEW, "accepted": store.accepted.field()}
    assert (
        on_disk(path)["manifest_generated_at"] == NEW and on_disk(path)["accepted"] == store.accepted.field()
    )
    assert journal.replay_with_list(journal_path) == (NEW, {}, {}, store.accepted.field())
    field = json.loads(store.payload_bytes(token=True, within=frozenset({ACCEPT_A})))["accepted"]
    assert (field["approve"], field["either"]) == ([ACCEPT_A], [])
    assert VerdictStore(path, journal_path).stamp_empty_store(NEW) is None
