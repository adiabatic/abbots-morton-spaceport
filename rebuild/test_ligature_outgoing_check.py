"""Synthetic contract probes for outgoing joins preserved through ligature formation."""

from dataclasses import replace

import pytest

from rebuild.pipeline import fixtures, kernel_exec
from rebuild.pipeline.ligature_outgoing_check import (
    LigatureOutgoingError,
    _capability_answer,
    _projection_pair,
    _right_windows,
    validate_ligature_outgoing,
)
from rebuild.pipeline.model import (
    Condition,
    FamilyInfo,
    FeatureInfo,
    Policy,
    PolicyRecord,
    Rune,
    Stance,
    Surface,
    When,
)
from rebuild.pipeline.settle import EDGE, RightToken, SettleError


def _world(
    *, outgoing=None, source_refuse=(), source_prefer=(), local_refuse=(), local_prefer=(), missing=False
):
    spec = fixtures.synthetic_spec()
    source = spec.runes["B"]
    source = replace(source, policy=replace(source.policy, refuse=source_refuse, prefer=source_prefer))
    stance = Stance(
        "joined", "joined", surface=Surface(exits={} if missing else source.stances["hook"].surface.exits)
    )
    ligature = Rune(
        "AB",
        sequence=("A", "B"),
        stances={"joined": stance},
        policy=Policy(order=("joined",), refuse=local_refuse, prefer=local_prefer),
    )
    spec = replace(
        spec,
        runes={**spec.runes, "B": source, "AB": ligature},
        registry=replace(
            spec.registry, families={**spec.registry.families, "AB": FamilyInfo(sequence=("A", "B"))}
        ),
    )
    declaration = {"stance": "hook"} if outgoing is None else outgoing
    return spec, {"AB": {"stances": {"joined": {"outgoing": declaration}}}}


def _refusal(**when):
    return PolicyRecord("refuse", exit="baseline", when=When(right=Condition(family=("C",)), **when))


def test_mapped_outgoing_contract_covers_boundaries_and_formation_preemption():
    spec, raw = _world()
    spec = replace(
        spec, registry=replace(spec.registry, features={"ss10": FeatureInfo("taste", overlay="isolated")})
    )
    summary = validate_ligature_outgoing(spec, raw)
    assert summary["mappings"] == 1
    assert summary["configurations"] == ["default"]
    assert summary["formation_preempted"] == ["ss10"]
    assert summary["windows"] == (len(spec.runes) + 4) ** 2 * 4


def test_unformed_component_internal_left_refusal_cannot_hide_a_lost_join():
    internal = _refusal(left=Condition(family=("A",)))
    spec, raw = _world(source_refuse=(internal,), local_refuse=(_refusal(),))
    assert kernel_exec.guard_sweep(spec)[("AB", RightToken("letter", "C"), EDGE)] is False
    with pytest.raises(
        LigatureOutgoingError, match=r"AB.stances.joined.outgoing: lost baseline.*B.hook.*right=\[C"
    ):
        validate_ligature_outgoing(spec, raw)


def test_guard_yielding_does_not_excuse_a_missing_mapped_exit():
    spec, raw = _world(missing=True)
    assert kernel_exec.guard_sweep(spec)[("AB", RightToken("letter", "C"), EDGE)] is True
    with pytest.raises(LigatureOutgoingError, match="lost baseline"):
        validate_ligature_outgoing(spec, raw)


def test_explicit_surface_exception_accepts_an_intentionally_absent_exit():
    spec, raw = _world(
        missing=True,
        outgoing={
            "stance": "hook",
            "exceptions": {"surface.exits.baseline": "The composite does not carry this stroke."},
        },
    )
    assert validate_ligature_outgoing(spec, raw)["mappings"] == 1


def test_blanket_outgoing_exception_skips_the_mapping():
    spec, raw = _world(missing=True, outgoing={"exception": "The trailing component is redrawn."})
    assert validate_ligature_outgoing(spec, raw)["mappings"] == 0


def test_lost_join_is_checked_under_capability_features():
    spec, raw = _world(local_refuse=(_refusal(feature="ss03"),))
    spec = replace(spec, registry=replace(spec.registry, features={"ss03": FeatureInfo("capability")}))
    with pytest.raises(LigatureOutgoingError, match="under ss03"):
        validate_ligature_outgoing(spec, raw)


def test_matching_source_yield_is_preserved():
    preference = PolicyRecord(
        "prefer",
        cell={"exit": "none"},
        over={"exit": "baseline"},
        mode="absolute",
        when=When(right=Condition(family=("C",))),
    )
    spec, raw = _world(source_prefer=(preference,), local_prefer=(replace(preference, stance="joined"),))
    assert validate_ligature_outgoing(spec, raw)["windows"] > 0


def test_lost_source_yield_is_reported_when_the_ligature_joins():
    preference = PolicyRecord(
        "prefer",
        cell={"exit": "none"},
        over={"exit": "baseline"},
        mode="absolute",
        when=When(right=Condition(family=("C",))),
    )
    spec, raw = _world(source_prefer=(preference,))
    with pytest.raises(LigatureOutgoingError, match=r"lost outgoing yield.*right=\[C"):
        validate_ligature_outgoing(spec, raw)


def test_deeper_authored_chain_is_in_the_probe_domain():
    preference = PolicyRecord(
        "prefer",
        cell={"exit": "none"},
        over={"exit": "baseline"},
        mode="absolute",
        when=When(
            right=Condition(family=("C",), then=Condition(family=("A",), then=Condition(family=("C",))))
        ),
    )
    spec, raw = _world(local_prefer=(preference,))
    with pytest.raises(LigatureOutgoingError, match=r"right=\[C A C edge\]"):
        validate_ligature_outgoing(spec, raw)


def test_a_boundary_sensitive_local_refusal_is_reported_at_that_boundary():
    spec, raw = _world(local_refuse=(_refusal(left=Condition(is_token="zwnj")),))
    with pytest.raises(LigatureOutgoingError, match="left=zwnj"):
        validate_ligature_outgoing(spec, raw)


def test_explicit_policy_replacement_preserves_the_local_yield():
    source = PolicyRecord(
        "prefer",
        cell={"exit": "none"},
        over={"exit": "baseline"},
        mode="absolute",
        when=When(right=Condition(family=("A",))),
    )
    local = replace(source, when=When(right=Condition(family=("C",))))
    declaration = {
        "stance": "hook",
        "exceptions": {"policy.prefer[0]": "The ligature yields before another follower."},
        "replacements": {"policy.prefer[0]": "policy.prefer[0]"},
    }
    spec, raw = _world(source_prefer=(source,), local_prefer=(local,), outgoing=declaration)
    assert validate_ligature_outgoing(spec, raw)["mappings"] == 1


def test_replacement_local_groups_are_distinct_from_inherited_source_groups():
    condition = When(right=Condition(klass=("followers",)))
    preference = PolicyRecord(
        "prefer", cell={"exit": "none"}, over={"exit": "baseline"}, mode="absolute", when=condition
    )
    refusal = PolicyRecord("refuse", exit="baseline", when=condition)
    declaration = {
        "stance": "hook",
        "exceptions": {"policy.prefer[0]": "The ligature yields to its local follower group."},
        "replacements": {"policy.prefer[0]": "policy.prefer[0]"},
    }
    inherited_refusal = replace(refusal, stance="joined", when=When(right=Condition(klass=("B.followers",))))
    spec, raw = _world(
        source_refuse=(refusal,),
        source_prefer=(preference,),
        local_refuse=(inherited_refusal,),
        local_prefer=(preference,),
        outgoing=declaration,
    )
    source = spec.runes["B"]
    source = replace(source, policy=replace(source.policy, groups={"followers": frozenset({"A"})}))
    ligature = spec.runes["AB"]
    ligature = replace(
        ligature,
        policy=replace(
            ligature.policy, groups={"followers": frozenset({"C"}), "B.followers": frozenset({"A"})}
        ),
    )
    spec = replace(spec, runes={**spec.runes, "B": source, "AB": ligature})
    _actual, expected = _projection_pair(spec, ligature, ligature.stances["joined"], declaration)
    assert expected.runes["AB"].policy.groups["followers"] == {"C"}
    assert expected.runes["AB"].policy.groups["B.followers"] == {"A"}
    assert validate_ligature_outgoing(spec, raw)["mappings"] == 1


def test_follower_policy_chains_are_shifted_into_the_probe_window():
    preference = PolicyRecord(
        "prefer",
        cell={"exit": "none"},
        over={"exit": "baseline"},
        when=When(
            right=Condition(family=("A",), then=Condition(family=("C",), then=Condition(family=("B",))))
        ),
    )
    spec, _raw = _world(source_prefer=(preference,))
    windows = set(_right_windows(spec))
    letter = lambda name: RightToken("letter", name)
    assert (letter("B"), letter("A"), letter("C"), EDGE) in windows
    assert (letter("B"), letter("A"), letter("C"), letter("B")) in windows


@pytest.mark.parametrize("bucket", ["E-INCOMPARABLE", "E-AMBIGUOUS"])
def test_ranking_errors_cannot_pass_as_missing_capability(bucket):
    with pytest.raises(SettleError) as error:
        _capability_answer({"raise": bucket, "message": "Synthetic ranking fault"})
    assert error.value.bucket == bucket
