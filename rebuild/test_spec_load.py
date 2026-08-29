"""spec_load unit tests: the real spec loads with class and group memberships that re-derive from the raw sources, every lint fires with a file/path/line error, and the built-in schema evaluator agrees with jsonschema when it is available."""

import textwrap
import warnings
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fixtures, model, spec_load
from rebuild.pipeline.model import Condition, PolicyRecord
from rebuild.pipeline.spec_load import SpecError, SpecWarning, load_default_spec, load_spec

MINI_SPEC = fixtures.mini_spec()

MINIMAL_REGISTRY = textwrap.dedent("""\
    heights: {baseline: 0, x-height: 5, y6: 6, top: 8}
    boundary_tokens:
      space: {codepoint: 0x0020, splits_runs: true}
    features:
      ss04: {kind: capability, description: "test capability"}
    predicate_classes:
      can-enter-at-baseline: {can_enter_at: baseline}
    families:
      qsDay: {codepoint: 0xE653}
      qsMay: {codepoint: 0xE665}
      qsIt: {codepoint: 0xE670}
    """)

MINIMAL_RUNE = textwrap.dedent("""\
    rune: qsIt
    codepoint: 0xE670
    ductus:
      hapax: |
        A vertical stroke.
    stances:
      hapax:
        motion: hapax
        bitmap:
        - "#"
        - "#"
        - "#"
        - "#"
        - "#"
        - "#"
        surface:
          entries:
            baseline: {x: 0}
          exits:
            baseline: {x: 1, withdrawal: safe}
    """)


def write_spec(
    tmp_path: Path, rune_texts: dict[str, str], registry: str = MINIMAL_REGISTRY
) -> tuple[Path, Path]:
    runes_dir = tmp_path / "runes"
    runes_dir.mkdir(exist_ok=True)
    for name, text in rune_texts.items():
        (runes_dir / f"{name}.yaml").write_text(text)
    registry_path = tmp_path / "script.yaml"
    registry_path.write_text(registry)
    return runes_dir, registry_path


def load_tmp_spec(tmp_path: Path, rune_texts: dict[str, str], registry: str = MINIMAL_REGISTRY):
    runes_dir, registry_path = write_spec(tmp_path, rune_texts, registry)
    return load_spec(runes_dir, registry_path, spec_load.DEFAULT_SCHEMA_DIR)


def load_tmp_error(tmp_path: Path, rune_texts: dict[str, str], registry: str = MINIMAL_REGISTRY) -> SpecError:
    with pytest.raises(SpecError) as caught:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SpecWarning)
            load_tmp_spec(tmp_path, rune_texts, registry)
    return caught.value


@pytest.fixture(scope="module")
def spec():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        return load_default_spec()


def test_loads_the_rune_files(spec):
    assert set(spec.runes) == {path.stem for path in spec_load.DEFAULT_RUNES_DIR.glob("*.yaml")}
    assert set(spec.runes["qsMay"].stances) == {"loop", "grounded-loop"}
    assert set(spec.runes["qsTea"].stances) == {"full", "half"}
    assert spec.runes["qsTea"].stances["half"].traits == ("half",)
    assert spec.runes["qsPea"].stances["half"].traits == ("half",)
    assert spec.runes["qsTea_qsOy"].sequence == ("qsTea", "qsOy")
    assert spec.runes["qsTea_qsOy"].codepoint is None
    assert spec.runes["qsIt"].mono is not None
    assert spec.runes["qsTea_qsOy"].notes is not None


def test_ductus_prose_survives_loading(tmp_path):
    prose = "- Either written from top to bottom or bottom to top."
    text = MINIMAL_RUNE.replace("A vertical stroke.", prose)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        loaded = load_tmp_spec(tmp_path, {"qsIt": text})
    assert loaded.runes["qsIt"].ductus["hapax"].strip() == prose


def test_registry_contents(spec):
    registry = spec.registry
    assert registry.heights == {"baseline": 0, "x-height": 5, "y6": 6, "top": 8}
    assert registry.boundary_tokens["zwnj"].codepoint == 0x200C
    assert registry.boundary_tokens["namer-dot"].splits_runs is False
    assert registry.features["ss10"].kind == "taste"
    assert registry.interactions == (("ss03", "ss05"),)
    assert registry.families["qsOoze"].codepoint == 0xE67E
    assert registry.families["qsTea_qsOy"].sequence == ("qsTea", "qsOy")


def _resolved_stance_satisfies(expression: dict, rune: model.Rune, stance: model.Stance) -> bool:
    if "can_enter_at" in expression:
        row = stance.surface.entries.get(expression["can_enter_at"])
        return row is not None and row.selectable
    if "can_exit_at" in expression:
        return expression["can_exit_at"] in stance.surface.exits
    if "trait" in expression:
        return expression["trait"] in stance.traits
    if "height_class" in expression:
        if rune.sequence is not None:
            return False
        shapes = {"tall": (9, 0), "short": (6, 0), "deep": (9, -3)}
        return (len(stance.bitmap.rows), stance.bitmap.y_offset) == shapes[expression["height_class"]]
    if "stroke_at" in expression:
        for wanted, rows in (
            (expression["stroke_at"].get("entry"), stance.surface.entries),
            (expression["stroke_at"].get("exit"), stance.surface.exits),
        ):
            if wanted is not None and not any(row.stroke == wanted for row in rows.values()):
                return False
        return True
    if "all" in expression:
        return all(_resolved_stance_satisfies(sub, rune, stance) for sub in expression["all"])
    if "union" in expression:
        return any(_resolved_stance_satisfies(sub, rune, stance) for sub in expression["union"])
    raise ValueError(f"unsupported predicate-class expression {expression!r}")


def test_predicate_class_membership(spec):
    declared = yaml.safe_load(spec_load.DEFAULT_REGISTRY_PATH.read_text())["predicate_classes"]
    assert set(spec.registry.predicate_classes) == set(declared)
    for class_name, expression in declared.items():
        derived = {
            rune.name
            for rune in spec.runes.values()
            if any(_resolved_stance_satisfies(expression, rune, stance) for stance in rune.stances.values())
        }
        assert spec.registry.predicate_classes[class_name] == derived, class_name


def test_group_resolution(spec):
    for path in sorted(spec_load.DEFAULT_RUNES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        raw_groups = ((raw.get("policy") or {}).get("groups")) or {}
        resolved = spec.runes[raw["rune"]].policy.groups
        assert set(resolved) == set(raw_groups), raw["rune"]
        for group_name, group in raw_groups.items():
            members: set[str] = set()
            for atom in group.get("union") or ():
                members.update(spec_load._as_tuple(atom.get("family")))
                for klass in spec_load._as_tuple(atom.get("class")):
                    members.update(spec.registry.predicate_classes[klass])
            for atom in group.get("minus") or ():
                if atom.get("trait") or atom.get("stance"):
                    continue
                members.difference_update(spec_load._as_tuple(atom.get("family")))
                for klass in spec_load._as_tuple(atom.get("class")):
                    members.difference_update(spec.registry.predicate_classes[klass])
            assert resolved[group_name] == members, f"{raw['rune']}.{group_name}"


def test_group_qualifier_warning(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          groups:
            qualified-vetoes:
              union: [{family: qsDay, trait: half}, family: qsMay]
        """)
    with pytest.warns(SpecWarning, match="family grain"):
        load_tmp_spec(tmp_path, {"qsIt": text})


def test_provenance_and_record_parsing(spec):
    refuse = spec.runes["qsIt"].policy.refuse[0]
    assert refuse.kind == "refuse"
    assert refuse.entry == "x-height"
    assert refuse.when.left == Condition(family=("qsIt",))
    assert refuse.provenance.file == "glyph_data/runes/qsIt.yaml"
    assert refuse.provenance.path == "policy.refuse[0]"
    flagship = spec.runes["qsIt"].policy.extend[1]
    assert flagship.exit == "baseline" and flagship.by == 1 and flagship.when.self_entry == "live"
    short_entry_contract, guarded_contract = spec.runes["qsJai"].policy.contract[:2]
    assert short_entry_contract.by == guarded_contract.by == 1
    assert short_entry_contract.entry == guarded_contract.entry == "x-height"
    assert short_entry_contract.when.left == Condition(family=("qsPea", "qsTea", "qsOut_qsTea"))
    assert guarded_contract.when.left == Condition(family=("qsThey", "qsHe"))


def test_scope_condition_parsing(spec):
    row = spec.runes["qsPea"].stances["half"].surface.exits["x-height"]
    assert row.ink_y == 6
    assert row.stub is not None and row.stub.cols == (3,) and row.stub.inks_when == "joined"
    (scope,) = row.scope
    assert scope.klass == ("can-enter-at-x-height",)
    assert scope.except_ == tuple(
        Condition(family=(name,)) for name in ("qsTea", "qsDay", "qsFee", "qsYe", "qsNo", "qsOwe")
    )
    top = spec.runes["qsTea"].stances["half"].surface.entries["top"]
    assert top.selectable is True
    grounded = spec.runes["qsMay"].stances["grounded-loop"].surface.entries["x-height"]
    assert grounded.joined is None and grounded.joined_x is None
    assert grounded.x == 2
    assert grounded.stub is not None and grounded.stub.cols == (3,) and grounded.stub.inks_when == "withdrawn"


def test_unlock_parsing(spec):
    unlocks = spec.runes["qsIt"].stances["hapax"].surface.unlocks
    assert len(unlocks) == 1
    (unlock,) = unlocks
    assert unlock.feature == "ss04"
    assert unlock.pairing.entry == "baseline" and unlock.pairing.exit == "baseline"
    assert unlock.when is None


def test_forbidden_stance_id(tmp_path):
    text = MINIMAL_RUNE.replace("hapax", "before-day")
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("pen motions" in issue.message for issue in error.issues)
    assert any("stances.before-day" in issue.path for issue in error.issues)


def test_lone_stance_must_be_hapax(tmp_path):
    text = textwrap.dedent("""\
        rune: qsIt
        codepoint: 0xE670
        ductus:
          hapax: |
            A vertical stroke.
        stances:
          bar:
            motion: hapax
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "single-stance rune must name its sole stance 'hapax'" in issue.message for issue in error.issues
    )
    assert any("stances.bar" in issue.path for issue in error.issues)


def test_hapax_stance_reserved_for_single_stance_rune(tmp_path):
    text = textwrap.dedent("""\
        rune: qsIt
        codepoint: 0xE670
        ductus:
          full: |
            A vertical stroke.
          grounded: |
            Another vertical stroke.
        stances:
          full:
            motion: full
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
          hapax:
            motion: grounded
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("reserved for the sole stance" in issue.message for issue in error.issues)
    assert any("stances.hapax" in issue.path for issue in error.issues)


def test_lone_motion_must_be_hapax(tmp_path):
    text = textwrap.dedent("""\
        rune: qsIt
        codepoint: 0xE670
        ductus:
          bar: |
            A vertical stroke.
        stances:
          hapax:
            motion: bar
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "single-motion ductus must name its sole motion 'hapax'" in issue.message for issue in error.issues
    )
    assert any("ductus.bar" in issue.path for issue in error.issues)


def test_hapax_motion_reserved_for_single_motion_ductus(tmp_path):
    text = textwrap.dedent("""\
        rune: qsIt
        codepoint: 0xE670
        ductus:
          full: |
            A vertical stroke.
          hapax: |
            Another vertical stroke.
        stances:
          full:
            motion: full
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
          grounded:
            motion: hapax
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("reserved for the sole motion" in issue.message for issue in error.issues)
    assert any("ductus.hapax" in issue.path for issue in error.issues)


def test_dangling_motion(tmp_path):
    text = MINIMAL_RUNE.replace("motion: hapax", "motion: pole")
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("not in the ductus" in issue.message for issue in error.issues)


def test_realized_motion_without_stance(tmp_path):
    text = MINIMAL_RUNE.replace("ductus:\n", "ductus:\n  pole: |\n    Another stroke.\n")
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("ductus parity" in issue.message for issue in error.issues)


def test_refuse_right_then_rejected(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          refuse:
          - {exit: baseline, when: {right: {family: qsDay, then: {family: qsMay}}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "right.then is forbidden" in issue.message and "decidable one position to the left" in issue.message
        for issue in error.issues
    )


def test_right_chain_two_hops_accepted(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - cell: {exit: none}
            over: {exit: baseline}
            when: {right: {family: qsDay, then: {family: qsMay, then: {family: qsIt}}}}
          - cell: {exit: none}
            over: {exit: baseline}
            when:
              right:
                family: [qsDay, qsMay]
                except: [{family: qsDay, then: {family: qsMay, then: {family: qsIt}}}]
        """)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        spec = load_tmp_spec(tmp_path, {"qsIt": text})
    prefer = spec.runes["qsIt"].policy.prefer
    right = prefer[0].when.right
    assert right is not None
    second = right.then
    assert second is not None
    third = second.then
    assert third is not None
    assert third.family == ("qsIt",)

    excepting = prefer[1].when.right
    assert excepting is not None
    second = excepting.except_[0].then
    assert second is not None
    third = second.then
    assert third is not None
    assert third.family == ("qsIt",)


def test_right_chain_three_hops_accepted(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - cell: {exit: none}
            over: {exit: baseline}
            when:
              right: {family: qsDay, then: {family: qsMay, then: {family: qsIt, then: {family: qsDay}}}}
        """)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        spec = load_tmp_spec(tmp_path, {"qsIt": text})
    right = spec.runes["qsIt"].policy.prefer[0].when.right
    assert right is not None
    second = right.then
    assert second is not None
    third = second.then
    assert third is not None
    fourth = third.then
    assert fourth is not None
    assert fourth.family == ("qsDay",)


def test_right_chain_four_hops_rejected(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - cell: {exit: none}
            over: {exit: baseline}
            when:
              right: {family: qsDay, then: {family: qsMay, then: {family: qsIt, then: {family: qsDay, then: {family: qsMay}}}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "at most three letters past" in issue.message and "policy.prefer[0].when.right" in issue.path
        for issue in error.issues
    )


def test_right_chain_hops_carried_by_except_count_toward_the_cap(tmp_path):
    rejected = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - cell: {exit: none}
            over: {exit: baseline}
            when:
              right:
                family: qsDay
                then:
                  family: qsMay
                  except: [{family: qsMay, then: {family: qsIt, then: {family: qsDay, then: {family: qsMay}}}}]
        """)
    error = load_tmp_error(tmp_path, {"qsIt": rejected})
    assert any(
        "at most three letters past" in issue.message and "policy.prefer[0].when.right" in issue.path
        for issue in error.issues
    )
    accepted = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - cell: {exit: none}
            over: {exit: baseline}
            when:
              right:
                family: qsDay
                then:
                  family: qsMay
                  except: [{family: qsMay, then: {family: qsIt, then: {family: qsDay}}}]
        """)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        spec = load_tmp_spec(tmp_path, {"qsIt": accepted})
    right = spec.runes["qsIt"].policy.prefer[0].when.right
    assert right is not None
    second = right.then
    assert second is not None
    third = second.except_[0].then
    assert third is not None
    fourth = third.then
    assert fourth is not None
    assert fourth.family == ("qsDay",)


def _right_then_chain(reach: int) -> dict:
    node: dict = {"family": "qsIt"}
    for _ in range(reach):
        node = {"family": "qsDay", "then": node}
    return node


def _rune_with_right_chain(reach: int) -> str:
    policy = {
        "policy": {
            "prefer": [
                {
                    "cell": {"exit": "none"},
                    "over": {"exit": "baseline"},
                    "when": {"right": _right_then_chain(reach)},
                }
            ]
        }
    }
    return MINIMAL_RUNE + yaml.safe_dump(policy, sort_keys=False)


def test_right_chain_cap_tracks_the_window_constant(tmp_path):
    assert model.RIGHT_CHAIN_CAP == model.RIGHT_WINDOW_SLOTS - 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        spec = load_tmp_spec(tmp_path, {"qsIt": _rune_with_right_chain(model.RIGHT_CHAIN_CAP)})
    assert spec.runes["qsIt"].policy.prefer[0].when.right is not None
    error = load_tmp_error(tmp_path, {"qsIt": _rune_with_right_chain(model.RIGHT_CHAIN_CAP + 1)})
    expected_word = spec_load._CAP_WORDS[model.RIGHT_CHAIN_CAP]
    assert any(
        f"at most {expected_word} letters past" in issue.message
        and "policy.prefer[0].when.right" in issue.path
        for issue in error.issues
    )


def test_unknown_family(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          refuse:
          - {exit: baseline, when: {right: {family: qsBogus}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("unknown family 'qsBogus'" in issue.message for issue in error.issues)


def test_unknown_class(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          refuse:
          - {exit: baseline, when: {right: {class: never-defined}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("unknown class 'never-defined'" in issue.message for issue in error.issues)


def test_closed_when_vocabulary(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          refuse:
          - {exit: baseline, when: {left2: {family: qsDay}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "unknown key 'left2'" in issue.message and "closed vocabulary" in issue.message
        for issue in error.issues
    )


def test_unlock_requires_exactly_one_grant(tmp_path):
    text = MINIMAL_RUNE.replace(
        "      exits:\n",
        "      unlocks:\n      - {feature: ss04, entry: baseline, exit: baseline}\n      exits:\n",
    )
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("exactly one" in issue.message for issue in error.issues)


def test_absolute_prefer_requires_why(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - {stance: hapax, mode: absolute, when: {left: {family: qsDay}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("'why'" in issue.message for issue in error.issues)


def test_trait_qualified_except_atom_rejected(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          refuse:
          - {exit: baseline, when: {right: {family: qsDay, except: [{family: qsMay, trait: half}]}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("trait" in issue.message and "not representable" in issue.message for issue in error.issues)


def test_run_splitting_boundaries_not_addressable_in_when(tmp_path):
    """The grammar half of the boundary-equals-text-edge guarantee: neither `is: zwnj` nor `is: space` is in the schema's boundaryValue enum, so no record can render a run-splitting boundary context differently from the same letters at a text edge. The rendering half is conform.check_split_buffer. The namer dot does not split runs and stays addressable."""
    for kind in ("zwnj", "space"):
        text = MINIMAL_RUNE + textwrap.dedent(f"""\
            policy:
              refuse:
              - {{exit: baseline, when: {{left: {{is: {kind}}}}}}}
            """)
        error = load_tmp_error(tmp_path, {"qsIt": text})
        enum_issues = [issue.message for issue in error.issues if f"got '{kind}'" in issue.message]
        assert enum_issues, kind
        assert all("'namer-dot'" in message for message in enum_issues), kind


def test_codepoint_must_match_registry(tmp_path):
    text = MINIMAL_RUNE.replace("codepoint: 0xE670", "codepoint: 0xE671")
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("disagrees with the registry" in issue.message for issue in error.issues)


def test_ambiguous_extend_target(tmp_path):
    text = textwrap.dedent("""\
        rune: qsIt
        codepoint: 0xE670
        ductus:
          bar: |
            A vertical stroke.
          pole: |
            Another vertical stroke.
        stances:
          bar:
            motion: bar
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
          pole:
            motion: pole
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
        policy:
          extend:
          - {exit: baseline, by: 1, when: {right: {family: qsDay}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("refuse-to-guess" in issue.message for issue in error.issues)


def test_errors_carry_lines_and_collect(tmp_path):
    text = MINIMAL_RUNE.replace("motion: hapax", "motion: pole") + textwrap.dedent("""\
        policy:
          refuse:
          - {exit: baseline, when: {right: {family: qsBogus}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert len(error.issues) >= 2
    refusal_line = text.splitlines().index("  - {exit: baseline, when: {right: {family: qsBogus}}}") + 1
    family_issue = next(issue for issue in error.issues if "qsBogus" in issue.message)
    assert family_issue.line == refusal_line
    assert family_issue.file.endswith("qsIt.yaml")


def test_duplicate_groups_flagged_across_files(tmp_path):
    group_block = textwrap.dedent("""\
        policy:
          groups:
            small-set: {union: [{family: qsDay}]}
        """)
    may_text = textwrap.dedent("""\
        rune: qsMay
        codepoint: 0xE665
        ductus:
          hapax: |
            A loop.
        stances:
          hapax:
            motion: hapax
            bitmap: ["#", "#", "#", "#", "#", "#"]
            surface:
              exits:
                baseline: {x: 1, withdrawal: safe}
        """)
    with pytest.warns(SpecWarning, match="identical membership"):
        load_tmp_spec(tmp_path, {"qsIt": MINIMAL_RUNE + group_block, "qsMay": may_text + group_block})


def test_resolve_floor_form_still_rejected(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          resolve:
          - {pick: {stance: hapax}, why: Recorded tie-break.}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("not yet implemented" in issue.message for issue in error.issues)


def test_rune_name_must_match_file_stem(tmp_path):
    error = load_tmp_error(tmp_path, {"qsDay": MINIMAL_RUNE})
    assert any("does not match its file name" in issue.message for issue in error.issues)


BROKEN_DOCUMENTS = (
    MINIMAL_RUNE.replace("rune: qsIt\n", ""),
    MINIMAL_RUNE.replace("codepoint: 0xE670", "codepoint: 0xE670\nsequence: [qsIt, qsDay]"),
    MINIMAL_RUNE.replace("hapax", "before-day"),
    MINIMAL_RUNE.replace("{x: 0}", "{x: 0, anchor: 3}"),
    MINIMAL_RUNE + "policy:\n  refuse:\n  - {exit: baseline, when: {left2: {family: qsDay}}}\n",
    MINIMAL_RUNE
    + "policy:\n  refuse:\n  - {exit: baseline, when: {right: {family: qsDay, then: {family: qsMay}}}}\n",
    MINIMAL_RUNE + "policy:\n  prefer:\n  - {stance: hapax, mode: absolute, when: {word: final}}\n",
)


def test_jsonschema_agrees_with_builtin_checker():
    jsonschema = pytest.importorskip("jsonschema")
    import json

    import yaml

    schema = json.loads((spec_load.DEFAULT_SCHEMA_DIR / "rune.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    checker = spec_load._SchemaChecker(schema, "rune.schema.json")
    for path in sorted(spec_load.DEFAULT_RUNES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        assert not list(validator.iter_errors(document)), path
        assert not checker.check(document), path
    script_schema = json.loads((spec_load.DEFAULT_SCHEMA_DIR / "script.schema.json").read_text())
    script_document = yaml.safe_load(spec_load.DEFAULT_REGISTRY_PATH.read_text())
    assert not list(jsonschema.Draft202012Validator(script_schema).iter_errors(script_document))
    assert not spec_load._SchemaChecker(script_schema, "script.schema.json").check(script_document)
    for text in BROKEN_DOCUMENTS:
        document = yaml.safe_load(text)
        assert list(validator.iter_errors(document)), text
        assert checker.check(document), text


def test_builtin_checker_rejects_broken_documents():
    import json

    import yaml

    schema = json.loads((spec_load.DEFAULT_SCHEMA_DIR / "rune.schema.json").read_text())
    checker = spec_load._SchemaChecker(schema, "rune.schema.json")
    for text in BROKEN_DOCUMENTS:
        assert checker.check(yaml.safe_load(text)), text


def test_ligature_transparency_expands_left_facing_family_lists(spec):
    """A family in an entry from-scope or a when.left admits every registered ligature whose sequence ends in it; toward-scopes, when.right, and except: lists stay literal, and predicate-class membership stays the ligature's own surface geometry. That a literal ligature name still outranks the expanded list it now appears in is the specificity order's claim rather than the loader's, stated over a registry of this shape by `specificity.rs`'s `a_ligature_family_name_ranks_as_an_ordinary_family`."""
    half_from = spec.runes["qsPea"].stances["half"].surface.entries["x-height"].scope
    utter_cond = next(cond for cond in half_from if "qsUtter" in cond.family)
    assert "qsDay_qsUtter" in utter_cond.family
    assert "qsSee_qsUtter" in utter_cond.family

    alt_from = spec.runes["qsUtter"].stances["alternate"].surface.entries["x-height"].scope
    assert set(alt_from[0].family) >= {"qsUtter", "qsDay_qsUtter", "qsSee_qsUtter"}

    vote = spec.runes["qsIt"].policy.prefer[0]
    assert vote.when.left is not None
    assert set(vote.when.left.family) == {"qsOy", "qsTea_qsOy"}
    assert vote.when.right is not None and vote.when.right.family == ("qsNo",)

    tea_half_from = spec.runes["qsTea"].stances["half"].surface.entries["x-height"].scope
    assert tea_half_from[0].except_[0].family == ("qsDay_qsUtter",)

    refuse = spec.runes["qsPea"].policy.refuse[0]
    assert refuse.when.right is not None
    assert "qsTea_qsOy" not in refuse.when.right.family

    classes = spec.registry.predicate_classes
    assert "qsSee_qsUtter" not in classes.get("can-exit-at-baseline", frozenset())
    assert "qsTea_qsOy" in classes.get("can-exit-at-baseline", frozenset())


# Real-YAML pins on the right-side chain grammar. The matcher that walks a chain is the crate's, and its deep-chain tests read their hops off synthetic specs; what stays on this side is the authored data those tests stand in for — which live records reach past their own slot, how far each reaches against the window cap, and the exact family scopes the orphaned-·Tea pins in rebuild/test_settle.py ride on. Every assertion below reads PolicyRecord.when straight off the loaded spec.


def _policy_records(spec):
    for rune_name, rune in spec.runes.items():
        for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
            for index, record in enumerate(getattr(rune.policy, kind)):
                yield f"{rune_name}.{kind}[{index}]", record


def _chain_reach(condition, depth: int = 0) -> int:
    """How many raw slots past its own a right condition reads, by the rule spec_load._right_chain_reach states over the raw YAML: a then: hop advances one slot, and an except: entry tests its parent's slot, so its own hops count from there."""
    if condition is None:
        return depth
    reach = depth
    for atom in condition.except_:
        reach = max(reach, _chain_reach(atom, depth))
    if condition.then is not None:
        reach = max(reach, _chain_reach(condition.then, depth + 1))
    return reach


def _chain_bearing_excepts(condition, found):
    """Every (parent, except entry) pair under one right condition where the entry carries a chain of its own."""
    if condition is None:
        return found
    for atom in condition.except_:
        if atom.then is not None:
            found.append((condition, atom))
        _chain_bearing_excepts(atom, found)
    return _chain_bearing_excepts(condition.then, found)


CHAIN_BEARING_EXCEPT_RECORDS = (
    ("qsDay.prefer[1]", 2),
    ("qsDay.prefer[5]", 3),
    ("qsGay.prefer[0]", 1),
    ("qsIt.prefer[1]", 1),
    ("qsIt.prefer[2]", 1),
    ("qsIt.prefer[3]", 1),
    ("qsMay.prefer[0]", 1),
    ("qsNo.prefer[5]", 1),
    ("qsOy.prefer[0]", 3),
    ("qsSee.prefer[0]", 1),
    ("qsTea_qsOy.prefer[0]", 3),
    ("qsUtter.prefer[2]", 1),
)


@pytest.mark.parametrize(
    "record_id,reach",
    CHAIN_BEARING_EXCEPT_RECORDS,
    ids=[row[0].replace("[", "").replace("]", "") for row in CHAIN_BEARING_EXCEPT_RECORDS],
)
def test_every_chain_bearing_except_walks_its_parents_tail(spec, record_id, reach):
    """The ten live records whose right condition hangs a chain off an except: entry, with the slot each one reaches. An except entry tests its parent's own slot rather than a deeper one, so a chain hung off it walks the tail its parent was already reading and its hops count against the same cap — which is what engine.rs's an_except_entry_carrying_a_chain_walks_the_same_tail and an_except_entry_carrying_a_four_hop_chain_walks_the_same_tail state over synthetic specs, and this is the authored data they stand in for. The census is asserted whole, so a newly authored chain cannot slip past the list."""
    records = dict(_policy_records(spec))
    carriers = {name for name, record in records.items() if _chain_bearing_excepts(record.when.right, [])}
    assert carriers == {row[0] for row in CHAIN_BEARING_EXCEPT_RECORDS}
    record = records[record_id]
    assert _chain_reach(record.when.right) == reach
    assert reach <= model.RIGHT_CHAIN_CAP
    for parent, atom in _chain_bearing_excepts(record.when.right, []):
        assert atom.then is not None
        assert not parent.family or set(atom.family) <= set(parent.family)


def test_the_qsday_depth_three_chains_both_hop_through_qsno(spec):
    """Three live records read a third raw slot off a then: spine, and two of them are qsDay's ·No windows: ·Day withholds its baseline exit before ·Tea·No when a joinable letter follows, and again when the word simply stops there. ·Oy and ·Tea+Oy reach exactly as deep for the same orphaned-·Tea phenomenon, but every hop of theirs hangs off an except: entry, so their spines carry no then: at all."""
    spines = {
        name
        for name, record in _policy_records(spec)
        if record.when.right is not None
        and record.when.right.then is not None
        and record.when.right.then.then is not None
    }
    assert spines == {"qsDay.prefer[3]", "qsDay.prefer[4]", "qsUtter.prefer[4]"}
    tails = []
    for record in spec.runes["qsDay"].policy.prefer[3:5]:
        right = record.when.right
        assert right is not None and right.then is not None and right.then.then is not None
        assert right.family == ("qsTea",)
        assert right.then.family == ("qsNo",)
        tails.append(right.then.then)
    assert tails[0].family == ("qsTea", "qsMay", "qsLow", "qsAh")
    assert tails[1].is_token == "boundary"
    for name in ("qsOy", "qsTea_qsOy"):
        right = spec.runes[name].policy.prefer[0].when.right
        assert right is not None and right.then is None
        assert _chain_reach(right) == model.RIGHT_CHAIN_CAP


def test_the_qsday_prefer_right_scopes_are_pinned(spec):
    """The two qsDay prefers the depth-3 orphaned-·Tea pins in rebuild/test_settle.py ride on: the ·Utter-scoped one, which only speaks for a ·Day nothing entered, and the broad follower list that withholds the exit before ·Tea and any of five joinable letters."""
    prefer = spec.runes["qsDay"].policy.prefer
    scoped = prefer[1].when.right
    assert scoped is not None and scoped.then is not None
    assert scoped.family == ("qsTea",)
    assert scoped.then.family == ("qsUtter",)
    assert prefer[1].when.self_entry == "none"
    broad = prefer[2].when.right
    assert broad is not None and broad.then is not None
    assert broad.family == ("qsTea",)
    assert broad.then.family == ("qsDay", "qsDay_qsUtter", "qsMay", "qsLow", "qsIt")


def test_resolve_record_slice_validation(tmp_path):
    floor = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          resolve:
          - at: {right: {family: qsDay}}
            pick: {exit: baseline}
            why: x
        """)
    assert "not yet implemented" in str(load_tmp_error(tmp_path, {"qsIt": floor}))

    dangling = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          resolve:
          - against: {rune: qsIt, id: no-such}
            when: {right: {family: qsDay}}
            pick: {exit: baseline}
            why: x
        """)
    assert "no record with id" in str(load_tmp_error(tmp_path, {"qsIt": dangling}))

    duplicate = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          refuse:
          - id: dup
            when: {right: {family: qsDay}}
          - id: dup
            when: {right: {family: qsMay}}
        """)
    assert "already used" in str(load_tmp_error(tmp_path, {"qsIt": duplicate}))


class TestStructureDigest:
    def test_stable_over_an_unchanged_spec(self):
        assert spec_load.spec_structure_digest(MINI_SPEC) == spec_load.spec_structure_digest(MINI_SPEC)

    def test_moves_when_the_alphabet_shrinks(self):
        smaller = replace(
            MINI_SPEC, runes={name: MINI_SPEC.runes[name] for name in list(MINI_SPEC.runes)[:-1]}
        )
        assert spec_load.spec_structure_digest(smaller) != spec_load.spec_structure_digest(MINI_SPEC)

    def test_moves_when_a_predicate_class_gains_a_member(self):
        classes = dict(MINI_SPEC.registry.predicate_classes)
        name, members = next(iter(classes.items()))
        classes[name] = frozenset(members | {"qsHapax"})
        widened = replace(MINI_SPEC, registry=replace(MINI_SPEC.registry, predicate_classes=classes))
        assert spec_load.spec_structure_digest(widened) != spec_load.spec_structure_digest(MINI_SPEC)


class TestRuneClosure:
    def test_every_rune_closes_over_itself(self):
        closure = spec_load.rune_closure(MINI_SPEC)
        assert set(closure) == set(MINI_SPEC.runes)
        assert all(name in names for name, names in closure.items())

    def test_a_resolve_against_reference_joins_the_closure(self):
        target = sorted(MINI_SPEC.runes)[0]
        owner = sorted(MINI_SPEC.runes)[1]
        rune = MINI_SPEC.runes[owner]
        record = PolicyRecord(kind="resolve", against=(target, None))
        patched = replace(rune, policy=replace(rune.policy, resolve=(record,)))
        spec = replace(MINI_SPEC, runes={**MINI_SPEC.runes, owner: patched})
        assert spec_load.rune_closure(spec)[owner] == {owner, target}
        assert spec_load.rune_closure(MINI_SPEC)[owner] == {owner}
