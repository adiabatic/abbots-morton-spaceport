"""Tests for the verdict-family grouper (rebuild/review/families.py): the seam-gain/seam-loss discriminator over hand-built enriched stubs, and the integration partition over the live UNMATCHED units at the name-grain (pre-merge) dedupe — deterministic and total, every window landing in exactly one family, with the stylistic-set-only windows deferred.

The partition itself comes from the surface build's census-facts.json sidecar, which records the family of every pre-merge UNMATCHED window: a deferred window's bucket is pure config logic over its pre-merge config classes, and every other one is its own fold survivor, so it carries the family phase 1 already computed. How many windows each family holds is the census's to report — the artifact cycle diffs it into rebuild/review-census-pins.json — so what is asserted here is totality and the sidecar's agreement with the families it ships. A stratified sample re-enriches windows from each family and re-runs `assign_family` on them, which is the continuous proof that the grain bookkeeping holds. The built surface then folds ink-duplicate siblings before families are assigned, which pulls the relabeled-only ss04 halves out of deferred-ss04 into their default families.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from rebuild.review import families
from rebuild.review.audit import load_workload
from rebuild.review.census import family_census, load_facts
from rebuild.review.enrich import LETTERS, Enricher, load_spec
from rebuild.review.families import FAMILY_ORDER, FAMILY_WHY, assign_family

REPO_ROOT = Path(__file__).resolve().parent.parent
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"


@dataclass(frozen=True)
class _StubUnit:
    config_classes: dict[str, str]
    configs: tuple[str, ...]


@dataclass(frozen=True)
class _StubEnriched:
    unit: _StubUnit
    before_seams: tuple[str, ...]
    after_seams: tuple[str, ...]
    after_cells: tuple[str, ...]


def _enriched(
    before: tuple[str, ...],
    after: tuple[str, ...],
    cells: tuple[str, ...],
    config_classes: dict[str, str],
) -> _StubEnriched:
    """A minimal stand-in carrying exactly the attributes assign_family reads, which is all its FamilyInput protocol asks for — no enrichment, no fonts, no audit rows."""
    return _StubEnriched(
        unit=_StubUnit(config_classes=dict(config_classes), configs=tuple(config_classes)),
        before_seams=before,
        after_seams=after,
        after_cells=cells,
    )


def _cells(*families_: str) -> tuple[str, ...]:
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
def facts(built_review_surface):
    out_dir, manifest = built_review_surface
    return load_facts(out_dir, manifest)


@pytest.fixture(scope="module")
def assigned(facts):
    return [family for _index, family in facts["premerge"]["families"]]


def test_partition_is_total_and_the_sidecar_reports_it(assigned, facts):
    """The partition is total — the census sums back to the windows it was taken over, so no window landed in two families or none — and the families group the sidecar ships is exactly that census, not a second tally the build kept alongside it."""
    census = family_census(assigned)
    assert sum(census.values()) == len(assigned), "every UNMATCHED window must land in exactly one family"
    assert facts["pins"]["volatile"]["families"] == {"census": census, "total": len(assigned)}


def test_every_assigned_family_is_ordered_and_documented(assigned):
    for family in set(assigned):
        assert family in FAMILY_ORDER
        assert FAMILY_WHY[family]


def test_families_cover_exactly_the_unmatched_premerge_units(facts, workload_index):
    """The sidecar's family records are indexed into the pre-merge unit list, so the indexes it carries must be precisely the UNMATCHED positions of that list — no matched window claiming a family, no UNMATCHED window left without one."""
    assert [index for index, _family in facts["premerge"]["families"]] == [
        index for index, unit in enumerate(workload_index.units) if unit.class_id == "UNMATCHED"
    ]


SAMPLE_PER_FAMILY = 4


def test_fresh_family_derivation_agrees_with_the_sidecar_over_a_sample(
    facts, workload_index, audit_windows, live_artifacts
):
    """The continuous proof of the grain bookkeeping: sample every family, enrich those windows from the fonts and the spec, and re-run the grouper. A deferred window's bucket has to re-derive from its own pre-merge config classes (the fold widens them, so a survivor's post-merge bucket can differ), and every non-deferred window has to reproduce the phase-1 family its own surviving object carries.

    Bounded at SAMPLE_PER_FAMILY windows apiece and loaded through a filtered pass over the audit: the property is that the derivation agrees, and a stride of a dozen per family witnessed that no better than four while dragging in the whole 451k-unit graph to do it.
    """
    by_family: dict[str, list[int]] = {}
    for index, family in facts["premerge"]["families"]:
        by_family.setdefault(family, []).append(index)
    sampled = {}
    for family, indexes in by_family.items():
        for index in indexes[:: max(1, len(indexes) // SAMPLE_PER_FAMILY)][:SAMPLE_PER_FAMILY]:
            entry = workload_index.units[index]
            sampled[(entry.codepoints, entry.configs[0])] = family
    workload = audit_windows({codepoints for codepoints, _config in sampled})
    units = {(unit.codepoints, unit.configs[0]): unit for unit in workload.units}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    enricher = Enricher(
        spec, live_artifacts.m1, live_artifacts.font, repo_root=REPO_ROOT, before_font=BEFORE_FONT
    )
    assert sampled
    for key, family in sampled.items():
        unit = units[key]
        assert unit.class_id == "UNMATCHED"
        assert assign_family(enricher.enrich(unit)) == family, unit.codepoints


def test_assignment_is_deterministic():
    """Two independently constructed Enrichers assign the same family to the same window. That is a property of the code, not of any window, so it runs over the frozen mini-M1 bundle: no live audit to scan for a sample, no live subset tables to parse, and the whole thing lands in the contracts lane."""
    mini = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
    workload = load_workload(mini / "audit.tsv", LEDGER_PATH, dict(LETTERS))
    unit = next(item for item in workload.units if item.class_id == "UNMATCHED")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    a = Enricher(spec, mini, mini / "M1.otf", repo_root=REPO_ROOT, before_font=BEFORE_FONT)
    b = Enricher(spec, mini, mini / "M1.otf", repo_root=REPO_ROOT, before_font=BEFORE_FONT)
    assert assign_family(a.enrich(unit)) == assign_family(b.enrich(unit))
