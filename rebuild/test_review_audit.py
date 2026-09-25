"""Tests for the review surface's M1-mode unit assembly: TSV/ledger loading, the dedupe to per-config-class units (including the UNMATCHED verdict windows that carry a per-config class map), and deterministic triage ordering.

None of it reads the live audit. Ordering, batch slicing, config order, and the one-render-group invariant are properties of `build_units` and `assign_batches` over any input, so the tests use the frozen mini workload under rebuild/review/fixtures/mini/, which holds real windows. Every build also checks that the dedupe loses no rows: `_SurfaceCheck.finish` in rebuild/review/build.py compares the manifest's row total with the rows summed over its classes.

The live counts change with every migrated letter, so they are not asserted here. The surface build's census reports them, and the artifact cycle diffs them into rebuild/review-census-pins.json.
"""

import sys
from pathlib import Path

import pytest
import yaml

from rebuild.review import families
from rebuild.review import unit_cache
from rebuild.review import unit_store
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    AuditRow,
    RowColumns,
    LedgerClass,
    assign_batches,
    batch_of,
    build_units,
    format_codepoints,
    load_audit,
    load_ledger,
    load_table,
    load_workload,
    merge_ink_duplicate_units,
    parse_codepoints,
    release_rows,
    render_groups_for_rows,
    sort_for_triage,
    triage_key,
)
from rebuild.review.build import row_columns_census, signature_text, unit_table_census
from rebuild.review.columns import MappingPool, TuplePool
from rebuild.review.enrich import LETTERS
from rebuild.review.unit_store import UnitStore
from rebuild.tools import pile_tally

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"

FIXTURE_AUDIT = """config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew
default\tE650:E665\tcell\tdangling-anchor-dropped\tqsPea|qsMay.en-y0\tqsPea/full/None/baseline/|qsMay/loop/baseline/None/
ss02\tE650:E665\tcell\tdangling-anchor-dropped\tqsPea|qsMay.en-y0\tqsPea/full/None/baseline/|qsMay/loop/baseline/None/
default\tE652:E670\tcell,seam\thalves-entry-extension-restored\tqsTea.half.ex-y5|qsIt.en-y5\tqsTea/half/None/x-height/|qsIt/hapax/x-height/None/en-ext-1
"""


def test_load_audit_parses_fixture(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    rows = list(load_audit(path))
    assert len(rows) == 3
    assert rows[0].config == "default"
    assert rows[0].baseline == ("qsPea", "qsMay.en-y0")
    assert rows[2].kinds == ("cell", "seam")


def test_load_audit_interns_every_label_and_pools_every_name_tuple(tmp_path):
    """Every label the audit states is interned with `sys.intern`, so a config name, class id or glyph name is one object everywhere it is used. Equal name tuples in two rows are one tuple object, the one held by the pool passed in, so the columns' ids name the rows' own tuples."""
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    names = TuplePool()
    rows = list(load_audit(path, names))
    assert rows[0].config is sys.intern("default") is rows[2].config
    assert rows[0].matched_entry is sys.intern("dangling-anchor-dropped") is rows[1].matched_entry
    assert rows[0].codepoints is sys.intern("E650:E665") is rows[1].codepoints
    assert rows[0].baseline is rows[1].baseline
    assert rows[0].new is rows[1].new
    assert rows[0].kinds is rows[1].kinds
    assert rows[0].baseline[0] is sys.intern("qsPea")
    assert rows[2].kinds[1] is sys.intern("seam")
    assert names[names.id(rows[0].baseline)] is rows[0].baseline
    assert names[names.id(("cell", "seam"))] is rows[2].kinds


def test_load_audit_rejects_wrong_header(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text("nope\tnope\n")
    with pytest.raises(ValueError):
        list(load_audit(path))


def test_fixture_units_dedupe_and_carry_configs(mini_bundle, tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    units, _rows = build_units(load_audit(path), load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert len(units) == 2
    by_codepoints = {unit.codepoints: unit for unit in units}
    assert by_codepoints["E650:E665"].configs == ("default", "ss02")
    assert by_codepoints["E652:E670"].kinds == ("cell", "seam")


def test_conflicting_class_resolves_to_unmatched_with_config_classes(mini_bundle):
    """A triple whose audit rows carry different classes per config is not a build error. When one config leaves it UNMATCHED (the ss03-chain-join-gains windows, blessed under ss03 but new under default), the unit's class is the UNMATCHED sentinel, so the new default behavior is what gets reviewed, and `config_classes` records every config's class. Two different matched classes for one triple is a classification bug and raises."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "ss03-chain-join-gains", ("a",), ("b",)),
    ]
    (unit,), _rows = build_units(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert unit.class_id == "UNMATCHED"
    assert unit.config_classes == {"default": "UNMATCHED", "ss03": "ss03-chain-join-gains"}

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
    """The class maps pool to one instance per distinct mapping, keyed on the mapping's insertion order. Two units whose maps differ only in key order get two entries, and each materializes in its own order, which is the order the shipped fragment carries. The pooled instance is typed read-only, and a whole-build test in `rebuild/test_unit_cache.py` checks that no build, cold or served, writes through it."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "ss03-chain-join-gains", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E652", ("cell",), "ss03-chain-join-gains", ("a",), ("b",)),
        AuditRow("ss03", "E650:E650", ("cell",), "ss03-chain-join-gains", ("a",), ("b",)),
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
        AuditRow("default", "E650:E665", ("seam",), "UNMATCHED", ("a",), ("b",)),
    ]
    (unit,), columns = build_units(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert unit.configs == ("default", "ss03", "ss02")
    assert columns.configs(unit.rows_start, unit.row_count) == unit.configs
    assert list(unit.config_classes) == ["ss03", "ss02", "default"]
    assert unit.kinds == ("cell", "seam")
    assert unit.render_groups == (unit.configs,)
    assert [
        columns.line(index, unit.codepoints) for index in range(unit.rows_start, unit.rows_start + 3)
    ] == [
        "default\tE650:E665\tseam\tUNMATCHED\ta\tb",
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
    census = row_columns_census(workload.rows)
    assert census.count == 3 and census.packed is not None
    assert census.est_bytes == census.packed.est_bytes and census.packed.string_bytes > 0
    assert census.packed.strings == len(workload.table.strings)


def test_the_row_columns_refuse_a_config_vocabulary_wider_than_a_byte():
    columns = RowColumns()
    for index in range(256):
        columns.config_id(f"ss{index}")
    with pytest.raises(ValueError, match="256"):
        columns.config_id("one-more")


def test_build_units_seals_the_tuple_pool_once_its_columns_are_written():
    """The pool's tuple-to-id dict is needed only while the rows are read, and it is the largest thing the pool holds, so `build_units` seals the pool when the rows end. The ids still name their tuples, which the units hold as their own name tuples, and a later `id` call raises."""
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
    """Every audit row ends up under exactly one unit, and the units' runs cover the columns without gaps or overlap. The census reports the counts, so only the totals are asserted, plus that both sides are nonempty, since an empty audit would pass the sum trivially. On the live corpus, `_SurfaceCheck.finish` checks the same row total on every build."""
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
    # The UNMATCHED units carry the sentinel class at workload level, because their verdict family is assigned later in the build. They rank after every ledger class.
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
    """An id comes from the content key, which is stamped at enrichment, and a batch is assigned only once every unit has its ink flags and its family, so the loaded workload has neither."""
    for unit in mini.units():
        assert unit.unit_id == ""
        assert unit.order is None
        assert unit.batch is None
        assert unit.ink_identical is False
        assert unit.picture_identical is False


def test_assign_batches_indexes_the_human_workload_and_nulls_machine_units(mini):
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
    """The manifest's index order, with the id as the last term, so sibling units of one window (same class, group and codepoints) get the same order on every surface instead of the audit's order. A shorter window sorts ahead of a longer one whatever its codepoints: the boundary-led `0020:E650:E652` window, whose group is the same two families, sorts after every two-cell window of that group and before `E650:E652:E650`, which is also three cells and compares higher cell by cell. The sort agrees with `triage_key` over the id strings."""
    rows = [
        AuditRow("default", "E650:E652", ("cell",), "class-b", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "class-b", ("a",), ("c",)),
        AuditRow("default", "E650:E650", ("cell",), "class-a", ("a",), ("b",)),
        AuditRow("default", "E650:E652:E650", ("cell",), "class-b", ("a",), ("b",)),
        AuditRow("default", "0020:E650:E652", ("cell",), "class-b", ("a",), ("b",)),
    ]
    ledger = [
        LedgerClass("class-b", "intended", "", False, False, 0, frozenset()),
        LedgerClass("class-a", "intended", "", False, False, 0, frozenset()),
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


def test_no_verdict_flag_mirrors_the_ledger_class():
    """The ledger's `no_verdict: true` exempts every unit of a class judged as a whole from individual verdicts. Which classes carry the flag is ledger content, so the test uses a synthetic ledger: a unit carries the flag if and only if its class does."""
    ledger = [
        LedgerClass(
            id="wholesale-adjudicated",
            status="intended",
            why="",
            ink_identical=False,
            no_verdict=True,
            count=0,
            exemplar_keys=frozenset(),
        ),
        LedgerClass(
            id="ordinary-class",
            status="intended",
            why="",
            ink_identical=False,
            no_verdict=False,
            count=0,
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
    """Pins the codepoint-string parsing in both directions. `build.signature_text` parses a row's window into the text the comparator shapes, and `build.ink_sig` formats a window back into a signature key. `unit_cache.signature_code_paths` leaves audit.py out of the signature store's stamp because this test pins the parsing. The test below pins the `chr` join that finishes the text."""
    assert parse_codepoints("200C:E652:E679") == (0x200C, 0xE652, 0xE679)
    assert format_codepoints((0x200C, 0xE652, 0xE679)) == "200C:E652:E679"


def test_the_signature_text_is_pinned_beside_the_store_format():
    """Pins the text `build.signature_text` returns for a window together with `SIGNATURE_STORE_FORMAT`. A store written for one text keeps serving its digests to a build that shapes another until the format changes, so an edit that changes a result here must bump the format and update both literals together."""
    assert unit_cache.SIGNATURE_STORE_FORMAT == "ams-review-ink-signatures/2"
    assert signature_text("200C:E652:E679") == "\u200c\ue652\ue679"
    assert signature_text("0020:E650:00B7") == " \ue650\u00b7"
    assert signature_text("E650") == "\ue650"


def test_ink_duplicate_siblings_fold_to_one_unit(mini_bundle):
    """The name-grain dedupe key splits one visual question into two units when a config only renames a glyph (the old font's ss04 rename of word-initial ·It). When the ink signature reports every render of the window identical, the siblings fold. The earliest-config unit survives with the union of configs, kinds and per-config classes, a merged run of both siblings' rows in the columns (each row keeps its own rendered names), and a single render group, and compacting the table removes the absorbed unit. The two runs the merged run replaces are orphaned: outside the columns' live count but still in their bytes."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("qsPea", "qsMay.en-y0"), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "UNMATCHED", ("qsPea", "qsMay.en-y0"), ("b",)),
        AuditRow("ss04", "E650:E665", ("seam",), "UNMATCHED", ("qsPea.ss04", "qsMay.en-y0"), ("b",)),
        AuditRow("default", "E650:E650", ("cell",), "UNMATCHED", ("qsPea", "qsPea"), ("c",)),
    ]
    table, columns = load_table(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert table.n == 3
    before = {ordinal: table.config_classes(ordinal) for ordinal in range(table.n)}
    stats = merge_ink_duplicate_units(table, columns, lambda text, config: text)
    assert stats == {"windows_folded": 1, "units_folded": 1, "kept_split_matched_classes": 0}
    assert table.n == 3 and sum(table.live(ordinal) for ordinal in range(table.n)) == 2
    victim = next(ordinal for ordinal in range(table.n) if not table.live(ordinal))
    assert table.survivor(victim) != victim and table.live(table.survivor(victim))
    compaction = table.compact()
    assert table.n == 2 and list(compaction.folded) == [1 if ordinal == victim else 0 for ordinal in range(3)]
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
    assert row_columns_census(columns).count == 4
    assert merged.baseline == ("qsPea", "qsMay.en-y0")
    assert merged.kinds == ("cell", "seam")
    assert merged.render_groups == (merged.configs,)
    assert merged.config_classes == {"default": "UNMATCHED", "ss03": "UNMATCHED", "ss04": "UNMATCHED"}
    assert merged.config_classes is table.mappings.pooled(dict(merged.config_classes))
    assert sorted(tuple(mapping.items()) for mapping in before.values()) == sorted(
        [
            (("default", "UNMATCHED"), ("ss03", "UNMATCHED")),
            (("ss04", "UNMATCHED"),),
            (("default", "UNMATCHED"),),
        ]
    ), "the survivor's map was re-pooled rather than the pooled pre-fold instances rewritten"


def test_ink_duplicate_fold_respects_matched_classes_and_exemptions(mini_bundle):
    """A fold that would put two different matched ledger classes on one unit is skipped, because different glyph names can match different ledger predicates. A matched class folding with an UNMATCHED sibling takes the UNMATCHED class and recomputes the no-verdict flag from the exempt classes."""
    conflicting, columns = load_table(
        [
            AuditRow("default", "E650:E665", ("cell",), "class-a", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "class-b", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    stats = merge_ink_duplicate_units(conflicting, columns, lambda text, config: text)
    assert stats["kept_split_matched_classes"] == 1
    conflicting.compact()
    assert conflicting.n == 2

    mixed, columns = load_table(
        [
            AuditRow("default", "E650:E665", ("cell",), "boundary-echo", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "UNMATCHED", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    for ordinal in range(mixed.n):
        mixed.set_no_verdict(ordinal, mixed.class_id(ordinal) == "boundary-echo")
    merge_ink_duplicate_units(mixed, columns, lambda text, config: text, exempt_classes={"boundary-echo"})
    mixed.compact()
    (merged,) = mixed.units()
    assert merged.class_id == "UNMATCHED"
    assert merged.no_verdict is False
    assert merged.config_classes == {"default": "boundary-echo", "ss04": "UNMATCHED"}


def test_units_whose_configs_render_differently_never_fold(mini_bundle):
    """A unit only folds when every config on both sides yields one ink signature; per-config signatures leave everything standing."""
    table, columns = load_table(
        [
            AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
            AuditRow("ss02", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "UNMATCHED", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    stats = merge_ink_duplicate_units(table, columns, lambda text, config: (text, config))
    assert stats == {"windows_folded": 0, "units_folded": 0, "kept_split_matched_classes": 0}
    assert table.compact().folded == bytearray(2) and table.n == 2


def test_the_name_tuples_are_released_after_phase_one(mini):
    """The table holds each unit's `baseline` and `new` as ids into the name pool it shares with the row columns, until the build calls `release_names` after phase 1. Releasing drops both columns and the pool, the census no longer counts the pool's bytes, and a unit materialized afterward has empty name tuples."""
    table = mini.table
    assert table.names is not None and table.names.sealed
    unit = table.unit(0)
    assert unit.baseline and unit.new and unit.baseline is table.baseline(0)
    before = unit_table_census(table)
    assert before.packed is not None
    pooled = pile_tally.pool_bytes(table.names)
    columns = 2 * 4 * table.n
    table.release_names()
    after = unit_table_census(table)
    assert after.packed is not None
    assert table.names is None and table.baseline(0) == () == table.new(0)
    assert table.unit(0).baseline == () == table.unit(0).new
    assert before.packed.est_bytes - after.packed.est_bytes == pooled + columns
    assert after.count == before.count == table.n


def test_the_table_census_is_the_columns_bytes_and_the_pools_priced_beside_the_string_table(mini):
    """The table's `workload.units` reading is exact. The packed figure is the columns' bytes plus every pool's packed size, the string table is reported beside it, and the two summed are the walked figure. A compaction removes the folded rows' bytes from it."""
    table = mini.table
    reading = unit_table_census(table)
    assert reading.packed is not None
    columns = sum(len(column) * column.itemsize for column in table.columns())
    pools = sum(pile_tally.pool_bytes(pool) for pool in table.pools())
    strings = table.strings.chars + len(table.strings) * pile_tally.OFFSET_WIDTH
    assert reading == pile_tally.Measure(
        table.n,
        columns + pools + strings,
        pile_tally.PackedCost(columns + pools, len(table.strings), strings),
    )
    assert reading.packed.est_bytes / table.n < 200
    table.fold_into(1, 0)
    table.compact()
    shrunk = unit_table_census(table)
    assert shrunk.count == reading.count - 1 and shrunk.est_bytes < reading.est_bytes


def _ledger(tmp_path, *ids: str) -> Path:
    path = tmp_path / "ledger.yaml"
    path.write_text(
        "".join(f"- id: {identifier}\n  status: accepted\n  why: because\n" for identifier in ids),
        encoding="utf-8",
    )
    return path


def test_a_ledger_declaring_one_class_twice_is_refused_at_load(tmp_path):
    """Code downstream indexes the ledger by id, so a repeated id would make one entry silently replace the other. `load_ledger` raises on it."""
    with pytest.raises(ValueError, match="halves-entry-extension-restored"):
        load_ledger(
            _ledger(
                tmp_path,
                "dangling-anchor-dropped",
                "halves-entry-extension-restored",
                "halves-entry-extension-restored",
            )
        )


@pytest.mark.parametrize("identifier", ["UNMATCHED", families.FAMILY_ORDER[0]])
def test_a_ledger_claiming_a_synthesized_class_is_refused_at_load(tmp_path, identifier):
    """The build creates the UNMATCHED catch-all and the verdict family classes itself, so a ledger entry with one of those ids would be overridden by a class the ledger does not describe."""
    with pytest.raises(ValueError, match=identifier):
        load_ledger(_ledger(tmp_path, identifier))


def test_the_live_ledger_loads_one_class_per_entry():
    """The checked-in ledger passes those checks, and every entry in the file loads as its own class."""
    path = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = load_ledger(path)
    assert [entry["id"] for entry in entries] == [entry.id for entry in classes]
