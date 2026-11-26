"""Tests for how `engine/tools/scratch_build.py` handles `--configs` and the out dir: which configurations reach the table build, what the JSON line reports, and the two errors raised before the spec is loaded. Every later stage is stubbed; `engine/test_kernel_exec.py` tests a narrowed build against the crate."""

import json
from types import SimpleNamespace

import pytest

from engine.pipeline import compile_font, conform, emit_gpos, emit_gsub, kern, readback, run_build
from engine.tools import scratch_build


class Reached(Exception):
    """Raised by a stubbed stage that a rejected invocation must not reach."""


def test_the_unnarrowed_request_is_the_settlement_set_and_a_named_set_keeps_its_order():
    assert scratch_build.request_configs("") == list(conform.SETTLEMENT_CONFIGS)
    assert scratch_build.request_configs("ss03,default") == ["ss03", "default"]
    assert scratch_build.request_configs("ss08") == ["ss08"]


def test_an_unknown_configuration_is_refused_before_anything_is_resolved(monkeypatch, tmp_path):
    """An unknown token exits with an error that lists the valid tokens, before the spec is loaded, before a table is built, and before anything is written under the out dir."""

    def never(*args, **rest):
        raise Reached

    monkeypatch.setattr(scratch_build, "load_spec", never)
    monkeypatch.setattr(run_build, "build_tables", never)
    with pytest.raises(SystemExit) as refused:
        scratch_build.main(["glyph_data/runes", str(tmp_path), "--configs", "ss99"])
    assert "ss99" in str(refused.value)
    assert all(config in str(refused.value) for config in conform.SETTLEMENT_CONFIGS)
    assert not sorted(tmp_path.iterdir())


def test_every_build_is_refused_under_the_live_table_directory(monkeypatch, tmp_path):
    """A build into engine/out/build or anywhere under it exits with an error naming that directory, with or without `--configs`, before the spec is loaded. Any other path is returned resolved."""

    def never(*args, **rest):
        raise Reached

    with pytest.raises(SystemExit, match="engine/out/build"):
        scratch_build.scratch_out_dir(str(scratch_build.BUILD_OUT / "candidate"))
    with pytest.raises(SystemExit, match="engine/out/build"):
        scratch_build.scratch_out_dir(str(scratch_build.BUILD_OUT))
    assert scratch_build.scratch_out_dir(str(tmp_path)) == tmp_path.resolve()
    monkeypatch.setattr(scratch_build, "load_spec", never)
    monkeypatch.setattr(run_build, "build_tables", never)
    for narrowing in ([], ["--configs", "default"]):
        with pytest.raises(SystemExit, match="engine/out/build"):
            scratch_build.main(["glyph_data/runes", str(scratch_build.BUILD_OUT), *narrowing])


@pytest.mark.parametrize(
    "narrowing, build",
    [
        ([], list(conform.SETTLEMENT_CONFIGS)),
        (["--configs", "default"], ["default"]),
        (["--configs", "default,ss08"], ["default", "ss08"]),
    ],
)
def test_the_build_takes_the_configurations_the_request_names_and_the_line_reports_them(
    monkeypatch, tmp_path, capsys, narrowing, build
):
    """The list `request_configs` returns is the one passed to the table build, and the JSON line reports it beside the defect gate's errors."""
    seen = {}

    def build_tables(spec, out_dir, *, configs, **rest):
        seen["build"] = list(configs)
        return {}, {}

    monkeypatch.setattr(scratch_build, "BUILD_OUT", tmp_path / "build")
    monkeypatch.setattr(scratch_build, "load_spec", lambda *args: SimpleNamespace())
    monkeypatch.setattr(run_build, "build_tables", build_tables)
    monkeypatch.setattr(run_build, "mint_cell_glyphs", lambda spec, tables: {})
    monkeypatch.setattr(run_build, "mint_raw_glyphs", lambda spec: ({}, {}, {}))
    monkeypatch.setattr(run_build, "namer_dot_glyphs", lambda: {})
    monkeypatch.setattr(run_build, "_run_defect_gates", lambda *args: SimpleNamespace(errors=()))
    monkeypatch.setattr(emit_gsub, "emit_gsub", lambda *args, **rest: SimpleNamespace(fea_text=""))
    monkeypatch.setattr(emit_gpos, "emit_gpos", lambda *args, **rest: "")
    monkeypatch.setattr(emit_gpos, "cursive_registrations", lambda *args, **rest: {})
    monkeypatch.setattr(compile_font, "build_font", lambda glyphs, fea, path, fea_path=None: path)
    monkeypatch.setattr(readback, "verify_font", lambda *args, **rest: {"pass": True})
    monkeypatch.setattr(kern, "run_stage", lambda *args, **rest: None)
    monkeypatch.setattr(kern, "write_classes", lambda *args, **rest: None)
    monkeypatch.setattr(emit_gpos, "kern_plan", lambda *args, **rest: None)
    scratch_build.main(["glyph_data/runes", str(tmp_path / "out"), *narrowing])
    assert seen == {"build": build}
    line = json.loads(capsys.readouterr().out)
    assert line == {"configs": build, "defect_errors": []}
