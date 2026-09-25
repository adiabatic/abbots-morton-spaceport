"""Tests for the window enumerations the build writes (`windows-<config>.tsv.gz`), which later steps load instead of recomputing the fixpoint. `run_m1.build_tables` reads each enumeration's head with `read_windows(windows=False)`, which gives the witness stage its certificates. `run_m1.serialized_tables` reads the same heads to give the conformance sweep and `--gates-only` their glyph inventory. The shipped-order walk (`kernel_exec.replay_emitted`) reads the full rows. The tests cover what `table.read_windows` reads back, the fingerprint guard that decides whether the files on disk can be loaded, and keeping the rows out of the build's parent process.

Every fixture is a real build's artifact, because the crate writes the payload (`artifacts::write_windows`) and `run_m1._pack_windows` packs it. A fixture that needs a different stamp edits the build's file through `restamp`.
"""

import gzip
import json
import shutil

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
    """The default configuration's table built in memory with its rows, which the build's parent never holds, so the artifact is compared with a separate kernel run instead of with itself."""
    return kernel_exec.build_tables(SPEC, frozenset())[0]


@pytest.fixture
def written(build_a):
    out_dir, _tables, _digests = build_a
    return table_module.windows_path(out_dir, "default")


def restamp(source, dest, inputs):
    """Copy an enumeration to `dest` with its head's `inputs` stamp replaced, repacked with `run_m1._pack_windows` so the gzip header and level match the build's. The plain payload goes through a scratch file beside `dest` because `_pack_windows` reads from a path."""
    marker, _, payload = gzip.decompress(source.read_bytes()).decode().partition("\t")
    head, _, rows = payload.partition("\n")
    record = json.loads(head)
    record["inputs"] = inputs
    body = f"{marker}\t{json.dumps(record, separators=(',', ':'))}\n{rows}"
    plain = dest.with_name(f"restamp-{dest.name}.tsv")
    plain.write_bytes(body.encode())
    try:
        run_m1._pack_windows(plain, dest)
    finally:
        plain.unlink()


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

    def test_the_head_carries_one_certificate_per_rule(self, built, written):
        """The head carries one certificate per rule, in rule order. Each certificate is a token list of rune names and the three boundary glyph labels, with no edge or `#NA` token, and contains its rule's input rune."""
        _inputs, head = table_module.read_windows(written, windows=False)
        assert head.certificates == built.certificates
        assert len(head.certificates) == len(head.rules)
        boundaries = {"space", "uni200C", "periodcentered"}
        for rule, certificate in zip(head.rules, head.certificates):
            assert certificate
            assert all(token in boundaries or token in SPEC.runes for token in certificate), certificate
            assert rule.input_glyph.split(".")[0] in certificate, (rule, certificate)

    def test_the_certificates_are_outside_both_digests(self, built):
        from dataclasses import replace

        stripped = replace(built, certificates=())
        assert table_module.windows_digest(stripped) == table_module.windows_digest(built)

    def test_two_builds_of_one_spec_write_the_same_bytes(self, build_a, build_b):
        """Two whole builds of one spec write byte-identical settlement TSVs, treaty TSVs, and windows files. The check runs over whole builds because the crate writes the TSVs and the windows payload, and the Python side adds only the gzip wrapper with a zeroed timestamp."""
        first, _tables, _digests = build_a
        for config in conform.SETTLEMENT_CONFIGS:
            for name in (f"settlement-{config}.tsv", f"treaties-{config}.tsv"):
                assert (first / name).read_bytes() == (build_b / name).read_bytes(), name
            packed = table_module.windows_path(first, config)
            assert packed.read_bytes() == table_module.windows_path(build_b, config).read_bytes(), config

    def test_the_pack_level_is_outside_what_the_artifact_claims(self, written, tmp_path):
        """Identity is defined on the decompressed bytes, so the compression level can change freely. The payload packed at levels 1 and 9 gives two different files, and both read back to the artifact's stamp and digest."""
        repacked = {}
        for level in (1, 9):
            repacked[level] = tmp_path / f"repacked-{level}.tsv.gz"
            with (
                gzip.GzipFile(written, "rb") as source,
                repacked[level].open("wb") as raw,
                gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=level) as packed,
            ):
                shutil.copyfileobj(source, packed)
        assert repacked[1].read_bytes() != repacked[9].read_bytes()
        for path in repacked.values():
            assert table_module.read_windows(path, windows=False)[0] == "fp-sources"
        digests = {
            table_module.windows_digest(table_module.read_windows(path)[1])
            for path in (written, *repacked.values())
        }
        assert len(digests) == 1

    def test_a_file_that_is_not_an_enumeration_is_refused(self, tmp_path):
        path = table_module.windows_path(tmp_path, "default")
        with gzip.open(path, "wt") as handle:
            handle.write("# settlement table, config default\n")
        with pytest.raises(ValueError):
            table_module.read_windows(path)


class TestWindowsDigest:
    """`windows_digest` ignores the inputs stamp, which changes with any edit to the fixpoint's inputs, and changes with the rules, the windows, and the deep-class map."""

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
    """`run_m1.build_tables` returns each configuration's enumeration head and treaty rows, and writes the full enumeration to disk under the stamp of its sources. The window rows never enter the parent process, so the parent holds only what `read_windows(windows=False)` returns."""

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
        """Nothing settles under an overlay configuration, so the build writes no table for it and removes any table an earlier build left under its name."""
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

    @pytest.mark.parametrize("config", conform.SETTLEMENT_CONFIGS)
    def test_the_crates_artifacts_are_what_this_sides_writers_write_back(self, build_a, tmp_path, config):
        """The crate writes both TSVs, and the Python writers and `table_digest` must reproduce them. The test reads the enumeration and the treaty rows back, writes them again, and requires the same bytes and the digest the crate reported. A difference in rule order between the two sides shows up in the settlement TSV, whose order is the shipped GSUB order."""
        out_dir, _tables, digests = build_a
        _inputs, decision = table_module.read_windows(table_module.windows_path(out_dir, config))
        treaty = table_module.read_treaty_tsv(out_dir / f"treaties-{config}.tsv")
        decision.write_tsv(tmp_path / f"settlement-{config}.tsv")
        treaty.write_tsv(tmp_path / f"treaties-{config}.tsv")
        for name in (f"settlement-{config}.tsv", f"treaties-{config}.tsv"):
            assert (tmp_path / name).read_bytes() == (out_dir / name).read_bytes(), name
        assert digests[config] == table_module.table_digest(decision, treaty)
