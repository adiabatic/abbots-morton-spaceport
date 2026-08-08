"""Tests for the review surface's ink-identity comparison: the proven census method (uharfbuzz shaping with kerning disabled, DecomposingRecordingPen outlines translated by cumulative advance plus offsets, pieces sorted and compared) reproduces the census facts — u-0000 is ink-identical, the verdict is deterministic, and the full kern-neutral histogram reproduces the machine-approved census over the live workload at the name-grain (pre-merge) dedupe, concentrated in the name-grain classes whose visible stragglers differ only in the old font's kerning, with the no-verdict exemptions (the boundary-echo blanket plus the two x-height-halves deletion forks) leaving the rest as human workload. Every count is pinned in rebuild/review-census-pins.json (the "ink" group). The built surface then folds ink-duplicate sibling units (merge_ink_duplicate_units), so the shipped manifest's counts are smaller — those are pinned in test_review_build. Also here: `delta_digest`, the persisted identity of one config's localized delta, whose shape check_unit enforces and whose recipe is a byte-identity contract with the digests recorded in rebuild/standing-approvals.yaml."""

import shutil
from pathlib import Path

import pytest

from rebuild.review.audit import load_workload
from rebuild.review.census import ink_histogram, load_pins
from rebuild.review.enrich import LETTERS
from rebuild.review.ink import (
    InkComparator,
    JuniorOracle,
    delta_digest,
    features_for,
    kern_neutral,
    shaper_for,
    signature_digest,
)
from rebuild.validation.shaping import Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
JUNIOR_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"

PINS = load_pins()


@pytest.fixture(scope="module")
def comparator():
    return InkComparator(BEFORE_FONT, AFTER_FONT)


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def test_features_for_config_tokens():
    assert features_for("default") == {}
    assert features_for(None) == {}
    assert features_for("ss03") == {"ss03": True}
    assert features_for("ss02+ss03+ss05") == {"ss02": True, "ss03": True, "ss05": True}


def test_kern_neutral_always_disables_kern():
    assert kern_neutral(None) == {"kern": False}
    assert kern_neutral({}) == {"kern": False}
    assert kern_neutral({"ss03": True}) == {"ss03": True, "kern": False}
    assert kern_neutral({"kern": True}) == {"kern": False}


def test_u_0126_is_ink_identical_only_because_kerning_is_neutralized(workload, comparator):
    """The worked kern-noise example: ◊ZWNJ ·May·Oy·Pea renders the same ink in both fonts once `kern` is off, and the old font really does kern it (positions move when the feature toggles), so the unit was a kern-only straggler before the census went kern-neutral."""
    unit = next(item for item in workload.units if item.codepoints == "200C:E665:E679:E650")
    assert comparator.ink_identical(_text(unit), unit.configs) is True
    before = Shaper(BEFORE_FONT)
    kerned = before.shape(_text(unit), {**features_for(unit.configs[0]), "kern": True})
    neutral = before.shape(_text(unit), kern_neutral(features_for(unit.configs[0])))
    assert kerned.names == neutral.names
    assert kerned.positions != neutral.positions


def test_u_0000_is_ink_identical(workload, comparator):
    unit = workload.units[0]
    assert unit.unit_id == "u-0000"
    assert unit.codepoints == "0020:E650:E650"
    assert comparator.ink_identical(_text(unit), unit.configs) is True


def test_verdicts_are_deterministic_across_two_comparators(workload, comparator):
    again = InkComparator(BEFORE_FONT, AFTER_FONT)
    sample = workload.units[::200]
    assert [comparator.ink_identical(_text(unit), unit.configs) for unit in sample] == [
        again.ink_identical(_text(unit), unit.configs) for unit in sample
    ]


def test_config_diff_localizes_the_delta_to_the_changed_region(comparator):
    """The worked flanking-context example from the may-baseline-entry-extension-dropped class: ·Pea·May drops ·May's one-pixel baseline entry extension, and followers appended after the judged pair add no ink to the delta — ·Low and ·Low·Fee render identically in their own frames and merely slide left by the dropped pixel's 50 units, so the localized delta is byte-identical across the follower contexts (one echo key, one visual question) and only the recorded shift distinguishes a window with followers from the bare pair, whose delta shows nothing sliding."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    one_follower = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667))
    two_followers = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667, 0xE658))
    diff_pair = comparator.config_diff(pair, "default")
    diff_one = comparator.config_diff(one_follower, "default")
    diff_two = comparator.config_diff(two_followers, "default")
    assert diff_two == diff_one
    assert diff_two[:2] == diff_pair[:2]
    assert diff_two[0] and diff_two[1]
    assert diff_pair[2] == 0
    assert diff_two[2] == -50


def test_config_diff_identity_sentinel_matches_ink_identical(workload, comparator):
    """The ink_identical flag build.py derives from config_diff stays exactly the census's absolute comparison: a diff of ((), (), 0) — empty middles and no follower shift — under every config is the same verdict ink_identical reaches by comparing whole placed runs."""
    unit = workload.units[0]
    assert unit.unit_id == "u-0000"
    assert all(comparator.config_diff(_text(unit), config) == ((), (), 0) for config in unit.configs)
    assert comparator.ink_identical(_text(unit), unit.configs) is True


def test_delta_digest_is_a_d_prefixed_twelve_hex_token(comparator):
    """The shape a standing-approval rule matches on and check_unit validates: `d-` followed by exactly twelve lowercase hex digits, for a real localized delta and for the identity sentinel alike."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    for diff in (comparator.config_diff(pair, "default"), ((), (), 0), ((), (), -50)):
        digest = delta_digest(diff)
        assert len(digest) == 14
        assert digest.startswith("d-")
        assert all(character in "0123456789abcdef" for character in digest[2:])


def test_delta_digest_is_determined_by_the_tuple_alone(comparator):
    """Equal tuples digest equally no matter which comparator, run, or process produced them — that is what lets a digest recorded in rebuild/standing-approvals.yaml keep matching across rebuilds. Asserted both against a freshly constructed comparator and against a hand-written copy of the tuple."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    diff = comparator.config_diff(pair, "default")
    again = InkComparator(BEFORE_FONT, AFTER_FONT)
    assert delta_digest(again.config_diff(pair, "default")) == delta_digest(diff)
    assert delta_digest((diff[0], diff[1], diff[2])) == delta_digest(diff)


def test_delta_digest_separates_the_deltas_config_diff_separates(comparator):
    """The digest carries exactly the distinctions the tuple carries, over the same worked ·Pea·May example as test_config_diff_localizes_the_delta_to_the_changed_region: the one- and two-follower windows share a tuple and so share a digest, while the bare pair — same ink appearing and disappearing, but nothing sliding — differs in the recorded shift and so digests apart."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    one_follower = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667))
    two_followers = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667, 0xE658))
    assert delta_digest(comparator.config_diff(one_follower, "default")) == delta_digest(
        comparator.config_diff(two_followers, "default")
    )
    assert delta_digest(comparator.config_diff(pair, "default")) != delta_digest(
        comparator.config_diff(one_follower, "default")
    )


def test_the_identity_diff_digests_to_a_pinned_constant():
    """((), (), 0) is the ink-identical sentinel the build declines to record, and its digest is the fixed token pinned here — a byte-identity contract, since changing the recipe orphans every digest already written into rebuild/standing-approvals.yaml. A nonzero shift is a different delta and digests apart even with empty middles."""
    assert delta_digest(((), (), 0)) == "d-f923c43ec75a"
    assert delta_digest(((), (), 1)) != delta_digest(((), (), 0))


def test_full_histogram_reproduces_the_census(comparator):
    """The kern-neutral census facts the rebatching rests on over the live workload at the name-grain (pre-merge) dedupe: the machine-approved units are ink-identical under every config in their sets, concentrated in the name-grain classes (boundary-echo, dangling-anchor-dropped, bare-name-live-join) whose visible difference is only the old font's kerning; the no-verdict share of the non-identical units — the boundary-echo blanket plus the two x-height-halves deletion forks — is exempt, leaving the human workload in its batches. Every count is pinned in rebuild/review-census-pins.json. No verdict family (the UNMATCHED windows) is ink-identical: each is a real new join under review. The built surface additionally folds ink-duplicate siblings; its smaller counts are pinned in test_review_build.

    This test loads its own workload rather than taking the shared session fixture: `ink_histogram` writes its verdicts (`ink_identical`, `batch`) into the units it censuses, and those writes are the very state asserted below — on the shared graph they would leak into every later test in the worker.
    """
    workload = load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))
    pins = PINS["ink"]
    stats = ink_histogram(workload, comparator)
    assert stats["machine_total"] == pins["machine_total"]
    assert stats["non_identical"] == pins["non_identical"]
    assert stats["by_class"] == pins["by_class"]
    assert not any(unit.class_id == "UNMATCHED" and unit.ink_identical for unit in workload.units)

    assert stats["batches"] == pins["batches"]
    assert stats["boundary_echo_exempt"] == pins["boundary_echo_exempt"]
    assert stats["human_units"] == pins["human_units"]
    human = [unit for unit in workload.units if not unit.ink_identical and not unit.no_verdict]
    assert [unit.batch for unit in human] == [index // 300 for index in range(len(human))]
    assert all(unit.batch is None for unit in workload.units if unit.ink_identical or unit.no_verdict)


def test_signature_digest_is_determined_by_the_tuple_alone(comparator):
    """Equal signatures digest equally across comparators and processes — what lets the persisted ink-signature store serve a digest recorded by a prior build — and different placed ink digests apart."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    digest = signature_digest(comparator.signature(pair, "default"))
    again = InkComparator(BEFORE_FONT, AFTER_FONT)
    assert signature_digest(again.signature(pair, "default")) == digest
    assert signature_digest(comparator.signature(pair[:1], "default")) != digest


def test_shaper_for_shares_one_memoized_shaper_per_font():
    """The surface build's shared shaper: one instance per font per process, and its memoized `shape` returns exactly what a plain Shaper returns, with the features dict canonicalized so {} and None — and any key order — land on one memo entry."""
    shared = shaper_for(BEFORE_FONT)
    assert shaper_for(BEFORE_FONT) is shared
    plain = Shaper(BEFORE_FONT)
    text = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667))
    features = {"ss03": True, "kern": False}
    assert shared.shape(text, features) == plain.shape(text, features)
    assert shared.shape(text, {"kern": False, "ss03": True}) is shared.shape(text, features)
    assert shared.shape(text) == plain.shape(text)
    assert shared.shape(text, {}) is shared.shape(text)


def test_shaper_for_rekeys_when_the_font_changes_on_disk(tmp_path):
    """A font rewritten in place — a test building surfaces over different mini fonts at one path — must never serve stale shapes: the registry keys on the file's identity, not its path alone."""
    target = tmp_path / "font.otf"
    shutil.copyfile(BEFORE_FONT, target)
    first = shaper_for(target)
    shutil.copyfile(JUNIOR_FONT, target)
    second = shaper_for(target)
    assert second is not first


@pytest.fixture(scope="module")
def oracle():
    return JuniorOracle(JUNIOR_FONT, BEFORE_FONT, AFTER_FONT)


def test_junior_tracking_premise_holds(oracle):
    """The oracle's founding premise, verified at construction and pinned here: Junior carries the same isolated letterforms as Senior plus exactly one pixel (50 units at upem 550) of extra advance on every Quikscript glyph, and no advance difference anywhere else."""
    assert oracle.tracking == 50


def test_junior_oracle_approves_a_suppressed_ligature_unit(oracle):
    """The ·No·Day·Utter·Utter window (divergent only under ss10 because the old font still formed the ·Day·Utter ligature there): the rebuild's ss10 rendering is Junior's isolated rendering minus the tracking, so the unit is machine-approvable."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("ss10",), text) is True


def test_junior_oracle_only_judges_ss10_only_units(oracle):
    """The oracle's ruling covers exactly the units whose entire divergence is under ss10; a unit also divergent under any other config still needs its other legs judged, so the oracle abstains regardless of the ink."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("default",), text) is False
    assert oracle.approves(("default", "ss10"), text) is False
    assert oracle.approves((), text) is False


def test_junior_oracle_refuses_the_lowered_namer_dot(oracle):
    """The known counterexample (the `· ◊ZWNJ ·X·Y` boundary windows): Junior renders the namer dot lowered (periodcentered.lowered) where the rebuild's ss10 run draws the plain dot, so the placed ink differs and the oracle correctly leaves the unit for human eyes."""
    text = "".join(chr(value) for value in (0x00B7, 0x200C, 0xE666, 0xE653))
    assert oracle.approves(("ss10",), text) is False
