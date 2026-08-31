"""The on-demand deep sweep's decisions, with the sweep itself stubbed out: what it refuses to run against, what it records when it passes, what it clears when it fails, and the belt green it hands forward. The sweep it drives is run_m1.run_font_conformance, which the font-facing gates already exercise."""

import json

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import deep_sweep


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A repo root the tool believes in: a behavior-class sidecar to arm on, a tables stamp treated as current, and every green record redirected into tmp_path so nothing touches rebuild/out."""
    from rebuild.pipeline.emit_gsub import BEHAVIOR_CLASSES_FORMAT

    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    (m1 / "behavior_classes.json").write_text(
        json.dumps({"format": BEHAVIOR_CLASSES_FORMAT, "classes": ["namer-dot", "settle:bk0-la2"]})
    )
    for rel in ac.COMPILE_CODE_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")
    monkeypatch.setattr(deep_sweep, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "DEEP_SWEEP_GREEN", tmp_path / "deep-sweep-green.json")
    monkeypatch.setattr(deep_sweep, "DEEP_SWEEP_GREEN", tmp_path / "deep-sweep-green.json")
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(deep_sweep, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(deep_sweep, "tables_stamped", lambda: True)
    return tmp_path


def _stub_sweep(monkeypatch, summary, swept=None):
    def fake(max_length, jobs, summary_name):
        if swept is not None:
            swept.append((max_length, jobs, summary_name))
        return summary

    monkeypatch.setattr(deep_sweep.run_m1, "run_font_conformance", fake)


def test_a_root_without_a_behavior_class_sidecar_is_refused(bench, monkeypatch):
    (bench / "rebuild" / "out" / "m1" / "behavior_classes.json").unlink()
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    with pytest.raises(SystemExit, match="behavior-class sidecar"):
        deep_sweep.main([])


def test_a_stale_tables_stamp_is_refused(bench, monkeypatch):
    """The precondition is artifact identity — the serialized enumeration stamped from exactly the sources on disk — never a green receipt: a --gates-only pass writes no green yet leaves a perfectly sweepable font, and a red interactive run deletes the receipt without changing a byte of the artifacts."""
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    monkeypatch.setattr(deep_sweep, "tables_stamped", lambda: False)
    with pytest.raises(SystemExit, match="stale relative to the runes"):
        deep_sweep.main([])


def test_tables_stamped_asks_the_enumerations_own_stamp(monkeypatch):
    calls: list = []
    monkeypatch.setattr(deep_sweep.run_m1, "tables_inputs", lambda: "stamp")
    monkeypatch.setattr(
        deep_sweep.run_m1,
        "serialized_tables",
        lambda out_dir, inputs: calls.append((out_dir, inputs)),
    )
    assert deep_sweep.tables_stamped() is False
    assert calls == [(deep_sweep.run_m1.OUT_DIR, "stamp")]
    monkeypatch.setattr(deep_sweep.run_m1, "serialized_tables", lambda out_dir, inputs: {})
    assert deep_sweep.tables_stamped() is True


def test_a_horizon_below_the_belt_is_refused(bench, monkeypatch):
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    with pytest.raises(SystemExit, match="shallower than the per-edit belt"):
        deep_sweep.main(["--horizon", str(ac.CONFORM_HORIZON_DEFAULT - 1)])
    assert swept == []


def test_a_green_run_records_its_horizon_and_hands_the_belt_its_green(bench, monkeypatch):
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    assert deep_sweep.main(["--horizon", "6", "--jobs", "3"]) == 0
    assert swept == [(6, 3, deep_sweep.SUMMARY_NAME)]
    record = ac.read_green_record(bench / "deep-sweep-green.json")
    assert record is not None
    assert record["horizon"] == 6
    assert record["fingerprint"] == ac.deep_sweep_skip_fingerprint(bench)
    assert "class:namer-dot" in record["files"]
    belt = ac.read_green_record(bench / "conform-green.json")
    assert belt is not None
    assert belt["fingerprint"] == ac.conform_skip_fingerprint(bench, ac.CONFORM_HORIZON_DEFAULT)


def test_a_red_run_records_nothing_and_clears_a_contradicted_green(bench, monkeypatch):
    fingerprint = ac.deep_sweep_skip_fingerprint(bench)
    assert fingerprint is not None
    ac.record_deep_sweep_green(fingerprint, 5, path=bench / "deep-sweep-green.json")
    _stub_sweep(monkeypatch, {"pass": False, "divergences": 2})
    assert deep_sweep.main([]) == 1
    assert ac.read_green_record(bench / "deep-sweep-green.json") is None
    assert ac.read_green_record(bench / "conform-green.json") is None


def test_a_build_landing_mid_sweep_records_nothing(bench, monkeypatch, capsys):
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    real = ac.deep_sweep_skip_fingerprint
    calls = [0]

    def moving(root=bench):
        calls[0] += 1
        return f"{real(root)}-{calls[0]}"

    monkeypatch.setattr(deep_sweep, "deep_sweep_skip_fingerprint", moving)
    assert deep_sweep.main([]) == 0
    assert ac.read_green_record(bench / "deep-sweep-green.json") is None
    assert "inputs changed while it ran" in capsys.readouterr().out


def test_status_exits_on_whether_the_sweep_is_current(bench, monkeypatch, capsys):
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    assert deep_sweep.main(["--status"]) == 1
    assert "never-run" in capsys.readouterr().out
    deep_sweep.main(["--horizon", "5"])
    assert deep_sweep.main(["--status"]) == 0
    assert "current" in capsys.readouterr().out
    assert deep_sweep.main(["--status", "--horizon", "7"]) == 1
