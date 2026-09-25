"""Tests for `rebuild/tools/verdict_chain.py`: the chain keeps only unit ids and echo records, reuses the echo records across steps, and gives the standing fill and the complaint docket fresh streams of human index records."""

import json
import pathlib

from rebuild.tools import console, verdict_chain as vc

STAMP = "S1"


def _payload():
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": STAMP,
        "exported_at": STAMP,
        "verdicts": [],
    }


def _write_out(argv):
    pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(_payload()))
    return 0


def test_a_step_opens_a_phase_and_the_timing_that_follows_closes_it(capsys):
    """Each step prints a `[phase]` line naming it and then a `[t]` line with the same label and its duration, which `console.Digest` matches by label. A failed step also prints a `[chain] failed:` line; that prefix marks a result, and `plumbing_sections` in `rebuild/tools/artifact_cycle.py` splits the chain's output on it."""
    assert vc._run("carry", lambda: 0) == 0
    assert vc._run("merge", lambda: 3) == 3
    lines = capsys.readouterr().out.splitlines()
    events = [console.parse_line(line) for line in lines]
    assert [event.name for event in events if isinstance(event, console.Phase)] == ["carry", "merge"]
    assert [event.label for event in events if isinstance(event, console.Timing)] == ["carry", "merge"]
    assert lines[-1] == f"{console.FAILED_LINE}merge (exit 3)"


IDS = frozenset({"u-1", "u-2", "u-machine"})


def _chain(tmp_path, monkeypatch, extra=(), complaints=False):
    """Run `verdict_chain.main` over a stub surface with every step stubbed. Return the exit code, the human index records, the standing fill's calls, the fill's `--out` path, the docket's calls (empty unless `complaints` is set), and the units each echo round received."""
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    master = tmp_path / "master.json"
    master.write_text(json.dumps(_payload()))
    index = [{"id": "u-1", "after": {"cells": [1]}}, {"id": "u-2"}]
    calls = []
    dockets = []
    echoes = []

    def stream(_surface, *, unit_ids=None):
        if unit_ids is not None:
            unit_ids.update(IDS)
        yield from (dict(unit) for unit in index)

    monkeypatch.setattr(vc.unit_index, "iter_human_units", stream)
    monkeypatch.setattr(vc.merge_verdicts, "main", lambda _argv: 0)

    def echo(argv, units=None):
        echoes.append(units)
        return _write_out(argv)

    monkeypatch.setattr(vc.echo_verdicts, "main", echo)

    def standing(argv, unit_source=None):
        assert unit_source is not None
        first, second = unit_source(), unit_source()
        assert first is not second
        assert list(first) == list(second) == index
        calls.append((argv, unit_source))
        return _write_out(argv)

    monkeypatch.setattr(vc.standing_verdicts, "main", standing)

    def docket(argv, units=None, unit_ids=None):
        assert units is not None
        dockets.append((argv, list(units), unit_ids))
        return 0

    monkeypatch.setattr(vc.complaint_docket, "main", docket)

    standing_out = tmp_path / "verdicts-standing-fill.json"
    code = vc.main(
        [
            "--surface",
            str(surface),
            "--merge-master",
            str(master),
            "--autosave",
            str(tmp_path / "verdicts-autosave.json"),
            "--journal",
            str(tmp_path / "verdicts-journal.ndjson"),
            "--echo-out",
            str(tmp_path / "verdicts-echo-fill.json"),
            "--standing-out",
            str(standing_out),
            "--rules",
            str(tmp_path / "standing-approvals.yaml"),
            *([] if complaints else ["--no-complaints"]),
            *extra,
        ]
    )
    return code, index, calls, standing_out, dockets, echoes


def test_the_chain_runs_the_standing_fill_in_its_open_only_form(tmp_path, monkeypatch):
    """The chain passes `--open-only --require-reach` and leaves the narrowing to the standing fill, so it still supplies fresh streams over all human records. The default memo sits beside the surface directory, outside it, so a surface rebuild does not delete it."""
    code, index, calls, standing_out, dockets, _echoes = _chain(tmp_path, monkeypatch)
    assert code == 0
    assert dockets == []
    [(argv, units)] = calls
    assert "--open-only" in argv
    assert "--require-reach" in argv
    assert argv[argv.index("--out") + 1] == str(standing_out)
    assert argv[argv.index("--memo") + 1] == str(tmp_path / vc.standing_verdicts.MEMO_NAME)
    assert "--fresh-memo" not in argv
    assert argv[argv.index("--jobs") + 1] == "1"
    assert list(units()) == index


def test_the_echo_fill_takes_the_human_records_and_the_docket_takes_the_id_set_beside_them(
    tmp_path, monkeypatch
):
    """Every echo round gets the same list of human echo records, because the echo fill reads nothing from machine records. The docket gets the human records and the set of every surface id, because its absent-unit warning checks verdicts against machine units too."""
    code, index, _calls, _out, dockets, echoes = _chain(tmp_path, monkeypatch, complaints=True)
    assert code == 0
    assert len(echoes) >= 2
    assert all(units is echoes[0] for units in echoes)
    assert echoes[0] == [vc.echo_verdicts.echo_record(unit) for unit in index]
    [(argv, units, unit_ids)] = dockets
    assert argv[argv.index("--surface") + 1] == str(tmp_path / "review")
    assert units == index
    assert unit_ids == IDS


def test_the_chain_forwards_the_cycles_standing_fill_width(tmp_path, monkeypatch):
    """The chain passes `--standing-fill-jobs` to the standing fill as `--jobs` and computes no width of its own."""
    code, _index, calls, _out, _dockets, _echoes = _chain(
        tmp_path, monkeypatch, ("--standing-fill-jobs", "6")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--jobs") + 1] == "6"


def test_the_chain_passes_a_named_memo_and_the_fresh_form_through(tmp_path, monkeypatch):
    """The chain passes `--standing-memo` to the fill as `--memo`, and `--fresh-standing-memo` (which the cycle sets under `--fresh`) as `--fresh-memo`."""
    memo = tmp_path / "elsewhere" / "memo.ndjson.gz"
    code, _index, calls, _out, _dockets, _echoes = _chain(
        tmp_path, monkeypatch, ("--standing-memo", str(memo), "--fresh-standing-memo")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--memo") + 1] == str(memo)
    assert "--fresh-memo" in argv


def _carrying_chain(tmp_path, monkeypatch, extra=()):
    """Run the chain with `--verdicts` and `--carry-out`, with every step stubbed. Return the exit code, the human index records, the carry's calls, and the merge's calls."""
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({**_payload(), "manifest_generated_at": "S0"}))
    index = [{"id": "u-DdcTojn1hba"}]
    carries = []
    merges = []

    def stream(_surface, *, unit_ids=None):
        if unit_ids is not None:
            unit_ids.update(IDS)
        yield from (dict(unit) for unit in index)

    monkeypatch.setattr(vc.unit_index, "iter_human_units", stream)
    monkeypatch.setattr(
        vc.carry_verdicts,
        "main",
        lambda argv, current_units=None, current_ids=None: carries.append((argv, current_units, current_ids))
        or 0,
    )
    monkeypatch.setattr(vc.merge_verdicts, "main", lambda argv: merges.append(argv) or 0)
    monkeypatch.setattr(vc.echo_verdicts, "main", lambda argv, units=None: _write_out(argv))
    monkeypatch.setattr(vc.standing_verdicts, "main", lambda argv, unit_source=None: _write_out(argv))
    code = vc.main(
        [
            "--surface",
            str(surface),
            "--verdicts",
            str(verdicts),
            "--carry-out",
            str(tmp_path / "carried.json"),
            "--autosave",
            str(tmp_path / "verdicts-autosave.json"),
            "--journal",
            str(tmp_path / "verdicts-journal.ndjson"),
            "--echo-out",
            str(tmp_path / "verdicts-echo-fill.json"),
            "--standing-out",
            str(tmp_path / "verdicts-standing-fill.json"),
            "--rules",
            str(tmp_path / "standing-approvals.yaml"),
            "--no-complaints",
            *extra,
        ]
    )
    return code, index, carries, merges


def test_the_carry_step_hands_the_verdicts_file_and_the_loaded_index_to_the_carry(tmp_path, monkeypatch):
    """The chain passes the carry the verdicts file, the human echo records, and every surface id, because the stranded count also checks machine ids. It names only the live surface as `--current-surface`, then merges the carried file."""
    code, index, carries, merges = _carrying_chain(tmp_path, monkeypatch)
    assert code == 0
    [(argv, units, unit_ids)] = carries
    assert argv[: argv.index("--out")] == ["--verdicts", str(tmp_path / "verdicts.json")]
    assert argv[argv.index("--out") + 1] == str(tmp_path / "carried.json")
    assert argv[argv.index("--current-surface") + 1] == str(tmp_path / "review")
    assert units == [vc.echo_verdicts.echo_record(unit) for unit in index]
    assert unit_ids == IDS
    assert merges[0][0] == str(tmp_path / "carried.json")


def test_the_rehearsal_form_stops_after_the_carry(tmp_path, monkeypatch):
    """`--no-merge` never writes the live store: the carry runs and nothing after it does."""
    code, _index, carries, merges = _carrying_chain(tmp_path, monkeypatch, ("--no-merge",))
    assert code == 0
    assert len(carries) == 1
    assert merges == []
