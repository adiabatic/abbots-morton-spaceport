"""Tests for the three verdict drafters against real M1 units: the semantic validator's teeth, the policy drafter's branch table on worked-example windows (contract for a gained extension, refuse for a new join, prefer on a name-grain divergence and on an empty trace, and the decline), any-of candidate ordering, and duplicate detection against a synthetic corpus index.

No whole-corpus sweep lives here: what every drafted pin and policy record must satisfy — the pin parses and replays "pass", the record is schema-valid, the any-of candidates are distinct and parseable — the drafter refuses to produce anything else, raising `DraftError` where the draft is made rather than recording a failure for `check_unit` to reject downstream, so no sweep of shipped fragments could witness a violation that exists. (A machine-approved or verdict-exempt unit is never drafted at all and its fragment omits `drafts`; that a record names only trace provenance in a real file is `check_unit`'s and `check_shards`'.) The worked examples take their windows from `example_units`, a filtered load of the frozen mini bundle's audit, and shape them in the bundle's own font — how the drafter words a record is a property of the drafter, so nothing here reaches the live corpus.
"""

import warnings
from pathlib import Path

import pytest

from rebuild.review.drafts import (
    Drafter,
    _import_test_shaping,
    build_corpus_index,
    expect_string,
    features_dict,
    stylistic_set_value,
)
from rebuild.review.enrich import Enricher, load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_FONT = MINI / "M1.otf"


@pytest.fixture(scope="module")
def enricher(mini_bundle):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    return Enricher(spec, MINI, MINI_FONT, repo_root=REPO_ROOT)


@pytest.fixture(scope="module")
def drafter():
    return Drafter(MINI_FONT, repo_root=REPO_ROOT)


def test_pins_are_whole_word_with_no_variant_assertions():
    """A drafted pin asserts the word and its joins, never which stance a letter took: `expect_string` emits bare letter names, so no token it produces can carry a variant, a negated variant, or an exact-glyph assertion. Asserted over the emitter rather than over a corpus of drafts, because it is the emitter's property — a sweep of 451k units restated it 451k times."""
    ts = _import_test_shaping()
    cases = (
        ((0x200C, 0xE652, 0xE679), ((0, 1), (1, 3)), ("break",)),
        ((0x00B7, 0xE650), ((0, 1), (1, 2)), ("break",)),
        ((0xE652, 0xE670), ((0, 1), (1, 2)), ("y5",)),
        ((0xE650, 0xE665, 0xE667), ((0, 1), (1, 2), (2, 3)), ("y0", "y0")),
        ((0xE650, 0x0020, 0xE650), ((0, 1), (1, 2), (2, 3)), ("break", "break")),
    )
    for values, spans, seams in cases:
        tokens, _connections = ts.parse_expect(expect_string(values, spans, seams))
        for token in tokens:
            assert token["variants"] == []
            assert token["neg_variants"] == []
            assert not token["exact_glyph"]


def test_semantic_validation_rejects_a_wrong_pin(drafter):
    status = drafter.validate_semantics("", "·It ~x~ ·It", None)
    assert status.startswith("fail")
    assert drafter.validate_semantics("", "·It | ·It", None) == "pass"


def test_stylistic_set_value_and_features():
    assert stylistic_set_value(("default", "ss02")) is None
    assert stylistic_set_value(("ss03", "ss02+ss03")) == "03"
    assert stylistic_set_value(("ss02+ss03+ss05",)) == "02 03 05"
    assert features_dict(("ss02+ss03",)) == {"ss02": True, "ss03": True}
    assert features_dict(("default",)) == {}


def test_expect_string_handles_boundaries_and_ligatures():
    values = (0x200C, 0xE652, 0xE679)
    spans = ((0, 1), (1, 3))
    assert expect_string(values, spans, ("break",)) == "◊ZWNJ | ·Tea+Oy"
    values = (0x00B7, 0xE650)
    spans = ((0, 1), (1, 2))
    assert expect_string(values, spans, ("break",)) == "\\· | ·Pea"


def test_policy_draft_prefers_contract_for_gained_extension(drafter, enricher, example_units):
    unit = example_units[("E652:E653:E67A:E652", "ss03")]
    assert unit.class_id == "halves-entry-extension-restored"
    policy = drafter.draft_policy(enricher.enrich(unit))
    assert policy is not None
    assert policy.keypath == "policy.contract[+]"
    assert policy.file == "glyph_data/runes/qsDay_qsUtter.yaml"
    assert "by: 1" in policy.suggested_record
    assert any("policy.extend" in pointer for pointer in policy.names_provenance)


def test_policy_draft_refuses_when_the_divergence_includes_a_new_join(drafter, enricher, example_units):
    unit = example_units[("E665:E670:E652:E679", "default")]
    assert unit.class_id == "pre-ligature-cleanup-regularized"
    policy = drafter.draft_policy(enricher.enrich(unit))
    assert policy is not None
    assert policy.keypath == "policy.refuse[+]"
    assert policy.file == "glyph_data/runes/qsMay.yaml"
    assert "exit: x-height" in policy.suggested_record
    assert "right: {family: [qsIt]}" in policy.suggested_record
    assert policy.schema_valid


def test_refuse_drafts_never_target_seam_identical_units(drafter, enricher, example_units):
    """A refuse draft says "do not take this new join", so it must never land on a unit whose seams did not move. Branch 4 of `draft_policy` implies it; the new-join branch does not, and this is the witness that its glyph-index seam equality and the codepoint-gap lookup cannot disagree. Sampled over the worked-example windows rather than swept over the corpus — the property is about the two derivations agreeing, not about how many units agree."""
    for unit in example_units.values():
        enriched = enricher.enrich(unit)
        policy = drafter.draft_policy(enriched)
        if policy is None or policy.keypath != "policy.refuse[+]":
            continue
        assert not drafter._seam_identical(enriched), enriched.unit.codepoints


def test_policy_draft_pins_baseline_cell_on_name_grain_divergence(drafter, enricher, example_units):
    unit = example_units[("E650:200C:E650:E665", "default")]
    assert unit.class_id == "boundary-echo"
    policy = drafter.draft_policy(enricher.enrich(unit))
    assert policy is not None
    assert policy.keypath == "policy.prefer[+]"
    assert "cell: {exit: none}" in policy.suggested_record
    assert "over: {exit: baseline}" in policy.suggested_record
    assert "mode: absolute" in policy.suggested_record
    assert policy.schema_valid


def test_policy_draft_declines_unexpressible_name_grain_divergence(drafter, enricher, example_units):
    unit = example_units[("E650:200C:E650:E670", "default")]
    assert unit.class_id == "boundary-echo"
    assert drafter.draft_policy(enricher.enrich(unit)) is None


def test_policy_draft_uses_prefer_when_provenance_is_empty(drafter, enricher, example_units):
    """The bare-name ·Fee·No window: a live join the runes never spoke about, so the trace names no record and `draft_policy` falls to its prefer branch. Pinned to one window rather than sampled from the class, so a window that grows provenance fails here loudly instead of skipping the branch and reporting green."""
    unit = example_units[("E658:E666", "default")]
    assert unit.class_id == "bare-name-live-join"
    enriched = enricher.enrich(unit)
    assert enriched.provenance == ()
    policy = drafter.draft_policy(enriched)
    assert policy is not None
    assert policy.keypath == "policy.prefer[+]"


def test_policy_note_is_threaded_into_the_why_stub(drafter, enricher, example_units):
    enriched = enricher.enrich(example_units[("E650:200C:E650:E665", "default")])
    policy = drafter.draft_policy(enriched, note="seam looks reached-for")
    assert policy is not None
    assert policy.why_stub.endswith("seam looks reached-for")


def test_any_of_orders_after_behavior_first(drafter, enricher, example_units):
    unit = example_units[("E650:E670:E65D", "default")]
    assert unit.class_id == "regrouping-floor-drift"
    enriched = enricher.enrich(unit)
    draft = drafter.draft_any_of(enriched)
    assert len(draft.candidates) == 2
    after_expect = expect_string(unit.codepoint_values, enriched.after_spans, enriched.after_seams)
    assert draft.candidates[0] == after_expect


def test_duplicate_detection_fires_on_a_known_pinned_text(enricher, example_units):
    enriched = enricher.enrich(example_units[("E652:E670", "default")])
    text = "".join(chr(value) for value in enriched.unit.codepoint_values)
    token = enriched.unit.configs[0]
    index = {(text, token): {"source": "site/the-manual.html:123", "attribute": "data-expect"}}
    drafter = Drafter(MINI_FONT, corpus_index=index)
    pin = drafter.draft_pin(enriched)
    assert pin.duplicate_of == "site/the-manual.html:123"
    assert pin.attribute == "data-expect"


def test_real_corpus_index_collects_manual_pins():
    index = build_corpus_index()
    assert len(index) > 500
    assert any(key[1] == "default" for key in index)
    assert any(record["attribute"] == "data-expect" for record in index.values())
