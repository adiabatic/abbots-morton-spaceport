"""Tests for the general table-vs-table treaty-diff mode: added/removed/changed classification on synthetic table pairs, remove+add pairing into regrouped rows, provenance-only demotion, witness search that re-settles to the changed row, and the snapshot round-trip."""

import warnings
from pathlib import Path

import pytest

from rebuild.pipeline.conform import features_for_config
from rebuild.review import tablediff
from rebuild.review.enrich import load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
M1_DIR = REPO_ROOT / "rebuild" / "out" / "m1"

SETTLEMENT_OLD = """# settlement table, config default
input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance
qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.hapax.en-y5\t-\tglyph_data/runes/qsIt.yaml:policy.extend[0]
qsMay\tqsPea.full.ex-y0\tqsIt\t-\tqsMay.loop.en-y0\t-\t
qsPea\tspace uni200C\t-\t-\tqsPea.full\t-\told-pointer
qsTea\tqsOy.hapax.ex-y0\t-\t-\tqsTea.full.en-y0\t-\t
"""

SETTLEMENT_NEW = """# settlement table, config default
input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance
qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.hapax.en-y5.en-ext-1\t-\tglyph_data/runes/qsIt.yaml:policy.extend[0]
qsOy\t-\t-\t-\tqsOy.hapax\t-\t
qsPea\tspace\t-\t-\tqsPea.full\t-\tnew-pointer
qsPea\tuni200C\t-\t-\tqsPea.full.locked\t-\tnew-pointer
qsTea\tqsOy.hapax.ex-y0\t-\t-\tqsTea.full.en-y0\t-\t
"""

TREATY_OLD = """# treaty table, config default
left\tright\tjunction\textension\tkern
qsIt.hapax\tqsIt.hapax\tbreak\t0\t0
qsTea.half.ex-y5\tqsIt.hapax.en-y5\ty5\t0\t0
"""

TREATY_NEW = """# treaty table, config default
left\tright\tjunction\textension\tkern
qsIt.hapax\tqsIt.hapax\tbreak\t0\t0
qsTea.half.ex-y5\tqsIt.hapax.en-y5\ty5\t1\t0
qsOy.hapax.ex-y0\tqsTea.full.en-y0\ty0\t0\t0
"""


@pytest.fixture()
def table_dirs(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    (old_dir / "settlement-default.tsv").write_text(SETTLEMENT_OLD)
    (new_dir / "settlement-default.tsv").write_text(SETTLEMENT_NEW)
    (old_dir / "treaties-default.tsv").write_text(TREATY_OLD)
    (new_dir / "treaties-default.tsv").write_text(TREATY_NEW)
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
    assert changed[0].old.outcome == "qsIt.hapax.en-y5"
    assert changed[0].new.outcome == "qsIt.hapax.en-y5.en-ext-1"

    added = [entry for entry in by_bucket["added"] if entry.table == "settlement"]
    assert [entry.key.input for entry in added] == ["qsOy"]

    removed = [entry for entry in by_bucket["removed"] if entry.table == "settlement"]
    assert [entry.key.input for entry in removed] == ["qsMay"]

    treaty_changed = [entry for entry in by_bucket["changed"] if entry.table == "treaty"]
    assert len(treaty_changed) == 1
    assert treaty_changed[0].old.extension == 0
    assert treaty_changed[0].new.extension == 1
    treaty_added = [entry for entry in by_bucket["added"] if entry.table == "treaty"]
    assert [entry.key.left for entry in treaty_added] == ["qsOy.hapax.ex-y0"]


def test_regrouped_pairs_removals_with_additions_sharing_input(table_dirs):
    old_dir, new_dir = table_dirs
    entries = tablediff.diff_dirs(old_dir, new_dir)
    regrouped = [entry for entry in entries if entry.bucket == "regrouped"]
    assert len(regrouped) == 1
    entry = regrouped[0]
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
    base = "qsIt\t-\t-\t-\tqsIt.hapax\t-\t{pointer}\n"
    header = "# settlement table, config default\ninput\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance\n"
    (old_dir / "settlement-default.tsv").write_text(header + base.format(pointer="old"))
    (new_dir / "settlement-default.tsv").write_text(header + base.format(pointer="new"))
    entries = tablediff.diff_dirs(old_dir, new_dir)
    assert [entry.bucket for entry in entries] == ["provenance-only"]
    assert entries[0].old.outcome == entries[0].new.outcome


def test_self_diff_is_empty():
    entries = tablediff.diff_dirs(M1_DIR, M1_DIR)
    assert entries == []


def test_diff_is_deterministic(table_dirs):
    old_dir, new_dir = table_dirs
    first = tablediff.diff_dirs(old_dir, new_dir)
    second = tablediff.diff_dirs(old_dir, new_dir)
    assert [(e.bucket, e.table, e.key.label()) for e in first] == [
        (e.bucket, e.table, e.key.label()) for e in second
    ]


@pytest.fixture(scope="module")
def witness_index():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    return spec, tablediff.WitnessIndex(spec, "default", max_depth=3)


def test_witness_resettles_to_the_settlement_row(witness_index):
    from rebuild.pipeline.settle import settle, cell_label

    spec, index = witness_index
    rows = tablediff.load_settlement(M1_DIR / "settlement-default.tsv")
    checked = 0
    for key, value in list(rows.items())[::10]:
        witness = index.witness_settlement(key)
        if witness is None:
            continue
        settled = settle(spec, list(witness), features_for_config("default"))
        labels = [cell_label(spec, item.cell) for item in settled]
        assert value.outcome in labels, (key.label(), value.outcome, labels)
        checked += 1
    assert checked >= 5


def test_witness_resettles_to_the_treaty_pair(witness_index):
    from rebuild.pipeline.settle import settle, cell_label

    spec, index = witness_index
    rows = tablediff.load_treaty(M1_DIR / "treaties-default.tsv")
    checked = 0
    for key in list(rows)[::25]:
        witness = index.witness_treaty(key)
        if witness is None:
            continue
        settled = settle(spec, list(witness), features_for_config("default"))
        labels = [cell_label(spec, item.cell) for item in settled]
        assert (key.left, key.right) in set(zip(labels, labels[1:]))
        checked += 1
    assert checked >= 5


def test_witness_attach_fills_entries(witness_index, table_dirs):
    _spec, index = witness_index
    old_dir, new_dir = table_dirs
    entries = tablediff.diff_dirs(old_dir, new_dir)
    index.attach(entries)
    changed = next(e for e in entries if e.bucket == "changed" and e.table == "settlement")
    assert changed.witness is not None


def test_snapshot_round_trip(tmp_path):
    snapshot_dir = tmp_path / "accepted"
    snapshot = tablediff.write_snapshot(M1_DIR, M1_DIR / "M1.otf", snapshot_dir, REPO_ROOT)
    assert (snapshot_dir / "snapshot.json").exists()
    assert (snapshot_dir / "M1.otf").exists()
    assert "settlement-default.tsv" in snapshot["files"]
    assert snapshot["files"]["M1.otf"]["sha256"]
    assert tablediff.diff_dirs(snapshot_dir, M1_DIR) == []
