"""The window enumerations the build stage serializes so the font-vs-settle sweep never rebuilds a fixpoint the same sources already produced: the `table.write_windows` / `table.read_windows` round trip, the fingerprint guard in `run_m1.serialized_tables` that decides between loading and rebuilding, and the drop that keeps a million rows per configuration out of the build's parent process."""

import gzip

import pytest

from rebuild.pipeline import conform, fixtures, run_m1
from rebuild.pipeline import table as table_module
from rebuild.pipeline.table import DecisionTable, build_tables

SPEC = fixtures.mini_spec()


@pytest.fixture(scope="module")
def built():
    return build_tables(SPEC, frozenset())[0]


@pytest.fixture
def written(built, tmp_path):
    path = table_module.windows_path(tmp_path, built.config)
    table_module.write_windows(built, path, "fp-sources")
    return path


class TestRoundTrip:
    def test_the_loaded_table_replays_what_the_fixpoint_enumerated(self, built, written):
        inputs, loaded = table_module.read_windows(written)
        assert inputs == "fp-sources"
        assert loaded.config == built.config
        assert loaded.rules == built.rules
        assert loaded.reachable_cells() == built.reachable_cells()
        assert loaded.identity_guard_rules == built.identity_guard_rules
        assert loaded.cited_provenance == built.cited_provenance
        assert [(row.key, row.outcome) for row in loaded.transitions] == [
            (row.key, row.outcome) for row in built.transitions
        ]

    def test_the_head_alone_answers_which_cells_are_reachable(self, built, written):
        inputs, head = table_module.read_windows(written, windows=False)
        assert inputs == "fp-sources"
        assert head.transitions == ()
        assert head.rules == built.rules
        assert head.reachable_cells() == built.reachable_cells()

    def test_two_writes_of_one_table_are_byte_identical(self, built, tmp_path):
        first, second = tmp_path / "first.tsv.gz", tmp_path / "second.tsv.gz"
        table_module.write_windows(built, first, "fp-sources")
        table_module.write_windows(built, second, "fp-sources")
        assert first.read_bytes() == second.read_bytes()

    def test_a_file_that_is_not_an_enumeration_is_refused(self, tmp_path):
        path = table_module.windows_path(tmp_path, "default")
        with gzip.open(path, "wt") as handle:
            handle.write("# settlement table, config default\n")
        with pytest.raises(ValueError):
            table_module.read_windows(path)


def _write_every_config(out_dir, inputs):
    for config in conform.ACCEPTANCE_CONFIGS:
        table_module.write_windows(
            DecisionTable(config=config), table_module.windows_path(out_dir, config), inputs
        )


class TestFingerprintGuard:
    def test_a_complete_matching_set_loads(self, tmp_path):
        _write_every_config(tmp_path, "fp-sources")
        tables = run_m1.serialized_tables(tmp_path, "fp-sources")
        assert tables is not None
        assert sorted(tables) == sorted(conform.ACCEPTANCE_CONFIGS)

    def test_one_configuration_written_from_other_sources_rejects_the_set(self, tmp_path):
        _write_every_config(tmp_path, "fp-sources")
        table_module.write_windows(
            DecisionTable(config="ss03"), table_module.windows_path(tmp_path, "ss03"), "fp-moved"
        )
        assert run_m1.serialized_tables(tmp_path, "fp-sources") is None

    def test_one_missing_configuration_rejects_the_set(self, tmp_path):
        _write_every_config(tmp_path, "fp-sources")
        table_module.windows_path(tmp_path, "ss10").unlink()
        assert run_m1.serialized_tables(tmp_path, "fp-sources") is None

    def test_one_unreadable_configuration_rejects_the_set(self, tmp_path):
        _write_every_config(tmp_path, "fp-sources")
        table_module.windows_path(tmp_path, "ss02").write_bytes(b"not an enumeration")
        assert run_m1.serialized_tables(tmp_path, "fp-sources") is None

    def test_an_empty_directory_rejects_rather_than_raises(self, tmp_path):
        assert run_m1.serialized_tables(tmp_path, "fp-sources") is None


class TestBuildStageHandoff:
    def test_a_stamped_build_serializes_every_configuration_and_keeps_none(self, tmp_path):
        tables = run_m1.build_tables(SPEC, tmp_path, inputs="fp-sources")
        assert sorted(tables) == sorted(conform.ACCEPTANCE_CONFIGS)
        for config, (decision, _treaty) in tables.items():
            assert decision.transitions == ()
            assert decision.rules
            inputs, loaded = table_module.read_windows(table_module.windows_path(tmp_path, config))
            assert inputs == "fp-sources"
            assert loaded.rules == decision.rules
            assert loaded.transitions

    def test_an_unstamped_build_writes_no_enumeration_and_keeps_the_windows(self, tmp_path):
        tables = run_m1.build_tables(SPEC, tmp_path)
        assert not list(tmp_path.glob("windows-*"))
        assert all(decision.transitions for decision, _treaty in tables.values())

    def test_the_sweep_refuses_a_table_handed_over_without_its_windows(self, tmp_path):
        # The alternative is a clean sweep over nothing: coverage is measured against the table's own windows.
        decision = build_tables(SPEC, frozenset())[0]
        with pytest.raises(ValueError):
            conform._conformance_config(
                None,  # pyright: ignore[reportArgumentType]
                SPEC,
                "default",
                (),
                frozenset(),
                {},
                None,
                1,
                decision=DecisionTable(config="default", rules=decision.rules),
            )
