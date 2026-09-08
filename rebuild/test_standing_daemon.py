"""Tests for the standing daemon, the one process that holds a review surface for the standing probe and the standing dry run: over a real build of the frozen mini bundle, a probe naming units and running its find, survey and coverage modes, and a dry run in both its forms — the whole-domain run with its fill file and the targeted run — print byte for byte through the daemon what they print in-process, exit code, stdout and fill bytes alike, with the held font pair serving the rendered grain; that the fallback is what a run with no daemon gets, silently when there was no socket to ask and with one stderr line when there was, and that `--daemon always` refuses to load instead; that a daemon whose surface manifest or font pair has moved declines the request as stale and exits with its socket removed; that a request for a surface it does not hold is declined without exiting; that SIGTERM removes the socket and a second `serve` refuses to start beside a live one without loading; that the `status` and `stop` verbs say what is held and end it; and that a caller handing either tool its own units is never served, which is the verdict chain's in-process form. Every daemon here is a real child process over a socket under tmp_path — SIGTERM, socket removal and the second-daemon refusal are process facts — and rebuild/conftest.py points the tools' default socket under tmp_path for every test, so nothing here can reach a daemon on the box."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rebuild.test_standing_verdicts import _human_units, _mini_rules
from rebuild.tools import standing_client, standing_daemon
from rebuild.tools import standing_probe as probe
from rebuild.tools import standing_verdicts as sv

DAEMON = Path(standing_daemon.__file__)
START_SECONDS = 120
STOP_SECONDS = 30
POLL_SECONDS = 0.25


def _stamp(surface):
    return json.loads((surface / "manifest.json").read_text())["generated_at"]


def _verdicts(path, surface, records):
    path.write_text(
        json.dumps(
            {"format": "ams-review-verdicts/1", "manifest_generated_at": _stamp(surface), "verdicts": records}
        )
    )
    return path


def _start(surface, cwd):
    """A daemon over `surface`, listening at `cwd / "daemon.sock"` with its log beside it, returned once its status verb answers; a child that dies or never answers fails the test with the log."""
    sock = cwd / "daemon.sock"
    log = cwd / "daemon.log"
    with log.open("w") as handle:
        proc = subprocess.Popen(
            [sys.executable, str(DAEMON), "serve", "--surface", str(surface), "--socket", str(sock)],
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        reply = standing_client.exchange(sock, {"tool": "status"})
        if reply is not None and reply.get("ok"):
            return proc, sock
        if proc.poll() is not None:
            break
        time.sleep(POLL_SECONDS)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    pytest.fail(f"the standing daemon did not answer:\n{log.read_text()}")


def _stop(proc, sock):
    if proc.poll() is None:
        standing_client.exchange(sock, {"tool": "stop"})
        proc.wait(STOP_SECONDS)
    return proc.returncode


@pytest.fixture(scope="module")
def daemon(mini_surface, tmp_path_factory):
    """One daemon over the mini surface for the tests that only ask it things; it is stopped at teardown and must exit clean with its socket gone."""
    proc, sock = _start(mini_surface, tmp_path_factory.mktemp("standing-daemon"))
    yield proc, sock
    assert _stop(proc, sock) == 0
    assert not os.path.lexists(sock)


def _probe(capsys, argv):
    code = probe.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _fill(capsys, argv):
    code = sv.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _probe_argv(surface, tmp_path, sock, *units):
    verdicts = _verdicts(tmp_path / "verdicts.json", surface, [])
    return [*units, "--surface", str(surface), "--verdicts", str(verdicts), "--socket", str(sock)]


def test_a_served_probe_is_byte_identical_to_the_in_process_one(daemon, mini_surface, tmp_path, capsys):
    """Every mode in one call, as the skill batches them — three unit ids at the rendered grain, --find, --survey over the first unit's family and --coverage on a rule whose shape has an enumeration — with a store holding one reject: served and in-process agree on the exit code and every byte of stdout, nothing reaches stderr, and the rendered-grain columns are there, so the held SlideContext served rather than the NO_FONTS fallback."""
    _proc, sock = daemon
    human = _human_units(mini_surface)
    ids = [unit["id"] for unit in human[:3]]
    verdicts = _verdicts(
        tmp_path / "verdicts.json",
        mini_surface,
        [{"unit": ids[0], "verdict": "reject", "note": "", "at": _stamp(mini_surface)}],
    )
    rules = _mini_rules(mini_surface, tmp_path / "rules.yaml")
    family = sv._family(human[0]["before"]["glyphs"][0])
    covered = next(
        rule["id"]
        for rule in sv.load_rules(rules)
        if probe._shape_name(rule["match"]) in probe.COVERAGE_SHAPES
    )
    argv = [
        *ids,
        "--find",
        "·",
        "--survey",
        family,
        "--coverage",
        covered,
        "--surface",
        str(mini_surface),
        "--rules",
        str(rules),
        "--verdicts",
        str(verdicts),
        "--socket",
        str(sock),
    ]
    served = _probe(capsys, [*argv, "--daemon", "always"])
    local = _probe(capsys, [*argv, "--daemon", "never"])
    assert served == local
    code, out, err = served
    assert code == 0 and err == ""
    assert probe.NO_FONTS not in out
    assert all(unit_id in out for unit_id in ids)
    assert f"survey of {family}" in out and f"coverage for rule {covered!r}" in out
    assert re.search(r"same shape|redrawn|inkless|ink appears|ink vanishes", out)


def test_a_served_dry_run_is_byte_identical_to_the_in_process_one(
    daemon, mini_surface, tmp_path, monkeypatch, capsys
):
    """Both forms, under the bundle-local rules and a store holding a reject and an approve: the whole-domain run with a relative --out, resolved under the client's working directory on both sides, agrees on the exit code, every report line and the fill file's bytes, and writes fills; the targeted run agrees on the exit code and every line and writes nothing."""
    _proc, sock = daemon
    human = [unit["id"] for unit in _human_units(mini_surface)]
    stamp = _stamp(mini_surface)
    verdicts = _verdicts(
        tmp_path / "verdicts.json",
        mini_surface,
        [
            {"unit": human[0], "verdict": "reject", "note": "", "at": stamp},
            {"unit": human[-1], "verdict": "approve", "note": "", "at": stamp},
        ],
    )
    rules = _mini_rules(mini_surface, tmp_path / "rules.yaml")
    base = [str(verdicts), "--surface", str(mini_surface), "--rules", str(rules), "--socket", str(sock)]
    whole = {}
    for mode in ("always", "never"):
        cwd = tmp_path / "whole" / mode
        cwd.mkdir(parents=True)
        monkeypatch.chdir(cwd)
        code, out, err = _fill(capsys, [*base, "--out", "out.json", "--daemon", mode])
        whole[mode] = (code, out, err, (cwd / "out.json").read_bytes())
    assert whole["always"] == whole["never"]
    code, out, err, fills = whole["always"]
    assert code == 0 and err == "" and "wrote out.json:" in out
    assert json.loads(fills)["verdicts"]
    targeted = {}
    for mode in ("always", "never"):
        cwd = tmp_path / "targeted" / mode
        cwd.mkdir(parents=True)
        monkeypatch.chdir(cwd)
        targeted[mode] = _fill(
            capsys,
            [*base, "--explain", "mini-bundle-ink-delta", "--targeted", "--unit", human[0], "--daemon", mode],
        )
        assert list(cwd.iterdir()) == []
    assert targeted["always"] == targeted["never"]
    code, out, err = targeted["always"]
    assert code == 0 and err == "" and out.startswith("  targeted at mini-bundle-ink-delta:")


def test_the_fallback_engages_when_no_daemon_answers(mini_surface, tmp_path, capsys):
    """With --socket naming a path nothing binds, auto mode is never mode for both tools with nothing on stderr; with a plain file there, auto mode still matches and says on stderr that it is loading here; always mode refuses to load, naming the socket."""
    unbound = tmp_path / "nothing" / "daemon.sock"
    plain = tmp_path / "plain.sock"
    plain.write_text("")
    human = _human_units(mini_surface)
    unit_id = human[0]["id"]
    rules = _mini_rules(mini_surface, tmp_path / "rules.yaml")
    verdicts = _verdicts(tmp_path / "verdicts.json", mini_surface, [])
    probe_argv = [unit_id, "--surface", str(mini_surface), "--verdicts", str(verdicts), "--rules", str(rules)]
    fill_argv = [
        str(verdicts),
        "--surface",
        str(mini_surface),
        "--rules",
        str(rules),
        "--explain",
        "mini-bundle-ink-delta",
        "--targeted",
    ]
    for run, argv in ((_probe, probe_argv), (_fill, fill_argv)):
        never = run(capsys, [*argv, "--socket", str(unbound), "--daemon", "never"])
        auto = run(capsys, [*argv, "--socket", str(unbound), "--daemon", "auto"])
        assert auto == never and auto[2] == "" and auto[0] == 0
        code, out, err = run(capsys, [*argv, "--socket", str(plain), "--daemon", "auto"])
        assert (code, out) == never[:2]
        assert err.count("\n") == 1 and "loading the surface in this process instead" in err
    with pytest.raises(SystemExit, match=re.escape(str(unbound))):
        probe.main([*probe_argv, "--socket", str(unbound), "--daemon", "always"])
    with pytest.raises(SystemExit, match=re.escape(str(unbound))):
        sv.main([*fill_argv, "--socket", str(unbound), "--daemon", "always"])


@pytest.mark.parametrize("axis", ["manifest", "font"])
def test_a_stale_daemon_declines_and_exits(mini_surface, tmp_path, capsys, axis):
    """A daemon over a copy of the mini surface, the copy then edited on one axis of its stamp — the manifest's generated_at, or the after font's bytes — declines the next request as stale, and exits clean with its socket gone."""
    copy = tmp_path / "surface"
    shutil.copytree(mini_surface, copy)
    proc, sock = _start(copy, tmp_path)
    try:
        if axis == "manifest":
            manifest = json.loads((copy / "manifest.json").read_text())
            manifest["generated_at"] = "1999-01-01T00:00:00Z"
            (copy / "manifest.json").write_text(json.dumps(manifest))
        else:
            with (copy / "fonts" / "after.otf").open("ab") as handle:
                handle.write(b"\0")
        unit_id = _human_units(mini_surface)[0]["id"]
        with pytest.raises(SystemExit, match="stale"):
            probe.main([*_probe_argv(copy, tmp_path, sock, unit_id), "--daemon", "always"])
        capsys.readouterr()
        proc.wait(STOP_SECONDS)
        assert proc.returncode == 0
        assert not os.path.lexists(sock)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_request_for_another_surface_is_declined(daemon, mini_surface, tmp_path, capsys):
    """Asked about a copy of the surface it holds, the daemon declines naming both: always mode exits with that reason, auto mode falls back to the in-process output with one stderr note, and the daemon stays up."""
    proc, sock = daemon
    copy = tmp_path / "other"
    shutil.copytree(mini_surface, copy)
    unit_id = _human_units(mini_surface)[0]["id"]
    argv = _probe_argv(copy, tmp_path, sock, unit_id)
    with pytest.raises(SystemExit) as caught:
        probe.main([*argv, "--daemon", "always"])
    assert str(mini_surface.resolve()) in str(caught.value) and str(copy.resolve()) in str(caught.value)
    never = _probe(capsys, [*argv, "--daemon", "never"])
    code, out, err = _probe(capsys, [*argv, "--daemon", "auto"])
    assert (code, out) == never[:2] and never[2] == ""
    assert err.count("\n") == 1 and "it holds" in err and "loading the surface in this process instead" in err
    assert proc.poll() is None
    status = standing_client.exchange(sock, {"tool": "status"})
    assert status is not None and status["ok"]


def test_sigterm_removes_the_socket_and_a_second_daemon_refuses_to_start(mini_surface, tmp_path, capsys):
    """A fresh daemon answers status; a second serve on the same socket exits 1 naming the first's pid without loading; SIGTERM to the first ends it clean, removes the socket, and leaves status answering that nothing is there."""
    proc, sock = _start(mini_surface, tmp_path)
    try:
        assert standing_daemon.main(["status", "--socket", str(sock)]) == 0
        capsys.readouterr()
        assert standing_daemon.main(["serve", "--surface", str(mini_surface), "--socket", str(sock)]) == 1
        out = capsys.readouterr().out
        assert "already answers" in out and f"pid {proc.pid}" in out
        assert proc.poll() is None
        proc.send_signal(signal.SIGTERM)
        proc.wait(STOP_SECONDS)
        assert proc.returncode == 0
        assert not os.path.lexists(sock)
        assert standing_daemon.main(["status", "--socket", str(sock)]) == 1
        assert "no standing daemon answers" in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_the_status_and_stop_verbs_report_the_held_surface(mini_surface, tmp_path, capsys):
    proc, sock = _start(mini_surface, tmp_path)
    try:
        assert standing_daemon.main(["status", "--socket", str(sock)]) == 0
        out = capsys.readouterr().out
        assert str(mini_surface.resolve()) in out and _stamp(mini_surface) in out
        assert f"{len(_human_units(mini_surface))} human units" in out
        assert standing_daemon.main(["stop", "--socket", str(sock)]) == 0
        assert f"pid {proc.pid}" in capsys.readouterr().out
        proc.wait(STOP_SECONDS)
        assert proc.returncode == 0
        assert not os.path.lexists(sock)
        assert standing_daemon.main(["stop", "--socket", str(sock)]) == 0
        assert "no standing daemon answers" in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_caller_that_injects_units_is_never_served(mini_surface, tmp_path, capsys):
    """The verdict chain hands `main` its own units; with --daemon always pointed at a socket nothing binds, both tools still run to completion in-process, which is what proves the chain never asks."""
    unbound = tmp_path / "nothing" / "daemon.sock"
    units = _human_units(mini_surface)
    rules = _mini_rules(mini_surface, tmp_path / "rules.yaml")
    verdicts = _verdicts(tmp_path / "verdicts.json", mini_surface, [])
    flags = ["--daemon", "always", "--socket", str(unbound)]
    assert (
        sv.main(
            [
                str(verdicts),
                "--surface",
                str(mini_surface),
                "--rules",
                str(rules),
                "--out",
                str(tmp_path / "out.json"),
                *flags,
            ],
            units=units,
        )
        == 0
    )
    assert (tmp_path / "out.json").is_file()
    assert (
        probe.main(
            [units[0]["id"], "--surface", str(mini_surface), "--verdicts", str(verdicts), *flags],
            units=units,
        )
        == 0
    )
    assert units[0]["id"] in capsys.readouterr().out
