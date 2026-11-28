"""Tests for the review server's logic.

The /autosave receiver: delta validation, the 400 any other body gets without touching the file (a whole-store document among them), export parsing and the full-store GET payload, and the store written whole through a temporary file renamed over it before each reply. A delta on another stamp is carried onto a store on the served corpus by unit id, with the `base_at`, `at` and tombstone tests deciding each unit, and the orphans and conflicts it cannot apply are kept in the orphan document; a stale skip is dropped and clears the record it replaced; a delta stamped for the served corpus carries a store on another stamp onto it by unit id, and one made on neither corpus gets a retryable 503 and writes nothing.

The POST path takes the verdict store's lock without waiting, and answers a retryable 503 while another writer holds it. The resident store behind it (`engine.review.verdict_store`): the delta POST the app sends, the no-op delta that writes nothing, the save whose write fails, which is put back, a real server that writes each save before it replies, the change token a sync GET returns, the reload after an external rewrite of the file, which keeps tokens valid when the stamp is unchanged, and the store file holding a record the store refuses, which serves no verdict and refuses every save until it is replaced.

The headers every static file is served with. `static_headers_for` is a pure function so it can be tested without a server.

The namer dot's stand-in in the fonts the app loads (`namer_dot_stand_in_font`), and the dot lowering that a browser's run split at the dot would defeat without it.

The reloads: the server's one livereload watch never fires, and `force_reload` sends an `ams:` path to /forcereload on the port it is given and returns False for anything but a server that took it.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from engine.review.serve import (
    DELTA_FORMAT,
    EXPORT_FORMAT,
    missing_autosave_reply,
    parse_autosave_payload,
    receive_autosave,
    receive_autosave_locked,
    namer_dot_stand_in_font,
    register_dormant_watch,
    static_headers_for,
)
from engine.pipeline.emit_gsub import namer_dot_lookup
from engine.tools.review_server import force_reload
from engine.validation.shaping import Shaper
from engine.review.store_lock import store_lock
from engine.review.verdict_store import (
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


def test_invalid_payloads_are_rejected_without_touching_the_file(tmp_path):
    """Any body but a delta gets a 400 and writes nothing, a whole-store document like the file itself among them."""
    path = tmp_path / "verdicts-autosave.json"
    stored = payload("2026-07-03T23:31:04Z", [verdict("u-0001")])
    path.write_bytes(stored)
    for raw in (
        stored,
        b"not json",
        b'"a string"',
        payload("2026-07-03T23:31:04Z", fmt="something-else/9"),
        json.dumps({"format": EXPORT_FORMAT, "manifest_generated_at": None, "verdicts": []}).encode(),
        json.dumps({"format": EXPORT_FORMAT, "manifest_generated_at": "x", "verdicts": "nope"}).encode(),
    ):
        status, body = receive_autosave(raw, path)
        assert status == 400
        assert body["ok"] is False
    assert path.read_bytes() == stored
    assert list(tmp_path.iterdir()) == [path]


def test_a_delta_replaces_an_unreadable_store(tmp_path):
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


def test_every_file_is_served_uncached():
    """Every file gets `no-store` and nothing else, because a rebuild reuses every name: a stale cached shard or font would show the reader another corpus without any sign of it."""
    for path in ("index.html", "app.js", "manifest.json", "units/joins.json", "fonts/after.otf"):
        assert static_headers_for(path) == {"Cache-Control": "no-store"}


DOT_CONTEXT_CALT = "feature calt { sub qsPea' periodcentered by qsPea.alt; } calt;"


def _dot_font(path: Path, cmap: dict[int, str], features: str = DOT_CONTEXT_CALT) -> Path:
    """Build a small CFF font, as the corpus fonts are, with a namer dot, its lowered form, a ZWNJ, a letter and its alternate, and the `features` given: by default a `calt` rule that swaps the letter for its alternate before the dot, so a test can see the dot's context reach the letter."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    order = [".notdef", "periodcentered", "periodcentered.lowered", "uni200C", "qsPea", "qsPea.alt"]
    charstrings = {}
    for name in order:
        pen = T2CharStringPen(500, None)
        if name != ".notdef":
            pen.moveTo((100, 100))
            pen.lineTo((150, 100))
            pen.lineTo((150, 150))
            pen.closePath()
        charstrings[name] = pen.getCharString()
    builder = FontBuilder(550, isTTF=False)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupCFF("DotTest-Regular", {"FullName": "DotTest"}, charstrings, {})
    builder.setupHorizontalMetrics({name: (500, 0 if name == ".notdef" else 100) for name in order})
    builder.setupHorizontalHeader(ascent=450, descent=-100)
    builder.setupNameTable({"familyName": "DotTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.addOpenTypeFeatures(features)
    builder.save(str(path))
    return path


def test_the_namer_dots_stand_in_shapes_as_the_dot_and_changes_nothing_else(tmp_path):
    """The stand-in maps to the dot's glyph, so a rule with the dot in its context fires on the stand-in too, and every table but `cmap` and `head`, which holds the checksum adjustment, comes back as the bytes it was read from."""
    from fontTools.ttLib import TTFont

    source = _dot_font(tmp_path / "dot.otf", {0x00B7: "periodcentered", 0xE650: "qsPea"})
    derived = tmp_path / "derived.otf"
    derived.write_bytes(namer_dot_stand_in_font(source))
    before, after = TTFont(source), TTFont(derived)
    assert after.getBestCmap() == {**(before.getBestCmap() or {}), 0xF8FF: "periodcentered"}
    tags = [tag for tag in before.keys() if tag != "GlyphOrder"]
    assert [tag for tag in after.keys() if tag != "GlyphOrder"] == tags
    for tag in tags:
        if tag not in ("cmap", "head"):
            assert after.getTableData(tag) == before.getTableData(tag), tag
    shaper = Shaper(derived)
    assert (
        shaper.shape("\ue650\uf8ff").names
        == shaper.shape("\ue650\u00b7").names
        == ("qsPea.alt", "periodcentered")
    )


def test_the_namer_dots_stand_in_refuses_a_font_it_cannot_patch(tmp_path):
    """A font with no namer dot has no glyph to stand in for, and one that maps the stand-in to another glyph would draw that glyph where the app writes the dot."""
    taken = _dot_font(tmp_path / "taken.otf", {0x00B7: "periodcentered", 0xE650: "qsPea", 0xF8FF: "qsPea"})
    with pytest.raises(ValueError, match=r"already maps U\+F8FF"):
        namer_dot_stand_in_font(taken)
    dotless = _dot_font(tmp_path / "dotless.otf", {0xE650: "qsPea"})
    with pytest.raises(ValueError, match=r"maps no glyph to U\+00B7"):
        namer_dot_stand_in_font(dotless)


def test_the_namer_dots_stand_in_keeps_the_dot_lowering_that_reads_the_next_letter(tmp_path):
    """Senior lowers the namer dot before a Short letter by a rule that reads the letter after the dot (`emit_gsub.namer_dot_lookup`). Chromium ends a shaping run at U+00B7, so in the app the dot alone would be shaped and drawn unlowered; the stand-in keeps the sample in one run, so the rule sees the follower. This fails once the dot's form no longer depends on the letter after it, when the run split changes nothing and the stand-in has nothing left to keep."""
    lookup = namer_dot_lookup("periodcentered", "periodcentered.lowered", ["qsPea"])
    feature = "\n".join(lookup.lines) + f"\nfeature calt {{ lookup {lookup.name}; }} calt;"
    source = _dot_font(
        tmp_path / "dot.otf", {0x00B7: "periodcentered", 0x200C: "uni200C", 0xE650: "qsPea"}, feature
    )
    derived = tmp_path / "derived.otf"
    derived.write_bytes(namer_dot_stand_in_font(source))
    dot_alone = Shaper(source).shape("\u00b7").names
    together = Shaper(source).shape("\u00b7\ue650").names
    assert dot_alone[0] != together[0], (
        "the namer dot's form no longer depends on the letter after it, so Chromium's run split at U+00B7 draws it the same: "
        "the stand-in (`namer_dot_stand_in_font` and the app's `namerDotStandIn`) can go"
    )
    assert Shaper(derived).shape("\uf8ff\ue650").names == together == ("periodcentered.lowered", "qsPea")


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


def test_a_delta_applies_sets_and_clears_and_writes_the_store_before_it_replies(tmp_path):
    """A save applies its delta in memory and writes the whole store over its file through a temporary file renamed over it before it replies: the records in unit order, a tombstone for each clear, and nothing beside the file."""
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    assert store.receive(delta(stamp, [verdict("u-1"), verdict("u-2")]))[0] == 200
    status, body = store.receive(
        delta(stamp, [verdict("u-2", "reject", at="2026-07-03T01:00:00Z"), verdict("u-3")], [{"unit": "u-1"}])
    )
    assert status == 200
    assert body["ok"] and body["saved"] == 2 and body["token"] == store.token
    written = on_disk(path)
    assert written["manifest_generated_at"] == stamp
    assert [(record["unit"], record["verdict"]) for record in written["verdicts"]] == [
        ("u-2", "reject"),
        ("u-3", "approve"),
    ]
    assert [entry["unit"] for entry in written["cleared"]] == ["u-1"]
    on_file = json.loads(path.read_bytes())
    assert {**on_file, "exported_at": None} == {**json.loads(store.payload_bytes()), "exported_at": None}
    assert list(tmp_path.iterdir()) == [path]
    assert not store.refresh_if_changed()


def test_a_post_while_another_writer_holds_the_store_lock_gets_a_retryable_503(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    assert receive_autosave_locked(store, delta(stamp, [verdict("u-1")]))[0] == 200
    before = path.read_bytes()
    with store_lock(path):
        status, body = receive_autosave_locked(store, delta(stamp, [verdict("u-2")]))
    assert status == 503
    assert body["retry"] is True and body["ok"] is False
    assert path.read_bytes() == before
    assert "u-2" not in store.records
    status, _ = receive_autosave_locked(store, delta(stamp, [verdict("u-2")]))
    assert status == 200
    assert "u-2" in {record["unit"] for record in on_disk(path)["verdicts"]}


def test_a_delta_that_changes_nothing_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(delta(stamp, [verdict("u-1")]))
    os.utime(path, ns=(1, 1))
    before = path.read_bytes()
    status, body = store.receive(delta(stamp, [verdict("u-1")], [{"unit": "u-9"}]))
    assert status == 200 and body["ok"]
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == 1
    assert not store.refresh_if_changed()


REPO = Path(__file__).resolve().parent.parent
SERVER_CHILD = """
import sys
from pathlib import Path
from engine.review import serve
root = Path(sys.argv[1])
serve.REPO_ROOT = root
serve.REVIEW_DIR = root / "engine" / "out" / "review"
serve.BUILD_OUT = root / "engine" / "out" / "build"
serve.RUNES_DIR = root / "glyph_data" / "runes"
serve.CYCLE_SUMMARY_PATH = root / "engine" / "out" / "cycle_summary.json"
serve.AUTOSAVE_PATH = root / "verdicts-autosave.json"
serve.PORT = int(sys.argv[2])
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


def start_server(root: Path) -> tuple[subprocess.Popen, int]:
    """Run `serve.main` in a child process with its paths pointed at `root` and return it once it answers /capabilities."""
    port = free_port()
    child = subprocess.Popen(
        [sys.executable, "-c", SERVER_CHILD, str(root), str(port)],
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


def test_a_real_server_writes_each_save_before_it_replies_and_serves_the_store(tmp_path):
    """The server's POST handler writes the store before it answers each save, so the file holds every save once its reply arrives, and a full GET of /autosave serves the store as the file holds it, with its sync token."""
    stamp = "2026-07-03T23:31:04Z"
    corpus = tmp_path / "engine" / "out" / "review"
    corpus.mkdir(parents=True)
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": stamp, "human_unit_ids": [f"u-{index}" for index in range(1, 6)]})
    )
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(payload(stamp, [verdict("u-1")]))
    server, port = start_server(tmp_path)
    try:
        for index in range(2, 6):
            status, _reply = http(
                port, "/autosave", delta(stamp, [verdict(f"u-{index}", at=f"2026-07-04T00:0{index}:00Z")])
            )
            assert status == 200
            assert {record["unit"] for record in on_disk(path)["verdicts"]} == {
                f"u-{n}" for n in range(1, index + 1)
            }
        status, raw = http(port, "/autosave")
        assert status == 200
        served_store = json.loads(raw)
        assert isinstance(served_store.pop("token"), str)
        assert served_store["verdicts"] == on_disk(path)["verdicts"]
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)


def test_a_save_whose_write_fails_is_put_back(tmp_path, monkeypatch):
    """A save whose store write fails reloads the store from the file, which holds every save but that one, before the error propagates, so memory never holds a record the file lacks, no temporary file is left, and a resend of the same delta is applied and written."""
    path = tmp_path / "verdicts-autosave.json"
    stamp = "2026-07-03T23:31:04Z"
    store = VerdictStore(path)
    store.receive(delta(stamp, [verdict("u-1")]))
    store.receive(delta(stamp, [verdict("u-2")]))
    before = path.read_bytes()

    def disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr("engine.review.verdict_store.os.replace", disk_full)
        with pytest.raises(OSError):
            store.receive(delta(stamp, [verdict("u-3")], [{"unit": "u-1"}]))
    assert set(store.records) == {"u-1", "u-2"} and store.cleared == {}
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]
    status, _ = store.receive(delta(stamp, [verdict("u-3")], [{"unit": "u-1"}]))
    assert status == 200 and set(store.records) == {"u-2", "u-3"}
    assert {record["unit"] for record in on_disk(path)["verdicts"]} == {"u-2", "u-3"}


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
    store.receive(delta(stamp, [verdict("u-3")], [{"unit": "u-1"}]))
    moved = changes(store, token)
    assert moved["token"] == store.token
    assert [record["unit"] for record in moved["sets"]] == ["u-3"]
    assert moved["clears"] == ["u-1"]
    assert changes(store, store.token)["sets"] == []
    for bad in (None, "", "nope", "other:1", f"{token.split(':')[0]}:99"):
        assert store.changes_since(bad) is None


def test_an_external_rewrite_onto_another_stamp_is_picked_up_and_invalidates_tokens(tmp_path):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    store.receive(delta("2026-07-03T06:13:47Z", [verdict("u-1")]))
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
    """A reload onto the same stamp reports each unit whose record or tombstone changed, came or went, so outstanding tokens stay valid."""
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
    store.receive(delta(stamp, [verdict("u-1")]))
    replacement = tmp_path / "merged.json"
    replacement.write_bytes(payload(stamp, [verdict("u-1"), verdict("u-2")]))
    os.utime(replacement, ns=(2, 2))
    real_replace = os.replace

    def replace_then_rename_over(src, dst):
        real_replace(src, dst)
        if Path(dst) == path and replacement.exists():
            real_replace(replacement, path)

    monkeypatch.setattr("engine.review.verdict_store.os.replace", replace_then_rename_over)
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


def new_store(tmp_path, records):
    path = tmp_path / "verdicts-autosave.json"
    store = VerdictStore(path)
    assert store.receive(delta(NEW, records))[0] == 200
    return path, store


def test_parse_delta_payload_normalizes_each_clear():
    parsed = parse_delta_payload(
        delta(NEW, clears=[{"unit": "u-1"}, {"unit": "u-2", "base_at": "t1", "at": "t2"}])
    )
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
def test_a_set_whose_record_the_store_refuses_is_refused_before_any_write(tmp_path, verdict_field):
    path, store = new_store(tmp_path, [verdict("u-0")])
    before = path.read_bytes(), sorted(tmp_path.rglob("*"))
    status, body = store.receive(delta(NEW, [{"unit": "u-1", "at": "2026-07-10T00:00:00Z", **verdict_field}]))
    assert status == 400 and body["ok"] is False
    assert body["error"].startswith(f"the {DELTA_FORMAT} document holds a malformed set on u-1:")
    assert (path.read_bytes(), sorted(tmp_path.rglob("*"))) == before
    assert set(store.records) == {"u-0"}
    assert store.receive(delta(NEW, [verdict("u-1", at="2026-07-10T00:00:00Z")]))[0] == 200
    assert on_disk(path)["verdicts"] == [verdict("u-0"), verdict("u-1", at="2026-07-10T00:00:00Z")]


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
        ("u-1", "at index 1 of its `clears` list"),
        ({"unit": "u-1", "at": 5}, "on u-1"),
    ],
    ids=["no-unit", "numeric-unit", "bare-unit-id", "numeric-at"],
)
def test_a_delta_holding_a_malformed_clear_is_refused_naming_the_first_such_clear(tmp_path, malformed, named):
    """The 400 names the first malformed clear by its unit, or by its index when it names no unit or a unit that is not a string, as it names a malformed set."""
    store = VerdictStore(tmp_path / "verdicts-autosave.json")
    status, body = store.receive(
        delta(NEW, [verdict("u-0")], [{"unit": "u-0"}, malformed, {"unit": "u-2", "at": 6}])
    )
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
def test_a_store_holding_a_record_it_refuses_is_read_as_unreadable_and_refuses_every_save(
    tmp_path, record, named, stamp, replay, sets, clears
):
    """A store file holding a record `well_formed` refuses serves no verdict, as an unreadable one does, and every save, which would otherwise weigh its `at` against the record's or write over the file, gets a 422 naming the file and the record, by its unit or by its index when it names none, and writes nothing, until the file is replaced. A GET's 404 names them under the same reason until then, and says there is no autosave once no file is there."""
    path = tmp_path / "verdicts-autosave.json"
    path.write_bytes(payload(NEW, [verdict("u-A"), verdict("u-C"), record]))
    before = path.read_bytes()
    store = VerdictStore(path)
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
    store = VerdictStore(path)
    assert store.stamp == NEW
    path.write_bytes(payload(NEW, [verdict("u-A"), {**verdict("u-A", "reject"), "at": 5}]))
    before = path.read_bytes()
    status, body = store.receive(delta(NEW, [verdict("u-B")]))
    assert status == 422 and body["reason"] == "store-malformed"
    assert body["error"].startswith(f"the store {path} holds a verdict record on u-A that is not an object")
    assert store.stamp is None and store.records == {}
    assert path.read_bytes() == before and sorted(tmp_path.rglob("*")) == [path]


def test_a_stale_stamp_delta_on_a_served_unit_is_carried(tmp_path):
    path, store = new_store(tmp_path, [verdict("u-1", at="t1")])
    status, body = store.receive(
        delta(OLD, [{**verdict("u-2", "reject", at="t5"), "base_at": None}]), served("u-1", "u-2")
    )
    assert status == 200
    assert body["carried"] == ["u-2"] and body["orphaned"] == [] and body["conflicts"] == []
    assert body["corpus_stamp"] == NEW
    written = on_disk(path)
    assert written["manifest_generated_at"] == NEW
    assert {record["unit"] for record in written["verdicts"]} == {"u-1", "u-2"}


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
    _, store = new_store(tmp_path, [{**verdict("u-1", at="t5"), "note": "first look"}])
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
    """A tab loaded on the old corpus skips a unit the land left holding the verdict the skip replaced. The skip is dropped, as the carry drops every skip, but it clears that record under the same tests as a set, so the unit comes back blank rather than holding the superseded verdict; the reply lists it under `dropped`, and the store is written without it. A skip older than a record the tab never saw is a conflict instead."""
    path, store = new_store(tmp_path, [verdict("u-1", at="t1"), verdict("u-2", at="t5")])
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
    assert "u-1" not in {record["unit"] for record in on_disk(path)["verdicts"]}
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
    store.receive(delta(NEW, clears=[{"unit": "u-1", "base_at": "t1", "at": "t5"}, {"unit": "u-2"}]))
    reloaded = VerdictStore(path)
    assert reloaded.cleared["u-1"] == "t5"
    assert set(reloaded.cleared) == {"u-1", "u-2"}
    assert reloaded.records == {}


def test_a_stale_clear_follows_the_same_tests_and_a_clear_with_no_at_is_orphaned(tmp_path):
    path, store = new_store(
        tmp_path, [verdict("u-1", at="t5"), verdict("u-2", at="t5"), verdict("u-3", at="t5")]
    )
    status, body = store.receive(
        delta(
            OLD,
            clears=[
                {"unit": "u-1", "base_at": "t5", "at": "t2"},
                {"unit": "u-2", "base_at": "t1", "at": "t2"},
                {"unit": "u-3"},
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
    """A land killed between its corpus swap and its store write, or a first pass, which builds the missing corpus in place and lands no store, leaves the store on another stamp than the served corpus. The first save from a tab on the served corpus carries the store onto it by unit id, as the land does: its verdicts and tombstones on the served corpus's human units stay, less its skips, each of which leaves a tombstone at its `at`, and the rest go to the orphan document of the stamp they were made on. The save is then applied, the moved store written at once and every token invalidated, so the tab keeps saving."""
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
    store = VerdictStore(path)
    assert store.receive(delta(NEW, [verdict("u-2", "reject", at="t3")]))[0] == 200
    old_token = store.token
    newer = "2026-07-05T00:00:00Z"
    status, body = store.receive(
        delta(newer, [verdict("u-9", "reject", at="t9")]), served("u-1", "u-3", "u-4", "u-9", stamp=newer)
    )
    assert status == 200
    assert body["corpus_stamp"] == newer and body["token"] == store.token != old_token
    written = on_disk(path)
    assert written["manifest_generated_at"] == newer
    assert [record["unit"] for record in written["verdicts"]] == ["u-1", "u-9"]
    assert written["cleared"] == [{"unit": "u-3", "at": "t1"}, {"unit": "u-4", "at": "t2"}]
    [orphan] = orphan_records(path, NEW)
    assert (orphan["unit"], orphan["verdict"], orphan["reason"]) == ("u-2", "reject", "orphan")
    status, body = store.receive(
        delta(OLD, [verdict("u-9", at="t95")]), served("u-1", "u-3", "u-4", "u-9", stamp=newer)
    )
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
