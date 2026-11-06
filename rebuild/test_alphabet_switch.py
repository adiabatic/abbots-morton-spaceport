"""Tests for the alphabet switch, `AMS_M1_RUNES` (`spec_load.M1_RUNES_ENV`), which runs an M1 build on a small alphabet for the fault kit: the spec `load_default_spec` narrows to, `baseline_subset.M1_ALPHABET` taken from that spec when the module is imported, the alphabet token in every stamp over a build while the switch is set and in none while it is unset, and the refusal of every entry point that writes the live tree unless the fault kit started it."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rebuild.pipeline import (
    baseline_subset,
    fingerprint,
    fixtures,
    kernel_exec,
    oracle_cache,
    run_m1,
    run_m1_gates,
    spec_load,
)
from rebuild.review import build, landing, unit_cache
from rebuild.tools import artifact_cycle, cycle_timings, fault_kit, scaling_series, verdict_update

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
    """The whole store loads and resolves before the narrowing, so the registry, its predicate classes and every kept rune's resolved records are the whole store's, and the runes kept are the ones `scaling_series.sub_spec` keeps for the same names: ·Tea·Oy, whose ·Oy is not named, is left out."""
    monkeypatch.setenv(spec_load.M1_RUNES_ENV, SMALL)
    narrowed = spec_load.load_default_spec()
    assert tuple(sorted(narrowed.runes)) == SMALL_RUNES
    assert list(narrowed.runes) == [name for name in whole_spec.runes if name in SMALL_RUNES]
    assert narrowed.registry == whole_spec.registry
    assert all(narrowed.runes[name] == whole_spec.runes[name] for name in SMALL_RUNES)
    assert narrowed.runes == scaling_series.sub_spec(whole_spec, list(SMALL_RUNES), len(SMALL_RUNES)).runes


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


def test_m1_alphabet_is_the_narrowed_spec_s_alphabet_when_imported_under_the_switch():
    """A fresh process under the switch imports `M1_ALPHABET` as the narrowed spec's alphabet, the boundary tokens beside the named letters' code points, and both the name the review build imports and `filter_table`'s default argument are that set."""
    probe = (
        "import json\n"
        "from rebuild.pipeline import baseline_subset, manual_pins, spec_load\n"
        "from rebuild.review import build\n"
        "print(json.dumps({\n"
        "    'alphabet': sorted(baseline_subset.M1_ALPHABET),\n"
        "    'migrated': sorted(manual_pins.migrated_alphabet(spec_load.load_default_spec())),\n"
        "    'default': baseline_subset.filter_table.__defaults__[0] is baseline_subset.M1_ALPHABET,\n"
        "    'imported': build.M1_ALPHABET is baseline_subset.M1_ALPHABET,\n"
        "}))\n"
    )
    environment = {**os.environ, spec_load.M1_RUNES_ENV: SMALL}
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
    assert seen["migrated"] == seen["alphabet"]
    assert seen["default"] and seen["imported"]
    assert baseline_subset.M1_ALPHABET > frozenset(seen["alphabet"])


def _stamps(root: Path, record_dir: str) -> dict:
    """Every stamp the switch reaches, over `root` and fixed stand-ins for what the live checkout would feed them."""
    spec = fixtures.mini_spec()
    records = root / record_dir
    records.mkdir()
    fingerprint.write_stage_a(records, fingerprint.stage_a(root))
    inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="C", data="D")
    return {
        "tables": run_m1.tables_inputs(),
        "skip": artifact_cycle.run_m1_skip_lines(root),
        "skip_value": artifact_cycle.run_m1_skip_fingerprint(root),
        "stage_a": (records / fingerprint.STAGE_A_FILENAME).read_text(),
        "records": records,
        "settle": oracle_cache.settle_memo_stamp(inputs, spec, "ss03", ["ss03"]),
        "unit": unit_cache.environment_stamp(
            root,
            spec,
            root / "subsets",
            root / "before.otf",
            root / "junior.otf",
            "H",
            {"default": "X", "ss03": "X"},
            identical_classes=["b", "a"],
            draft_lines=["site_corpus\tP", "draft_harness\tQ"],
        ),
    }


def test_the_alphabet_token_joins_every_stamp_only_while_the_switch_is_set(monkeypatch, tmp_path):
    """With the switch unset, the tables' stamp, the run_m1 skip key, the Stage A record, the settle memo's stamp and the unit store's stamp are byte for byte what the code before the switch computed over the same inputs, the values pinned here. With it set, each carries the alphabet token and nothing else changes, and a Stage A record from either side never reads current to a process on the other."""
    monkeypatch.setattr(fingerprint, "tables_value", lambda root: "T")
    monkeypatch.setattr(fingerprint, "FORMAT", "stage-a-format")
    monkeypatch.setattr(kernel_exec, "enumeration_tokens", lambda: ["simulated-prospect"])
    monkeypatch.setattr(kernel_exec, "settlement_flags", lambda modes=None: ["--candidacy-prospect"])
    monkeypatch.setattr(spec_load, "spec_structure_digest", lambda spec: "S")
    monkeypatch.setattr(spec_load, "capability_features", lambda spec: ["ss03"])
    monkeypatch.setattr(unit_cache, "STORE_FORMAT", "unit-format")
    monkeypatch.setattr(unit_cache, "ACCEPTANCE_CONFIGS", ("default", "ss03"))
    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
    whole = _stamps(tmp_path, "whole")
    assert whole["tables"] == "T+simulated-prospect"
    assert whole["skip_value"] == "032c8de293443828f0227d0f951de648a2e63462d76c67934a7481c8f71eea99"
    assert whole["stage_a"] == (
        '{\n  "format": "stage-a-format",\n'
        '  "data": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n'
        '  "baselines": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",\n'
        '  "pipeline_code": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"\n}\n'
    )
    assert whole["settle"].value == "d5d85b5add5cb3dc9e984cc472db4c1ade6481f1b9c5577a2729577322a1c91a"
    assert whole["unit"].value == "5c2b5700fd1989c967bca9a05e3b909410218fce1c7d7f86a4948810d2f82913"

    monkeypatch.setenv(spec_load.M1_RUNES_ENV, SMALL)
    small = _stamps(tmp_path, "small")
    line = f"alphabet\t{SMALL_TOKEN}"
    assert small["tables"] == f"{whole['tables']}+alphabet={SMALL_TOKEN}"
    assert small["skip"] == [*whole["skip"], line]
    assert json.loads(small["stage_a"]) == {**json.loads(whole["stage_a"]), "alphabet": SMALL_TOKEN}
    assert small["settle"].lines == (*whole["settle"].lines, line)
    assert small["unit"].lines == (*whole["unit"].lines, line)
    assert fingerprint.read_stage_a(small["records"]) == fingerprint.stage_a(tmp_path)
    assert fingerprint.read_stage_a(whole["records"]) != fingerprint.stage_a(tmp_path)

    monkeypatch.delenv(spec_load.M1_RUNES_ENV)
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
    for name, value in ((spec_load.M1_RUNES_ENV, switch), (spec_load.CYCLE_RUN_ENV, cycle_run)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert (spec_load.small_alphabet_refusal("run_m1") is not None) is refuses


def _refuse_to_run(*args, **kwargs):
    raise AssertionError("the entry point ran past its refusal")


@pytest.mark.parametrize(
    "entry",
    [
        "the artifact cycle",
        "run_m1",
        "the subset refilter",
        "the review build",
        "the verdict update",
        "the land",
    ],
)
def test_each_entry_point_that_writes_the_live_tree_refuses_the_switch(monkeypatch, tmp_path, entry):
    """Each entry point refuses before it reads its command line, here under a cycle's run id, as a shell that leaked the switch into a pass would. What each would run next is replaced by a failure, so a refusal that let it through fails the test without writing anything."""
    monkeypatch.setenv(spec_load.M1_RUNES_ENV, SMALL)
    monkeypatch.setenv(spec_load.CYCLE_RUN_ENV, CYCLE_RUN_ID)
    monkeypatch.setattr(artifact_cycle, "pass_lock", _refuse_to_run)
    monkeypatch.setattr(artifact_cycle, "_run_pass", _refuse_to_run)
    monkeypatch.setattr(run_m1_gates, "_run_cli", _refuse_to_run)
    monkeypatch.setattr(gc, "freeze", _refuse_to_run)
    monkeypatch.setattr(gc, "disable", _refuse_to_run)
    monkeypatch.setattr(build, "build_m1", _refuse_to_run)
    monkeypatch.setattr(build, "build_table_diff", _refuse_to_run)
    monkeypatch.setattr(verdict_update.unit_index, "iter_human_units", _refuse_to_run)
    monkeypatch.setattr(landing, "land", _refuse_to_run)
    monkeypatch.setattr(baseline_subset, "check_font_provenance", _refuse_to_run)
    monkeypatch.setattr(baseline_subset, "refresh", _refuse_to_run)
    scratch = [str(tmp_path / name) for name in ("autosave.json", "journal.ndjson", "run", "corpus")]
    run = {
        "the artifact cycle": lambda: artifact_cycle.main(["--review-out", str(tmp_path / "corpus")]),
        "run_m1": run_m1.cli,
        "the subset refilter": baseline_subset.main,
        "the review build": lambda: build.main(["--out", str(tmp_path / "corpus")]),
        "the verdict update": lambda: verdict_update.main(
            ["--corpus", scratch[3], "--autosave", scratch[0], "--journal", scratch[1], "--no-merge"]
        ),
        "the land": lambda: landing.main(
            [
                "--autosave",
                scratch[0],
                "--journal",
                scratch[1],
                "--run-dir",
                scratch[2],
                "--corpus",
                scratch[3],
            ]
        ),
    }[entry]
    with pytest.raises(SystemExit) as refused:
        run()
    assert isinstance(refused.value.code, str)
    assert refused.value.code.startswith(f"{entry} refuses: {spec_load.M1_RUNES_ENV} is set")
