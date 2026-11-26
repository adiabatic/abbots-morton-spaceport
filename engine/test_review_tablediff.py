"""Tests for the row diff the table-diff corpus reads its evidence from (`engine/review/tablediff.py`): added, removed, and changed classification on synthetic table pairs, pairing of removals and additions into regrouped rows, provenance-only demotion, the table widths and gzipped tables the loaders read, the byte comparison that passes over an unchanged table, and the settled configurations the diff reads. `engine/test_review_diff_units.py` covers the units the corpus makes of the rows, and `engine/test_review_snapshot.py` the snapshot. The classification and self-diff tests use the synthetic pair or the frozen mini bundle's tables."""

import gzip
import shutil
from pathlib import Path

import pytest

from engine.pipeline import conform
from engine.review import tablediff

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "engine" / "review" / "fixtures" / "mini"

SETTLEMENT_OLD = """# settlement table, config default
input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance
qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.sole.en-y5\t-\tglyph_data/runes/qsIt.yaml:policy.extend[0]
qsMay\tqsPea.full.ex-y0\tqsIt\t-\tqsMay.loop.en-y0\t-\t
qsPea\tspace uni200C\t-\t-\tqsPea.full\t-\told-pointer
qsTea\tqsOy.sole.ex-y0\t-\t-\tqsTea.full.en-y0\t-\t
"""

SETTLEMENT_NEW = """# settlement table, config default
input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance
qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.sole.en-y5.en-ext-1\t-\tglyph_data/runes/qsIt.yaml:policy.extend[0]
qsOy\t-\t-\t-\tqsOy.sole\t-\t
qsPea\tspace\t-\t-\tqsPea.full\t-\tnew-pointer
qsPea\tuni200C\t-\t-\tqsPea.full.locked\t-\tnew-pointer
qsTea\tqsOy.sole.ex-y0\t-\t-\tqsTea.full.en-y0\t-\t
"""

JOINS_OLD = """# join table, config default
left\tright\tjunction\textension
qsIt.sole\tqsIt.sole\tbreak\t0
qsTea.half.ex-y5\tqsIt.sole.en-y5\ty5\t0
"""

JOINS_NEW = """# join table, config default
left\tright\tjunction\textension
qsIt.sole\tqsIt.sole\tbreak\t0
qsTea.half.ex-y5\tqsIt.sole.en-y5\ty5\t1
qsOy.sole.ex-y0\tqsTea.full.en-y0\ty0\t0
"""


@pytest.fixture()
def table_dirs(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    (old_dir / "settlement-default.tsv").write_text(SETTLEMENT_OLD)
    (new_dir / "settlement-default.tsv").write_text(SETTLEMENT_NEW)
    (old_dir / "joins-default.tsv").write_text(JOINS_OLD)
    (new_dir / "joins-default.tsv").write_text(JOINS_NEW)
    return old_dir, new_dir


def test_diff_classifies_buckets(table_dirs):
    old_dir, new_dir = table_dirs
    entries = tablediff.diff_dirs(old_dir, new_dir)
    by_bucket = {}
    for entry in entries:
        by_bucket.setdefault(entry.bucket, []).append(entry)

    changed = [entry for entry in by_bucket["changed"] if entry.table == "settlement"]
    assert len(changed) == 1
    assert changed[0].key.input == "qsIt"
    assert changed[0].old.outcome == "qsIt.sole.en-y5"
    assert changed[0].new.outcome == "qsIt.sole.en-y5.en-ext-1"

    added = [entry for entry in by_bucket["added"] if entry.table == "settlement"]
    assert [entry.key.input for entry in added] == ["qsOy"]

    removed = [entry for entry in by_bucket["removed"] if entry.table == "settlement"]
    assert [entry.key.input for entry in removed] == ["qsMay"]

    joins_changed = [entry for entry in by_bucket["changed"] if entry.table == "join"]
    assert len(joins_changed) == 1
    assert joins_changed[0].old.extension == 0
    assert joins_changed[0].new.extension == 1
    joins_added = [entry for entry in by_bucket["added"] if entry.table == "join"]
    assert [entry.key.left for entry in joins_added] == ["qsOy.sole.ex-y0"]


def test_regrouped_pairs_removals_with_additions_sharing_input(table_dirs):
    old_dir, new_dir = table_dirs
    entries = tablediff.diff_dirs(old_dir, new_dir)
    regrouped = [entry for entry in entries if entry.bucket == "regrouped"]
    assert len(regrouped) == 1
    entry = regrouped[0]
    assert isinstance(entry, tablediff.SettlementDiffEntry)
    assert entry.key.input == "qsPea"
    old_sides = [member for member in entry.paired if member.old is not None]
    new_sides = [member for member in entry.paired if member.new is not None]
    assert len(old_sides) == 1
    assert len(new_sides) == 2
    assert not any(
        member.key.input == "qsPea"
        for member in entries
        if member.bucket in ("added", "removed") and member.table == "settlement"
    )


def test_provenance_only_demotion(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    base = "qsIt\t-\t-\t-\tqsIt.sole\t-\t{pointer}\n"
    header = "# settlement table, config default\ninput\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance\n"
    (old_dir / "settlement-default.tsv").write_text(header + base.format(pointer="old"))
    (new_dir / "settlement-default.tsv").write_text(header + base.format(pointer="new"))
    entries = tablediff.diff_dirs(old_dir, new_dir)
    assert [entry.bucket for entry in entries] == ["provenance-only"]
    entry = entries[0]
    assert isinstance(entry, tablediff.SettlementDiffEntry)
    assert entry.old is not None
    assert entry.new is not None
    assert entry.old.outcome == entry.new.outcome


def test_load_settlement_widths_round_trip(tmp_path):
    def load(body):
        path = tmp_path / "settlement-default.tsv"
        path.write_text(body)
        return tablediff.load_settlement(path)

    seven = load(
        "input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.sole.en-y5\t-\t\n"
    )
    eight = load(
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\t-\tqsIt.sole.en-y5\t-\t\n"
    )
    nine = load(
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\t-\tqsLow\tqsIt.sole.en-y5\t-\t\n"
    )

    legacy = tablediff.SettlementKey("default", "qsIt", frozenset({"qsTea.half.ex-y5"}), None, None, None)
    assert list(seven) == [legacy]
    assert list(eight) == [legacy]
    assert next(iter(seven)).look4 is None
    assert next(iter(eight)).look4 is None

    with_look4 = tablediff.SettlementKey(
        "default", "qsIt", frozenset({"qsTea.half.ex-y5"}), None, None, None, frozenset({"qsLow"})
    )
    assert list(nine) == [with_look4]
    assert with_look4 != legacy
    assert next(iter(nine)).look4 == frozenset({"qsLow"})


def test_self_diff_is_empty(table_dirs):
    """A directory diffed against itself gives no entries."""
    old_dir, _new_dir = table_dirs
    assert tablediff.diff_dirs(old_dir, old_dir) == []


def test_diff_is_deterministic(table_dirs):
    old_dir, new_dir = table_dirs
    first = tablediff.diff_dirs(old_dir, new_dir)
    second = tablediff.diff_dirs(old_dir, new_dir)
    assert [(e.bucket, e.table, e.key.label()) for e in first] == [
        (e.bucket, e.table, e.key.label()) for e in second
    ]


def test_a_join_table_with_the_old_kern_column_reads_as_the_same_rows(tmp_path):
    """A baseline build directory written while the join table had a `kern` column (always 0) still loads: the fifth field is ignored, and the rows equal the four-column table's."""
    current = MINI / "joins-default.tsv"
    lines = current.read_text().splitlines()
    widened = [
        line if line.startswith("#") else line + ("\tkern" if line.startswith("left\t") else "\t0")
        for line in lines
    ]
    old = tmp_path / "joins-default.tsv"
    old.write_text("\n".join(widened) + "\n")
    assert tablediff.load_joins(old) == tablediff.load_joins(current)
    assert len(tablediff.load_joins(old)) == len(
        [line for line in lines if not line.startswith(("#", "left\t"))]
    )


def test_a_gzipped_table_reads_as_the_plain_one_and_compares_by_its_rows(tmp_path):
    """A snapshot holds its tables gzipped; they load as the plain tables do, take their configuration from the name before `.tsv.gz`, and `same_table` compares them with a plain table byte for byte."""
    for name, loader in (
        ("settlement-default.tsv", tablediff.load_settlement),
        ("joins-default.tsv", tablediff.load_joins),
    ):
        packed = tmp_path / f"{name}.gz"
        with open(MINI / name, "rb") as source, gzip.GzipFile(packed, "wb") as target:
            shutil.copyfileobj(source, target)
        assert loader(packed) == loader(MINI / name)
        assert tablediff.same_table(packed, MINI / name)
        changed = tmp_path / name
        changed.write_bytes((MINI / name).read_bytes() + b"x")
        assert not tablediff.same_table(packed, changed)
        assert not tablediff.same_table(changed, packed)


def test_the_settled_configurations_match_the_builds():
    """The review package cannot import `conform`, so `tablediff.SETTLED_CONFIGS` restates the build's settled configurations; this ties the two lists together."""
    assert tablediff.SETTLED_CONFIGS == conform.SETTLEMENT_CONFIGS


def test_a_diff_ignores_tables_of_a_configuration_outside_the_settled_set(table_dirs):
    """Tables left under the name of a configuration the build does not settle, on either side, add no entries to a diff that takes its configurations from the directories."""
    old_dir, new_dir = table_dirs
    for unsettled in ("ss02+ss03", *conform.OVERLAY_CONFIGS):
        assert unsettled not in conform.SETTLEMENT_CONFIGS
        (old_dir / f"settlement-{unsettled}.tsv").write_text(SETTLEMENT_OLD)
        (old_dir / f"joins-{unsettled}.tsv").write_text(JOINS_OLD)
        (new_dir / f"settlement-{unsettled}.tsv").write_text(SETTLEMENT_NEW)
        (new_dir / f"joins-{unsettled}.tsv").write_text(JOINS_NEW)
    (new_dir / "settlement-ss02.tsv").write_text(SETTLEMENT_NEW)
    (new_dir / "joins-ss02.tsv").write_text(JOINS_NEW)
    assert tablediff.table_configs(old_dir) == tablediff.table_configs(new_dir) == ["default"]
    assert {entry.config for entry in tablediff.diff_dirs(old_dir, new_dir)} == {"default"}
