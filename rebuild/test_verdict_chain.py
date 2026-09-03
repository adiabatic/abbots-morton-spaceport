"""Tests for the verdict chain's contract with the steps it drives, which is thinner than it looks: the chain loads the surface's unit index once and hands every step the whole of it, and each step decides for itself what part of that list it has any business reading. The standing fill is the one step that decides visibly — the chain asks for its `--open-only --require-reach` form, narrowing what it writes from to the units that can move a fill while keeping the reach check over the whole domain, where a rule that has run out of windows fails the step — so what is asserted here is that the chain passes both flags and still hands the index over entire."""

import json
import pathlib

from rebuild.tools import verdict_chain as vc

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


def test_the_chain_runs_the_standing_fill_in_its_open_only_form(tmp_path, monkeypatch):
    """The narrowing and the refusal are both the tool's, so the chain still hands over the whole index and merely names the form."""
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
        ]
    )

    assert code == 0
    [(argv, units)] = calls
    assert "--open-only" in argv
    assert "--require-reach" in argv
    assert argv[argv.index("--out") + 1] == str(standing_out)
    assert units is index
