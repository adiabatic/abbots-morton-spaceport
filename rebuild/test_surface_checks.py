"""Tests for the review surface build's contract checks, each run against a surface that breaks one predicate, and for the drafters and packers that raise instead of producing a bad value.

`build_m1` runs `check_unit` over every unit it computes and runs the cross-unit predicates of `check_shards` through `_SurfaceCheck`, so a violation fails the build that produced it. A cache-served unit skips `check_unit` and is covered instead by the `content_key` stamp that its shard and its store record must agree on; the cross-unit predicates run over both kinds. The manifest-shape predicates (`check_manifest`) and the file predicates (`_check_output_files`) do not run in `build_m1`, because a build writes every field they read from its own inputs; `check_output_dir` runs them over a real m1 build of the frozen mini bundle in `rebuild/test_app_index.py`. A violation appears only as one line in a `contract check failed` list, not as a named failing test, so this module tests the checker itself. It runs `check_manifest` and `check_shards` over the checked-in fixture surface, which passes as shipped and carries no fonts, index page, or sidecars, and then with one field broken at a time. It runs the file predicates over small surfaces under tmp_path with one file missing or wrong. The drafter tests run over the frozen mini bundle; the highlight and subset-pack tests use small synthetic inputs, plus one check over the mini bundle's real tables.
"""

import copy
import gzip
import json
import shutil
import sys
import warnings
from dataclasses import replace
from pathlib import Path

import pytest

from rebuild.review import app_index, census, drafts, unit_index
from rebuild.review import build as review_build
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    AUDIT_HEADER,
    SLIM_OMITTED_KEYS,
    UNMATCHED_CLASS,
    AuditRow,
    load_table,
    load_workload,
)
from rebuild.review.build import (
    _HELD_SCAFFOLD_KEYS,
    _SCAFFOLD_HEAD,
    _SCAFFOLD_TAIL,
    DRAFTED,
    PATCHED,
    _check_output_files,
    _copy_font,
    _sha256,
    _verification_sample,
    _write_json,
    build_m1,
    build_table_diff,
    check_manifest,
    check_output_dir,
    check_shards,
    check_unit,
)
from rebuild.review.drafts import DraftError, Drafter
from rebuild.review.enrich import LETTERS, Enricher, _highlight, load_spec
from rebuild.review.subset_pack import SubsetPack, SubsetRow, pack_key, table_digests, write_pack
from rebuild.review.unit_store import UnitStore
from rebuild.validation.rowmodel import Row, iter_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
MINI = FIXTURES / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"
# How many of the bundle's windows the drafter tests enrich: enough to include each shape those tests ask for, and few enough for one settlement pass.
MINI_SLICE = 64
SEAM_BEARER = "u-WJSK8gMxjxy"
SEAM_HOME = "u-Ng8Npb18Kha"
PLAIN_UNIT = "u-DdcTojn1hba"
ECHO_MATE = "u-8nacGTcgMRS"
THIRD_UNIT = "u-2WvdGAWe6bX"


def _surface() -> tuple[dict, dict[str, list[dict]]]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    shards = {
        meta["id"]: [
            unit
            for part in unit_index.class_shards(meta)
            for unit in json.loads((FIXTURES / part).read_text(encoding="utf-8"))
        ]
        for meta in manifest["classes"]
    }
    return copy.deepcopy(manifest), copy.deepcopy(shards)


def _unit(shards: dict[str, list[dict]], unit_id: str) -> dict:
    return next(unit for shard in shards.values() for unit in shard if unit["id"] == unit_id)


def _one(unit_id: str = PLAIN_UNIT) -> dict:
    _manifest, shards = _surface()
    return _unit(shards, unit_id)


def _complaint(errors: list[str], needle: str) -> None:
    assert any(needle in error for error in errors), errors


def _sidecars(surface: Path) -> None:
    """Writes the stamped sidecars the output check requires beside a manifest (the plumbing's unit index and the files in `app_index.ARTIFACTS`), so a test about one missing file does not also fail on the others."""
    unit_index.write_index(surface, [])
    app_index.write_app_artifacts(surface, {}, {})


def test_the_fixture_surface_passes_every_predicate():
    """The checked-in fixture surface passes every predicate as shipped, so each failure in the tests below comes from the field that test breaks."""
    manifest, shards = _surface()
    assert check_manifest(manifest) == []
    assert check_shards(manifest, shards, REPO_ROOT) == []


# --- the drafts a reviewer would act on -------------------------------------------------------

# The drafter raises `DraftError` for a draft no reviewer could use, instead of recording a `fail: …` value for `check_unit` to reject later. These tests run over the frozen mini bundle (real windows, a real after font, a real settlement), because a drafter can only be wrong about a window it actually drafted.


@pytest.fixture(scope="module")
def mini_enriched(mini_bundle):
    """The first MINI_SLICE units of the bundle's workload, enriched under the spec they were settled with. Enrichment is the expensive step, so every drafter test shares this slice; each test needs only some window of the shape it asks for."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    enricher = Enricher(spec, MINI, MINI_FONT, repo_root=REPO_ROOT, subset_pack=mini_bundle.subset_pack)
    workload = load_workload(MINI_AUDIT, mini_bundle.ledger, dict(LETTERS))
    return enricher.enrich_many(workload.units()[:MINI_SLICE])


@pytest.fixture(scope="module")
def mini_drafter():
    return Drafter(MINI_FONT, repo_root=REPO_ROOT)


@pytest.fixture(scope="module")
def joined_unit(mini_enriched):
    """A window whose first after-seam is `break` or `y0`, so a test can flip it to the other and know the after font refutes the flipped pin."""
    return next(unit for unit in mini_enriched if unit.after_seams[:1] in (("break",), ("y0",)))


@pytest.fixture(scope="module")
def policy_unit(mini_enriched, mini_drafter):
    return next(unit for unit in mini_enriched if mini_drafter.draft_policy(unit) is not None)


def test_a_pin_the_after_font_refutes_is_never_drafted(mini_drafter, joined_unit):
    """A pin drafted from a real window passes against the after font. A pin drafted from the same window with its first seam flipped raises DraftError instead of being shipped with a recorded failure."""
    assert mini_drafter.draft_pin(joined_unit).semantics_after_font == "pass"
    flipped = ("y0" if joined_unit.after_seams[0] == "break" else "break", *joined_unit.after_seams[1:])
    with pytest.raises(DraftError) as raised:
        mini_drafter.draft_pin(replace(joined_unit, after_seams=flipped))
    assert "after font" in str(raised.value)


def test_a_pin_that_does_not_parse_is_never_drafted(monkeypatch, mini_drafter, joined_unit):
    monkeypatch.setattr(drafts, "expect_string", lambda *_args: "·Tea ~~~ ·Oy")
    with pytest.raises(DraftError) as raised:
        mini_drafter.draft_pin(joined_unit)
    assert "does not parse" in str(raised.value)


def test_a_policy_record_the_rune_schema_rejects_is_never_drafted(monkeypatch, mini_drafter, policy_unit):
    """Which of the three schema checkers the drafter uses depends on the window, so all three are made to fail. The test checks that a record the schema rejects raises DraftError instead of being recorded with `schema_valid` false."""
    for name in ("_refuse_checker", "_prefer_checker", "_contract_checker"):
        monkeypatch.setattr(getattr(mini_drafter, name), "check", lambda _record: ["boom"])
    with pytest.raises(DraftError) as raised:
        mini_drafter.draft_policy(policy_unit)
    assert "rune schema" in str(raised.value)


def test_an_any_of_candidate_that_does_not_parse_is_never_drafted(monkeypatch, mini_drafter, joined_unit):
    """The before-behavior candidate is the only string in the any-of record that nothing else checks (the pin covers the after behavior), so the drafter parses it when it writes it."""
    real = drafts.expect_string
    calls: list[int] = []

    def once_then_garbage(*args):
        calls.append(1)
        return real(*args) if len(calls) == 1 else "·Tea ~~~ ·Oy"

    monkeypatch.setattr(drafts, "expect_string", once_then_garbage)
    with pytest.raises(DraftError) as raised:
        mini_drafter.draft_any_of(joined_unit)
    assert "does not parse" in str(raised.value)


def test_a_policy_draft_naming_a_file_that_is_not_in_the_repo_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, PLAIN_UNIT)["drafts"]["policy"]["file"] = "glyph_data/runes/qsNotAletter.yaml"
    _complaint(check_shards(manifest, shards, REPO_ROOT), "which is not a file in the repo")


# --- the slim machine-approved and no-verdict shape ----------------------------------------------

SLIM_UNIT = "u-hRgMc2EJjbs"


@pytest.mark.parametrize("key", SLIM_OMITTED_KEYS)
def test_a_slim_unit_carrying_explain_material_fails_the_build(key):
    unit = _one(SLIM_UNIT)
    assert key not in unit
    unit[key] = _one()[key]
    _complaint(check_unit(unit), f"omit {key}")


@pytest.mark.parametrize("key", SLIM_OMITTED_KEYS)
def test_a_slim_unit_carrying_an_emptied_field_fails_the_build(key):
    """A slim fragment omits these keys; it never sets them to null. The app would read a null field on a slim fragment as a full record with a blank field."""
    unit = _one(SLIM_UNIT)
    unit[key] = None
    _complaint(check_unit(unit), f"omit {key}")


@pytest.mark.parametrize("key", SLIM_OMITTED_KEYS)
def test_a_human_unit_without_its_explain_material_fails_the_build(key):
    unit = _one()
    del unit[key]
    _complaint(check_unit(unit), key)


def test_a_human_unit_with_drafts_null_fails_the_build():
    unit = _one()
    unit["drafts"] = None
    _complaint(check_unit(unit), "drafts must carry pin/policy/any_of")


@pytest.mark.parametrize("flag", ("picture_identical", "junior_equivalent", "no_verdict"))
def test_every_machine_channel_and_the_exemption_take_the_slim_shape(flag):
    """Every unit that takes no verdict is slim: a picture-identical unit, a Junior-equivalent unit, and a unit in a no-verdict class each pass without the explain material, and each fails the build when it carries drafts."""
    unit = _one(SLIM_UNIT)
    deltas = {} if flag == "picture_identical" else {"ss02": "d-000000000000"}
    unit.update(ink_identical=False, ink_deltas=deltas, **{flag: True})
    assert check_unit(unit) == []
    unit["drafts"] = _one()["drafts"]
    _complaint(check_unit(unit), "omit drafts")


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
    _unit(shards, PLAIN_UNIT)["config_gate"][0]["feature"] = "ss99"
    _complaint(check_shards(manifest, shards), "feature_descriptions does not gloss")


# --- the grains that only exist across units ----------------------------------------------------


def test_an_echo_group_spanning_two_config_sets_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, ECHO_MATE)["echo"] = _unit(shards, PLAIN_UNIT)["echo"]
    _complaint(check_shards(manifest, shards), "one group spans")


def test_an_echo_group_spanning_two_clusters_fails_the_build():
    manifest, shards = _surface()
    left, right = _unit(shards, ECHO_MATE), _unit(shards, THIRD_UNIT)
    right["echo"] = left["echo"]
    right["cluster"] = "c-0badc0de"
    _complaint(check_shards(manifest, shards), "spans two clusters")


def test_a_cluster_spanning_two_classes_fails_the_build():
    manifest, shards = _surface()
    _unit(shards, SEAM_BEARER)["cluster"] = _unit(shards, ECHO_MATE)["cluster"]
    _complaint(check_shards(manifest, shards), "one signature spans")


def test_human_unit_ids_out_of_triage_order_fails_the_build():
    """`human_unit_ids` lists the human units in triage order (class, group, window, id). A fragment carries no position, so the checker derives the order from the fragments with `triage_key` and compares the manifest with it."""
    manifest, shards = _surface()
    manifest["human_unit_ids"] = list(reversed(manifest["human_unit_ids"]))
    _complaint(check_shards(manifest, shards), "not the triage-ordered sequence")


def test_a_class_claiming_a_batch_its_units_do_not_occupy_fails_the_build():
    manifest, shards = _surface()
    manifest["classes"][0]["batches"] = [0, 4]
    _complaint(check_shards(manifest, shards), "are not the slices")


def test_a_batch_count_the_index_does_not_bear_out_fails_the_build():
    manifest, shards = _surface()
    manifest["totals"]["batches"] = 7
    _complaint(check_shards(manifest, shards), "totals.batches does not count")


def test_a_fragment_carrying_a_batch_fails_the_build():
    """A fragment's bytes depend only on its content and the ledger, which is what lets a served fragment be copied unchanged. A batch is a position in the queue, so it is recorded in the manifest's index."""
    unit = _one()
    unit["batch"] = 0
    _complaint(check_unit(unit), "carries no batch")


def test_an_id_that_is_not_its_stamps_fails_the_build():
    """The id is the content key's first 64 bits in base58. The check fails a fragment whose id belongs to different content than its stamp, and an id of any other form."""
    unit = _one()
    unit["id"] = _one(ECHO_MATE)["id"]
    _complaint(check_unit(unit), "must be the content key's own")
    unit["id"] = "u-0000"
    _complaint(check_unit(unit), "base58")


def test_a_no_verdict_class_carrying_batches_fails_the_build():
    manifest, shards = _surface()
    manifest["classes"][0]["no_verdict"] = True
    _complaint(check_shards(manifest, shards), "no-verdict class must carry no batches")


# --- the secondary-seam home relation -----------------------------------------------------------


def _homed_surface() -> tuple[dict, dict[str, list[dict]]]:
    """Returns the fixture with its one homed seam made to look like the resolver's output: a `secondary_seams` census in the manifest, which marks the homes as resolver-assigned, a home window that is a substring of the bearer's window, and a primary pair on the home. The fixture ships without a census because its seam is placed by hand."""
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
    """The resolver counts a seam whose home is ink-identical in `seams_suppressed_invisible` instead of shipping it. A shipped seam with such a home means that suppression did not happen."""
    manifest, shards = _homed_surface()
    home = _unit(shards, SEAM_HOME)
    home["ink_identical"] = True
    home["ink_deltas"] = {}
    home["echo"] = None
    home["cluster"] = None
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != SEAM_HOME]
    _complaint(check_shards(manifest, shards), "shows no visible change")


def test_a_picture_identical_home_fails_the_build_the_same_way():
    """A picture-identical home also shows no visible change: its delta is empty under every config, so the resolver should have suppressed the seam."""
    manifest, shards = _homed_surface()
    home = _unit(shards, SEAM_HOME)
    home["picture_identical"] = True
    home["ink_deltas"] = {}
    home["echo"] = None
    home["cluster"] = None
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != SEAM_HOME]
    _complaint(check_shards(manifest, shards), "shows no visible change")


# --- the files beside the manifest --------------------------------------------------------------


def test_a_missing_unit_index_fails_the_build(tmp_path):
    """The plumbing reads the unit index, not the shards. A surface without an index, or with one stamped for another manifest, would make the next carry read stale data."""
    manifest = {"classes": [], "fonts": {}}
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _complaint(_check_output_files(tmp_path, manifest), "units-index.ndjson.gz is missing")
    _sidecars(tmp_path)
    assert _check_output_files(tmp_path, manifest) == []
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    _complaint(_check_output_files(tmp_path, {"classes": [], "fonts": {}}), "stamped for another manifest")


def test_a_missing_or_empty_shard_fails_the_build(tmp_path):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    _complaint(_check_output_files(tmp_path, manifest), "is missing")
    (tmp_path / "units").mkdir()
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            (tmp_path / part).write_bytes(b"")
    _complaint(_check_output_files(tmp_path, manifest), "is empty")


def _split_first_class(surface: Path) -> None:
    """Rewrite the surface's first class as two numbered parts, which is the layout a class past the byte cap ships in."""
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    meta = manifest["classes"][0]
    (whole,) = unit_index.class_shards(meta)
    units = json.loads((surface / whole).read_text(encoding="utf-8"))
    chunks = (units[:1], units[1:])
    meta["shards"] = [f"units/{meta['id']}.{index:03d}.json" for index in range(len(chunks))]
    for part, chunk in zip(meta["shards"], chunks, strict=True):
        _write_json(surface / part, chunk)
    (surface / whole).unlink()
    _write_json(surface / "manifest.json", manifest)


def test_a_class_written_as_parts_reads_back_as_the_same_class(tmp_path):
    """`check_output_dir` concatenates a class's parts in order before checking, so it reports the same errors whether a class is one file or several."""
    plain = tmp_path / "plain"
    shutil.copytree(FIXTURES, plain, ignore=shutil.ignore_patterns("mini", "*.md", "*.tsv", "*.yaml"))
    split = tmp_path / "split"
    shutil.copytree(plain, split)
    _split_first_class(split)
    assert check_output_dir(split) == check_output_dir(plain)
    assert not any("shard" in error for error in check_output_dir(split))


def test_every_part_of_a_split_class_must_be_present_and_non_empty(tmp_path):
    """A build that wrote part 000 and died before 001 must not pass as complete, so the check reads every part."""
    manifest = {
        "classes": [{"id": "big", "shards": ["units/big.000.json", "units/big.001.json"]}],
        "fonts": {},
    }
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _sidecars(tmp_path)
    _complaint(_check_output_files(tmp_path, manifest), "units/big.000.json is missing")
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "big.000.json").write_text("[]", encoding="utf-8")
    _complaint(_check_output_files(tmp_path, manifest), "units/big.001.json is missing")
    (tmp_path / "units" / "big.001.json").write_bytes(b"")
    _complaint(_check_output_files(tmp_path, manifest), "units/big.001.json is empty")
    (tmp_path / "units" / "big.001.json").write_text("[]", encoding="utf-8")
    assert _check_output_files(tmp_path, manifest) == []


def test_a_font_copy_that_is_not_its_source_fails_the_build(tmp_path):
    """The copied font matches the manifest's sha256, but its source has since been recompiled. Only the check with `repo_root` catches this."""
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
    _sidecars(tmp_path)
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


def test_an_empty_audit_refuses_before_any_unit_is_built(tmp_path, mini_bundle):
    """An audit in which every window matches, the rebuild's intended end state, leaves the build nothing to do. The build exits naming the audit when it reads the rows, instead of writing a manifest with an empty `classes` list minutes later."""
    audit_path = tmp_path / "audit.tsv"
    audit_path.write_text("\t".join(AUDIT_HEADER) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        build_m1(
            tmp_path / "surface",
            audit_path=audit_path,
            ledger_path=mini_bundle.ledger,
            subset_dir=MINI,
            after_font=MINI_FONT,
            spec_root=mini_bundle.spec_root,
            subset_pack=mini_bundle.subset_pack,
            jobs=1,
        )
    assert str(audit_path) in str(raised.value)


def test_identical_table_directories_refuse_to_diff(tmp_path):
    """The table-diff build exits the same way: two directories that settle every window alike have no diff to review."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for name in ("settlement-default.tsv", "treaties-default.tsv"):
        shutil.copyfile(MINI / name, old_dir / name)
        shutil.copyfile(MINI / name, new_dir / name)
    with pytest.raises(SystemExit) as raised:
        build_table_diff(
            tmp_path / "out",
            old_dir,
            new_dir,
            REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
            MINI_FONT,
            with_witnesses=False,
        )
    assert str(old_dir) in str(raised.value)
    assert str(new_dir) in str(raised.value)


def test_a_font_that_moved_since_load_fails_the_copy(tmp_path):
    """`_copy_font` compares the copy it ships with the digest the build took when it loaded the font. Without this, a run_m1 that finished between the load and the copy would leave units that describe the old font beside an `after.otf` that is the new one."""
    digest = _sha256(MINI_FONT)
    record = _copy_font(MINI_FONT, tmp_path, "after.otf", "AMS Review After", REPO_ROOT, digest)
    assert record["sha256"] == digest
    with pytest.raises(SystemExit) as raised:
        _copy_font(MINI_FONT, tmp_path, "after.otf", "AMS Review After", REPO_ROOT, "0" * 64)
    assert str(MINI_FONT) in str(raised.value)


def test_a_manifest_with_no_classes_draws_no_complaint():
    """`check_manifest` accepts an empty `classes` list. The build exits before it would write one (see the tests above), and a checker that failed on it would fail on the rebuild's intended end state."""
    manifest, _shards = _surface()
    manifest["classes"] = []
    assert not [error for error in check_manifest(manifest) if "classes" in error]


# --- the served-vs-recomputed sample ------------------------------------------------------------


def test_the_verification_sample_is_reproducible_and_bounded():
    served = list(range(1000))
    first = _verification_sample(served, "an-environment-stamp", 200)
    assert len(first) == 200
    assert set(first) <= set(served)
    assert first == _verification_sample(served, "an-environment-stamp", 200)
    assert first != _verification_sample(served, "a-different-stamp", 200)
    assert _verification_sample([], "an-environment-stamp") == []
    assert sorted(_verification_sample(served[:5], "an-environment-stamp", 200)) == served[:5]


# --- what the emitters refuse to produce --------------------------------------------------------


def test_a_highlight_rect_is_refused_where_the_pens_run_backwards():
    """`_highlight` requires non-decreasing pen positions. Those always give `x_min <= x_max <= advance_total`, and a shaped run whose pens go backwards should not get a highlight band."""
    with pytest.raises(ValueError) as raised:
        _highlight([0, 10, 5], [(0, 1), (1, 2)], 0, 1)
    assert "non-decreasing" in str(raised.value)
    assert _highlight([0, 5, 10], [(0, 1), (1, 2)], 0, 1) == {
        "x_min": 0,
        "x_max": 10,
        "advance_total": 10,
    }


def _subset_row(codepoints: tuple[int, ...], seams: tuple[str, ...]) -> Row:
    return Row(
        codepoints=codepoints,
        glyphs=tuple({0xE650: "qsPea", 0xE652: "qsTea"}[value] for value in codepoints),
        clusters=tuple(range(len(codepoints))),
        seams=seams,
        positions=tuple((0, 0, 10 + 2 * index) for index in range(len(codepoints))),
    )


def _subset_table(path: Path, *rows: Row) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        for row in rows:
            stream.write(row.to_tsv() + "\n")
    return path


def _pack(tmp_path: Path, *configs: str) -> SubsetPack:
    """Packs the named configurations' tables under `tmp_path` and opens the pack, checking its header against digests of the same tables."""
    digests = table_digests(tmp_path, configs)
    return SubsetPack.open(write_pack(tmp_path, configs, digests, tmp_path / "subsets.pack"), digests)


def test_a_baseline_row_outside_the_seam_vocabulary_is_refused_by_the_packer(tmp_path):
    """`SeamClassifier.classify` can emit a compound token naming two heights, and a shard's `before.seams` cannot represent one. The packer checks every row of every table it packs, so a compound token on any row fails the table before a pack exists, whichever rows the workers later look up. Nothing is renamed into place on a failure, so no partial pack is left behind."""
    pair = (0xE650, 0xE652)
    _subset_table(tmp_path / "baseline-clean.subset.tsv.gz", _subset_row(pair, ("y0",)))
    clean = _pack(tmp_path, "clean")
    assert [codepoints for codepoints, _row in clean.rows("clean")] == ["E650:E652"]
    clean.close()
    _subset_table(
        tmp_path / "baseline-compound.subset.tsv.gz",
        _subset_row(pair, ("y0",)),
        _subset_row((0xE650, 0xE652, 0xE650), ("y0", "y0+y5")),
    )
    digests = table_digests(tmp_path, ("clean", "compound"))
    with pytest.raises(ValueError) as raised:
        write_pack(tmp_path, ("clean", "compound"), digests, tmp_path / "refused.pack")
    assert "'y0+y5'" in str(raised.value)
    assert "E650:E652:E650" in str(raised.value)
    assert str(tmp_path / "baseline-compound.subset.tsv.gz") in str(raised.value)
    assert not (tmp_path / "refused.pack").exists()
    assert not list(tmp_path.glob("refused.pack.tmp-*"))


def test_a_packed_row_is_the_projection_the_enricher_reads(tmp_path):
    """A lookup returns a `SubsetRow` with only the glyphs, clusters, and seams of the parsed `Row`: `positions` is not read on this path and `codepoints` is the key. Its strings come from the pack's interned string table, so a glyph name or seam token is one object however many rows use it, and the same object `sys.intern` returns elsewhere in the process. A window the table does not hold, or a configuration the pack does not hold, returns None."""
    first = _subset_row((0xE650, 0xE652), ("y0",))
    second = _subset_row((0xE652, 0xE650), ("y0",))
    _subset_table(tmp_path / "baseline-two.subset.tsv.gz", first, second)
    pack = _pack(tmp_path, "two")
    projected = pack.row("two", "E650:E652")
    assert isinstance(projected, SubsetRow)
    assert (projected.glyphs, projected.clusters, projected.seams) == (
        first.glyphs,
        first.clusters,
        first.seams,
    )
    assert not hasattr(projected, "positions")
    assert not hasattr(projected, "codepoints")
    other = pack.row("two", "E652:E650")
    assert other is not None
    assert other.glyphs[0] is projected.glyphs[1]
    assert other.seams[0] is projected.seams[0]
    assert projected.glyphs[0] is sys.intern("qsPea") and projected.seams[0] is sys.intern("y0")
    assert pack.row("two", "E650:E650") is None
    assert pack.row("default", "E650:E652") is None
    assert pack.row("two", "E650:E652:E650:E652:E650") is None
    assert pack.row("two", "10000:E652") is None
    assert pack.census() == (2, pack.census()[1]) and pack.census()[1] > 0
    pack.close()


def test_a_window_no_key_can_spell_is_refused_at_the_packer(tmp_path):
    """A key is four 16-bit slots, and `pack_key` checks both bounds. A fifth codepoint does not fit. A codepoint past the Basic Multilingual Plane would spill into its neighbor's slot and give two windows one key (`10000:0020` and `0001:0000:0020`), so `pack_key` raises instead of masking it. A table holding such a window never becomes a pack."""
    with pytest.raises(ValueError) as wide:
        pack_key("E650:E652:E650:E652:E650")
    assert "5 codepoints" in str(wide.value)
    with pytest.raises(ValueError) as tall:
        pack_key("10000:0020")
    assert "10000" in str(tall.value)
    assert pack_key("0001:0000:0020") == 0x100000020
    with gzip.open(tmp_path / "baseline-astral.subset.tsv.gz", "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        stream.write("10000:E652\tqsPea|qsTea\t0,1\ty0\t0,0,10|0,0,12\n")
    digests = table_digests(tmp_path, ("astral",))
    with pytest.raises(ValueError):
        write_pack(tmp_path, ("astral",), digests, tmp_path / "astral.pack")
    assert not (tmp_path / "astral.pack").exists()


@pytest.mark.parametrize(
    "path", sorted(MINI.glob("baseline-*.subset.tsv.gz")), ids=lambda path: path.name.split(".")[0]
)
def test_the_pack_drops_nothing_the_enricher_reads_from_a_real_table(path: Path, mini_bundle):
    """For every table the frozen bundle ships, all packed into the bundle's pack, the rows read back match what `rowmodel.Row` parses: the pack's keys, in sorted order, are the table's keys, and each has the same glyphs, clusters, and seams. `iter_rows` is the reference because the packer never builds a `Row`; it splits each line and reads three fields, so the row model's parse is an independent reading. The synthetic tests above check the format; this one checks tables the extractor wrote, including ligature rows and boundary tokens."""
    config = path.name.split(".")[0].removeprefix("baseline-")
    pack = SubsetPack.open(mini_bundle.subset_pack, table_digests(MINI, ACCEPTANCE_CONFIGS))
    packed = list(pack.rows(config))
    parsed = {":".join(f"{value:04X}" for value in row.codepoints): row for row in iter_rows(path)}
    keys = [codepoints for codepoints, _row in packed]
    assert keys == sorted(keys, key=pack_key) and set(keys) == set(parsed) and len(keys) == len(parsed)
    for codepoints, projected in packed:
        row = parsed[codepoints]
        assert (projected.glyphs, projected.clusters, projected.seams) == (
            row.glyphs,
            row.clusters,
            row.seams,
        )
        assert pack.row(config, codepoints) == projected
    pack.close()


def test_a_subset_key_is_spelled_the_way_the_row_model_spells_it(tmp_path):
    """The packer converts every key through `int`, so a window is found under the codepoints the row model parses whatever case or padding the table uses. The pack writes keys in the uppercase four-digit form `Row.to_tsv` writes. Header and blank lines are skipped as `iter_rows` skips them, and a table written out of key order is sorted when packed."""
    path = tmp_path / "baseline-spelled.subset.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        stream.write("# rows: 2\n")
        stream.write("\n")
        stream.write("E650:E652:E650\tqsPea|qsTea|qsPea\t0,1,2\ty0,y0\t0,0,10|0,0,12|0,0,14\n")
        stream.write("e650:e652\tqsPea|qsTea\t0,1\ty0\t0,0,10|0,0,12\n")
    pack = _pack(tmp_path, "spelled")
    assert [codepoints for codepoints, _row in pack.rows("spelled")] == ["E650:E652", "E650:E652:E650"]
    parsed = {":".join(f"{value:04X}" for value in row.codepoints): row for row in iter_rows(path)}
    for key, row in parsed.items():
        projected = pack.row("spelled", key)
        assert projected is not None
        assert (projected.glyphs, projected.clusters, projected.seams) == (
            row.glyphs,
            row.clusters,
            row.seams,
        )
    assert pack.row("spelled", "e650:e652") == pack.row("spelled", "E650:E652")
    pack.close()


def test_a_pack_is_written_once_and_rewritten_only_when_a_table_moves(tmp_path, monkeypatch):
    """Every reader goes through `ensure_pack`, which writes only when it must. A pack whose header records the tables' digests and this packer's code digest is reused. A pack is rewritten when a table's digest changes, when the packer code changes (so a change to the seam check or the projection reaches every pack on the next build), or when the configuration list or destination differs. `SubsetPack.open` raises on a pack whose header disagrees with the tables or the packer code."""
    pair = (0xE650, 0xE652)
    _subset_table(tmp_path / "baseline-default.subset.tsv.gz", _subset_row(pair, ("y0",)))
    _subset_table(tmp_path / "baseline-ss10.subset.tsv.gz", _subset_row(pair, ("break",)))
    configs = ("default", "ss10")
    writes: list[Path] = []
    real_write = write_pack

    def counted(subset_dir, config_names, digests, destination):
        writes.append(Path(destination))
        return real_write(subset_dir, config_names, digests, destination)

    monkeypatch.setattr("rebuild.review.subset_pack.write_pack", counted)
    from rebuild.review import subset_pack as module

    first = module.ensure_pack(tmp_path, configs)
    assert first == tmp_path / module.PACK_NAME and writes == [first]
    assert module.ensure_pack(tmp_path, configs) == first and len(writes) == 1
    assert (
        module.ensure_pack(tmp_path, configs, table_digests(tmp_path, configs)) == first and len(writes) == 1
    )
    stale = table_digests(tmp_path, configs)
    _subset_table(tmp_path / "baseline-ss10.subset.tsv.gz", _subset_row(pair, ("y5",)))
    with pytest.raises(ValueError) as raised:
        SubsetPack.open(first, table_digests(tmp_path, configs))
    assert "other tables" in str(raised.value)
    assert module.ensure_pack(tmp_path, configs) == first and len(writes) == 2
    pack = SubsetPack.open(first, table_digests(tmp_path, configs))
    row = pack.row("ss10", "E650:E652")
    assert row is not None and row.seams == ("y5",)
    pack.close()
    with pytest.raises(ValueError):
        SubsetPack.open(first, stale)
    assert (
        module.ensure_pack(tmp_path, configs, pack=tmp_path / "elsewhere.pack") == tmp_path / "elsewhere.pack"
    )
    assert len(writes) == 3
    assert module.ensure_pack(tmp_path, ("default",)) == first and len(writes) == 4
    monkeypatch.setattr(module, "PACKER_DIGEST", "0" * 64)
    with pytest.raises(ValueError) as other_code:
        SubsetPack.open(first, table_digests(tmp_path, ("default",)))
    assert "other packer code" in str(other_code.value)
    assert module.ensure_pack(tmp_path, ("default",)) == first and len(writes) == 5
    module.SubsetPack.open(first, table_digests(tmp_path, ("default",))).close()


def test_a_served_unit_skips_check_unit_but_not_the_cross_unit_grain():
    """`served_ids` skips `check_unit` for a served fragment, whose stamp already covers the per-unit predicates. The predicates that relate a unit to its shard and to other units still run over every unit, served or not."""
    manifest, shards = _surface()
    _unit(shards, PLAIN_UNIT)["drafts"]["pin"]["syntax"] = "fail: Expected glyph token at pos 0"
    _complaint(check_shards(manifest, shards, REPO_ROOT), "drafts.pin.syntax")
    assert check_shards(manifest, shards, REPO_ROOT, served_ids={PLAIN_UNIT}) == []
    _unit(shards, PLAIN_UNIT)["class"] = "a-class-of-its-own"
    _complaint(check_shards(manifest, shards, REPO_ROOT, served_ids={PLAIN_UNIT}), "in shard")


# --- the census projection ----------------------------------------------------------------------


def test_the_premerge_projection_answers_one_ink_flag_per_captured_unit():
    """`ink_flags` has one entry per captured unit, at the pre-merge grain the census pins are defined over, so an index into it identifies a unit. The related claim that no unit with a family is ink-identical holds only for the real corpus, so `build_m1` asserts it instead of `derive_premerge`."""
    rows = [
        AuditRow(
            "default",
            codepoints,
            ("cell",),
            UNMATCHED_CLASS,
            ("qsPea", "qsMay"),
            ("qsPea/full/None/baseline/", "qsMay/full/baseline/None/"),
        )
        for codepoints in ("E650:E665", "E650:E652")
    ]
    table, _rows = load_table(rows, [], dict(LETTERS))
    capture = census.capture_premerge(table)
    capture.rebase(table.compact())
    store = UnitStore(table.n, strings=table.strings)
    for ordinal in range(table.n):
        table.set_family(ordinal, "a-family")
    facts = census.derive_premerge(capture, table, store)
    assert len(facts.ink_flags) == facts.units == table.n == 2
    assert [index for index, _family in facts.families] == [0, 1]


# --- the two moments a build checks a fresh fragment at ----------------------------------------


def _fixture_units() -> list[dict]:
    _manifest, shards = _surface()
    return [unit for shard in shards.values() for unit in shard]


def _broken(unit: dict, key: str) -> list[dict]:
    """Returns two copies of the unit: one with `key` deleted and one with `key` set to the string `"wrong"`. Neither makes `check_unit` raise."""
    without = copy.deepcopy(unit)
    without.pop(key, None)
    wrong = copy.deepcopy(unit)
    wrong[key] = "wrong"
    return [without, wrong]


@pytest.mark.parametrize("mode", ("m1-audit", "table-diff"))
def test_the_two_check_moments_partition_the_whole_contract(mode):
    """`check_unit` has two named subsets, and running `DRAFTED` then `PATCHED` must equal running it whole. For every fixture unit as shipped, and with each key in turn deleted or set to a wrong value, the two subsets' complaints concatenate to the full check's list in order and never overlap. A predicate that ran at both moments or at neither fails this test."""
    for unit in _fixture_units():
        variants = [unit] + [broken for key in list(unit) for broken in _broken(unit, key)]
        for variant in variants:
            whole = check_unit(variant, mode)
            drafted = check_unit(variant, mode, at=(DRAFTED,))
            patched = check_unit(variant, mode, at=(PATCHED,))
            assert whole == drafted + patched
            assert not set(drafted) & set(patched)


def test_every_scaffold_key_is_either_held_at_the_write_or_checked_there():
    """Every key `unit_scaffold` writes is either checked by `hold_scaffold` at the write (`_HELD_SCAFFOLD_KEYS`, so the drafting-time check read the same value that ships) or is one of the two keys the parent's reduces assign after drafting, `echo` and `cluster`, and no key is both. Deleting or corrupting `echo`, `cluster`, or `secondary_seams` (which the patch also writes) draws no complaint from `DRAFTED`. A wrong value in any of them draws one from `PATCHED`, and so does a missing `echo` or `cluster`. A key added to the scaffold fails this test until it is assigned to one side."""
    scaffold_keys = _SCAFFOLD_HEAD + _SCAFFOLD_TAIL
    unheld = {"echo", "cluster"}
    assert set(_HELD_SCAFFOLD_KEYS) | unheld == set(scaffold_keys)
    assert not set(_HELD_SCAFFOLD_KEYS) & unheld
    assert len(set(_HELD_SCAFFOLD_KEYS)) == len(_HELD_SCAFFOLD_KEYS)
    for unit in _fixture_units():
        assert check_unit(unit) == []
        for key in sorted(unheld | {"secondary_seams"}):
            without, wrong = _broken(unit, key)
            assert check_unit(without, at=(DRAFTED,)) == []
            assert check_unit(wrong, at=(DRAFTED,)) == []
            _complaint(check_unit(wrong, at=(PATCHED,)), key)
            if key in unheld:
                _complaint(check_unit(without, at=(PATCHED,)), f"{key} must be present")


def _build_mini(out: Path, mini_bundle) -> None:
    build_m1(
        out,
        audit_path=MINI_AUDIT,
        ledger_path=mini_bundle.ledger,
        subset_dir=MINI,
        after_font=MINI_FONT,
        spec_root=mini_bundle.spec_root,
        subset_pack=mini_bundle.subset_pack,
        jobs=1,
    )


def test_a_fragment_the_worker_drafts_wrong_fails_the_build(mini_bundle, monkeypatch, tmp_path):
    """Tests the drafting-time subset through a real serial build. A failing pin written onto every human fragment by `unit_to_json` is caught by `DRAFTED` where the fragment is drafted, and fails the build at the write in the parent's `contract check failed` list with the predicate's message. The build is serial because a spawned worker does not see the monkeypatch; the pooled path runs the same `_phase1_unit`."""
    unit_to_json = review_build.unit_to_json

    def refuted(*args, **kwargs):
        fragment = unit_to_json(*args, **kwargs)
        if fragment.get("drafts"):
            fragment["drafts"]["pin"]["syntax"] = "fail: refuted for the test"
        return fragment

    monkeypatch.setattr(review_build, "unit_to_json", refuted)
    with pytest.raises(SystemExit) as raised:
        _build_mini(tmp_path / "surface", mini_bundle)
    assert "contract check failed" in str(raised.value)
    assert "drafts.pin.syntax is 'fail: refuted for the test'" in str(raised.value)


def test_an_echo_the_parent_nulls_still_fails_the_build(mini_bundle, monkeypatch, tmp_path):
    """Tests the write-time subset through the same serial build. A null echo on every human unit, a field the parent assigns after drafting, is caught by `PATCHED` at the write and fails the build with the predicate's message."""
    monkeypatch.setattr(review_build.unit_cache, "echo_id_for", lambda key: None)
    with pytest.raises(SystemExit) as raised:
        _build_mini(tmp_path / "surface", mini_bundle)
    assert "contract check failed" in str(raised.value)
    assert "human-workload units must carry an echo group id" in str(raised.value)
