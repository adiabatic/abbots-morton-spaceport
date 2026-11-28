"""Tests for the alphabet switch, `AMS_RUNES` (`spec_load.RUNES_ENV`), which runs a build on a small alphabet for the fault kit: the spec `load_default_spec` narrows to, `alphabet.ALPHABET` taken from that spec when the module is imported, the alphabet token in every stamp over a build while the switch is set and in none while it is unset, and the refusal of every entry point that writes the live tree unless the fault kit started it."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.pipeline import (
    alphabet,
    fingerprint,
    fixtures,
    kernel_exec,
    run_build,
    run_build_gates,
    settle_memo_keys,
    spec_load,
)
from engine.review import build, landing
from engine.tools import artifact_cycle, cycle_timings, fault_kit

REPO_ROOT = Path(__file__).resolve().parent.parent
SMALL = "qsTea, qsUtter,qsDay_qsUtter,qsDay"
SMALL_RUNES = ("qsDay", "qsDay_qsUtter", "qsTea", "qsUtter")
SMALL_TOKEN = "qsDay,qsDay_qsUtter,qsTea,qsUtter"
CYCLE_RUN_ID = "cafef00d1234"


@pytest.fixture(scope="module")
def whole_spec():
    return spec_load.load_spec(
        spec_load.DEFAULT_RUNES_DIR, spec_load.DEFAULT_REGISTRY_PATH, spec_load.DEFAULT_SCHEMA_DIR
    )


def test_the_switch_narrows_the_default_spec_to_the_named_runes(monkeypatch, whole_spec):
    """The whole store loads and resolves before the narrowing, so the registry, its predicate classes and every kept rune's resolved records are the whole store's, and the runes kept are exactly the ones named: ·Tea·Oy, whose ·Tea is named, is left out."""
    monkeypatch.setenv(spec_load.RUNES_ENV, SMALL)
    small = spec_load.load_default_spec()
    assert tuple(sorted(small.runes)) == SMALL_RUNES
    assert list(small.runes) == [name for name in whole_spec.runes if name in SMALL_RUNES]
    assert small.registry == whole_spec.registry
    assert all(small.runes[name] == whole_spec.runes[name] for name in SMALL_RUNES)
    assert small.narrowed and not whole_spec.narrowed


def test_a_spec_holding_every_rune_of_the_store_is_not_narrowed(whole_spec):
    """A spec is marked narrowed (`model.ResolvedSpec.narrowed`), the mark the kern stage reads to leave stale records to the whole alphabet's build, when it holds fewer runes than its input or its input is already narrowed: naming every rune of the store gives a spec the kern stage treats as the whole alphabet, and narrowing a narrowed spec again keeps the mark."""
    assert not spec_load.narrowed(whole_spec, whole_spec.runes).narrowed
    small = spec_load.narrowed(whole_spec, SMALL_RUNES)
    assert spec_load.narrowed(small, SMALL_RUNES).narrowed


@pytest.mark.parametrize(
    ("names", "complaint"),
    [
        ((), "names no runes"),
        (("qsDay", "qsNope"), "does not hold: qsNope"),
        (("qsDay", "qsDay_qsUtter"), "without every component they name: qsDay_qsUtter"),
    ],
)
def test_the_switch_refuses_an_alphabet_other_than_the_one_it_names(whole_spec, names, complaint):
    with pytest.raises(spec_load.SpecError, match=complaint):
        spec_load.narrowed(whole_spec, names)


def test_the_alphabet_is_the_narrowed_spec_s_alphabet_when_imported_under_the_switch():
    """A fresh process under the switch imports `ALPHABET` as the narrowed spec's alphabet, the boundary tokens beside the named letters' code points, and the name the review build imports is that set."""
    probe = (
        "import json\n"
        "from engine.pipeline import alphabet, manual_pins, spec_load\n"
        "from engine.review import build\n"
        "print(json.dumps({\n"
        "    'alphabet': sorted(alphabet.ALPHABET),\n"
        "    'spec_codepoints': sorted(manual_pins.spec_codepoints(spec_load.load_default_spec())),\n"
        "    'imported': build.ALPHABET is alphabet.ALPHABET,\n"
        "}))\n"
    )
    environment = {**os.environ, spec_load.RUNES_ENV: SMALL}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    seen = json.loads(result.stdout)
    assert seen["alphabet"] == [0x0020, 0x00B7, 0x200C, 0xE652, 0xE653, 0xE67A]
    assert seen["spec_codepoints"] == seen["alphabet"]
    assert seen["imported"]
    assert alphabet.ALPHABET > frozenset(seen["alphabet"])


def _stamps(root: Path, record_dir: str) -> dict:
    """Every stamp the switch reaches, over `root` and fixed stand-ins for what the live checkout would feed them."""
    spec = fixtures.mini_spec()
    records = root / record_dir
    records.mkdir()
    fingerprint.write_stage_a(records, fingerprint.stage_a(root))
    inputs = settle_memo_keys.SettleMemoInputs(rune_digests={}, code="C", data="D")
    return {
        "tables": run_build.tables_inputs(),
        "skip": artifact_cycle.run_build_skip_lines(root),
        "skip_value": artifact_cycle.run_build_skip_fingerprint(root),
        "stage_a": (records / fingerprint.STAGE_A_FILENAME).read_text(),
        "records": records,
        "settle": settle_memo_keys.settle_memo_stamp(inputs, spec, "ss03", ["ss03"]),
    }


def test_the_alphabet_token_joins_every_stamp_only_while_the_switch_is_set(monkeypatch, tmp_path):
    """With the switch unset, the tables' stamp, the run_build skip key, the Stage A record and the settle memo's stamp are byte for byte what the code before the switch computed over the same inputs, the values pinned here. With it set, each carries the alphabet token and nothing else changes, and a Stage A record from either side never reads current to a process on the other."""
    monkeypatch.setattr(fingerprint, "tables_value", lambda root: "T")
    monkeypatch.setattr(fingerprint, "FORMAT", "stage-a-format")
    monkeypatch.setattr(kernel_exec, "enumeration_tokens", lambda: ["simulated-prospect"])
    monkeypatch.setattr(kernel_exec, "settlement_flags", lambda modes=None: ["--candidacy-prospect"])
    monkeypatch.setattr(spec_load, "spec_structure_digest", lambda spec: "S")
    monkeypatch.setattr(spec_load, "capability_features", lambda spec: ["ss03"])
    monkeypatch.delenv(spec_load.RUNES_ENV, raising=False)
    whole = _stamps(tmp_path, "whole")
    assert whole["tables"] == "T+simulated-prospect"
    assert whole["skip_value"] == "4ee1818f96d356343ac7ba1fc0128cd1c9bf7521e971fbf68848a082f8b2fd20"
    assert whole["stage_a"] == (
        '{\n  "format": "stage-a-format",\n'
        '  "data": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n'
        '  "pipeline_code": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"\n}\n'
    )
    assert whole["settle"].value == "d7de3032c782a2627fa15f96f74c16b4ffbe0e254072278fb1d63466bbc092c1"

    monkeypatch.setenv(spec_load.RUNES_ENV, SMALL)
    small = _stamps(tmp_path, "small")
    line = f"alphabet\t{SMALL_TOKEN}"
    assert small["tables"] == f"{whole['tables']}+alphabet={SMALL_TOKEN}"
    assert small["skip"] == [*whole["skip"], line]
    assert json.loads(small["stage_a"]) == {**json.loads(whole["stage_a"]), "alphabet": SMALL_TOKEN}
    assert small["settle"].lines == (*whole["settle"].lines, line)
    assert fingerprint.read_stage_a(small["records"]) == fingerprint.stage_a(tmp_path)
    assert fingerprint.read_stage_a(whole["records"]) != fingerprint.stage_a(tmp_path)

    monkeypatch.delenv(spec_load.RUNES_ENV)
    assert fingerprint.read_stage_a(whole["records"]) == fingerprint.stage_a(tmp_path)
    assert fingerprint.read_stage_a(small["records"]) != fingerprint.stage_a(tmp_path)


@pytest.mark.parametrize(
    ("switch", "cycle_run", "refuses"),
    [
        (None, None, False),
        (SMALL, None, True),
        (SMALL, CYCLE_RUN_ID, True),
        (SMALL, "fault-kit", False),
    ],
)
def test_only_the_fault_kit_runs_with_the_switch_set(monkeypatch, switch, cycle_run, refuses):
    assert spec_load.CYCLE_RUN_ENV == cycle_timings.CYCLE_RUN_ENV
    assert spec_load.FAULT_KIT_RUN == fault_kit.CYCLE_RUN_VALUE
    for name, value in ((spec_load.RUNES_ENV, switch), (spec_load.CYCLE_RUN_ENV, cycle_run)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert (spec_load.small_alphabet_refusal("run_build") is not None) is refuses


def _refuse_to_run(*args, **kwargs):
    raise AssertionError("the entry point ran past its refusal")


@pytest.mark.parametrize(
    "entry",
    [
        "the artifact cycle",
        "run_build",
        "the review build",
        "the land",
    ],
)
def test_each_entry_point_that_writes_the_live_tree_refuses_the_switch(monkeypatch, tmp_path, entry):
    """Each entry point refuses before it reads its command line, here under a cycle's run id, as a shell that leaked the switch into a pass would. What each would run next is replaced by a failure, so a refusal that let it through fails the test without writing anything."""
    monkeypatch.setenv(spec_load.RUNES_ENV, SMALL)
    monkeypatch.setenv(spec_load.CYCLE_RUN_ENV, CYCLE_RUN_ID)
    monkeypatch.setattr(artifact_cycle, "pass_lock", _refuse_to_run)
    monkeypatch.setattr(artifact_cycle, "_run_pass", _refuse_to_run)
    monkeypatch.setattr(run_build_gates, "_run_cli", _refuse_to_run)
    monkeypatch.setattr(gc, "freeze", _refuse_to_run)
    monkeypatch.setattr(gc, "disable", _refuse_to_run)
    monkeypatch.setattr(build, "build_table_diff", _refuse_to_run)
    monkeypatch.setattr(landing, "land", _refuse_to_run)
    scratch = [str(tmp_path / name) for name in ("autosave.json", "run", "corpus")]
    run = {
        "the artifact cycle": lambda: artifact_cycle.main(["--review-out", str(tmp_path / "corpus")]),
        "run_build": run_build.cli,
        "the review build": lambda: build.main(["--out", str(tmp_path / "corpus")]),
        "the land": lambda: landing.main(
            [
                "--autosave",
                scratch[0],
                "--run-dir",
                scratch[1],
                "--corpus",
                scratch[2],
            ]
        ),
    }[entry]
    with pytest.raises(SystemExit) as refused:
        run()
    assert isinstance(refused.value.code, str)
    assert refused.value.code.startswith(f"{entry} refuses: {spec_load.RUNES_ENV} is set")
