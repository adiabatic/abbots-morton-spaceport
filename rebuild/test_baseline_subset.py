"""baseline_subset tests over synthetic tables: the row filter itself, and the stamp-keyed freshness contract run_m1 leans on so a stale subset can never feed the oracle."""

import gzip
import hashlib
import json

from rebuild.pipeline import baseline_subset


def _write_table(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("# baseline-extractor v1\n# config: default\n")
        for row in rows:
            fh.write(row + "\n")


class TestFilterTable:
    def test_keeps_subset_rows_and_header(self, tmp_path):
        source = tmp_path / "baseline-default.tsv.gz"
        rows = [
            "E670\tqsIt\t0\t\t0,0,150",
            "E656\tqsThaw\t0\t\t0,0,250",
            "E652:E670\tqsTea.half.ex-y5|qsIt.en-y5.ex-y0\t0,1\ty5\t0,0,100|0,0,100",
            "E652:E656\tqsTea|qsThaw\t0,1\tbreak\t0,0,100|0,0,250",
        ]
        _write_table(source, rows)
        destination = tmp_path / "out" / "baseline-default.subset.tsv.gz"
        kept = baseline_subset.filter_table(source, destination)
        assert kept == 2
        with gzip.open(destination, "rt", encoding="utf-8") as fh:
            content = fh.read()
        assert content.startswith("# baseline-extractor v1\n# config: default\n")
        assert "E670\t" in content
        assert "E652:E670\t" in content
        assert "E656" not in content

    def test_canonical_order_preserved(self, tmp_path):
        source = tmp_path / "baseline-ss03.tsv.gz"
        rows = [
            "E650\tqsPea\t0\t\t0,0,300",
            "E650:E650\tqsPea.ex-y6|qsPea.en-y6\t0,1\ty6\t0,0,300|0,0,300",
        ]
        _write_table(source, rows)
        destination = tmp_path / "baseline-ss03.subset.tsv.gz"
        baseline_subset.filter_table(source, destination)
        with gzip.open(destination, "rt", encoding="utf-8") as fh:
            data_lines = [line for line in fh if not line.startswith("#")]
        assert data_lines[0].startswith("E650\t")
        assert data_lines[1].startswith("E650:E650\t")

    def test_full_alphabet_membership(self):
        assert baseline_subset._codepoints_in_alphabet("0020:200C:E679", baseline_subset.M1_ALPHABET)
        assert not baseline_subset._codepoints_in_alphabet("E66C", baseline_subset.M1_ALPHABET)
        assert not baseline_subset._codepoints_in_alphabet("garbage", baseline_subset.M1_ALPHABET)


def _seed_repo(tmp_path):
    out = tmp_path / "rebuild" / "out"
    out.mkdir(parents=True)
    _write_table(out / "baseline-default.tsv.gz", ["E670\tqsIt\t0\t\t0,0,150", "E656\tqsThaw\t0\t\t0,0,250"])
    (out / "digests.tsv").write_text("config\trows\tsha256_uncompressed\ndefault\t2\tabc\n")
    return tmp_path


class TestEnsureFresh:
    def test_first_ensure_refilters_and_second_skips_without_touching_bytes(self, tmp_path):
        root = _seed_repo(tmp_path)
        assert baseline_subset.ensure_fresh(root) is True
        subset = root / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz"
        stamp = root / "rebuild" / "out" / "m1" / baseline_subset.STAMP_NAME
        assert subset.exists()
        assert stamp.exists()
        first = subset.read_bytes()
        assert baseline_subset.ensure_fresh(root) is False
        assert subset.read_bytes() == first

    def test_a_source_table_change_reads_as_stale_and_refilters(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        _write_table(
            root / "rebuild" / "out" / "baseline-default.tsv.gz",
            ["E670\tqsIt\t0\t\t0,0,150", "E656\tqsThaw\t0\t\t0,0,250", "E672\tqsEt\t0\t\t0,0,200"],
        )
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        with gzip.open(
            root / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz", "rt", encoding="utf-8"
        ) as fh:
            assert "E672\t" in fh.read()

    def test_an_alphabet_change_moves_the_key(self, tmp_path, monkeypatch):
        root = _seed_repo(tmp_path)
        before = baseline_subset.stamp_key(root)
        monkeypatch.setattr(baseline_subset, "M1_ALPHABET", frozenset({0x0020}))
        assert baseline_subset.stamp_key(root) != before

    def test_a_deleted_output_reads_as_stale(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        subset = root / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz"
        subset.unlink()
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        assert subset.exists()

    def test_refiltering_unchanged_sources_is_byte_identical(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.refresh(root)
        subset = root / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz"
        first = subset.read_bytes()
        baseline_subset.refresh(root)
        assert subset.read_bytes() == first

    def test_main_hand_run_refilters_unconditionally(self, tmp_path, monkeypatch, capsys):
        root = _seed_repo(tmp_path)
        assert baseline_subset.ensure_fresh(root) is True
        monkeypatch.setattr(baseline_subset, "REPO_ROOT", root)
        baseline_subset.main()
        assert "kept 1 rows" in capsys.readouterr().out

    def test_refresh_prunes_outputs_no_longer_backed_by_a_source(self, tmp_path):
        """The orphan direction: a subset whose source vanished must not linger for the oracle's fixed config list to stream forever."""
        root = _seed_repo(tmp_path)
        out = root / "rebuild" / "out"
        _write_table(out / "baseline-ss04.tsv.gz", ["E670\tqsIt\t0\t\t0,0,150"])
        baseline_subset.ensure_fresh(root)
        orphan = out / "m1" / "baseline-ss04.subset.tsv.gz"
        assert orphan.exists()
        (out / "baseline-ss04.tsv.gz").unlink()
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        assert not orphan.exists()

    def test_an_extra_output_on_disk_reads_as_stale_and_is_pruned(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        rogue = root / "rebuild" / "out" / "m1" / "baseline-zz.subset.tsv.gz"
        rogue.write_bytes(b"junk")
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        assert not rogue.exists()

    def test_a_corrupted_output_reads_as_stale_and_is_refiltered(self, tmp_path):
        """A 0-byte gz streams as an empty table and the oracle gate never notices, so the stamp's content hashes are what keep a truncated subset from riding a recorded green."""
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        subset = root / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz"
        subset.write_bytes(b"")
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        assert subset.stat().st_size > 0
        assert baseline_subset.is_fresh(root) is True

    def test_the_stamp_records_each_outputs_hash(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.refresh(root)
        out = root / "rebuild" / "out" / "m1"
        stamp = json.loads((out / baseline_subset.STAMP_NAME).read_text())
        assert stamp["outputs"]
        for name, digest in stamp["outputs"].items():
            assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest

    def test_a_malformed_stamp_reads_as_stale_without_crashing(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        stamp = root / "rebuild" / "out" / "m1" / baseline_subset.STAMP_NAME
        good_key = baseline_subset.stamp_key(root)
        for payload in (
            "{",
            "[]",
            '{"key": 5}',
            json.dumps({"key": good_key, "outputs": ["baseline-default.subset.tsv.gz"]}),
            json.dumps({"key": good_key, "outputs": {"baseline-default.subset.tsv.gz": 5}}),
        ):
            stamp.write_text(payload)
            assert baseline_subset.is_fresh(root) is False


class TestFilterTriage:
    def test_filters_on_the_codepoints_column(self, tmp_path):
        source = tmp_path / "equivalence-triage.tsv"
        source.write_text(
            "config\tcheck\tcodepoints\tbaseline_glyphs\tboundary_glyphs\tfirst\tbs\tns\tkind\n"
            "default\tzwnj-vs-edge\t200C:E650\ta\tb\t0\tx\ty\tname\n"
            "default\tzwnj-vs-edge\t200C:E656\ta\tb\t0\tx\ty\tname\n"
        )
        destination = tmp_path / "triage.subset.tsv"
        kept = baseline_subset.filter_triage(source, destination)
        assert kept == 1
        content = destination.read_text()
        assert content.startswith("config\t")
        assert "200C:E650" in content
        assert "E656" not in content
