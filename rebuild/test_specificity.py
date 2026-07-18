"""The dedicated regression-test class for the section 6.2 extensional specificity order — the one module where a quiet bug would masquerade as taste regression, so it gets its own paranoia budget (design section 15.5). The two named design cases (the decline-discriminator window and the qsJay contract-vs-extend overlap) need families outside the M1 alphabet and therefore run as synthetic analogs here, per M1-PLAN section 5."""

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline.model import Condition, PolicyRecord, When
from rebuild.pipeline.specificity import (
    EIncomparableError,
    Ordering,
    axis_sets,
    class_members,
    outranks,
    pick_most_specific,
)

SPEC = fixtures.mini_spec()


def extend(by=1, **when_kwargs) -> PolicyRecord:
    return PolicyRecord(kind="extend", stance="hapax", exit="baseline", by=by, when=When(**when_kwargs))


class TestAxisExpansion:
    def test_class_reference_expands_to_registry_membership(self):
        assert class_members(SPEC, "halves-that-exit-at-x-height") == frozenset({"qsPea", "qsTea"})

    def test_rune_local_group_resolves(self):
        assert "qsShe" in class_members(SPEC, "utter-pass-through-vetoes", owner="qsIt")

    def test_family_and_class_are_conjunctive(self):
        when = When(left=Condition(family=("qsTea", "qsMay"), klass=("halves-that-exit-at-x-height",)))
        assert axis_sets(SPEC, when)["left.family"] == frozenset({"qsTea"})

    def test_except_carves_the_family_axis(self):
        when = When(
            left=Condition(klass=("halves-that-exit-at-x-height",), except_=(Condition(family=("qsPea",)),))
        )
        assert axis_sets(SPEC, when)["left.family"] == frozenset({"qsTea"})

    def test_is_boundary_expands_to_the_token_set(self):
        when = When(right=Condition(is_token="boundary"))
        assert axis_sets(SPEC, when)["right.is"] == frozenset({"edge", "space", "zwnj", "namer-dot"})


class TestOutranks:
    def test_literal_singleton_outranks_the_class_it_belongs_to(self):
        narrow = extend(left=Condition(family=("qsTea",)))
        broad = extend(left=Condition(klass=("halves-that-exit-at-x-height",)))
        assert outranks(SPEC, narrow, broad) is Ordering.A_OUTRANKS
        assert outranks(SPEC, broad, narrow) is Ordering.B_OUTRANKS

    def test_extra_constrained_axis_outranks(self):
        narrow = extend(left=Condition(family=("qsTea",), joined_at="x-height"))
        broad = extend(left=Condition(family=("qsTea",)))
        assert outranks(SPEC, narrow, broad) is Ordering.A_OUTRANKS

    def test_identical_conditions_are_equal(self):
        a = extend(left=Condition(family=("qsTea",)))
        b = extend(left=Condition(family=("qsTea",)))
        assert outranks(SPEC, a, b) is Ordering.EQUAL

    def test_crossing_axes_are_incomparable(self):
        a = extend(left=Condition(family=("qsTea",)))
        b = extend(right=Condition(family=("qsIt",)))
        assert outranks(SPEC, a, b) is Ordering.INCOMPARABLE

    def test_overlapping_family_lists_are_incomparable(self):
        a = extend(left=Condition(family=("qsTea", "qsMay")))
        b = extend(left=Condition(family=("qsTea", "qsIt")))
        assert outranks(SPEC, a, b) is Ordering.INCOMPARABLE

    def test_except_narrowing_is_a_strict_subset(self):
        carved = extend(
            left=Condition(klass=("halves-that-exit-at-x-height",), except_=(Condition(family=("qsPea",)),))
        )
        whole = extend(left=Condition(klass=("halves-that-exit-at-x-height",)))
        assert outranks(SPEC, carved, whole) is Ordering.A_OUTRANKS


class TestPickMostSpecific:
    def test_nested_conflict_resolves_silently_to_the_narrow_record(self):
        narrow = extend(by=2, left=Condition(family=("qsTea",)))
        broad = extend(by=1, left=Condition(klass=("halves-that-exit-at-x-height",)))
        assert pick_most_specific(SPEC, [broad, narrow]) is narrow

    def test_equal_demand_non_nested_overlap_is_tolerated(self):
        # The real qsIt/qsMay shape: a self-scoped record and a toward-list record, both by 1, co-matching one seam (M1-PLAN authoring caveats; exercised end to end by the qsTea qsMay qsIt settlement row).
        a = extend(by=1, self_entry="live")
        b = extend(by=1, right=Condition(family=("qsJai", "qsCheer", "qsOwe", "qsIt")))
        assert pick_most_specific(SPEC, [a, b]) is a

    def test_conflicting_demands_at_non_nested_overlap_refuse_to_guess(self):
        a = extend(by=1, self_entry="live")
        b = extend(by=2, right=Condition(family=("qsIt",)))
        with pytest.raises(EIncomparableError):
            pick_most_specific(SPEC, [a, b])

    def test_contract_vs_extend_overlap_analog(self):
        # Synthetic analog of the named qsJay case: the single-family contract outranks the broad list-authored extend by membership — today's documented idiom as a theorem (design section 6.2).
        broad_extend = extend(by=1, left=Condition(family=("qsPea", "qsTea", "qsMay", "qsIt")))
        narrow_contract = PolicyRecord(
            kind="contract", stance="hapax", exit="baseline", by=1, when=When(left=Condition(family=("qsTea",)))
        )
        assert outranks(SPEC, narrow_contract, broad_extend) is Ordering.A_OUTRANKS

    def test_decline_discriminator_analog(self):
        # Synthetic analog of the decline-discriminator window: a record keyed on the seam having declined (joined_at none-state via self) is narrower than its unconditioned sibling, never incomparable with it.
        keyed = extend(by=1, self_entry="none", right=Condition(family=("qsIt",)))
        sibling = extend(by=1, right=Condition(family=("qsIt",)))
        assert outranks(SPEC, keyed, sibling) is Ordering.A_OUTRANKS
        assert pick_most_specific(SPEC, [sibling, keyed]) is keyed


class TestDepthThreeChainSpecificity:
    """The depth-3 and depth-4 chain records must not move in the section 6.2 order: `_side_axes` walks only the spine of `then:` hops and `_family_set` conservatively ignores multi-axis `except:` entries, so a chain carried by an `except` adds no axis and subtracts nothing — the edited record ranks exactly as its pre-chain shape did."""

    @pytest.fixture(scope="class")
    def real_spec(self):
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_default_spec()

    @staticmethod
    def _strip_chain(cond: Condition) -> Condition:
        from dataclasses import replace

        stripped_then = None if cond.then is None else TestDepthThreeChainSpecificity._strip_chain(cond.then)
        kept_excepts = tuple(ex for ex in cond.except_ if ex.then is None)
        return replace(cond, then=stripped_then, except_=kept_excepts)

    @pytest.mark.parametrize(
        "rune,index",
        (("qsDay", 1), ("qsDay", 5), ("qsOy", 0), ("qsTea_qsOy", 0)),
        ids=("qsDay.prefer1", "qsDay.prefer5", "qsOy.prefer0", "qsTea_qsOy.prefer0"),
    )
    def test_edited_records_keep_their_axes_and_rank(self, real_spec, rune, index):
        from dataclasses import replace

        record = real_spec.runes[rune].policy.prefer[index]
        assert record.when.right is not None
        stripped_when = replace(record.when, right=self._strip_chain(record.when.right))
        assert axis_sets(real_spec, record.when, rune) == axis_sets(real_spec, stripped_when, rune)
        assert (
            outranks(real_spec, record, replace(record, when=stripped_when), rune, rune) is Ordering.EQUAL
        )

    def test_then_then_axes_only_on_the_qsday_no_records(self, real_spec):
        for rune in ("qsOy", "qsTea_qsOy"):
            for record in real_spec.runes[rune].policy.prefer:
                axes = axis_sets(real_spec, record.when, rune)
                assert not any("then.then" in axis for axis in axes)
        chained = [
            axes
            for record in real_spec.runes["qsDay"].policy.prefer
            for axes in [axis_sets(real_spec, record.when, "qsDay")]
            if any("then.then" in axis for axis in axes)
        ]
        assert len(chained) == 2
        assert all(axes["right.then.family"] == frozenset({"qsNo"}) for axes in chained)
        assert chained[0]["right.then.then.family"] == frozenset({"qsTea", "qsMay", "qsLow"})
        assert chained[1]["right.then.then.is"] == frozenset({"edge", "namer-dot", "space", "zwnj"})

    def test_qsday_prefer_family_axes_pinned(self, real_spec):
        record = real_spec.runes["qsDay"].policy.prefer[1]
        axes = axis_sets(real_spec, record.when, "qsDay")
        assert axes["right.family"] == frozenset({"qsTea"})
        assert axes["right.then.family"] == frozenset({"qsUtter"})
        record = real_spec.runes["qsDay"].policy.prefer[2]
        axes = axis_sets(real_spec, record.when, "qsDay")
        assert axes["right.family"] == frozenset({"qsTea"})
        assert axes["right.then.family"] == frozenset(
            {"qsDay", "qsDay_qsUtter", "qsMay", "qsLow", "qsIt"}
        )
