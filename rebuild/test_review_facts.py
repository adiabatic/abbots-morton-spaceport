"""Tests for the review-facts sidecar (`rebuild/review/facts.py`): projecting a corpus build's post-merge phase-1 products back onto the pre-merge grain the pins are defined over, re-routing the units of `ink_identical` classes whose picture changes, the in-memory ink and built reductions against values worked out by hand over synthetic units, reading and writing the sidecar, and the `--update` path that reads it.

The tests use hand-built audit rows and hand-set machine flags and change groups, with no fonts, no shaping, and no live workload; the only live input is the checked-in divergence ledger, and the re-routing tests use a ledger of their own. A failure here is a bug in the derivation, not a change in the corpus.
"""

import json
from collections.abc import Collection
from pathlib import Path

import pytest

from rebuild.review import facts, unit_store
from rebuild.review.audit import (
    UNMATCHED_CLASS,
    AuditRow,
    LedgerClass,
    UnitTable,
    load_ledger,
    load_table,
    merge_ink_duplicate_units,
    parse_codepoints,
)
from rebuild.review.change_groups import CHANGE_GROUP_ORDER, reroutes
from rebuild.review.facts import (
    FACTS_FORMAT,
    REFERENCE_WINDOW_CODEPOINTS,
    PremergeFacts,
    build_facts,
    built_group_from_memory,
    capture_premerge,
    derive_premerge,
    ink_group_from_flags,
    invariant_delta,
    invariant_diff,
    invariant_group,
    ledger_coverage,
    load_facts,
    write_facts,
)
from rebuild.review.enrich import LETTERS
from rebuild.review.unit_store import UnitStore

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"

MERGE_WINDOW = "E650:E665"
DEFERRED_WINDOW = "E651:E665"
MIXED_WINDOW = "E652:E665"
STANDALONE_UNMATCHED = "E653:E653"
STANDALONE_MATCHED = "E654:E654"


def _window_text(codepoints: str) -> str:
    return "".join(chr(value) for value in parse_codepoints(codepoints))


def _row(config: str, codepoints: str, matched: str, baseline: tuple[str, ...]) -> AuditRow:
    return AuditRow(
        config=config,
        codepoints=codepoints,
        kinds=("cell",),
        matched_entry=matched,
        baseline=baseline,
        new=("after",),
    )


def _index_of(capture: facts.PremergeSnapshot, codepoints: str, config: str) -> int:
    return next(
        index
        for index, grain in enumerate(capture.grains())
        if grain.codepoints == codepoints and config in grain.configs
    )


def _ledger_entry(identifier: str, *, ink_identical: bool = False, no_verdict: bool = False) -> LedgerClass:
    return LedgerClass(
        id=identifier,
        status="per-unit",
        why="",
        ink_identical=ink_identical,
        no_verdict=no_verdict,
        exemplar_keys=frozenset(),
    )


def _merged_table(
    rows: list[AuditRow], ledger: list[LedgerClass] | None = None, identical: Collection[str] | None = None
) -> tuple[facts.PremergeSnapshot, UnitTable, UnitStore]:
    """Load `rows` against `ledger`, the live one by default, and capture the pre-merge snapshot with the ledger's `ink_identical` classes, or with `identical` when given. Then merge with an ink signature that makes every config of a window identical, so every pair of sibling units the merge allows merges, compact the table, and rebase the snapshot onto it. Returns the snapshot, the table, and an empty store sized to the compacted table."""
    ledger = load_ledger(LEDGER_PATH) if ledger is None else ledger
    table, columns = load_table(rows, ledger, dict(LETTERS))
    capture = capture_premerge(
        table, {entry.id for entry in ledger if entry.ink_identical} if identical is None else identical
    )
    exempt = {entry.id for entry in ledger if entry.no_verdict}
    merge_ink_duplicate_units(table, columns, lambda window, config: window, exempt)
    capture.rebase(table.compact())
    return capture, table, UnitStore(table.n, strings=table.strings)


def _merged_fixture():
    """Five windows covering every case the projection handles for UNMATCHED units: a default-reachable UNMATCHED survivor absorbing a relabeled ss04 sibling, two stylistic-set-only UNMATCHED siblings with different set groups, a no-verdict matched unit absorbing an UNMATCHED sibling, and two standalone units that never merge. Returns the pre-merge snapshot, the compacted table, and a store over it, with phase 1's products set by hand on the survivors' rows."""
    rows = [
        _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss03", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss04", MERGE_WINDOW, "UNMATCHED", ("qsPea.ss04", "qsMay")),
        _row("ss03", DEFERRED_WINDOW, "UNMATCHED", ("qsBay", "qsMay")),
        _row("ss04", DEFERRED_WINDOW, "UNMATCHED", ("qsBay.ss04", "qsMay")),
        _row("default", MIXED_WINDOW, "split-by-space-or-zwnj", ("qsTea", "qsMay")),
        _row("ss04", MIXED_WINDOW, "UNMATCHED", ("qsTea.ss04", "qsMay")),
        _row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay")),
        _row("default", STANDALONE_MATCHED, "identical-unused-join-name", ("qsKey", "qsKey")),
    ]
    capture, table, store = _merged_table(rows)
    verdicts = {
        MERGE_WINDOW: (True, "new-join-at-baseline"),
        DEFERRED_WINDOW: (False, "only-with-several-sets"),
        MIXED_WINDOW: (True, "new-join-at-xheight-or-top"),
        STANDALONE_UNMATCHED: (False, "join-dropped"),
        STANDALONE_MATCHED: (True, ""),
    }
    assert table.n == 5
    for ordinal in range(table.n):
        ink_identical, group = verdicts[table.codepoints_text(ordinal)]
        if ink_identical:
            store._flags[ordinal] |= unit_store.INK_IDENTICAL
        table.set_unmatched_group(ordinal, group)
    return capture, table, store


def test_merged_siblings_take_their_survivors_ink_verdict():
    """A merge happens only when every config of every merged sibling renders identical ink, so the survivor's ink verdict applies to the whole window. Each captured sibling reports its survivor's flag, and the units that never merged report their own."""
    capture, table, store = _merged_fixture()
    premerge = derive_premerge(capture, table, store)
    flags = premerge.ink_flags
    assert premerge.units == len(capture) == len(flags) == 8
    assert flags[_index_of(capture, MERGE_WINDOW, "default")] == "1"
    assert flags[_index_of(capture, MERGE_WINDOW, "ss04")] == "1"
    assert flags[_index_of(capture, DEFERRED_WINDOW, "ss03")] == "0"
    assert flags[_index_of(capture, DEFERRED_WINDOW, "ss04")] == "0"
    assert flags[_index_of(capture, MIXED_WINDOW, "default")] == "1"
    assert flags[_index_of(capture, MIXED_WINDOW, "ss04")] == "1"
    assert flags[_index_of(capture, STANDALONE_UNMATCHED, "default")] == "0"
    assert flags[_index_of(capture, STANDALONE_MATCHED, "default")] == "1"


def test_change_groups_read_set_groups_from_the_premerge_config_classes():
    """A pre-merge UNMATCHED unit's change group is its own set group when it has one, and otherwise its survivor's phase-1 group. The set group is decided from the pre-merge configs and config classes: the ss03-only survivor stays only-with-ss03, although the merged unit with its ss04 sibling is only-with-several-sets. A matched unit gets no change group, even when the merge put it in an UNMATCHED survivor."""
    capture, table, store = _merged_fixture()
    premerge = derive_premerge(capture, table, store)
    assert dict(premerge.unmatched_groups) == {
        _index_of(capture, MERGE_WINDOW, "default"): "new-join-at-baseline",
        _index_of(capture, MERGE_WINDOW, "ss04"): "only-with-ss04",
        _index_of(capture, DEFERRED_WINDOW, "ss03"): "only-with-ss03",
        _index_of(capture, DEFERRED_WINDOW, "ss04"): "only-with-ss04",
        _index_of(capture, MIXED_WINDOW, "ss04"): "only-with-ss04",
        _index_of(capture, STANDALONE_UNMATCHED, "default"): "join-dropped",
    }
    assert [index for index, _group in premerge.unmatched_groups] == sorted(
        index for index, _group in premerge.unmatched_groups
    )
    matched = {_index_of(capture, MIXED_WINDOW, "default"), _index_of(capture, STANDALONE_MATCHED, "default")}
    assert matched.isdisjoint(index for index, _group in premerge.unmatched_groups)


def test_derive_premerge_reads_each_merged_rows_survivor_off_the_compaction():
    """A captured row that the merge removed reads through the survivor the compaction recorded for it, which is the earliest-config sibling's post-merge row. `derive_premerge` raises on a snapshot that was never rebased, and `rebase` raises on a compaction of a different size."""
    rows = [
        _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss04", MERGE_WINDOW, "UNMATCHED", ("qsPea.ss04", "qsMay")),
    ]
    table, columns = load_table(rows, load_ledger(LEDGER_PATH), dict(LETTERS))
    capture = capture_premerge(table)
    merge_ink_duplicate_units(table, columns, lambda window, config: window)
    with pytest.raises(ValueError, match="rebased"):
        derive_premerge(capture, table, UnitStore(table.n))
    capture.rebase(table.compact())
    assert capture.survivor is not None and capture.removed is not None
    removed = _index_of(capture, MERGE_WINDOW, "ss04")
    assert list(capture.removed) == [1 if index == removed else 0 for index in range(2)]
    assert list(capture.survivor) == [0, 0] and table.n == 1
    with pytest.raises(ValueError, match="against 2 captured"):
        capture.rebase(table.compact())


def test_derive_premerge_refuses_an_unmatched_unit_with_no_group():
    """Every pre-merge UNMATCHED unit must have a change group. `derive_premerge` raises on one with no set group whose survivor has no phase-1 group instead of recording an empty group."""
    capture, table, store = _merged_table(
        [_row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay"))]
    )
    with pytest.raises(ValueError, match=STANDALONE_UNMATCHED):
        derive_premerge(capture, table, store)


LOOKS_IDENTICAL = "looks-identical"
LOOKS_IDENTICAL_EXEMPT = "looks-identical-exempt"
REROUTE_LEDGER = [
    _ledger_entry(LOOKS_IDENTICAL, ink_identical=True),
    _ledger_entry(LOOKS_IDENTICAL_EXEMPT, ink_identical=True, no_verdict=True),
]

REROUTED = "E660:E665"
REROUTED_UNDER_SS04 = "E661:E665"
PICTURE_IDENTICAL = "E662:E665"
JUNIOR_ALONE = "E663:E665"
JUNIOR_MERGED = "E664:E665"
ABSORBED = "E666:E665"
EXEMPT = "E667:E665"
INK_IDENTICAL = "E668:E665"

REROUTE_ROWS = [
    _row("default", REROUTED, LOOKS_IDENTICAL, ("qsYe", "qsMay")),
    _row("ss04", REROUTED_UNDER_SS04, LOOKS_IDENTICAL, ("qsWay", "qsMay")),
    _row("default", PICTURE_IDENTICAL, LOOKS_IDENTICAL, ("qsHe", "qsMay")),
    _row("ss10", JUNIOR_ALONE, LOOKS_IDENTICAL, ("qsWhy", "qsMay")),
    _row("default", JUNIOR_MERGED, LOOKS_IDENTICAL, ("qsIng", "qsMay")),
    _row("ss10", JUNIOR_MERGED, LOOKS_IDENTICAL, ("qsIng.ss10", "qsMay")),
    _row("default", ABSORBED, LOOKS_IDENTICAL, ("qsNo", "qsMay")),
    _row("ss04", ABSORBED, "UNMATCHED", ("qsNo.ss04", "qsMay")),
    _row("default", EXEMPT, LOOKS_IDENTICAL_EXEMPT, ("qsLow", "qsMay")),
    _row("default", INK_IDENTICAL, LOOKS_IDENTICAL, ("qsRoe", "qsMay")),
]


class _Fonts:
    """Machine readings preset per window, standing in for `InkComparator` and `JuniorOracle`. The Junior check approves only a unit whose configs are ss10 alone, as `JuniorOracle.approves` does."""

    def __init__(self, ink: set[str], picture: set[str], junior: set[str]) -> None:
        self._ink = {_window_text(window) for window in ink}
        self._picture = {_window_text(window) for window in picture}
        self._junior = {_window_text(window) for window in junior}

    def ink_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        return text in self._ink

    def picture_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        return text in self._picture

    def approves(self, configs: tuple[str, ...], text: str) -> bool:
        return tuple(configs) == ("ss10",) and text in self._junior


FONTS = _Fonts(ink={INK_IDENTICAL}, picture={PICTURE_IDENTICAL}, junior={JUNIOR_ALONE, JUNIOR_MERGED})


def _rerouted_build(
    identical: Collection[str] | None = None,
) -> tuple[facts.PremergeSnapshot, UnitTable, UnitStore]:
    """The re-routing windows as `build_m1` hands them to `derive_premerge`: captured with the test ledger's `ink_identical` classes, or with `identical` when given, and merged, then given the machine flags phase 1 reads off `FONTS` for each survivor, an ink delta under every config of a survivor whose ink changed, and the change group the build promotes onto it: `new-join-at-baseline` for an UNMATCHED survivor and `same-joins-narrower` for one `reroutes` moves."""
    capture, table, store = _merged_table(REROUTE_ROWS, REROUTE_LEDGER, identical)
    identical_classes = {entry.id for entry in REROUTE_LEDGER if entry.ink_identical}
    for ordinal in range(table.n):
        text = _window_text(table.codepoints_text(ordinal))
        configs = table.configs(ordinal)
        if FONTS.ink_identical(text, configs):
            store._flags[ordinal] |= unit_store.INK_IDENTICAL
        elif FONTS.picture_identical(text, configs):
            store._flags[ordinal] |= unit_store.PICTURE_IDENTICAL
        else:
            store._load_deltas(ordinal, [(config, "changed") for config in configs])
            if FONTS.approves(configs, text):
                store._flags[ordinal] |= unit_store.JUNIOR_EQUIVALENT
        class_id = table.class_id(ordinal)
        if class_id == UNMATCHED_CLASS:
            group = "new-join-at-baseline"
        elif reroutes(
            class_id, table.no_verdict(ordinal), store.machine_approved(ordinal), identical_classes
        ):
            group = "same-joins-narrower"
        else:
            continue
        table.set_unmatched_group(ordinal, group)
        table.set_class(ordinal, group)
    return capture, table, store


def _keys(capture: facts.PremergeSnapshot) -> list[tuple[str, tuple[str, ...]]]:
    return [(grain.codepoints, grain.configs) for grain in capture.grains()]


def test_identical_class_units_whose_picture_changes_take_a_change_group():
    """A unit of an `ink_identical` class takes a change group when no machine check approves its survivor and the survivor stays in the class: its own set group when it lacks the default config, and its survivor's group when it has it. It keeps its class when its survivor is ink-identical, picture-identical or Junior-equivalent (an ss10-only unit alone, though not one merged into a default-config sibling, whose survivor the Junior check does not approve), when the merge put it in an UNMATCHED survivor, and when its class exempts it. Each unit that takes a change group keeps its captured class, which the pins count it under: a re-routed unit its `ink_identical` class, and an UNMATCHED unit UNMATCHED."""
    capture, table, store = _rerouted_build()
    premerge = derive_premerge(capture, table, store)
    keys = _keys(capture)
    assert {keys[index]: group for index, group in premerge.unmatched_groups} == {
        (REROUTED, ("default",)): "same-joins-narrower",
        (REROUTED_UNDER_SS04, ("ss04",)): "only-with-ss04",
        (JUNIOR_MERGED, ("default",)): "same-joins-narrower",
        (JUNIOR_MERGED, ("ss10",)): "only-with-ss10",
        (ABSORBED, ("ss04",)): "only-with-ss04",
    }
    grains = list(capture.grains())
    assert {keys[index]: grains[index].class_id for index, _group in premerge.unmatched_groups} == {
        (REROUTED, ("default",)): LOOKS_IDENTICAL,
        (REROUTED_UNDER_SS04, ("ss04",)): LOOKS_IDENTICAL,
        (JUNIOR_MERGED, ("default",)): LOOKS_IDENTICAL,
        (JUNIOR_MERGED, ("ss10",)): LOOKS_IDENTICAL,
        (ABSORBED, ("ss04",)): UNMATCHED_CLASS,
    }
    assert premerge.ink_flags[keys.index((INK_IDENTICAL, ("default",)))] == "1"


def test_a_set_group_counts_an_identical_class_config_only_where_its_ink_changed():
    """A pre-merge unit's set group is decided over the configs no ruling explains, as phase 1 decides a unit's: an UNMATCHED config, or a config of an `ink_identical` class whose ink changed. A re-routed unit that changed only under ss03+ss05 is only-with-ss03, not only-with-ss05, and an UNMATCHED unit whose identical-class default config changed passes the set gate to its survivor's group."""
    rows = [
        _row("ss05", REROUTED, LOOKS_IDENTICAL, ("qsYe", "qsMay")),
        _row("ss03+ss05", REROUTED, LOOKS_IDENTICAL, ("qsYe", "qsMay")),
        _row("default", ABSORBED, LOOKS_IDENTICAL, ("qsNo", "qsMay")),
        _row("ss04", ABSORBED, "UNMATCHED", ("qsNo", "qsMay")),
    ]
    capture, table, store = _merged_table(rows, REROUTE_LEDGER)
    assert table.n == 2
    for ordinal in range(table.n):
        if table.codepoints_text(ordinal) == REROUTED:
            store._load_deltas(ordinal, [("ss03+ss05", "changed")])
            table.set_unmatched_group(ordinal, "only-with-ss03")
        else:
            store._load_deltas(ordinal, [("default", "changed"), ("ss04", "changed")])
            table.set_unmatched_group(ordinal, "same-joins-narrower")
    keys = _keys(capture)
    assert {
        keys[index]: group for index, group in derive_premerge(capture, table, store).unmatched_groups
    } == {
        (REROUTED, ("ss05", "ss03+ss05")): "only-with-ss03",
        (ABSORBED, ("default", "ss04")): "same-joins-narrower",
    }


def test_derive_premerge_refuses_a_rerouting_the_build_and_the_facts_disagree_on():
    """The re-routing `derive_premerge` decides is checked against the build's. A capture given no `ink_identical` classes re-routes nothing, so a build that re-routed a unit fails the derivation instead of reporting the unit unchanged, and a unit the review facts re-route whose survivor the build left without a change group fails too."""
    capture, table, store = _rerouted_build(identical=())
    with pytest.raises(ValueError, match="do not re-route"):
        derive_premerge(capture, table, store)

    capture, table, store = _rerouted_build()
    survivor = next(ordinal for ordinal in range(table.n) if table.codepoints_text(ordinal) == REROUTED)
    table.set_unmatched_group(survivor, "")
    with pytest.raises(ValueError, match=f"{REROUTED}: the review facts re-route"):
        derive_premerge(capture, table, store)


def test_ink_group_from_flags_counts_the_ink_verdict_alone():
    """A flagged unit is machine-approved under its class whatever its no-verdict flag, and `by_class` lists the classes in the order of their first flagged unit. An unflagged no-verdict unit is exempt, and every other unflagged unit is a human unit. The (class, no-verdict) pairs are the snapshot's `class_rows` over units loaded against the live ledger, the pairs `build_facts` counts, one per unit in capture order."""
    classes = ["split-by-space-or-zwnj", "identical-unused-join-name", UNMATCHED_CLASS]
    rows = [_row("default", f"E65{index:X}:E665", classes[index % 3], (f"q{index}",)) for index in range(10)]
    table, _rows = load_table(rows, load_ledger(LEDGER_PATH), dict(LETTERS))
    snapshot = capture_premerge(table)
    assert list(snapshot.class_rows()) == [
        *[("split-by-space-or-zwnj", True)] * 4,
        *[("identical-unused-join-name", False)] * 3,
        *[(UNMATCHED_CLASS, False)] * 3,
    ]
    result = ink_group_from_flags(snapshot.class_rows(), "1000111000")
    assert result == {
        "machine_total": 4,
        "non_identical": 6,
        "by_class": {"split-by-space-or-zwnj": 1, "identical-unused-join-name": 3},
        "boundary_window_exempt": 3,
        "human_units": 3,
        "batches": 1,
    }
    assert list(result["by_class"]) == ["split-by-space-or-zwnj", "identical-unused-join-name"]


def test_the_snapshots_grains_match_the_materialized_units():
    """The snapshot's grains carry the same window, class, no-verdict flag, and configs, in row order, as the units the table materializes. `ink_group_from_flags` relies on the snapshot's no-verdict column matching the table's, so the fixture includes a no-verdict unit."""
    table, _rows = load_table(
        [
            _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
            _row("ss03", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
            _row("default", STANDALONE_MATCHED, "split-by-space-or-zwnj", ("qsKey", "qsKey")),
        ],
        load_ledger(LEDGER_PATH),
        dict(LETTERS),
    )
    grains = list(capture_premerge(table).grains())
    assert grains == [
        (unit.codepoints, unit.class_id, unit.no_verdict, unit.configs) for unit in table.units()
    ]
    assert any(grain.no_verdict for grain in grains)


def _example_table() -> tuple[UnitTable, dict[int, str | None]]:
    """A table of four windows with order, batch, and duplicate group set as the whole-corpus passes set them: three human units in two duplicate groups, the reference window among them, and one machine-approved unit outside the index. Returns the table and each unit's config note by ordinal."""
    windows = {
        REFERENCE_WINDOW_CODEPOINTS: (0, "e-0000", None),
        "E670:E653:E652:E650": (0, "e-0000", None),
        "E670:E653:E652:E651": (1, "e-0001", "only under ss10"),
        "E650:E651": (None, None, "only when ss03 is on"),
    }
    table, _rows = load_table(
        [_row("default", codepoints, "split-by-space-or-zwnj", ("q",)) for codepoints in windows],
        load_ledger(LEDGER_PATH),
        dict(LETTERS),
    )
    config_notes: dict[int, str | None] = {}
    for ordinal in range(table.n):
        batch, duplicate_group, note = windows[table.codepoints_text(ordinal)]
        table.set_order_batch(ordinal, None if batch is None else ordinal, batch)
        table.set_duplicate_group(ordinal, duplicate_group)
        config_notes[ordinal] = note
    return table, config_notes


_EXAMPLE_BUILT = {
    "human_units": 3,
    "reference_window_duplicate_siblings": 2,
    "config_note_distribution": [[None, 2], ["only under ss10", 1], ["only when ss03 is on", 1]],
}


def _example_manifest() -> dict:
    """A minimal manifest with only what the review-facts reduction reads: the three classes `manifest_group` looks up by name (`CLASS_UNIT_COUNT_KEYS`), the first holding `_example_table`'s units, with the no-verdict flags and machine-approved histogram `invariant_group` reads, the live ledger's ids as the `ledger_classes` lookup the pins order classes by, and the manifest fields the sidecar copies into its stamp."""
    ids = list(facts.CLASS_UNIT_COUNT_KEYS)
    return {
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
        "totals": {"units": 4, "rows": 4, "batches": 1, "duplicate_groups": 1},
        "classes": [
            {
                "id": class_id,
                "unit_count": 4 if position == 0 else 0,
                "no_verdict": class_id == "split-by-space-or-zwnj",
            }
            for position, class_id in enumerate(ids)
        ],
        "ledger_classes": {entry.id: {"title": entry.title} for entry in load_ledger(LEDGER_PATH)},
        "machine_approved": {
            "units": 3,
            "by_class": {"identical-variant-name": 2, "split-by-space-or-zwnj": 1},
        },
        "secondary_junctions": {"units_with_markers": 0, "junctions_with_primary_unit": 0},
    }


def test_built_group_from_memory_counts_human_units_siblings_and_notes():
    """The human units are the table's units with a batch. The reference window's duplicate-sibling count is the number of distinct windows in its duplicate group, and the config-note histogram counts every unit's note, null first and then lexicographic."""
    table, config_notes = _example_table()
    assert built_group_from_memory(table, config_notes) == _EXAMPLE_BUILT


def test_built_group_reports_a_missing_reference_window_as_none():
    """When the reference window is not a human unit, as on every mini corpus a test builds, `built_group_from_memory` reports its duplicate-sibling count as None instead of failing. On the live corpus, the pins diff shows the loss as an accepted count replaced by null."""
    table, config_notes = _example_table()
    reference = next(
        ordinal for ordinal in range(table.n) if table.codepoints_text(ordinal) == REFERENCE_WINDOW_CODEPOINTS
    )
    table.set_order_batch(reference, None, None)
    assert built_group_from_memory(table, config_notes) == {
        **_EXAMPLE_BUILT,
        "human_units": 2,
        "reference_window_duplicate_siblings": None,
    }


def _pins(row_count: int) -> dict:
    """A small pin set in the checked-in file's two-block shape: an invariant block over three classes, and two volatile groups."""
    return {
        "invariant": {
            "classes": ["split-by-space-or-zwnj", "identical-variant-name", "join-dropped"],
            "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
            "no_verdict_classes": ["split-by-space-or-zwnj"],
            "unmatched_groups": ["join-dropped"],
        },
        "volatile": {
            "audit": {"row_count": row_count, "units": 1},
            "unmatched_groups": {
                "counts": {"join-dropped": 1},
                "total": 1,
                "rerouted": {"identical-variant-name": 0},
            },
        },
    }


def _facts(pins: dict, generated_at: str = "2026-01-01T00:00:00Z") -> dict:
    manifest = {
        "generated_at": generated_at,
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
    }
    return {
        "format": FACTS_FORMAT,
        "corpus": manifest,
        "pins": pins,
    }


def test_facts_round_trip(tmp_path):
    written = _facts(_pins(row_count=2))
    write_facts(tmp_path, written)
    assert (tmp_path / facts.FACTS_FILENAME).read_text(encoding="utf-8").endswith("}\n")
    assert load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"}) == written


def test_load_facts_refuses_a_missing_wrong_format_or_orphaned_sidecar(tmp_path):
    """`load_facts` accepts only the corpus's own sidecar. It raises on a missing file, an unknown format, or a `generated_at` that differs from the manifest's, since then the sidecar and the corpus came from different builds."""
    with pytest.raises(ValueError, match="rebuild.review.build"):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    stale = _facts({})
    stale["format"] = "ams-review-facts/0"
    write_facts(tmp_path, stale)
    with pytest.raises(ValueError, match=FACTS_FORMAT):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    write_facts(tmp_path, _facts({}, generated_at="2026-01-01T00:00:00Z"))
    with pytest.raises(ValueError, match="2026-02-02T00:00:00Z"):
        load_facts(tmp_path, {"generated_at": "2026-02-02T00:00:00Z"})


def test_invariant_group_lists_classes_in_ledger_then_group_order():
    """The invariant block lists the classes, the machine-approved classes and the no-verdict classes in ledger order (the manifest's `ledger_classes`) and then `CHANGE_GROUP_ORDER`, not in the manifest's section order, so a class that changes section does not reorder them; the change groups keep the order `change_group_counts` emits. A class that is neither a ledger class nor a change group raises."""
    manifest = {
        "ledger_classes": dict.fromkeys(
            [
                "split-by-space-or-zwnj",
                "join-switches-sides",
                "halves-closer-to-no",
                "identical-variant-name",
            ],
            {},
        ),
        "classes": [
            {"id": "join-switches-sides", "no_verdict": False},
            {"id": "join-dropped", "no_verdict": False},
            {"id": "split-by-space-or-zwnj", "no_verdict": True},
            {"id": "halves-closer-to-no", "no_verdict": True},
            {"id": "identical-variant-name", "no_verdict": False},
        ],
        "machine_approved": {
            "units": 5,
            "by_class": {"identical-variant-name": 3, "split-by-space-or-zwnj": 2},
        },
    }
    assert invariant_group(manifest, {"new-join-at-baseline": 8, "only-with-ss03": 1}) == {
        "classes": [
            "split-by-space-or-zwnj",
            "join-switches-sides",
            "halves-closer-to-no",
            "identical-variant-name",
            "join-dropped",
        ],
        "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
        "no_verdict_classes": ["split-by-space-or-zwnj", "halves-closer-to-no"],
        "unmatched_groups": ["new-join-at-baseline", "only-with-ss03"],
    }
    with pytest.raises(KeyError, match="not-a-class"):
        invariant_group({**manifest, "classes": [{"id": "not-a-class", "no_verdict": False}]}, {})


def test_build_facts_reduces_the_premerge_records_to_the_pins():
    """The sidecar's ink and unmatched_groups groups are reduced from the pre-merge records `derive_premerge` returns. A change-group record whose unit's class is not UNMATCHED counts as a unit that class re-routed, and the re-routed counts list every `ink_identical` class in ledger order. The invariant block is reduced from the same manifest and change-group counts as the volatile groups, so the two blocks cannot disagree."""
    table, config_notes = _example_table()
    manifest = _example_manifest()
    ledger = load_ledger(LEDGER_PATH)
    identical = [entry.id for entry in ledger if entry.ink_identical]
    capture = capture_premerge(
        load_table(
            [
                _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
                _row("default", STANDALONE_MATCHED, "split-by-space-or-zwnj", ("qsKey", "qsKey")),
                _row("default", REROUTED, "identical-variant-name", ("qsYe", "qsMay")),
            ],
            ledger,
            dict(LETTERS),
        )[0],
        set(identical),
    )
    matched = _index_of(capture, STANDALONE_MATCHED, "default")
    premerge = PremergeFacts(
        units=len(capture),
        ink_flags="".join("1" if index == matched else "0" for index in range(len(capture))),
        unmatched_groups=sorted(
            [
                (_index_of(capture, MERGE_WINDOW, "default"), "new-join-at-baseline"),
                (_index_of(capture, REROUTED, "default"), "same-joins-narrower"),
            ]
        ),
    )
    built = build_facts(manifest, table, config_notes, capture, premerge, row_count=3)
    assert built["format"] == FACTS_FORMAT
    assert built["corpus"]["generated_at"] == manifest["generated_at"]
    volatile = built["pins"]["volatile"]
    assert volatile["audit"] == {"row_count": 3, "units": 3}
    assert volatile["built"] == _EXAMPLE_BUILT
    assert volatile["unmatched_groups"] == {
        "counts": {"new-join-at-baseline": 1, "same-joins-narrower": 1},
        "total": 2,
        "rerouted": {**dict.fromkeys(identical, 0), "identical-variant-name": 1},
    }
    assert list(volatile["unmatched_groups"]["rerouted"]) == identical
    assert volatile["ink"] == {
        "machine_total": 1,
        "non_identical": 2,
        "by_class": {"split-by-space-or-zwnj": 1},
        "boundary_window_exempt": 0,
        "human_units": 2,
        "batches": 1,
    }
    assert list(volatile["manifest"]["machine_approved"]["by_class"]) == [
        "split-by-space-or-zwnj",
        "identical-variant-name",
    ]
    assert built["pins"]["invariant"] == {
        "classes": [meta["id"] for meta in manifest["classes"]],
        "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
        "no_verdict_classes": ["split-by-space-or-zwnj"],
        "unmatched_groups": ["new-join-at-baseline", "same-joins-narrower"],
    }


def _cli_corpus(tmp_path: Path, pins: dict) -> Path:
    """Write a corpus for the review-facts CLI: a manifest whose class list, ledger lookup and machine-approved histogram agree with the invariant block of `pins`, and a sidecar carrying `pins`."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    invariant = pins["invariant"]
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0000000",
        "classes": [
            {"id": identifier, "no_verdict": identifier in invariant["no_verdict_classes"]}
            for identifier in invariant["classes"]
        ],
        "ledger_classes": {
            identifier: {} for identifier in invariant["classes"] if identifier not in CHANGE_GROUP_ORDER
        },
        "machine_approved": {
            "units": len(invariant["machine_approved_classes"]),
            "by_class": {identifier: 1 for identifier in invariant["machine_approved_classes"]},
        },
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_facts(corpus, _facts(pins))
    return corpus


def test_update_copies_the_sidecars_volatile_block_and_reduces_the_invariant_again(tmp_path, monkeypatch):
    """`--update` copies the sidecar's volatile block into the pins file unchanged and recomputes the invariant block from the corpus's manifest and the sidecar's change-group counts. The file gets the invariant block's current shape even when an older build wrote the sidecar with a different one."""
    pins_path = tmp_path / "pins.json"
    monkeypatch.setattr(facts, "PINS_PATH", pins_path)
    monkeypatch.setattr(facts, "REPO_ROOT", tmp_path)
    pins = _pins(row_count=2)
    stale = {"invariant": {"classes_count": 3}, "volatile": pins["volatile"]}
    corpus = _cli_corpus(tmp_path, pins)
    write_facts(corpus, _facts(stale))
    assert facts.main(["--update", "--corpus", str(corpus)]) == 0
    assert json.loads(pins_path.read_text(encoding="utf-8")) == pins


_ACCEPTED_INVARIANT = {
    "classes": ["split-by-space-or-zwnj", "identical-variant-name", "only-with-ss10"],
    "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
    "no_verdict_classes": ["split-by-space-or-zwnj"],
    "unmatched_groups": ["new-join-at-baseline", "only-with-ss10"],
}


def test_invariant_delta_is_empty_exactly_when_nothing_moved():
    assert invariant_delta(_ACCEPTED_INVARIANT, dict(_ACCEPTED_INVARIANT)) == []


def test_invariant_delta_names_what_appeared_and_what_went_in_the_blocks_own_order():
    """`invariant_delta` names the ids that were added to or removed from each list (classes, machine-approved classes, no-verdict classes, change groups), each in the block's own order, so the cycle log can be searched the same way on every pass."""
    current = {
        "classes": [
            "split-by-space-or-zwnj",
            "identical-variant-name",
            "identical-see-out-fusion",
            "only-with-ss04",
        ],
        "machine_approved_classes": [
            "split-by-space-or-zwnj",
            "identical-variant-name",
            "identical-see-out-fusion",
        ],
        "no_verdict_classes": ["split-by-space-or-zwnj", "identical-see-out-fusion"],
        "unmatched_groups": ["new-join-at-baseline", "only-with-ss04"],
    }
    assert invariant_delta(_ACCEPTED_INVARIANT, current) == [
        "classes +2 (identical-see-out-fusion, only-with-ss04)",
        "classes -1 (only-with-ss10)",
        "machine-approved +1 (identical-see-out-fusion)",
        "no-verdict +1 (identical-see-out-fusion)",
        "change groups +1 (only-with-ss04)",
        "change groups -1 (only-with-ss10)",
    ]


def test_invariant_delta_tells_a_reorder_and_a_shape_change_from_a_corpus_change():
    """A ledger reorder changes a list's order but not its members, and a key that changes shape (a count replaced by a list) is a change to the block, not to the corpus. Neither may be reported as classes appearing."""
    reordered = {
        **_ACCEPTED_INVARIANT,
        "classes": ["identical-variant-name", "split-by-space-or-zwnj", "only-with-ss10"],
    }
    assert invariant_delta(_ACCEPTED_INVARIANT, reordered) == ["classes reordered"]
    counted = {**_ACCEPTED_INVARIANT}
    counted["classes_count"] = counted.pop("classes")
    assert invariant_delta(counted, _ACCEPTED_INVARIANT) == [
        "classes_count no longer recorded",
        "classes newly recorded",
    ]


def test_invariant_diff_is_the_blocks_own_unified_diff():
    """The cycle prints this when the invariant block changed: the block's unified diff without the volatile block, formatted like the pins file so the lines match what `git diff` shows for those keys."""
    current = {**_ACCEPTED_INVARIANT, "unmatched_groups": ["new-join-at-baseline"]}
    lines = invariant_diff(_ACCEPTED_INVARIANT, current)
    assert lines[:2] == ["--- invariant (accepted)", "+++ invariant (this corpus)"]
    assert '-    "only-with-ss10"' in lines
    assert all("units" not in line for line in lines)
    assert invariant_diff(_ACCEPTED_INVARIANT, dict(_ACCEPTED_INVARIANT)) == []


def test_ledger_coverage_holds_the_ledgers_declarations_against_what_the_corpus_reached():
    """A class is machine-approved when the build approved any of its units, so the ledger's ink-identical declarations and the machine-approved classes can disagree in both directions. A no-verdict declaration or a ledger entry is unreached when no unit in the corpus matches it. `ledger_coverage` compares the ledger's declarations with the invariant block."""
    ledger = [
        _ledger_entry("split-by-space-or-zwnj", no_verdict=True),
        _ledger_entry("identical-variant-name", ink_identical=True),
        _ledger_entry("identical-see-out-fusion", ink_identical=True),
        _ledger_entry("vie-closer-at-baseline-join", no_verdict=True),
    ]
    invariant = {
        "classes": [
            "split-by-space-or-zwnj",
            "identical-variant-name",
            "identical-see-out-fusion",
            "only-with-ss10",
        ],
        "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name", "only-with-ss10"],
        "no_verdict_classes": ["split-by-space-or-zwnj"],
        "unmatched_groups": ["only-with-ss10"],
    }
    coverage = ledger_coverage(ledger, invariant)
    assert coverage.unreached == ("vie-closer-at-baseline-join",)
    assert coverage.ink_declared == ("identical-variant-name", "identical-see-out-fusion")
    assert coverage.ink_declared_unapproved == ("identical-see-out-fusion",)
    assert coverage.machine_approved_undeclared == ("split-by-space-or-zwnj", "only-with-ss10")
    assert coverage.no_verdict_declared == ("split-by-space-or-zwnj", "vie-closer-at-baseline-join")
    assert coverage.no_verdict_reached == ("split-by-space-or-zwnj",)
    assert coverage.no_verdict_unreached == ("vie-closer-at-baseline-join",)
    assert coverage.describe() == (
        "machine-approved: 3 classes approve units, 2 undeclared;"
        " ink-identical: 2 declared, 1 (identical-see-out-fusion) approving none;"
        " no-verdict: 1 of 2 declared reached, unreached 1 (vie-closer-at-baseline-join);"
        " ledger: 3 of 4 classes reached, unreached 1 (vie-closer-at-baseline-join)"
    )
    assert coverage.as_json()["ink_declared_unapproved"] == ["identical-see-out-fusion"]


def test_ledger_coverage_reads_clean_when_the_ledger_and_the_corpus_agree():
    ledger = [
        _ledger_entry("split-by-space-or-zwnj", no_verdict=True),
        _ledger_entry("identical-variant-name", ink_identical=True),
    ]
    coverage = ledger_coverage(ledger, _ACCEPTED_INVARIANT)
    assert coverage.unreached == ()
    assert coverage.ink_declared_unapproved == ()
    assert coverage.no_verdict_unreached == ()
    assert coverage.describe() == (
        "machine-approved: 2 classes approve units, 1 undeclared; ink-identical: 1 declared, all approving;"
        " no-verdict: 1 of 1 declared reached; ledger: 2 of 2 classes reached"
    )
