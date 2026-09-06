"""Tests for the verdict chain's contract with the steps it drives, which is thinner than it looks: the chain loads the surface's unit index once and hands every step the whole of it, and each step decides for itself what part of that list it has any business reading. The standing fill is the one step that decides visibly — the chain asks for its `--open-only --require-reach` form, narrowing what it writes from to the units that can move a fill while keeping the reach check over the whole domain, where a rule that has run out of windows fails the step — so what is asserted here is that the chain passes both flags and still hands the index over entire."""

import json
import pathlib

from rebuild.review import journal
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
    """The cycle surfaces the chain's steps the way it surfaces every other child's: the phase line says which step is running and the `[t]` line carrying the same label closes it with the duration, which is the pairing `console.Digest` prints one line for. A step that refused keeps the `[chain] ` prefix instead, because that is a result rather than a phase and it is what the driver still splits the plumbing report on."""
    assert vc._run("carry", lambda: 0) == 0
    assert vc._run("merge", lambda: 3) == 3
    lines = capsys.readouterr().out.splitlines()
    events = [console.parse_line(line) for line in lines]
    assert [event.name for event in events if isinstance(event, console.Phase)] == ["carry", "merge"]
    assert [event.label for event in events if isinstance(event, console.Timing)] == ["carry", "merge"]
    assert lines[-1] == f"{console.FAILED_LINE}merge (exit 3)"


def _chain(tmp_path, monkeypatch, extra=()):
    """The chain over a stub surface with every step but the standing fill stubbed out, returning its exit code, the index it loaded, the standing fill's calls, and where the fill was told to write."""
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    master = tmp_path / "master.json"
    master.write_text(json.dumps(_payload()))
    index = [{"id": "u-1"}, {"id": "u-2"}]
    calls = []

    monkeypatch.setattr(vc.unit_index, "load_units", lambda _surface: index)
    monkeypatch.setattr(vc.merge_verdicts, "main", lambda _argv: 0)
    monkeypatch.setattr(vc.echo_verdicts, "main", lambda argv, units=None: _write_out(argv))

    def standing(argv, units=None):
        calls.append((argv, units))
        return _write_out(argv)

    monkeypatch.setattr(vc.standing_verdicts, "main", standing)

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
            "--no-complaints",
            *extra,
        ]
    )
    return code, index, calls, standing_out


def test_the_chain_runs_the_standing_fill_in_its_open_only_form(tmp_path, monkeypatch):
    """The narrowing and the refusal are both the tool's, so the chain still hands over the whole index and merely names the form — and hands it the memo beside the surface directory, outside it, so a surface rebuild never clears it."""
    code, index, calls, standing_out = _chain(tmp_path, monkeypatch)
    assert code == 0
    [(argv, units)] = calls
    assert "--open-only" in argv
    assert "--require-reach" in argv
    assert argv[argv.index("--out") + 1] == str(standing_out)
    assert argv[argv.index("--memo") + 1] == str(tmp_path / vc.standing_verdicts.MEMO_NAME)
    assert "--fresh-memo" not in argv
    assert units is index


def test_the_chain_passes_a_named_memo_and_the_fresh_form_through(tmp_path, monkeypatch):
    """`--standing-memo` names where the fill keeps its decisions and `--fresh-standing-memo` — the cycle's `--fresh` — has it evaluate everything and rewrite the file."""
    memo = tmp_path / "elsewhere" / "memo.ndjson.gz"
    code, _index, calls, _out = _chain(
        tmp_path, monkeypatch, ("--standing-memo", str(memo), "--fresh-standing-memo")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--memo") + 1] == str(memo)
    assert "--fresh-memo" in argv


def _carrying_chain(tmp_path, monkeypatch, extra=()):
    """The chain in its carrying form over a stub snapshot that still names its units positionally, the carry itself stubbed out, returning the exit code and the journal it was handed."""
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    snapshot = tmp_path / "review-pre-abc"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text(json.dumps({"generated_at": "S0"}))
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({**_payload(), "manifest_generated_at": "S0"}))
    journal_path = tmp_path / "verdicts-journal.ndjson"
    journal.record_transition(
        journal_path,
        source="merge",
        stamp="S0",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[{"unit": "u-0001", "verdict": "approve", "note": "", "at": "t1"}],
    )
    monkeypatch.setattr(vc.unit_index, "load_units", lambda _surface: [{"id": "u-DdcTojn1hba"}])
    monkeypatch.setattr(vc.carry_verdicts, "main", lambda _argv, current_units=None: 0)
    monkeypatch.setattr(
        vc.carry_verdicts,
        "id_migration",
        lambda root: {"u-0001": "u-DdcTojn1hba"} if pathlib.Path(root) == snapshot else {},
    )
    monkeypatch.setattr(vc.merge_verdicts, "main", lambda _argv: 0)
    monkeypatch.setattr(vc.echo_verdicts, "main", lambda argv, units=None: _write_out(argv))
    monkeypatch.setattr(vc.standing_verdicts, "main", lambda argv, units=None: _write_out(argv))
    code = vc.main(
        [
            "--surface",
            str(surface),
            "--source",
            str(snapshot),
            str(verdicts),
            "--carry-out",
            str(tmp_path / "carried.json"),
            "--autosave",
            str(tmp_path / "verdicts-autosave.json"),
            "--journal",
            str(journal_path),
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
    return code, journal_path


def _journal_units(journal_path):
    return [entry["unit"] for entry in journal._iter_entries(journal_path) if entry["kind"] == "set"]


def test_the_carry_step_migrates_the_journal_through_the_snapshots_ids(tmp_path, monkeypatch, capsys):
    """The cutover rewrite rides the carry step: the snapshot's positional-to-content mapping rewrites the journal's lines under that snapshot's stamp, and the digest reads what happened off the carry section."""
    code, journal_path = _carrying_chain(tmp_path, monkeypatch)
    assert code == 0
    assert _journal_units(journal_path) == ["u-DdcTojn1hba"]
    assert "1 lines under 1 events stamped S0 carried onto content ids" in capsys.readouterr().out


def test_the_rehearsal_form_leaves_the_journal_alone(tmp_path, monkeypatch):
    """`--no-merge` never writes the live store, and the journal is the store's own history, so the rehearsal carries without migrating."""
    code, journal_path = _carrying_chain(tmp_path, monkeypatch, ("--no-merge",))
    assert code == 0
    assert _journal_units(journal_path) == ["u-0001"]
