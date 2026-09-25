"""Tests for the verdict-family grouper (rebuild/review/families.py): the seam-gain and seam-loss branches of `assign_family` over hand-built stubs, and the determinism of an assignment over the frozen mini bundle.

The partition of the live corpus is checked elsewhere. `census.derive_premerge` records one family for each UNMATCHED row of the pre-merge list and raises on a row that resolves to no family. The census reports how many windows each family holds, and the artifact cycle diffs those counts into rebuild/review-census-pins.json.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

from rebuild.review.audit import load_workload
from rebuild.review.enrich import LETTERS, Enricher, load_spec
from rebuild.review.families import FAMILY_ORDER, FAMILY_WHY, assign_family

REPO_ROOT = Path(__file__).resolve().parent.parent
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"


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
    """Return a stub with only the attributes the `FamilyInput` protocol asks for, which are all that `assign_family` reads."""
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
    """A seam is named by the left cell's exit, which for a ligature is its trailing component, and the right cell's entry. In `·Tea+Oy ~b~ ·It` the ligature joins ·It through its trailing ·Oy, so the window is in oy-it-baseline even though the ligature's lead is ·Tea."""
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
    # No seam changed, so the window is in extension-non-summing.
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


def test_assignment_is_deterministic(mini_bundle):
    """Two independently constructed Enrichers assign the same family to the same window. The property belongs to the code, so the test runs over the frozen mini-M1 bundle instead of the live audit and subset tables."""
    mini = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
    workload = load_workload(mini / "audit.tsv", mini_bundle.ledger, dict(LETTERS))
    unit = next(item for item in workload.units() if item.class_id == "UNMATCHED")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    pack = mini_bundle.subset_pack
    a = Enricher(spec, mini, mini / "M1.otf", repo_root=REPO_ROOT, before_font=BEFORE_FONT, subset_pack=pack)
    b = Enricher(spec, mini, mini / "M1.otf", repo_root=REPO_ROOT, before_font=BEFORE_FONT, subset_pack=pack)
    assert assign_family(a.enrich(unit)) == assign_family(b.enrich(unit))
