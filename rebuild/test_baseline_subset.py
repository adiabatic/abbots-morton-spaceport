"""baseline_subset tests over synthetic tables: the row filter itself, the stamp-keyed freshness contract run_m1 leans on so a stale subset can never feed the oracle, the default-covered identity proof the refilter refuses to stamp around, and the names sidecar the alias check reads instead of the tables."""

import gzip
import hashlib
import json

import pytest

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
            "E657\tqsThey\t0\t\t0,0,250",
            "E652:E670\tqsTea.half.ex-y5|qsIt.en-y5.ex-y0\t0,1\ty5\t0,0,100|0,0,100",
            "E652:E657\tqsTea|qsThey\t0,1\tbreak\t0,0,100|0,0,250",
        ]
        _write_table(source, rows)
        destination = tmp_path / "out" / "baseline-default.subset.tsv.gz"
        result = baseline_subset.filter_table(source, destination)
        assert result.kept == 2
        with gzip.open(destination, "rt", encoding="utf-8") as fh:
            content = fh.read()
        assert content.startswith("# baseline-extractor v1\n# config: default\n")
        assert "E670\t" in content
        assert "E652:E670\t" in content
        assert "E657" not in content

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


SEED_ROWS = ["E670\tqsIt\t0\t\t0,0,150", "E657\tqsThey\t0\t\t0,0,250"]


def _write_sources(out, rows):
    """The reference table and the three configurations the acceptance gate covers through it, all filtering to the same rows — the shape the identity proof demands of a healthy tree."""
    for config in (baseline_subset.IDENTITY_REFERENCE,) + baseline_subset.DEFAULT_COVERED_CONFIGS:
        _write_table(out / f"baseline-{config}.tsv.gz", rows)


def _seed_repo(tmp_path):
    out = tmp_path / "rebuild" / "out"
    out.mkdir(parents=True)
    _write_sources(out, SEED_ROWS)
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
        _write_sources(root / "rebuild" / "out", SEED_ROWS + ["E672\tqsEt\t0\t\t0,0,200"])
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
        outputs = json.loads(stamp.read_text())["outputs"]
        for payload in (
            "{",
            "[]",
            '{"key": 5}',
            json.dumps({"key": good_key, "outputs": ["baseline-default.subset.tsv.gz"]}),
            json.dumps({"key": good_key, "outputs": {"baseline-default.subset.tsv.gz": 5}}),
            json.dumps({"format": baseline_subset.STAMP_FORMAT, "key": good_key, "outputs": outputs}),
            json.dumps({"format": "ams-baseline-subset-stamp/1", "key": good_key, "outputs": outputs}),
        ):
            stamp.write_text(payload)
            assert baseline_subset.is_fresh(root) is False


class TestDefaultCovered:
    """The ss06/ss07/ss06+ss07 proof, made where the tables are written. What it is defending is the acceptance gate's coverage claim: those three run through default's arm alone, which is only sound while their filtered rows are default's filtered rows."""

    def test_identical_roster_tables_stamp_fresh(self, tmp_path):
        root = _seed_repo(tmp_path)
        assert baseline_subset.ensure_fresh(root) is True
        assert baseline_subset.is_fresh(root) is True

    def test_a_diverged_roster_table_refuses_and_leaves_no_stamp(self, tmp_path):
        root = _seed_repo(tmp_path)
        _write_table(
            root / "rebuild" / "out" / "baseline-ss06.tsv.gz",
            ["E670\tqsIt.x\t0\t\t0,0,150", "E657\tqsThey\t0\t\t0,0,250"],
        )
        with pytest.raises(baseline_subset.SubsetIdentityError) as error:
            baseline_subset.refresh(root)
        message = str(error.value)
        assert "ss06" in message
        assert "default" in message
        assert "ACCEPTANCE_CONFIGS" in message
        assert baseline_subset.is_fresh(root) is False

    def test_a_missing_roster_source_refuses_naming_it(self, tmp_path):
        root = _seed_repo(tmp_path)
        (root / "rebuild" / "out" / "baseline-ss07.tsv.gz").unlink()
        with pytest.raises(baseline_subset.SubsetIdentityError, match="ss07"):
            baseline_subset.refresh(root)


class TestSubsetNames:
    """The sidecar that replaced a ten-million-row stream: the alias check's whole input, written once per refilter and stamped so it cannot go missing behind a fresh reading."""

    def test_the_sidecar_holds_the_kept_rows_distinct_names_per_config(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.refresh(root)
        names = baseline_subset.read_subset_names(root / "rebuild" / "out" / "m1")
        assert names["default"] == ["qsIt"]
        assert names["ss06"] == ["qsIt"]
        assert "qsThey" not in names["default"]

    def test_ligation_grain_names_are_split_and_sorted(self, tmp_path):
        root = _seed_repo(tmp_path)
        _write_sources(
            root / "rebuild" / "out",
            ["E652:E670\tqsTea.half.ex-y5|qsIt.en-y5\t0,1\ty5\t0,0,100|0,0,100"] + SEED_ROWS,
        )
        baseline_subset.refresh(root)
        names = baseline_subset.read_subset_names(root / "rebuild" / "out" / "m1")
        assert names["default"] == ["qsIt", "qsIt.en-y5", "qsTea.half.ex-y5"]

    def test_the_stamp_records_the_sidecars_hash(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.refresh(root)
        out = root / "rebuild" / "out" / "m1"
        stamp = json.loads((out / baseline_subset.STAMP_NAME).read_text())
        recorded = stamp["sidecars"][baseline_subset.NAMES_NAME]
        assert hashlib.sha256((out / baseline_subset.NAMES_NAME).read_bytes()).hexdigest() == recorded

    def test_a_deleted_or_edited_sidecar_reads_as_stale_and_is_regenerated(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        sidecar = root / "rebuild" / "out" / "m1" / baseline_subset.NAMES_NAME
        first = sidecar.read_bytes()
        sidecar.unlink()
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        assert sidecar.read_bytes() == first
        sidecar.write_text('{"format": "ams-baseline-subset-names/1", "names": {}}\n')
        assert baseline_subset.is_fresh(root) is False
        assert baseline_subset.ensure_fresh(root) is True
        assert sidecar.read_bytes() == first

    def test_a_missing_sidecar_names_ensure_fresh_as_the_remedy(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        (root / "rebuild" / "out" / "m1" / baseline_subset.NAMES_NAME).unlink()
        with pytest.raises(FileNotFoundError, match="ensure_fresh"):
            baseline_subset.read_subset_names(root / "rebuild" / "out" / "m1")

    def test_a_wrong_format_or_shape_is_a_loud_refusal(self, tmp_path):
        root = _seed_repo(tmp_path)
        baseline_subset.ensure_fresh(root)
        sidecar = root / "rebuild" / "out" / "m1" / baseline_subset.NAMES_NAME
        sidecar.write_text('{"format": "ams-baseline-subset-names/0", "names": {}}\n')
        with pytest.raises(ValueError):
            baseline_subset.read_subset_names(sidecar.parent)
        sidecar.write_text('{"format": "ams-baseline-subset-names/1", "names": []}\n')
        with pytest.raises(ValueError):
            baseline_subset.read_subset_names(sidecar.parent)
