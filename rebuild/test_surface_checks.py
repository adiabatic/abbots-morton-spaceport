"""The invariants the surface build now enforces on itself, each shown refusing a surface that breaks it.

These predicates used to be tests that swept the live shards once a validators lane; they are checks inside `build_m1` now, which means they run over every shipped unit on every build and fail the build that produced them rather than a gate half an hour later. What that move costs is granularity — a violation arrives as one line in a `contract check failed` list rather than a named failing test — so what this module holds is the checker's own correctness: every predicate is exercised against the checked-in fixture surface, which passes as shipped, and then against one field broken at a time.
"""

import copy
import json
from pathlib import Path

import pytest

from rebuild.review import census
from rebuild.review.audit import UNMATCHED_CLASS, Unit
from rebuild.review import unit_index
from rebuild.review.build import (
    _check_output_files,
    _verification_sample,
    build_m1,
    check_manifest,
    check_shards,
    check_unit,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
SEAM_BEARER = "u-0004"
SEAM_HOME = "u-0005"


def _surface() -> tuple[dict, dict[str, list[dict]]]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    shards = {
        meta["id"]: json.loads((FIXTURES / meta["shard"]).read_text(encoding="utf-8"))
        for meta in manifest["classes"]
    }
    return copy.deepcopy(manifest), copy.deepcopy(shards)


def _unit(shards: dict[str, list[dict]], unit_id: str) -> dict:
    return next(unit for shard in shards.values() for unit in shard if unit["id"] == unit_id)


def _one(unit_id: str = "u-0000") -> dict:
    _manifest, shards = _surface()
    return _unit(shards, unit_id)


def _complaint(errors: list[str], needle: str) -> None:
    assert any(needle in error for error in errors), errors


def test_the_fixture_surface_passes_every_predicate():
    """The floor under everything else here: the checked-in miniature satisfies the whole contract as shipped, so every failure below is the broken field and not the fixture."""
    manifest, shards = _surface()
    assert check_manifest(manifest) == []
    assert check_shards(manifest, shards, REPO_ROOT) == []


# --- the drafts a reviewer would act on -------------------------------------------------------


def test_a_pin_that_does_not_parse_fails_the_build():
    unit = _one()
    unit["drafts"]["pin"]["syntax"] = "fail: Expected glyph token at pos 0"
    _complaint(check_unit(unit), "drafts.pin.syntax")


def test_a_pin_the_after_font_refutes_fails_the_build():
    unit = _one()
    unit["drafts"]["pin"]["semantics_after_font"] = "fail: seam mismatch at 0"
    _complaint(check_unit(unit), "drafts.pin.semantics_after_font")


def test_a_policy_draft_that_the_rune_schema_rejects_fails_the_build():
    unit = _one()
    unit["drafts"]["policy"]["schema_valid"] = False
    _complaint(check_unit(unit), "must validate against the rune schema")


def test_a_policy_draft_pointing_outside_the_three_keypaths_fails_the_build():
    unit = _one()
    unit["drafts"]["policy"]["keypath"] = "policy.resolve[+]"
    _complaint(check_unit(unit), "drafts.policy.keypath")


def test_a_policy_draft_naming_a_record_the_unit_never_saw_fails_the_build():
    unit = _one()
    unit["drafts"]["policy"]["names_provenance"] = ["glyph_data/runes/qsMay.yaml#/policy/refuse/9"]
    _complaint(check_unit(unit), "names_provenance")


def test_a_policy_draft_naming_a_file_that_is_not_in_the_repo_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, "u-0000")["drafts"]["policy"]["file"] = "glyph_data/runes/qsNotAletter.yaml"
    _complaint(check_shards(manifest, shards, REPO_ROOT), "which is not a file in the repo")


def test_repeated_any_of_candidates_fail_the_build():
    unit = _one()
    unit["drafts"]["any_of"]["candidates"] = ["·Tea+Oy", "·Tea+Oy"]
    _complaint(check_unit(unit), "must not repeat a behavior")


def test_an_any_of_candidate_that_does_not_parse_fails_the_build():
    unit = _one()
    unit["drafts"]["any_of"]["candidates"] = [unit["drafts"]["pin"]["expect"], "·Tea ~~~ ·Oy"]
    _complaint(check_unit(unit), "does not parse")


# --- the fields the app draws ------------------------------------------------------------------


def test_a_summary_without_its_new_clause_fails_the_build():
    unit = _one()
    unit["summary"] = "The ·Tea·Oy ligature forms."
    _complaint(check_unit(unit), "summary must open with the New: clause")


def test_a_multiline_summary_fails_the_build():
    unit = _one()
    unit["summary"] = unit["summary"] + "\nand another thing"
    _complaint(check_unit(unit), "summary must be one line")


def test_a_unit_whose_configs_render_two_ways_fails_the_build():
    unit = _one()
    configs = unit["configs"]
    unit["render_groups"] = [{"configs": configs[:1]}, {"configs": configs[1:]}]
    _complaint(check_unit(unit), "exactly one render group")


@pytest.mark.parametrize("side", ("before", "after"))
def test_a_highlight_reaching_past_the_run_fails_the_build(side):
    unit = _one()
    unit["highlight"][side]["x_max"] = unit["highlight"][side]["advance_total"] + 1
    _complaint(check_unit(unit), f"highlight.{side} must satisfy")


def test_a_secondary_seam_rect_reaching_past_the_run_fails_the_build():
    unit = _one(SEAM_BEARER)
    unit["secondary_seams"][0]["after"]["x_max"] = unit["secondary_seams"][0]["after"]["advance_total"] + 1
    _complaint(check_unit(unit), "secondary_seams[0].after must satisfy")


def test_a_gate_clause_the_manifest_does_not_gloss_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, "u-0000")["config_gate"][0]["feature"] = "ss99"
    _complaint(check_shards(manifest, shards), "feature_descriptions does not gloss")


# --- the grains that only exist across units ----------------------------------------------------


def test_an_echo_group_spanning_two_config_sets_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, "u-0001")["echo"] = _unit(shards, "u-0000")["echo"]
    _complaint(check_shards(manifest, shards), "one group spans")


def test_an_echo_group_spanning_two_clusters_fails_the_build():
    manifest, shards = _surface()
    left, right = _unit(shards, "u-0001"), _unit(shards, "u-0002")
    right["echo"] = left["echo"]
    right["cluster"] = "c-0badc0de"
    _complaint(check_shards(manifest, shards), "spans two clusters")


def test_a_cluster_spanning_two_classes_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, SEAM_BEARER)["cluster"] = _unit(shards, "u-0001")["cluster"]
    _complaint(check_shards(manifest, shards), "one signature spans")


def test_human_unit_ids_out_of_id_order_fails_the_build():
    manifest, shards = _surface()
    manifest["human_unit_ids"] = list(reversed(manifest["human_unit_ids"]))
    _complaint(check_shards(manifest, shards), "not the id-ordered sequence")


def test_a_batch_that_is_not_a_contiguous_slice_fails_the_build():
    manifest, shards = _surface()
    unit = _unit(shards, "u-0002")
    unit["batch"] = 4
    manifest["classes"][0]["batches"] = [0, 4]
    manifest["totals"]["batches"] = 2
    _complaint(check_shards(manifest, shards), "not contiguous slices")


def test_a_batch_count_the_shards_do_not_bear_out_fails_the_build():
    manifest, shards = _surface()
    manifest["totals"]["batches"] = 7
    _complaint(check_shards(manifest, shards), "totals.batches does not count")


def test_a_no_verdict_class_carrying_batches_fails_the_build():
    manifest, shards = _surface()
    manifest["classes"][0]["no_verdict"] = True
    _complaint(check_shards(manifest, shards), "no-verdict class must carry no batches")


# --- the secondary-seam home relation -----------------------------------------------------------


def _homed_surface() -> tuple[dict, dict[str, list[dict]]]:
    """The fixture with its one homed seam made resolver-shaped: a census present (which is what says the homes were assigned rather than hand-written), a home window that really is a substring of its bearer's, and a primary pair on the home. The fixture ships without a census precisely because its seam is hand-placed."""
    manifest, shards = _surface()
    manifest["secondary_seams"] = {
        "units_with_markers": 1,
        "seams_homed": 1,
        "seams_homeless": 0,
        "seams_suppressed_invisible": 0,
    }
    home = _unit(shards, SEAM_HOME)
    home["codepoints"] = "E679:E652"
    home["pair"] = {"left": 0, "right": 1}
    home["pair_codepoints"] = [0, 1]
    return manifest, shards


def test_a_resolver_shaped_home_passes():
    manifest, shards = _homed_surface()
    assert check_shards(manifest, shards) == []


def test_a_home_that_is_not_a_substring_window_fails_the_build():
    manifest, shards = _homed_surface()
    _unit(shards, SEAM_HOME)["codepoints"] = "E652:E670"
    _complaint(check_shards(manifest, shards), "is not a substring window")


def test_a_home_with_no_primary_pair_fails_the_build():
    manifest, shards = _homed_surface()
    home = _unit(shards, SEAM_HOME)
    home["pair"] = None
    home["pair_codepoints"] = None
    _complaint(check_shards(manifest, shards), "has no primary pair")


def test_a_home_with_nothing_to_see_fails_the_build():
    """An ink-identical home is what `seams_suppressed_invisible` counts; one that reached a shipped seam instead means the resolver's suppression did not fire."""
    manifest, shards = _homed_surface()
    home = _unit(shards, SEAM_HOME)
    home["ink_identical"] = True
    home["ink_deltas"] = {}
    home["batch"] = None
    home["echo"] = None
    home["cluster"] = None
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != SEAM_HOME]
    _complaint(check_shards(manifest, shards), "is ink-identical")


# --- the files beside the manifest --------------------------------------------------------------


def test_a_missing_unit_index_fails_the_build(tmp_path):
    """The plumbing reads the index and never the shards, so a surface that ships without one, or with one stamped for a manifest it does not describe, is a surface the next carry would resolve off a stale projection."""
    manifest = {"classes": [], "fonts": {}}
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _complaint(_check_output_files(tmp_path, manifest), "units-index.ndjson.gz is missing")
    unit_index.write_index(tmp_path, [])
    assert _check_output_files(tmp_path, manifest) == []
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    _complaint(_check_output_files(tmp_path, {"classes": [], "fonts": {}}), "stamped for another manifest")


def test_a_missing_or_empty_shard_fails_the_build(tmp_path):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    _complaint(_check_output_files(tmp_path, manifest), "is missing")
    (tmp_path / "units").mkdir()
    for meta in manifest["classes"]:
        (tmp_path / meta["shard"]).write_bytes(b"")
    _complaint(_check_output_files(tmp_path, manifest), "is empty")


def test_a_font_copy_that_is_not_its_source_fails_the_build(tmp_path):
    """The staleness this catches is a real one: a surface whose manifest is internally consistent, whose copied font is faithful to that manifest, and whose source has since been recompiled."""
    import hashlib

    source = Path("site") / "AbbotsMortonSpaceportSansSenior-Regular.otf"
    copied = tmp_path / "fonts" / "before.otf"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(b"not the font it says it is")
    manifest = {
        "classes": [],
        "fonts": {
            "before": {
                "file": "fonts/before.otf",
                "source": str(source),
                "sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
            }
        },
    }
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    unit_index.write_index(tmp_path, [])
    assert _check_output_files(tmp_path, manifest) == []
    _complaint(_check_output_files(tmp_path, manifest, REPO_ROOT), "as it stands on disk")


def test_a_build_without_its_baseline_subset_tables_refuses_before_it_starts(tmp_path):
    with pytest.raises(SystemExit) as raised:
        build_m1(
            tmp_path / "surface",
            audit_path=FIXTURES / "fixture-audit.tsv",
            ledger_path=FIXTURES / "fixture-ledger.yaml",
            subset_dir=tmp_path / "no-tables",
        )
    assert "baseline subset tables" in str(raised.value)


# --- the served-vs-recomputed sample ------------------------------------------------------------


def test_the_verification_sample_is_reproducible_and_bounded():
    served = [f"u-{index:04d}" for index in range(1000)]
    first = _verification_sample(served, "an-environment-stamp", 200)
    assert len(first) == 200
    assert set(first) <= set(served)
    assert first == _verification_sample(served, "an-environment-stamp", 200)
    assert first != _verification_sample(served, "a-different-stamp", 200)
    assert _verification_sample([], "an-environment-stamp") == []
    assert sorted(_verification_sample(served[:5], "an-environment-stamp", 200)) == served[:5]


# --- the census projection ----------------------------------------------------------------------


def test_the_premerge_projection_answers_one_ink_flag_per_captured_unit():
    """What makes an index into `ink_flags` mean anything: the flags run parallel to the capture, which is the pre-merge grain the census pins are defined over. `derive_premerge` now says so itself rather than leaving a sweep over the live sidecar to discover otherwise. (The companion claim — that no unit at a family index is ink-identical — is a fact about the corpus, not about the projection, and lives in `build_m1` where the corpus is.)"""
    units = [
        Unit(
            codepoints=codepoints,
            baseline=("qsPea", "qsMay"),
            new=("qsPea/full/None/baseline/", "qsMay/full/baseline/None/"),
            class_id=UNMATCHED_CLASS,
            rows=(),
            configs=("default",),
            unit_id=f"u-{index:04d}",
            family_id="a-family",
        )
        for index, codepoints in enumerate(("E650:E665", "E650:E652"))
    ]
    capture = census.capture_premerge(units)
    facts = census.derive_premerge(capture, units)
    assert len(facts.ink_flags) == facts.units == len(units)
    assert [index for index, _family in facts.families] == [0, 1]
