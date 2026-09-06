"""Tests for the review surface's M1-mode unit assembly: TSV/ledger loading, the dedupe to per-config-class units (including the UNMATCHED verdict windows that carry a per-config class map), and deterministic triage ordering.

None of it needs the live audit. Ordering, id assignment, batch slicing, config order, and the one-render-group invariant are properties of `build_units` and `assign_batches` over any input, and the frozen mini workload under rebuild/review/fixtures/mini/ is a thousand real windows' worth of input — so the whole module runs in the contracts lane at full width. The two claims that really were about the live corpus, that the dedupe conserves rows and that every ledger exemplar resolves, are `build_units`' own assertions now, where they cover every build rather than every gate run.

The live counts belong to the census the surface build emits and the artifact cycle diffs into rebuild/review-census-pins.json, never to an assertion here — they move with every migrated letter.
"""

import sys
from pathlib import Path

import pytest
import yaml

from rebuild.review import families
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    AuditRow,
    LedgerClass,
    assign_batches,
    batch_of,
    build_units,
    load_audit,
    load_ledger,
    load_workload,
    merge_ink_duplicate_units,
    parse_codepoints,
    release_rows,
    render_groups_for_rows,
    sort_for_triage,
)
from rebuild.review.enrich import LETTERS

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
    rows = load_audit(path)
    assert len(rows) == 3
    assert rows[0].config == "default"
    assert rows[0].baseline == ("qsPea", "qsMay.en-y0")
    assert rows[2].kinds == ("cell", "seam")


def test_load_audit_interns_every_label_and_pools_every_name_tuple(tmp_path):
    """Every label the audit states is the `sys.intern` instance of itself, so a config name, a class id or a glyph name is one object across the rows, the subset tables and the cache alike, and a name tuple two rows state the same is one tuple."""
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    rows = load_audit(path)
    assert rows[0].config is sys.intern("default") is rows[2].config
    assert rows[0].matched_entry is sys.intern("dangling-anchor-dropped") is rows[1].matched_entry
    assert rows[0].codepoints is sys.intern("E650:E665") is rows[1].codepoints
    assert rows[0].baseline is rows[1].baseline
    assert rows[0].new is rows[1].new
    assert rows[0].kinds is rows[1].kinds
    assert rows[0].baseline[0] is sys.intern("qsPea")
    assert rows[2].kinds[1] is sys.intern("seam")


def test_load_audit_rejects_wrong_header(tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text("nope\tnope\n")
    with pytest.raises(ValueError):
        load_audit(path)


def test_fixture_units_dedupe_and_carry_configs(mini_bundle, tmp_path):
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    units = build_units(load_audit(path), load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert len(units) == 2
    by_codepoints = {unit.codepoints: unit for unit in units}
    assert by_codepoints["E650:E665"].configs == ("default", "ss02")
    assert by_codepoints["E652:E670"].kinds == ("cell", "seam")


def test_conflicting_class_resolves_to_unmatched_with_config_classes(mini_bundle):
    """A triple whose audit rows carry different classes per config is not a build error. When one config leaves it UNMATCHED (the ss03-chain-join-gains windows, blessed under ss03 but novel under default), the unit takes the UNMATCHED sentinel as its class — so the novel default behavior is what gets adjudicated — and records every config's class in config_classes. Two distinct *matched* classes for one triple is still a genuine classification bug and raises."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "ss03-chain-join-gains", ("a",), ("b",)),
    ]
    (unit,) = build_units(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
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
    assert render_groups_for_rows(rows) == (("default", "ss03"), ("ss02",))


@pytest.fixture
def mini(mini_bundle):
    """The frozen mini-M1 audit under rebuild/review/fixtures/mini/, loaded against the bundle's pinned ledger — a thousand-odd real windows over four letters, which is what these properties want: enough classes to order, enough per-config splits to dedupe, and not one byte of rebuild/out/. Regenerating it is `fixtures/mini/regenerate.py`."""
    return load_workload(MINI_AUDIT, mini_bundle.ledger, dict(LETTERS))


def test_build_units_pools_the_per_unit_tuples_and_interns_the_group(mini):
    """A unit's config set, kinds and render groups are drawn from a few dozen distinct tuples over the whole audit and its group from a few thousand family pairs, so two units that state the same value hold the same object rather than one built apiece."""
    by_value: dict[str, dict] = {"configs": {}, "kinds": {}, "render_groups": {}}
    for unit in mini.units:
        assert unit.group is sys.intern(unit.group)
        for name, seen in by_value.items():
            value = getattr(unit, name)
            assert seen.setdefault(value, value) is value, (name, value)
    assert all(len(seen) < len(mini.units) for seen in by_value.values())


def test_release_rows_leaves_the_count_behind(tmp_path, mini_bundle):
    """A unit's row count is stated once, off the rows it was built from, and survives their release; releasing empties the rows and nothing else."""
    path = tmp_path / "audit.tsv"
    path.write_text(FIXTURE_AUDIT)
    units = build_units(load_audit(path), load_ledger(mini_bundle.ledger), dict(LETTERS))
    counts = {unit.codepoints: unit.row_count for unit in units}
    assert counts == {"E650:E665": 2, "E652:E670": 1}
    assert all(unit.row_count == len(unit.rows) for unit in units)
    release_rows(units)
    assert all(unit.rows == () for unit in units)
    assert {unit.codepoints: unit.row_count for unit in units} == counts
    assert {unit.codepoints: unit.configs for unit in units} == {
        "E650:E665": ("default", "ss02"),
        "E652:E670": ("default",),
    }


def test_every_unit_has_exactly_one_render_group(mini):
    """The M1 invariant of the dedupe key: a unit's rows share (codepoints, baseline, new), so the per-config rendered outcomes can never differ within a unit — even the per-config-split UNMATCHED units (blessed under ss03, novel under default) render identically across configs, the difference being only the class label. If this ever fails, the data violates the dedupe key's documented guarantee and the extra groups must render stacked, never collapsed."""
    for unit in mini.units:
        assert unit.render_groups == (unit.configs,)


def test_the_dedupe_loses_no_rows(mini):
    """Every audit row ends up under exactly one unit: the dedupe groups rows, it never drops or duplicates one. How many there are is the census's business, so only the accounting is asserted — plus that both sides are nonempty, since an empty audit would satisfy the sum vacuously. Over the live corpus `check_shards` re-proves the same conservation on every build through the shard totals, which is why it need not be swept here."""
    assert mini.row_count > 0
    assert len(mini.units) > 0
    assert sum(len(unit.rows) for unit in mini.units) == mini.row_count


def test_triage_order_follows_ledger_then_group_then_codepoints(mini):
    # The UNMATCHED units carry the sentinel class at workload level (their verdict family is assigned later, at build time); they rank after every ledger class so they sort last and clean-unit ids are preserved.
    class_order = {entry.id: index for index, entry in enumerate(mini.ledger)}
    indices = [class_order.get(unit.class_id, len(mini.ledger)) for unit in mini.units]
    assert indices == sorted(indices)
    by_class: dict[str, list] = {}
    for unit in mini.units:
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
    """An id is the content key's, stamped at enrichment, and a batch is a slice of the index the build lays down once every unit has its ink flags and its family — so the loaded workload carries none of them."""
    for unit in mini.units:
        assert unit.unit_id == ""
        assert unit.order is None
        assert unit.batch is None
        assert unit.ink_identical is False
        assert unit.picture_identical is False


def test_assign_batches_indexes_the_human_workload_and_nulls_machine_units(mini):
    """`assign_batches` is pure over a unit list, so the mini workload witnesses it exactly as the live one did — and without the live graph there is no longer a test that mutates a shared session fixture and has to put it back. Every human unit takes its position in the list of human units and the slice that position falls in; every other unit takes neither."""
    units = mini.units
    for index, unit in enumerate(units):
        unit.ink_identical = index % 3 == 0
        unit.picture_identical = index % 3 == 2 and index % 7 == 0
        unit.junior_equivalent = index % 3 == 1 and index % 5 == 0
    total = assign_batches(units, batch_size=300)
    human = [unit for unit in units if not unit.machine_approved and not unit.no_verdict]
    assert human
    assert [unit.order for unit in human] == list(range(len(human)))
    assert [unit.batch for unit in human] == [batch_of(index, 300) for index in range(len(human))]
    assert all(
        unit.batch is None and unit.order is None
        for unit in units
        if unit.machine_approved or unit.no_verdict
    )
    assert total == (len(human) + 299) // 300


def test_sort_for_triage_orders_by_class_group_window_then_id():
    """The order the manifest's index takes, with the id as the last term so sibling units of one window — which share class, group and codepoints — fall into an order that is the same on every surface rather than the audit's."""
    rows = [
        AuditRow("default", "E650:E652", ("cell",), "class-b", ("a",), ("b",)),
        AuditRow("default", "E650:E652", ("cell",), "class-b", ("a",), ("c",)),
        AuditRow("default", "E650:E650", ("cell",), "class-a", ("a",), ("b",)),
        AuditRow("default", "E650:E652:E650", ("cell",), "class-b", ("a",), ("b",)),
    ]
    ledger = [
        LedgerClass("class-b", "intended", "", False, False, 0, frozenset()),
        LedgerClass("class-a", "intended", "", False, False, 0, frozenset()),
    ]
    units = build_units(rows, ledger, dict(LETTERS))
    for unit in units:
        unit.unit_id = "u-" + ("z" if unit.new == ("b",) else "a") + unit.codepoints.replace(":", "")
    sort_for_triage(units, {"class-a": 0, "class-b": 1}, dict(LETTERS))
    assert [(unit.class_id, unit.codepoints, unit.unit_id) for unit in units] == [
        ("class-a", "E650:E650", "u-zE650E650"),
        ("class-b", "E650:E652", "u-aE650E652"),
        ("class-b", "E650:E652", "u-zE650E652"),
        ("class-b", "E650:E652:E650", "u-zE650E652E650"),
    ]


def test_no_verdict_flag_mirrors_the_ledger_class():
    """The ledger's `no_verdict: true` marks every unit of a wholesale-adjudicated class exempt from individual verdicts; every other unit stays verdictable. Which classes carry the flag is the ledger's own content, not a code contract, so the propagation runs against a synthetic ledger: a unit carries the flag iff its class does."""
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
    units = build_units(rows, ledger, dict(LETTERS))
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
    assert [unit.unit_id for unit in again.units] == [unit.unit_id for unit in mini.units]
    assert [unit.codepoints for unit in again.units] == [unit.codepoints for unit in mini.units]


def test_configs_within_a_unit_are_in_acceptance_order(mini):
    order = {token: index for index, token in enumerate(ACCEPTANCE_CONFIGS)}
    for unit in mini.units:
        ranks = [order[config] for config in unit.configs]
        assert ranks == sorted(ranks)


def test_parse_codepoints():
    assert parse_codepoints("200C:E652:E679") == (0x200C, 0xE652, 0xE679)


def test_ink_duplicate_siblings_fold_to_one_unit(mini_bundle):
    """The name-grain dedupe key splits one visual question in two when a config merely relabels a glyph (the old font's ss04 rename of word-initial ·It). With an ink signature reporting every render of the window identical, the siblings fold: the earliest-config unit survives with the union of configs, rows, kinds, and per-config classes, a single render group, and contiguous renumbered ids."""
    rows = [
        AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("qsPea", "qsMay.en-y0"), ("b",)),
        AuditRow("ss03", "E650:E665", ("cell",), "UNMATCHED", ("qsPea", "qsMay.en-y0"), ("b",)),
        AuditRow("ss04", "E650:E665", ("seam",), "UNMATCHED", ("qsPea.ss04", "qsMay.en-y0"), ("b",)),
        AuditRow("default", "E650:E650", ("cell",), "UNMATCHED", ("qsPea", "qsPea"), ("c",)),
    ]
    units = build_units(rows, load_ledger(mini_bundle.ledger), dict(LETTERS))
    assert len(units) == 3
    stats = merge_ink_duplicate_units(units, lambda text, config: text)
    assert stats == {"windows_folded": 1, "units_folded": 1, "kept_split_matched_classes": 0}
    assert len(units) == 2
    merged = next(unit for unit in units if unit.codepoints == "E650:E665")
    assert merged.configs == ("default", "ss03", "ss04")
    assert merged.row_count == len(merged.rows) == 3
    assert merged.baseline == ("qsPea", "qsMay.en-y0")
    assert merged.kinds == ("cell", "seam")
    assert merged.render_groups == (merged.configs,)
    assert merged.config_classes == {"default": "UNMATCHED", "ss03": "UNMATCHED", "ss04": "UNMATCHED"}


def test_ink_duplicate_fold_respects_matched_classes_and_exemptions(mini_bundle):
    """A fold that would put two distinct matched ledger classes on one unit is skipped (different names legitimately hit different ledger predicates), while a matched class folding with an UNMATCHED sibling resolves UNMATCHED-wins and recomputes the no-verdict flag from the exemption set."""
    conflicting = build_units(
        [
            AuditRow("default", "E650:E665", ("cell",), "class-a", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "class-b", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    stats = merge_ink_duplicate_units(conflicting, lambda text, config: text)
    assert stats["kept_split_matched_classes"] == 1
    assert len(conflicting) == 2

    mixed = build_units(
        [
            AuditRow("default", "E650:E665", ("cell",), "boundary-echo", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "UNMATCHED", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    for unit in mixed:
        unit.no_verdict = unit.class_id == "boundary-echo"
    merge_ink_duplicate_units(mixed, lambda text, config: text, exempt_classes={"boundary-echo"})
    (merged,) = mixed
    assert merged.class_id == "UNMATCHED"
    assert merged.no_verdict is False
    assert merged.config_classes == {"default": "boundary-echo", "ss04": "UNMATCHED"}


def test_units_whose_configs_render_differently_never_fold(mini_bundle):
    """A unit only folds when every config on both sides yields one ink signature; per-config signatures leave everything standing."""
    units = build_units(
        [
            AuditRow("default", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
            AuditRow("ss02", "E650:E665", ("cell",), "UNMATCHED", ("a",), ("b",)),
            AuditRow("ss04", "E650:E665", ("cell",), "UNMATCHED", ("a2",), ("b",)),
        ],
        load_ledger(mini_bundle.ledger),
        dict(LETTERS),
    )
    stats = merge_ink_duplicate_units(units, lambda text, config: (text, config))
    assert stats == {"windows_folded": 0, "units_folded": 0, "kept_split_matched_classes": 0}
    assert len(units) == 2


def _ledger(tmp_path, *ids: str) -> Path:
    path = tmp_path / "ledger.yaml"
    path.write_text(
        "".join(f"- id: {identifier}\n  status: accepted\n  why: because\n" for identifier in ids),
        encoding="utf-8",
    )
    return path


def test_a_ledger_declaring_one_class_twice_is_refused_at_load(tmp_path):
    """Everything downstream indexes the ledger by id — the class order the surface shards in, the oracle's row matcher, the verdict store's class keys — so a repeated id is a class whose second entry silently loses, and it is refused where the file is read."""
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
    """The catch-all and the verdict families are classes the build makes for itself, so a ledger entry claiming one of those ids would be shadowed by a class the ledger does not describe."""
    with pytest.raises(ValueError, match=identifier):
        load_ledger(_ledger(tmp_path, identifier))


def test_the_live_ledger_loads_one_class_per_entry():
    """The other end of those refusals: the checked-in ledger passes them, and every entry in the file reaches the loader as its own class."""
    path = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = load_ledger(path)
    assert [entry["id"] for entry in entries] == [entry.id for entry in classes]
