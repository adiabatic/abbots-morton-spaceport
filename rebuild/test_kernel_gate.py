"""The differential gate's own plumbing (issue #40, sub-issue #47): the guard that refuses to compare against artifacts a cycle did not just build, the byte comparison that catches a kernel folding to something else, and the summary the artifact cycle reads a verdict off.

Nearly all of it runs without a Rust toolchain, because the kernel this gate drives is claimed to answer with exactly the bytes Python's own `enumerate_transitions` does — so a stub serving Python's streams stands in for a kernel that agrees, and every path around the comparison is testable on any box. The one arm that needs the real binary is the one making the claim rather than assuming it, and it skips where there is none: the gate itself is what fails loudly on a cargo-less box, and a suite that refused to run without one would be saying so twice.

Every test drives `main(argv)` with `--out` at scratch. The live `rebuild/out/m1` is a cycle's own output and this tool writes a summary into whatever directory it is pointed at; a test that pointed it there would be writing over the record a cycle left.
"""

import gzip
import json
import os
import shutil

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, kernel_io, run_m1
from rebuild.pipeline import table as table_module
from rebuild.tools import kernel_gate

SPEC = fixtures.mini_spec()
STAMP = "gate-pinned-stamp"
POISONED = "ss04"

needs_kernel = pytest.mark.skipif(
    shutil.which("cargo") is None or not kernel_exec.BINARY.is_file(),
    reason="no built Rust kernel here — `make kernel-build` builds it, and the gate is what fails loudly without one",
)


@pytest.fixture(scope="module")
def cycle_out(tmp_path_factory):
    """A scratch `rebuild/out/m1` as a green cycle leaves one: every acceptance configuration's three artifacts under one stamp, and the digest record beside them."""
    out = tmp_path_factory.mktemp("cycle-out")
    run_m1.build_tables(SPEC, out, inputs=STAMP)
    return out


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
def out_dir(cycle_out, tmp_path):
    """A writable copy of that cycle's output, so a test can poison one artifact without poisoning the fixture every other test reads."""
    copy = tmp_path / "m1"
    shutil.copytree(cycle_out, copy)
    return copy


@pytest.fixture
def mini_world(monkeypatch):
    """The live alphabet swapped for the mini fixture and the sources stamp pinned, so a gate run costs seconds and compares against the artifacts `cycle_out` holds."""
    monkeypatch.setattr(kernel_gate, "load_default_spec", lambda: SPEC)
    monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)


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


def _snapshot(out_dir):
    return {path.name: path.read_bytes() for path in sorted(out_dir.iterdir()) if path.is_file()}


def _rewrite_gzipped(path, raw):
    with path.open("wb") as handle, gzip.GzipFile(filename="", fileobj=handle, mode="wb", mtime=0) as zipped:
        zipped.write(raw)


def _restamp_windows(out_dir, config, stamp):
    path = table_module.windows_path(out_dir, config)
    _rewrite_gzipped(path, gzip.decompress(path.read_bytes()).replace(STAMP.encode(), stamp))


def _poison_windows(out_dir):
    path = table_module.windows_path(out_dir, POISONED)
    rows = gzip.decompress(path.read_bytes()).split(b"\n")
    _rewrite_gzipped(path, b"\n".join(rows[:-2]) + b"\n")


def _poison_settlement(out_dir):
    path = out_dir / f"settlement-{POISONED}.tsv"
    path.write_bytes(path.read_bytes().replace(b"\t", b"  ", 1))


def _poison_treaties(out_dir):
    path = out_dir / f"treaties-{POISONED}.tsv"
    path.write_bytes(path.read_bytes() + b"qsPea\tqsPea\tbaseline\t0\t0\n")


def _poison_digest(out_dir):
    path = out_dir / kernel_gate.TABLE_DIGESTS_NAME
    payload = json.loads(path.read_text())
    payload["digests"][POISONED] = "0" * 64
    path.write_text(json.dumps(payload, indent=2) + "\n")


class TestTheStalenessGuard:
    def test_a_windows_head_from_other_sources_is_stale(self, out_dir, mini_world, kernel):
        _restamp_windows(out_dir, "ss03", b"other-sources")
        assert _run(out_dir, "--skip-build") == 1
        assert any("windows-ss03.tsv.gz" in note for note in _summary(out_dir)["stale"])

    def test_an_absent_windows_artifact_is_stale(self, out_dir, mini_world, kernel):
        table_module.windows_path(out_dir, "ss10").unlink()
        assert _run(out_dir, "--skip-build") == 1
        assert any("windows-ss10.tsv.gz" in note for note in _summary(out_dir)["stale"])

    def test_an_absent_digest_record_is_stale(self, out_dir, mini_world, kernel):
        (out_dir / kernel_gate.TABLE_DIGESTS_NAME).unlink()
        assert _run(out_dir, "--skip-build") == 1
        assert any(kernel_gate.TABLE_DIGESTS_NAME in note for note in _summary(out_dir)["stale"])

    def test_a_digest_record_from_other_sources_is_stale(self, out_dir, mini_world, kernel):
        path = out_dir / kernel_gate.TABLE_DIGESTS_NAME
        payload = json.loads(path.read_text())
        payload["inputs"] = "other-sources"
        path.write_text(json.dumps(payload, indent=2) + "\n")
        assert _run(out_dir, "--skip-build") == 1
        assert any(
            kernel_gate.TABLE_DIGESTS_NAME in note and "other sources" in note
            for note in _summary(out_dir)["stale"]
        )

    def test_a_digest_record_missing_a_configuration_is_stale(self, out_dir, mini_world, kernel):
        path = out_dir / kernel_gate.TABLE_DIGESTS_NAME
        payload = json.loads(path.read_text())
        del payload["digests"]["ss05"]
        path.write_text(json.dumps(payload, indent=2) + "\n")
        assert _run(out_dir, "--skip-build") == 1
        assert any("ss05" in note for note in _summary(out_dir)["stale"])

    def test_a_rust_built_table_set_is_refused_not_self_compared(self, out_dir, mini_world, kernel):
        """The tautology guard: a set `run_m1 --engine rust` wrote satisfies every stamp, and comparing the kernel against its own fold would read identical by construction — the one green this gate must never record."""
        path = out_dir / kernel_gate.TABLE_DIGESTS_NAME
        payload = json.loads(path.read_text())
        payload["engine"] = "rust"
        path.write_text(json.dumps(payload, indent=2) + "\n")
        assert _run(out_dir, "--skip-build") == 1
        assert any("rust-built" in note and "engine of record" in note for note in _summary(out_dir)["stale"])
        assert "configs" not in kernel

    def test_a_digest_record_with_no_engine_is_not_one_this_build_understands(
        self, out_dir, mini_world, kernel
    ):
        path = out_dir / kernel_gate.TABLE_DIGESTS_NAME
        payload = json.loads(path.read_text())
        del payload["engine"]
        path.write_text(json.dumps(payload, indent=2) + "\n")
        assert _run(out_dir, "--skip-build") == 1
        assert any("not a digest record" in note for note in _summary(out_dir)["stale"])

    def test_the_whole_stale_set_is_reported_at_once(self, out_dir, mini_world, kernel):
        for config in ("default", "ss03"):
            _restamp_windows(out_dir, config, b"other-sources")
        assert _run(out_dir, "--skip-build") == 1
        assert len(_summary(out_dir)["stale"]) == 2

    def test_a_stale_gate_rebuilds_nothing_and_never_runs_the_kernel(
        self, out_dir, mini_world, kernel, capsys
    ):
        (out_dir / kernel_gate.TABLE_DIGESTS_NAME).unlink()
        before = _snapshot(out_dir)
        assert _run(out_dir, "--skip-build") == 1
        after = _snapshot(out_dir)
        assert set(after) - set(before) == {kernel_gate.SUMMARY_NAME}
        assert all(after[name] == blob for name, blob in before.items())
        assert "configs" not in kernel
        assert kernel_gate.STALE_REMEDY in capsys.readouterr().out


class TestTheSummary:
    def test_a_clean_run_reports_every_configuration_identical(self, out_dir, mini_world, kernel):
        assert _run(out_dir, "--skip-build") == 0
        summary = _summary(out_dir)
        assert summary["format"] == kernel_gate.SUMMARY_FORMAT
        assert summary["inputs"] == STAMP
        assert (summary["stale"], summary["error"], summary["divergences"]) == ([], None, 0)
        assert list(summary["configs"]) == list(conform.ACCEPTANCE_CONFIGS)
        for entry in summary["configs"].values():
            assert list(entry) == list(kernel_gate.COMPARISONS)
            assert set(entry.values()) == {"identical"}

    def test_the_run_names_the_binary_and_the_world_it_compared(self, out_dir, mini_world, kernel):
        assert _run(out_dir, "--skip-build") == 0
        summary = _summary(out_dir)
        assert summary["binary"].endswith("ams-m1-kernel")
        assert summary["world"]

    def test_the_thread_width_is_capped_at_the_work_and_the_machine(self, out_dir, mini_world, kernel):
        assert _run(out_dir, "--skip-build", "--threads", "99") == 0
        wanted = min(len(conform.ACCEPTANCE_CONFIGS), os.process_cpu_count() or 1)
        assert _summary(out_dir)["threads"] == wanted
        assert kernel["threads"] == wanted

    def test_the_kernel_is_asked_for_its_timing_lines(self, out_dir, mini_world, kernel):
        assert _run(out_dir, "--skip-build") == 0
        assert kernel["timings"] is True
        assert kernel["configs"] == conform.ACCEPTANCE_CONFIGS

    def test_a_box_without_cargo_is_a_red_gate_carrying_the_remedy(
        self, out_dir, mini_world, kernel, monkeypatch, capsys
    ):
        def absent():
            raise kernel_exec.KernelBuildError("no cargo on PATH — install the Rust toolchain")

        monkeypatch.setattr(kernel_exec, "cargo_build", absent)
        assert _run(out_dir) == 1
        summary = _summary(out_dir)
        assert "Rust toolchain" in summary["error"]
        assert summary["configs"] == {}
        assert "Rust toolchain" in capsys.readouterr().err

    def test_skip_build_compares_against_the_binary_already_on_disk(
        self, out_dir, mini_world, kernel, monkeypatch
    ):
        def never():
            raise AssertionError("--skip-build built the crate anyway")

        monkeypatch.setattr(kernel_exec, "cargo_build", never)
        assert _run(out_dir, "--skip-build") == 0

    def test_a_kernel_that_refuses_the_invocation_is_a_red_gate(
        self, out_dir, mini_world, kernel, monkeypatch
    ):
        def refuse(*arguments, **rest):
            raise kernel_exec.KernelRunError("kernel does not support enumerate-configs yet")

        monkeypatch.setattr(kernel_exec, "enumerate_configs", refuse)
        assert _run(out_dir, "--skip-build") == 1
        assert "enumerate-configs" in _summary(out_dir)["error"]


class TestADivergenceIsNamed:
    @pytest.mark.parametrize(
        "kind, poison",
        [
            ("windows", _poison_windows),
            ("settlement", _poison_settlement),
            ("treaties", _poison_treaties),
            ("digest", _poison_digest),
        ],
    )
    def test_one_moved_artifact_names_its_configuration_and_its_grain(
        self, out_dir, mini_world, kernel, capsys, kind, poison
    ):
        poison(out_dir)
        assert _run(out_dir, "--skip-build") == 1
        summary = _summary(out_dir)
        assert summary["divergences"] == 1
        assert summary["configs"][POISONED][kind] == "diverged"
        for config, entry in summary["configs"].items():
            for name, state in entry.items():
                assert state == "identical" or (config, name) == (POISONED, kind)
        report = capsys.readouterr().out
        assert POISONED in report
        assert "1 divergences" in report

    def test_a_stream_that_does_not_fold_diverges_at_every_grain(
        self, out_dir, mini_world, kernel, monkeypatch
    ):
        answering = kernel_exec.enumerate_configs

        def garbled(*arguments, **rest):
            streams = answering(*arguments, **rest)
            streams[POISONED].write_bytes(b"this is not a transition stream\n")
            return streams

        monkeypatch.setattr(kernel_exec, "enumerate_configs", garbled)
        assert _run(out_dir, "--skip-build") == 1
        summary = _summary(out_dir)
        assert set(summary["configs"][POISONED].values()) == {"diverged"}
        assert summary["divergences"] == len(kernel_gate.COMPARISONS)


@needs_kernel
class TestTheKernelFoldsToTheCyclesOwnTables:
    def test_every_artifact_and_digest_is_what_the_cycle_wrote(self, out_dir, mini_world, capsys):
        assert kernel_gate.main(["--out", str(out_dir)]) == 0
        summary = _summary(out_dir)
        assert summary["divergences"] == 0
        assert list(summary["configs"]) == list(conform.ACCEPTANCE_CONFIGS)
        assert "comparisons identical" in capsys.readouterr().out

    def test_the_kernels_own_timing_lines_reach_this_processs_stderr(self, out_dir, mini_world, capsys):
        assert kernel_gate.main(["--out", str(out_dir)]) == 0
        assert "[t] " in capsys.readouterr().err
