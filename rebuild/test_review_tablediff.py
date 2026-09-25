"""Tests for the table-vs-table treaty-diff mode: added, removed, and changed classification on synthetic table pairs, pairing of removals and additions into regrouped rows, provenance-only demotion, witness search that re-settles to the changed row, and the snapshot round trip.

The two witness tests re-settle real settlement rows and treaty pairs from the frozen mini-M1 bundle's tables and check that the outcome comes back. They settle under the spec `mini_bundle` materializes, which is the spec those tables were built from, so they test `WitnessIndex` and not the current rules. The classification, round-trip, and self-diff tests use the synthetic pair or the same bundle.
"""

import warnings
from pathlib import Path

import pytest

from rebuild.pipeline import conform, explain
from rebuild.pipeline.labels import features_for_config
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


@pytest.fixture(scope="module")
def witness_index(mini_bundle):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    return spec, tablediff.WitnessIndex(spec, "default", max_depth=3)


def test_witness_resettles_to_the_settlement_row(witness_index):
    """Every witness the index returns for a frozen settlement row settles to that row's outcome under the spec that wrote the row. The sampled witnesses are explained in one `explain_many` call, which runs a few kernel processes instead of one per witness."""
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
    """Every witness the index returns for a frozen treaty row settles to that row's left and right as adjacent cells, explained in one `explain_many` call."""
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
    """`write_snapshot` copies a table directory's settlement and treaty TSVs and the font, records their sha256s, and the copy diffs empty against its source. The table directory is the frozen mini-M1 bundle, which has real tables and a real font."""
    snapshot_dir = tmp_path / "accepted"
    snapshot = tablediff.write_snapshot(MINI, MINI / "M1.otf", snapshot_dir, REPO_ROOT)
    assert (snapshot_dir / "snapshot.json").exists()
    assert (snapshot_dir / "M1.otf").exists()
    assert "settlement-default.tsv" in snapshot["files"]
    assert snapshot["files"]["M1.otf"]["sha256"]
    assert tablediff.diff_dirs(snapshot_dir, MINI) == []


def test_the_settled_configurations_match_the_builds():
    """The review package cannot import `conform`, so `tablediff.SETTLED_CONFIGS` restates the build's settled configurations; this ties the two lists together."""
    assert tablediff.SETTLED_CONFIGS == conform.SETTLEMENT_CONFIGS


def test_a_diff_ignores_tables_of_a_configuration_outside_the_settled_set(table_dirs):
    """Tables left under the name of a configuration the build does not settle, on either side, add no entries to a diff that takes its configurations from the directories."""
    old_dir, new_dir = table_dirs
    for unsettled in ("ss02+ss03", *conform.OVERLAY_CONFIGS):
        assert unsettled not in conform.SETTLEMENT_CONFIGS
        (old_dir / f"settlement-{unsettled}.tsv").write_text(SETTLEMENT_OLD)
        (old_dir / f"treaties-{unsettled}.tsv").write_text(TREATY_OLD)
        (new_dir / f"settlement-{unsettled}.tsv").write_text(SETTLEMENT_NEW)
        (new_dir / f"treaties-{unsettled}.tsv").write_text(TREATY_NEW)
    (new_dir / "settlement-ss02.tsv").write_text(SETTLEMENT_NEW)
    (new_dir / "treaties-ss02.tsv").write_text(TREATY_NEW)
    assert tablediff.table_configs(old_dir) == tablediff.table_configs(new_dir) == ["default"]
    assert {entry.config for entry in tablediff.diff_dirs(old_dir, new_dir)} == {"default"}


def test_a_snapshot_leaves_out_tables_of_a_configuration_outside_the_settled_set(table_dirs, tmp_path):
    """`write_snapshot` copies only the tables of settled configurations, so a stale configuration's tables never become part of the accepted state."""
    _old_dir, new_dir = table_dirs
    for unsettled in ("ss02+ss03", *conform.OVERLAY_CONFIGS):
        (new_dir / f"settlement-{unsettled}.tsv").write_text(SETTLEMENT_NEW)
        (new_dir / f"treaties-{unsettled}.tsv").write_text(TREATY_NEW)
    font = tmp_path / "M1.otf"
    font.write_bytes(b"font")
    snapshot_dir = tmp_path / "accepted"
    snapshot = tablediff.write_snapshot(new_dir, font, snapshot_dir, REPO_ROOT)
    assert sorted(snapshot["files"]) == ["M1.otf", "settlement-default.tsv", "treaties-default.tsv"]
    assert sorted(path.name for path in snapshot_dir.glob("*.tsv")) == [
        "settlement-default.tsv",
        "treaties-default.tsv",
    ]
