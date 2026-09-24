"""The scratch-build harness's request seam (`rebuild/tools/scratch_build.py`): which configurations a `--configs` hands the build and the oracle, what the JSON line reports of them, and the two refusals that fire before a spec is resolved. Path arithmetic and stubs only — the crate-backed narrowed build is `rebuild/test_kernel_exec.py`'s."""

import json
from types import SimpleNamespace

import pytest

from rebuild.pipeline import compile_font, conform, emit_gpos, emit_gsub, oracle, readback, run_m1
from rebuild.tools import scratch_build


class Reached(Exception):
    """Raised from a stubbed stage a refused invocation must never reach."""


def test_the_unnarrowed_request_is_the_settlement_set_and_the_acceptance_set():
    """A plain run keeps checking the ss10 overlay arm: the build gets the settlement set and the oracle the acceptance set, stated rather than left to fall through to the build's."""
    build, oracle = scratch_build.request_configs("")
    assert build == list(conform.SETTLEMENT_CONFIGS)
    assert oracle == list(conform.ACCEPTANCE_CONFIGS)
    assert "ss10" in oracle and "ss10" not in build


def test_a_named_set_narrows_the_build_and_the_oracle_alike_in_the_order_named():
    assert scratch_build.request_configs("ss03,default") == (["ss03", "default"], ["ss03", "default"])
    assert scratch_build.request_configs("default") == (["default"], ["default"])


def test_an_unknown_configuration_is_refused_before_anything_is_resolved(monkeypatch, tmp_path):
    """An unknown token is refused with the offered tokens named, before the spec is loaded, before a table is built and before anything is written under the out dir."""

    def never(*args, **rest):
        raise Reached

    monkeypatch.setattr(scratch_build, "load_spec", never)
    monkeypatch.setattr(run_m1, "build_tables", never)
    with pytest.raises(SystemExit) as refused:
        scratch_build.main(["glyph_data/runes", str(tmp_path), "--configs", "ss99"])
    assert "ss99" in str(refused.value)
    assert all(config in str(refused.value) for config in conform.SETTLEMENT_CONFIGS)
    assert not sorted(tmp_path.iterdir())


def test_every_build_is_refused_under_the_live_table_directory(monkeypatch, tmp_path):
    """A build into rebuild/out/m1, or anywhere under it, is refused by name whether or not `--configs` narrows it, before the spec is loaded; any path elsewhere comes back resolved."""

    def never(*args, **rest):
        raise Reached

    with pytest.raises(SystemExit, match="rebuild/out/m1"):
        scratch_build.scratch_out_dir(str(scratch_build.M1_OUT / "candidate"))
    with pytest.raises(SystemExit, match="rebuild/out/m1"):
        scratch_build.scratch_out_dir(str(scratch_build.M1_OUT))
    assert scratch_build.scratch_out_dir(str(tmp_path)) == tmp_path.resolve()
    monkeypatch.setattr(scratch_build, "load_spec", never)
    monkeypatch.setattr(run_m1, "build_tables", never)
    for narrowing in ([], ["--configs", "default"]):
        with pytest.raises(SystemExit, match="rebuild/out/m1"):
            scratch_build.main(["glyph_data/runes", str(scratch_build.M1_OUT), *narrowing])


@pytest.mark.parametrize(
    "narrowing, build, walked",
    [
        ([], list(conform.SETTLEMENT_CONFIGS), list(conform.ACCEPTANCE_CONFIGS)),
        (["--configs", "default"], ["default"], ["default"]),
    ],
)
def test_the_oracle_walks_the_configurations_the_request_names_and_the_line_reports_them(
    monkeypatch, tmp_path, capsys, narrowing, build, walked
):
    """What `request_configs` answers is what reaches the table build and the oracle, and the JSON line names both lists: a narrowed run's oracle that fell through to its own default would walk every configuration against a font built for one, and a plain run's line would hide the ss10 walk its row count includes."""
    seen = {}

    def build_tables(spec, out_dir, *, configs, **rest):
        seen["build"] = list(configs)
        return {}, {}

    def compare_against_baseline(*args, configs=None, **rest):
        seen["oracle"] = configs
        return SimpleNamespace(rows_compared=0, divergent_rows=0, unmatched_count=0, multi_matched_count=0)

    monkeypatch.setattr(scratch_build, "M1_OUT", tmp_path / "m1")
    monkeypatch.setattr(scratch_build, "load_spec", lambda *args: SimpleNamespace())
    monkeypatch.setattr(run_m1, "build_tables", build_tables)
    monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, tables: {})
    monkeypatch.setattr(run_m1, "mint_raw_glyphs", lambda spec: ({}, {}, {}))
    monkeypatch.setattr(run_m1, "namer_dot_glyphs", lambda: {})
    monkeypatch.setattr(run_m1, "_run_defect_gates", lambda *args: SimpleNamespace(errors=()))
    monkeypatch.setattr(emit_gsub, "emit_gsub", lambda *args, **rest: SimpleNamespace(fea_text=""))
    monkeypatch.setattr(emit_gpos, "emit_gpos", lambda *args, **rest: "")
    monkeypatch.setattr(emit_gpos, "cursive_registrations", lambda *args, **rest: {})
    monkeypatch.setattr(compile_font, "build_mini_font", lambda glyphs, fea, path: path)
    monkeypatch.setattr(readback, "verify_font", lambda *args: {"pass": True})
    monkeypatch.setattr(oracle, "compare_against_baseline", compare_against_baseline)
    scratch_build.main(["glyph_data/runes", str(tmp_path / "out"), *narrowing])
    assert seen == {"build": build, "oracle": walked}
    line = json.loads(capsys.readouterr().out)
    assert line["configs"] == build
    assert line["oracle_configs"] == walked
