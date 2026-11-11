"""Tests for the review corpus's M1-mode unit assembly: TSV/ledger loading, the dedupe to per-config-class units (including the UNMATCHED verdict windows that carry a per-config class map), and deterministic triage ordering.

None of it reads the live audit. Ordering, batch slicing, config order, and the one-render-group invariant are properties of `build_units` and `assign_batches` over any input, so the tests use the frozen mini workload under rebuild/review/fixtures/mini/, which holds real windows. Every build also checks that the dedupe loses no rows: `_CorpusCheck.finish` in rebuild/review/build.py compares the manifest's row total with the rows summed over its classes.

The live counts change with every migrated letter, so they are not asserted here. The corpus build's review facts report them, and the artifact cycle diffs them into rebuild/review-facts-pins.json.
"""

import collections
import hashlib
import os
import sys
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from rebuild.pipeline import oracle, row_delta
from rebuild.review import audit_stream, change_groups
from rebuild.review import unit_cache
from rebuild.review import unit_store
from rebuild.review.audit import (
    AuditParts,
    ACCEPTANCE_CONFIGS,
    AUDIT_HEADER,
    LEDGER_STATUSES,
    RULED_STATUSES,
    SECTIONS,
    AuditRow,
    RowColumns,
    LedgerClass,
    assign_batches,
    batch_of,
    build_units,
    class_section,
    format_codepoints,
    load_audit,
    load_audit_table,
    load_ledger,
    load_table,
    load_workload,
    merge_ink_duplicate_units,
    parse_codepoints,
    release_rows,
    render_groups_for_rows,
    sort_for_triage,
    synthesize_unmatched_group_classes,
    UnitTable,
    _counting_permutation,
    _triage_permutation,
    audit_layout,
    signature_rows,
    stream_workload,
    triage_key,
)
from rebuild.review import build as review_build
from rebuild.review.build import row_column_sizes, signature_text, unit_table_sizes
from rebuild.review.columns import MappingPool, TuplePool, WindowIndex
from rebuild.review.facts import PremergeSnapshot, capture_premerge
from rebuild.review.enrich import LETTERS
from rebuild.review.unit_store import UnitStore
from rebuild.tools import memory_tally

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"

FIXTURE_AUDIT = """config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew
default\tE650:E665\tcell\tidentical-unused-join-name\tqsPea|qsMay.en-y0\tqsPea/full/None/baseline/|qsMay/loop/baseline/None/
ss02\tE650:E665\tcell\tidentical-unused-join-name\tqsPea|qsMay.en-y0\tqsPea/full/None/baseline/|qsMay/loop/baseline/None/
default\tE652:E670\tcell,junction\tlonger-entry-join\tqsTea.half.ex-y5|qsIt.en-y5\tqsTea/half/None/x-height/|qsIt/sole/x-height/None/en-ext-1
"""


def test_load_audit_parses_fixture(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    rows = list(load_audit(path))
    assert len(rows) == 3
    assert rows[0].config == "default"
    assert rows[0].baseline == ("qsPea", "qsMay.en-y0")
    assert rows[2].kinds == ("cell", "junction")


def test_load_audit_interns_every_label_and_pools_every_name_tuple(tmp_path):
    """Every label the audit states is interned with `sys.intern`, so a config name, class id or glyph name is one object everywhere it is used. Equal name tuples in two rows are one tuple object, the one held by the pool passed in, so the columns' ids name the rows' own tuples."""
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    names = TuplePool()
    rows = list(load_audit(path, names))
    assert rows[0].config is sys.intern("default") is rows[2].config
    assert rows[0].matched_entry is sys.intern("identical-unused-join-name") is rows[1].matched_entry
    assert rows[0].codepoints is sys.intern("E650:E665") is rows[1].codepoints
    assert rows[0].baseline is rows[1].baseline
    assert rows[0].new is rows[1].new
    assert rows[0].kinds is rows[1].kinds
    assert rows[0].baseline[0] is sys.intern("qsPea")
    assert rows[2].kinds[1] is sys.intern("junction")
    assert names[names.id(rows[0].baseline)] is rows[0].baseline
    assert names[names.id(("cell", "junction"))] is rows[2].kinds


def test_load_audit_rejects_wrong_header(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text("nope\tnope\n")
    with pytest.raises(ValueError):
        list(load_audit(path))
    with pytest.raises(ValueError):
        load_audit_table(path, [], dict(LETTERS))


def test_the_fused_audit_load_matches_load_table_over_load_audit(mini_bundle):
    """`load_audit_table`, which maps each raw field to its id through a memo, assigns every id in the order `load_table(load_audit(...))` does, so every row column, the name pool, the string table and the config vocabulary come out identical over the mini audit."""
    ledger = load_ledger(mini_bundle.ledger)
    names = TuplePool()
    _expected_table, expected = load_table(load_audit(MINI_AUDIT, names), ledger, dict(LETTERS), names)
    _table, fused = load_audit_table(MINI_AUDIT, ledger, dict(LETTERS))
    assert len(fused) > 0
    assert list(fused.columns()) == list(expected.columns())
    assert fused.names._tuples == expected.names._tuples and fused.names.sealed
    assert fused.table._strings == expected.table._strings
    assert (fused.vocabulary, fused.ranks) == (expected.vocabulary, expected.ranks)


SYNTHETIC_LEDGER = """
- id: class-a
  title: The class-a title
  summary: The class-a summary.
  status: by-design
  why: because
- id: class-b
  title: The class-b title
  summary: The class-b summary.
  status: approved
  no_verdict: true
  why: because
- id: class-c
  title: The class-c title
  summary: The class-c summary.
  status: per-unit
  ink_identical: true
  why: because
- id: class-d
  title: The class-d title
  summary: The class-d summary.
  status: by-design
  exemplars: [{config: default, codepoints: "E650:E651"}]
  why: because
"""

# One section a configuration in rank order, `ss04` absent, each section's windows strictly increasing: `E650:E651` in every section as one unit; siblings in `E650:E652` that merge; in `E651:E650`, siblings the merge keeps apart because they match two ledger classes; in `E652:E653`, siblings whose ink differs (`SPLIT`); in `E650:E651:E652`, three siblings that merge into an UNMATCHED survivor; a boundary-led window and a four-letter one.
SYNTHETIC_SECTIONS = {
    "default": [
        ("0020:E650", "cell", "class-a", "space|qsPea", "space|qsPea/full/None/None/"),
        ("E650:E651", "cell", "class-d", "qsPea|qsBay", "qsPea/full/None/None/|qsBay/full/None/None/"),
        ("E650:E652", "cell,junction", "UNMATCHED", "qsPea|qsTea", "qsPea/a|qsTea/a"),
        ("E651:E650", "cell", "class-a", "qsBay|qsPea", "qsBay/c|qsPea/c"),
        ("E652:E653", "junction", "class-a", "qsTea|qsDay", "qsTea/p|qsDay/p"),
        ("E650:E651:E652", "cell", "class-b", "qsPea|qsBay|qsTea", "qsPea/e|qsBay/e|qsTea/e"),
    ],
    "ss03": [
        ("E650:E651", "cell", "class-d", "qsPea|qsBay", "qsPea/full/None/None/|qsBay/full/None/None/"),
        ("E650:E652", "cell", "class-a", "qsPea|qsTea", "qsPea/a|qsTea/a"),
        ("E651:E650", "cell", "class-c", "qsBay|qsPea", "qsBay/d|qsPea/d"),
        ("E652:E653", "junction", "class-a", "qsTea|qsDay", "qsTea/q|qsDay/q"),
        ("E650:E651:E652", "junction", "class-b", "qsPea|qsBay|qsTea", "qsPea/f|qsBay/f|qsTea/f"),
    ],
    "ss05": [
        ("E650:E651", "cell", "class-d", "qsPea|qsBay", "qsPea/full/None/None/|qsBay/full/None/None/"),
        ("E650:E652", "cell", "class-a", "qsPea|qsTea.alt", "qsPea/b|qsTea/b"),
        (
            "E650:E651:E652:E653",
            "cell",
            "class-a",
            "qsPea|qsBay|qsTea|qsDay",
            "qsPea/g|qsBay/g|qsTea/g|qsDay/g",
        ),
    ],
    "ss03+ss05": [
        ("E650:E651", "cell", "class-d", "qsPea|qsBay", "qsPea/full/None/None/|qsBay/full/None/None/"),
        ("E650:E651:E652", "cell", "class-b", "qsPea|qsBay|qsTea", "qsPea/e|qsBay/e|qsTea/e"),
    ],
    "ss10": [
        ("E650:E651", "cell", "class-d", "qsPea|qsBay", "qsPea/full/None/None/|qsBay/full/None/None/"),
        ("E650:E652", "junction", "class-a", "qsPea|qsTea.alt", "qsPea/b|qsTea/b"),
        ("E650:E651:E652", "cell", "UNMATCHED", "qsPea|qsBay|qsTea", "qsPea/h|qsBay/h|qsTea/h"),
    ],
}
SPLIT = frozenset({"E652:E653"})


def _synthetic_audit(
    tmp_path: Path, sections: Mapping[str, list[tuple[str, ...]]] = SYNTHETIC_SECTIONS
) -> tuple[Path, Path]:
    audit = tmp_path / "audit.tsv"
    lines = ["\t".join(AUDIT_HEADER)] + [
        "\t".join((config, *row)) for config, rows in sections.items() for row in rows
    ]
    audit.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(SYNTHETIC_LEDGER, encoding="utf-8")
    return audit, ledger


def _family_keys() -> dict[str, str]:
    names = sorted(set(LETTERS.values())) + ["qsPea_qsBay"]
    return {name: hashlib.sha256(name.encode()).hexdigest() for name in names}


def _signature(split: Collection[str]) -> Callable[[str, str], object]:
    """An ink signature over a window and a config: the window alone, so a window's siblings merge, except for a window in `split`, whose every config differs."""
    return lambda window, config: (window, config) if window in split else window


class _Loaded(NamedTuple):
    table: UnitTable
    snapshot: PremergeSnapshot
    stats: dict
    input_keys: list[str]
    signature_keys: list[int]
    rows: int


def _segmented(audit: Path, directory: Path) -> Path:
    """An audit under a head in `directory` whose materialized form is `audit`'s single file, as run_m1's oracle writes it over three passes (`oracle.AuditWriter`): a base of `audit`'s rows with every fifth row's kinds changed, every seventh row dropped, a row at a window no pass keeps added to each configuration and the rows of the configuration with the fewest left out, then a plain keyed segment of the second pass's upserts and tombstones, which also changes every eleventh row's kinds, and a segment of the third pass's, which changes them back. So the materialized rows come from every part, a window's rows among them, a window's latest row from the later of two segments, and one configuration's rows from the segments alone."""
    header, *rows = audit.read_text(encoding="utf-8").splitlines()
    counts = collections.Counter(row.split("\t", 1)[0] for row in rows)
    sparse = min(counts, key=lambda config: (counts[config], config))
    first: list[str] = []
    for index, row in enumerate(rows):
        fields = row.split("\t")
        if index % 7 == 3 or fields[0] == sparse:
            continue
        if index % 5 == 1:
            fields[2] = fields[2] + ",junction"
        first.append("\t".join(fields))
        following = rows[index + 1].split("\t", 1)[0] if index + 1 < len(rows) else None
        if following != fields[0]:
            first.append("\t".join((fields[0], ":".join(["E650"] * 9), *fields[2:])))
    middle = [
        "\t".join((*fields[:2], fields[2] + ",middle", *fields[3:])) if index % 11 == 5 else row
        for index, row in enumerate(rows)
        for fields in [row.split("\t")]
    ]
    for lines in (first, middle, rows):
        with oracle.AuditWriter(directory) as writer:
            writer.feed(("\n".join(lines) + "\n").encode("utf-8"))
    head = row_delta.read_head(directory, oracle.AUDIT_NAME)
    assert head is not None and len(head.segments) == 2
    assert b"".join(oracle.materialized_audit(directory)) == audit.read_bytes()
    return directory / oracle.AUDIT_NAME


def _misses(audit: Path) -> list[tuple[int, str, str]]:
    """What the build's key-only read finds over `audit` against an empty ink-signature store, so every sibling row misses: each miss's key, configuration and window, in window order, read through the descriptors of the audit's parts (`review_build._key_only_read`)."""
    parts = AuditParts(audit)
    try:
        layout = audit_layout(parts.descriptors, audit)
        assert layout.sections is not None, layout.why
        found = review_build._key_only_read(
            parts.descriptors, layout.sections, dict(LETTERS), _family_keys(), unit_cache.SignatureArrays()
        )
    finally:
        parts.close()
    assert not found.broken
    return [
        (key, layout.sections[section].config, window)
        for key, section, window in zip(found.keys, found.sections, found.windows)
    ]


def _in_memory(audit: Path, ledger_path: Path, split: Collection[str]) -> _Loaded:
    """The in-memory source as the build runs it: the load, the signature rows' keys, the pre-merge snapshot, the merge, the compaction and each unit's input key. An audit under a head is read through the descriptors of its parts (`AuditParts`), as the build reads it."""
    parts = AuditParts(audit)
    try:
        workload = load_workload(audit, ledger_path, dict(LETTERS), descriptors=parts.descriptors)
    finally:
        parts.close()
    table, rows = workload.table, workload.rows
    assert rows is not None
    keyer = unit_cache.UnitKeyer(_family_keys(), dict(LETTERS))
    keys = sorted(int(key, 16) for _view, key in keyer.signature_keys(signature_rows(table, rows)))
    snapshot = capture_premerge(
        table, frozenset(entry.id for entry in workload.ledger if entry.ink_identical)
    )
    exempt = {entry.id for entry in workload.ledger if entry.no_verdict}
    stats = merge_ink_duplicate_units(table, rows, _signature(split), exempt)
    snapshot.rebase(table.compact())
    input_keys = [keyer.key(table, rows, ordinal) for ordinal in range(table.n)]
    return _Loaded(table, snapshot, stats, input_keys, keys, workload.row_count)


def _streamed(audit: Path, ledger_path: Path, split: Collection[str]) -> _Loaded:
    """The stream over the same audit, through a held descriptor, its digests from the same signature."""
    ledger = load_ledger(ledger_path)
    signature = _signature(split)
    requested: list[int] = []

    def digest(key: int, window: str, config: str) -> object:
        requested.append(key)
        return signature(window, config)

    parts = AuditParts(audit)
    try:
        layout = audit_layout(parts.descriptors, audit)
        assert layout.sections is not None, layout.why
        streamed = stream_workload(
            parts.descriptors,
            audit,
            ledger_path,
            layout.sections,
            ledger,
            dict(LETTERS),
            _family_keys(),
            digest,
        )
    finally:
        parts.close()
    table = streamed.workload.table
    snapshot = PremergeSnapshot.from_columns(
        table, streamed.premerge, frozenset(entry.id for entry in ledger if entry.ink_identical)
    )
    snapshot.rebase(streamed.compaction)
    keys = [streamed.input_keys[ordinal * 32 : ordinal * 32 + 32].hex() for ordinal in range(table.n)]
    return _Loaded(
        table, snapshot, streamed.merge_stats, keys, sorted(requested), streamed.workload.row_count
    )


def _accessors(table: UnitTable) -> list[tuple]:
    """Every accessor of every row in load order but `rows_start`, an address into the in-memory source's row columns, which the stream does not build; the class map with its order."""
    return [
        (
            table.class_id(ordinal),
            table.group(ordinal),
            table.unmatched_group(ordinal),
            table.duplicate_group(ordinal),
            table.cluster(ordinal),
            table.configs(ordinal),
            table.kinds(ordinal),
            table.render_groups(ordinal),
            table.baseline(ordinal),
            table.new(ordinal),
            list(table.config_classes(ordinal).items()),
            table.exemplar(ordinal),
            table.no_verdict(ordinal),
            table.live(ordinal),
            table.survivor(ordinal),
            table.codepoints(ordinal),
            table.config_rank(ordinal),
            table.row_count(ordinal),
            table.order(ordinal),
            table.batch(ordinal),
        )
        for ordinal in range(table.n)
    ]


def _snapshot(snapshot: PremergeSnapshot) -> tuple:
    assert snapshot.survivor is not None and snapshot.removed is not None
    return (
        list(snapshot.grains()),
        list(snapshot.class_rows()),
        list(snapshot.survivor),
        bytes(snapshot.removed),
    )


@pytest.mark.parametrize("source", ["mini", "synthetic", "mini under a head", "synthetic under a head"])
def test_the_stream_gives_the_in_memory_sources_table_snapshot_merge_and_keys(source, mini_bundle, tmp_path):
    """The build's two window sources agree, and so do its key-only reads. Over the mini audit, which run_m1 wrote in sorted sections, and over a synthetic audit in sorted sections with siblings that merge, siblings the merge keeps apart because they match two ledger classes, siblings whose ink differs, an absent section and a window in every section, the stream (`stream_workload`) and the in-memory source (`load_workload`, then the merge and the compaction, as the build's in-memory source runs them) give the same table through every accessor in load order, the same pre-merge snapshot with the same survivors, the same merge stats, the same input keys, and request the same ink-signature keys. The stream's window index is the one the table would sort for itself. Each audit is also read under a head, as a base and two segments of upserts, tombstones and inserted windows whose materialized form is the single file (`_segmented`): the stream and the in-memory source over its parts give what the in-memory source gives over the single file."""
    if source.startswith("mini"):
        audit, ledger = MINI_AUDIT, mini_bundle.ledger
        split = {
            window
            for window in parse_audit_windows(MINI_AUDIT)
            if int(hashlib.sha256(window.encode()).hexdigest(), 16) % 3 == 0
        }
    else:
        audit, ledger = _synthetic_audit(tmp_path)
        split = SPLIT
    expected = _in_memory(audit, ledger, split)
    if source.endswith("under a head"):
        misses = _misses(audit)
        audit = _segmented(audit, tmp_path / "segmented")
        in_memory = _in_memory(audit, ledger, split)
        assert _accessors(in_memory.table) == _accessors(expected.table)
        assert in_memory.input_keys == expected.input_keys and in_memory.stats == expected.stats
        assert _misses(audit) == misses and misses
    streamed = _streamed(audit, ledger, split)
    assert expected.stats["units_merged"] > 0 and streamed.stats == expected.stats
    if source == "synthetic":
        assert expected.stats == {"windows_merged": 2, "units_merged": 3, "kept_split_matched_classes": 1}
    assert _accessors(streamed.table) == _accessors(expected.table)
    assert _snapshot(streamed.snapshot) == _snapshot(expected.snapshot)
    assert streamed.input_keys == expected.input_keys
    assert streamed.signature_keys == expected.signature_keys and expected.signature_keys
    assert streamed.rows == expected.rows
    built = WindowIndex.build(
        (ordinal, streamed.table.codepoints(ordinal)) for ordinal in range(streamed.table.n)
    )
    index = streamed.table.window_index()
    assert (list(index.ordinals), list(index.keys)) == (list(built.ordinals), list(built.keys))


def parse_audit_windows(path: Path) -> set[str]:
    return {line.split("\t")[1] for line in path.read_text(encoding="utf-8").splitlines()[1:] if line}


@pytest.mark.parametrize(
    "shape",
    [
        "fixture",
        "repeated window",
        "sections out of rank order",
        "an order break partway",
        "a carriage return",
    ],
)
def test_an_audit_not_in_sorted_sections_takes_the_in_memory_source(shape, mini_bundle, tmp_path):
    """The stream reads only an audit in sorted sections. The fixture audit's one-row sections and a section out of rank order show before any window is read (`audit_layout`); a window repeated in its section, a row moved out of its section's order, and a carriage return show while the stream reads (`audit_stream.OrderBreak`). None of these is refused: the in-memory source, which the build then reads it through, takes rows in any order."""
    if shape == "fixture":
        audit = REPO_ROOT / "rebuild" / "review" / "fixtures" / "fixture-audit.tsv"
        ledger = REPO_ROOT / "rebuild" / "review" / "fixtures" / "fixture-ledger.yaml"
    else:
        sections = {config: list(rows) for config, rows in SYNTHETIC_SECTIONS.items()}
        if shape == "repeated window":
            sections["ss03"].insert(2, sections["ss03"][1])
        elif shape == "sections out of rank order":
            sections = {"ss03": sections["ss03"], "default": sections["default"]}
        elif shape == "an order break partway":
            sections["default"][3], sections["default"][4] = sections["default"][4], sections["default"][3]
        else:
            sections["ss10"][-1] = (*sections["ss10"][-1][:-1], sections["ss10"][-1][-1] + "\r")
        audit, ledger = _synthetic_audit(tmp_path, sections)
    descriptor = os.open(audit, os.O_RDONLY)
    try:
        layout = audit_layout((descriptor,), audit)
        if shape in ("fixture", "sections out of rank order"):
            assert layout.sections is None and layout.why
        else:
            assert layout.sections is not None
            with pytest.raises(audit_stream.OrderBreak):
                stream_workload(
                    (descriptor,),
                    audit,
                    ledger,
                    layout.sections,
                    load_ledger(ledger),
                    dict(LETTERS),
                    _family_keys(),
                    None,
                )
        assert load_workload(audit, ledger, dict(LETTERS), descriptors=(descriptor,)).table.n > 0
    finally:
        os.close(descriptor)


def test_fixture_units_dedupe_and_carry_configs(mini_bundle, tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    units, _rows = build_units(load_audit(path), load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert len(units) == 2
    by_codepoints = {unit.codepoints: unit for unit in units}
    assert by_codepoints["E650:E665"].configs == ("default", "ss02")
    assert by_codepoints["E652:E670"].kinds == ("cell", "junction")


def test_conflicting_class_resolves_to_unmatched_with_config_classes(mini_bundle):
    """A triple whose audit rows carry different classes per config is not a build error. When one config leaves it UNMATCHED (the ss03-adds-joins-around-tea windows, blessed under ss03 but new under default), the unit's class is the UNMATCHED sentinel, so the new default behavior is what gets reviewed, and `config_classes` records every config's class. Two different matched classes for one triple is a classification bug and raises."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "ss03-adds-joins-around-tea", ("a",), ("b",)),
    ]
    (unit,), _rows = build_units(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert unit.class_id == "UNMATCHED"
    assert unit.config_classes == {"default": "UNMATCHED", "ss03": "ss03-adds-joins-around-tea"}

    conflicting = [
        AuditRow("default", "E650:E665", ("cell",), "class-a", ("a",), ("b",)),
        AuditRow("ss02", "E650:E665", ("cell",), "class-b", ("a",), ("b",)),
    ]
    with pytest.raises(ValueError, match="multiple matched ledger classes"):
        build_units(conflicting, load_ledger(mini_bundle.ledger), dict(LETTERS))


def test_render_groups_split_by_rendered_outcome_identity():
    rows = (
        AuditRow("default", "E650:E665", ("cell",), "x", ("qsPea",), ("qsPea/full/None/None/",)),
        AuditRow("ss02", "E650:E665", ("cell",), "x", ("qsPea",), ("qsPea/half/None/None/",)),
        AuditRow("ss03", "E650:E665", ("cell",), "x", ("qsPea",), ("qsPea/full/None/None/",)),
    )
    identities = [(row.baseline, row.new, row.config) for row in rows]
    assert render_groups_for_rows(identities) == (("default", "ss03"), ("ss02",))
    assert render_groups_for_rows([(1, 7, "default"), (1, 8, "ss02"), (1, 7, "ss03")]) == (
        ("default", "ss03"),
        ("ss02",),
    )


@pytest.fixture
def mini(mini_bundle):
    """The frozen mini-M1 audit under rebuild/review/fixtures/mini/, loaded against the bundle's pinned ledger. It holds real windows over four letters, with enough classes to order and enough per-config splits to dedupe, and reads nothing from rebuild/out/. `fixtures/mini/regenerate.py` regenerates it."""
    return load_workload(MINI_AUDIT, mini_bundle.ledger, dict(LETTERS))


def test_the_table_pools_the_per_unit_tuples_and_interns_the_group(mini):
    """A unit's config set, kinds, render groups, class map and group come from small vocabularies, so the table pools or interns them. Two rows with the same value return the same object on every read, and a materialized unit carries those instances."""
    table = mini.table
    by_value: dict[str, dict] = {"configs": {}, "kinds": {}, "render_groups": {}, "config_classes": {}}
    for ordinal in range(table.n):
        assert table.group(ordinal) is sys.intern(table.group(ordinal))
        unit = table.unit(ordinal)
        for name, seen in by_value.items():
            value = getattr(table, name)(ordinal)
            assert seen.setdefault(id(value), value) is value, (name, value)
            assert getattr(unit, name) is value
            assert getattr(table, name)(ordinal) is value
    assert all(len(seen) < table.n for seen in by_value.values())
    for pool in (table.tuples, table.groups, table.mappings):
        assert 0 < len(pool) < table.n
    distinct = {tuple(table.config_classes(ordinal).items()) for ordinal in range(table.n)}
    assert len(distinct) == len(table.mappings)


def test_two_units_stating_the_same_class_map_share_one_pooled_instance(mini_bundle):
    """The class maps pool to one instance per distinct mapping, keyed on the mapping's insertion order. Two units whose maps differ only in key order get two entries, and each materializes in its own order, which is the order the shipped fragment carries. The pooled instance is typed read-only, and a whole-build test in `rebuild/test_unit_cache.py` checks that no build, cold or cached, writes through it."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "ss03-adds-joins-around-tea", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E652", ("cell",), "ss03-adds-joins-around-tea", ("a",), ("b",)),
        AuditRow("ss03", "E650:E650", ("cell",), "ss03-adds-joins-around-tea", ("a",), ("b",)),
        AuditRow("default", "E650:E650", ("cell",), "UNMATCHED", ("a",), ("b",)),
    ]
    table, _rows = load_table(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    by_window = {table.codepoints_text(ordinal): ordinal for ordinal in range(table.n)}
    first = table.config_classes(by_window["E650:E665"])
    assert table.config_classes(by_window["E650:E652"]) is first
    assert list(first) == ["default", "ss03"]
    reordered = table.config_classes(by_window["E650:E650"])
    assert reordered is not first and dict(reordered) == dict(first)
    assert list(reordered) == ["ss03", "default"]
    assert len(table.mappings) == 2
    assert list(table.unit(by_window["E650:E650"]).config_classes) == ["ss03", "default"]
    pool = MappingPool()
    shared = pool.pooled({"x": "y"})
    assert pool.pooled({"x": "y"}) is shared and pool.pooled({"x": "y", "z": "w"}) is not shared
    assert pool.elements == 2 + 4 and len(pool) == 2
    with pytest.raises(TypeError):
        pool.id({"x": 1})  # pyright: ignore[reportArgumentType]


def test_release_rows_leaves_the_count_behind(tmp_path, mini_bundle):
    """A unit's row count is taken from its run and survives `release_rows`. Releasing drops the workload's row columns and nothing else, so every count and config set is unchanged."""
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    workload = load_workload(path, mini_bundle.ledger, dict(LETTERS))
    units = workload.units()
    counts = {unit.codepoints: unit.row_count for unit in units}
    assert counts == {"E650:E665": 2, "E652:E670": 1}
    assert workload.rows is not None
    assert len(workload.rows) == workload.row_count == 3
    assert {unit.codepoints: workload.rows.configs(unit.rows_start, unit.row_count) for unit in units} == {
        "E650:E665": ("default", "ss02"),
        "E652:E670": ("default",),
    }
    release_rows(workload)
    assert workload.rows is None
    units = workload.units()
    assert {unit.codepoints: unit.row_count for unit in units} == counts
    assert {unit.codepoints: unit.configs for unit in units} == {
        "E650:E665": ("default", "ss02"),
        "E652:E670": ("default",),
    }


def test_every_unit_has_exactly_one_render_group(mini):
    """A unit's rows share (codepoints, baseline, new), so the rendered outcome cannot differ between configs within a unit. The per-config-split UNMATCHED units (blessed under ss03, new under default) differ only in their class label. If this fails, the extra render groups must be shown stacked, not collapsed into one."""
    for unit in mini.units():
        assert unit.render_groups == (unit.configs,)


def test_config_classes_follow_the_file_order_and_the_run_follows_the_config_order(mini_bundle):
    """A unit keeps two orders. Its run in the columns, which `configs` reads and the content key hashes, is in config order, with the file's order within a config. `config_classes` keeps the order the file states the configs in, because that order is in the shipped fragment's bytes. A config outside `ACCEPTANCE_CONFIGS` sorts after every listed one in the run and keeps its file position in the map."""
    rows = [
        AuditRow("ss03", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss02", "E650:E665", ("cell",), "x-class", ("a",), ("b",)),
        AuditRow("default", "E650:E665", ("junction",), "UNMATCHED", ("a",), ("b",)),
    ]
    (unit,), columns = build_units(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert unit.configs == ("default", "ss03", "ss02")
    assert columns.configs(unit.rows_start, unit.row_count) == unit.configs
    assert list(unit.config_classes) == ["ss03", "ss02", "default"]
    assert unit.kinds == ("cell", "junction")
    assert unit.render_groups == (unit.configs,)
    assert [
        columns.line(index, unit.codepoints) for index in range(unit.rows_start, unit.rows_start + 3)
    ] == [
        "default\tE650:E665\tjunction\tUNMATCHED\ta\tb",
        "ss03\tE650:E665\tcell\tUNMATCHED\ta\tb",
        "ss02\tE650:E665\tcell\tx-class\ta\tb",
    ]


def test_a_row_line_is_the_audit_line_it_was_read_from(tmp_path, mini_bundle):
    """For every row of every unit, `line` reproduces the audit file's line minus its newline, byte for byte. This is what the content key hashes."""
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    workload = load_workload(path, mini_bundle.ledger, dict(LETTERS))
    assert workload.rows is not None
    lines = {
        workload.rows.line(index, unit.codepoints)
        for unit in workload.units()
        for index in range(unit.rows_start, unit.rows_start + unit.row_count)
    }
    assert lines == set(FIXTURE_AUDIT.splitlines()[1:])
    sizes = row_column_sizes(workload.rows)
    assert sizes.count == 3 and sizes.packed is not None
    assert sizes.est_bytes == sizes.packed.est_bytes and sizes.packed.string_bytes > 0
    assert sizes.packed.strings == len(workload.table.strings)


def test_the_row_columns_refuse_a_config_vocabulary_wider_than_a_byte():
    columns = RowColumns()
    for index in range(256):
        columns.config_id(f"ss{index}")
    with pytest.raises(ValueError, match="256"):
        columns.config_id("one-more")


def test_build_units_seals_the_tuple_pool_once_its_columns_are_written():
    """The pool's tuple-to-id dict is needed only while the rows are read, and apart from the tuples themselves it is the largest thing the pool holds, so `build_units` seals the pool when the rows end. The ids still name their tuples, which the units hold as their own name tuples, and a later `id` call raises."""
    names = TuplePool()
    rows = [AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a", "b"), ("c",))]
    (unit,), columns = build_units(rows, [], dict(LETTERS), names)
    assert columns.names is names and names.sealed
    assert names[columns.baseline[unit.rows_start]] is unit.baseline == ("a", "b")
    assert names[columns.new[unit.rows_start]] is unit.new == ("c",)
    assert columns.line(unit.rows_start, unit.codepoints) == "default\tE650:E665\tcell\tUNMATCHED\ta|b\tc"
    with pytest.raises(ValueError, match="sealed"):
        names.id(("a", "b"))


def test_the_row_sort_key_ranks_a_config_outside_a_grown_acceptance_tuple(monkeypatch):
    """A config outside `ACCEPTANCE_CONFIGS` ranks at the tuple's length (`audit._config_index`). With the tuple grown to eight entries, every row still stays in its own unit's run, in config order."""
    from rebuild.review import audit

    monkeypatch.setattr(audit, "ACCEPTANCE_CONFIGS", (*ACCEPTANCE_CONFIGS, "ss06", "ss07"))
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("default", "E652:E670", ("cell",), "UNMATCHED", ("c",), ("d",)),
        AuditRow("other", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
    ]
    units, columns = build_units(rows, [], dict(LETTERS))
    runs = {
        unit.codepoints: [
            columns.line(index, unit.codepoints)
            for index in range(unit.rows_start, unit.rows_start + unit.row_count)
        ]
        for unit in units
    }
    assert runs == {
        "E650:E665": ["default\tE650:E665\tcell\tUNMATCHED\ta\tb", "other\tE650:E665\tcell\tUNMATCHED\ta\tb"],
        "E652:E670": ["default\tE652:E670\tcell\tUNMATCHED\tc\td"],
    }
    assert {unit.codepoints: unit.configs for unit in units} == {
        "E650:E665": ("default", "other"),
        "E652:E670": ("default",),
    }


def test_the_dedupe_loses_no_rows(mini):
    """Every audit row ends up under exactly one unit, and the units' runs cover the columns without gaps or overlap. The review facts report the counts, so only the totals are asserted, plus that both sides are nonempty, since an empty audit would pass the sum trivially. On the live corpus, `_CorpusCheck.finish` checks the same row total on every build."""
    units = mini.units()
    assert mini.row_count > 0
    assert len(units) == mini.table.n > 0
    assert sum(unit.row_count for unit in units) == mini.row_count
    assert mini.rows is not None and len(mini.rows) == mini.rows.live == mini.row_count
    runs = sorted((unit.rows_start, unit.row_count) for unit in units)
    assert all(
        start == previous_start + previous_count
        for (previous_start, previous_count), (start, _count) in zip(runs, runs[1:])
    )
    assert runs[0][0] == 0 and sum(runs[-1]) == mini.row_count


def test_triage_order_follows_ledger_then_group_then_codepoints(mini):
    # The UNMATCHED units carry the sentinel class at workload level, because their change group is assigned later in the build. They rank after every ledger class.
    class_order = {entry.id: index for index, entry in enumerate(mini.ledger)}
    units = mini.units()
    indices = [class_order.get(unit.class_id, len(mini.ledger)) for unit in units]
    assert indices == sorted(indices)
    by_class: dict[str, list] = {}
    for unit in units:
        by_class.setdefault(unit.class_id, []).append(unit)
    assert len(by_class) > 1, "the mini workload must span classes for the ordering to say anything"
    for units in by_class.values():
        groups = [unit.group for unit in units]
        first_seen: dict[str, int] = {}
        for index, group in enumerate(groups):
            first_seen.setdefault(group, index)
        for left, right in zip(groups, groups[1:]):
            if left != right:
                assert first_seen[left] < first_seen[right], "groups must form contiguous ordered runs"
        for left, right in zip(units, units[1:]):
            if left.group == right.group:
                assert (len(left.codepoint_values), left.codepoint_values) <= (
                    len(right.codepoint_values),
                    right.codepoint_values,
                )


def test_unit_ids_batches_and_positions_are_unassigned_until_the_build_knows_them(mini):
    """An id comes from the content key, which is stamped at enrichment, and a batch is assigned only once every unit has its ink flags and its change group, so the loaded workload has neither."""
    for unit in mini.units():
        assert unit.unit_id == ""
        assert unit.order is None
        assert unit.batch is None
        assert unit.ink_identical is False
        assert unit.picture_identical is False


def test_assign_batches_indexes_the_human_units_and_nulls_machine_units(mini):
    """`assign_batches` depends only on the table, the store's flags and an order, so the mini workload tests it as well as the live one would. Every human unit gets its position among the human units in the order and the batch that position falls in. Every other unit gets neither, and a materialized unit carries the same values."""
    table = mini.table
    store = UnitStore(table.n, strings=table.strings)
    for ordinal in range(table.n):
        store._flags[ordinal] = (
            (unit_store.INK_IDENTICAL if ordinal % 3 == 0 else 0)
            | (unit_store.PICTURE_IDENTICAL if ordinal % 3 == 2 and ordinal % 7 == 0 else 0)
            | (unit_store.JUNIOR_EQUIVALENT if ordinal % 3 == 1 and ordinal % 5 == 0 else 0)
        )
    order = list(reversed(range(table.n)))
    total = assign_batches(table, store, order, batch_size=300)
    human = [
        ordinal for ordinal in order if not store.machine_approved(ordinal) and not table.no_verdict(ordinal)
    ]
    assert human
    assert [table.order(ordinal) for ordinal in human] == list(range(len(human)))
    assert [table.batch(ordinal) for ordinal in human] == [
        batch_of(index, 300) for index in range(len(human))
    ]
    assert all(
        table.batch(ordinal) is None and table.order(ordinal) is None
        for ordinal in range(table.n)
        if store.machine_approved(ordinal) or table.no_verdict(ordinal)
    )
    assert total == (len(human) + 299) // 300
    unit = table.unit(human[3], store)
    assert (unit.order, unit.batch) == (3, 0) and not unit.machine_approved


def test_sort_for_triage_orders_by_class_group_window_then_id():
    """The manifest's index order, with the id as the last term, so sibling units of one window (same class, group and codepoints) get the same order on every corpus instead of the audit's order. A shorter window sorts ahead of a longer one whatever its codepoints: the boundary-led `0020:E650:E652` window, whose group is the same two families, sorts after every two-cell window of that group and before `E650:E652:E650`, which is also three cells and compares higher cell by cell. The sort agrees with `triage_key` over the id strings."""
    rows = [
        AuditRow("default", "E650:E652", ("cell",), "class-b", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "class-b", ("a",), ("c",)),
        AuditRow("default", "E650:E650", ("cell",), "class-a", ("a",), ("b",)),
        AuditRow("default", "E650:E652:E650", ("cell",), "class-b", ("a",), ("b",)),
        AuditRow("default", "0020:E650:E652", ("cell",), "class-b", ("a",), ("b",)),
    ]
    ledger = [
        LedgerClass("class-b", "by-design", "", False, False, frozenset()),
        LedgerClass("class-a", "by-design", "", False, False, frozenset()),
    ]
    table, _rows = load_table(rows, ledger, dict(LETTERS))
    store = UnitStore(table.n, strings=table.strings)
    for ordinal in range(table.n):
        word = (0xF0 if table.new(ordinal) == ("b",) else 0x10) << 56 | table.codepoints(ordinal)[-1]
        store._content_keys[ordinal * unit_store.KEY_BYTES : ordinal * unit_store.KEY_BYTES + 8] = (
            word.to_bytes(8, "big")
        )
    order = sort_for_triage(table, store, {"class-a": 0, "class-b": 1}, dict(LETTERS))
    assert sorted(order) == list(range(table.n))
    assert [(table.class_id(o), table.codepoints_text(o), table.new(o)) for o in order] == [
        ("class-a", "E650:E650", ("b",)),
        ("class-b", "E650:E652", ("c",)),
        ("class-b", "E650:E652", ("b",)),
        ("class-b", "0020:E650:E652", ("b",)),
        ("class-b", "E650:E652:E650", ("b",)),
    ]
    family_rank = {name: value for value, name in dict(LETTERS).items()}
    by_id = sorted(
        range(table.n),
        key=lambda o: triage_key(
            {"class-a": 0, "class-b": 1}[table.class_id(o)],
            table.group(o),
            table.codepoints(o),
            store.unit_id(o),
            family_rank,
        ),
    )
    assert list(order) == by_id


def _tied_table() -> tuple[UnitTable, UnitStore]:
    """A table whose units tie on class, group and window by the dozen, across two classes, three groups and windows of two to four letters, in an order that is neither load nor window order, with id words that tie too (two units of one window share a word), over the store `sort_for_triage` reads them from."""
    rows = []
    windows = (
        "E650:E652",
        "E652:E650",
        "E650:E651:E652",
        "0020:E650:E652",
        "E653:E650",
        "E650:E652:E650:E651",
    )
    for index in range(60):
        window = windows[index * 7 % len(windows)]
        rows.append(
            AuditRow("default", window, ("cell",), ("class-a", "class-b")[index % 2], ("a",), (f"n{index}",))
        )
    ledger = [
        LedgerClass("class-b", "by-design", "", False, False, frozenset()),
        LedgerClass("class-a", "by-design", "", False, False, frozenset()),
    ]
    table, _rows = load_table(rows, ledger, dict(LETTERS))
    store = UnitStore(table.n, strings=table.strings)
    for ordinal in range(table.n):
        word = ((ordinal * 0x9E3779B97F4A7C15) & (2**64 - 1)) if ordinal % 5 else 7 << 56
        store._content_keys[ordinal * unit_store.KEY_BYTES : ordinal * unit_store.KEY_BYTES + 8] = (
            word.to_bytes(8, "big")
        )
    return table, store


def test_the_counting_sort_is_the_comparison_sorts_permutation_ties_included(mini):
    """The triage sort and the stream's load order are a counting sort over (class, group) buckets walked in window order (`_counting_permutation`), not a comparison sort over the corpus. Its permutation is `_triage_permutation`'s exactly: with id words, ties of one window sort by word and then by ordinal; without, by ordinal. Over the mini table, whose windows hold siblings, and over a table whose units tie by the dozen."""
    table, store = _tied_table()
    family_rank = {name: value for value, name in dict(LETTERS).items()}
    for tables, class_order in (
        ((table, store), {"class-a": 0, "class-b": 1}),
        ((mini.table, _mini_store(mini.table)), {entry.id: index for index, entry in enumerate(mini.ledger)}),
    ):
        subject, words = tables
        index = subject.window_index()
        assert list(sort_for_triage(subject, words, class_order, dict(LETTERS))) == list(
            _triage_permutation(subject, class_order, family_rank, words.id_word)
        )
        assert list(
            _counting_permutation(
                subject._class,
                subject._group,
                subject.strings,
                class_order,
                family_rank,
                index.ordinals,
                subject.n,
            )
        ) == list(_triage_permutation(subject, class_order, family_rank))


def _mini_store(table: UnitTable) -> UnitStore:
    store = UnitStore(table.n, strings=table.strings)
    for ordinal in range(table.n):
        word = int(hashlib.sha256(table.codepoints_text(ordinal).encode()).hexdigest()[:16], 16) ^ ordinal % 2
        store._content_keys[ordinal * unit_store.KEY_BYTES : ordinal * unit_store.KEY_BYTES + 8] = (
            word.to_bytes(8, "big")
        )
    return store


def test_the_window_index_answers_as_a_window_keyed_dict_does(mini):
    """The primary-unit search bisects the window index (`columns.WindowIndex`), the units in window order beside their windows packed into words. For every window of the mini table and of the tied table, and every sub-window of each, the index gives the ordinals a dict of every window's ordinals gives, in the same ascending order, and nothing for a window no unit has; it counts the windows that dict holds."""
    for table in (mini.table, _tied_table()[0]):
        by_codepoints: dict[tuple[int, ...], list[int]] = {}
        for ordinal in range(table.n):
            by_codepoints.setdefault(table.codepoints(ordinal), []).append(ordinal)
        index = table.window_index()
        assert index.packed and index.windows == len(by_codepoints)
        asked = {
            window[start:end]
            for window in by_codepoints
            for start in range(len(window))
            for end in range(start + 1, len(window) + 1)
        }
        assert {window: list(index.candidates(window)) for window in asked} == {
            window: by_codepoints.get(window, []) for window in asked
        }
        assert list(index.candidates((0x0041, 0x0042))) == []


def test_no_verdict_flag_mirrors_the_ledger_class():
    """The ledger's `no_verdict: true` exempts every unit of a class judged as a whole from individual verdicts. Which classes carry the flag is ledger content, so the test uses a synthetic ledger: a unit carries the flag if and only if its class does."""
    ledger = [
        LedgerClass(
            id="wholesale-adjudicated",
            status="by-design",
            why="",
            ink_identical=False,
            no_verdict=True,
            exemplar_keys=frozenset(),
        ),
        LedgerClass(
            id="ordinary-class",
            status="by-design",
            why="",
            ink_identical=False,
            no_verdict=False,
            exemplar_keys=frozenset(),
        ),
    ]
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "wholesale-adjudicated", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "ordinary-class", ("a",), ("b",)),
        AuditRow("default", "E650:E650", ("cell",), "UNMATCHED", ("a",), ("b",)),
    ]
    units, _rows = build_units(rows, ledger, dict(LETTERS))
    flagged = {entry.id for entry in ledger if entry.no_verdict}
    for unit in units:
        assert unit.no_verdict == (unit.class_id in flagged), unit.unit_id
    assert {unit.class_id: unit.no_verdict for unit in units} == {
        "wholesale-adjudicated": True,
        "ordinary-class": False,
        "UNMATCHED": False,
    }


def test_ordering_is_deterministic(mini_bundle, mini):
    again = load_workload(MINI_AUDIT, mini_bundle.ledger, dict(LETTERS))
    assert [unit.unit_id for unit in again.units()] == [unit.unit_id for unit in mini.units()]
    assert [unit.codepoints for unit in again.units()] == [unit.codepoints for unit in mini.units()]


def test_configs_within_a_unit_are_in_acceptance_order(mini):
    order = {token: index for index, token in enumerate(ACCEPTANCE_CONFIGS)}
    for unit in mini.units():
        ranks = [order[config] for config in unit.configs]
        assert ranks == sorted(ranks)


def test_parse_codepoints():
    """Pins the codepoint-string parsing in both directions. `build.signature_text` parses a row's window into the text the comparator shapes, and `UnitTable.codepoints_text` formats the window strings the signature keys and the merge's lookups are keyed by, so the parse must invert the format. `unit_cache.signature_code_paths` leaves audit.py out of the signature store's stamp because this test pins the parsing. The test below pins the `chr` join that finishes the text."""
    assert parse_codepoints("200C:E652:E679") == (0x200C, 0xE652, 0xE679)
    assert format_codepoints((0x200C, 0xE652, 0xE679)) == "200C:E652:E679"


def test_the_signature_text_is_pinned_beside_the_store_format():
    """Pins the text `build.signature_text` returns for a window together with `SIGNATURE_STORE_FORMAT`. A store written for one text keeps supplying its digests to a build that shapes another until the format changes, so an edit that changes a result here must bump the format and update both literals together."""
    assert unit_cache.SIGNATURE_STORE_FORMAT == "ams-review-ink-signatures/2"
    assert signature_text("200C:E652:E679") == "\u200c\ue652\ue679"
    assert signature_text("0020:E650:00B7") == " \ue650\u00b7"
    assert signature_text("E650") == "\ue650"


def test_ink_duplicate_siblings_merge_into_one_unit(mini_bundle):
    """The name-grain dedupe key splits one visual question into two units when a config only renames a glyph (the old font's ss04 rename of word-initial ·It). When the ink signature reports every render of the window identical, the siblings merge. The earliest-config unit survives with the union of configs, kinds and per-config classes, a merged run of both siblings' rows in the columns (each row keeps its own rendered names), and a single render group, and compacting the table removes the absorbed unit. The two runs the merged run replaces are orphaned: outside the columns' live count but still in their bytes."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("qsPea", "qsMay.en-y0"), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "UNMATCHED", ("qsPea", "qsMay.en-y0"), ("b",)),
        AuditRow("ss04", "E650:E665", ("junction",), "UNMATCHED", ("qsPea.ss04", "qsMay.en-y0"), ("b",)),
        AuditRow("default", "E650:E650", ("cell",), "UNMATCHED", ("qsPea", "qsPea"), ("c",)),
    ]
    table, columns = load_table(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert table.n == 3
    before = {ordinal: table.config_classes(ordinal) for ordinal in range(table.n)}
    stats = merge_ink_duplicate_units(table, columns, lambda window, config: window)
    assert stats == {"windows_merged": 1, "units_merged": 1, "kept_split_matched_classes": 0}
    assert table.n == 3 and sum(table.live(ordinal) for ordinal in range(table.n)) == 2
    victim = next(ordinal for ordinal in range(table.n) if not table.live(ordinal))
    assert table.survivor(victim) != victim and table.live(table.survivor(victim))
    compaction = table.compact()
    assert table.n == 2 and list(compaction.removed) == [
        1 if ordinal == victim else 0 for ordinal in range(3)
    ]
    assert sorted(compaction.survivor) == sorted([0, 1] + [compaction.survivor[victim]])
    units = table.units()
    merged = next(unit for unit in units if unit.codepoints == "E650:E665")
    assert merged.configs == ("default", "ss03", "ss04")
    assert merged.row_count == 3
    assert columns.configs(merged.rows_start, merged.row_count) == merged.configs
    assert [
        columns.view(index, merged.codepoints).baseline
        for index in range(merged.rows_start, merged.rows_start + 3)
    ] == [
        ("qsPea", "qsMay.en-y0"),
        ("qsPea", "qsMay.en-y0"),
        ("qsPea.ss04", "qsMay.en-y0"),
    ]
    assert len(columns) == 7 and columns.orphaned == 3 and columns.live == 4
    assert row_column_sizes(columns).count == 4
    assert merged.baseline == ("qsPea", "qsMay.en-y0")
    assert merged.kinds == ("cell", "junction")
    assert merged.render_groups == (merged.configs,)
    assert merged.config_classes == {"default": "UNMATCHED", "ss03": "UNMATCHED", "ss04": "UNMATCHED"}
    assert merged.config_classes is table.mappings.pooled(dict(merged.config_classes))
    assert sorted(tuple(mapping.items()) for mapping in before.values()) == sorted(
        [
            (("default", "UNMATCHED"), ("ss03", "UNMATCHED")),
            (("ss04", "UNMATCHED"),),
            (("default", "UNMATCHED"),),
        ]
    ), "the survivor's map was re-pooled rather than the pooled pre-merge instances rewritten"


def test_ink_duplicate_merge_respects_matched_classes_and_exemptions(mini_bundle):
    """A merge that would put two different matched ledger classes on one unit is skipped, because different glyph names can match different ledger predicates. A matched class merging with an UNMATCHED sibling takes the UNMATCHED class and recomputes the no-verdict flag from the exempt classes."""
    conflicting, columns = load_table(
        [
            AuditRow("default", "E650:E665", ("cell",), "class-a", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "class-b", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    stats = merge_ink_duplicate_units(conflicting, columns, lambda window, config: window)
    assert stats["kept_split_matched_classes"] == 1
    conflicting.compact()
    assert conflicting.n == 2

    mixed, columns = load_table(
        [
            AuditRow("default", "E650:E665", ("cell",), "split-by-space-or-zwnj", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "UNMATCHED", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    for ordinal in range(mixed.n):
        mixed.set_no_verdict(ordinal, mixed.class_id(ordinal) == "split-by-space-or-zwnj")
    merge_ink_duplicate_units(
        mixed, columns, lambda window, config: window, exempt_classes={"split-by-space-or-zwnj"}
    )
    mixed.compact()
    (merged,) = mixed.units()
    assert merged.class_id == "UNMATCHED"
    assert merged.no_verdict is False
    assert merged.config_classes == {"default": "split-by-space-or-zwnj", "ss04": "UNMATCHED"}


def test_units_whose_configs_render_differently_never_merge(mini_bundle):
    """A unit only merges when every config on both sides yields one ink signature; per-config signatures leave everything standing."""
    table, columns = load_table(
        [
            AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
            AuditRow("ss02", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "UNMATCHED", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    stats = merge_ink_duplicate_units(table, columns, lambda window, config: (window, config))
    assert stats == {"windows_merged": 0, "units_merged": 0, "kept_split_matched_classes": 0}
    assert table.compact().removed == bytearray(2) and table.n == 2


def test_the_name_tuples_are_released_after_phase_one(mini):
    """The table holds each unit's `baseline` and `new` as ids into the name pool it shares with the row columns, until the build calls `release_names` after phase 1. Releasing drops both columns and the pool, `unit_table_sizes` drops the pool's bytes, and a unit materialized afterward has empty name tuples."""
    table = mini.table
    assert table.names is not None and table.names.sealed
    unit = table.unit(0)
    assert unit.baseline and unit.new and unit.baseline is table.baseline(0)
    before = unit_table_sizes(table)
    assert before.packed is not None
    pooled = memory_tally.pool_bytes(table.names)
    columns = 2 * 4 * table.n
    table.release_names()
    after = unit_table_sizes(table)
    assert after.packed is not None
    assert table.names is None and table.baseline(0) == () == table.new(0)
    assert table.unit(0).baseline == () == table.unit(0).new
    assert before.packed.est_bytes - after.packed.est_bytes == pooled + columns
    assert after.count == before.count == table.n


def test_the_table_sizes_are_the_columns_bytes_and_the_pools_estimated_beside_the_string_table(mini):
    """The table's `workload.units` reading is exact. The packed figure is the columns' bytes plus every pool's packed size, the string table is reported beside it, and the two summed are the walked figure. A compaction removes the merged rows' bytes from it."""
    table = mini.table
    reading = unit_table_sizes(table)
    assert reading.packed is not None
    columns = sum(len(column) * column.itemsize for column in table.columns())
    pools = sum(memory_tally.pool_bytes(pool) for pool in table.pools())
    strings = table.strings.chars + len(table.strings) * memory_tally.OFFSET_WIDTH
    assert reading == memory_tally.Measure(
        table.n,
        columns + pools + strings,
        memory_tally.PackedCost(columns + pools, len(table.strings), strings),
    )
    assert reading.packed.est_bytes / table.n < 200
    table.merge_into(1, 0)
    table.compact()
    shrunk = unit_table_sizes(table)
    assert shrunk.count == reading.count - 1 and shrunk.est_bytes < reading.est_bytes


def _entry(identifier: str) -> str:
    return (
        f"- id: {identifier}\n  title: The {identifier} title\n  summary: The {identifier} summary.\n"
        "  status: by-design\n  match: {predicate: boundary_window, configs: all}\n  why: because\n"
    )


def _ledger(tmp_path, *ids: str, text: str = "") -> Path:
    path = tmp_path / "ledger.yaml"
    path.write_text("".join(_entry(identifier) for identifier in ids) + text, encoding="utf-8")
    return path


def test_a_ledger_declaring_one_class_twice_is_refused_at_load(tmp_path):
    """Code downstream indexes the ledger by id, so a repeated id would make one entry silently replace the other. `load_ledger` raises on it."""
    with pytest.raises(ValueError, match="longer-entry-join"):
        load_ledger(
            _ledger(
                tmp_path,
                "identical-unused-join-name",
                "longer-entry-join",
                "longer-entry-join",
            )
        )


@pytest.mark.parametrize("identifier", ["UNMATCHED", change_groups.CHANGE_GROUP_ORDER[0]])
def test_a_ledger_claiming_a_synthesized_class_is_refused_at_load(tmp_path, identifier):
    """The build creates the UNMATCHED catch-all and the change-group classes itself, so a ledger entry with one of those ids would be overridden by a class the ledger does not describe."""
    with pytest.raises(ValueError, match=identifier):
        load_ledger(_ledger(tmp_path, identifier))


def test_a_ledger_entry_carries_its_copy_and_an_optional_note(tmp_path):
    """`load_ledger` copies each entry's title, summary and note, stripped, beside its why, and an entry without a note gets an empty one."""
    noted = _entry("noted").replace(
        "  why: because\n", '  note: "Look at the join into ·May."\n  why: because\n'
    )
    plain, with_note = load_ledger(_ledger(tmp_path, "plain", text=noted))
    assert (plain.title, plain.summary, plain.note, plain.why) == (
        "The plain title",
        "The plain summary.",
        "",
        "because",
    )
    assert (with_note.status, with_note.note) == ("by-design", "Look at the join into ·May.")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("status: by-design", "status: intended", "status 'intended'"),
        ("  status: by-design\n", "", "status None"),
        ("  title: The bad title\n", "", "nonempty string title"),
        ("summary: The bad summary.", "summary: ' '", "nonempty string summary"),
        ("why: because", 'note: ""\n  why: because', "nonempty string note"),
    ],
)
def test_a_ledger_entry_the_review_app_cannot_show_is_refused(tmp_path, old, new, message):
    """Every entry needs a status from `LEDGER_STATUSES`, which decides its section, and a nonempty title and summary, which the sidebar shows; a note, when present, must say something."""
    text = _entry("bad").replace(old, new)
    assert text != _entry("bad")
    with pytest.raises(ValueError, match=message):
        load_ledger(_ledger(tmp_path, "good", text=text))


def test_class_section_puts_exemption_and_machine_approval_ahead_of_the_status():
    """A no-verdict class, or one whose every unit a machine check approves, needs no review whatever its status. Otherwise a ruled status makes a spot-check and `per-unit`, or a table-diff bucket's null status, an open question. A status outside the vocabulary raises rather than landing in a section by default."""
    assert SECTIONS == ("open-questions", "spot-check", "no-review-needed")
    assert set(RULED_STATUSES) < set(LEDGER_STATUSES)
    for status in (*LEDGER_STATUSES, None):
        assert class_section(status, True, 5) == "no-review-needed"
        assert class_section(status, False, 0) == "no-review-needed"
    assert class_section("by-design", False, 1) == "spot-check"
    assert class_section("approved", False, 1) == "spot-check"
    assert class_section("per-unit", False, 1) == "open-questions"
    assert class_section(None, False, 1) == "open-questions"
    with pytest.raises(ValueError, match="reviewed-approved"):
        class_section("reviewed-approved", False, 1)


def test_the_change_groups_a_table_holds_become_per_unit_classes_in_group_order():
    """Only the groups some unit falls in get a class, in the order given, whatever order the units hold them in. Each takes status `per-unit`, since no ruling covers a change group, and its title, summary and why from the group prose."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "UNMATCHED", ("a",), ("c",)),
        AuditRow("default", "E650:E650", ("cell",), "UNMATCHED", ("a",), ("d",)),
    ]
    table, _rows = load_table(rows, [], dict(LETTERS))
    for ordinal, group in zip(range(table.n), ("join-raised", "join-lowered", "join-raised")):
        table.set_unmatched_group(ordinal, group)
    prose = {
        group: change_groups.ChangeGroupProse(f"{group} title", f"{group} summary", f"{group} why")
        for group in change_groups.CHANGE_GROUP_ORDER
    }
    classes = synthesize_unmatched_group_classes(table, change_groups.CHANGE_GROUP_ORDER, prose)
    assert [entry.id for entry in classes] == ["join-lowered", "join-raised"]
    assert classes[1] == LedgerClass(
        id="join-raised",
        status="per-unit",
        why="join-raised why",
        ink_identical=False,
        no_verdict=False,
        exemplar_keys=frozenset(),
        title="join-raised title",
        summary="join-raised summary",
    )


def test_a_workload_whose_rows_name_a_class_the_ledger_lacks_is_refused(tmp_path):
    """An audit written under a ledger whose ids have since been renamed would list its units under classes the ledger no longer describes, so `load_workload` refuses any `matched_entry` that is neither UNMATCHED nor a ledger id."""
    ledger = _ledger(tmp_path, "identical-unused-join-name", "longer-entry-join")
    audit = tmp_path / "audit.tsv"
    audit.write_text(FIXTURE_AUDIT, encoding="utf-8")
    assert load_workload(audit, ledger, dict(LETTERS)).table.n == 2
    audit.write_text(FIXTURE_AUDIT.replace("longer-entry-join", "a-class-renamed-since"))
    with pytest.raises(ValueError, match="a-class-renamed-since"):
        load_workload(audit, ledger, dict(LETTERS))


def test_the_live_ledger_loads_one_class_per_entry():
    """The checked-in ledger passes those checks, and every entry in the file loads as its own class."""
    path = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = load_ledger(path)
    assert [entry["id"] for entry in entries] == [entry.id for entry in classes]


def test_a_held_descriptor_keeps_reading_a_part_a_later_commit_deleted(tmp_path):
    """The build opens every part its head lists as it reads the head and reads them through those descriptors alone (`AuditParts`), so a commit during the build that deletes them, as a fold does, cannot reach what the build reads: the stream still walks the parts it opened, and gives the rows the head it read describes."""
    header = "\t".join(AUDIT_HEADER)
    rows = [f"default\tE65{digit}\tcell\tclass-a\tqsPea\tqsPea/g" for digit in "0123"]
    out = tmp_path / "m1"
    with oracle.AuditWriter(out) as writer:
        writer.feed(("\n".join(rows) + "\n").encode("utf-8"))
    parts = AuditParts(out / oracle.AUDIT_NAME)
    try:
        assert parts.head is not None and len(parts.descriptors) == 1
        for name in [row_delta.head_name(oracle.AUDIT_NAME), parts.head.base.name]:
            (out / name).unlink()
        layout = audit_layout(parts.descriptors, out / oracle.AUDIT_NAME)
        assert layout.sections is not None
        walked = [found for _window, found in audit_stream.lockstep(parts.descriptors, layout.sections)]
    finally:
        parts.close()
    assert [line for found in walked for _index, _fields, line in found] == rows
    assert header == oracle.ORACLE_AUDIT_HEADER
