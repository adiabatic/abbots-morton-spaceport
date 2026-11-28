"""Tests for the row diff the table-diff corpus reads its evidence from (`engine/review/tablediff.py`): added, removed, and changed classification on synthetic table pairs, pairing of removals and additions into regrouped rows, provenance-only demotion, the row width and gzipped tables the loaders read, the byte comparison that passes over an unchanged table, and the settled configurations the diff reads. `engine/test_review_diff_units.py` covers the units the corpus makes of the rows, and `engine/test_review_snapshot.py` the snapshot. The tables the tests read are synthetic, written inline."""

import gzip
from pathlib import Path

import pytest

from engine.pipeline import conform
from engine.review import tablediff

SETTLEMENT_OLD = """# settlement table, config default
input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance
qsIt\tqsTea.half.ex-y5\t-\t-\t-\t-\tqsIt.sole.en-y5\t-\tglyph_data/runes/qsIt.yaml:policy.extend[0]
qsMay\tqsPea.full.ex-y0\tqsIt\t-\t-\t-\tqsMay.loop.en-y0\t-\t
qsPea\tspace uni200C\t-\t-\t-\t-\tqsPea.full\t-\told-pointer
qsTea\tqsOy.sole.ex-y0\t-\t-\t-\t-\tqsTea.full.en-y0\t-\t
"""

SETTLEMENT_NEW = """# settlement table, config default
input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance
qsIt\tqsTea.half.ex-y5\t-\t-\t-\t-\tqsIt.sole.en-y5.en-ext-1\t-\tglyph_data/runes/qsIt.yaml:policy.extend[0]
qsOy\t-\t-\t-\t-\t-\tqsOy.sole\t-\t
qsPea\tspace\t-\t-\t-\t-\tqsPea.full\t-\tnew-pointer
qsPea\tuni200C\t-\t-\t-\t-\tqsPea.full.locked\t-\tnew-pointer
qsTea\tqsOy.sole.ex-y0\t-\t-\t-\t-\tqsTea.full.en-y0\t-\t
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


def diff_dirs(old_dir: Path, new_dir: Path, config: str = "default") -> list[tablediff.DiffEntry]:
    """Diff one configuration's settlement and join tables in two directories, as `build.config_diffs` diffs a snapshot's against a build's, a missing table reading as one with no rows."""

    def rows(path: Path, loader):
        return loader(path) if path.exists() else {}

    settlement = tablediff.diff_settlement(
        rows(old_dir / f"settlement-{config}.tsv", tablediff.load_settlement),
        rows(new_dir / f"settlement-{config}.tsv", tablediff.load_settlement),
        config,
    )
    joins = tablediff.diff_joins(
        rows(old_dir / f"joins-{config}.tsv", tablediff.load_joins),
        rows(new_dir / f"joins-{config}.tsv", tablediff.load_joins),
        config,
    )
    return [*settlement, *joins]


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
    entries = diff_dirs(old_dir, new_dir)
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
    entries = diff_dirs(old_dir, new_dir)
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
    base = "qsIt\t-\t-\t-\t-\t-\tqsIt.sole\t-\t{pointer}\n"
    header = "# settlement table, config default\ninput\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance\n"
    (old_dir / "settlement-default.tsv").write_text(header + base.format(pointer="old"))
    (new_dir / "settlement-default.tsv").write_text(header + base.format(pointer="new"))
    entries = diff_dirs(old_dir, new_dir)
    assert [entry.bucket for entry in entries] == ["provenance-only"]
    entry = entries[0]
    assert isinstance(entry, tablediff.SettlementDiffEntry)
    assert entry.old is not None
    assert entry.new is not None
    assert entry.old.outcome == entry.new.outcome


def test_load_settlement_reads_every_lookahead_and_refuses_a_narrower_row(tmp_path):
    """A settlement row has the nine columns the build writes; each lookahead column is its own key slot, and a row with fewer columns is refused rather than read with its slots shifted."""
    path = tmp_path / "settlement-default.tsv"
    path.write_text(
        "# settlement table, config default\n"
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\t-\tqsLow\tqsIt.sole.en-y5\t-\t\n"
    )
    assert list(tablediff.load_settlement(path)) == [
        tablediff.SettlementKey(
            "default", "qsIt", frozenset({"qsTea.half.ex-y5"}), None, None, None, frozenset({"qsLow"})
        )
    ]
    path.write_text(
        "input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.sole.en-y5\t-\t\n"
    )
    with pytest.raises(ValueError, match="7 fields, not 9"):
        tablediff.load_settlement(path)


def test_self_diff_is_empty(table_dirs):
    """A directory diffed against itself gives no entries."""
    old_dir, _new_dir = table_dirs
    assert diff_dirs(old_dir, old_dir) == []


def test_diff_is_deterministic(table_dirs):
    old_dir, new_dir = table_dirs
    first = diff_dirs(old_dir, new_dir)
    second = diff_dirs(old_dir, new_dir)
    assert [(e.bucket, e.table, e.key.label()) for e in first] == [
        (e.bucket, e.table, e.key.label()) for e in second
    ]


def test_a_gzipped_table_reads_as_the_plain_one_and_compares_by_its_rows(tmp_path, monkeypatch):
    """A snapshot holds its tables gzipped; they load as the plain tables do, take their configuration from the name before `.tsv.gz`, and `same_table` compares them with a plain table byte for byte. The compare chunk is shrunk so the tables span many chunks and the changed byte lies past the first."""
    monkeypatch.setattr(tablediff, "_COMPARE_CHUNK", 16)
    for name, body, loader in (
        ("settlement-default.tsv", SETTLEMENT_OLD, tablediff.load_settlement),
        ("joins-default.tsv", JOINS_OLD, tablediff.load_joins),
    ):
        plain = tmp_path / "plain" / name
        plain.parent.mkdir(exist_ok=True)
        plain.write_text(body)
        packed = tmp_path / f"{name}.gz"
        with gzip.GzipFile(packed, "wb") as target:
            target.write(body.encode())
        assert loader(packed) == loader(plain)
        assert {key.config for key in loader(packed)} == {"default"}
        assert tablediff.same_table(packed, plain)
        changed = tmp_path / name
        changed.write_text(body + "x")
        assert not tablediff.same_table(packed, changed)
        assert not tablediff.same_table(changed, packed)


def test_the_settled_configurations_match_the_builds():
    """The review package cannot import `conform`, so `tablediff.SETTLED_CONFIGS` restates the build's settled configurations; this ties the two lists together."""
    assert tablediff.SETTLED_CONFIGS == conform.SETTLEMENT_CONFIGS
