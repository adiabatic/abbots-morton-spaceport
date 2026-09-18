"""The per-unit index sidecar the surface build writes beside its manifest: that it is a true projection of the shards, that it is refused rather than trusted when its stamp does not describe the manifest on disk, and that the fallback to the shards answers identically. The plumbing reads the index and never the shards now, so a field that drifts out of the projection does not read as an error — a standing rule quietly stops matching and a blessed delta re-queues. This is what stops that: every field, every unit, held against the shipped fixture shards."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from rebuild.pipeline import fingerprint
from rebuild.review import app_index, unit_index
from rebuild.review.build import _check_output_files, _write_shard

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"


def _fixture_surface(tmp_path: Path) -> Path:
    surface = tmp_path / "surface"
    shutil.copytree(FIXTURES / "units", surface / "units")
    shutil.copyfile(FIXTURES / "manifest.json", surface / "manifest.json")
    return surface


def _shard_units(surface: Path) -> list[dict]:
    units: list[dict] = []
    for path in sorted((surface / "units").glob("*.json")):
        units.extend(json.loads(path.read_text(encoding="utf-8")))
    return units


def _write(surface: Path) -> Path:
    shards = [
        (path.stem, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((surface / "units").glob("*.json"))
    ]
    return unit_index.write_index(surface, shards)


def test_class_shards_reads_either_manifest_format():
    """A `ams-review-manifest/1` class carries one `shard` string, and the prior surface the unit cache reads is that shape until it is rebuilt — so both spellings have to answer."""
    assert unit_index.class_shards({"id": "a", "shard": "units/a.json"}) == ["units/a.json"]
    assert unit_index.class_shards({"id": "a", "shards": ["units/a.000.json", "units/a.001.json"]}) == [
        "units/a.000.json",
        "units/a.001.json",
    ]


def test_shard_paths_walks_the_parts_in_the_order_the_index_is_written(tmp_path, monkeypatch):
    """`shard_paths` and `write_index` order classes by the same key and a class's parts by the manifest's own list, so the index and a shard walk hand a reader the same units in the same order — which is what lets a tool resolve ties by "first seen" either way."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 32)
    surface = tmp_path / "surface"
    surface.mkdir()
    fragments = {"beta": [{"id": "u-0003"}], "alpha": [{"id": f"u-{index:04d}"} for index in range(3)]}
    classes = [
        {"id": class_id, "shards": _write_shard(surface, class_id, units)[0]}
        for class_id, units in fragments.items()
    ]
    (surface / "manifest.json").write_text(json.dumps({"classes": classes}), encoding="utf-8")
    assert len(dict(zip([meta["id"] for meta in classes], classes))["alpha"]["shards"]) > 1

    ordered = sorted(classes, key=lambda meta: unit_index.class_shard_key(meta["id"]))
    assert unit_index.shard_paths(surface) == [surface / part for meta in ordered for part in meta["shards"]]
    walked = [unit["id"] for unit in unit_index.iter_shard_fragments(surface)]
    assert walked == ["u-0000", "u-0001", "u-0002", "u-0003"]

    unit_index.write_index(surface, [(meta["id"], fragments[meta["id"]]) for meta in classes])
    records = unit_index.load_index(surface)
    assert records is not None
    assert [record["id"] for record in records] == walked


def test_the_index_is_the_shards_field_for_field(tmp_path):
    surface = _fixture_surface(tmp_path)
    _write(surface)
    records = unit_index.load_index(surface)
    assert records is not None
    fragments = _shard_units(surface)
    assert len(records) == len(fragments)
    by_id = {fragment["id"]: fragment for fragment in fragments}
    slot = unit_index.slot_reader(surface)
    for record in records:
        fragment = by_id[record["id"]]
        for field, value in record.items():
            if field in ("order", "batch"):
                assert value == slot(fragment)[field], f"{record['id']}.{field}"
            elif field == "render_groups":
                assert value == len(fragment.get("render_groups") or []), record["id"]
            elif field == "secondary_seams":
                assert value == len(fragment.get("secondary_seams") or []), record["id"]
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


def test_the_index_covers_every_field_the_plumbing_reads(tmp_path):
    """Named rather than derived, so adding a field to the projection is a deliberate act and removing one that a tool reads fails here rather than in a fill that silently matches nothing."""
    surface = _fixture_surface(tmp_path)
    _write(surface)
    records = unit_index.load_index(surface)
    assert records is not None
    assert set(records[0]) == {
        "id",
        "order",
        "batch",
        "class",
        "cluster",
        "echo",
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
        "secondary_seams",
        "before",
        "after",
        "policy",
    }


def test_the_index_holds_the_units_in_shard_order(tmp_path):
    surface = _fixture_surface(tmp_path)
    _write(surface)
    records = unit_index.load_index(surface)
    assert records is not None
    assert [record["id"] for record in records] == [unit["id"] for unit in _shard_units(surface)]


def test_a_surface_with_no_index_falls_back_to_the_shards(tmp_path):
    surface = _fixture_surface(tmp_path)
    assert unit_index.load_index(surface) is None
    fallback = unit_index.load_units(surface)
    _write(surface)
    assert unit_index.load_units(surface) == fallback


def test_an_index_stamped_for_another_manifest_is_refused(tmp_path):
    surface = _fixture_surface(tmp_path)
    _write(surface)
    slot = unit_index.slot_reader(surface)
    fallback = [unit_index.index_record(unit, **slot(unit)) for unit in _shard_units(surface)]
    assert unit_index.load_index(surface) == fallback

    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at"] = "2099-01-01T00:00:00Z"
    (surface / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    assert unit_index.index_is_current(surface) is False
    assert unit_index.load_index(surface) is None
    # The shards are the authority, so a stale stamp costs a slower read and never a wrong answer.
    assert unit_index.load_units(surface) == fallback


def test_the_manifest_identity_ignores_the_assets_component(tmp_path):
    """The stamp is the manifest's identity — what it says about which units and shards it describes — and the copied review UI assets are outside it. That is what lets an assets refresh rewrite `inputs_fingerprint.static` over a served surface and leave this sidecar, both app sidecars and the unit-cache store still describing the manifest beside them; anything the readers actually resolve against still moves the digest."""
    assert set(unit_index.ASSET_COMPONENTS) <= set(fingerprint.STAGE_B_COMPONENTS)
    surface = _fixture_surface(tmp_path)
    _write(surface)
    manifest_path = surface / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = unit_index.manifest_sha256(surface)

    refreshed = {
        **manifest,
        "inputs_fingerprint": {**manifest["inputs_fingerprint"], "static": "refreshed"},
    }
    manifest_path.write_text(json.dumps(refreshed, indent=4) + "\n", encoding="utf-8")
    assert unit_index.manifest_sha256(surface) == before
    assert unit_index.index_is_current(surface) is True

    manifest_path.write_text(
        json.dumps({**refreshed, "generated_at": "2099-01-01T00:00:00Z"}), encoding="utf-8"
    )
    assert unit_index.manifest_sha256(surface) != before

    classes = [dict(entry) for entry in refreshed["classes"]]
    classes[0]["shards"] = list(classes[0]["shards"]) + ["units/invented.json"]
    manifest_path.write_text(json.dumps({**refreshed, "classes": classes}), encoding="utf-8")
    assert unit_index.manifest_sha256(surface) != before

    manifest_path.write_bytes(b"{ not json")
    assert unit_index.manifest_sha256(surface) == hashlib.sha256(b"{ not json").hexdigest()
    assert unit_index.index_is_current(surface) is False


def test_a_truncated_or_foreign_index_is_refused(tmp_path):
    surface = _fixture_surface(tmp_path)
    path = _write(surface)
    path.write_bytes(b"")
    assert unit_index.load_index(surface) is None
    with gzip.open(path, "wb") as stream:
        stream.write((json.dumps({"format": "something-else"}) + "\n").encode())
    assert unit_index.load_index(surface) is None


def test_writing_the_index_twice_writes_the_same_bytes(tmp_path):
    surface = _fixture_surface(tmp_path)
    first = _write(surface).read_bytes()
    assert _write(surface).read_bytes() == first


def test_iter_units_and_load_units_agree(tmp_path):
    surface = _fixture_surface(tmp_path)
    _write(surface)
    assert list(unit_index.iter_units(surface)) == unit_index.load_units(surface)


def _human_and_ids(units: Sequence[Mapping]) -> tuple[list[Mapping], set[str]]:
    return [unit for unit in units if unit["batch"] is not None], {unit["id"] for unit in units}


def test_load_human_units_is_load_units_filtered_to_the_human_records(tmp_path):
    """The byte test over the index and the parse agree: the human records are exactly the records whose `batch` is not None, in the same order, and the id set is every record's. The fixture surface holds both kinds, so both branches of the classification run."""
    surface = _fixture_surface(tmp_path)
    _write(surface)
    every = unit_index.load_units(surface)
    human, ids = _human_and_ids(every)
    assert human and len(human) < len(every)
    assert unit_index.load_human_units(surface) == (human, ids)


@pytest.mark.parametrize("mode", ["current", "absent", "stale"])
def test_human_stream_preserves_shard_order_and_collects_all_ids(tmp_path, mode):
    surface = _fixture_surface(tmp_path)
    expected, expected_ids = _human_and_ids(unit_index.load_units(surface))
    if mode != "absent":
        _write(surface)
    if mode == "stale":
        manifest = json.loads((surface / "manifest.json").read_text())
        manifest["generated_at"] = "stale"
        (surface / "manifest.json").write_text(json.dumps(manifest))
    ids = set()
    units = unit_index.iter_human_units(surface, unit_ids=ids)
    assert ids == set()
    assert list(units) == expected
    assert ids == expected_ids
    assert list(unit_index.iter_human_units(surface)) == expected


def test_human_stream_skips_machine_json_and_parses_humans_on_demand(tmp_path, monkeypatch):
    surface = _fixture_surface(tmp_path)
    records = unit_index.load_units(surface)
    human, ids = _human_and_ids(records)
    _write(surface)
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
    stream = unit_index.iter_human_units(surface, unit_ids=seen_ids)
    assert parsed == []
    assert next(stream) == human[0]
    assert parsed == human[:1]
    assert list(stream) == human[1:]
    assert parsed == human
    assert seen_ids == ids


def test_corrupt_current_human_stream_raises_without_restarting_shards(tmp_path):
    surface = _fixture_surface(tmp_path)
    human, _ids = _human_and_ids(unit_index.load_units(surface))
    unit_index.write_index_lines(surface, [(json.dumps(dict(human[0])) + "\n").encode(), b"broken\n"])
    stream = unit_index.iter_human_units(surface)
    assert next(stream) == human[0]
    with pytest.raises(ValueError):
        next(stream)
    assert unit_index.load_human_units(surface)[0] == human


def test_human_stream_fallback_keeps_legacy_fragment_batches(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"classes": [{"id": "legacy", "shard": "legacy.json"}]})
    )
    (tmp_path / "legacy.json").write_text(json.dumps([{"id": "human", "batch": 2}, {"id": "machine"}]))
    ids = set()
    [human] = unit_index.iter_human_units(tmp_path, unit_ids=ids)
    assert (human["id"], human["order"], human["batch"]) == ("human", None, 2)
    assert ids == {"human", "machine"}


def test_an_index_line_opens_with_the_id_order_and_batch(tmp_path):
    """`load_human_units` reads a line's id and its place in the queue off the head cut at `CLASS_SEAM` without parsing it, which rests on `index_record` opening every record with `id`, `order`, `batch` in that order. A key added before `batch` or a moved `class` would reclassify every record on the surface with no error, so the order is held here: the head closes as a record of exactly those three keys, the id slices out of it, and the tail names a machine record exactly when the batch is null."""
    fragment = _shard_units(_fixture_surface(tmp_path))[0]
    assert list(unit_index.index_record(fragment))[:3] == ["id", "order", "batch"]
    for order, batch in ((7, 0), (None, None)):
        line = unit_index.index_line(fragment, order=order, batch=batch)
        head = line[: line.index(unit_index.CLASS_SEAM)]
        assert head.startswith(unit_index.ID_OPEN)
        assert json.loads(head + b"}") == {"id": fragment["id"], "order": order, "batch": batch}
        assert head[len(unit_index.ID_OPEN) : head.index(unit_index.ORDER_SEAM)].decode() == fragment["id"]
        assert head.endswith(unit_index.MACHINE_TAIL) is (batch is None)


def test_a_fragment_carrying_its_own_batch_reads_as_human():
    """`workload_slot`'s old-surface branch — no `order`, a `batch` off the fragment itself — writes a head ending in a number rather than `null`, so the record classifies as human, the one shape where `batch` is non-null without an `order`."""
    fragment = {"id": "u-0001", "batch": 2}
    slot = unit_index.workload_slot({}, 300, fragment)
    assert slot == {"order": None, "batch": 2}
    line = unit_index.index_line(fragment, **slot)
    head = line[: line.index(unit_index.CLASS_SEAM)]
    assert not head.endswith(unit_index.MACHINE_TAIL)
    assert json.loads(head + b"}")["batch"] == 2


def test_load_human_units_falls_back_to_the_shards(tmp_path):
    surface = _fixture_surface(tmp_path)
    assert unit_index.load_index(surface) is None
    fallback = _human_and_ids(unit_index.load_units(surface))
    assert unit_index.load_human_units(surface) == fallback
    _write(surface)
    assert unit_index.load_human_units(surface) == fallback

    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at"] = "2099-01-01T00:00:00Z"
    (surface / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    assert unit_index.index_is_current(surface) is False
    assert unit_index.load_human_units(surface) == fallback


def test_a_truncated_or_foreign_index_refuses_the_human_load_too(tmp_path):
    surface = _fixture_surface(tmp_path)
    fallback = _human_and_ids(unit_index.load_units(surface))
    path = _write(surface)
    path.write_bytes(b"")
    assert unit_index.load_human_units(surface) == fallback
    with gzip.open(path, "wb") as stream:
        stream.write((json.dumps({"format": "something-else"}) + "\n").encode())
    assert unit_index.load_human_units(surface) == fallback
    with gzip.open(path, "wb") as stream:
        stream.write(
            (
                json.dumps(
                    {
                        "format": unit_index.INDEX_FORMAT,
                        "manifest_sha256": unit_index.manifest_sha256(surface),
                    }
                )
                + "\n"
            ).encode()
        )
        stream.write(b'{"id": "u-torn", "order": 0, "bat')
    assert unit_index.load_human_units(surface) == fallback


def _output_manifest(surface: Path) -> dict:
    return {"classes": [], "fonts": {}}


def test_the_contract_check_requires_the_sidecar(tmp_path):
    surface = _fixture_surface(tmp_path)
    (surface / "index.html").write_text("<html></html>", encoding="utf-8")
    app_index.write_app_artifacts(surface, {}, {})
    manifest = _output_manifest(surface)
    assert any("units-index" in line for line in _check_output_files(surface, manifest))
    _write(surface)
    assert _check_output_files(surface, manifest) == []


def test_the_contract_check_refuses_a_sidecar_stamped_for_another_manifest(tmp_path):
    surface = _fixture_surface(tmp_path)
    (surface / "index.html").write_text("<html></html>", encoding="utf-8")
    _write(surface)
    (surface / "manifest.json").write_text("{}\n", encoding="utf-8")
    complaints = _check_output_files(surface, _output_manifest(surface))
    assert any(f"{unit_index.INDEX_NAME} is unreadable or stamped" in line for line in complaints)


def _reader_surface(tmp_path: Path, mode: str) -> tuple[Path, list[dict]]:
    surface = _fixture_surface(tmp_path)
    slot = unit_index.slot_reader(surface)
    expected = [unit_index.index_record(fragment, **slot(fragment)) for fragment in _shard_units(surface)]
    if mode != "absent":
        _write(surface)
    if mode == "stale":
        manifest = json.loads((surface / "manifest.json").read_text())
        manifest["generated_at"] = "stale"
        (surface / "manifest.json").write_text(json.dumps(manifest))
    return surface, expected


@pytest.mark.parametrize("mode", ["current", "absent", "stale"])
@pytest.mark.parametrize("fields", [None, (), ("after", "id", "before", "configs"), ("class",)])
def test_compact_readers_preserve_fields_order_and_human_classification(tmp_path, mode, fields):
    surface, full = _reader_surface(tmp_path, mode)
    selected = set(full[0]) if fields is None else set(fields)
    expected = [{key: value for key, value in record.items() if key in selected} for record in full]
    human = [record for record, original in zip(expected, full) if original["batch"] is not None]
    ids = {record["id"] for record in full}
    for reader in (unit_index.load_units, unit_index.iter_units, unit_index.stream_shards):
        actual = list(reader(surface, fields=fields))
        assert actual == expected
        for record, original in zip(actual, expected):
            assert isinstance(record, Mapping)
            assert not hasattr(record, "__dict__")
            assert list(record) == list(original)
            assert json.loads(json.dumps(dict(record))) == original
    indexed = unit_index.load_index(surface, fields=fields)
    assert indexed == (expected if mode == "current" else None)
    seen = set()
    assert list(unit_index.iter_human_units(surface, fields=fields, unit_ids=seen)) == human
    assert seen == ids
    assert unit_index.load_human_units(surface, fields=fields) == (human, ids)


@pytest.mark.parametrize("mode", ["current", "absent", "stale"])
def test_unknown_projection_fields_are_rejected_by_every_reader(tmp_path, mode):
    surface, _ = _reader_surface(tmp_path, mode)
    for reader in (
        unit_index.load_index,
        unit_index.load_units,
        unit_index.iter_units,
        unit_index.stream_shards,
        unit_index.iter_human_units,
        unit_index.load_human_units,
    ):
        with pytest.raises(ValueError, match="misspelled_field"):
            result = reader(surface, fields=("id", "misspelled_field"))
            if result is not None:
                list(result)


def test_projection_omissions_cannot_silently_disable_a_rule(tmp_path):
    surface = _fixture_surface(tmp_path)
    [record, *_] = unit_index.load_units(surface, fields=("id", "no_verdict"))
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
        "before": {"glyphs": ["repeated glyph value"], "seams": [5]},
        "after": {"cells": ["repeated cell value"], "seams": [5]},
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
    assert first["before"]["seams"] is first["after"]["seams"]
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
            unit_index.index_line({"id": f"unit-{index}", "before": {"seams": [value]}})
            for index, value in enumerate(values)
        ),
    )
    records = unit_index.load_units(tmp_path, fields=("before",))
    seams = [record["before"]["seams"] for record in records]
    assert len({id(value) for value in seams}) == len(values)
    for actual, expected in zip(seams, values):
        assert type(actual[0]) is type(expected)
        assert json.dumps(actual) == json.dumps([expected])


def test_standing_projection_covers_the_fields_read_by_both_consumers(tmp_path):
    from rebuild.tools import standing_daemon

    expected = {
        "id",
        "batch",
        "class",
        "echo",
        "notation",
        "codepoints",
        "configs",
        "ink_deltas",
        "no_verdict",
        "content_key",
        "render_groups",
        "pair",
        "secondary_seams",
        "before",
        "after",
    }
    derived = set()
    for name in ("standing_probe.py", "standing_verdicts.py"):
        derived.update(_standing_unit_fields((REPO_ROOT / "rebuild" / "tools" / name).read_text()))
    assert _standing_unit_fields("print(f\"{unit['class']} {unit.get('echo')}\")") == {"class", "echo"}
    assert derived == expected == standing_daemon.UNIT_FIELDS
    surface, full = _reader_surface(tmp_path, "current")
    assert unit_index.load_units(surface, fields=standing_daemon.UNIT_FIELDS) == [
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
