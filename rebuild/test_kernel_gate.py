"""The on-demand differential gate's own plumbing (issue #40; re-shaped at the issue #48 cutover): both sides built fresh in the gate itself, the byte comparison that catches a kernel folding to something else, and the summary the artifact cycle reads a verdict off.

Nearly all of it runs without a Rust toolchain, because the kernel this gate drives is claimed to answer with exactly the bytes Python's own `enumerate_transitions` does — so a stub serving Python's streams stands in for a kernel that agrees, and every path around the comparison is testable on any box. The one arm that needs the real binary is the one making the claim rather than assuming it, and it skips where there is none: the gate itself is what fails loudly on a cargo-less box, and a suite that refused to run without one would be saying so twice.

Every test drives `main(argv)` with `--out` at scratch. `--out` receives only the summary now — every table either engine builds lives and dies in the gate's own temporary directories — but the live `rebuild/out/m1` still holds the record a cycle left, and a test has no business writing over it.
"""

import gzip
import json
import os
import shutil

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, kernel_io
from rebuild.pipeline import table as table_module
from rebuild.tools import kernel_gate

SPEC = fixtures.mini_spec()
POISONED = "ss04"

needs_kernel = pytest.mark.skipif(
    shutil.which("cargo") is None or not kernel_exec.BINARY.is_file(),
    reason="no built Rust kernel here — `make kernel-build` builds it, and the gate is what fails loudly without one",
)


@pytest.fixture(scope="module")
def kernel_streams(tmp_path_factory):
    """The transition streams an agreeing kernel answers with, in the plain ndjson shape `enumerate-configs` writes. Enumerated by Python, which is the port's own claim: where the two sides disagree the gate is what says so, and standing the fixture up this way is what lets the tests around that comparison run anywhere."""
    directory = tmp_path_factory.mktemp("streams")
    for config in conform.ACCEPTANCE_CONFIGS:
        product = table_module.enumerate_transitions(SPEC, conform.features_for_config(config))
        blob = directory / f"{config}.ndjson.gz"
        kernel_io.write_transitions(product, blob)
        (directory / f"transitions-{config}.ndjson").write_bytes(gzip.decompress(blob.read_bytes()))
        blob.unlink()
    return directory


@pytest.fixture
def mini_world(monkeypatch):
    """The live alphabet swapped for the mini fixture, so both of the gate's own fixpoints cost seconds."""
    monkeypatch.setattr(kernel_gate, "load_default_spec", lambda: SPEC)


@pytest.fixture
def kernel(monkeypatch, kernel_streams):
    """A kernel that answers with Python's own streams, and a record of how it was asked. The streams are handed over as copies because the gate unlinks each one as it folds it."""
    seen: dict = {}

    def enumerate_configs(spec_path, out_dir, configs, *, threads, timings=False):
        seen.update(threads=threads, timings=timings, configs=tuple(configs), spec=spec_path.is_file())
        out_dir.mkdir(parents=True, exist_ok=True)
        answered = {}
        for config in configs:
            path = out_dir / f"transitions-{config}.ndjson"
            shutil.copyfile(kernel_streams / f"transitions-{config}.ndjson", path)
            answered[config] = path
        return answered

    monkeypatch.setattr(kernel_exec, "cargo_build", lambda: None)
    monkeypatch.setattr(kernel_exec, "enumerate_configs", enumerate_configs)
    return seen


def _run(out_dir, *arguments):
    return kernel_gate.main([*arguments, "--out", str(out_dir)])


def _summary(out_dir):
    return json.loads((out_dir / kernel_gate.SUMMARY_NAME).read_text())


class TestTheSummary:
    def test_a_clean_run_reports_every_configuration_identical(self, tmp_path, mini_world, kernel):
        assert _run(tmp_path, "--skip-build") == 0
        summary = _summary(tmp_path)
        assert summary["format"] == kernel_gate.SUMMARY_FORMAT
        assert (summary["error"], summary["divergences"]) == (None, 0)
        assert list(summary["configs"]) == list(conform.ACCEPTANCE_CONFIGS)
        for entry in summary["configs"].values():
            assert list(entry) == list(kernel_gate.COMPARISONS)
            assert set(entry.values()) == {"identical"}

    def test_the_gate_writes_only_its_summary(self, tmp_path, mini_world, kernel):
        assert _run(tmp_path, "--skip-build") == 0
        assert [path.name for path in sorted(tmp_path.iterdir())] == [kernel_gate.SUMMARY_NAME]

    def test_the_run_names_the_binary_and_the_world_it_compared(self, tmp_path, mini_world, kernel):
        assert _run(tmp_path, "--skip-build") == 0
        summary = _summary(tmp_path)
        assert summary["binary"].endswith("ams-m1-kernel")
        assert summary["world"]

    def test_the_thread_width_is_capped_at_the_work_and_the_machine(self, tmp_path, mini_world, kernel):
        assert _run(tmp_path, "--skip-build", "--threads", "99") == 0
        wanted = min(len(conform.ACCEPTANCE_CONFIGS), os.process_cpu_count() or 1)
        assert _summary(tmp_path)["threads"] == wanted
        assert kernel["threads"] == wanted

    def test_the_kernel_is_asked_for_its_timing_lines(self, tmp_path, mini_world, kernel):
        assert _run(tmp_path, "--skip-build") == 0
        assert kernel["timings"] is True
        assert kernel["configs"] == conform.ACCEPTANCE_CONFIGS

    def test_a_box_without_cargo_is_a_red_gate_carrying_the_remedy(
        self, tmp_path, mini_world, kernel, monkeypatch, capsys
    ):
        def absent():
            raise kernel_exec.KernelBuildError("no cargo on PATH — install the Rust toolchain")

        monkeypatch.setattr(kernel_exec, "cargo_build", absent)
        assert _run(tmp_path) == 1
        summary = _summary(tmp_path)
        assert "Rust toolchain" in summary["error"]
        assert summary["configs"] == {}
        assert "Rust toolchain" in capsys.readouterr().err

    def test_skip_build_compares_against_the_binary_already_on_disk(
        self, tmp_path, mini_world, kernel, monkeypatch
    ):
        def never():
            raise AssertionError("--skip-build built the crate anyway")

        monkeypatch.setattr(kernel_exec, "cargo_build", never)
        assert _run(tmp_path, "--skip-build") == 0

    def test_a_kernel_that_refuses_the_invocation_is_a_red_gate(
        self, tmp_path, mini_world, kernel, monkeypatch
    ):
        def refuse(*arguments, **rest):
            raise kernel_exec.KernelRunError("kernel does not support enumerate-configs yet")

        monkeypatch.setattr(kernel_exec, "enumerate_configs", refuse)
        assert _run(tmp_path, "--skip-build") == 1
        assert "enumerate-configs" in _summary(tmp_path)["error"]


class TestADivergenceIsNamed:
    def test_a_kernel_stream_that_moved_names_its_configuration(
        self, tmp_path, mini_world, kernel, monkeypatch, capsys
    ):
        """One configuration's kernel answer loses its last transition. Depending on where the loss lands the fold either seats a different table or refuses to seat one at all, so the assertion is on the shape both roads share: the poisoned configuration diverges, every other configuration stays identical at every grain, and the run is red."""
        answering = kernel_exec.enumerate_configs

        def shortened(*arguments, **rest):
            streams = answering(*arguments, **rest)
            rows = streams[POISONED].read_bytes().splitlines(keepends=True)
            streams[POISONED].write_bytes(b"".join(rows[:-1]))
            return streams

        monkeypatch.setattr(kernel_exec, "enumerate_configs", shortened)
        assert _run(tmp_path, "--skip-build") == 1
        summary = _summary(tmp_path)
        assert summary["divergences"] >= 1
        assert "diverged" in summary["configs"][POISONED].values()
        for config, entry in summary["configs"].items():
            if config != POISONED:
                assert set(entry.values()) == {"identical"}
        report = capsys.readouterr().out
        assert POISONED in report
        assert "divergences" in report

    def test_a_stream_that_does_not_fold_diverges_at_every_grain(
        self, tmp_path, mini_world, kernel, monkeypatch
    ):
        answering = kernel_exec.enumerate_configs

        def garbled(*arguments, **rest):
            streams = answering(*arguments, **rest)
            streams[POISONED].write_bytes(b"this is not a transition stream\n")
            return streams

        monkeypatch.setattr(kernel_exec, "enumerate_configs", garbled)
        assert _run(tmp_path, "--skip-build") == 1
        summary = _summary(tmp_path)
        assert set(summary["configs"][POISONED].values()) == {"diverged"}
        assert summary["divergences"] == len(kernel_gate.COMPARISONS)


@needs_kernel
class TestTheKernelFoldsToPythonsOwnTables:
    def test_every_artifact_and_digest_matches_the_fresh_python_fold(self, tmp_path, mini_world, capsys):
        assert kernel_gate.main(["--out", str(tmp_path)]) == 0
        summary = _summary(tmp_path)
        assert summary["divergences"] == 0
        assert list(summary["configs"]) == list(conform.ACCEPTANCE_CONFIGS)
        assert "comparisons identical" in capsys.readouterr().out

    def test_the_kernels_own_timing_lines_reach_this_processs_stderr(self, tmp_path, mini_world, capsys):
        assert kernel_gate.main(["--out", str(tmp_path)]) == 0
        assert "[t] " in capsys.readouterr().err
