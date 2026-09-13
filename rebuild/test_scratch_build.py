"""The scratch-build harness's request seam (`rebuild/tools/scratch_build.py`): which configurations a `--configs` hands the build and the oracle, and the two refusals that fire before a spec is resolved. Path arithmetic and stubs only — the crate-backed narrowed build is `rebuild/test_kernel_exec.py`'s."""

import pytest

from rebuild.pipeline import conform, run_m1
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


def test_a_narrowed_build_is_refused_under_the_live_table_directory(tmp_path):
    """A narrowed build into rebuild/out/m1, or anywhere under it, is refused by name; the same path unnarrowed and any path elsewhere narrowed both come back resolved."""
    with pytest.raises(SystemExit, match="rebuild/out/m1"):
        scratch_build.scratch_out_dir(str(scratch_build.M1_OUT / "candidate"), narrowed=True)
    with pytest.raises(SystemExit, match="rebuild/out/m1"):
        scratch_build.scratch_out_dir(str(scratch_build.M1_OUT), narrowed=True)
    assert scratch_build.scratch_out_dir(str(tmp_path), narrowed=True) == tmp_path.resolve()
    assert scratch_build.scratch_out_dir(str(scratch_build.M1_OUT), narrowed=False) == scratch_build.M1_OUT
