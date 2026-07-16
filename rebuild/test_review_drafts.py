"""Tests for the three verdict drafters against real M1 units: every drafted pin parses with the repo's actual data-expect parser and validates semantically against rebuild/out/m1/M1.otf, the seam-to-connector map is total over observed seams, policy drafts name only provenance that occurs in the unit's explain trace and carry schema-valid records, any-of candidates are individually parseable and pairwise distinct, and duplicate detection fires on a known corpus-pinned text."""

import warnings
from pathlib import Path

import pytest

from rebuild.review.audit import load_workload
from rebuild.review.drafts import (
    CONNECTORS,
    Drafter,
    _import_test_shaping,
    build_corpus_index,
    expect_string,
    features_dict,
    stylistic_set_value,
)
from rebuild.review.enrich import LETTERS, Enricher, load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
M1_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
AFTER_FONT = M1_DIR / "M1.otf"


@pytest.fixture(scope="module")
def workload():
    return load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))


@pytest.fixture(scope="module")
def enricher():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    return Enricher(spec, M1_DIR, AFTER_FONT)


@pytest.fixture(scope="module")
def drafter():
    return Drafter(AFTER_FONT)


@pytest.fixture(scope="module")
def enriched_units(enricher, workload):
    return [enricher.enrich(unit) for unit in workload.units]


def test_every_drafted_pin_passes_the_real_parser(drafter, enriched_units):
    ts = _import_test_shaping()
    for enriched in enriched_units:
        pin = drafter.draft_pin(enriched)
        assert pin.syntax == "pass", f"{enriched.unit.codepoints}: {pin.syntax}"
        tokens, connections = ts.parse_expect(pin.expect)
        assert len(connections) == len(tokens) - 1


def test_every_drafted_pin_validates_against_the_after_font(drafter, enriched_units):
    failures = [
        (enriched.unit.codepoints, pin.expect, pin.semantics_after_font)
        for enriched in enriched_units
        for pin in (drafter.draft_pin(enriched),)
        if pin.semantics_after_font != "pass"
    ]
    assert failures == []


def test_pins_are_whole_word_with_no_variant_assertions(drafter, enriched_units):
    ts = _import_test_shaping()
    for enriched in enriched_units[::100]:
        pin = drafter.draft_pin(enriched)
        tokens, _connections = ts.parse_expect(pin.expect)
        for token in tokens:
            assert token["variants"] == []
            assert token["neg_variants"] == []
            assert not token["exact_glyph"]


def test_connector_map_is_total_over_observed_seams(enriched_units):
    observed = {
        seam for enriched in enriched_units for seam in (*enriched.after_seams, *enriched.before_seams)
    }
    assert observed <= set(CONNECTORS)


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


def test_policy_drafts_name_only_trace_provenance_and_real_files(drafter, enriched_units):
    checked = 0
    for enriched in enriched_units[::50]:
        policy = drafter.draft_policy(enriched)
        if policy is None:
            continue
        checked += 1
        assert (REPO_ROOT / policy.file).exists(), policy.file
        assert set(policy.names_provenance) <= set(enriched.provenance)
        assert policy.schema_valid, (enriched.unit.codepoints, policy.suggested_record)
        assert policy.keypath in ("policy.refuse[+]", "policy.prefer[+]", "policy.contract[+]")
        assert enriched.unit.codepoints in policy.why_stub
    assert checked > 0


def test_policy_draft_prefers_contract_for_gained_extension(drafter, enricher, workload):
    unit = next(
        unit
        for unit in workload.units
        if unit.codepoints == "E652:E653:E67A:E652" and unit.class_id == "halves-entry-extension-restored"
    )
    policy = drafter.draft_policy(enricher.enrich(unit))
    assert policy is not None
    assert policy.keypath == "policy.contract[+]"
    assert policy.file == "glyph_data/runes/qsDay_qsUtter.yaml"
    assert "by: 1" in policy.suggested_record
    assert any("policy.extend" in pointer for pointer in policy.names_provenance)


def test_policy_draft_refuses_when_the_divergence_includes_a_new_join(drafter, enricher, workload):
    unit = next(
        unit
        for unit in workload.units
        if unit.codepoints == "E665:E670:E652:E679" and unit.class_id == "pre-ligature-cleanup-regularized"
    )
    policy = drafter.draft_policy(enricher.enrich(unit))
    assert policy is not None
    assert policy.keypath == "policy.refuse[+]"
    assert policy.file == "glyph_data/runes/qsMay.yaml"
    assert "exit: x-height" in policy.suggested_record
    assert "right: {family: [qsIt]}" in policy.suggested_record
    assert policy.schema_valid


def test_contract_drafts_never_ride_a_new_join(drafter, enriched_units):
    for enriched in enriched_units:
        policy = drafter.draft_policy(enriched)
        if policy is None or policy.keypath != "policy.contract[+]":
            continue
        position = drafter._policy_position(enriched)
        assert drafter._new_join_side(enriched, position) is None, enriched.unit.codepoints


def test_refuse_drafts_never_target_seam_identical_units(drafter, enriched_units):
    for enriched in enriched_units:
        policy = drafter.draft_policy(enriched)
        if policy is None or policy.keypath != "policy.refuse[+]":
            continue
        assert not drafter._seam_identical(enriched), enriched.unit.codepoints


def test_policy_draft_pins_baseline_cell_on_name_grain_divergence(drafter, enricher, workload):
    unit = next(
        unit
        for unit in workload.units
        if unit.codepoints == "E650:200C:E650:E665" and unit.class_id == "boundary-echo"
    )
    policy = drafter.draft_policy(enricher.enrich(unit))
    assert policy is not None
    assert policy.keypath == "policy.prefer[+]"
    assert "cell: {exit: none}" in policy.suggested_record
    assert "over: {exit: baseline}" in policy.suggested_record
    assert "mode: absolute" in policy.suggested_record
    assert policy.schema_valid


def test_policy_draft_declines_unexpressible_name_grain_divergence(drafter, enricher, workload):
    unit = next(
        unit
        for unit in workload.units
        if unit.codepoints == "E650:200C:E650:E670" and unit.class_id == "boundary-echo"
    )
    assert drafter.draft_policy(enricher.enrich(unit)) is None


def test_policy_draft_uses_prefer_when_provenance_is_empty(drafter, enricher, workload):
    unit = next(
        unit
        for unit in workload.units
        if unit.class_id == "bare-name-live-join" and not unit.codepoints.startswith("00B7")
    )
    enriched = enricher.enrich(unit)
    if enriched.provenance:
        pytest.skip("sampled bare-name unit unexpectedly carries provenance")
    policy = drafter.draft_policy(enriched)
    assert policy is not None
    assert policy.keypath == "policy.prefer[+]"


def test_policy_note_is_threaded_into_the_why_stub(drafter, enriched_units):
    enriched = enriched_units[0]
    policy = drafter.draft_policy(enriched, note="seam looks reached-for")
    assert policy is not None
    assert policy.why_stub.endswith("seam looks reached-for")


def test_any_of_candidates_parse_and_are_distinct(drafter, enriched_units):
    ts = _import_test_shaping()
    for enriched in enriched_units[::25]:
        draft = drafter.draft_any_of(enriched)
        assert len(set(draft.candidates)) == len(draft.candidates)
        for candidate in draft.candidates:
            ts.parse_expect(candidate)
        for token in draft.text.split(" "):
            assert token.startswith("qs") or token in ("space", "ZWNJ", "·")


def test_any_of_orders_after_behavior_first(drafter, enricher, workload):
    unit = next(unit for unit in workload.units if unit.class_id == "regrouping-floor-drift")
    enriched = enricher.enrich(unit)
    draft = drafter.draft_any_of(enriched)
    assert len(draft.candidates) == 2
    after_expect = expect_string(unit.codepoint_values, enriched.after_spans, enriched.after_seams)
    assert draft.candidates[0] == after_expect


def test_duplicate_detection_fires_on_a_known_pinned_text(enriched_units):
    enriched = enriched_units[0]
    text = "".join(chr(value) for value in enriched.unit.codepoint_values)
    token = enriched.unit.configs[0]
    index = {(text, token): {"source": "site/the-manual.html:123", "attribute": "data-expect"}}
    drafter = Drafter(AFTER_FONT, corpus_index=index)
    pin = drafter.draft_pin(enriched)
    assert pin.duplicate_of == "site/the-manual.html:123"
    assert pin.attribute == "data-expect"


def test_real_corpus_index_collects_manual_pins():
    index = build_corpus_index()
    assert len(index) > 500
    assert any(key[1] == "default" for key in index)
    assert any(record["attribute"] == "data-expect" for record in index.values())


def test_real_units_do_not_collide_with_corpus_pins(drafter, enriched_units):
    duplicates = [
        (enriched.unit.codepoints, pin.duplicate_of)
        for enriched in enriched_units[::100]
        for pin in (drafter.draft_pin(enriched),)
        if pin.duplicate_of is not None
    ]
    for _codepoints, source in duplicates:
        assert source.split(":")[0] in {
            "site/index.html",
            "site/the-manual.html",
            "site/extra-senior-words.html",
        }
