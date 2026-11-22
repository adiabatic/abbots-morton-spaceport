"""Tests of the corpus build interface, `rebuild/review/corpus_exec.py`, the Python side of the Rust build of the review corpus's parent (`rebuild/CORPUS-PARENT-PORT-PLAN.md`): the switch, the vocabulary and the fields the build input carries, which the crate's reader names too, the Rust build behind the switch writing the Python build's corpus over the mini bundle's `first`, `warm` and `incremental` scenarios, and its refusals of the environments it cannot build in. From the plan's `opt-in-switch` task on, a change to the Python a build stage mirrors fails the mini test until the crate follows it."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from rebuild.review import build, corpus_exec, units_exec
from rebuild.tools import cycle_timings, memory_tally, units_stage_fixtures

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_SOURCE = REPO_ROOT / "rebuild" / "review-rs" / "src" / "corpus" / "input.rs"
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"
REUSED = re.compile(r"reused (\d[\d,]*) of (\d[\d,]*) units from the cache")


def test_the_switch_reads_the_environment_and_the_build_runs_both_engines(monkeypatch):
    """`corpus_exec.engine()` reads `AMS_CORPUS_BUILD_ENGINE` at call time, `python` by default, and refuses a value the switch does not know; the build runs either engine."""
    monkeypatch.delenv(corpus_exec.ENGINE_VARIABLE, raising=False)
    assert corpus_exec.engine() == "python"
    monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, "cobol")
    with pytest.raises(ValueError, match="cobol"):
        corpus_exec.engine()
    for value in corpus_exec.SWITCH_VALUES:
        monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, value)
        assert corpus_exec.engine() == value
    assert corpus_exec.SWITCH_VALUES == ("python", "rust")
    assert corpus_exec.ENGINES == ("python", "rust")


def _rust_names(name: str) -> tuple[str, ...]:
    source = INPUT_SOURCE.read_text(encoding="utf-8")
    block = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", source, re.S)
    assert block is not None, f"input.rs no longer declares {name}"
    return tuple(re.findall(r'"([^"]+)"', block.group(1)))


def test_the_vocabulary_resolves_and_the_build_reads_the_same_names_and_fields():
    """Every `VOCABULARY` name is a constant the typed encoding can carry, and the crate's reader refuses an input without the same names (`corpus::input::VOCABULARY`), reads the same fields in the same order (`INPUT_FIELDS`) and the same format."""
    values = corpus_exec.vocabulary()
    assert tuple(values) == corpus_exec.VOCABULARY
    for name, value in values.items():
        assert units_exec.decode(json.loads(units_exec.compact(units_exec.encode(value)))) is not None, name
    assert _rust_names("VOCABULARY") == corpus_exec.VOCABULARY
    assert _rust_names("INPUT_FIELDS") == corpus_exec.INPUT_FIELDS
    source = INPUT_SOURCE.read_text(encoding="utf-8")
    assert re.search(rf'pub const INPUT_FORMAT: &str = "{re.escape(corpus_exec.INPUT_FORMAT)}";', source)


def _build_mini(out: Path, inputs: units_stage_fixtures.BuildInputs) -> dict:
    return build.build_m1(
        out,
        audit_path=inputs.audit,
        ledger_path=inputs.ledger,
        subset_dir=inputs.subset_dir,
        after_font=inputs.after_font,
        spec_root=inputs.spec_root,
        subset_pack=inputs.subset_pack,
        jobs=2,
    )


def _tree(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(Path(path).rglob("*")) if p.is_file()
    }


def test_the_rust_build_writes_the_python_build_s_corpus_over_the_mini_s_first_warm_and_incremental_scenarios(
    mini_bundle, tmp_path, capfd, monkeypatch
):
    """Behind the switch, `build_m1` runs the Rust build, which writes the Python build's corpus byte for byte, as `rebuild/test_unit_cache.py`'s tests compare two builds, over three of the mini source's scenarios (`units_stage_fixtures`' § Scenarios): `first`, from an empty directory; `warm`, over the Python `first` build with the same inputs; and `incremental`, over it with the scenario's edited audit and ledger (`_incremental_inputs`), which reuses some units and recomputes others. Both sides build at two jobs under the Rust units engine, so the Python build's unit store stamps the `units_engine` line the Rust build's always does. The Rust build's load line names it and it files one `corpus-build-rs` pool record a build."""
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "rust")
    inputs = units_stage_fixtures.BuildInputs(
        MINI_AUDIT, mini_bundle.ledger, MINI, mini_bundle.subset_pack, MINI_FONT, mini_bundle.spec_root
    )
    for engine in corpus_exec.ENGINES:
        monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, engine)
        _build_mini(tmp_path / engine / "first", inputs)
        err = capfd.readouterr().err
        assert ("\tbuild=rust " in err) == (engine == "rust"), err
    base = tmp_path / "python" / "first"
    edited = units_stage_fixtures._incremental_inputs(
        units_stage_fixtures.BasePrior("mini", base, inputs), tmp_path / "incremental-inputs"
    )
    for name, scenario_inputs in (("warm", inputs), ("incremental", edited)):
        reused = {}
        for engine in corpus_exec.ENGINES:
            monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, engine)
            out = tmp_path / engine / name
            shutil.copytree(base, out)
            _build_mini(out, scenario_inputs)
            match = REUSED.search(capfd.readouterr().err)
            assert match, f"the {engine} {name} build did not report what it reused"
            reused[engine] = tuple(int(group.replace(",", "")) for group in match.groups())
        assert reused["rust"] == reused["python"], name
        done, total = reused["python"]
        assert (done == total) if name == "warm" else (0 < done < total), (name, reused)
    for name in ("first", "warm", "incremental"):
        assert _tree(tmp_path / "rust" / name) == _tree(tmp_path / "python" / name), name
    records = [json.loads(line) for line in cycle_timings.JOURNAL.read_text(encoding="utf-8").splitlines()]
    pools = [record for record in records if record["unit"] == "corpus-build-rs"]
    assert [(record["width"], record["worker_reading"]) for record in pools] == [(2, "footprint")] * 3, pools


@pytest.mark.parametrize(
    ("variable", "value"),
    [(units_exec.ENGINE_VARIABLE, "python"), (memory_tally.TALLY_ENV, "1")],
    ids=["python-units-engine", "memory-tally"],
)
def test_the_rust_build_refuses_the_python_units_engine_and_the_memory_tally(
    variable, value, mini_bundle, tmp_path, monkeypatch
):
    """The Rust build computes every unit with the Rust units engine and holds none of the Python objects the debug tally reads, so behind the switch `build_m1` refuses `AMS_CORPUS_UNITS_ENGINE=python` and `AMS_CORPUS_MEMORY_TALLY=1` before it writes anything, naming both variables."""
    monkeypatch.setenv(units_exec.ENGINE_VARIABLE, "rust")
    monkeypatch.delenv(memory_tally.TALLY_ENV, raising=False)
    monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, "rust")
    monkeypatch.setenv(variable, value)
    inputs = units_stage_fixtures.BuildInputs(
        MINI_AUDIT, mini_bundle.ledger, MINI, mini_bundle.subset_pack, MINI_FONT, mini_bundle.spec_root
    )
    out = tmp_path / "corpus"
    with pytest.raises(SystemExit) as stop:
        _build_mini(out, inputs)
    message = str(stop.value.code)
    assert f"{variable}={value}" in message and f"{corpus_exec.ENGINE_VARIABLE}=rust" in message, message
    assert not out.exists()
