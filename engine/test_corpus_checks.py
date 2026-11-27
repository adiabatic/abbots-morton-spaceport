"""Tests for the review corpus's contract checks, each run against a corpus that breaks one predicate, and for the build steps that refuse instead of producing a bad value.

`check_unit`, `check_shards` (through `_CorpusCheck`), `check_manifest` and the file predicates (`_check_output_files`) are the §7 contract, which the build runs over what it writes and `check_output_dir` runs over a written corpus, a real table-diff build among them in `engine/test_review_diff_units.py`. A violation appears only as one line in a `contract check failed` list, not as a named failing test, so this module tests the checker itself. It runs `check_manifest` and `check_shards` over the checked-in fixture corpus, which passes as shipped and carries no fonts, index page, or sidecars, and then with one field broken at a time. It runs the file predicates over small corpora under tmp_path with one file missing or wrong.
"""

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from engine.review import app_index, shards
from engine.review.build import (
    _check_output_files,
    _copy_font,
    _sha256,
    _write_json,
    build_table_diff,
    check_manifest,
    check_output_dir,
    check_shards,
    check_unit,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "engine" / "review" / "fixtures"
MINI = FIXTURES / "mini"
MINI_FONT = MINI / "Senior-Regular.otf"
# A human joins unit with a config gate and a highlight, and another human unit of the same class.
PLAIN_UNIT = "u-MHckzMuQQVX"
OTHER_UNIT = "u-MG7BYctpZgY"


def _corpus() -> tuple[dict, dict[str, list[dict]]]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    by_class = {meta["id"]: list(shards.iter_class_fragments(FIXTURES, meta)) for meta in manifest["classes"]}
    return copy.deepcopy(manifest), copy.deepcopy(by_class)


def _unit(by_class: dict[str, list[dict]], unit_id: str) -> dict:
    return next(unit for shard in by_class.values() for unit in shard if unit["id"] == unit_id)


def _one(unit_id: str = PLAIN_UNIT) -> dict:
    _manifest, by_class = _corpus()
    return _unit(by_class, unit_id)


def _expect_error(errors: list[str], needle: str) -> None:
    assert any(needle in error for error in errors), errors


def _sidecars(corpus: Path) -> None:
    """Writes the stamped sidecars the output check requires beside a manifest (the files in `app_index.ARTIFACTS`), so a test about one missing file does not also fail on the others."""
    app_index.write_app_artifacts(corpus, {}, {})


def test_the_fixture_corpus_passes_every_predicate():
    """The checked-in fixture corpus passes every predicate as shipped, so each failure in the tests below comes from the field that test breaks."""
    manifest, by_class = _corpus()
    assert check_manifest(manifest) == []
    assert check_shards(manifest, by_class) == []


# --- the fields the app draws ------------------------------------------------------------------


@pytest.mark.parametrize("side", ("before", "after"))
def test_a_highlight_reaching_past_the_run_fails_the_build(side):
    unit = _one()
    unit["highlight"][side]["x_max"] = unit["highlight"][side]["advance_total"] + 1
    _expect_error(check_unit(unit), f"highlight.{side} must satisfy")


def test_a_gate_clause_the_manifest_does_not_gloss_fails_the_build():
    manifest, by_class = _corpus()
    _unit(by_class, PLAIN_UNIT)["config_gate"][0]["feature"] = "ss99"
    _expect_error(check_shards(manifest, by_class), "feature_descriptions does not gloss")


# --- the grains that only exist across units ----------------------------------------------------


def test_human_unit_ids_out_of_triage_order_fails_the_build():
    """`human_unit_ids` lists the human units in triage order (class, group, window, id). A fragment carries no position, so the checker derives the order from the fragments with `triage_key` and compares the manifest with it."""
    manifest, by_class = _corpus()
    manifest["human_unit_ids"] = list(reversed(manifest["human_unit_ids"]))
    _expect_error(check_shards(manifest, by_class), "not the triage-ordered sequence")


def test_a_class_claiming_a_batch_its_units_do_not_occupy_fails_the_build():
    manifest, by_class = _corpus()
    manifest["classes"][0]["batches"] = [0, 4]
    _expect_error(check_shards(manifest, by_class), "are not the slices")


def test_a_batch_count_the_index_does_not_bear_out_fails_the_build():
    manifest, by_class = _corpus()
    manifest["totals"]["batches"] = 7
    _expect_error(check_shards(manifest, by_class), "totals.batches does not count")


def test_a_fragment_carrying_a_batch_fails_the_build():
    """A fragment's bytes depend only on its content. A batch is a position in the queue, so it is recorded in the manifest's index."""
    unit = _one()
    unit["batch"] = 0
    _expect_error(check_unit(unit), "carries no batch")


def test_an_id_that_is_not_its_stamps_fails_the_build():
    """The id is the content key's first 64 bits in base58. The check fails a fragment whose id belongs to different content than its stamp, and an id of any other form."""
    unit = _one()
    unit["id"] = _one(OTHER_UNIT)["id"]
    _expect_error(check_unit(unit), "must be the content key's own")
    unit["id"] = "u-0000"
    _expect_error(check_unit(unit), "base58")


def test_a_no_verdict_class_carrying_batches_fails_the_build():
    manifest, by_class = _corpus()
    manifest["classes"][0]["no_verdict"] = True
    _expect_error(check_shards(manifest, by_class), "no-verdict class must carry no batches")


# --- the files beside the manifest --------------------------------------------------------------


def test_a_missing_or_stale_sidecar_fails_the_build(tmp_path):
    """The app boots from the sidecars, not the shards. A corpus without one, or with one stamped for another manifest, would show the app another corpus's rows."""
    manifest = {"classes": [], "fonts": {}}
    (tmp_path / "index.html").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _expect_error(_check_output_files(tmp_path, manifest), f"{app_index.APP_INDEX_NAME} is missing")
    _sidecars(tmp_path)
    assert _check_output_files(tmp_path, manifest) == []
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    _expect_error(_check_output_files(tmp_path, {"classes": [], "fonts": {}}), "stamped for another manifest")


def test_a_missing_or_empty_shard_fails_the_build(tmp_path):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    _expect_error(_check_output_files(tmp_path, manifest), "is missing")
    (tmp_path / "units").mkdir()
    for meta in manifest["classes"]:
        for part in shards.class_shards(meta):
            (tmp_path / part).write_bytes(b"")
    _expect_error(_check_output_files(tmp_path, manifest), "is empty")


def _split_first_class(corpus: Path) -> None:
    """Rewrite the corpus's first class as two numbered parts, which is the layout a class past the byte cap ships in."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    meta = manifest["classes"][0]
    (whole,) = shards.class_shards(meta)
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
    shutil.copytree(FIXTURES, plain, ignore=shutil.ignore_patterns("mini", "*.md", "*.py"))
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
    """A copied font matches the manifest's sha256, but its source has since been recompiled. Only the check with `repo_root` catches this, and it holds both fonts to their sources byte for byte, the after font included, since the kern units show its `kern` lookup."""
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
    assert _check_output_files(tmp_path, manifest) == []
    errors = _check_output_files(tmp_path, manifest, REPO_ROOT)
    assert [error for error in errors if "as it stands on disk" in error] == [
        f"fonts.{side}: the copy is not {fonts[side]['source']} as it stands on disk"
        for side in ("before", "after")
    ]


def test_a_table_diff_build_with_no_snapshot_writes_a_corpus_with_no_units(tmp_path):
    """No unaccepted change is the build's normal state, so it writes a corpus with no units rather than refusing: here a machine with no snapshot, whose corpus shows the after font as both fonts, so the app shell and the kerning view boot. `engine/test_review_diff_units.py` builds the empty diff of a snapshot against its own build."""
    out = tmp_path / "out"
    manifest = build_table_diff(out, None, MINI)
    assert manifest["totals"]["units"] == 0 and manifest["classes"] == []
    assert manifest["baseline"]["present"] is False
    assert check_output_dir(out) == []


def test_a_font_that_moved_since_load_fails_the_copy(tmp_path):
    """`_copy_font` compares the copy it ships with the digest the build took when it loaded the font. Without this, a run_build that finished between the load and the copy would leave units that describe the old font beside an `after.otf` that is the new one."""
    digest = _sha256(MINI_FONT)
    record = _copy_font(MINI_FONT, tmp_path, "after.otf", "AMS Review After", REPO_ROOT, digest)
    assert record["sha256"] == digest
    with pytest.raises(SystemExit) as raised:
        _copy_font(MINI_FONT, tmp_path, "after.otf", "AMS Review After", REPO_ROOT, "0" * 64)
    assert str(MINI_FONT) in str(raised.value)


def test_a_manifest_with_no_classes_draws_no_error():
    """`check_manifest` accepts an empty `classes` list, which a corpus with no unaccepted change has (see the test above)."""
    manifest, _shards = _corpus()
    manifest["classes"] = []
    assert not [error for error in check_manifest(manifest) if "classes" in error]
