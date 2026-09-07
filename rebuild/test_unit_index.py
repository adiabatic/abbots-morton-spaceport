"""The per-unit index sidecar the surface build writes beside its manifest: that it is a true projection of the shards, that it is refused rather than trusted when its stamp does not describe the manifest on disk, and that the fallback to the shards answers identically. The plumbing reads the index and never the shards now, so a field that drifts out of the projection does not read as an error — a standing rule quietly stops matching and a blessed delta re-queues. This is what stops that: every field, every unit, held against the shipped fixture shards."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

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
    """A `ams-review-manifest/1` class carries one `shard` string, and both the prior surface the unit cache reads and the archived snapshots under var/review-pre-* the carry resolves against are that shape until they are rebuilt — so both spellings have to answer."""
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
