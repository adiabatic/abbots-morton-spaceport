"""baseline_subset filter tests over synthetic tables (the real 11-table pass is a one-time Phase 5 run)."""

import gzip

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
            "E651\tqsBay\t0\t\t0,0,300",
            "E652:E670\tqsTea.half.ex-y5|qsIt.en-y5.ex-y0\t0,1\ty5\t0,0,100|0,0,100",
            "E652:E651\tqsTea|qsBay\t0,1\tbreak\t0,0,100|0,0,300",
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
        assert "E651" not in content

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


class TestFilterTriage:
    def test_filters_on_the_codepoints_column(self, tmp_path):
        source = tmp_path / "equivalence-triage.tsv"
        source.write_text(
            "config\tcheck\tcodepoints\tbaseline_glyphs\tboundary_glyphs\tfirst\tbs\tns\tkind\n"
            "default\tzwnj-vs-edge\t200C:E650\ta\tb\t0\tx\ty\tname\n"
            "default\tzwnj-vs-edge\t200C:E651\ta\tb\t0\tx\ty\tname\n"
        )
        destination = tmp_path / "triage.subset.tsv"
        kept = baseline_subset.filter_triage(source, destination)
        assert kept == 1
        content = destination.read_text()
        assert content.startswith("config\t")
        assert "200C:E650" in content
        assert "E651" not in content
