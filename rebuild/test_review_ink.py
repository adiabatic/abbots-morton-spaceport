"""Tests for the review surface's ink-identity comparison: the unified boolean (config_diff's identity sentinel, which implies the sorted-placed-pieces census reference) reproduces the census facts — u-0000 is ink-identical, and the verdict is deterministic across comparators.

The kern-neutral census over the live workload at the name-grain (pre-merge) dedupe comes from the surface build's census-facts.json sidecar, which reports one ink flag per pre-merge unit: the machine-approved units concentrated in the name-grain classes whose visible stragglers differ only in the old font's kerning, with the no-verdict exemptions (the boundary-echo blanket plus the two x-height-halves deletion forks) leaving the rest as human workload. The totals themselves are the census's to report — the artifact cycle diffs them into rebuild/review-census-pins.json — so what this module asserts is that the sidecar is consistent with the workload it was taken over: its own aggregate is what its own flags reduce to, and its flag string is indexed against the workload the digest identifies. Since the flags are derived rather than re-shaped, a sample here re-shapes them fresh — the whole-corpus stride plus a stratum drawn from the sibling windows, the fold-candidate population where a folded unit borrows its survivor's verdict. The built surface then folds those ink-duplicate sibling units (merge_ink_duplicate_units), so the shipped manifest's counts are smaller.

Also here: `delta_digest`, the persisted identity of one config's localized delta, whose shape check_unit enforces and whose recipe is a byte-identity contract with the digests recorded in rebuild/standing-approvals.yaml.
"""

import hashlib
import marshal
import shutil
from pathlib import Path

import pytest

from rebuild.review import census
from rebuild.review.audit import _sibling_windows
from rebuild.review.census import ink_group_from_flags, load_facts
from rebuild.review.ink import (
    IDENTITY_DIFF,
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
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
JUNIOR_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf"


@pytest.fixture(scope="module")
def comparator(live_artifacts):
    return InkComparator(BEFORE_FONT, live_artifacts.font)


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


def test_verdicts_are_deterministic_across_two_comparators(workload, comparator, live_artifacts):
    again = InkComparator(BEFORE_FONT, live_artifacts.font)
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


def test_the_retired_sorted_runs_formulation_agrees_over_a_corpus_sample(workload, live_artifacts):
    """The unification contract: ink_identical reads config_diff's identity sentinel, and the retired census formulation — sorted whole placed runs compared across fonts — must reach the same verdict on every (text, config). The sentinel implies run identity by construction (empty middles and no follower shift leave nothing moved), so the direction this samples is the converse: no window whose placed runs match may come back with a nonempty localized delta or a recorded shift. The full-corpus backstop is the census itself — a disagreement can only flip a unit identical→non-identical, so any escape moves machine_total in the pins diff a human reads at acceptance."""
    comparator = InkComparator(BEFORE_FONT, live_artifacts.font, shaper_for)
    disagreements = []
    for unit in workload.units[::200]:
        text = _text(unit)
        for config in unit.configs:
            features = features_for(config)
            retired = comparator.ink_pieces("before", text, features) == comparator.ink_pieces(
                "after", text, features
            )
            unified = comparator.config_diff(text, config) == IDENTITY_DIFF
            if retired is not unified:
                disagreements.append((unit.unit_id, config, retired, unified))
    assert disagreements == []


def test_delta_digest_is_a_d_prefixed_twelve_hex_token(comparator):
    """The shape a standing-approval rule matches on and check_unit validates: `d-` followed by exactly twelve lowercase hex digits, for a real localized delta and for the identity sentinel alike."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    for diff in (comparator.config_diff(pair, "default"), ((), (), 0), ((), (), -50)):
        digest = delta_digest(diff)
        assert len(digest) == 14
        assert digest.startswith("d-")
        assert all(character in "0123456789abcdef" for character in digest[2:])


def test_delta_digest_is_determined_by_the_tuple_alone(comparator, live_artifacts):
    """Equal tuples digest equally no matter which comparator, run, or process produced them — that is what lets a digest recorded in rebuild/standing-approvals.yaml keep matching across rebuilds. Asserted both against a freshly constructed comparator and against a hand-written copy of the tuple."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    diff = comparator.config_diff(pair, "default")
    again = InkComparator(BEFORE_FONT, live_artifacts.font)
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
    """((), (), 0) is IDENTITY_DIFF, the ink-identical sentinel the build declines to record, and both the constant's value and its digest are pinned here — a byte-identity contract, since changing the recipe orphans every digest already written into rebuild/standing-approvals.yaml. A nonzero shift is a different delta and digests apart even with empty middles."""
    assert IDENTITY_DIFF == ((), (), 0)
    assert delta_digest(((), (), 0)) == "d-f923c43ec75a"
    assert delta_digest(((), (), 1)) != delta_digest(((), (), 0))


def test_census_facts_sidecar_is_consistent_with_the_live_workload(built_review_surface, workload):
    """The kern-neutral census facts the rebatching rests on, at the name-grain (pre-merge) dedupe, as the surface build reports them: one ink flag per pre-merge unit, one flag per unit of the workload the digest identifies, and an ink group that is exactly what those flags reduce to — the machine-approved units, the no-verdict share of the rest exempt, and the human workload in its batches. Reducing the flags here rather than trusting the sidecar's own aggregate is what makes this a check of the records and not of a number the build wrote twice. Whole-corpus invariant: no UNMATCHED window is ink-identical, because each is a real new join under review."""
    out_dir, manifest = built_review_surface
    facts = load_facts(out_dir, manifest)
    assert facts["premerge"]["units"] == len(facts["premerge"]["ink_identical"])
    assert census.workload_digest(workload.units) == facts["premerge"]["workload_digest"]
    rows = [(unit.class_id, unit.no_verdict) for unit in workload.units]
    assert ink_group_from_flags(rows, facts["premerge"]["ink_identical"]) == facts["pins"]["volatile"]["ink"]
    for index, _family in facts["premerge"]["families"]:
        assert facts["premerge"]["ink_identical"][index] == "0"


def test_fresh_ink_derivation_agrees_with_the_sidecar_over_a_sample(
    built_review_surface, workload, comparator
):
    """The sidecar's flags are projected from the build's phase 1, not re-shaped, so something has to keep the projection honest against the fonts. Two strata do: a stride over the whole corpus, and a stride over the units of multi-sibling windows — the fold-candidate population, where a folded unit's flag is its survivor's rather than its own, which is exactly the grain the derivation must get right."""
    out_dir, manifest = built_review_surface
    facts = load_facts(out_dir, manifest)
    assert len(workload.units) == facts["premerge"]["units"]
    flags = facts["premerge"]["ink_identical"]
    position = {id(unit): index for index, unit in enumerate(workload.units)}
    sibling_indices = sorted(
        position[id(unit)] for siblings in _sibling_windows(workload.units).values() for unit in siblings
    )
    stride = max(1, len(sibling_indices) // 150)
    sample = sorted(set(range(0, len(workload.units), 1000)) | set(sibling_indices[::stride]))
    for index in sample:
        unit = workload.units[index]
        assert comparator.ink_identical(_text(unit), unit.configs) == (flags[index] == "1"), unit.unit_id


def test_signature_digest_is_determined_by_the_tuple_alone(comparator, live_artifacts):
    """Equal signatures digest equally across comparators and processes — what lets the persisted ink-signature store serve a digest recorded by a prior build — and different placed ink digests apart."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    digest = signature_digest(comparator.signature(pair, "default"))
    again = InkComparator(BEFORE_FONT, live_artifacts.font)
    assert signature_digest(again.signature(pair, "default")) == digest
    assert signature_digest(comparator.signature(pair[:1], "default")) != digest


def test_signature_digest_uses_alias_insensitive_marshal_v2():
    outline = (("lineTo", ((1, 2), (3, 4))),)
    shared = (outline, outline)
    reconstructed = (
        outline,
        tuple((operator, tuple((x, y) for x, y in points)) for operator, points in outline),
    )
    assert shared == reconstructed
    expected = hashlib.sha256(marshal.dumps(shared, 2)).hexdigest()
    assert signature_digest(shared) == expected
    assert signature_digest(reconstructed) == expected


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
def oracle(live_artifacts):
    return JuniorOracle(JUNIOR_FONT, BEFORE_FONT, live_artifacts.font)


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
