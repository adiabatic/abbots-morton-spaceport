"""Tests for the review surface's M1-mode unit assembly: TSV/ledger loading, the dedupe to per-config-class units (including the UNMATCHED verdict windows that carry a per-config class map), and deterministic triage ordering.

None of it needs the live audit. Ordering, id assignment, batch slicing, config order, and the one-render-group invariant are properties of `build_units` and `assign_batches` over any input, and the frozen mini workload under rebuild/review/fixtures/mini/ is a thousand real windows' worth of input — so the whole module runs in the contracts lane at full width. The two claims that really were about the live corpus, that the dedupe conserves rows and that every ledger exemplar resolves, are `build_units`' own assertions now, where they cover every build rather than every gate run.

The live counts belong to the census the surface build emits and the artifact cycle diffs into rebuild/review-census-pins.json, never to an assertion here — they move with every migrated letter.
"""

from pathlib import Path

import pytest

from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    AuditRow,
    LedgerClass,
    assign_batches,
    build_units,
    load_audit,
    load_ledger,
    load_workload,
    merge_ink_duplicate_units,
    parse_codepoints,
    render_groups_for_rows,
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


def test_every_unit_has_exactly_one_render_group(mini):
    """The M1 invariant of the dedupe key: a unit's rows share (codepoints, baseline, new), so the per-config rendered outcomes can never differ within a unit — even the per-config-split UNMATCHED units (blessed under ss03, novel under default) render identically across configs, the difference being only the class label. If this ever fails, the data violates the dedupe key's documented guarantee and the extra groups must render stacked, never collapsed."""
    for unit in mini.units:
        assert unit.render_groups == (unit.configs,)


def test_the_dedupe_loses_no_rows(mini):
    """Every audit row ends up under exactly one unit: the dedupe groups rows, it never drops or duplicates one. How many there are is the census's business, so only the accounting is asserted — plus that both sides are nonempty, since an empty audit would satisfy the sum vacuously. Over the live corpus this is `build_units`' own assertion, which is why it need not be swept here."""
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


def test_unit_ids_are_sequential_and_batches_unassigned_until_ink_is_known(mini):
    for index, unit in enumerate(mini.units):
        assert unit.unit_id == f"u-{index:04d}"
        assert unit.batch is None
        assert unit.ink_identical is False


def test_assign_batches_slices_the_human_workload_and_nulls_machine_units(mini):
    """`assign_batches` is pure over a unit list, so the mini workload witnesses it exactly as the live one did — and without the live graph there is no longer a test that mutates a shared session fixture and has to put it back."""
    units = mini.units
    for index, unit in enumerate(units):
        unit.ink_identical = index % 3 == 0
        unit.junior_equivalent = index % 3 == 1 and index % 5 == 0
    total = assign_batches(units, batch_size=300)
    human = [
        unit
        for unit in units
        if not unit.ink_identical and not unit.junior_equivalent and not unit.no_verdict
    ]
    assert human
    assert [unit.batch for unit in human] == [index // 300 for index in range(len(human))]
    assert all(
        unit.batch is None
        for unit in units
        if unit.ink_identical or unit.junior_equivalent or unit.no_verdict
    )
    assert total == (len(human) + 299) // 300


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
    assert [unit.unit_id for unit in units] == ["u-0000", "u-0001"]
    merged = next(unit for unit in units if unit.codepoints == "E650:E665")
    assert merged.configs == ("default", "ss03", "ss04")
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
