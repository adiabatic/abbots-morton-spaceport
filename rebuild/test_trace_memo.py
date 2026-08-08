"""The persisted trace memo (issue 25): a rebuild over unchanged runes serves every kernel call from the previous build's store and rewrites it byte for byte; a single moved rune digest re-traces only the entries that could feel it; and a served build's tables — `cited_provenance` above all, since the dead-policy gate reads it and nothing else backstops it — are indistinguishable from a fresh build's. The cross-configuration share (issue 15) rides the same key discipline: a recipient configuration served from the default build's memo must be indistinguishable from a full per-config build, and only keys no named rune of which can feel the feature delta may be served at all."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from rebuild.pipeline import fixtures, trace_memo
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import PolicyRecord
from rebuild.pipeline.settle import EDGE, UNKNOWN, RightToken

SPEC = fixtures.mini_spec()
DIGESTS = {name: f"digest-{name}" for name in SPEC.runes}


def _build(tmp, digests=DIGESTS, environment="env", fresh=False):
    store = trace_memo.open_store(
        trace_memo.store_path(tmp, "default"), SPEC, digests, environment, "default", fresh=fresh
    )
    decision, treaty = table_module.build_tables(SPEC, frozenset(), trace_store=store)
    return store, decision, treaty


def _outcomes(decision):
    return [(row.key, row.outcome) for row in decision.transitions]


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("memo")
    path = trace_memo.store_path(tmp, "default")
    fresh_store, fresh_decision, fresh_treaty = _build(tmp)
    fresh_bytes = path.read_bytes()
    warm_store, warm_decision, warm_treaty = _build(tmp)
    warm_bytes = path.read_bytes()
    return SimpleNamespace(
        tmp=tmp,
        path=path,
        fresh_store=fresh_store,
        fresh_decision=fresh_decision,
        fresh_treaty=fresh_treaty,
        fresh_bytes=fresh_bytes,
        warm_store=warm_store,
        warm_decision=warm_decision,
        warm_treaty=warm_treaty,
        warm_bytes=warm_bytes,
    )


class TestNoEditRebuild:
    def test_a_fresh_build_serves_nothing_and_persists_the_pile(self, builds):
        assert builds.fresh_store.served == 0
        assert builds.fresh_store.saved > 0
        assert builds.path.exists()

    def test_a_no_edit_rebuild_serves_from_the_store(self, builds):
        assert builds.warm_store.served > 0

    def test_the_served_build_is_indistinguishable_from_the_fresh_one(self, builds):
        assert _outcomes(builds.warm_decision) == _outcomes(builds.fresh_decision)
        assert builds.warm_decision.rules == builds.fresh_decision.rules
        assert builds.warm_treaty.rows == builds.fresh_treaty.rows
        assert table_module.windows_digest(builds.warm_decision) == table_module.windows_digest(
            builds.fresh_decision
        )

    def test_served_entries_fill_cited_provenance_exactly_as_a_recomputation_would(self, builds):
        # The dead-policy gate reads cited_provenance; a memo that served traces without replaying their fired deltas would read live records as dead here.
        assert builds.warm_decision.cited_provenance == builds.fresh_decision.cited_provenance

    def test_the_rewritten_store_is_byte_identical(self, builds):
        # A served parent short-circuits the nested calls that would have consulted its children, so byte-identity proves the rewrite carries unconsulted-but-valid entries forward rather than shedding them.
        assert builds.warm_bytes == builds.fresh_bytes


class TestInvalidation:
    def test_a_moved_rune_digest_retraces_only_what_could_feel_it(self, builds):
        moved = dict(DIGESTS)
        moved[sorted(SPEC.runes)[0]] = "digest-moved"
        store, decision, treaty = _build(builds.tmp, digests=moved)
        assert 0 < store.served < store.saved
        assert _outcomes(decision) == _outcomes(builds.fresh_decision)
        assert decision.rules == builds.fresh_decision.rules
        assert decision.cited_provenance == builds.fresh_decision.cited_provenance
        assert treaty.rows == builds.fresh_treaty.rows

    def test_a_changed_environment_serves_nothing(self, builds):
        store, _decision, _treaty = _build(builds.tmp, environment="other-env")
        assert store.served == 0

    def test_a_changed_spec_structure_loads_nothing(self, builds):
        smaller = replace(SPEC, runes={name: SPEC.runes[name] for name in list(SPEC.runes)[:-1]})
        store = trace_memo.open_store(builds.path, smaller, DIGESTS, "env", "default")
        assert store.loaded == 0

    def test_fresh_skips_the_read_but_still_rewrites(self, builds):
        store, _decision, _treaty = _build(builds.tmp, fresh=True)
        assert store.loaded == 0
        assert store.served == 0
        assert store.saved > 0
        assert builds.path.read_bytes() == builds.fresh_bytes

    def test_a_corrupt_store_loads_nothing_and_the_build_proceeds(self, tmp_path):
        trace_memo.store_path(tmp_path, "default").write_bytes(b"junk")
        store, decision, _treaty = _build(tmp_path)
        assert store.loaded == 0
        decision.assert_outcome_partition()


class TestClosure:
    def test_every_rune_closes_over_itself(self):
        closure = trace_memo.rune_closure(SPEC)
        assert set(closure) == set(SPEC.runes)
        assert all(name in names for name, names in closure.items())

    def test_a_resolve_against_reference_joins_the_closure(self):
        target = sorted(SPEC.runes)[0]
        owner = sorted(SPEC.runes)[1]
        rune = SPEC.runes[owner]
        record = PolicyRecord(kind="resolve", against=(target, None))
        patched = replace(rune, policy=replace(rune.policy, resolve=(record,)))
        spec = replace(SPEC, runes={**SPEC.runes, owner: patched})
        assert trace_memo.rune_closure(spec)[owner] == {owner, target}
        assert trace_memo.rune_closure(SPEC)[owner] == {owner}


def _share_key(left, input_rune, *tokens):
    filled = list(tokens) + [EDGE] * (4 - len(tokens))
    return (*left, input_rune, *filled)


_EDGE_LEFT = ("edge", None, None, None, 0)
_MAY_LEFT = ("letter", "qsMay", "loop", "x-height", 0)
_TEA_LEFT = ("letter", "qsTea", "full", None, 0)
_LETTER = {name: RightToken("letter", name) for name in SPEC.runes}


class TestFeatureSensitivity:
    def test_the_fixture_ss03_delta_yields_pair_triggers_and_nothing_unverifiable(self):
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss03"}))
        assert sens.anywhere == set()
        assert sens.right_triggers == {"qsMay": frozenset({"qsTea"})}
        assert "qsMay" in sens.left_triggers["qsTea"]

    def test_a_gate_with_no_positive_family_axis_marks_its_owner_everywhere(self):
        # The fixture ss04 unlock's right side is an except:-only condition; nothing verifiable remains, so qsIt's mere presence must mark a key sensitive.
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss04"}))
        assert "qsIt" in sens.anywhere

    def test_a_key_with_no_sensitive_rune_is_shared(self):
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss03"}))
        assert sens.key_shared(_share_key(_EDGE_LEFT, "qsPea", _LETTER["qsOy"]))

    def test_a_right_trigger_owner_before_its_trigger_family_is_sensitive(self):
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss03"}))
        assert not sens.key_shared(_share_key(_EDGE_LEFT, "qsMay", _LETTER["qsTea"]))
        assert not sens.key_shared(_share_key(_EDGE_LEFT, "qsPea", _LETTER["qsMay"], _LETTER["qsTea"]))
        assert sens.key_shared(_share_key(_EDGE_LEFT, "qsMay", _LETTER["qsPea"]))
        assert sens.key_shared(_share_key(_EDGE_LEFT, "qsMay"))

    def test_an_unknown_or_beyond_window_neighbor_keeps_a_trigger_owner_sensitive(self):
        # Shifted evaluations read UNKNOWN past the window, where a gated condition returns None against the gate-off False — so an owner facing UNKNOWN, or sitting at the last slot, cannot be shared.
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss03"}))
        assert not sens.key_shared(_share_key(_EDGE_LEFT, "qsPea", _LETTER["qsMay"], UNKNOWN))
        assert not sens.key_shared(
            _share_key(
                _EDGE_LEFT, "qsPea", _LETTER["qsOy"], _LETTER["qsOy"], _LETTER["qsOy"], _LETTER["qsMay"]
            )
        )

    def test_a_left_trigger_owner_reads_its_own_left(self):
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss03"}))
        assert not sens.key_shared(_share_key(_MAY_LEFT, "qsTea"))
        assert not sens.key_shared(_share_key(_TEA_LEFT, "qsPea"))
        assert sens.key_shared(_share_key(_EDGE_LEFT, "qsTea"))

    def test_an_anywhere_owner_marks_any_slot(self):
        sens = trace_memo.FeatureSensitivity(SPEC, frozenset({"ss04"}))
        assert not sens.key_shared(_share_key(_EDGE_LEFT, "qsPea", _LETTER["qsIt"]))
        assert not sens.key_shared(_share_key(("letter", "qsIt", "hapax", None, 0), "qsPea"))
        assert sens.key_shared(_share_key(_EDGE_LEFT, "qsPea", _LETTER["qsOy"]))


class TestConfigShare:
    @pytest.fixture(scope="class")
    def shared(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("share")
        share = trace_memo.TraceShare(SPEC)
        donor_store = trace_memo.open_store(
            trace_memo.store_path(tmp, "default"), SPEC, DIGESTS, "env", "default"
        )
        table_module.build_tables(SPEC, frozenset(), trace_store=donor_store, share=share)
        recipient_store = trace_memo.open_store(
            trace_memo.store_path(tmp, "ss03"), SPEC, DIGESTS, "env", "ss03"
        )
        decision, treaty = table_module.build_tables(
            SPEC, frozenset({"ss03"}), trace_store=recipient_store, share=share
        )
        reader = share.last_reader
        share.release()
        reference_decision, reference_treaty = table_module.build_tables(SPEC, frozenset({"ss03"}))
        return SimpleNamespace(
            donor_store=donor_store,
            recipient_store=recipient_store,
            reader=reader,
            decision=decision,
            treaty=treaty,
            reference_decision=reference_decision,
            reference_treaty=reference_treaty,
        )

    def test_the_share_actually_serves(self, shared):
        assert shared.reader is not None
        assert shared.reader.served > 0

    def test_a_shared_build_is_indistinguishable_from_a_full_one(self, shared):
        assert _outcomes(shared.decision) == _outcomes(shared.reference_decision)
        assert shared.decision.rules == shared.reference_decision.rules
        assert shared.treaty.rows == shared.reference_treaty.rows
        assert table_module.windows_digest(shared.decision) == table_module.windows_digest(
            shared.reference_decision
        )

    def test_served_keys_fill_cited_provenance_exactly_as_a_full_build_would(self, shared):
        assert shared.decision.cited_provenance == shared.reference_decision.cited_provenance

    def test_share_hits_stay_out_of_the_recipient_store(self, shared):
        # A recipient's persisted memo should carry only the windows its configuration owns — the sensitive fraction — because share hits never enter the engine cache the store rewrites.
        assert 0 < shared.recipient_store.saved < shared.donor_store.saved

    def test_the_donor_itself_gets_no_reader(self):
        share = trace_memo.TraceShare(SPEC)
        assert share.reader_for(frozenset()) is None
        engine_less = share.reader_for(frozenset({"ss03"}))
        assert engine_less is None


class TestStructureDigest:
    def test_stable_over_an_unchanged_spec(self):
        assert trace_memo.spec_structure_digest(SPEC) == trace_memo.spec_structure_digest(SPEC)

    def test_moves_when_the_alphabet_shrinks(self):
        smaller = replace(SPEC, runes={name: SPEC.runes[name] for name in list(SPEC.runes)[:-1]})
        assert trace_memo.spec_structure_digest(smaller) != trace_memo.spec_structure_digest(SPEC)

    def test_moves_when_a_predicate_class_gains_a_member(self):
        classes = dict(SPEC.registry.predicate_classes)
        name, members = next(iter(classes.items()))
        classes[name] = frozenset(members | {"qsHapax"})
        widened = replace(SPEC, registry=replace(SPEC.registry, predicate_classes=classes))
        assert trace_memo.spec_structure_digest(widened) != trace_memo.spec_structure_digest(SPEC)
