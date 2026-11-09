"""Tests for the window rows the build writes, which later steps load instead of recomputing the fixpoint: each configuration's head file (`windows-<config>.head.json.gz`) and its rows, held as a base and segments under a windows head (`windows-<config>.tsv.gz.head`, `table`'s module docstring). `run_m1.build_tables` reads each payload's head line with `read_windows(windows=False)`, which gives the witness stage its certificates. `run_m1.serialized_tables` reads the head files through the one reader, `table.read_windows_head`, to give the conformance sweep and `--gates-only` their glyph inventory. The shipped-order walk (`kernel_exec.replay_emitted`) reads the full rows. The tests cover what the readers read back, the fingerprint guard that decides whether the files on disk can be loaded, keeping the rows out of the build's parent process, the rows that differ written as segments and their materialized form against a from-scratch build's (#618's decision 48), the routes decisions 74 and 75 settle, the adoption of a tree from before the segment format, and the contract that every reader of a table's head goes through the one reader.

Every fixture is a real build's artifact, because the crate writes the payload (`artifacts::write_windows`) and `run_m1` packs it. A fixture that needs a different stamp rewrites the build's head file and commits its head over it through `restamp`.
"""

import ast
import gzip
import io
import json
import os
import shutil
from pathlib import Path

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, output_sink, row_delta, run_m1, spec_load
from rebuild.pipeline import table as table_module

SPEC = fixtures.mini_spec()
NARROW = spec_load.narrowed(SPEC, ("qsTea", "qsMay", "qsIt"))
REPO_ROOT = Path(__file__).resolve().parent.parent


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
def narrow_scratch(tmp_path_factory):
    """`NARROW` built from scratch into an empty directory, the reference a build over earlier rows must materialize to."""
    out_dir = tmp_path_factory.mktemp("windows-narrow")
    run_m1.build_tables(NARROW, out_dir, inputs="fp-narrow")
    return out_dir


@pytest.fixture(scope="module")
def built():
    """The default configuration's table built in memory with its rows, which the build's parent never holds, so the artifact is compared with a separate kernel run instead of with itself."""
    return kernel_exec.build_tables(SPEC, frozenset())[0]


@pytest.fixture
def written(build_a):
    out_dir, _tables, _digests = build_a
    return out_dir


def _materialized(out_dir, config) -> bytes:
    return b"".join(table_module.materialized_windows(out_dir, config, sha256=True))


def _windows_files(out_dir) -> list[str]:
    return sorted(path.name for path in Path(out_dir).glob("windows-*"))


def _head(out_dir, config) -> row_delta.Head:
    return table_module.windows_head(out_dir, config)


def restamp(source, dest, config, inputs, *, commit=True):
    """Copy one configuration's windows output from `source` to `dest`, its head file rewritten with the head's `inputs` stamp replaced, packed as the build packs it, and, with `commit`, its head committed over it with the new stamps; without, the head stays as it was, as a build that wrote the head file and never committed leaves it."""
    head = _head(source, config)
    dest.mkdir(parents=True, exist_ok=True)
    for part in head.parts:
        shutil.copyfile(source / part.name, dest / part.name)
    marker, _, record = gzip.decompress(
        table_module.windows_head_file(source, config).read_bytes()
    ).partition(b"\t")
    document = json.loads(record)
    document["inputs"] = inputs
    line = marker + b"\t" + json.dumps(document, separators=(",", ":")).encode() + b"\n"
    head_file = table_module.windows_head_file(dest, config)
    staged = run_m1._stage_head_file(dest, config, line)
    os.replace(staged, head_file)
    if commit:
        stamps = {
            **head.stamps,
            table_module.INPUTS_STAMP: inputs,
            table_module.HEAD_FILE_STAMP: {
                "name": head_file.name,
                "size": head_file.stat().st_size,
                "sha256": row_delta.file_sha256(head_file),
            },
        }
        row_delta.commit(
            dest,
            row_delta.Head(
                head.output, head.generation, head.content, stamps, head.base, head.segments, head.merge
            ),
        )
    return dest


class TestRoundTrip:
    def test_the_loaded_table_replays_what_the_fixpoint_enumerated(self, built, written):
        inputs, loaded = table_module.read_materialized_windows(written, "default")
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
        inputs, head = table_module.read_windows_head(written, "default")
        assert inputs == "fp-sources"
        assert head.transitions == ()
        assert head.rules == built.rules
        assert head.reachable_cells() == built.reachable_cells()
        assert (
            table_module.read_windows(table_module.windows_head_file(written, "default"), windows=False)[0]
            == inputs
        )

    def test_the_head_carries_one_certificate_per_rule(self, built, written):
        """The head carries one certificate per rule, in rule order. Each certificate is a token list of rune names and the three boundary glyph labels, with no edge or `#NA` token, and contains its rule's input rune."""
        _inputs, head = table_module.read_windows_head(written, "default")
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
        """Two whole builds of one spec write byte-identical settlement TSVs, join TSVs, head files, windows heads and bases. The check runs over whole builds because the crate writes the TSVs and the windows payload, and the Python side adds only the gzip wrapper with a zeroed timestamp and the head."""
        first, _tables, _digests = build_a
        assert _windows_files(first) == _windows_files(build_b)
        for config in conform.SETTLEMENT_CONFIGS:
            for name in (f"settlement-{config}.tsv", f"joins-{config}.tsv"):
                assert (first / name).read_bytes() == (build_b / name).read_bytes(), name
        for name in _windows_files(first):
            assert (first / name).read_bytes() == (build_b / name).read_bytes(), name

    def test_the_pack_level_is_outside_what_the_artifact_claims(self, written, tmp_path):
        """Identity is defined on the decompressed bytes, so the compression level can change freely. The base, a whole payload, packed at levels 1 and 9 gives two different files, and both read back to the artifact's stamp and digest."""
        base = written / _head(written, "default").base.name
        repacked = {}
        for level in (1, 9):
            repacked[level] = tmp_path / f"repacked-{level}.tsv.gz"
            with (
                gzip.GzipFile(base, "rb") as source,
                repacked[level].open("wb") as raw,
                gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=level) as packed,
            ):
                shutil.copyfileobj(source, packed)
        assert repacked[1].read_bytes() != repacked[9].read_bytes()
        for path in repacked.values():
            assert table_module.read_windows(path, windows=False)[0] == "fp-sources"
        digests = {
            table_module.windows_digest(table_module.read_windows(path)[1])
            for path in (base, *repacked.values())
        }
        assert len(digests) == 1

    def test_a_file_that_is_not_an_enumeration_is_refused(self, tmp_path):
        path = tmp_path / "not-an-enumeration.tsv.gz"
        with gzip.open(path, "wt") as handle:
            handle.write("# settlement table, config default\n")
        with pytest.raises(ValueError):
            table_module.read_windows(path)


class TestWindowsDigest:
    """`windows_digest` ignores the inputs stamp, which changes with any edit to the fixpoint's inputs, and changes with the rules, the windows, and the deep-class map."""

    def test_the_loaded_table_digests_like_the_built_one(self, built, written):
        _inputs, loaded = table_module.read_materialized_windows(written, "default")
        assert table_module.windows_digest(loaded) == table_module.windows_digest(built)

    def test_the_inputs_stamp_is_outside_the_digest(self, written, tmp_path):
        moved = restamp(written, tmp_path / "moved", "default", "fp-moved")
        assert table_module.read_windows_head(moved, "default")[0] == "fp-moved"
        digests = {
            table_module.windows_digest(table_module.read_materialized_windows(out_dir, "default")[1])
            for out_dir in (written, moved)
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


def test_the_deep_classes_stamp_is_included_in_tables_inputs(monkeypatch):
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", True)
    with_classes = run_m1.tables_inputs()
    assert with_classes.endswith("+deep-classes")
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", False)
    assert run_m1.tables_inputs() == with_classes.removesuffix("+deep-classes")


class TestFingerprintGuard:
    @pytest.fixture
    def stamped(self, build_a, tmp_path):
        source, _tables, _digests = build_a

        def write(inputs, configs=conform.SETTLEMENT_CONFIGS, *, commit=True):
            for config in configs:
                restamp(source, tmp_path, config, inputs, commit=commit)
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

    def test_one_missing_head_or_head_file_rejects_the_set(self, stamped):
        out_dir = stamped("fp-sources")
        table_module.windows_head_path(out_dir, "ss04").unlink()
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None
        out_dir = stamped("fp-sources")
        table_module.windows_head_file(out_dir, "ss04").unlink()
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None

    def test_one_unreadable_configuration_rejects_the_set(self, stamped):
        out_dir = stamped("fp-sources")
        table_module.windows_head_file(out_dir, "ss05").write_bytes(b"not an enumeration")
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None
        out_dir = stamped("fp-sources")
        table_module.windows_head_path(out_dir, "ss05").write_bytes(b"not a head")
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None

    def test_a_head_file_its_head_does_not_record_reads_as_a_missing_build(self, stamped):
        """A head file a build wrote and never committed a head over, here one stamped with the very inputs asked for, is not what its head records, so the reader refuses it, and the set reads as a missing build rather than as a current stamp over old rows."""
        out_dir = stamped("fp-old")
        stamped("fp-sources", ["ss03"], commit=False)
        assert (
            table_module.read_windows(table_module.windows_head_file(out_dir, "ss03"), windows=False)[0]
            == "fp-sources"
        )
        with pytest.raises(ValueError, match="never committed"):
            table_module.read_windows_head(out_dir, "ss03")
        assert run_m1.serialized_tables(out_dir, "fp-sources") is None

    def test_an_empty_directory_rejects_rather_than_raises(self, tmp_path):
        assert run_m1.serialized_tables(tmp_path, "fp-sources") is None


class TestBuildStageHandoff:
    """`run_m1.build_tables` returns each configuration's enumeration head and join rows, and writes the full enumeration to disk under the stamp of its sources. The window rows never enter the parent process, so the parent holds only what `read_windows(windows=False)` returns."""

    def test_a_stamped_build_serializes_every_settlement_configuration_and_keeps_none(self, build_a):
        out_dir, tables, digests = build_a
        assert list(tables) == list(conform.SETTLEMENT_CONFIGS)
        assert list(digests) == list(conform.SETTLEMENT_CONFIGS)
        for config, (decision, joins) in tables.items():
            assert decision.transitions == ()
            assert decision.rules
            assert joins.rows and joins.config == config
            inputs, loaded = table_module.read_materialized_windows(out_dir, config)
            assert inputs == "fp-sources"
            assert loaded.rules == decision.rules
            assert loaded.transitions
            assert _head(out_dir, config).stamps[table_module.TABLE_DIGEST_STAMP] == digests[config]

    def test_an_unstamped_build_leaves_no_enumeration_behind(self, tmp_path):
        run_m1.build_tables(SPEC, tmp_path)
        assert not list(tmp_path.glob("windows-*"))
        assert sorted(path.name for path in tmp_path.glob("settlement-*"))

    def test_the_overlay_configuration_gets_no_table_and_a_stale_one_is_swept(self, build_a, tmp_path):
        """Nothing settles under an overlay configuration, so the build writes no table for it and removes every file an earlier build left under its name: its tables, its windows head and head file, its parts, the staging files a killed build left of its head file and its parts, and the single file of a tree from before the segment format."""
        out_dir, _tables, _digests = build_a
        for config in conform.OVERLAY_CONFIGS:
            assert not [path.name for path in out_dir.glob(f"*-{config}.*")]
        overlay = conform.OVERLAY_CONFIGS[0]
        output = table_module.windows_output(overlay)
        stale = [
            *run_m1.config_table_files(tmp_path, overlay),
            tmp_path / row_delta.base_name(output, 0),
            tmp_path / row_delta.segment_name(output, 0, 1),
            tmp_path / f"{row_delta.segment_name(output, 0, 2)}{output_sink.STAGING_SUFFIX}",
        ]
        for path in stale:
            path.write_bytes(b"a table an earlier build left behind\n")
        assert set(stale) <= set(run_m1.config_table_files(tmp_path, overlay))
        run_m1.build_tables(SPEC, tmp_path, inputs="fp-sources")
        assert not any(path.exists() for path in stale)
        assert _windows_files(tmp_path) == _windows_files(out_dir)

    def test_a_configuration_outside_the_settlement_set_leaves_no_table_behind(self, build_a, tmp_path):
        """A configuration outside `conform.SETTLEMENT_CONFIGS` keeps no tables in the out dir: the build removes the ones an earlier build wrote under its name, its windows head, head file and every part among them, so a snapshot or table diff of the directory never picks them up. The sweep reaches only such configurations, so a narrowed build keeps the tables of the settled configurations it does not name."""
        source, _tables, _digests = build_a
        unsettled = "ss02+ss03"
        assert unsettled not in conform.ACCEPTANCE_CONFIGS
        for name in _windows_files(source):
            if name.startswith("windows-ss03."):
                shutil.copyfile(source / name, tmp_path / name.replace("ss03", unsettled, 1))
        (tmp_path / f"settlement-{unsettled}.tsv").write_bytes(b"a table an earlier build left behind\n")
        (tmp_path / f"joins-{unsettled}.tsv").write_bytes(b"a table an earlier build left behind\n")
        stale = [path for path in run_m1.config_table_files(tmp_path, unsettled) if path.exists()]
        assert {path.name for path in stale} >= {
            table_module.windows_head_path(tmp_path, unsettled).name,
            table_module.windows_head_file(tmp_path, unsettled).name,
            row_delta.base_name(table_module.windows_output(unsettled), 0),
        }
        assert "ss03" in conform.SETTLEMENT_CONFIGS
        kept = [
            tmp_path / "settlement-ss03.tsv",
            tmp_path / "joins-ss03.tsv",
            table_module.windows_path(tmp_path, "ss03"),
        ]
        for path in kept:
            path.write_bytes(f"{path.name} from another build\n".encode())
        run_m1.build_tables(SPEC, tmp_path, inputs="fp-sources", configs=["default"])
        assert not any(path.exists() for path in stale)
        assert not [path.name for path in tmp_path.iterdir() if unsettled in path.name]
        for path in kept:
            assert path.read_bytes() == f"{path.name} from another build\n".encode(), path.name
        assert (tmp_path / "settlement-default.tsv").exists()

    @pytest.mark.parametrize("config", conform.SETTLEMENT_CONFIGS)
    def test_the_crates_artifacts_are_what_this_sides_writers_write_back(self, build_a, tmp_path, config):
        """The crate writes both TSVs, and the Python writers and `table_digest` must reproduce them. The test reads the enumeration and the join rows back, writes them again, and requires the same bytes and the digest the crate reported. A difference in rule order between the two sides shows up in the settlement TSV, whose order is the shipped GSUB order."""
        out_dir, _tables, digests = build_a
        _inputs, decision = table_module.read_materialized_windows(out_dir, config)
        joins = table_module.read_join_tsv(out_dir / f"joins-{config}.tsv")
        decision.write_tsv(tmp_path / f"settlement-{config}.tsv")
        joins.write_tsv(tmp_path / f"joins-{config}.tsv")
        for name in (f"settlement-{config}.tsv", f"joins-{config}.tsv"):
            assert (tmp_path / name).read_bytes() == (out_dir / name).read_bytes(), name
        assert digests[config] == table_module.table_digest(decision, joins)


def _packed(payload: bytes, directory: Path, name: str) -> bytes:
    """`payload` packed as `run_m1._pack_windows` packs a whole payload into a base, read back."""
    source = io.BytesIO(payload)
    head = source.readline()
    run_m1._pack_windows(source, directory, name, head)
    return (directory / name).read_bytes()


class TestTheSegments:
    """A build over earlier rows writes only the rows that differ, as a segment, and its materialized form is the payload a from-scratch build writes, byte for byte, decompressed and packed (#618's decision 48)."""

    def test_a_segment_build_materializes_to_a_from_scratch_payload_and_so_does_a_fold(
        self, build_a, narrow_scratch, monkeypatch, tmp_path
    ):
        """A build of the whole fixture, a build narrowed to three runes over it, which moves most rows, and a build of the whole fixture again each materialize to what the same spec builds from scratch: the head file's line, the column line and the merged rows, which packed as `_pack_windows` packs them are the from-scratch base's bytes. With the cap at 2, the next narrowed build finds every configuration's fold due, and only one of them holds the pass's fold, the one with the most segments and then the first in configuration order: it writes the materialized form as its next base, the from-scratch base byte for byte, and the rest write a third segment."""
        whole, _tables, _digests = build_a
        out_dir = tmp_path / "tables"
        run_m1.build_tables(SPEC, out_dir, inputs="fp-sources")
        for spec, reference, segments in ((NARROW, narrow_scratch, 1), (SPEC, whole, 2)):
            inputs = _head(reference, "default").stamps[table_module.INPUTS_STAMP]
            run_m1.build_tables(spec, out_dir, inputs=inputs)
            for config in conform.SETTLEMENT_CONFIGS:
                head = _head(out_dir, config)
                assert (head.generation, len(head.segments)) == (0, segments), config
                materialized = _materialized(out_dir, config)
                assert materialized == _materialized(reference, config), config
                base = _head(reference, config).base
                assert (
                    _packed(materialized, tmp_path, f"packed-{config}")
                    == (reference / base.name).read_bytes()
                )
                assert (
                    head.stamps[table_module.TABLE_DIGEST_STAMP]
                    == _head(reference, config).stamps[table_module.TABLE_DIGEST_STAMP]
                )
        monkeypatch.setattr(row_delta, "SEGMENT_CAP", 2)
        run_m1.build_tables(NARROW, out_dir, inputs="fp-narrow")
        first, *rest = conform.SETTLEMENT_CONFIGS
        folded = _head(out_dir, first)
        assert (folded.generation, folded.segments) == (1, ())
        assert (out_dir / folded.base.name).read_bytes() == (
            narrow_scratch / _head(narrow_scratch, first).base.name
        ).read_bytes()
        assert not list(out_dir.glob(f"{table_module.windows_output(first)}.0.*"))
        for config in rest:
            assert len(_head(out_dir, config).segments) == 3, config
        for config in conform.SETTLEMENT_CONFIGS:
            assert _materialized(out_dir, config) == _materialized(narrow_scratch, config), config

    def test_an_alphabet_move_writes_a_segment_whatever_its_size_and_the_next_build_folds(
        self, tmp_path, capsys
    ):
        """A letter batch re-derives every row, and the windows rows still take a segment (#618's decision 74): a build of the whole fixture over the narrowed one's rows writes a segment whose row bytes pass its base's, with no size test (decision 75). Those row bytes make the fold due, so the next build, of the same spec, folds one configuration, the first in configuration order, whose base is then the materialized form, while the rest, whose rows come out the same, write their heads alone and wait."""
        out_dir = tmp_path / "tables"
        run_m1.build_tables(NARROW, out_dir, inputs="fp-narrow")
        capsys.readouterr()
        run_m1.build_tables(SPEC, out_dir, inputs="fp-sources")
        assert "wrote=segment" in capsys.readouterr().out
        for config in conform.SETTLEMENT_CONFIGS:
            head = _head(out_dir, config)
            (segment,) = head.segments
            assert segment.rows_bytes > head.base.rows_bytes, config
            assert row_delta.fold_due(head), config
        first, *rest = conform.SETTLEMENT_CONFIGS
        before = {config: _materialized(out_dir, config) for config in conform.SETTLEMENT_CONFIGS}
        run_m1.build_tables(SPEC, out_dir, inputs="fp-sources")
        output = capsys.readouterr().out
        assert f"pack_windows[{first}]" in output and "wrote=base reason=fold" in output
        assert output.count("wrote=head") == len(rest)
        assert (_head(out_dir, first).generation, _head(out_dir, first).segments) == (1, ())
        for config in rest:
            assert len(_head(out_dir, config).segments) == 1, config
        for config in conform.SETTLEMENT_CONFIGS:
            assert _materialized(out_dir, config) == before[config], config

    def test_a_tree_from_before_the_segment_format_is_adopted_by_link_and_a_build_that_moves_no_row_writes_its_heads_alone(
        self, build_a, tmp_path, capsys
    ):
        """A tree holding today's single file for each configuration and no head is adopted: the first build links each file as generation 0's base, which keeps its inode and modification time, merges against it, finds no row that differs, and writes only each configuration's head file and head, with no segment. The single file is gone once the head is in place, and the rows materialize as before."""
        whole, _tables, _digests = build_a
        out_dir = tmp_path / "tables"
        out_dir.mkdir()
        before = {}
        for config in conform.SETTLEMENT_CONFIGS:
            for name in (f"settlement-{config}.tsv", f"joins-{config}.tsv"):
                shutil.copyfile(whole / name, out_dir / name)
            before[config] = _materialized(whole, config)
            single = table_module.windows_path(out_dir, config)
            single.write_bytes(_packed(before[config], tmp_path, f"single-{config}"))
        stats = {
            config: table_module.windows_path(out_dir, config).stat() for config in conform.SETTLEMENT_CONFIGS
        }
        capsys.readouterr()
        run_m1.build_tables(SPEC, out_dir, inputs="fp-sources")
        output = capsys.readouterr().out
        assert f"adopting={len(conform.SETTLEMENT_CONFIGS)}" in output
        assert output.count("wrote=head adopted=true") == len(conform.SETTLEMENT_CONFIGS)
        assert not list(out_dir.glob("*.delta"))
        for config in conform.SETTLEMENT_CONFIGS:
            assert not table_module.windows_path(out_dir, config).exists()
            head = _head(out_dir, config)
            assert (head.generation, head.segments) == (0, ())
            base = (out_dir / head.base.name).stat()
            assert (base.st_ino, base.st_mtime_ns) == (
                stats[config].st_ino,
                stats[config].st_mtime_ns,
            ), config
            assert _materialized(out_dir, config) == before[config]
        assert run_m1.serialized_tables(out_dir, "fp-sources") is not None


# The functions outside rebuild/pipeline/table.py, the one reader of a configuration's table head (`table.read_windows_head`), allowed to name `read_windows`, `windows_path`, `windows_head_file` or `_head_file_bytes`, each with what it reads or names them for. None reads a configuration's table head from the disk: that is `read_windows_head`'s, which checks the head file against the windows head.
HEAD_NAME_READERS = {
    (
        "rebuild/pipeline/run_m1.py",
        "build_tables.read_one",
    ): "the head line its payload's reader kept from the crate's FIFO",
    (
        "rebuild/pipeline/run_m1.py",
        "_pack_config",
    ): "the head file it renames into place and the single file it deletes",
    ("rebuild/pipeline/run_m1.py", "_head_file_staging"): "the name the head file is staged under",
    ("rebuild/pipeline/run_m1.py", "_adopted_base"): "the single file it adopts",
    ("rebuild/pipeline/run_m1.py", "_route_of"): "whether a single file is there to adopt",
    ("rebuild/pipeline/run_m1.py", "config_table_files"): "the files it names for a configuration",
    (
        "rebuild/pipeline/kernel_exec.py",
        "build_tables",
    ): "the plain payload of a build into its own scratch directory",
    ("rebuild/tools/artifact_cycle.py", "m1_table_head_names"): "the head file whose existence it checks",
    (
        "rebuild/tools/perf_fingerprint.py",
        "table_digests",
    ): "the single file of a tree from before the segment format",
    ("rebuild/tools/scaling_sweep.py", "counts"): "the head line of a payload read through its FIFO",
}


def _head_reader_uses(path: Path) -> set[str]:
    """Return the qualified names of the functions in `path` that name `read_windows`, `windows_path`, `windows_head_file` or `_head_file_bytes`, other than by defining them, as a name, an attribute or an import, aliased or not."""
    names = {"read_windows", "windows_path", "windows_head_file", "_head_file_bytes"}
    found: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, (*scope, child.name))
                continue
            named = (
                child.id
                if isinstance(child, ast.Name)
                else (
                    child.attr
                    if isinstance(child, ast.Attribute)
                    else child.name if isinstance(child, ast.alias) else None
                )
            )
            if named in names:
                found.add(".".join(scope) or "<module>")
            visit(child, scope)

    visit(ast.parse(path.read_text(encoding="utf-8")), ())
    return found


def test_every_reader_of_a_tables_head_goes_through_the_one_reader():
    """The contract the head file stands on: outside rebuild/pipeline/table.py, no rebuild/ module but the tests names `read_windows`, `windows_path`, `windows_head_file` or `_head_file_bytes`, by a use or an import under any alias, except the readers `HEAD_NAME_READERS` lists with their reasons, and each of those still does, so the list cannot go stale. Every other reader of a configuration's table head reads it through `table.read_windows_head`."""
    from rebuild.tools import artifact_cycle

    closure = artifact_cycle.rebuild_gate_closure_files(REPO_ROOT)
    assert closure is not None, "the rebuild closure needs git, and without it there is nothing to sweep"
    modules = [
        relative
        for relative in closure
        if relative.endswith(".py")
        and relative.startswith("rebuild/")
        and not Path(relative).name.startswith("test_")
        and Path(relative).name != "conftest.py"
        and relative != "rebuild/pipeline/table.py"
    ]
    assert "rebuild/pipeline/run_m1.py" in modules, "the sweep reached nothing; the closure moved"
    found = {(relative, scope) for relative in modules for scope in _head_reader_uses(REPO_ROOT / relative)}
    assert found == set(HEAD_NAME_READERS)
