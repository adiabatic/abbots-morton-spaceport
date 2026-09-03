"""The invariants the surface build enforces on itself, each shown refusing a surface that breaks it, beside the drafts and the geometry it refuses to produce in the first place.

These predicates used to be tests that swept the live shards once a validators lane; the cross-unit ones are checks inside `build_m1` now, which means a violation fails the build that produced it rather than a gate half an hour later. The manifest-shape predicates and the ones about the files beside the manifest are not: a build writes every field they read out of its own inputs, so they are proven through `check_output_dir` over a real m1 build instead (`rebuild/test_app_index.py` over the frozen mini bundle), and what this module holds them to is the checked-in fixture surface — which carries no fonts, index page or sidecars, so `check_manifest` runs over it directly and the file predicates over a copy with one file broken at a time. `check_unit` runs over every unit a build computed, a cache-served one held instead by the `content_key` stamp its shard and its store record must agree on, and every cross-unit predicate in `check_shards` runs over both kinds. What that move costs is granularity — a violation arrives as one line in a `contract check failed` list rather than a named failing test — so what this module holds is the checker's own correctness: every predicate is exercised against the checked-in fixture surface, which passes as shipped, and then against one field broken at a time. The emitters answer for the other end, where a draft, a highlight rect, or a baseline row that could only ship as a recorded failure raises where it is made instead; those are exercised over the frozen mini bundle rather than over a broken fixture, because an emitter can only be wrong about a window it actually saw.
"""

import copy
import gzip
import json
import shutil
import warnings
from dataclasses import replace
from pathlib import Path

import pytest

from rebuild.review import app_index, census, drafts, unit_index
from rebuild.review.audit import AUDIT_HEADER, UNMATCHED_CLASS, Unit, load_workload
from rebuild.review.build import (
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
from rebuild.review.enrich import LETTERS, Enricher, _highlight, load_spec, load_subset_rows
from rebuild.validation.rowmodel import Row

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
MINI = FIXTURES / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"
# Enough of the bundle's windows for each shape the drafter tests ask for to be among them, and few enough that one settlement pass covers the slice.
MINI_SLICE = 64
SEAM_BEARER = "u-0004"
SEAM_HOME = "u-0005"


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


def _one(unit_id: str = "u-0000") -> dict:
    _manifest, shards = _surface()
    return _unit(shards, unit_id)


def _complaint(errors: list[str], needle: str) -> None:
    assert any(needle in error for error in errors), errors


def _sidecars(surface: Path) -> None:
    """Every stamped file the output check wants beside a manifest — the plumbing's unit index and the app's two — so a test about one missing file is not also a test about the other three."""
    unit_index.write_index(surface, [])
    app_index.write_app_artifacts(surface, {}, {})


def test_the_fixture_surface_passes_every_predicate():
    """The floor under everything else here: the checked-in miniature satisfies the whole contract as shipped, so every failure below is the broken field and not the fixture."""
    manifest, shards = _surface()
    assert check_manifest(manifest) == []
    assert check_shards(manifest, shards, REPO_ROOT) == []


# --- the drafts a reviewer would act on -------------------------------------------------------

# A draft nobody could act on is refused where it is made rather than recorded as a `fail: …` value for a later re-read to reject, so these are `DraftError`s out of the drafter and not complaints out of `check_unit`. They are asserted over the frozen mini bundle — real windows, a real after font, a real settlement — because a drafter can only be wrong about a window it actually drafted.


@pytest.fixture(scope="module")
def mini_enriched(mini_bundle):
    """A slice of the bundle's windows, enriched under the spec they settled beneath. One slice serves every drafter test here: enrichment is the expensive half, and none of them cares which window it gets beyond the shape it asks for."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(mini_bundle.spec_root)
    enricher = Enricher(spec, MINI, MINI_FONT, repo_root=REPO_ROOT)
    workload = load_workload(MINI_AUDIT, mini_bundle.ledger, dict(LETTERS))
    return enricher.enrich_many(workload.units[:MINI_SLICE])


@pytest.fixture(scope="module")
def mini_drafter():
    return Drafter(MINI_FONT, repo_root=REPO_ROOT)


@pytest.fixture(scope="module")
def joined_unit(mini_enriched):
    """A window whose first after-seam is one a pin can assert either way, so a test can flip it and know the after font refutes the result."""
    return next(unit for unit in mini_enriched if unit.after_seams[:1] in (("break",), ("y0",)))


@pytest.fixture(scope="module")
def policy_unit(mini_enriched, mini_drafter):
    return next(unit for unit in mini_enriched if mini_drafter.draft_policy(unit) is not None)


def test_a_pin_the_after_font_refutes_is_never_drafted(mini_drafter, joined_unit):
    """The pin the drafter would hand a reviewer to paste into the corpus, drafted from a real window and then from the same window with one seam asserted the other way: the first is what ships, and the second is refused rather than shipped with a recorded failure beside it."""
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
    """Which of the three checkers the branch table reaches depends on the window, so all three are made to refuse: what is under test is that a refused record raises rather than riding out as a schema_valid false."""
    for name in ("_refuse_checker", "_prefer_checker", "_contract_checker"):
        monkeypatch.setattr(getattr(mini_drafter, name), "check", lambda _record: ["boom"])
    with pytest.raises(DraftError) as raised:
        mini_drafter.draft_policy(policy_unit)
    assert "rune schema" in str(raised.value)


def test_an_any_of_candidate_that_does_not_parse_is_never_drafted(monkeypatch, mini_drafter, joined_unit):
    """The before-behavior candidate is the one string in the any-of record nothing else checks — the pin covers the after behavior — so it is parsed where it is written."""
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
    _unit(shards, "u-0000")["drafts"]["policy"]["file"] = "glyph_data/runes/qsNotAletter.yaml"
    _complaint(check_shards(manifest, shards, REPO_ROOT), "which is not a file in the repo")


# --- the slim machine-approved shape ------------------------------------------------------------

SLIM_UNIT = "u-0003"


def test_a_slim_unit_carrying_drafts_fails_the_build():
    unit = _one(SLIM_UNIT)
    unit["drafts"] = _one()["drafts"]
    _complaint(check_unit(unit), "carry drafts null")


def test_a_slim_unit_carrying_a_candidate_table_fails_the_build():
    unit = _one(SLIM_UNIT)
    unit["explain"] += "\n\nposition 0: qsPea\n  decided by: join-count"
    _complaint(check_unit(unit), "explain header only")


def test_a_human_unit_without_drafts_fails_the_build():
    unit = _one()
    unit["drafts"] = None
    _complaint(check_unit(unit), "drafts must carry pin/policy/any_of")


def test_a_picture_identical_unit_keeps_its_drafts_and_candidate_table():
    """Picture identity is machine approval too, but its units are the ones whose ink moved and the ones a human occasionally opens, so the checker wants them whole — the slim shape is a property of the two channels alone."""
    unit = _one()
    unit.update(picture_identical=True, batch=None, echo=None, cluster=None)
    assert check_unit(unit) == []
    unit["drafts"] = None
    _complaint(check_unit(unit), "drafts must carry pin/policy/any_of")


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
    _complaint(check_shards(manifest, shards), "shows no visible change")


def test_a_picture_identical_home_fails_the_build_the_same_way():
    """Picture identity is the same nothing-to-see at the coarser grain: the home keeps the nonempty ink_deltas a name-grain change records, and the resolver's suppression must still have fired."""
    manifest, shards = _homed_surface()
    home = _unit(shards, SEAM_HOME)
    home["picture_identical"] = True
    home["batch"] = None
    home["echo"] = None
    home["cluster"] = None
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != SEAM_HOME]
    _complaint(check_shards(manifest, shards), "shows no visible change")


# --- the files beside the manifest --------------------------------------------------------------


def test_a_missing_unit_index_fails_the_build(tmp_path):
    """The plumbing reads the index and never the shards, so a surface that ships without one, or with one stamped for a manifest it does not describe, is a surface the next carry would resolve off a stale projection."""
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
    """Splitting a class is invisible to the contract: `check_output_dir` concatenates the parts in order before it checks anything, so a surface answers the same whether a class is one file or several."""
    plain = tmp_path / "plain"
    shutil.copytree(FIXTURES, plain, ignore=shutil.ignore_patterns("mini", "*.md", "*.tsv", "*.yaml"))
    split = tmp_path / "split"
    shutil.copytree(plain, split)
    _split_first_class(split)
    assert check_output_dir(split) == check_output_dir(plain)
    assert not any("shard" in error for error in check_output_dir(split))


def test_every_part_of_a_split_class_must_be_present_and_non_empty(tmp_path):
    """A build that wrote part 000 and died before 001 must not read as complete, so the check walks every part rather than the first."""
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
    """The rebuild's own end state — an audit whose every window matches — is not a surface with nothing in it but a build with nothing to do, and it says so where the rows are read rather than several minutes later as a manifest whose `classes` list came out empty."""
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
            jobs=1,
        )
    assert str(audit_path) in str(raised.value)


def test_identical_table_directories_refuse_to_diff(tmp_path):
    """The same refusal on the table-diff side, and the same end state: two directories that settle every window alike have no diff to review."""
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
    """What the build holds its fonts to now that it no longer re-reads them after the fact: the digest it took when it loaded the font, asserted against the copy it ships. A run_m1 landing anywhere in the minutes between would otherwise leave a surface whose units describe the old font beside an `after.otf` that is the new one."""
    digest = _sha256(MINI_FONT)
    record = _copy_font(MINI_FONT, tmp_path, "after.otf", "AMS Review After", REPO_ROOT, digest)
    assert record["sha256"] == digest
    with pytest.raises(SystemExit) as raised:
        _copy_font(MINI_FONT, tmp_path, "after.otf", "AMS Review After", REPO_ROOT, "0" * 64)
    assert str(MINI_FONT) in str(raised.value)


def test_a_manifest_with_no_classes_draws_no_complaint():
    """A surface with an empty `classes` list is not a contract violation — it is a workload the build refuses to start, and the refusals above are where that is said. The checker used to call it malformed, which fired on the rebuild's own end state."""
    manifest, _shards = _surface()
    manifest["classes"] = []
    assert not [error for error in check_manifest(manifest) if "classes" in error]


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


# --- what the emitters refuse to produce --------------------------------------------------------


def test_a_highlight_rect_is_refused_where_the_pens_run_backwards():
    """`x_min <= x_max <= advance_total` is a property of the pen positions the rect is read off, so it is held there: monotone pens can only make a well-formed rect, and pens that are not are a shaped run the enricher has no business drawing a band over."""
    with pytest.raises(ValueError) as raised:
        _highlight([0, 10, 5], [(0, 1), (1, 2)], 0, 1)
    assert "non-decreasing" in str(raised.value)
    assert _highlight([0, 5, 10], [(0, 1), (1, 2)], 0, 1) == {
        "x_min": 0,
        "x_max": 10,
        "advance_total": 10,
    }


def _subset_table(path: Path, seams: tuple[str, ...]) -> Path:
    row = Row(
        codepoints=(0xE650, 0xE652),
        glyphs=("qsPea", "qsTea"),
        clusters=(0, 1),
        seams=seams,
        positions=((0, 0, 10), (0, 0, 12)),
    )
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        stream.write(row.to_tsv() + "\n")
    return path


def test_a_baseline_row_outside_the_seam_vocabulary_is_refused_at_load(tmp_path):
    """`SeamClassifier.classify` can name two heights at once, and a shard's `before.seams` cannot say that. The refusal belongs where the table is read — a compound token there is a bad input, and one config's table is read once per process rather than once per unit."""
    clean = _subset_table(tmp_path / "baseline-clean.subset.tsv.gz", ("y0",))
    assert list(load_subset_rows(clean)) == ["E650:E652"]
    compound = _subset_table(tmp_path / "baseline-compound.subset.tsv.gz", ("y0+y5",))
    with pytest.raises(ValueError) as raised:
        load_subset_rows(compound)
    assert "'y0+y5'" in str(raised.value)


def test_a_served_unit_skips_check_unit_but_not_the_cross_unit_grain():
    """What `served_ids` buys and what it must not: the per-unit predicates are the ones a served fragment's stamp already answers for, while the predicates that relate a unit to its shard and to its neighbors run over every unit on every build, served or not."""
    manifest, shards = _surface()
    _unit(shards, "u-0000")["drafts"]["pin"]["syntax"] = "fail: Expected glyph token at pos 0"
    _complaint(check_shards(manifest, shards, REPO_ROOT), "drafts.pin.syntax")
    assert check_shards(manifest, shards, REPO_ROOT, served_ids={"u-0000"}) == []
    _unit(shards, "u-0000")["class"] = "a-class-of-its-own"
    _complaint(check_shards(manifest, shards, REPO_ROOT, served_ids={"u-0000"}), "in shard")


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
