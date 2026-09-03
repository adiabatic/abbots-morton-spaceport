"""Tests for the general table-vs-table treaty-diff mode: added/removed/changed classification on synthetic table pairs, remove+add pairing into regrouped rows, provenance-only demotion, witness search that re-settles to the changed row, and the snapshot round-trip.

The two witness arms re-settle real settlement rows and treaty pairs and check the outcome comes back — over the frozen mini-M1 bundle's tables, under the spec `mini_bundle` materializes, which is the spec those tables were built from. That pairing is what the arms need and all they need: a witness re-settling to its own row is a property of `WitnessIndex` against the spec the row came from, not a claim about today's rules. The classification, the round trip, and the self-diff are properties of `diff_dirs` and `write_snapshot` over any tables, so they take the synthetic pair or the same bundle; the whole file runs in the contracts lane.
"""

import warnings
from pathlib import Path

import pytest

from rebuild.pipeline import explain
from rebuild.pipeline.conform import features_for_config
from rebuild.pipeline.settle import cell_label
from rebuild.review import tablediff
from rebuild.review.enrich import load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"

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
    base = "qsIt\t-\t-\t-\tqsIt.hapax\t-\t{pointer}\n"
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
        "qsIt\tqsTea.half.ex-y5\t-\t-\tqsIt.hapax.en-y5\t-\t\n"
    )
    eight = load(
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\t-\tqsIt.hapax.en-y5\t-\t\n"
    )
    nine = load(
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance\n"
        "qsIt\tqsTea.half.ex-y5\t-\t-\t-\tqsLow\tqsIt.hapax.en-y5\t-\t\n"
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
    """A directory diffed against itself is empty — a property of `diff_dirs`, not of any particular tables, which is why the synthetic pair witnesses it as well as the live one did and without reaching rebuild/out."""
    old_dir, _new_dir = table_dirs
    assert tablediff.diff_dirs(old_dir, old_dir) == []


def test_diff_is_deterministic(table_dirs):
    old_dir, new_dir = table_dirs
    first = tablediff.diff_dirs(old_dir, new_dir)
    second = tablediff.diff_dirs(old_dir, new_dir)
    assert [(e.bucket, e.table, e.key.label()) for e in first] == [
        (e.bucket, e.table, e.key.label()) for e in second
    ]


@pytest.fixture(scope="module")
def witness_index(mini_bundle):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    return spec, tablediff.WitnessIndex(spec, "default", max_depth=3)


def test_witness_resettles_to_the_settlement_row(witness_index):
    """Every witness the index hands back for a frozen settlement row settles to that row's outcome under the spec the row was written by. The whole stride is gathered first and explained in one `explain_many` call, so the check costs a handful of kernel invocations rather than one per witness."""
    spec, index = witness_index
    rows = tablediff.load_settlement(MINI / "settlement-default.tsv")
    asked = []
    for key, value in list(rows.items())[::10]:
        witness = index.witness_settlement(key)
        if witness is not None:
            asked.append((key, value, witness))
    assert len(asked) >= 5
    features = features_for_config("default")
    reports = explain.explain_many(spec, [(list(witness), features) for _key, _value, witness in asked])
    for (key, value, _witness), report in zip(asked, reports):
        labels = [cell_label(spec, item.cell) for item in report.settled]
        assert value.outcome in labels, (key.label(), value.outcome, labels)


def test_witness_resettles_to_the_treaty_pair(witness_index):
    """Every witness the index hands back for a frozen treaty row settles to that row's left and right as adjacent cells, batched through one `explain_many` call the same way."""
    spec, index = witness_index
    rows = tablediff.load_treaty(MINI / "treaties-default.tsv")
    asked = []
    for key in list(rows)[::25]:
        witness = index.witness_treaty(key)
        if witness is not None:
            asked.append((key, witness))
    assert len(asked) >= 5
    features = features_for_config("default")
    reports = explain.explain_many(spec, [(list(witness), features) for _key, witness in asked])
    for (key, _witness), report in zip(asked, reports):
        labels = [cell_label(spec, item.cell) for item in report.settled]
        assert (key.left, key.right) in set(zip(labels, labels[1:]))


def test_witness_attach_fills_entries(witness_index, table_dirs):
    _spec, index = witness_index
    old_dir, new_dir = table_dirs
    entries = tablediff.diff_dirs(old_dir, new_dir)
    index.attach(entries)
    changed = next(e for e in entries if e.bucket == "changed" and e.table == "settlement")
    assert changed.witness is not None


def test_snapshot_round_trip(tmp_path):
    """`write_snapshot` copies a table directory's TSVs and the font beside them, records sha256s, and the copy diffs empty against its source. The frozen mini-M1 bundle is the table directory here: real tables and a real font, checked in, so what the round trip is about — the copy, not today's rules — runs in the contracts lane."""
    snapshot_dir = tmp_path / "accepted"
    snapshot = tablediff.write_snapshot(MINI, MINI / "M1.otf", snapshot_dir, REPO_ROOT)
    assert (snapshot_dir / "snapshot.json").exists()
    assert (snapshot_dir / "M1.otf").exists()
    assert "settlement-default.tsv" in snapshot["files"]
    assert snapshot["files"]["M1.otf"]["sha256"]
    assert tablediff.diff_dirs(snapshot_dir, MINI) == []
