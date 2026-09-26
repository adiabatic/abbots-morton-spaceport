"""Tests for `rebuild/tools/verdict_update.py`: the verdict update keeps only unit ids and echo records, reuses the echo records across steps, and gives the standing fill and the complaint list fresh streams of human index records."""

import json
import pathlib

from rebuild.tools import console, verdict_update as vu

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
    """Each step prints a `[phase]` line naming it and then a `[t]` line with the same label and its duration, which `console.CycleConsole` matches by label. A failed step also prints a `[verdict-update] failed:` line; that prefix marks a result, and `verdict_update_sections` in `rebuild/tools/artifact_cycle.py` splits the verdict update's output on it."""
    assert vu._run("carry", lambda: 0) == 0
    assert vu._run("merge", lambda: 3) == 3
    lines = capsys.readouterr().out.splitlines()
    events = [console.parse_line(line) for line in lines]
    assert [event.name for event in events if isinstance(event, console.Phase)] == ["carry", "merge"]
    assert [event.label for event in events if isinstance(event, console.Timing)] == ["carry", "merge"]
    assert lines[-1] == f"{console.FAILED_LINE}merge (exit 3)"


IDS = frozenset({"u-1", "u-2", "u-machine"})


def _run_verdict_update(tmp_path, monkeypatch, extra=(), complaints=False):
    """Run `verdict_update.main` over a stub corpus with every step stubbed. Return the exit code, the human index records, the standing fill's calls, the fill's `--out` path, the complaint list's calls (empty unless `complaints` is set), and the units each echo round received."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    master = tmp_path / "master.json"
    master.write_text(json.dumps(_payload()))
    index = [{"id": "u-1", "after": {"cells": [1]}}, {"id": "u-2"}]
    calls = []
    complaint_list_calls = []
    echoes = []

    def stream(_corpus, *, unit_ids=None):
        if unit_ids is not None:
            unit_ids.update(IDS)
        yield from (dict(unit) for unit in index)

    monkeypatch.setattr(vu.unit_index, "iter_human_units", stream)
    monkeypatch.setattr(vu.merge_verdicts, "main", lambda _argv: 0)

    def echo(argv, units=None):
        echoes.append(units)
        return _write_out(argv)

    monkeypatch.setattr(vu.echo_verdicts, "main", echo)

    def standing(argv, unit_source=None):
        assert unit_source is not None
        first, second = unit_source(), unit_source()
        assert first is not second
        assert list(first) == list(second) == index
        calls.append((argv, unit_source))
        return _write_out(argv)

    monkeypatch.setattr(vu.standing_verdicts, "main", standing)

    def fake_complaint_list(argv, units=None, unit_ids=None):
        assert units is not None
        complaint_list_calls.append((argv, list(units), unit_ids))
        return 0

    monkeypatch.setattr(vu.complaint_list, "main", fake_complaint_list)

    standing_out = tmp_path / "verdicts-standing-fill.json"
    code = vu.main(
        [
            "--corpus",
            str(corpus),
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
    return code, index, calls, standing_out, complaint_list_calls, echoes


def test_the_verdict_update_runs_the_standing_fill_in_its_open_only_form(tmp_path, monkeypatch):
    """The verdict update passes `--open-only --require-reach` and leaves the narrowing to the standing fill, so it still supplies fresh streams over all human records. The default memo sits beside the corpus directory, outside it, so a corpus rebuild does not delete it."""
    code, index, calls, standing_out, complaint_list_calls, _echoes = _run_verdict_update(
        tmp_path, monkeypatch
    )
    assert code == 0
    assert complaint_list_calls == []
    [(argv, units)] = calls
    assert "--open-only" in argv
    assert "--require-reach" in argv
    assert argv[argv.index("--out") + 1] == str(standing_out)
    assert argv[argv.index("--memo") + 1] == str(tmp_path / vu.standing_verdicts.MEMO_NAME)
    assert "--fresh-memo" not in argv
    assert argv[argv.index("--jobs") + 1] == "1"
    assert list(units()) == index


def test_the_echo_fill_takes_the_human_records_and_the_complaint_list_takes_the_id_set_beside_them(
    tmp_path, monkeypatch
):
    """Every echo round gets the same list of human echo records, because the echo fill reads nothing from machine records. The complaint list gets the human records and the set of every corpus id, because its absent-unit warning checks verdicts against machine units too."""
    code, index, _calls, _out, complaint_list_calls, echoes = _run_verdict_update(
        tmp_path, monkeypatch, complaints=True
    )
    assert code == 0
    assert len(echoes) >= 2
    assert all(units is echoes[0] for units in echoes)
    assert echoes[0] == [vu.echo_verdicts.echo_record(unit) for unit in index]
    [(argv, units, unit_ids)] = complaint_list_calls
    assert argv[argv.index("--corpus") + 1] == str(tmp_path / "review")
    assert units == index
    assert unit_ids == IDS


def test_the_verdict_update_forwards_the_cycles_standing_fill_width(tmp_path, monkeypatch):
    """The verdict update passes `--standing-fill-jobs` to the standing fill as `--jobs` and computes no width of its own."""
    code, _index, calls, _out, _complaint_list_calls, _echoes = _run_verdict_update(
        tmp_path, monkeypatch, ("--standing-fill-jobs", "6")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--jobs") + 1] == "6"


def test_the_verdict_update_passes_a_named_memo_and_the_fresh_form_through(tmp_path, monkeypatch):
    """The verdict update passes `--standing-memo` to the fill as `--memo`, and `--fresh-standing-memo` (which the cycle sets under `--fresh`) as `--fresh-memo`."""
    memo = tmp_path / "elsewhere" / "memo.ndjson.gz"
    code, _index, calls, _out, _complaint_list_calls, _echoes = _run_verdict_update(
        tmp_path, monkeypatch, ("--standing-memo", str(memo), "--fresh-standing-memo")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--memo") + 1] == str(memo)
    assert "--fresh-memo" in argv


def _carrying_verdict_update(tmp_path, monkeypatch, extra=()):
    """Run the verdict update with `--verdicts` and `--carry-out`, with every step stubbed. Return the exit code, the human index records, the carry's calls, and the merge's calls."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({**_payload(), "manifest_generated_at": "S0"}))
    index = [{"id": "u-DdcTojn1hba"}]
    carries = []
    merges = []

    def stream(_corpus, *, unit_ids=None):
        if unit_ids is not None:
            unit_ids.update(IDS)
        yield from (dict(unit) for unit in index)

    monkeypatch.setattr(vu.unit_index, "iter_human_units", stream)
    monkeypatch.setattr(
        vu.carry_verdicts,
        "main",
        lambda argv, current_units=None, current_ids=None: carries.append((argv, current_units, current_ids))
        or 0,
    )
    monkeypatch.setattr(vu.merge_verdicts, "main", lambda argv: merges.append(argv) or 0)
    monkeypatch.setattr(vu.echo_verdicts, "main", lambda argv, units=None: _write_out(argv))
    monkeypatch.setattr(vu.standing_verdicts, "main", lambda argv, unit_source=None: _write_out(argv))
    code = vu.main(
        [
            "--corpus",
            str(corpus),
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
    """The verdict update passes the carry the verdicts file, the human echo records, and every corpus id, because the orphaned count also checks machine ids. It names only the live corpus as `--current-corpus`, then merges the carried file."""
    code, index, carries, merges = _carrying_verdict_update(tmp_path, monkeypatch)
    assert code == 0
    [(argv, units, unit_ids)] = carries
    assert argv[: argv.index("--out")] == ["--verdicts", str(tmp_path / "verdicts.json")]
    assert argv[argv.index("--out") + 1] == str(tmp_path / "carried.json")
    assert argv[argv.index("--current-corpus") + 1] == str(tmp_path / "review")
    assert units == [vu.echo_verdicts.echo_record(unit) for unit in index]
    assert unit_ids == IDS
    assert merges[0][0] == str(tmp_path / "carried.json")


def test_the_staging_form_stops_after_the_carry(tmp_path, monkeypatch):
    """`--no-merge` never writes the live store: the carry runs and nothing after it does."""
    code, _index, carries, merges = _carrying_verdict_update(tmp_path, monkeypatch, ("--no-merge",))
    assert code == 0
    assert len(carries) == 1
    assert merges == []
