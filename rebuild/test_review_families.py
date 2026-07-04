"""Tests for the round-3 verdict-family grouper (rebuild/review/families.py): the seam-gain/seam-loss discriminator over hand-built enriched stubs, and the integration partition over the live UNMATCHED units at the name-grain (pre-merge) dedupe — deterministic, total (every window lands in a family, summing to the audit's 1,579), with the stylistic-set-only windows deferred and the named default families matching the measured census. The built surface then folds ink-duplicate siblings before families are assigned, which pulls the relabeled-only ss04 halves out of deferred-ss04 into their default families; the built counts are pinned in test_review_build."""

import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from rebuild.review import families
from rebuild.review.audit import _config_index, load_audit, parse_codepoints, render_groups_for_rows
from rebuild.review.audit import Unit, group_for
from rebuild.review.enrich import LETTERS, Enricher, load_spec
from rebuild.review.families import FAMILY_ORDER, FAMILY_WHY, assign_family

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
SUBSETS = REPO_ROOT / "rebuild" / "out" / "m1"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

MEASURED_CENSUS = {
    "no-chain-gains": 108,
    "tea-it-xheight": 33,
    "oy-it-baseline": 16,
    "may-utter-gains": 82,
    "seam-loss-withdrawal": 370,
    "extension-non-summing": 529,
    "unmatched-misc": 128,
    "deferred-ss04": 223,
    "deferred-ss03": 90,
}


def _enriched(before, after, cells, config_classes):
    """A minimal stand-in carrying exactly the attributes assign_family reads."""
    unit = SimpleNamespace(config_classes=dict(config_classes), configs=tuple(config_classes))
    return SimpleNamespace(unit=unit, before_seams=before, after_seams=after, after_cells=cells)


def _cells(*families_):
    return tuple(f"{family}/stance/None/None/" for family in families_)


DEFAULT = {"default": "UNMATCHED"}


def test_family_order_and_why_agree():
    assert set(FAMILY_ORDER) == set(FAMILY_WHY)
    assert FAMILY_ORDER[-3:] == ["deferred-ss04", "deferred-ss10", "deferred-ss03"]


def test_gain_families_by_pair():
    assert (
        assign_family(_enriched(("break", "y0"), ("y5", "y0"), _cells("qsTea", "qsIt", "qsDay"), DEFAULT))
        == "tea-it-xheight"
    )
    assert assign_family(_enriched(("break",), ("y0",), _cells("qsOy", "qsIt"), DEFAULT)) == "oy-it-baseline"
    assert (
        assign_family(_enriched(("break",), ("y5",), _cells("qsMay", "qsUtter"), DEFAULT))
        == "may-utter-gains"
    )
    assert (
        assign_family(_enriched(("break",), ("y0",), _cells("qsUtter", "qsMay"), DEFAULT))
        == "may-utter-gains"
    )
    assert assign_family(_enriched(("break",), ("y5",), _cells("qsNo", "qsOy"), DEFAULT)) == "no-chain-gains"
    assert assign_family(_enriched(("y0",), ("y5",), _cells("qsIt", "qsNo"), DEFAULT)) == "no-chain-gains"


def test_seam_family_uses_the_ligature_trailing_component():
    """The seam is the left cell's EXIT (its trailing component for a ligature) joining the right cell's ENTRY. ·Tea·Oy·It joins ·It via the trailing ·Oy of the Tea+Oy ligature at the baseline, so it is an oy-it-baseline window, not a tea-it-xheight one — even though the ligature's lead is ·Tea."""
    cells = (*_cells("qsTea_qsOy"), *_cells("qsIt", "qsNo"))
    assert assign_family(_enriched(("break", "y0"), ("y0", "y5"), cells, DEFAULT)) == "oy-it-baseline"


def test_loss_and_cell_only_families():
    assert (
        assign_family(_enriched(("y0",), ("break",), _cells("qsNo", "qsTea"), DEFAULT))
        == "seam-loss-withdrawal"
    )
    assert (
        assign_family(_enriched(("y5",), ("y0",), _cells("qsUtter", "qsNo"), DEFAULT))
        == "seam-loss-withdrawal"
    )
    # No seam changed; the lead settled a different cell -> the extension-non-summing window.
    assert (
        assign_family(_enriched(("y0",), ("y0",), _cells("qsTea_qsOy", "qsDay"), DEFAULT))
        == "extension-non-summing"
    )


def test_unnamed_gain_is_misc_not_dropped():
    assert assign_family(_enriched(("y0",), ("y5",), _cells("qsPea", "qsDay"), DEFAULT)) == "unmatched-misc"


def test_stylistic_set_only_windows_defer():
    assert (
        assign_family(_enriched(("y0",), ("y5",), _cells("qsPea", "qsIt"), {"ss04": "UNMATCHED"}))
        == "deferred-ss04"
    )
    assert (
        assign_family(_enriched(("break",), ("break",), _cells("qsDay", "qsUtter"), {"ss10": "UNMATCHED"}))
        == "deferred-ss10"
    )
    assert (
        assign_family(_enriched(("y0",), ("y5",), _cells("qsUtter", "qsTea"), {"ss03": "UNMATCHED"}))
        == "deferred-ss03"
    )
    # A window UNMATCHED under default but blessed under ss03 is adjudicated on its default behavior, never deferred.
    split = {"default": "UNMATCHED", "ss03": "ss03-chain-join-gains"}
    assert (
        assign_family(_enriched(("break", "y0"), ("y5", "y0"), _cells("qsTea", "qsIt", "qsDay"), split))
        == "tea-it-xheight"
    )


@pytest.fixture(scope="module")
def assigned():
    rows = load_audit(AUDIT_PATH)
    by_triple: dict[tuple, list] = {}
    for row in rows:
        by_triple.setdefault((row.codepoints, row.baseline, row.new), []).append(row)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    enricher = Enricher(spec, SUBSETS, AFTER_FONT, repo_root=REPO_ROOT, before_font=BEFORE_FONT)
    out: list[str] = []
    for (codepoints, baseline, new), members in by_triple.items():
        if not any(member.matched_entry == "UNMATCHED" for member in members):
            continue
        config_classes = {member.config: member.matched_entry for member in members}
        ordered = tuple(sorted(members, key=lambda member: _config_index(member.config)))
        unit = Unit(
            codepoints=codepoints,
            baseline=baseline,
            new=new,
            class_id="UNMATCHED",
            rows=ordered,
            configs=tuple(member.config for member in ordered),
            kinds=tuple(sorted({kind for member in members for kind in member.kinds})),
            group=group_for(parse_codepoints(codepoints), dict(LETTERS)),
            render_groups=render_groups_for_rows(ordered),
            config_classes=config_classes,
        )
        out.append(assign_family(enricher.enrich(unit)))
    return out


def test_partition_is_total_and_matches_the_measured_census(assigned):
    census: dict[str, int] = {}
    for family in assigned:
        census[family] = census.get(family, 0) + 1
    assert sum(census.values()) == 1579, "every UNMATCHED window must land in exactly one family"
    assert census == MEASURED_CENSUS


def test_every_assigned_family_is_ordered_and_documented(assigned):
    for family in set(assigned):
        assert family in FAMILY_ORDER
        assert FAMILY_WHY[family]


def test_assignment_is_deterministic():
    rows = load_audit(AUDIT_PATH)
    sample = next((r.codepoints, r.baseline, r.new) for r in rows if r.matched_entry == "UNMATCHED")
    members = [r for r in rows if (r.codepoints, r.baseline, r.new) == sample]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    config_classes = {m.config: m.matched_entry for m in members}
    ordered = tuple(sorted(members, key=lambda m: _config_index(m.config)))
    unit = Unit(
        codepoints=sample[0],
        baseline=sample[1],
        new=sample[2],
        class_id="UNMATCHED",
        rows=ordered,
        configs=tuple(m.config for m in ordered),
        kinds=(),
        group=group_for(parse_codepoints(sample[0]), dict(LETTERS)),
        render_groups=render_groups_for_rows(ordered),
        config_classes=config_classes,
    )
    a = Enricher(spec, SUBSETS, AFTER_FONT, repo_root=REPO_ROOT, before_font=BEFORE_FONT)
    b = Enricher(spec, SUBSETS, AFTER_FONT, repo_root=REPO_ROOT, before_font=BEFORE_FONT)
    assert assign_family(a.enrich(unit)) == assign_family(b.enrich(unit))
