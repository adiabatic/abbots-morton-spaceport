"""The engine seam in `run_m1` (issue #40, sub-issue #47): the flag that chooses which half of the port enumerates the windows, the digest record both halves leave behind, and the claim that the choice is invisible in what a build writes.

The end-to-end arm is the one that matters and it is stated the only way it can be — the mini fixture built twice, once by each engine, with every artifact compared as bytes and the contract digests compared as a whole. `rebuild/tools/kernel_fixpoint.py` makes the same comparison over the live alphabet and every rung of the scaling ladder, and `make kernel-gate` makes the same comparison on demand, to be run around any kernel-semantics change; what this file adds is that the comparison holds through `build_tables` itself — the writers, the asserts, the stamp and the digest record, not just the fold. It skips rather than fails on a box with no kernel, because the M1 build itself is what fails loudly there — `run_m1` needs the crate — with `make kernel-gate` saying the same thing again, and a suite that refused to run without a Rust toolchain would only be stopping a font author's afternoon over it a third time.

Everything else here is plumbing at the grain plumbing goes wrong: a flag that parses and then reaches nothing, an engine that would build tables in memory for a caller who cannot stamp them, a world flag list that stops reflecting the defaults it is meant to mirror.
"""

import json
import os
import shutil

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, run_m1
from rebuild.pipeline import table as table_module

SPEC = fixtures.mini_spec()
STAMP = "engine-pinned-stamp"
ARTIFACT_NAMES = ("settlement-{}.tsv", "treaties-{}.tsv", "windows-{}.tsv.gz")

needs_kernel = pytest.mark.skipif(
    shutil.which("cargo") is None or not kernel_exec.BINARY.is_file(),
    reason="no built Rust kernel here — `make kernel-build` builds it, and the M1 build itself (and `make kernel-gate`) is what fails loudly without one",
)


class Reached(Exception):
    """Raised from a stubbed stage to end a run the moment the arguments under test have arrived."""


@pytest.fixture(scope="module")
def python_build(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("python-engine")
    run_m1.build_tables(SPEC, out_dir, inputs=STAMP, engine="python")
    return out_dir


def _digest_record(out_dir):
    return json.loads((out_dir / "table-digests.json").read_text())


def _never_built():
    raise AssertionError("the rust engine built the crate before checking it had somewhere to write")


class TestTheDigestRecord:
    def test_it_carries_one_digest_per_config_under_the_windows_stamp(self, python_build):
        record = _digest_record(python_build)
        assert record["format"] == run_m1.TABLE_DIGESTS_FORMAT
        assert record["inputs"] == STAMP
        assert record["engine"] == "python"
        assert list(record["digests"]) == list(conform.ACCEPTANCE_CONFIGS)

    def test_the_recorded_digest_is_the_configs_own_table_digest(self, python_build):
        decision, treaty = table_module.build_tables(SPEC, conform.features_for_config("ss04"))
        assert _digest_record(python_build)["digests"]["ss04"] == table_module.table_digest(decision, treaty)

    def test_a_build_with_no_stamp_records_its_digests_under_a_null_one(self, tmp_path):
        run_m1.build_tables(SPEC, tmp_path, engine="python")
        record = _digest_record(tmp_path)
        assert record["inputs"] is None
        assert list(record["digests"]) == list(conform.ACCEPTANCE_CONFIGS)
        assert not sorted(tmp_path.glob("windows-*"))


class TestTheEngineChoice:
    def test_the_rust_engine_refuses_a_caller_with_no_out_dir(self, monkeypatch):
        monkeypatch.setattr(kernel_exec, "cargo_build", _never_built)
        with pytest.raises(ValueError) as complaint:
            run_m1.build_tables(SPEC, engine="rust")
        assert "out_dir" in str(complaint.value)

    def test_the_rust_engine_refuses_a_caller_with_no_stamp(self, monkeypatch, tmp_path):
        monkeypatch.setattr(kernel_exec, "cargo_build", _never_built)
        with pytest.raises(ValueError) as complaint:
            run_m1.build_tables(SPEC, tmp_path, engine="rust")
        assert "inputs" in str(complaint.value)

    def test_an_engine_nobody_ships_is_refused_by_name(self, tmp_path):
        with pytest.raises(ValueError) as complaint:
            run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, engine="rusty")
        assert "rusty" in str(complaint.value)

    @pytest.mark.parametrize(
        "asked, wanted",
        [
            (None, kernel_exec.KERNEL_THREADS_DEFAULT),
            (2, 2),
            (99, len(conform.ACCEPTANCE_CONFIGS)),
        ],
    )
    def test_the_thread_width_is_capped_at_the_work_and_the_machine(
        self, monkeypatch, tmp_path, asked, wanted
    ):
        seen = {}

        def enumerate_configs(spec_path, out_dir, configs, *, threads, timings=False):
            seen["threads"] = threads
            raise Reached

        monkeypatch.setattr(kernel_exec, "cargo_build", lambda: None)
        monkeypatch.setattr(kernel_exec, "enumerate_configs", enumerate_configs)
        with pytest.raises(Reached):
            run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, engine="rust", kernel_threads=asked)
        assert seen["threads"] == min(wanted, os.process_cpu_count() or 1)

    def test_run_hands_the_engine_to_the_table_build(self, monkeypatch, tmp_path):
        seen = {}

        def build_tables(spec, out_dir=None, **rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(run_m1, "build_tables", build_tables)
        with pytest.raises(Reached):
            run_m1.run(out_dir=tmp_path, spec=SPEC, inputs=STAMP, engine="rust", kernel_threads=5)
        assert seen["engine"] == "rust"
        assert seen["kernel_threads"] == 5

    @pytest.mark.parametrize(
        "argv, engine, threads",
        [
            ([], "rust", None),
            (["--engine", "python"], "python", None),
            (["--engine", "rust"], "rust", None),
            (["--engine", "rust", "--kernel-threads", "5"], "rust", 5),
        ],
    )
    def test_the_cli_carries_the_engine_into_run(self, monkeypatch, argv, engine, threads):
        from rebuild.tools import artifact_cycle

        seen = {}

        def run(**rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(artifact_cycle, "run_m1_skip_fingerprint", lambda root: "pinned-key")
        monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda root: False)
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
        monkeypatch.setattr(run_m1, "run", run)
        with pytest.raises(Reached):
            run_m1.main(argv)
        assert seen["engine"] == engine
        assert seen["kernel_threads"] == threads


class TestTheInvocationSeam:
    def test_the_world_flags_reflect_the_python_side_defaults(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        assert kernel_exec.world_flags() == []
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.world_flags() == [flag for flag, _module, _attribute in kernel_exec.WORLD_FLAGS]

    def test_one_default_switched_off_carries_one_flag(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        flag, module, attribute = kernel_exec.WORLD_FLAGS[1]
        monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.world_flags() == [flag]

    def test_a_missing_binary_names_the_recipe_that_builds_one(self, monkeypatch, tmp_path):
        monkeypatch.setattr(kernel_exec, "BINARY", tmp_path / "ams-m1-kernel")
        with pytest.raises(kernel_exec.KernelRunError) as complaint:
            kernel_exec.enumerate_configs(
                tmp_path / "spec.json", tmp_path / "streams", ["default"], threads=1
            )
        assert "make kernel-build" in str(complaint.value)

    def test_a_box_without_cargo_names_the_remedy(self, monkeypatch):
        def absent(*arguments, **rest):
            raise FileNotFoundError("cargo")

        monkeypatch.setattr(kernel_exec.subprocess, "run", absent)
        with pytest.raises(kernel_exec.KernelBuildError) as complaint:
            kernel_exec.cargo_build()
        assert "Rust toolchain" in str(complaint.value)


@needs_kernel
class TestTheEnginesAgree:
    @pytest.fixture(scope="class")
    def rust_build(self, tmp_path_factory):
        out_dir = tmp_path_factory.mktemp("rust-engine")
        tables = run_m1.build_tables(SPEC, out_dir, inputs=STAMP, engine="rust")
        assert list(tables) == list(conform.ACCEPTANCE_CONFIGS)
        return out_dir

    def test_every_artifact_is_byte_identical(self, python_build, rust_build):
        for config in conform.ACCEPTANCE_CONFIGS:
            for shape in ARTIFACT_NAMES:
                name = shape.format(config)
                assert (rust_build / name).read_bytes() == (python_build / name).read_bytes(), name

    def test_the_digest_records_agree_and_name_their_engines(self, python_build, rust_build):
        python_record, rust_record = _digest_record(python_build), _digest_record(rust_build)
        assert rust_record["digests"] == python_record["digests"]
        assert rust_record["inputs"] == python_record["inputs"]
        assert (python_record["engine"], rust_record["engine"]) == ("python", "rust")
