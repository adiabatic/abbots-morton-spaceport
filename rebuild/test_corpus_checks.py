"""Tests for the review corpus's contract checks, each run against a corpus that breaks one predicate, and for the build steps and the subset packer that refuse instead of producing a bad value.

The review crate runs the contract's per-unit and cross-unit predicates over every unit `build_m1` writes, so a violation fails the build that produced it. `check_unit`, `check_shards` (through `_CorpusCheck`), `check_manifest` and the file predicates (`_check_output_files`) are the same contract in Python, which the table-diff build runs over what it writes and `check_output_dir` runs over a written corpus, a real m1 build of the frozen mini bundle among them in `rebuild/test_app_index.py`. A violation appears only as one line in a `contract check failed` list, not as a named failing test, so this module tests the checker itself. It runs `check_manifest` and `check_shards` over the checked-in fixture corpus, which passes as shipped and carries no fonts, index page, or sidecars, and then with one field broken at a time. It runs the file predicates over small corpora under tmp_path with one file missing or wrong. The build's refusals run over the frozen mini bundle; the subset-pack tests use small synthetic tables, plus the mini bundle's real tables.
"""

import copy
import gzip
import json
import shutil
from pathlib import Path

import pytest

from rebuild.pipeline import row_delta
from rebuild.review import app_index, subset_pack, unit_index
from rebuild.review import build as review_build
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    AUDIT_HEADER,
    SLIM_OMITTED_KEYS,
)
from rebuild.review.build import (
    _check_output_files,
    _copy_font,
    _sha256,
    _write_json,
    build_m1,
    build_table_diff,
    check_manifest,
    check_output_dir,
    check_shards,
    check_unit,
)
from rebuild.review.subset_pack import pack_key, table_digests, write_pack
from rebuild.tools import cycle_timings
from rebuild.validation.rowmodel import Row, iter_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
MINI = FIXTURES / "mini"
MINI_FONT = MINI / "M1.otf"
JUNCTION_BEARER = "u-Xp5KzYAPYK7"
PRIMARY_UNIT = "u-6VFDHaMNgCP"
PLAIN_UNIT = "u-BLyK6Z13RtN"
DUPLICATE_MATE = "u-Nc3wVLJaPHB"
THIRD_UNIT = "u-5NP1CPJxyNT"


def _corpus() -> tuple[dict, dict[str, list[dict]]]:
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
    _manifest, shards = _corpus()
    return _unit(shards, unit_id)


def _expect_error(errors: list[str], needle: str) -> None:
    assert any(needle in error for error in errors), errors


def _sidecars(corpus: Path) -> None:
    """Writes the stamped sidecars the output check requires beside a manifest (the verdict update's unit index and the files in `app_index.ARTIFACTS`), so a test about one missing file does not also fail on the others."""
    unit_index.write_index(corpus, [])
    app_index.write_app_artifacts(corpus, {}, {})


def test_the_fixture_corpus_passes_every_predicate():
    """The checked-in fixture corpus passes every predicate as shipped, so each failure in the tests below comes from the field that test breaks."""
    manifest, shards = _corpus()
    assert check_manifest(manifest) == []
    assert check_shards(manifest, shards, REPO_ROOT) == []


# --- the drafts a reviewer would act on -------------------------------------------------------


def test_a_policy_draft_naming_a_file_that_is_not_in_the_repo_fails_the_build():
    manifest, shards = _corpus()
    _unit(shards, PLAIN_UNIT)["drafts"]["policy"]["file"] = "glyph_data/runes/qsNotAletter.yaml"
    _expect_error(check_shards(manifest, shards, REPO_ROOT), "which is not a file in the repo")


# --- the slim machine-approved and no-verdict shape ----------------------------------------------

SLIM_UNIT = "u-DBug8cSj15L"


@pytest.mark.parametrize("key", SLIM_OMITTED_KEYS)
def test_a_slim_unit_carrying_explain_material_fails_the_build(key):
    unit = _one(SLIM_UNIT)
    assert key not in unit
    unit[key] = _one()[key]
    _expect_error(check_unit(unit), f"omit {key}")


@pytest.mark.parametrize("key", SLIM_OMITTED_KEYS)
def test_a_slim_unit_carrying_an_emptied_field_fails_the_build(key):
    """A slim fragment omits these keys; it never sets them to null. The app would read a null field on a slim fragment as a full record with a blank field."""
    unit = _one(SLIM_UNIT)
    unit[key] = None
    _expect_error(check_unit(unit), f"omit {key}")


@pytest.mark.parametrize("key", SLIM_OMITTED_KEYS)
def test_a_human_unit_without_its_explain_material_fails_the_build(key):
    unit = _one()
    del unit[key]
    _expect_error(check_unit(unit), key)


def test_a_human_unit_with_drafts_null_fails_the_build():
    unit = _one()
    unit["drafts"] = None
    _expect_error(check_unit(unit), "drafts must carry pin/policy/any_of")


@pytest.mark.parametrize("flag", ("picture_identical", "junior_equivalent", "no_verdict"))
def test_every_machine_check_and_the_exemption_take_the_slim_shape(flag):
    """Every unit that takes no verdict is slim: a picture-identical unit, a Junior-equivalent unit, and a unit in a no-verdict class each pass without the explain material, and each fails the build when it carries drafts."""
    unit = _one(SLIM_UNIT)
    deltas = {} if flag == "picture_identical" else {"ss02": "d-000000000000"}
    unit.update(ink_identical=False, ink_deltas=deltas, **{flag: True})
    assert check_unit(unit) == []
    unit["drafts"] = _one()["drafts"]
    _expect_error(check_unit(unit), "omit drafts")


# --- the fields the app draws ------------------------------------------------------------------


def test_a_summary_without_its_new_clause_fails_the_build():
    unit = _one()
    unit["summary"] = "The ·Tea·Oy ligature forms."
    _expect_error(check_unit(unit), "summary must open with the New: clause")


def test_a_multiline_summary_fails_the_build():
    unit = _one()
    unit["summary"] = unit["summary"] + "\nand another thing"
    _expect_error(check_unit(unit), "summary must be one line")


def test_a_unit_whose_configs_render_two_ways_fails_the_build():
    unit = _one()
    configs = unit["configs"]
    unit["render_groups"] = [{"configs": configs[:1]}, {"configs": configs[1:]}]
    _expect_error(check_unit(unit), "exactly one render group")


@pytest.mark.parametrize("side", ("before", "after"))
def test_a_highlight_reaching_past_the_run_fails_the_build(side):
    unit = _one()
    unit["highlight"][side]["x_max"] = unit["highlight"][side]["advance_total"] + 1
    _expect_error(check_unit(unit), f"highlight.{side} must satisfy")


def test_a_secondary_junction_rect_reaching_past_the_run_fails_the_build():
    unit = _one(JUNCTION_BEARER)
    unit["secondary_junctions"][0]["after"]["x_max"] = (
        unit["secondary_junctions"][0]["after"]["advance_total"] + 1
    )
    _expect_error(check_unit(unit), "secondary_junctions[0].after must satisfy")


def test_a_gate_clause_the_manifest_does_not_gloss_fails_the_build():
    manifest, shards = _corpus()
    _unit(shards, PLAIN_UNIT)["config_gate"][0]["feature"] = "ss99"
    _expect_error(check_shards(manifest, shards), "feature_descriptions does not gloss")


# --- the grains that only exist across units ----------------------------------------------------


def test_a_duplicate_group_spanning_two_config_sets_fails_the_build():
    manifest, shards = _corpus()
    _unit(shards, DUPLICATE_MATE)["duplicate_group"] = _unit(shards, PLAIN_UNIT)["duplicate_group"]
    _expect_error(check_shards(manifest, shards), "one group spans")


def test_a_duplicate_group_spanning_two_clusters_fails_the_build():
    manifest, shards = _corpus()
    left, right = _unit(shards, DUPLICATE_MATE), _unit(shards, THIRD_UNIT)
    right["duplicate_group"] = left["duplicate_group"]
    right["cluster"] = "c-0badc0de"
    _expect_error(check_shards(manifest, shards), "spans two clusters")


def test_a_cluster_spanning_two_classes_fails_the_build():
    manifest, shards = _corpus()
    _unit(shards, JUNCTION_BEARER)["cluster"] = _unit(shards, DUPLICATE_MATE)["cluster"]
    _expect_error(check_shards(manifest, shards), "one signature spans")


def test_human_unit_ids_out_of_triage_order_fails_the_build():
    """`human_unit_ids` lists the human units in triage order (class, group, window, id). A fragment carries no position, so the checker derives the order from the fragments with `triage_key` and compares the manifest with it."""
    manifest, shards = _corpus()
    manifest["human_unit_ids"] = list(reversed(manifest["human_unit_ids"]))
    _expect_error(check_shards(manifest, shards), "not the triage-ordered sequence")


def test_a_class_claiming_a_batch_its_units_do_not_occupy_fails_the_build():
    manifest, shards = _corpus()
    manifest["classes"][0]["batches"] = [0, 4]
    _expect_error(check_shards(manifest, shards), "are not the slices")


def test_a_batch_count_the_index_does_not_bear_out_fails_the_build():
    manifest, shards = _corpus()
    manifest["totals"]["batches"] = 7
    _expect_error(check_shards(manifest, shards), "totals.batches does not count")


def test_a_fragment_carrying_a_batch_fails_the_build():
    """A fragment's bytes depend only on its content and the ledger, which is what lets a cached fragment be copied unchanged. A batch is a position in the queue, so it is recorded in the manifest's index."""
    unit = _one()
    unit["batch"] = 0
    _expect_error(check_unit(unit), "carries no batch")


def test_an_id_that_is_not_its_stamps_fails_the_build():
    """The id is the content key's first 64 bits in base58. The check fails a fragment whose id belongs to different content than its stamp, and an id of any other form."""
    unit = _one()
    unit["id"] = _one(DUPLICATE_MATE)["id"]
    _expect_error(check_unit(unit), "must be the content key's own")
    unit["id"] = "u-0000"
    _expect_error(check_unit(unit), "base58")


def test_a_no_verdict_class_carrying_batches_fails_the_build():
    manifest, shards = _corpus()
    manifest["classes"][0]["no_verdict"] = True
    _expect_error(check_shards(manifest, shards), "no-verdict class must carry no batches")


# --- the secondary-junction primary unit relation -----------------------------------------------------------


def _corpus_with_primary_unit() -> tuple[dict, dict[str, list[dict]]]:
    """Returns the fixture with its one junction with a primary unit made to look like the resolver's output: a `secondary_junctions` count record in the manifest, which marks the primary units as resolver-assigned and counts that junction and the fixture's junction with no primary unit, a primary unit window that is a substring of the bearer's window, and a primary pair on the primary unit. The fixture ships without those counts because its junctions are placed by hand."""
    manifest, shards = _corpus()
    manifest["secondary_junctions"] = {
        "units_with_markers": 2,
        "junctions_with_primary_unit": 1,
        "junctions_without_primary_unit": 1,
        "junctions_suppressed_invisible": 0,
    }
    primary_unit = _unit(shards, PRIMARY_UNIT)
    primary_unit["codepoints"] = "E679:E652"
    primary_unit["pair"] = {"left": 0, "right": 1}
    primary_unit["pair_codepoints"] = [0, 1]
    return manifest, shards


def test_a_resolver_shaped_primary_unit_passes():
    manifest, shards = _corpus_with_primary_unit()
    assert check_shards(manifest, shards) == []


def test_a_primary_unit_that_is_not_a_substring_window_fails_the_build():
    manifest, shards = _corpus_with_primary_unit()
    _unit(shards, PRIMARY_UNIT)["codepoints"] = "E652:E670"
    _expect_error(check_shards(manifest, shards), "is not a substring window")


def test_a_primary_unit_with_no_primary_pair_fails_the_build():
    manifest, shards = _corpus_with_primary_unit()
    primary_unit = _unit(shards, PRIMARY_UNIT)
    primary_unit["pair"] = None
    primary_unit["pair_codepoints"] = None
    _expect_error(check_shards(manifest, shards), "has no primary pair")


def test_a_primary_unit_with_nothing_to_see_fails_the_build():
    """The resolver counts a junction whose primary unit is ink-identical in `junctions_suppressed_invisible` instead of shipping it. A shipped junction with such a primary unit means that suppression did not happen."""
    manifest, shards = _corpus_with_primary_unit()
    primary_unit = _unit(shards, PRIMARY_UNIT)
    primary_unit["ink_identical"] = True
    primary_unit["ink_deltas"] = {}
    primary_unit["duplicate_group"] = None
    primary_unit["cluster"] = None
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != PRIMARY_UNIT]
    _expect_error(check_shards(manifest, shards), "shows no visible change")


def test_a_picture_identical_primary_unit_fails_the_build_the_same_way():
    """A picture-identical primary unit also shows no visible change: its delta is empty under every config, so the resolver should have suppressed the junction."""
    manifest, shards = _corpus_with_primary_unit()
    primary_unit = _unit(shards, PRIMARY_UNIT)
    primary_unit["picture_identical"] = True
    primary_unit["ink_deltas"] = {}
    primary_unit["duplicate_group"] = None
    primary_unit["cluster"] = None
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != PRIMARY_UNIT]
    _expect_error(check_shards(manifest, shards), "shows no visible change")


def _not_a_substring(shards: dict[str, list[dict]]) -> None:
    _unit(shards, PRIMARY_UNIT)["codepoints"] = "E652:E670"


def _no_primary_pair(shards: dict[str, list[dict]]) -> None:
    _unit(shards, PRIMARY_UNIT)["pair"] = None


def _nothing_to_see(shards: dict[str, list[dict]]) -> None:
    _unit(shards, PRIMARY_UNIT)["ink_identical"] = True


# --- the files beside the manifest --------------------------------------------------------------


def test_a_missing_unit_index_fails_the_build(tmp_path):
    """The verdict update reads the unit index, not the shards. A corpus without an index, or with one stamped for another manifest, would make the next carry read stale data."""
    manifest = {"classes": [], "fonts": {}}
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _expect_error(_check_output_files(tmp_path, manifest), "units-index.ndjson.gz is missing")
    _sidecars(tmp_path)
    assert _check_output_files(tmp_path, manifest) == []
    row_delta.head_path(tmp_path, unit_index.INDEX_NAME).unlink()
    assert (tmp_path / row_delta.base_name(unit_index.INDEX_NAME, 0)).is_file()
    _expect_error(_check_output_files(tmp_path, manifest), "units-index.ndjson.gz is missing")
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    _expect_error(_check_output_files(tmp_path, {"classes": [], "fonts": {}}), "stamped for another manifest")


def test_a_missing_or_empty_shard_fails_the_build(tmp_path):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    _expect_error(_check_output_files(tmp_path, manifest), "is missing")
    (tmp_path / "units").mkdir()
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            (tmp_path / part).write_bytes(b"")
    _expect_error(_check_output_files(tmp_path, manifest), "is empty")


def _split_first_class(corpus: Path) -> None:
    """Rewrite the corpus's first class as two numbered parts, which is the layout a class past the byte cap ships in."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    meta = manifest["classes"][0]
    (whole,) = unit_index.class_shards(meta)
    units = json.loads((corpus / whole).read_text(encoding="utf-8"))
    chunks = (units[:1], units[1:])
    meta["shards"] = [f"units/{meta['id']}.{index:03d}.json" for index in range(len(chunks))]
    for part, chunk in zip(meta["shards"], chunks, strict=True):
        _write_json(corpus / part, chunk)
    (corpus / whole).unlink()
    _write_json(corpus / "manifest.json", manifest)


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
    _expect_error(_check_output_files(tmp_path, manifest), "units/big.000.json is missing")
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "big.000.json").write_text("[]", encoding="utf-8")
    _expect_error(_check_output_files(tmp_path, manifest), "units/big.001.json is missing")
    (tmp_path / "units" / "big.001.json").write_bytes(b"")
    _expect_error(_check_output_files(tmp_path, manifest), "units/big.001.json is empty")
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
    _expect_error(_check_output_files(tmp_path, manifest, REPO_ROOT), "as it stands on disk")


def test_the_after_font_s_copy_is_compared_kern_blind_and_the_before_font_s_byte_for_byte(
    tmp_path, monkeypatch
):
    """A kern-only rebuild changes M1.otf's bytes and nothing the corpus reads, so the after font's copy is checked through `fingerprint.after_font_current`; the before font has no such allowance."""
    import hashlib

    asked = []

    def current(copy, digest, source):
        asked.append((Path(copy).name, digest, Path(source).name))
        return True

    monkeypatch.setattr(review_build.fingerprint, "after_font_current", current)
    fonts = {}
    for side in ("before", "after"):
        source = tmp_path / "sources" / f"{side}.otf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"the {side} font as rebuilt".encode())
        copied = tmp_path / "fonts" / f"{side}.otf"
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(f"the {side} font as copied".encode())
        fonts[side] = {
            "file": f"fonts/{side}.otf",
            "source": str(source),
            "sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
        }
    manifest = {"classes": [], "fonts": fonts}
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _sidecars(tmp_path)
    errors = _check_output_files(tmp_path, manifest, REPO_ROOT)
    assert [error for error in errors if "as it stands on disk" in error] == [
        f"fonts.before: the copy is not {fonts['before']['source']} as it stands on disk"
    ]
    assert asked == [("after.otf", fonts["after"]["sha256"], "after.otf")]


def test_a_build_without_its_baseline_subset_tables_refuses_before_it_starts(tmp_path):
    with pytest.raises(SystemExit) as raised:
        build_m1(
            tmp_path / "corpus",
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
            tmp_path / "corpus",
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
    for name in ("settlement-default.tsv", "joins-default.tsv"):
        shutil.copyfile(MINI / name, old_dir / name)
        shutil.copyfile(MINI / name, new_dir / name)
    with pytest.raises(SystemExit) as raised:
        build_table_diff(
            tmp_path / "out",
            old_dir,
            new_dir,
            REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
            MINI_FONT,
            with_example_texts=False,
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


def test_a_manifest_with_no_classes_draws_no_error():
    """`check_manifest` accepts an empty `classes` list. The build exits before it would write one (see the tests above), and a checker that failed on it would fail on the rebuild's intended end state."""
    manifest, _shards = _corpus()
    manifest["classes"] = []
    assert not [error for error in check_manifest(manifest) if "classes" in error]


# --- what the subset packer refuses to produce --------------------------------------------------


def _subset_row(codepoints: tuple[int, ...], junctions: tuple[str, ...]) -> Row:
    return Row(
        codepoints=codepoints,
        glyphs=tuple({0xE650: "qsPea", 0xE652: "qsTea"}[value] for value in codepoints),
        clusters=tuple(range(len(codepoints))),
        junctions=junctions,
        positions=tuple((0, 0, 10 + 2 * index) for index in range(len(codepoints))),
    )


def _subset_table(path: Path, *rows: Row) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        for row in rows:
            stream.write(row.to_tsv() + "\n")
    return path


def _pack_pools() -> list[dict]:
    """Return the `subset-pack` pool records in the test's timings journal, which the autouse redirect points under the test's `tmp_path`."""
    return [
        record
        for record in cycle_timings.load_pool_records(cycle_timings.JOURNAL)
        if record["unit"] == "subset-pack"
    ]


def _pack(tmp_path: Path, *configs: str) -> Path:
    """Packs the named configurations' tables under `tmp_path` and returns the pack, its header recording digests of the same tables."""
    return write_pack(tmp_path, configs, table_digests(tmp_path, configs), tmp_path / "subsets.pack")


def _section_rows(pack: Path, config: str) -> int:
    """The row count the pack's header records for one configuration's section."""
    header, _base = subset_pack._read_header(pack)
    return header["sections"][config]["rows"]


def test_a_baseline_row_outside_the_junction_vocabulary_is_refused_by_the_packer(tmp_path):
    """`JunctionClassifier.classify` can emit a compound token naming two heights, and a shard's `before.junctions` cannot represent one. The packer checks every row of every table it packs, so a compound token on any row fails the table before a pack exists, whichever rows the build later looks up. Nothing is renamed into place on a failure, so no partial pack is left behind."""
    pair = (0xE650, 0xE652)
    _subset_table(tmp_path / "baseline-clean.subset.tsv.gz", _subset_row(pair, ("y0",)))
    assert _section_rows(_pack(tmp_path, "clean"), "clean") == 1
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


def _without_digests(pack: Path) -> tuple[dict, bytes]:
    """A pack's header without its table digests, and its body: what two packs of the same rows from differently written tables share."""
    header, base = subset_pack._read_header(pack)
    header.pop("digests")
    return header, pack.read_bytes()[base:]


def test_a_subset_key_is_spelled_the_way_the_row_model_spells_it(tmp_path):
    """The packer converts every key through `int`, so a window is found under the codepoints the row model parses whatever case or padding the table uses. The pack writes keys in the uppercase four-digit form `Row.to_tsv` writes. Header and blank lines are skipped as `iter_rows` skips them, and a table written out of key order is sorted when packed, so the pack holds what a pack of the same rows written canonically and in key order holds."""
    spelled = tmp_path / "spelled"
    spelled.mkdir()
    path = spelled / "baseline-spelled.subset.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        stream.write("# rows: 2\n")
        stream.write("\n")
        stream.write("E650:E652:E650\tqsPea|qsTea|qsPea\t0,1,2\ty0,y0\t0,0,10|0,0,12|0,0,14\n")
        stream.write("e650:e652\tqsPea|qsTea\t0,1\ty0\t0,0,10|0,0,12\n")
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    _subset_table(
        canonical / "baseline-spelled.subset.tsv.gz",
        *sorted(iter_rows(path), key=lambda row: (len(row.codepoints), row.codepoints)),
    )
    assert _without_digests(_pack(spelled, "spelled")) == _without_digests(_pack(canonical, "spelled"))


def test_a_pack_is_written_once_and_rewritten_only_when_a_table_moves(tmp_path, monkeypatch):
    """The build goes through `ensure_pack`, which writes only when it must. A pack whose header records the tables' digests and this packer's code digest is reused. A pack is rewritten when a table's digest changes, when the packer code changes (so a change to the junction check or the projection reaches every pack on the next build), or when the configuration list or destination differs. A header that disagrees with the tables or the packer code is named as such (`_header_disagreement`), the check the crate repeats against the digests the units init names."""
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

    def disagreement(digests):
        return module._header_disagreement(module._read_header(first)[0], digests)

    _subset_table(tmp_path / "baseline-ss10.subset.tsv.gz", _subset_row(pair, ("y5",)))
    assert disagreement(table_digests(tmp_path, configs)) == "written over other tables"
    assert module.ensure_pack(tmp_path, configs) == first and len(writes) == 2
    assert disagreement(table_digests(tmp_path, configs)) is None
    assert disagreement(stale) == "written over other tables"
    assert (
        module.ensure_pack(tmp_path, configs, pack=tmp_path / "elsewhere.pack") == tmp_path / "elsewhere.pack"
    )
    assert len(writes) == 3
    assert module.ensure_pack(tmp_path, ("default",)) == first and len(writes) == 4
    monkeypatch.setattr(module, "PACKER_DIGEST", "0" * 64)
    assert disagreement(table_digests(tmp_path, ("default",))) == "written by other packer code"
    assert module.ensure_pack(tmp_path, ("default",)) == first and len(writes) == 5
    assert disagreement(table_digests(tmp_path, ("default",))) is None


def _row(codepoints: tuple[int, ...], glyphs: tuple[str, ...], junctions: tuple[str, ...]) -> Row:
    return Row(
        codepoints=codepoints,
        glyphs=glyphs,
        clusters=tuple(range(len(glyphs))),
        junctions=junctions,
        positions=tuple((0, 0, 10) for _ in glyphs),
    )


def _table_lines(path: Path, *lines: str) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("# config: default\n")
        for line in lines:
            stream.write(line + "\n")
    return path


def test_the_two_passes_write_the_bytes_the_in_memory_path_writes(tmp_path, mini_bundle):
    """`write_pack` writes, byte for byte, the file `write_pack_in_memory` writes over the same tables, header, padding, sections and the gaps between them alike: here two configurations that share a row, rows with no junction, the widest record, four glyphs and three junctions, in one table only, and a table with no rows last, so each slot count and the string table come from every table at once and the file ends on an aligned gap; and the mini bundle's tables, which its fixture packs in memory. Each `write_pack` writes one `subset-pack` pool record of peak footprints, one a configuration's process, and `write_pack_in_memory` writes none."""
    shared = _row((0xE650, 0xE652), ("qsPea", "qsTea"), ("y0",))
    _subset_table(
        tmp_path / "baseline-first.subset.tsv.gz",
        _row((0xE650,), ("qsPea",), ()),
        shared,
        _row((0xE650, 0xE652, 0xE650), ("qsPea", "qsTea", "qsPea"), ("y0", "y5")),
    )
    _subset_table(
        tmp_path / "baseline-second.subset.tsv.gz",
        _row((0xE652,), ("qsTea",), ()),
        shared,
        _row((0xE652, 0xE650), ("qsTea", "qsPea.alt"), ("break",)),
    )
    _subset_table(
        tmp_path / "baseline-wide.subset.tsv.gz",
        _row(
            (0xE650, 0xE652, 0xE650, 0xE652),
            ("qsPea", "qsTea", "qsPea_qsTea", "qsTea.half"),
            ("lig", "absent", "y0"),
        ),
    )
    _subset_table(tmp_path / "baseline-empty.subset.tsv.gz")
    configs = ("first", "second", "wide", "empty")
    digests = table_digests(tmp_path, configs)
    two = write_pack(tmp_path, configs, digests, tmp_path / "two.pack")
    one = subset_pack.write_pack_in_memory(tmp_path, configs, digests, tmp_path / "one.pack")
    assert two.read_bytes() == one.read_bytes()
    header, base = subset_pack._read_header(two)
    assert (header["glyph_slots"], header["cluster_slots"], header["junction_slots"]) == (4, 4, 3)
    assert (
        header["sections"]["empty"]["rows"] == 0
        and two.stat().st_size == base + header["sections"]["empty"]["keys"]
    )
    mini = write_pack(
        MINI, ACCEPTANCE_CONFIGS, table_digests(MINI, ACCEPTANCE_CONFIGS), tmp_path / "mini.pack"
    )
    assert mini.read_bytes() == mini_bundle.subset_pack.read_bytes()
    pools = _pack_pools()
    assert [(pool["width"], pool["worker_reading"]) for pool in pools] == [
        (len(configs), "footprint"),
        (len(ACCEPTANCE_CONFIGS), "footprint"),
    ]
    for pool, named in zip(pools, (configs, ACCEPTANCE_CONFIGS)):
        assert set(pool["worker_peak_rss_bytes"]) == set(named), pool
        assert all(peak > 0 for peak in pool["worker_peak_rss_bytes"].values()), pool
        assert pool["controller_peak_rss_bytes"] > 0


def test_the_two_passes_raise_the_earliest_configurations_error_and_leave_no_file(tmp_path):
    """Pass 1 reads every table at once, each in a process of its own, so the tables finish in any order; the parent collects every survey and raises the error a serial read in configuration order meets first, the in-memory path's, by type and message. Each case fails in two configurations in different ways (a compound junction, a window `pack_key` refuses, a duplicate window in an otherwise sorted table, a line short of fields), the earlier one's failure behind a few thousand good rows so its table finishes last, and each way fails both first and second. No pack and no temporary file is left."""
    filler = [
        _row((first, second), (f"g{first:04X}", f"g{second:04X}"), ("y0",)).to_tsv()
        for first in range(0xE650, 0xE680)
        for second in range(0xE650, 0xE680)
    ]
    bad = {
        "compound": _row((0xE650, 0xE652, 0xE650), ("qsPea", "qsTea", "qsPea"), ("y0", "y0+y5")).to_tsv(),
        "five": _row((0xE650,) * 5, ("qsPea",) * 5, ("y0",) * 4).to_tsv(),
        "duplicate": filler[-1],
        "short": "E650:E652:E650\tqsPea|qsTea|qsPea",
    }
    clean = _row((0xE650, 0xE652), ("qsPea", "qsTea"), ("y0",)).to_tsv()
    for first, second in (
        ("compound", "five"),
        ("five", "duplicate"),
        ("duplicate", "short"),
        ("short", "compound"),
    ):
        directory = tmp_path / f"{first}-{second}"
        directory.mkdir()
        _table_lines(directory / "baseline-clean.subset.tsv.gz", clean)
        _table_lines(directory / f"baseline-{first}.subset.tsv.gz", *filler, bad[first])
        _table_lines(directory / f"baseline-{second}.subset.tsv.gz", bad[second], *filler)
        configs = ("clean", first, second)
        digests = table_digests(directory, configs)
        with pytest.raises(Exception) as serial:
            subset_pack.write_pack_in_memory(directory, configs, digests, directory / "serial.pack")
        with pytest.raises(type(serial.value)) as raised:
            write_pack(directory, configs, digests, directory / "refused.pack")
        assert str(raised.value) == str(serial.value), (first, second)
        assert not (directory / "refused.pack").exists() and not list(directory.glob("*.tmp-*"))


def test_each_section_lands_the_same_whatever_order_the_writers_open_the_pack_in(tmp_path):
    """Pass 2's writers open the staged pack by path without truncating it and write only their own sections, so a late opener leaves its neighbors' bytes as they are: run one after another in reverse configuration order, over a file sized as `write_pack` sizes it, they leave the bytes `write_pack` writes."""
    pair = (0xE650, 0xE652)
    _subset_table(tmp_path / "baseline-first.subset.tsv.gz", _subset_row(pair, ("y0",)))
    _subset_table(tmp_path / "baseline-second.subset.tsv.gz", _subset_row(pair, ("break",)))
    _subset_table(
        tmp_path / "baseline-third.subset.tsv.gz",
        _subset_row(pair, ("y5",)),
        _subset_row((*pair, 0xE650), ("y5", "lig")),
    )
    configs = ("first", "second", "third")
    digests = table_digests(tmp_path, configs)
    paths = [str(subset_pack.table_path(tmp_path, config)) for config in configs]
    layout = subset_pack._layout(configs, [subset_pack._survey(path) for path in paths], digests)
    staged = tmp_path / "staged.pack"
    with open(staged, "wb") as out:
        out.write(layout.head)
        out.truncate(layout.size)
    for config, path in reversed(list(zip(configs, paths))):
        section = layout.header["sections"][config]
        written = subset_pack._write_section(
            str(staged),
            path,
            section["rows"],
            layout.base + section["keys"],
            layout.base + section["records"],
            layout.header["strings"],
            layout.slots,
        )
        assert written is None
    assert (
        staged.read_bytes() == write_pack(tmp_path, configs, digests, tmp_path / "subsets.pack").read_bytes()
    )


def test_a_table_out_of_key_order_sends_the_whole_pack_down_the_in_memory_path(tmp_path, monkeypatch):
    """A table whose keys do not strictly increase is packed, with every table beside it, by the in-memory path, which sorts it, as `test_a_subset_key_is_spelled_the_way_the_row_model_spells_it` checks; tables in key order never reach that path. The pack's processes ran pass 1 before it, so their pool record still holds one reading a configuration."""
    taken: list[tuple[str, ...]] = []
    in_memory = subset_pack.write_pack_in_memory

    def counted(subset_dir, configs, digests, destination):
        taken.append(tuple(configs))
        return in_memory(subset_dir, configs, digests, destination)

    monkeypatch.setattr(subset_pack, "write_pack_in_memory", counted)
    pair, triple = _subset_row((0xE650, 0xE652), ("y0",)), _subset_row((0xE650, 0xE652, 0xE650), ("y0", "y5"))
    _subset_table(tmp_path / "baseline-sorted.subset.tsv.gz", pair, triple)
    _subset_table(tmp_path / "baseline-unsorted.subset.tsv.gz", triple, pair)
    _pack(tmp_path, "sorted")
    assert taken == []
    pack = _pack(tmp_path, "sorted", "unsorted")
    assert taken == [("sorted", "unsorted")]
    assert _section_rows(pack, "unsorted") == 2
    assert [sorted(pool["worker_peak_rss_bytes"]) for pool in _pack_pools()] == [
        ["sorted"],
        ["sorted", "unsorted"],
    ]


# --- the re-routed unit's ledger class -----------------------------------------------------------


def test_ledger_class_may_be_absent_and_is_checked_when_present():
    """`ledger_class` is optional: a fragment passes without it and with null. A present value is checked: on a human unit it names a class other than the unit's own, and a machine-approved or no-verdict unit carries none."""
    plain, slim = _one(), _one(SLIM_UNIT)
    for unit in (plain, slim):
        assert "ledger_class" not in unit
        assert check_unit(unit) == check_unit({**unit, "ledger_class": None}) == []
    assert check_unit({**plain, "ledger_class": "an-identical-class"}) == []
    for wrong in (
        {**plain, "ledger_class": plain["class"]},
        {**plain, "ledger_class": ""},
        {**slim, "ledger_class": "an-identical-class"},
    ):
        _expect_error(check_unit(wrong), "ledger_class must be")
