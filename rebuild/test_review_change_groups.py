"""Tests for the change-group assigner (rebuild/review/change_groups.py): one hand-built stub per group, the rules' order (the set gate's tests, a new join before a dropped join before a height move, the new join's height, the width), the unexplained-config rule, the raise on an unreadable junction token, which units re-route out of an `ink_identical` class, the prose file's ids, and the determinism of an assignment over the frozen mini bundle.

The partition of the live corpus is checked elsewhere. `facts.derive_premerge` records one change group for each pre-merge unit that takes one, and the review facts report how many windows each group holds, which the artifact cycle diffs into rebuild/review-facts-pins.json.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from rebuild.review.audit import NO_DELTAS, load_workload
from rebuild.review.change_groups import (
    CHANGE_GROUP_ORDER,
    ChangeGroupInput,
    assign_change_group,
    load_change_group_prose,
    reroutes,
)
from rebuild.review.enrich import LETTERS, Enricher, load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

UNMATCHED = "UNMATCHED"
IDENTICAL = "identical-variant-name"
DEFAULT = {"default": UNMATCHED}


@dataclass(frozen=True)
class _StubUnit:
    config_classes: dict[str, str]
    configs: tuple[str, ...]


@dataclass(frozen=True)
class _StubEnriched:
    unit: _StubUnit
    before_junctions: tuple[str, ...]
    after_junctions: tuple[str, ...]
    after_cells: tuple[str, ...]
    highlight_before: dict
    highlight_after: dict


def _enriched(
    before: tuple[str, ...] = ("y0",),
    after: tuple[str, ...] = ("y0",),
    *,
    classes: dict[str, str] = DEFAULT,
    configs: tuple[str, ...] | None = None,
    cells: int | None = None,
    width: tuple[int, int] = (100, 100),
) -> _StubEnriched:
    """Return a stub with only the attributes the `ChangeGroupInput` protocol asks for: the configs `classes` maps unless `configs` is given, `cells` after cells (one more than the after junctions by default), and `width` as the before and after advance totals."""
    count = len(after) + 1 if cells is None else cells
    return _StubEnriched(
        unit=_StubUnit(config_classes=dict(classes), configs=tuple(classes) if configs is None else configs),
        before_junctions=before,
        after_junctions=after,
        after_cells=tuple(f"qsPea/stance-{index}/None/None/" for index in range(count)),
        highlight_before={"x_min": 0, "x_max": 1, "advance_total": width[0]},
        highlight_after={"x_min": 0, "x_max": 1, "advance_total": width[1]},
    )


def _group(enriched: ChangeGroupInput, deltas=NO_DELTAS, identical=frozenset({IDENTICAL})) -> str:
    return assign_change_group(enriched, deltas, identical)


ONE_STUB_PER_GROUP = {
    "new-join-at-baseline": _enriched(("break", "y5"), ("y0", "y0")),
    "new-join-at-xheight-or-top": _enriched(("y5", "break"), ("break", "y5")),
    "join-dropped": _enriched(("y5", "y5"), ("y0", "break")),
    "join-lowered": _enriched(("y5",), ("y0",)),
    "join-raised": _enriched(("y0",), ("y5",)),
    "ligature-forms-or-splits": _enriched(("y0", "y0"), ("y0",), cells=2),
    "same-joins-narrower": _enriched(width=(100, 90)),
    "same-joins-wider": _enriched(width=(90, 100)),
    "same-joins-same-width": _enriched(width=(100, 100)),
    "only-with-ss03": _enriched(("break",), ("y0",), classes={"ss03": UNMATCHED, "ss03+ss05": UNMATCHED}),
    "only-with-ss04": _enriched(("break",), ("y0",), classes={"ss04": UNMATCHED}),
    "only-with-ss05": _enriched(("break",), ("y0",), classes={"ss05": UNMATCHED, "ss03+ss05": UNMATCHED}),
    "only-with-ss10": _enriched(classes={"ss10": UNMATCHED}),
    "only-with-several-sets": _enriched(("break",), ("y0",), classes={"ss03": UNMATCHED, "ss05": UNMATCHED}),
}


def test_one_stub_reaches_each_group():
    assert tuple(ONE_STUB_PER_GROUP) == CHANGE_GROUP_ORDER
    for group, enriched in ONE_STUB_PER_GROUP.items():
        assert _group(enriched) == group


def test_the_prose_file_names_every_group_in_order(tmp_path):
    prose = load_change_group_prose()
    assert tuple(prose) == CHANGE_GROUP_ORDER
    assert all(entry.title and entry.summary and entry.why for entry in prose.values())
    shortened = tmp_path / "change-groups.yaml"
    shortened.write_text("- {id: join-dropped, title: T, summary: S, why: W}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CHANGE_GROUP_ORDER"):
        load_change_group_prose(shortened)


def test_a_new_join_decides_before_a_dropped_join_and_a_dropped_join_before_a_height_move():
    """Kind priority, not position, picks the deciding junction: a new join anywhere in the window outranks a dropped or moved join to its left, and a dropped join outranks a height move to its left."""
    assert _group(_enriched(("y0", "y5", "break"), ("break", "y0", "y0"))) == "new-join-at-baseline"
    assert _group(_enriched(("y5", "y0"), ("y0", "break"))) == "join-dropped"


def test_the_leftmost_new_join_decides_its_height_and_the_leftmost_move_its_direction():
    assert _group(_enriched(("break", "break"), ("y0", "y5"))) == "new-join-at-baseline"
    assert _group(_enriched(("break", "break"), ("y8", "y0"))) == "new-join-at-xheight-or-top"
    assert _group(_enriched(("y5", "y0"), ("y0", "y5"))) == "join-lowered"
    assert _group(_enriched(("y0", "y5"), ("y5", "y0"))) == "join-raised"


def test_the_set_gate_runs_first_and_tests_ss04_ss10_ss03_ss05_in_order():
    """Each test asks whether the set is on in every unexplained config, in the order ss04, ss10, ss03, ss05. A default config among the unexplained ones passes the gate, whatever sets the other configs turn on, and the junctions decide."""
    assert _group(_enriched(classes={"ss04+ss10": UNMATCHED, "ss10": UNMATCHED})) == "only-with-ss10"
    assert _group(_enriched(classes={"ss04+ss10": UNMATCHED})) == "only-with-ss04"
    assert _group(_enriched(classes={"ss03+ss05": UNMATCHED})) == "only-with-ss03"
    assert _group(_enriched(classes={"ss03": UNMATCHED, "ss10": UNMATCHED})) == "only-with-several-sets"
    blessed = {"default": UNMATCHED, "ss03": "ss03-adds-joins-around-tea"}
    assert _group(_enriched(("break",), ("y0",), classes=blessed)) == "new-join-at-baseline"
    assert _group(_enriched(("break",), ("y0",), classes={"ss03": UNMATCHED, "default": UNMATCHED})) == (
        "new-join-at-baseline"
    )


def test_an_identical_class_explains_a_config_only_where_its_ink_is_unchanged():
    """A config mapped to an `ink_identical` class is unexplained exactly when its ink changed, so a re-routed unit, whose every config maps to its identical class and changed, has every config unexplained. A unit with no per-config classes has every config unexplained, and a unit whose every config is explained has no group."""
    mixed = _enriched(("break",), ("y0",), classes={"default": IDENTICAL, "ss03": UNMATCHED})
    assert _group(mixed, deltas={"ss03": "d-000000000001"}) == "only-with-ss03"
    assert _group(mixed, deltas={"default": "d-000000000001", "ss03": "d-000000000001"}) == (
        "new-join-at-baseline"
    )
    rerouted = _enriched(width=(100, 99), classes={"default": IDENTICAL, "ss04": IDENTICAL})
    changed = {"default": "d-000000000001", "ss04": "d-000000000001"}
    assert _group(rerouted, deltas=changed) == "same-joins-narrower"
    assert _group(_enriched(width=(100, 99), classes={}, configs=("default",))) == "same-joins-narrower"
    with pytest.raises(ValueError, match="no ledger ruling explains"):
        _group(rerouted)


def test_a_changed_junction_token_outside_break_and_yn_raises():
    with pytest.raises(ValueError, match="break or yN"):
        _group(_enriched(("absent",), ("y0",)))
    assert _group(_enriched(("absent", "y5"), ("absent", "y0"))) == "join-lowered"


def test_only_a_human_unit_of_an_identical_class_that_takes_verdicts_re_routes():
    """A unit re-routes when its class is an `ink_identical` ledger class, the class does not exempt it, and no machine check approves it. An UNMATCHED unit takes a change group without re-routing, since it has no class to leave."""
    identical = frozenset({IDENTICAL})
    assert reroutes(IDENTICAL, False, False, identical)
    assert not reroutes(IDENTICAL, False, True, identical)
    assert not reroutes(IDENTICAL, True, False, identical)
    assert not reroutes("join-switches-sides", False, False, identical)
    assert not reroutes(UNMATCHED, False, False, identical)


def test_assignment_is_deterministic(mini_bundle):
    """Two independently constructed Enrichers assign the same change group to the same window. The property belongs to the code, so the test runs over the frozen mini-M1 bundle instead of the live audit and subset tables."""
    mini = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
    workload = load_workload(mini / "audit.tsv", mini_bundle.ledger, dict(LETTERS))
    unit = next(item for item in workload.units() if item.class_id == UNMATCHED)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    pack = mini_bundle.subset_pack
    a = Enricher(spec, mini, mini / "M1.otf", repo_root=REPO_ROOT, before_font=BEFORE_FONT, subset_pack=pack)
    b = Enricher(spec, mini, mini / "M1.otf", repo_root=REPO_ROOT, before_font=BEFORE_FONT, subset_pack=pack)
    identical = frozenset(entry.id for entry in workload.ledger if entry.ink_identical)
    assert _group(a.enrich(unit), identical=identical) == _group(b.enrich(unit), identical=identical)
