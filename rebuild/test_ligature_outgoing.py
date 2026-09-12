"""Synthetic contracts for a ligature's preserved trailing stroke and its local geometry."""

import warnings
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import model, spec_load
from rebuild.test_spec_load import MINIMAL_REGISTRY, MINIMAL_RUNE, load_tmp_error, load_tmp_spec


@pytest.fixture
def runes() -> dict:
    source = yaml.safe_load(MINIMAL_RUNE)
    ligature = deepcopy(source)
    ligature["rune"] = "qsDay_qsIt"
    del ligature["codepoint"]
    ligature["sequence"] = ["qsDay", "qsIt"]
    stance = ligature["stances"]["hapax"]
    stance["bitmap"] = ["###"] * 6
    stance["surface"]["exits"]["baseline"]["x"] = 3
    return {"qsIt": source, "qsDay_qsIt": ligature}


def _texts(runes: dict) -> dict[str, str]:
    return {name: yaml.safe_dump(raw, sort_keys=False) for name, raw in runes.items()}


def _registry(runes: dict) -> str:
    registry = yaml.safe_load(MINIMAL_REGISTRY)
    for name, raw in runes.items():
        if "sequence" in raw:
            registry["families"][name] = {"sequence": raw["sequence"]}
    return yaml.safe_dump(registry, sort_keys=False)


def _load(tmp_path: Path, runes: dict) -> model.ResolvedSpec:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", spec_load.SpecWarning)
        return load_tmp_spec(tmp_path, _texts(runes), _registry(runes))


def _error(tmp_path: Path, runes: dict, text: str) -> None:
    error = load_tmp_error(tmp_path, _texts(runes), _registry(runes))
    assert any(text in issue.message for issue in error.issues), str(error)


def test_implicit_single_stance_inherits_scope_and_tracks_component_edits(tmp_path, runes):
    source_row = runes["qsIt"]["stances"]["hapax"]["surface"]["exits"]["baseline"]
    for family in ("qsDay", "qsMay"):
        source_row["toward"] = [{"family": family}]
        spec = _load(tmp_path, runes)
        assert spec.runes["qsDay_qsIt"].stances["hapax"].surface.exits["baseline"].scope == (
            model.Condition(family=(family,)),
        )


def test_outgoing_inheritance_preserves_ligature_geometry_and_incoming_state(tmp_path, runes):
    source = runes["qsIt"]["stances"]["hapax"]
    source["surface"]["entries"]["baseline"]["from"] = [{"family": "qsMay"}]
    source["surface"]["pairings"] = {"never": [{"entry": "baseline", "exit": "baseline"}]}
    source["surface"]["require"] = ["exit"]
    source["bitmaps"] = {"withdrawn": {"bitmap": [" "] * 6}}
    source["surface"]["exits"]["baseline"]["withdrawal"] = "withdrawn"
    source["surface"]["exits"]["baseline"]["stroke"] = "vertical"
    spec = _load(tmp_path, runes)
    local = spec.runes["qsDay_qsIt"].stances["hapax"]
    assert local.bitmap.rows == ("###",) * 6
    assert local.bitmaps == {}
    assert local.surface.entries["baseline"].scope == ()
    assert local.surface.pairings == model.Pairings()
    assert local.surface.require == ()
    assert local.surface.exits["baseline"].x == 3
    assert local.surface.exits["baseline"].withdrawal == "safe"
    assert local.surface.exits["baseline"].stroke is None


def _multiple_stances(runes: dict) -> None:
    source = runes["qsIt"]
    stance = source["stances"].pop("hapax")
    source["stances"] = {"full": deepcopy(stance), "half": deepcopy(stance)}
    source["stances"]["full"]["surface"]["exits"]["baseline"]["toward"] = [{"family": "qsDay"}]
    source["stances"]["half"]["surface"]["exits"]["baseline"]["toward"] = [{"family": "qsMay"}]


def test_multiple_component_stances_require_an_explicit_mapping(tmp_path, runes):
    _multiple_stances(runes)
    _error(tmp_path, runes, "multiple stances")


def test_mapping_inherits_only_the_selected_stances_policy(tmp_path, runes):
    _multiple_stances(runes)
    runes["qsIt"]["policy"] = {
        "refuse": [
            {"stance": stance, "exit": "baseline", "when": {"right": {"family": family}}}
            for stance, family in (("full", "qsDay"), ("half", "qsMay"))
        ]
    }
    runes["qsDay_qsIt"]["stances"]["hapax"]["outgoing"] = {"stance": "half"}
    spec = _load(tmp_path, runes)
    ligature = spec.runes["qsDay_qsIt"]
    assert ligature.stances["hapax"].surface.exits["baseline"].scope == (model.Condition(family=("qsMay",)),)
    assert ligature.policy.refuse == (replace(spec.runes["qsIt"].policy.refuse[1], stance="hapax"),)


@pytest.mark.parametrize(
    ("kind", "record"),
    [
        ("refuse", {"entry": "baseline", "when": {"right": {"family": "qsDay"}}}),
        ("refuse", {"exit": "baseline", "when": {"left": {"family": "qsDay"}}}),
        ("refuse", {"exit": "baseline", "when": {"self": {"entry": "none"}}}),
        ("refuse", {"exit": "baseline", "when": {"word": "initial"}}),
        ("prefer", {"stance": "hapax", "when": {"right": {"family": "qsDay"}}}),
        ("prefer", {"cell": {"exit": "baseline"}, "over": {"entry": "baseline"}}),
        ("prefer", {"cell": {"entry": "none", "exit": "baseline"}}),
        ("extend", {"entry": "baseline", "by": 1, "when": {"right": {"family": "qsDay"}}}),
    ],
)
def test_incoming_and_placement_policy_does_not_inherit(tmp_path, runes, kind, record):
    runes["qsIt"]["policy"] = {kind: [record]}
    spec = _load(tmp_path, runes)
    assert getattr(spec.runes["qsDay_qsIt"].policy, kind) == ()


def test_exit_yield_inherits_its_right_chain_and_mode(tmp_path, runes):
    runes["qsIt"]["policy"] = {
        "prefer": [
            {
                "cell": {"exit": "none"},
                "over": {"exit": "baseline"},
                "mode": "yields-to-joins",
                "when": {
                    "right": {"family": "qsDay", "except": [{"family": "qsDay", "then": {"family": "qsMay"}}]}
                },
            }
        ]
    }
    spec = _load(tmp_path, runes)
    assert spec.runes["qsDay_qsIt"].policy.prefer == (
        replace(spec.runes["qsIt"].policy.prefer[0], stance="hapax"),
    )


@pytest.mark.parametrize("kind", ["refuse", "extend", "contract"])
def test_exit_policy_keeps_feature_and_adjustment_terms(tmp_path, runes, kind):
    record: dict = {"exit": "baseline", "when": {"feature": "ss04", "right": {"family": "qsDay"}}}
    if kind in ("extend", "contract"):
        record["by"] = 1
    if kind == "extend":
        record["ok"] = [1, 2]
    runes["qsIt"]["policy"] = {kind: [record]}
    spec = _load(tmp_path, runes)
    assert getattr(spec.runes["qsDay_qsIt"].policy, kind) == (
        replace(getattr(spec.runes["qsIt"].policy, kind)[0], stance="hapax"),
    )


def test_only_exit_unlocks_without_incoming_context_inherit(tmp_path, runes):
    runes["qsIt"]["stances"]["hapax"]["surface"]["unlocks"] = [
        {"feature": "ss04", "exit": "baseline", "when": {"right": {"family": "qsDay"}}},
        {"feature": "ss04", "entry": "baseline"},
        {"feature": "ss04", "pairing": {"entry": "baseline", "exit": "baseline"}},
        {"feature": "ss04", "exit": "baseline", "when": {"self": {"entry": "none"}}},
        {"feature": "ss04", "exit": "baseline", "when": {"left": {"family": "qsDay"}}},
        {"feature": "ss04", "exit": "baseline", "when": {"word": "initial"}},
    ]
    spec = _load(tmp_path, runes)
    assert spec.runes["qsDay_qsIt"].stances["hapax"].surface.unlocks == (
        spec.runes["qsIt"].stances["hapax"].surface.unlocks[0],
    )


def test_named_unlock_exception_drops_only_that_grant(tmp_path, runes):
    runes["qsIt"]["stances"]["hapax"]["surface"]["unlocks"] = [
        {"feature": "ss04", "exit": "baseline"},
    ]
    runes["qsDay_qsIt"]["stances"]["hapax"]["outgoing"] = {
        "exceptions": {"surface.unlocks[0]": "This drawing keeps the exit without a feature."}
    }
    spec = _load(tmp_path, runes)
    assert spec.runes["qsDay_qsIt"].stances["hapax"].surface.unlocks == ()
    assert "baseline" in spec.runes["qsDay_qsIt"].stances["hapax"].surface.exits


@pytest.mark.parametrize("missing", [True, False])
def test_missing_exit_or_changed_scope_needs_a_named_exception(tmp_path, runes, missing):
    local = runes["qsDay_qsIt"]["stances"]["hapax"]
    if missing:
        local["surface"]["exits"] = {}
    else:
        local["surface"]["exits"]["baseline"]["toward"] = [{"family": "qsMay"}]
    _error(tmp_path, runes, "surface.exits.baseline")
    local["outgoing"] = {"exceptions": {"surface.exits.baseline": "The ligature has its own exit."}}
    spec = _load(tmp_path, runes)
    exits = spec.runes["qsDay_qsIt"].stances["hapax"].surface.exits
    if missing:
        assert exits == {}
    else:
        assert exits["baseline"].scope == (model.Condition(family=("qsMay",)),)


def test_bitmap_policy_requires_exception_and_local_binding(tmp_path, runes):
    source = runes["qsIt"]
    source["stances"]["hapax"]["bitmaps"] = {"shortened": {"bitmap": [" "] * 6}}
    source["policy"] = {
        "contract": [{"exit": "baseline", "bind": "shortened", "when": {"right": {"family": "qsDay"}}}]
    }
    _error(tmp_path, runes, "binds a component bitmap")
    local = runes["qsDay_qsIt"]
    local["stances"]["hapax"]["outgoing"] = {
        "exceptions": {"policy.contract[0]": "The combined drawing uses its own binding."}
    }
    local["stances"]["hapax"]["bitmaps"] = {"shortened": {"bitmap": ["## "] * 6}}
    local["policy"] = deepcopy(source["policy"])
    spec = _load(tmp_path, runes)
    assert len(spec.runes["qsDay_qsIt"].policy.contract) == 1
    assert spec.runes["qsDay_qsIt"].stances["hapax"].bitmaps["shortened"].rows == ("## ",) * 6


def _replacement(runes: dict) -> dict:
    runes["qsIt"]["policy"] = {"refuse": [{"exit": "baseline", "when": {"right": {"family": "qsDay"}}}]}
    runes["qsDay_qsIt"]["policy"] = {
        "refuse": [{"exit": "baseline", "when": {"right": {"family": "qsMay"}}}],
        "prefer": [{"cell": {"exit": "none"}, "over": {"exit": "baseline"}}],
    }
    declaration = {
        "exceptions": {"policy.refuse[0]": "The combined stroke refuses a different follower."},
        "replacements": {"policy.refuse[0]": "policy.refuse[0]"},
    }
    runes["qsDay_qsIt"]["stances"]["hapax"]["outgoing"] = declaration
    return declaration


def test_explicit_policy_replacement_can_change_the_follower_condition(tmp_path, runes):
    _replacement(runes)
    spec = _load(tmp_path, runes)
    (refusal,) = spec.runes["qsDay_qsIt"].policy.refuse
    assert refusal.when.right == model.Condition(family=("qsMay",))


def test_policy_replacement_requires_a_source_exception(tmp_path, runes):
    declaration = _replacement(runes)
    del declaration["exceptions"]
    _error(tmp_path, runes, "excepted source policy")


@pytest.mark.parametrize("replacement", ["policy.refuse[99]", "policy.prefer[0]"])
def test_policy_replacement_must_name_an_existing_local_record_of_the_same_kind(tmp_path, runes, replacement):
    declaration = _replacement(runes)
    declaration["replacements"]["policy.refuse[0]"] = replacement
    _error(tmp_path, runes, "local record of the same kind")


@pytest.mark.parametrize(
    "declaration",
    [
        {"stance": "missing"},
        {"exception": "   "},
        {"exception": "A replacement stroke.", "stance": "hapax"},
        {"exceptions": {"policy.refuse[99]": "A stale reference."}},
        {"exceptions": {"surface.entries.baseline": "An incoming row."}},
        {"exceptions": {"surface.exits.baseline": "   "}},
    ],
)
def test_invalid_or_stale_outgoing_declaration_is_rejected(tmp_path, runes, declaration):
    runes["qsDay_qsIt"]["stances"]["hapax"]["outgoing"] = declaration
    load_tmp_error(tmp_path, _texts(runes), _registry(runes))


def test_exception_cannot_name_a_policy_that_does_not_apply_to_the_outgoing_stroke(tmp_path, runes):
    runes["qsIt"]["policy"] = {"refuse": [{"entry": "baseline", "when": {"right": {"family": "qsDay"}}}]}
    runes["qsDay_qsIt"]["stances"]["hapax"]["outgoing"] = {
        "exceptions": {"policy.refuse[0]": "This is an incoming policy."}
    }
    _error(tmp_path, runes, "does not name an applicable inherited")


def test_unmigrated_trailing_component_requires_a_full_stance_exception(tmp_path, runes):
    del runes["qsIt"]
    _error(tmp_path, runes, "trailing component must be migrated")
    runes["qsDay_qsIt"]["stances"]["hapax"]["outgoing"] = {
        "exception": "The complete local drawing defines this stroke."
    }
    spec = _load(tmp_path, runes)
    assert spec.runes["qsDay_qsIt"].stances["hapax"].surface.exits["baseline"].x == 3


def test_full_stance_exception_keeps_local_policy_and_scope(tmp_path, runes):
    runes["qsIt"]["policy"] = {"refuse": [{"exit": "baseline", "when": {"right": {"family": "qsDay"}}}]}
    local = runes["qsDay_qsIt"]["stances"]["hapax"]
    local["outgoing"] = {"exception": "The combined drawing ends in a different stroke."}
    local["surface"]["exits"] = {}
    spec = _load(tmp_path, runes)
    assert spec.runes["qsDay_qsIt"].policy.refuse == ()
    assert spec.runes["qsDay_qsIt"].stances["hapax"].surface.exits == {}
    assert spec_load.rune_closure(spec)["qsDay_qsIt"] == {"qsDay_qsIt", "qsIt"}


def test_source_groups_keep_membership_despite_a_local_name_collision(tmp_path, runes):
    for name, family in (("qsIt", "qsDay"), ("qsDay_qsIt", "qsMay")):
        runes[name]["policy"] = {
            "groups": {"followers": {"union": [{"family": family}]}},
            "refuse": [{"exit": "baseline", "when": {"right": {"class": "followers"}}}],
        }
    runes["qsIt"]["stances"]["hapax"]["surface"]["exits"]["baseline"]["toward"] = [{"class": "followers"}]
    spec = _load(tmp_path, runes)
    ligature = spec.runes["qsDay_qsIt"]
    local, inherited = ligature.policy.refuse
    assert local.when.right is not None and inherited.when.right is not None
    assert ligature.policy.groups[local.when.right.klass[0]] == {"qsMay"}
    assert ligature.policy.groups[inherited.when.right.klass[0]] == {"qsDay"}
    scope = ligature.stances["hapax"].surface.exits["baseline"].scope[0]
    assert ligature.policy.groups[scope.klass[0]] == {"qsDay"}


def test_trailing_dependencies_are_transitive(tmp_path, runes):
    outer = deepcopy(runes["qsDay_qsIt"])
    outer["rune"] = "qsMay_qsDay_qsIt"
    outer["sequence"] = ["qsMay", "qsDay_qsIt"]
    runes[outer["rune"]] = outer
    spec = _load(tmp_path, runes)
    assert spec_load.rune_closure(spec)[outer["rune"]] == {"qsMay_qsDay_qsIt", "qsDay_qsIt", "qsIt"}
