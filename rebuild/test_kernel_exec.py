"""The kernel boundary's Python face (issue #78, which left the crate as the only fixpoint): the flags that tell the kernel which world to enumerate, the product it hands back, and the plumbing between the crate and `run_m1` — the digest record, the thread cap, the CLI. Every table here is built on the mini fixture, because the live alphabet's enumeration is the build's business and nothing a contracts test should be paying for; what the fixture is enough to state is the shape of the answer, which is what this file is about.

Nothing skips. A box without `cargo` fails these tests with the remedy `KernelBuildError` carries, and that is the honest signal now that no in-process fixpoint exists to fall back to: the M1 build itself cannot run there either.
"""

import json
import os

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, run_m1
from rebuild.pipeline import table as table_module
from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, UNKNOWN, ZWNJ, RightToken

SPEC = fixtures.mini_spec()
STAMP = "kernel-pinned-stamp"
CONFIGS = {"default": frozenset(), "ss03": frozenset({"ss03"}), "ss04": frozenset({"ss04"})}


class Reached(Exception):
    """Raised from a stubbed stage to end a run the moment the arguments under test have arrived."""


@pytest.fixture(scope="module")
def products():
    return {name: kernel_exec.enumerate_transitions(SPEC, features) for name, features in CONFIGS.items()}


@pytest.fixture(scope="module")
def stamped_build(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("stamped")
    tables = run_m1.build_tables(SPEC, out_dir, inputs=STAMP)
    assert list(tables) == list(conform.ACCEPTANCE_CONFIGS)
    return out_dir


def _digest_record(out_dir):
    return json.loads((out_dir / "table-digests.json").read_text())


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

    def test_settlement_flags_exclude_the_enumerations_deep_grain(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.settlement_flags() == ["--candidacy-prospect", "--vote-slots-off"]
        assert "--deep-classes-off" not in kernel_exec.settlement_flags()

    def test_settle_cases_batches_questions_with_canonical_features_and_modes(self, monkeypatch, tmp_path):
        question = {
            "left": {"kind": "edge", "settled": None},
            "input": "qsMay",
            "right": [
                {"kind": "edge", "letter": None},
                {"kind": "edge", "letter": None},
                {"kind": "edge", "letter": None},
                {"kind": "edge", "letter": None},
            ],
            "result": None,
        }
        answer = {**question, "result": {"settled": "trace"}}
        calls = []

        class Finished:
            returncode = 0
            stdout = (json.dumps(answer) + "\n").encode()
            stderr = b""

        def run(arguments, verb):
            calls.append((arguments, verb))
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        got = kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.ndjson",
            [question],
            frozenset({"ss05", "ss03"}),
        )
        assert got == [answer]
        arguments = calls[0][0]
        assert arguments[1:4] == [
            "settle-cases",
            str(tmp_path / "spec.json"),
            str(tmp_path / "cases.ndjson"),
        ]
        assert "--features=ss03,ss05" in arguments
        assert "--candidacy-prospect" in arguments
        assert "--vote-slots-off" in arguments
        assert "--deep-classes-off" not in arguments

    def test_settle_cases_refuses_an_answer_to_a_different_question(self, monkeypatch, tmp_path):
        question = {"left": {}, "input": "qsMay", "right": [], "result": None}
        changed = {**question, "input": "qsIt", "result": {}}

        class Finished:
            returncode = 0
            stdout = (json.dumps(changed) + "\n").encode()
            stderr = b""

        monkeypatch.setattr(kernel_exec, "_run_kernel", lambda *args, **kwargs: Finished())
        with pytest.raises(kernel_exec.KernelRunError, match="changed"):
            kernel_exec._settle_cases(
                tmp_path / "spec.json",
                tmp_path / "cases.ndjson",
                [question],
                frozenset(),
            )

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

    def test_the_crate_is_built_once_per_process(self, monkeypatch):
        """`ensure_built` is what every caller in a process shares, so a suite that builds a hundred tables consults cargo once. The memo is a module attribute precisely so a test can drive it."""
        builds = []
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        monkeypatch.setattr(kernel_exec, "cargo_build", lambda: builds.append(1))
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        assert builds == [1]

    def test_guard_sweep_returns_the_complete_semantic_surface(self):
        verdicts = kernel_exec.guard_sweep(SPEC)
        letters = tuple(RightToken("letter", name) for name in sorted(SPEC.runes))
        ligatures = tuple(name for name, rune in SPEC.runes.items() if rune.sequence)
        second_slots = (*letters, EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN)
        assert len(verdicts) == len(ligatures) * len(letters) * len(second_slots)
        assert set(verdicts.values()) <= {False, True}
        first = letters[0]
        for ligature in ligatures:
            assert (ligature, first, ZWNJ) in verdicts
            assert (ligature, first, NAMER_DOT) in verdicts


@pytest.mark.parametrize(
    ("deep", "prospect", "votes", "wanted"),
    [
        (True, True, True, True),
        (True, True, False, True),
        (True, False, True, True),
        (True, False, False, False),
        (False, True, True, False),
        (False, False, False, False),
    ],
)
def test_the_class_grain_rule_needs_a_fiber_source(monkeypatch, deep, prospect, votes, wanted):
    """Class grain is asked for by the flag and granted only where a deep token can move an outcome at all: in the pinned candidacy world the crate has nothing to probe and enumerates at label grain however the flag reads."""
    from rebuild.pipeline import settle as settle_module

    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", deep)
    monkeypatch.setattr(settle_module, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(settle_module, "VOTE_SLOTS_DEFAULT", votes)
    assert kernel_exec.class_grain() is wanted


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheProductStandsAlone:
    def test_the_stream_is_key_sorted_without_duplicates(self, products, config):
        keys = [row.key for row in products[config].transitions]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

    def test_every_settled_cell_the_rows_name_is_in_the_product(self, products, config):
        product = products[config]
        for row in product.transitions:
            assert row.settled.cell in product.cells
            if row.left_settled is not None:
                assert row.left_settled.cell in product.cells

    def test_the_prospect_divergence_pass_runs_in_the_back_half(self, products, config):
        """The fold genuinely raises joint flags the kernel left unflagged — the prospect-divergence pass runs on this side of the boundary rather than vacuously — and it is monotone, never clearing a joint the trace floor set. The mini spec's one deep class never covers a divergent row, so the claim is stated over the class-grain stream as a whole instead of over a class row in particular."""
        product = products[config]
        folded, _treaty = table_module.assemble_tables(SPEC, product)
        rows = [row for row in folded.transitions if isinstance(row, table_module.Transition)]
        assert [row.key for row in product.transitions] == [row.key for row in rows]
        flipped = [
            before.key for before, after in zip(product.transitions, rows) if after.joint and not before.joint
        ]
        assert flipped
        assert not any(before.joint and not after.joint for before, after in zip(product.transitions, rows))


def test_the_default_configuration_enumerates_at_class_grain(products):
    product = products["default"]
    assert product.deep_classes
    for token, members in product.deep_classes.items():
        assert token.startswith(table_module.DEEP_CLASS_PREFIX)
        assert len(members) > 1


class TestTheDigestRecord:
    def test_it_carries_one_digest_per_config_under_the_windows_stamp(self, stamped_build):
        record = _digest_record(stamped_build)
        assert record["format"] == run_m1.TABLE_DIGESTS_FORMAT
        assert record["inputs"] == STAMP
        assert list(record["digests"]) == list(conform.ACCEPTANCE_CONFIGS)

    def test_the_record_names_no_engine(self, stamped_build):
        """There is one engine, so the record no longer says which one — the format marker is what a reader of an older file trips over."""
        assert "engine" not in _digest_record(stamped_build)

    def test_the_recorded_digest_is_the_configs_own_table_digest(self, stamped_build):
        decision, treaty = kernel_exec.build_tables(SPEC, conform.features_for_config("ss04"))
        assert _digest_record(stamped_build)["digests"]["ss04"] == table_module.table_digest(decision, treaty)

    def test_a_build_with_no_stamp_records_its_digests_under_a_null_one(self, tmp_path):
        run_m1.build_tables(SPEC, tmp_path)
        record = _digest_record(tmp_path)
        assert record["inputs"] is None
        assert list(record["digests"]) == list(conform.ACCEPTANCE_CONFIGS)
        assert not sorted(tmp_path.glob("windows-*"))


class TestTheKernelInvocation:
    def test_a_caller_with_nowhere_to_write_still_gets_its_tables(self, tmp_path, monkeypatch):
        """The kernel serves an in-memory build too: the arm that once refused a caller with no `out_dir` existed only to route such callers to the in-process fixpoint, and there is no such fixpoint now."""
        monkeypatch.chdir(tmp_path)
        tables = run_m1.build_tables(SPEC)
        assert list(tables) == list(conform.ACCEPTANCE_CONFIGS)
        assert all(decision.transitions for decision, _treaty in tables.values())
        assert not sorted(tmp_path.iterdir())

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

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "enumerate_configs", enumerate_configs)
        with pytest.raises(Reached):
            run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, kernel_threads=asked)
        assert seen["threads"] == min(wanted, os.process_cpu_count() or 1)

    def test_run_hands_the_thread_width_to_the_table_build(self, monkeypatch, tmp_path):
        seen = {}

        def build_tables(spec, out_dir=None, **rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(run_m1, "build_tables", build_tables)
        with pytest.raises(Reached):
            run_m1.run(out_dir=tmp_path, spec=SPEC, inputs=STAMP, kernel_threads=5)
        assert seen["kernel_threads"] == 5

    @pytest.mark.parametrize("argv, threads", [([], None), (["--kernel-threads", "5"], 5)])
    def test_the_cli_carries_the_thread_width_into_run(self, monkeypatch, argv, threads):
        from rebuild.tools import artifact_cycle

        seen = {}

        def run(**rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(artifact_cycle, "run_m1_skip_fingerprint", lambda root: "pinned-key")
        monkeypatch.setattr(run_m1.conform, "unaliased_subset_names", lambda subset_dir, alias_path: {})
        monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda root: False)
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
        monkeypatch.setattr(run_m1, "run", run)
        with pytest.raises(Reached):
            run_m1.main(argv)
        assert seen["kernel_threads"] == threads
