"""Tests for the per-unit index sidecar the corpus build writes beside its manifest: that it is an accurate projection of the shards, that readers reject it when its stamp does not match the manifest on disk, and that the fallback to the shards returns the same records. The verdict-update tools read units through the index whenever it is current, so a field that drops out of the projection raises no error: a standing rule stops matching and a blessed delta returns to the queue. These tests check every field of every unit against the shipped fixture shards."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from rebuild.pipeline import fingerprint, row_delta
from rebuild.review import app_index, unit_index
from rebuild.review.build import _check_output_files, _write_shard

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"


def _fixture_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    shutil.copytree(FIXTURES / "units", corpus / "units")
    shutil.copyfile(FIXTURES / "manifest.json", corpus / "manifest.json")
    return corpus


def _shard_units(corpus: Path) -> list[dict]:
    units: list[dict] = []
    for path in sorted((corpus / "units").glob("*.json")):
        units.extend(json.loads(path.read_text(encoding="utf-8")))
    return units


def _shards(corpus: Path) -> dict[str, list[dict]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((corpus / "units").glob("*.json"))
    }


def _write(corpus: Path) -> unit_index.IndexWrite:
    return unit_index.write_index(corpus, _shards(corpus).items())


def _index_files(corpus: Path) -> dict[str, bytes]:
    """Every file of the corpus's index by name: its head, its parts, a single file and any staging file."""
    return {path.name: path.read_bytes() for path in sorted(corpus.glob(f"{unit_index.INDEX_NAME}*"))}


def _single(corpus: Path) -> Path:
    """Put the corpus's index in the form a corpus from before the segment format holds, the single file `units-index.ndjson.gz` with no head, holding the index's materialized form, and return its path."""
    materialized = unit_index.materialized_index(corpus)
    assert materialized is not None
    header, lines = materialized
    held = list(lines)
    row_delta.head_path(corpus, unit_index.INDEX_NAME).unlink()
    row_delta.remove_unlisted(corpus, unit_index.INDEX_NAME, None)
    return unit_index.write_materialized_index(corpus, header, held)


def _manifest(corpus: Path) -> dict:
    return json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(corpus: Path, manifest: dict) -> None:
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def _whole(corpus: Path, shards: dict[str, list[dict]], target: Path) -> bytes:
    """Return the file `write_index` writes whole over `shards` beside a copy of the corpus's manifest in `target`, its first base."""
    target.mkdir()
    shutil.copyfile(corpus / "manifest.json", target / "manifest.json")
    assert unit_index.write_index(target, shards.items()).outcome == "base"
    return (target / row_delta.base_name(unit_index.INDEX_NAME, 0)).read_bytes()


def _materialized(corpus: Path, target: Path) -> bytes:
    """Return the corpus's materialized index written as the single file, as `build.materialize_corpus` writes it, into `target`."""
    materialized = unit_index.materialized_index(corpus, sha256=True)
    assert materialized is not None
    target.mkdir()
    return unit_index.write_materialized_index(target, *materialized).read_bytes()


def _segment_rows(corpus: Path, number: int) -> list[tuple[bytes, bytes]]:
    head = row_delta.read_head(corpus, unit_index.INDEX_NAME)
    assert head is not None and len(head.segments) >= number
    with open(corpus / head.segments[number - 1].name, "rb") as handle:
        return list(row_delta.keyed_segment_rows(handle, unit_index.INDEX_NAME, head.generation, number))


def test_class_shards_reads_either_manifest_format():
    """An `ams-review-manifest/1` class entry has one `shard` string, and the prior corpus the unit cache reads keeps that form until it is rebuilt, so both forms must be read."""
    assert unit_index.class_shards({"id": "a", "shard": "units/a.json"}) == ["units/a.json"]
    assert unit_index.class_shards({"id": "a", "shards": ["units/a.000.json", "units/a.001.json"]}) == [
        "units/a.000.json",
        "units/a.001.json",
    ]


def test_shard_paths_walks_the_parts_in_the_order_the_index_is_written(tmp_path, monkeypatch):
    """`shard_paths` and `write_index` order classes by the same key and a class's parts by the manifest's list, so the index and a shard walk return the same units in the same order, and a tool that resolves ties by "first seen" gets the same answer either way."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 32)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    fragments = {"beta": [{"id": "u-0003"}], "alpha": [{"id": f"u-{index:04d}"} for index in range(3)]}
    classes = [
        {"id": class_id, "shards": _write_shard(corpus, class_id, units)[0]}
        for class_id, units in fragments.items()
    ]
    (corpus / "manifest.json").write_text(json.dumps({"classes": classes}), encoding="utf-8")
    assert len(dict(zip([meta["id"] for meta in classes], classes))["alpha"]["shards"]) > 1

    ordered = sorted(classes, key=lambda meta: unit_index.class_shard_key(meta["id"]))
    assert unit_index.shard_paths(corpus) == [corpus / part for meta in ordered for part in meta["shards"]]
    walked = [unit["id"] for unit in unit_index.iter_shard_fragments(corpus)]
    assert walked == ["u-0000", "u-0001", "u-0002", "u-0003"]

    unit_index.write_index(corpus, [(meta["id"], fragments[meta["id"]]) for meta in classes])
    records = unit_index.load_index(corpus)
    assert records is not None
    assert [record["id"] for record in records] == walked


def test_the_index_is_the_shards_field_for_field(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    records = unit_index.load_index(corpus)
    assert records is not None
    fragments = _shard_units(corpus)
    assert len(records) == len(fragments)
    by_id = {fragment["id"]: fragment for fragment in fragments}
    slot = unit_index.slot_reader(corpus)
    for record in records:
        fragment = by_id[record["id"]]
        for field, value in record.items():
            if field in ("order", "batch"):
                assert value == slot(fragment)[field], f"{record['id']}.{field}"
            elif field == "render_groups":
                assert value == len(fragment.get("render_groups") or []), record["id"]
            elif field == "secondary_junctions":
                assert value == len(fragment.get("secondary_junctions") or []), record["id"]
            elif field == "policy":
                policy = (fragment.get("drafts") or {}).get("policy")
                expected = (
                    None
                    if not policy
                    else {
                        "file": policy["file"],
                        "keypath": policy["keypath"],
                        "suggested_record": policy.get("suggested_record"),
                    }
                )
                assert value == expected, record["id"]
            elif field in ("before", "after"):
                block = fragment.get(field) or {}
                for key, inner in value.items():
                    assert inner == (block.get(key) or []), f"{record['id']}.{field}.{key}"
            elif field in ("notation_tokens", "configs", "kinds", "provenance"):
                assert value == (fragment.get(field) or []), f"{record['id']}.{field}"
            else:
                assert value == fragment.get(field), f"{record['id']}.{field}"


def test_the_index_covers_every_field_the_verdict_update_reads(tmp_path):
    """The field set is written out rather than derived, so adding a field to the projection is an explicit change here, and removing one a tool reads fails here instead of in a fill that matches nothing."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    records = unit_index.load_index(corpus)
    assert records is not None
    assert set(records[0]) == {
        "id",
        "order",
        "batch",
        "class",
        "cluster",
        "duplicate_group",
        "group",
        "notation",
        "notation_tokens",
        "codepoints",
        "configs",
        "kinds",
        "ink_identical",
        "picture_identical",
        "junior_equivalent",
        "ink_deltas",
        "no_verdict",
        "content_key",
        "render_groups",
        "summary",
        "provenance",
        "pair",
        "secondary_junctions",
        "before",
        "after",
        "policy",
    }


def test_the_index_holds_the_units_in_shard_order(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    records = unit_index.load_index(corpus)
    assert records is not None
    assert [record["id"] for record in records] == [unit["id"] for unit in _shard_units(corpus)]


def test_a_corpus_with_no_index_falls_back_to_the_shards(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    assert unit_index.load_index(corpus) is None
    fallback = unit_index.load_units(corpus)
    _write(corpus)
    assert unit_index.load_units(corpus) == fallback


def test_an_index_stamped_for_another_manifest_is_refused(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    slot = unit_index.slot_reader(corpus)
    fallback = [unit_index.index_record(unit, **slot(unit)) for unit in _shard_units(corpus)]
    assert unit_index.load_index(corpus) == fallback

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at"] = "2099-01-01T00:00:00Z"
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    assert unit_index.index_is_current(corpus) is False
    assert unit_index.load_index(corpus) is None
    # The shards are the authority, so a stale stamp costs a slower read and never a wrong answer.
    assert unit_index.load_units(corpus) == fallback


def test_the_manifest_identity_ignores_the_assets_component(tmp_path):
    """The stamp is the manifest's identity: what it says about which units and shards it describes. The copied review UI assets are outside it, so an assets refresh can rewrite `inputs_fingerprint.static` over a served corpus and leave this sidecar, both app sidecars, and the unit-cache store valid for the manifest beside them. Any other change to the manifest, such as a new `generated_at` or an added shard part, still changes the digest."""
    assert set(unit_index.ASSET_COMPONENTS) <= set(fingerprint.STAGE_B_COMPONENTS)
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = unit_index.manifest_sha256(corpus)

    refreshed = {
        **manifest,
        "inputs_fingerprint": {**manifest["inputs_fingerprint"], "static": "refreshed"},
    }
    manifest_path.write_text(json.dumps(refreshed, indent=4) + "\n", encoding="utf-8")
    assert unit_index.manifest_sha256(corpus) == before
    assert unit_index.index_is_current(corpus) is True

    manifest_path.write_text(
        json.dumps({**refreshed, "generated_at": "2099-01-01T00:00:00Z"}), encoding="utf-8"
    )
    assert unit_index.manifest_sha256(corpus) != before

    classes = [dict(entry) for entry in refreshed["classes"]]
    classes[0]["shards"] = list(classes[0]["shards"]) + ["units/invented.json"]
    manifest_path.write_text(json.dumps({**refreshed, "classes": classes}), encoding="utf-8")
    assert unit_index.manifest_sha256(corpus) != before

    manifest_path.write_bytes(b"{ not json")
    assert unit_index.manifest_sha256(corpus) == hashlib.sha256(b"{ not json").hexdigest()
    assert unit_index.index_is_current(corpus) is False


def test_a_truncated_or_foreign_index_is_refused(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    path = _single(corpus)
    assert unit_index.load_index(corpus) is not None
    path.write_bytes(b"")
    assert unit_index.load_index(corpus) is None
    with gzip.open(path, "wb") as stream:
        stream.write((json.dumps({"format": "something-else"}) + "\n").encode())
    assert unit_index.load_index(corpus) is None


def test_writing_the_index_twice_leaves_it_as_it_was(tmp_path):
    """A first write over no index writes its records whole as generation 0's base under a new head. A second write over the same fragments finds every record as the first wrote it, so it writes the head alone, which comes out the same, so every file of the index keeps its bytes."""
    corpus = _fixture_corpus(tmp_path)
    assert _write(corpus) == unit_index.IndexWrite("base", "first", 11, len(_index_lines(corpus)))
    first = _index_files(corpus)
    assert sorted(first) == [
        row_delta.base_name(unit_index.INDEX_NAME, 0),
        row_delta.head_name(unit_index.INDEX_NAME),
    ]
    assert _write(corpus) == unit_index.IndexWrite("head")
    assert _index_files(corpus) == first


def _index_lines(corpus: Path) -> bytes:
    materialized = unit_index.materialized_index(corpus)
    assert materialized is not None
    return b"".join(materialized[1])


def test_a_failed_index_write_leaves_the_previous_index_whole(tmp_path):
    """The head is the index's commit point, renamed into place last, so a write that raises leaves the previous head and every part it lists as they were, whether its lines stop partway or come out of key order, which the write refuses rather than write a segment no merge would take, and the segment it was staging is removed, with no staging file left. The base's gzip header names the index as a whole write's does, `units-index.ndjson` with gzip's `.gz` dropped, never a staging file."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    intact = _index_files(corpus)
    base = intact[row_delta.base_name(unit_index.INDEX_NAME, 0)]
    assert base[10:].startswith(b"units-index.ndjson\0")
    slot = unit_index.slot_reader(corpus)
    lines = [
        unit_index.index_line({**fragment, "notation": "changed"}, **slot(fragment))
        for fragment in _shard_units(corpus)
    ]

    def stopping():
        yield lines[0]
        raise RuntimeError("the write stops here")

    with pytest.raises(RuntimeError, match="stops here"):
        unit_index.write_index_lines(corpus, stopping())
    assert _index_files(corpus) == intact
    with pytest.raises(ValueError, match="out of key order"):
        unit_index.write_index_lines(corpus, [lines[1], lines[0], *lines[2:]])
    assert _index_files(corpus) == intact


def test_iter_units_and_load_units_agree(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    assert list(unit_index.iter_units(corpus)) == unit_index.load_units(corpus)


def _human_and_ids(units: Sequence[Mapping]) -> tuple[list[Mapping], set[str]]:
    return [unit for unit in units if unit["batch"] is not None], {unit["id"] for unit in units}


def test_load_human_units_is_load_units_filtered_to_the_human_records(tmp_path):
    """Over the index, the byte test and the full parse agree: the human records are the records whose `batch` is not None, in the same order, and the id set covers every record. The fixture corpus has both kinds, so both branches of the classification run."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    every = unit_index.load_units(corpus)
    human, ids = _human_and_ids(every)
    assert human and len(human) < len(every)
    assert unit_index.load_human_units(corpus) == (human, ids)


@pytest.mark.parametrize("mode", ["current", "absent", "stale"])
def test_human_stream_preserves_shard_order_and_collects_all_ids(tmp_path, mode):
    corpus = _fixture_corpus(tmp_path)
    expected, expected_ids = _human_and_ids(unit_index.load_units(corpus))
    if mode != "absent":
        _write(corpus)
    if mode == "stale":
        manifest = json.loads((corpus / "manifest.json").read_text())
        manifest["generated_at"] = "stale"
        (corpus / "manifest.json").write_text(json.dumps(manifest))
    ids = set()
    units = unit_index.iter_human_units(corpus, unit_ids=ids)
    assert ids == set()
    assert list(units) == expected
    assert ids == expected_ids
    assert list(unit_index.iter_human_units(corpus)) == expected


def test_human_stream_skips_machine_json_and_parses_humans_on_demand(tmp_path, monkeypatch):
    corpus = _fixture_corpus(tmp_path)
    records = unit_index.load_units(corpus)
    human, ids = _human_and_ids(records)
    _write(corpus)
    original = json.loads
    parsed = []

    def loads(value, *args, **kwargs):
        record = original(value, *args, **kwargs)
        if isinstance(record, dict) and "id" in record:
            parsed.append(record)
            assert record["batch"] is not None
        return record

    monkeypatch.setattr(unit_index.json, "loads", loads)
    seen_ids = set()
    stream = unit_index.iter_human_units(corpus, unit_ids=seen_ids)
    assert parsed == []
    assert next(stream) == human[0]
    assert parsed == human[:1]
    assert list(stream) == human[1:]
    assert parsed == human
    assert seen_ids == ids


def test_corrupt_current_human_stream_raises_without_restarting_shards(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    human, _ids = _human_and_ids(unit_index.load_units(corpus))
    unit_index.write_index_lines(corpus, [(json.dumps(dict(human[0])) + "\n").encode(), b"broken\n"])
    stream = unit_index.iter_human_units(corpus)
    assert next(stream) == human[0]
    with pytest.raises(ValueError):
        next(stream)
    assert unit_index.load_human_units(corpus)[0] == human


def test_human_stream_fallback_keeps_legacy_fragment_batches(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"classes": [{"id": "legacy", "shard": "legacy.json"}]})
    )
    (tmp_path / "legacy.json").write_text(json.dumps([{"id": "human", "batch": 2}, {"id": "machine"}]))
    ids = set()
    [human] = unit_index.iter_human_units(tmp_path, unit_ids=ids)
    assert (human["id"], human["order"], human["batch"]) == ("human", None, 2)
    assert ids == {"human", "machine"}


def test_a_record_reads_its_place_from_the_manifest_whatever_its_line_holds(tmp_path):
    """Every reader takes a record's `order` and `batch` from the manifest beside the index (`unit_index.Places`), never from its line (#618's decision 55). A line opens with `id`, `order` and `batch`, so its id is sliced after `ID_OPEN` and its place is whatever lies before `CLASS_SEAM`: a base's line with a stale place, one with none, and a segment's line, which holds no place, all read the manifest's place for their id. That lookup also classifies a line as human or machine without parsing it. A fragment that carries its own `batch`, as only one from an older corpus does, reads null for both through the index when the triage index lacks its id, though its line holds that batch."""
    corpus = _fixture_corpus(tmp_path)
    fragment = _shard_units(corpus)[0]
    places = unit_index.Places(_manifest(corpus))
    assert list(unit_index.index_record(fragment))[:3] == ["id", "order", "batch"]
    order = places.positions[fragment["id"]]
    expected = unit_index.index_line(fragment, order=order, batch=order // places.batch_size)
    unplaced = (
        unit_index.ID_OPEN
        + fragment["id"].encode()
        + b'"'
        + expected[expected.index(unit_index.CLASS_SEAM) :]
    )
    for line in (
        unit_index.index_line(fragment, order=order + 7, batch=9),
        unit_index.index_line(fragment),
        unplaced,
    ):
        assert unit_index.line_id(line) == fragment["id"]
        assert places.placed(line) == expected
        assert unit_index.index_key(line) == [
            unit_index.class_shard_key(fragment["class"]).encode(),
            fragment["id"].encode(),
        ]
    legacy = {"id": "u-legacy", "class": "fixture-intended", "batch": 2}
    slot = unit_index.human_unit_slot(places.positions, places.batch_size, legacy)
    assert slot == {"order": None, "batch": 2}
    assert places.placed(unit_index.index_line(legacy, **slot)) == unit_index.index_line(legacy)


def test_a_unit_inserted_ahead_in_the_triage_index_moves_every_later_place_and_its_segment_holds_only_what_changed(
    tmp_path,
):
    """A human unit inserted at the front of the triage index moves the place of every human unit after it, and a write over a base whose records hold the old places writes a segment of the new record and the one whose fields changed alone, each without a place, since the compare cuts places on both sides. Every reader reads the places a whole write gives, and the materialized index, written as the single file, is that whole file byte for byte, gzip bytes included (#618's decision 48)."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    shards = _shards(corpus)
    changed = shards["join-dropped"][0]["id"]
    shards["join-dropped"][0] = {**shards["join-dropped"][0], "notation": "·Changed"}
    shards["fixture-intended"] = sorted(
        [*shards["fixture-intended"], {**shards["fixture-intended"][0], "id": "u-0inserted"}],
        key=lambda fragment: fragment["id"],
    )
    manifest = _manifest(corpus)
    manifest["human_unit_ids"] = ["u-0inserted", *manifest["human_unit_ids"]]
    _write_manifest(corpus, manifest)
    written = unit_index.write_index(corpus, shards.items())
    assert (written.outcome, written.rows) == ("segment", 2)
    rows = _segment_rows(corpus, 1)
    assert [(op, unit_index.line_id(payload)) for op, payload in rows] == [
        (b"+", "u-0inserted"),
        (b"+", changed),
    ]
    assert not any(b'"order"' in payload or b'"batch"' in payload for _op, payload in rows)
    whole = _whole(corpus, shards, tmp_path / "whole")
    assert _materialized(corpus, tmp_path / "form") == whole
    fresh = tmp_path / "whole"
    assert unit_index.load_index(corpus) == unit_index.load_index(fresh)
    assert list(unit_index.iter_units(corpus)) == list(unit_index.iter_units(fresh))
    assert unit_index.load_human_units(corpus) == unit_index.load_human_units(fresh)
    assert list(unit_index.iter_human_units(corpus)) == list(unit_index.iter_human_units(fresh))
    human = manifest["human_unit_ids"]
    for record in unit_index.iter_units(corpus):
        position = human.index(record["id"]) if record["id"] in human else None
        assert (record["order"], record["batch"]) == (
            (None, None) if position is None else (position, position // manifest["batch_size"])
        ), record["id"]


def test_a_unit_that_leaves_the_triage_index_reads_null_though_its_base_record_holds_a_place(tmp_path):
    """A human unit the manifest's triage index drops reads null for `order` and `batch`, and as a machine unit, through every reader, though the base's record still holds its old place: a write whose records all come out the same writes the head alone under the new manifest's stamp, and the readers take every place from that manifest. The units after it move up one place, and the materialized index is the file a whole write gives."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    manifest = _manifest(corpus)
    left = manifest["human_unit_ids"].pop(3)
    following = manifest["human_unit_ids"][3]
    _write_manifest(corpus, manifest)
    assert _write(corpus) == unit_index.IndexWrite("head")
    head = row_delta.read_head(corpus, unit_index.INDEX_NAME)
    assert head is not None and not head.segments
    with gzip.open(corpus / head.base.name, "rb") as stream:
        held = next(line for line in stream if unit_index.line_id(line) == left)
    assert held.startswith(unit_index.line_head(left, 3, 0))
    records = {record["id"]: record for record in unit_index.iter_units(corpus)}
    assert (records[left]["order"], records[left]["batch"]) == (None, None)
    assert (records[following]["order"], records[following]["batch"]) == (3, 0)
    human, ids = unit_index.load_human_units(corpus)
    assert left not in {record["id"] for record in human} and left in ids
    assert left not in {record["id"] for record in unit_index.iter_human_units(corpus)}
    assert _materialized(corpus, tmp_path / "form") == _whole(corpus, _shards(corpus), tmp_path / "whole")


def test_a_removed_unit_leaves_every_reader_and_a_unit_that_changes_class_is_a_tombstone_and_an_upsert(
    tmp_path,
):
    """A unit no fragment holds any more is a tombstone at its key, its class and id, and a unit whose class changed is a tombstone at its old key and an upsert at its new one, all in one segment in key order, and nothing else is written though the removed human unit moved every later place. Every reader then reads the removed unit nowhere and the moved one once, in its new class, and the materialized index is the file a whole write gives."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    shards = _shards(corpus)
    removed = shards["fixture-mismatch"].pop(0)["id"]
    moved = shards["fixture-intended"].pop(0)
    shards["join-dropped"] = sorted(
        [*shards["join-dropped"], {**moved, "class": "join-dropped"}], key=lambda fragment: fragment["id"]
    )
    manifest = _manifest(corpus)
    assert removed in manifest["human_unit_ids"]
    manifest["human_unit_ids"].remove(removed)
    _write_manifest(corpus, manifest)
    assert unit_index.write_index(corpus, shards.items()).outcome == "segment"
    assert [(op, unit_index.index_key(payload)) for op, payload in _segment_rows(corpus, 1)] == [
        (b"-", [b"fixture-intended.json", moved["id"].encode()]),
        (b"-", [b"fixture-mismatch.json", removed.encode()]),
        (b"+", [b"join-dropped.json", moved["id"].encode()]),
    ]
    classes = [(record["id"], record["class"]) for record in unit_index.iter_units(corpus)]
    assert removed not in {unit_id for unit_id, _class in classes}
    assert [found for found in classes if found[0] == moved["id"]] == [(moved["id"], "join-dropped")]
    assert removed not in unit_index.load_human_units(corpus)[1]
    ids = unit_index.iter_index_ids(corpus)
    assert ids is not None and sorted(ids) == sorted(unit_id for unit_id, _class in classes)
    assert _materialized(corpus, tmp_path / "form") == _whole(corpus, shards, tmp_path / "whole")


def test_index_is_current_reads_the_head_and_a_single_file_by_its_inline_header(tmp_path):
    """The index is current when its head's stamps name this format and the manifest beside it and every part the head lists is on disk at the size the head records: a head stamped for another manifest, a manifest moved under it, a segment missing and a base of another size each make it not current, and every reader falls back to the shards. With no head, the single file a corpus from before the segment format holds is read by its inline header, which `index_header` returns as it returns the head's stamps."""
    corpus = _fixture_corpus(tmp_path)
    _write(corpus)
    shards = _shards(corpus)
    shards["join-dropped"][0] = {**shards["join-dropped"][0], "notation": "·Changed"}
    unit_index.write_index(corpus, shards.items())
    head = row_delta.read_head(corpus, unit_index.INDEX_NAME)
    assert head is not None and len(head.segments) == 1
    stamps = {"format": unit_index.INDEX_FORMAT, "manifest_sha256": unit_index.manifest_sha256(corpus)}
    assert unit_index.index_header(corpus) == stamps and unit_index.index_is_current(corpus)
    row_delta.commit(corpus, replace(head, stamps={**stamps, "manifest_sha256": "another"}))
    assert not unit_index.index_is_current(corpus) and unit_index.load_index(corpus) is None
    row_delta.commit(corpus, head)
    manifest = _manifest(corpus)
    _write_manifest(corpus, {**manifest, "generated_at": "2099-01-01T00:00:00Z"})
    assert not unit_index.index_is_current(corpus)
    _write_manifest(corpus, manifest)
    assert unit_index.index_is_current(corpus)
    segment = corpus / head.segments[0].name
    segment.rename(corpus / "held.delta")
    assert not unit_index.index_is_current(corpus)
    assert unit_index.load_human_units(corpus) == _human_and_ids(list(unit_index.stream_shards(corpus)))
    (corpus / "held.delta").rename(segment)
    base = corpus / head.base.name
    held = base.read_bytes()
    base.write_bytes(held + b"\0")
    assert not unit_index.index_is_current(corpus)
    base.write_bytes(held)
    assert unit_index.index_is_current(corpus)
    path = _single(corpus)
    assert not row_delta.head_path(corpus, unit_index.INDEX_NAME).exists()
    with gzip.open(path, "rb") as stream:
        assert json.loads(stream.readline()) == unit_index.index_header(corpus) == stamps
    assert unit_index.index_is_current(corpus)
    _write_manifest(corpus, {**manifest, "generated_at": "2099-01-01T00:00:00Z"})
    assert not unit_index.index_is_current(corpus)


def test_load_human_units_falls_back_to_the_shards(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    assert unit_index.load_index(corpus) is None
    fallback = _human_and_ids(unit_index.load_units(corpus))
    assert unit_index.load_human_units(corpus) == fallback
    _write(corpus)
    assert unit_index.load_human_units(corpus) == fallback

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at"] = "2099-01-01T00:00:00Z"
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    assert unit_index.index_is_current(corpus) is False
    assert unit_index.load_human_units(corpus) == fallback


def test_a_truncated_or_foreign_index_refuses_the_human_load_too(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    fallback = _human_and_ids(unit_index.load_units(corpus))
    _write(corpus)
    path = _single(corpus)
    assert unit_index.load_human_units(corpus) == fallback
    path.write_bytes(b"")
    assert unit_index.load_human_units(corpus) == fallback
    with gzip.open(path, "wb") as stream:
        stream.write((json.dumps({"format": "something-else"}) + "\n").encode())
    assert unit_index.load_human_units(corpus) == fallback
    with gzip.open(path, "wb") as stream:
        stream.write(
            (
                json.dumps(
                    {
                        "format": unit_index.INDEX_FORMAT,
                        "manifest_sha256": unit_index.manifest_sha256(corpus),
                    }
                )
                + "\n"
            ).encode()
        )
        stream.write(b'{"id": "u-torn", "order": 0, "bat')
    assert unit_index.load_human_units(corpus) == fallback


def _output_manifest(corpus: Path) -> dict:
    return {"classes": [], "fonts": {}}


def test_the_contract_check_requires_the_sidecar(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    (corpus / "index.html").write_text("<html></html>", encoding="utf-8")
    app_index.write_app_artifacts(corpus, {}, {})
    manifest = _output_manifest(corpus)
    assert any("units-index" in line for line in _check_output_files(corpus, manifest))
    _write(corpus)
    assert _check_output_files(corpus, manifest) == []


def test_the_contract_check_refuses_a_sidecar_stamped_for_another_manifest(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    (corpus / "index.html").write_text("<html></html>", encoding="utf-8")
    _write(corpus)
    (corpus / "manifest.json").write_text("{}\n", encoding="utf-8")
    complaints = _check_output_files(corpus, _output_manifest(corpus))
    assert any(f"{unit_index.INDEX_NAME} is unreadable or stamped" in line for line in complaints)


def _reader_corpus(tmp_path: Path, mode: str) -> tuple[Path, list[dict]]:
    corpus = _fixture_corpus(tmp_path)
    slot = unit_index.slot_reader(corpus)
    expected = [unit_index.index_record(fragment, **slot(fragment)) for fragment in _shard_units(corpus)]
    if mode != "absent":
        _write(corpus)
    if mode == "stale":
        manifest = json.loads((corpus / "manifest.json").read_text())
        manifest["generated_at"] = "stale"
        (corpus / "manifest.json").write_text(json.dumps(manifest))
    return corpus, expected


@pytest.mark.parametrize("mode", ["current", "absent", "stale"])
@pytest.mark.parametrize("fields", [None, (), ("after", "id", "before", "configs"), ("class",)])
def test_compact_readers_preserve_fields_order_and_human_classification(tmp_path, mode, fields):
    corpus, full = _reader_corpus(tmp_path, mode)
    selected = set(full[0]) if fields is None else set(fields)
    expected = [{key: value for key, value in record.items() if key in selected} for record in full]
    human = [record for record, original in zip(expected, full) if original["batch"] is not None]
    ids = {record["id"] for record in full}
    for reader in (unit_index.load_units, unit_index.iter_units, unit_index.stream_shards):
        actual = list(reader(corpus, fields=fields))
        assert actual == expected
        for record, original in zip(actual, expected):
            assert isinstance(record, Mapping)
            assert not hasattr(record, "__dict__")
            assert list(record) == list(original)
            assert json.loads(json.dumps(dict(record))) == original
    indexed = unit_index.load_index(corpus, fields=fields)
    assert indexed == (expected if mode == "current" else None)
    seen = set()
    assert list(unit_index.iter_human_units(corpus, fields=fields, unit_ids=seen)) == human
    assert seen == ids
    assert unit_index.load_human_units(corpus, fields=fields) == (human, ids)


@pytest.mark.parametrize("mode", ["current", "absent", "stale"])
def test_unknown_projection_fields_are_rejected_by_every_reader(tmp_path, mode):
    corpus, _ = _reader_corpus(tmp_path, mode)
    for reader in (
        unit_index.load_index,
        unit_index.load_units,
        unit_index.iter_units,
        unit_index.stream_shards,
        unit_index.iter_human_units,
        unit_index.load_human_units,
    ):
        with pytest.raises(ValueError, match="misspelled_field"):
            result = reader(corpus, fields=("id", "misspelled_field"))
            if result is not None:
                list(result)


def test_projection_omissions_cannot_silently_disable_a_rule(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    [record, *_] = unit_index.load_units(corpus, fields=("id", "no_verdict"))
    assert record.get("no_verdict", "default") == record["no_verdict"]
    for read in (lambda: record["before"], lambda: record.get("before"), lambda: record.get("before", {})):
        with pytest.raises((ValueError, KeyError), match="before"):
            read()
    assert record.get("not_a_unit_field", "default") == "default"
    with pytest.raises(KeyError):
        record["not_a_unit_field"]
    with pytest.raises(TypeError):
        record["id"] = "changed"  # pyright: ignore[reportIndexIssue]


def test_repeated_nested_values_share_storage_without_changing_json_types(tmp_path):
    fragment = {
        "id": "first",
        "class": "repeated class value",
        "configs": ["senior config value"],
        "before": {"glyphs": ["repeated glyph value"], "junctions": [5]},
        "after": {"cells": ["repeated cell value"], "junctions": [5]},
        "ink_deltas": {"senior config value": "same delta value"},
    }
    (tmp_path / "manifest.json").write_text(json.dumps({"classes": []}))
    unit_index.write_index(tmp_path, [("test", [fragment, {**fragment, "id": "second"}])])
    first, second = unit_index.load_units(tmp_path)
    assert first == unit_index.index_record(fragment)
    for field in ("class", "configs", "before", "after", "ink_deltas"):
        assert first[field] is second[field]
    assert isinstance(first["before"], dict)
    assert isinstance(first["before"]["glyphs"], list)
    assert first["before"]["junctions"] is first["after"]["junctions"]
    assert first["configs"][0] is next(iter(first["ink_deltas"]))
    assert json.loads(json.dumps(dict(first))) == unit_index.index_record(fragment)


def _standing_unit_fields(source: str) -> set[str]:
    fields = set()
    for node in ast.walk(ast.parse(source)):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "unit":
            key = node.slice
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "unit"
            and node.func.attr == "get"
            and node.args
        ):
            key = node.args[0]
        if key is not None:
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), ast.dump(node)
            fields.add(key.value)
    return fields


def test_pool_preserves_distinct_json_scalar_types_inside_equal_containers(tmp_path):
    values = [True, 1, 1.0, False, 0, 0.0, -0.0]
    (tmp_path / "manifest.json").write_text(json.dumps({"classes": []}))
    unit_index.write_index_lines(
        tmp_path,
        (
            unit_index.index_line({"id": f"unit-{index}", "before": {"junctions": [value]}})
            for index, value in enumerate(values)
        ),
    )
    records = unit_index.load_units(tmp_path, fields=("before",))
    junctions = [record["before"]["junctions"] for record in records]
    assert len({id(value) for value in junctions}) == len(values)
    for actual, expected in zip(junctions, values):
        assert type(actual[0]) is type(expected)
        assert json.dumps(actual) == json.dumps([expected])


def test_standing_projection_covers_the_fields_read_by_both_consumers(tmp_path):
    from rebuild.tools import standing_daemon

    expected = {
        "id",
        "batch",
        "class",
        "duplicate_group",
        "notation",
        "codepoints",
        "configs",
        "ink_deltas",
        "no_verdict",
        "content_key",
        "render_groups",
        "pair",
        "secondary_junctions",
        "before",
        "after",
    }
    derived = set()
    for name in ("standing_probe.py", "standing_verdicts.py"):
        derived.update(_standing_unit_fields((REPO_ROOT / "rebuild" / "tools" / name).read_text()))
    assert _standing_unit_fields("print(f\"{unit['class']} {unit.get('duplicate_group')}\")") == {
        "class",
        "duplicate_group",
    }
    assert derived == expected == standing_daemon.UNIT_FIELDS
    corpus, full = _reader_corpus(tmp_path, "current")
    assert unit_index.load_units(corpus, fields=standing_daemon.UNIT_FIELDS) == [
        {key: value for key, value in record.items() if key in expected} for record in full
    ]


def test_streaming_discards_unique_containers_after_the_reader_pool_fills(tmp_path, monkeypatch):
    monkeypatch.setattr(unit_index, "_CONTAINER_POOL_LIMIT", 32)
    readers = []
    original = unit_index._RecordReader

    class ObservedReader(original):
        def __init__(self, fields):
            super().__init__(fields)
            readers.append(self)

    monkeypatch.setattr(unit_index, "_RecordReader", ObservedReader)
    (tmp_path / "manifest.json").write_text(json.dumps({"classes": []}))
    unit_index.write_index_lines(
        tmp_path,
        (
            unit_index.index_line({"id": f"unit-{index}", "configs": [f"unique-{index}"]})
            for index in range(512)
        ),
    )
    stream = unit_index.iter_units(tmp_path, fields=("id", "configs"))
    consumed = 0
    for index, record in enumerate(stream):
        assert record == {"id": f"unit-{index}", "configs": [f"unique-{index}"]}
        assert len(readers[-1].pool) <= 32
        consumed += 1
    assert consumed == 512
    assert len(readers) == 1
    assert len(readers[0].pool) == 32
    next_stream = unit_index.iter_units(tmp_path, fields=("configs",))
    next(next_stream)
    assert len(readers) == 2
    assert readers[0].pool is not readers[1].pool
    assert len(readers[1].pool) == 1
    list(next_stream)


def _appended(mini_corpus: Path, mini_bundle, tmp_path: Path) -> tuple[Path, Path]:
    """Return a copy of the mini corpus rebuilt over an audit edit that retags two windows into one class, so that class holds an overflow part and the classes they left hold dead slots, beside its materialized form (`build.materialize_corpus`)."""
    from rebuild.review import build
    from rebuild.test_unit_cache import _build, _edited_audit

    corpus = tmp_path / "corpus"
    shutil.copytree(mini_corpus, corpus)
    _build(corpus, mini_bundle, audit_path=_edited_audit(tmp_path, 2), jobs=1)
    assert list((corpus / "units").glob("*.x000.json"))
    form = tmp_path / "form"
    build.materialize_corpus(corpus, form)
    return corpus, form


@pytest.fixture
def appended_corpus(mini_corpus, mini_bundle, tmp_path) -> tuple[Path, Path]:
    """`_appended`, whose appends the build writes deflated (#618's change 35)."""
    return _appended(mini_corpus, mini_bundle, tmp_path)


@pytest.fixture
def plain_appended_corpus(mini_corpus, mini_bundle, tmp_path, monkeypatch) -> tuple[Path, Path]:
    """`_appended` with its appends written plain, so its parts are ASCII JSON throughout and the index can walk them."""
    from rebuild.review import build

    real = build._ShardWriter.open

    def plain(self, class_id, prior_parts=(), *, append=False, deflate=False):
        real(self, class_id, prior_parts, append=append)

    monkeypatch.setattr(build._ShardWriter, "open", plain)
    found = _appended(mini_corpus, mini_bundle, tmp_path)
    monkeypatch.setattr(build._ShardWriter, "open", real)
    return found


def _dead_bytes(corpus: Path) -> int:
    """The bytes of the corpus's parts that no live fragment takes, each live fragment counted with the comma or bracket before it, as `build.PriorShards.dead_and_live` counts them over the slots the unit store gives."""
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "store"
        held = sum(
            (corpus / part).stat().st_size - len(unit_index.PART_CLOSING)
            for meta in live.ordered()
            for part in unit_index.class_shards(meta)
        )
        return held - sum(slot.length + 1 for meta in live.ordered() for slot in live.slots(meta))


def _every_slot(corpus: Path) -> list[str]:
    """Every element of every part the manifest lists, dead slots included, as a reader that walks the parts would take them."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    return [
        unit["id"]
        for meta in manifest["classes"]
        for part in unit_index.class_shards(meta)
        for unit in json.loads((corpus / part).read_text(encoding="utf-8"))
    ]


def test_every_whole_part_reader_reads_the_live_fragments_its_materialized_form_holds(appended_corpus):
    """A build that appends a changed fragment to its class's overflow parts leaves its old slot dead, so a reader that walked the parts would read units twice, and holds the append deflated (#618's change 35), which a reader that took its bytes as they are would fail to parse. Every reader of fragments goes through the live-fragment iterator instead, which reads the live slots and inflates a deflated one, so over a corpus with deflated overflow parts and dead slots each reads exactly what it reads over the corpus's materialized form, all plain, in the same order: the shard walk the verdict update's fallbacks take, the triage export, the re-key map, the build's output check, the dictionary's sample and the plan's walk for addressless records."""
    from rebuild.review import build, export, unit_cache
    from rebuild.tools import fragment_dictionary, rekey_verdicts

    corpus, form = appended_corpus
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "store"
        assert any(slot.inflated for meta in live.ordered() for slot in live.slots(meta))
    walked = [fragment["id"] for fragment in unit_index.iter_shard_fragments(corpus)]
    assert _dead_bytes(corpus) > 0 and len(walked) == len(set(walked))
    assert [fragment["id"] for fragment in unit_index.iter_shard_fragments(form)] == walked
    assert list(unit_index.iter_shard_fragments(corpus)) == list(unit_index.iter_shard_fragments(form))
    assert list(export.load_units(corpus)[1].items()) == list(export.load_units(form)[1].items())
    assert list(rekey_verdicts.iter_fragments(corpus)) == list(rekey_verdicts.iter_fragments(form))
    assert build.check_output_dir(corpus) == build.check_output_dir(form) == []
    assert fragment_dictionary.build_dictionary(corpus) == fragment_dictionary.build_dictionary(form)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    wanted = unit_cache.WantedFragments()
    for meta in manifest["classes"]:
        for unit_id in walked:
            word = unit_cache.id_word(unit_id)
            assert word is not None
            wanted.add(meta["id"], word)
    found = {}
    for directory in (corpus, form):
        located = unit_cache.locate_prior_fragments(directory, wanted)
        with unit_cache.PriorFragmentReader(directory) as reader:
            found[directory] = {
                unit_id: reader.read(place)
                for meta in manifest["classes"]
                for unit_id in walked
                if (place := located.find(meta["id"], unit_id)) is not None
            }
    assert found[corpus] == found[form] and len(found[corpus]) == len(walked)


def test_the_iterator_takes_the_index_where_the_store_does_not_describe_the_shards_and_refuses_neither(
    plain_appended_corpus, mini_corpus, tmp_path
):
    """Over a corpus whose appends are plain (`plain_appended_corpus`), so the index can walk its parts: where the unit store does not describe the shards beside it (a part missing under its head, or a record that names a slot outside the parts of the class it records, such as the dead slot a unit left when it changed class), the iterator takes each id the index lists from its last occurrence in manifest order, overflow parts last, and reads what the store would give. A corpus with no store head, as one whose store was deleted by hand, is taken from its index too whenever the index is current, so it is read without its dead slots whether it lists an overflow part or only holds the slot a unit left in a base part when its class lost it on a pass that appended nothing. A corpus with a store head or an overflow part whose store and index are both unusable is refused rather than walked with its dead slots."""
    from rebuild.review import unit_cache
    from rebuild.test_unit_cache import _rewrite_store

    corpus, _form = plain_appended_corpus
    expected = list(unit_index.iter_shard_fragments(corpus))
    overflow = next((corpus / "units").glob("*.x000.json"))
    moved = json.loads(overflow.read_bytes())[0]["id"]
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    dead = next(
        (part, start, length)
        for meta in manifest["classes"]
        for part in unit_index.class_shards(meta)
        if part != f"units/{overflow.name}"
        for start, length, fragment in unit_index._walk_part(corpus / part)
        if fragment.get("id") == moved
    )
    _rewrite_store(
        corpus, lambda record: {**record, "address": list(dead)} if record["id"] == moved else record
    )
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "index"
    assert list(unit_index.iter_shard_fragments(corpus)) == expected
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    assert head is not None
    (corpus / head.base.name).unlink()
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "index"
    assert list(unit_index.iter_shard_fragments(corpus)) == expected
    head_path = row_delta.head_path(corpus, unit_cache.STORE_NAME)
    head_path.rename(corpus / "held.head")
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "index"
    assert list(unit_index.iter_shard_fragments(corpus)) == expected
    (corpus / "held.head").rename(head_path)
    index_head = row_delta.read_head(corpus, unit_index.INDEX_NAME)
    assert index_head is not None
    row_delta.commit(corpus, replace(index_head, stamps={**index_head.stamps, "manifest_sha256": "another"}))
    with pytest.raises(
        unit_index.LayoutError, match="has a unit store head.*cannot be told from its dead slots"
    ):
        unit_index.LiveCorpus(corpus)
    head_path.unlink()
    with pytest.raises(
        unit_index.LayoutError, match="lists an overflow part.*cannot be told from its dead slots"
    ):
        unit_index.LiveCorpus(corpus)
    shrunk = tmp_path / "shrunk"
    shutil.copytree(mini_corpus, shrunk)
    with unit_index.LiveCorpus(shrunk) as live:
        lost = next(
            slots[-1].unit_id
            for meta in live.ordered()
            if len(unit_index.class_shards(meta)) == 1 and len(slots := live.slots(meta)) > 1
        )
    materialized = unit_index.materialized_index(shrunk)
    assert materialized is not None
    kept = [line for line in materialized[1] if unit_index.line_id(line) != lost]
    assert unit_index.write_index_lines(shrunk, kept).outcome == "segment"
    row_delta.head_path(shrunk, unit_cache.STORE_NAME).unlink()
    assert lost in _every_slot(shrunk) and not list((shrunk / "units").glob("*.x000.json"))
    with unit_index.LiveCorpus(shrunk) as live:
        assert live.source == "index"
    assert [fragment["id"] for fragment in unit_index.iter_shard_fragments(shrunk)] == [
        fragment["id"] for fragment in unit_index.iter_shard_fragments(mini_corpus) if fragment["id"] != lost
    ]


def test_only_the_store_reads_a_corpus_that_holds_a_deflated_fragment(appended_corpus, tmp_path):
    """The index and the parts walk each part as JSON, and a deflated fragment (#618's change 35) is a raw deflate stream that is not ASCII, so where either drives the iterator a part that is not ASCII is refused (`unit_index.LayoutError`) instead of yielding nothing, which would drop its class's every unit without a word. Here the store stops describing a corpus with a deflated append, and the index refuses it, as every whole-part reader then does, the build's output check among them; and a corpus whose deflated append was folded into a class's base, with neither a store head nor a current index, is refused by the parts."""
    from rebuild.review import build, unit_cache
    from rebuild.test_unit_cache import RETAG_CLASS, _rewrite_store

    corpus, _form = appended_corpus
    folded = tmp_path / "folded"
    shutil.copytree(corpus, folded)
    _rewrite_store(corpus, lambda record: record, parts=[])
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "index"
        with pytest.raises(unit_index.LayoutError, match="not ASCII"):
            list(live.fragments())
    with pytest.raises(unit_index.LayoutError, match="not ASCII"):
        list(unit_index.iter_shard_fragments(corpus))
    assert any("not ASCII" in error for error in build.check_output_dir(corpus))
    manifest = json.loads((folded / "manifest.json").read_text(encoding="utf-8"))
    meta = next(meta for meta in manifest["classes"] if meta["id"] == RETAG_CLASS)
    base, overflow = meta["shards"]
    held = (folded / base).read_bytes()[: -len(unit_index.PART_CLOSING)]
    appended = (folded / overflow).read_bytes()[1:]
    (folded / base).write_bytes(held + b"," + appended)
    (folded / overflow).unlink()
    meta["shards"] = [base]
    build._write_json(folded / "manifest.json", manifest)
    row_delta.head_path(folded, unit_cache.STORE_NAME).unlink()
    with unit_index.LiveCorpus(folded) as live:
        assert live.source == "parts"
        with pytest.raises(unit_index.LayoutError, match="not ASCII"):
            list(live.fragments())


def test_a_deflated_slot_that_does_not_inflate_to_its_unit_is_refused(appended_corpus):
    """A deflated slot is read through one checked inflate (`unit_index.inflate_fragment`), so the iterator's read (`LiveCorpus.read`) and the materialized form, which reads every part's fragments through it, refuse a deflated stream cut short, one with bytes after its end, one that inflates to another length, and one that inflates to another unit, each with `unit_index.LayoutError`, rather than hand a reader bytes that are not the unit's fragment. The codec alone refuses the same streams."""
    from dataclasses import replace

    from rebuild.review import build
    from rebuild.test_unit_cache import _rewrite_store

    corpus, _form = appended_corpus
    with unit_index.LiveCorpus(corpus) as live:
        held = [slot for meta in live.ordered() for slot in live.slots(meta) if slot.inflated]
        assert len(held) == 2 and held[0].part == held[1].part
        first, second = held
        body = live.read(first)
        assert unit_index.holds_unit(body, first.unit_id) and len(body) == first.inflated
        stored = (corpus / first.part).read_bytes()[first.start : first.start + first.length]
        assert (
            unit_index.inflate_fragment(stored, first.inflated) == unit_index.inflate_fragment(stored) == body
        )
        refused = {
            "cut short": replace(first, length=first.length - 1),
            "bytes after its end": replace(first, length=first.length + 1),
            "another length": replace(first, inflated=first.inflated - 1),
            "another unit": replace(second, unit_id=first.unit_id),
        }
        for why, slot in refused.items():
            with pytest.raises(unit_index.LayoutError):
                live.read(slot)
            if why != "another unit":
                with pytest.raises(ValueError):
                    unit_index.inflate_fragment(
                        (corpus / slot.part).read_bytes()[slot.start : slot.start + slot.length],
                        slot.inflated,
                    )
    _rewrite_store(
        corpus,
        lambda record: (
            {**record, "address": [second.part, second.start, second.length, second.inflated]}
            if record["id"] == first.unit_id
            else record
        ),
    )
    with build.MaterializedCorpus(corpus) as materialized:
        with pytest.raises(unit_index.LayoutError, match="another unit"):
            for _name, chunks in materialized.part_files():
                for _chunk in chunks:
                    pass


# The readers outside the live-fragment iterator allowed to read shard part names through `unit_index.class_shards`, `unit_index.shard_paths`, `unit_index._walk_elements` or `unit_index._walk_part`, each with what it reads them for. None reads a fragment out of a part, which only the iterator does (`unit_index.LiveCorpus`), so none can read a dead slot. The whole of rebuild/review/unit_index.py, the iterator's module, is allowed.
SHARD_PART_NAME_READERS = {
    ("rebuild/review/build.py", "_prune_orphan_shards"): "the parts the manifest lists, which it keeps",
    (
        "rebuild/review/build.py",
        "_prior_parts",
    ): "the previous corpus's parts, which the shard writer appends to",
    ("rebuild/review/build.py", "_build_m1"): "the parts whose sizes the unit store's head records",
    ("rebuild/review/build.py", "_check_output_files"): "each listed part's existence and size",
    ("rebuild/review/build.py", "check_output_dir"): "which classes have every part on disk",
    (
        "rebuild/review/build.py",
        "MaterializedCorpus.__init__",
    ): "each class's parts on disk, beside its materialized ones",
    ("rebuild/tools/artifact_cycle.py", "corpus_build_skippable"): "each listed part's existence",
    ("rebuild/tools/memory_series.py", "null_routes"): "each class's part count, for its staging names",
}


def _shard_reader_uses(path: Path) -> set[str]:
    """Return the qualified names of the functions in `path` that name `class_shards`, `shard_paths`, `_walk_elements` or `_walk_part`, other than by defining them, as a name, an attribute or an import, aliased or not."""
    names = {"class_shards", "shard_paths", "_walk_elements", "_walk_part"}
    found: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, (*scope, child.name))
                continue
            named = (
                child.id
                if isinstance(child, ast.Name)
                else (
                    child.attr
                    if isinstance(child, ast.Attribute)
                    else child.name if isinstance(child, ast.alias) else None
                )
            )
            if named in names:
                found.add(".".join(scope) or "<module>")
            visit(child, scope)

    visit(ast.parse(path.read_text(encoding="utf-8")), ())
    return found


def test_only_the_iterator_reads_shard_parts_and_every_reader_of_part_names_is_named():
    """The contract the live-fragment iterator stands on: outside rebuild/review/unit_index.py, no rebuild/ module but the tests names `class_shards`, `shard_paths`, `_walk_elements` or `_walk_part`, by a use or an import under any alias, except the readers of part names `SHARD_PART_NAME_READERS` lists with their reasons, and each of those still does, so the list cannot go stale."""
    from rebuild.tools import artifact_cycle

    closure = artifact_cycle.rebuild_gate_closure_files(REPO_ROOT)
    assert closure is not None, "the rebuild closure needs git, and without it there is nothing to sweep"
    modules = [
        relative
        for relative in closure
        if relative.endswith(".py")
        and relative.startswith("rebuild/")
        and not Path(relative).name.startswith("test_")
        and Path(relative).name != "conftest.py"
        and relative != "rebuild/review/unit_index.py"
    ]
    assert "rebuild/review/build.py" in modules, "the sweep reached nothing; the closure moved"
    found = {(relative, scope) for relative in modules for scope in _shard_reader_uses(REPO_ROOT / relative)}
    assert found == set(SHARD_PART_NAME_READERS)
