"""Tests for the review server's /autosave receiving logic: payload validation, atomic overwrite, and the stash-aside of an existing autosave whose manifest generation differs from the incoming one (a stale-manifest autosave may be the only copy of un-exported work from before a surface rebuild, and its unit ids must never be silently joined to the new surface)."""

import json

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


def verdict(unit, kind="approve"):
    return {"unit": unit, "verdict": kind, "note": "", "at": "2026-07-03T00:00:00Z"}


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
