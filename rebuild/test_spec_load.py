"""spec_load unit tests: the real six-rune spec loads with the expected shapes, every lint fires with a file/path/line error, and the built-in schema evaluator agrees with jsonschema when it is available."""

import textwrap
import warnings
from pathlib import Path

import pytest

from rebuild.pipeline import spec_load
from rebuild.pipeline.model import Condition
from rebuild.pipeline.spec_load import SpecError, SpecWarning, load_default_spec, load_spec

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


def test_loads_all_six_runes(spec):
    assert sorted(spec.runes) == ["qsIt", "qsMay", "qsOy", "qsPea", "qsTea", "qsTea_qsOy"]
    assert set(spec.runes["qsMay"].stances) == {"loop", "grounded-loop"}
    assert set(spec.runes["qsTea"].stances) == {"full", "half"}
    assert spec.runes["qsTea"].stances["half"].traits == ("half",)
    assert spec.runes["qsPea"].stances["half"].traits == ("half",)
    assert spec.runes["qsTea_qsOy"].sequence == ("qsTea", "qsOy")
    assert spec.runes["qsTea_qsOy"].codepoint is None
    assert spec.runes["qsIt"].mono is not None
    assert spec.runes["qsTea_qsOy"].notes


def test_ductus_shapes(spec):
    may = spec.runes["qsMay"].ductus
    assert "clockwise" in may["loop"]
    assert spec.runes["qsIt"].ductus["hapax"].strip() == "- Either written from top to bottom or bottom to top."


def test_registry_contents(spec):
    registry = spec.registry
    assert registry.heights == {"baseline": 0, "x-height": 5, "y6": 6, "top": 8}
    assert registry.boundary_tokens["zwnj"].codepoint == 0x200C
    assert registry.boundary_tokens["namer-dot"].splits_runs is False
    assert registry.features["ss10"].kind == "taste"
    assert registry.interactions == (("ss02", "ss03"), ("ss02", "ss03", "ss05"))
    assert registry.families["qsOoze"].codepoint == 0xE67E
    assert registry.families["qsTea_qsOy"].sequence == ("qsTea", "qsOy")


def test_predicate_class_membership(spec):
    classes = spec.registry.predicate_classes
    assert classes["halves-that-exit-at-x-height"] == frozenset({"qsPea", "qsTea"})
    assert classes["can-enter-at-baseline"] == frozenset({"qsPea", "qsTea", "qsMay", "qsIt"})
    assert classes["can-enter-at-x-height"] == frozenset({"qsPea", "qsTea", "qsMay", "qsIt", "qsOy"})
    assert classes["can-exit-at-baseline"] == frozenset(
        {"qsPea", "qsTea", "qsMay", "qsIt", "qsOy", "qsTea_qsOy"}
    )
    assert classes["can-exit-at-x-height"] == frozenset({"qsPea", "qsTea", "qsMay", "qsIt"})
    assert classes["talls"] == frozenset({"qsPea", "qsTea"})
    assert classes["shorts"] == frozenset({"qsIt", "qsOy"})
    assert classes["deeps"] == frozenset({"qsMay"})


def test_group_resolution(spec):
    groups = spec.runes["qsIt"].policy.groups
    assert groups["utter-pass-through-vetoes"] == frozenset({"qsDay", "qsZoo", "qsShe", "qsYe", "qsOwe"})


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
    contract = spec.runes["qsNo"].policy.contract[0]
    assert contract.by == 1
    assert contract.exit == "x-height"
    assert contract.when.right == Condition(family=("qsJai",))


def test_scope_condition_parsing(spec):
    row = spec.runes["qsPea"].stances["half"].surface.exits["x-height"]
    assert row.ink_y == 6
    assert row.stub is not None and row.stub.cols == (3,) and row.stub.inks_when == "joined"
    (scope,) = row.scope
    assert scope.klass == ("can-enter-at-x-height",)
    assert scope.except_ == tuple(
        Condition(family=(name,)) for name in ("qsTea", "qsDay", "qsFee", "qsYe", "qsOwe")
    )
    top = spec.runes["qsTea"].stances["half"].surface.entries["top"]
    assert top.selectable is False
    grounded = spec.runes["qsMay"].stances["grounded-loop"].surface.entries["x-height"]
    assert grounded.joined == "pulled-back-grounded" and grounded.joined_x == 2


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
    assert any("single-stance rune must name its sole stance 'hapax'" in issue.message for issue in error.issues)
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
    assert any("single-motion ductus must name its sole motion 'hapax'" in issue.message for issue in error.issues)
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
    assert prefer[0].when.right.then.then.family == ("qsIt",)
    assert prefer[1].when.right.except_[0].then.then.family == ("qsIt",)


def test_right_chain_three_hops_rejected(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          prefer:
          - cell: {exit: none}
            over: {exit: baseline}
            when:
              right: {family: qsDay, then: {family: qsMay, then: {family: qsIt, then: {family: qsDay}}}}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "at most two letters past" in issue.message and "policy.prefer[0].when.right" in issue.path
        for issue in error.issues
    )


def test_right_chain_hops_carried_by_except_count_toward_the_cap(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
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
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any(
        "at most two letters past" in issue.message and "policy.prefer[0].when.right" in issue.path
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


def test_resolve_records_rejected(tmp_path):
    text = MINIMAL_RUNE + textwrap.dedent("""\
        policy:
          resolve:
          - {pick: {stance: hapax}, why: Recorded tie-break.}
        """)
    error = load_tmp_error(tmp_path, {"qsIt": text})
    assert any("resolve records" in issue.message for issue in error.issues)


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
