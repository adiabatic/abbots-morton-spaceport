"""The window enumerations the build stage serializes so nothing downstream rebuilds a fixpoint the same sources already produced — `run_m1.serialized_tables`' header read, which mints the sweep's glyph inventory, and the full rows the font-free witness gate reads: what `table.read_windows` gets back off the artifact, the fingerprint guard that decides between loading and rebuilding, and the drop that keeps a million rows per configuration out of the build's parent process. Every fixture here is a real build's artifact rather than something Python composed, because since the fold moved into the crate the writer is `artifacts::write_windows` and the packer is `run_m1._pack_windows`; a fixture that needs a stamp the build did not give it edits the build's own file through `restamp`."""

import gzip
import json

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, run_m1
from rebuild.pipeline import table as table_module

SPEC = fixtures.mini_spec()


@pytest.fixture(scope="module")
def build_a(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("windows-a")
    tables, digests = run_m1.build_tables(SPEC, out_dir, inputs="fp-sources")
    return out_dir, tables, digests


@pytest.fixture(scope="module")
def build_b(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("windows-b")
    run_m1.build_tables(SPEC, out_dir, inputs="fp-sources")
    return out_dir


@pytest.fixture(scope="module")
def built():
    """The default configuration's whole table in memory, rows included — which the build's own parent never holds — so the artifact is checked against a second reading of the kernel rather than against itself."""
    return kernel_exec.build_tables(SPEC, frozenset())[0]


@pytest.fixture
def written(build_a):
    out_dir, _tables, _digests = build_a
    return table_module.windows_path(out_dir, "default")


def restamp(source, dest, inputs):
    """One enumeration under a different fingerprint: the head's `inputs` field rewritten in the payload and the whole thing repacked the way `run_m1._pack_windows` packs one, zeroed stamp included."""
    marker, _, payload = gzip.decompress(source.read_bytes()).decode().partition("\t")
    head, _, rows = payload.partition("\n")
    record = json.loads(head)
    record["inputs"] = inputs
    body = f"{marker}\t{json.dumps(record, separators=(',', ':'))}\n{rows}"
    with (
        dest.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as handle,
    ):
        handle.write(body.encode())


class TestRoundTrip:
    def test_the_loaded_table_replays_what_the_fixpoint_enumerated(self, built, written):
        inputs, loaded = table_module.read_windows(written)
        assert inputs == "fp-sources"
        assert loaded.config == built.config
        assert loaded.rules == built.rules
        assert loaded.reachable_cells() == built.reachable_cells()
        assert loaded.identity_guard_rules == built.identity_guard_rules
        assert loaded.cited_provenance == built.cited_provenance
        assert loaded.deep_classes == built.deep_classes
        assert [(row.key, row.outcome) for row in loaded.transitions] == [
            (row.key, row.outcome) for row in built.transitions
        ]
        assert [(row.key, row.outcome) for row in loaded.expanded_transitions()] == [
            (row.key, row.outcome) for row in built.expanded_transitions()
        ]

    def test_the_head_alone_answers_which_cells_are_reachable(self, built, written):
        inputs, head = table_module.read_windows(written, windows=False)
        assert inputs == "fp-sources"
        assert head.transitions == ()
        assert head.rules == built.rules
        assert head.reachable_cells() == built.reachable_cells()

    def test_two_builds_of_one_spec_write_the_same_bytes(self, build_a, build_b):
        """Diff-stability where it is actually stated — over two whole builds rather than over two calls to one writer — since the settlement and treaty TSVs are the crate's bytes and the enumeration is the crate's payload under this side's zeroed gzip stamp."""
        first, _tables, _digests = build_a
        for config in conform.SETTLEMENT_CONFIGS:
            for name in (f"settlement-{config}.tsv", f"treaties-{config}.tsv"):
                assert (first / name).read_bytes() == (build_b / name).read_bytes(), name
            packed = table_module.windows_path(first, config)
            assert packed.read_bytes() == table_module.windows_path(build_b, config).read_bytes(), config

    def test_a_file_that_is_not_an_enumeration_is_refused(self, tmp_path):
        path = table_module.windows_path(tmp_path, "default")
        with gzip.open(path, "wt") as handle:
            handle.write("# settlement table, config default\n")
        with pytest.raises(ValueError):
            table_module.read_windows(path)


class TestWindowsDigest:
    """The row-level table digest: it must survive everything a rune edit can move without moving the table (the inputs stamp), and move with anything the settlement rows themselves carry (the rules, the windows, the class map)."""

    def test_the_loaded_table_digests_like_the_built_one(self, built, written):
        _inputs, loaded = table_module.read_windows(written)
        assert table_module.windows_digest(loaded) == table_module.windows_digest(built)

    def test_the_inputs_stamp_is_outside_the_digest(self, written, tmp_path):
        moved = tmp_path / "moved.tsv.gz"
        restamp(written, moved, "fp-moved")
        assert table_module.read_windows(moved, windows=False)[0] == "fp-moved"
        digests = {
            table_module.windows_digest(table_module.read_windows(path)[1]) for path in (written, moved)
        }
        assert len(digests) == 1

    def test_a_moved_window_or_rule_moves_the_digest(self, built):
        from dataclasses import replace

        fewer_windows = replace(built, transitions=built.transitions[:-1])
        fewer_rules = replace(built, rules=built.rules[:-1])
        digests = {table_module.windows_digest(table) for table in (built, fewer_windows, fewer_rules)}
        assert len(digests) == 3

    def test_a_moved_class_map_moves_the_digest(self, built):
        from dataclasses import replace

        assert built.deep_classes
        token, members = next(iter(built.deep_classes.items()))
        moved = replace(built, deep_classes={**built.deep_classes, token: members[:-1]})
        assert table_module.windows_digest(moved) != table_module.windows_digest(built)


def test_the_deep_classes_stamp_rides_tables_inputs(monkeypatch):
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", True)
    with_classes = run_m1.tables_inputs()
    assert with_classes.endswith("+deep-classes")
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", False)
    assert run_m1.tables_inputs() == with_classes.removesuffix("+deep-classes")


class TestFingerprintGuard:
    @pytest.fixture
    def stamped(self, build_a, tmp_path):
        source, _tables, _digests = build_a

        def write(inputs, configs=conform.SETTLEMENT_CONFIGS):
            for config in configs:
                restamp(
                    table_module.windows_path(source, config),
                    table_module.windows_path(tmp_path, config),
                    inputs,
                )
            return tmp_path

        return write

    def test_a_complete_matching_set_loads(self, stamped):
        out_dir = stamped("fp-sources")
        tables = run_m1.serialized_tables(out_dir, "fp-sources")
        assert tables is not None
        assert sorted(tables) == sorted(conform.SETTLEMENT_CONFIGS)

    def test_one_configuration_written_from_other_sources_rejects_the_set(self, stamped):
        out_dir = stamped("fp-sources")
        stamped("fp-moved", ["ss03"])
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None

    def test_one_missing_configuration_rejects_the_set(self, stamped):
        out_dir = stamped("fp-sources")
        table_module.windows_path(out_dir, "ss04").unlink()
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None

    def test_one_unreadable_configuration_rejects_the_set(self, stamped):
        out_dir = stamped("fp-sources")
        table_module.windows_path(out_dir, "ss05").write_bytes(b"not an enumeration")
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None

    def test_an_empty_directory_rejects_rather_than_raises(self, tmp_path):
        assert run_m1.serialized_tables(tmp_path, "fp-sources") is None


class TestBuildStageHandoff:
    """What the build stage hands its parent since the fold moved into the crate: the head of each configuration's enumeration and its treaty rows, with the enumeration itself on disk under the stamp that names its sources. The rows never cross into the parent at all — a million per configuration is a resident peak nothing after the build spends — so what the parent holds is what `read_windows(windows=False)` answers."""

    def test_a_stamped_build_serializes_every_settlement_configuration_and_keeps_none(self, build_a):
        out_dir, tables, digests = build_a
        assert list(tables) == list(conform.SETTLEMENT_CONFIGS)
        assert list(digests) == list(conform.SETTLEMENT_CONFIGS)
        for config, (decision, treaty) in tables.items():
            assert decision.transitions == ()
            assert decision.rules
            assert treaty.rows and treaty.config == config
            inputs, loaded = table_module.read_windows(table_module.windows_path(out_dir, config))
            assert inputs == "fp-sources"
            assert loaded.rules == decision.rules
            assert loaded.transitions

    def test_an_unstamped_build_leaves_no_enumeration_behind(self, tmp_path):
        run_m1.build_tables(SPEC, tmp_path)
        assert not list(tmp_path.glob("windows-*"))
        assert sorted(path.name for path in tmp_path.glob("settlement-*"))

    def test_the_overlay_configuration_gets_no_table_and_a_stale_one_is_swept(self, build_a, tmp_path):
        """Nothing settles under the overlay, so no table is enumerated for it — and a table left under its name by an earlier build is removed before this one writes, so a directory globbed afterward holds this build's tables alone."""
        out_dir, _tables, _digests = build_a
        for config in conform.OVERLAY_CONFIGS:
            assert not [path.name for path in out_dir.glob(f"*-{config}.tsv*")]
        stale = run_m1.overlay_table_files(tmp_path, conform.OVERLAY_CONFIGS[0])
        for path in stale:
            path.write_bytes(b"a table an earlier build left behind\n")
        run_m1.build_tables(SPEC, tmp_path, inputs="fp-sources")
        assert not any(path.exists() for path in stale)
        assert sorted(path.name for path in tmp_path.glob("windows-*")) == sorted(
            table_module.windows_path(tmp_path, config).name for config in conform.SETTLEMENT_CONFIGS
        )

    def test_the_crates_artifacts_are_what_this_sides_writers_write_back(self, build_a, tmp_path):
        """Both TSVs are the crate's bytes now, so what keeps this side's copies of those writers and of `table_digest` honest is that they reproduce them: read the enumeration and the treaty rows back, write them out again here, and require the same bytes and the same digest the crate reported at build time. A rule-ordering divergence between the two sides shows in the settlement TSV, which is the shipped GSUB order."""
        out_dir, _tables, digests = build_a
        _inputs, decision = table_module.read_windows(table_module.windows_path(out_dir, "default"))
        treaty = table_module.read_treaty_tsv(out_dir / "treaties-default.tsv")
        decision.write_tsv(tmp_path / "settlement-default.tsv")
        treaty.write_tsv(tmp_path / "treaties-default.tsv")
        for name in ("settlement-default.tsv", "treaties-default.tsv"):
            assert (tmp_path / name).read_bytes() == (out_dir / name).read_bytes(), name
        assert digests["default"] == table_module.table_digest(decision, treaty)
