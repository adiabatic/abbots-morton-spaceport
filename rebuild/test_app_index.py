"""Tests for the sidecars the review app boots from, and the byte spans that address the shards they were projected from.

The tests check four properties. `app_row` matches the shard fragment field for field, as `rebuild/test_unit_index.py` does for the plumbing's index, because a field that drops out shows no error: a card just stops drawing something. Every span slices its fragment back out of the bytes `_write_shard` wrote, including across a forced part split and around a fragment too large to share a part, which catches a change to the dump's framing that leaves the offsets wrong. The two files partition the corpus: the app index holds the manifest's `human_unit_ids` in shard order and the locator holds the rest, so every id the app can link to resolves. Every block the locator table names slices out of the rows file as a gzip member that decodes alone to the rows the table says, within one class, so the app's fold and deep-link binary search read what they expect.

Nothing here reads the live surface. The fixture units are rewritten through the real writer in a temp directory, and the end-to-end tests use a mini build over the frozen bundle.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from rebuild.review import app_index, unit_index
from rebuild.review.audit import MACHINE_CHANNELS
from rebuild.review.build import _check_output_files, _write_shard, check_output_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"

# Listed rather than derived, as in `rebuild/test_unit_index.py`: adding a field the app carries shows up as a diff here, and dropping one the app draws fails here instead of leaving a blank line on a card.
APP_ROW_KEYS = {
    "id",
    "order",
    "batch",
    "class",
    "group",
    "echo",
    "cluster",
    "notation",
    "notation_tokens",
    "codepoints",
    "pair",
    "pair_codepoints",
    "boundary_marks",
    "secondary_seams",
    "configs",
    "config_gate",
    "config_note",
    "config_class_note",
    "render_groups",
    "summary",
    "exemplar",
    "kinds",
    "shard_part",
    "byte_start",
    "byte_length",
}
LOCATOR_ROW_KEYS = {"id", "class", "shard_part", "byte_start", "byte_length"}
LOCATOR_BLOCK_KEYS = {"class", "byte_start", "byte_length", "first", "last", "units"}
# Read by the card from the record it Range-fetches, never from the resident row.
CARD_RECORD_KEYS = ("text_entities", "highlight", "after")
ADDRESS_KEYS = {"shard_part", "byte_start", "byte_length"}
# Read from the manifest's triage index, because a fragment carries neither.
INDEX_KEYS = {"order", "batch"}
LIST_DEFAULTED = ("notation_tokens", "boundary_marks", "configs", "kinds")
_SIDECAR_NAMES = (app_index.APP_INDEX_NAME, app_index.LOCATOR_NAME, app_index.LOCATOR_ROWS_NAME)


def _class_fragments(root: Path, meta: dict) -> list[dict]:
    return [
        unit
        for part in unit_index.class_shards(meta)
        for unit in json.loads((root / part).read_text(encoding="utf-8"))
    ]


def _surface_shards(surface: Path) -> tuple[dict, dict[str, list[dict]]]:
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    return manifest, {meta["id"]: _class_fragments(surface, meta) for meta in manifest["classes"]}


def _rewrite_fixture_surface(tmp_path: Path) -> Path:
    """Rewrite the checked-in fixture units through the real shard writer into a temp directory, with the sidecars beside them, so their spans are taken over real fragments."""
    surface = tmp_path / "surface"
    surface.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    shards: dict[str, list[dict]] = {}
    spans: dict[str, list[tuple[int, int, int]]] = {}
    for meta in manifest["classes"]:
        fragments = _class_fragments(FIXTURES, meta)
        meta["shards"], spans[meta["id"]] = _write_shard(surface, meta["id"], fragments)
        shards[meta["id"]] = fragments
    (surface / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    app_index.write_app_artifacts(surface, shards, spans)
    return surface


def _rewrite_fragments(tmp_path: Path, fragments: dict[str, list[dict]]) -> Path:
    """Write arbitrary fragments through the real shard writer, under a manifest that names them, with the sidecars beside them."""
    surface = tmp_path / "surface"
    surface.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    manifest["classes"] = []
    spans: dict[str, list[tuple[int, int, int]]] = {}
    for class_id, units in fragments.items():
        parts, spans[class_id] = _write_shard(surface, class_id, units)
        manifest["classes"].append({"id": class_id, "shards": parts})
    (surface / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    app_index.write_app_artifacts(surface, fragments, spans)
    return surface


@pytest.fixture
def fixture_surface(tmp_path) -> Path:
    return _rewrite_fixture_surface(tmp_path)


def _addressed(surface: Path, manifest: dict, row: dict) -> dict:
    """Return the fragment a row's span points at, read the way the browser's Range request reads it: that slice of the named part's bytes, parsed alone."""
    meta = next(entry for entry in manifest["classes"] if entry["id"] == row["class"])
    part = unit_index.class_shards(meta)[row["shard_part"]]
    raw = (surface / part).read_bytes()
    return json.loads(raw[row["byte_start"] : row["byte_start"] + row["byte_length"]])


# --- the spans -----------------------------------------------------------------------------------


def _spans_address_their_fragments(root: Path, class_id: str, fragments: list[dict]) -> None:
    parts, spans = _write_shard(root, class_id, fragments)
    assert len(spans) == len(fragments)
    for fragment, (part, start, length) in zip(fragments, spans, strict=True):
        raw = (root / parts[part]).read_bytes()
        assert raw.isascii(), parts[part]
        assert json.loads(raw[start : start + length]) == fragment


def test_a_span_slices_its_own_fragment_back_out_of_a_single_part(tmp_path):
    """Only the element's own bytes lie between `byte_start` and `byte_start + byte_length`, and the part is ASCII, so a character offset is a byte offset."""
    _spans_address_their_fragments(tmp_path, "small", [{"id": f"u-{index:04d}"} for index in range(6)])


def test_spans_survive_a_class_written_as_parts(tmp_path, monkeypatch):
    """A split resets the running offset at every part boundary, so a span carries the part it belongs to and never a running total across the class."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 512)
    fragments = [{"id": f"u-{index:04d}", "pad": "x" * 60} for index in range(24)]
    _spans_address_their_fragments(tmp_path, "big", fragments)
    parts, spans = _write_shard(tmp_path, "big2", fragments)
    assert len(parts) > 1
    assert len({part for part, _start, _length in spans}) == len(parts)


def test_a_fragment_too_large_to_share_a_part_is_still_addressed(tmp_path, monkeypatch):
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 128)
    fragments = [{"id": "u-0000"}, {"id": "u-0001", "pad": "x" * 400}, {"id": "u-0002"}]
    _spans_address_their_fragments(tmp_path, "big", fragments)


def test_a_fragment_with_non_ascii_prose_stays_byte_addressable(tmp_path):
    """`ensure_ascii=True` makes the character count a byte count, and the corpus has many `·` names and curly quotes, so an escaped fragment must slice back out at the same offsets."""
    fragments = [{"id": "u-0000", "notation": "·Tea·Oy — “joined”"}, {"id": "u-0001"}]
    _spans_address_their_fragments(tmp_path, "prose", fragments)


def test_an_empty_class_addresses_nothing(tmp_path):
    assert _write_shard(tmp_path, "empty", []) == (["units/empty.json"], [])


# --- the projection ------------------------------------------------------------------------------


def _assert_row_projects(row: dict, fragment: dict) -> None:
    assert set(row) == APP_ROW_KEYS, row["id"]
    for field, value in row.items():
        if field in ADDRESS_KEYS or field in INDEX_KEYS:
            continue
        if field in LIST_DEFAULTED:
            assert value == (fragment.get(field) or []), f"{row['id']}.{field}"
        else:
            assert value == fragment.get(field), f"{row['id']}.{field}"


def test_the_app_index_is_the_shards_field_for_field(fixture_surface):
    _manifest, shards = _surface_shards(fixture_surface)
    by_id = {fragment["id"]: fragment for shard in shards.values() for fragment in shard}
    rows = app_index.load_rows(fixture_surface, app_index.APP_INDEX_NAME)
    assert rows
    for row in rows:
        _assert_row_projects(row, by_id[row["id"]])


def test_every_row_addresses_the_fragment_it_was_projected_from(fixture_surface):
    manifest, _shards = _surface_shards(fixture_surface)
    for rows in (
        app_index.load_rows(fixture_surface, app_index.APP_INDEX_NAME),
        app_index.load_locator_rows(fixture_surface),
    ):
        assert rows
        for row in rows:
            assert _addressed(fixture_surface, manifest, row)["id"] == row["id"]


def test_the_slimmed_flags_are_absent_and_every_row_carries_an_integer_batch(fixture_surface):
    """A row in this file is a human unit: the surface check (`_SurfaceCheck.finish`, which the m1 build and `check_shards` both run) fails a build whose `human_unit_ids` differs from the shards' human workload, which excludes machine-approved and no-verdict units. So the four flags are dropped instead of carried as four falses per unit, and a reader finds them undefined, which is falsy."""
    rows = app_index.load_rows(fixture_surface, app_index.APP_INDEX_NAME)
    assert rows
    for row in rows:
        assert isinstance(row["batch"], int)
        for flag in (*MACHINE_CHANNELS, "no_verdict"):
            assert flag not in row


def test_a_row_whose_flags_are_not_false_refuses_to_be_written():
    """`app_row` asserts the flags are false, so a build that put a machine-approved unit into the human workload fails instead of writing a row the app would draw as human."""
    fragment = {"id": "u-0000", "picture_identical": True}
    with pytest.raises(AssertionError):
        app_index.app_row(fragment, 0, 0, 10, order=3, batch=0)


def test_what_a_card_draws_from_its_record_is_not_in_the_row():
    """The sample cells and the seam underlines draw from `text_entities`, `highlight`, and `after.cells`, which the card Range-fetches from its record, so the resident row carries none of the three even when the fragment has them."""
    fragment = {
        "id": "u-0000",
        "text_entities": "&#xe652;&#xe679;",
        "highlight": {"before": {"x_min": 0, "x_max": 1}, "after": {"x_min": 0, "x_max": 1}},
        "secondary_seams": [{"pair": {"left": 0, "right": 1}, "home": None}],
        "after": {"cells": ["a", "b", "c"], "seams": [], "extensions": []},
    }
    row = app_index.app_row(fragment, 0, 0, 10, order=0, batch=0)
    for key in CARD_RECORD_KEYS:
        assert key not in row
    assert row["secondary_seams"] == fragment["secondary_seams"]


def test_the_locator_carries_an_address_and_nothing_else(fixture_surface):
    rows = app_index.load_locator_rows(fixture_surface)
    assert rows
    for row in rows:
        assert set(row) == LOCATOR_ROW_KEYS


# --- the locator's blocks -------------------------------------------------------------------------


def _blocks_address_their_rows(surface: Path) -> None:
    """Check what the app assumes of the locator table. Each block is a gzip member that decodes alone from its span, holding the rows the table counts, all of one class, with the named first and last ids, in unit-number order. The spans tile the rows file with no gaps. Within a class the blocks are disjoint and ordered, which the deep link's binary search assumes."""
    blocks = app_index.load_rows(surface, app_index.LOCATOR_NAME)
    rows = app_index.load_locator_rows(surface)
    assert blocks is not None and rows is not None
    header = app_index.artifact_header(surface, app_index.LOCATOR_NAME)
    assert header is not None
    assert header["blocks"] == len(blocks)
    assert (
        header["rows_bytes"] == app_index.artifact_path(surface, app_index.LOCATOR_ROWS_NAME).stat().st_size
    )
    offset = 0
    replayed: list[dict] = []
    by_class: dict[str | None, list[dict]] = {}
    for block in blocks:
        assert set(block) == LOCATOR_BLOCK_KEYS
        assert block["byte_start"] == offset
        member = gzip.decompress(app_index.locator_block_bytes(surface, block))
        held = [json.loads(line) for line in member.splitlines()]
        assert 0 < len(held) == block["units"] <= app_index.LOCATOR_BLOCK_ROWS
        assert {row["class"] for row in held} == {block["class"]}
        assert held[0]["id"] == block["first"] and held[-1]["id"] == block["last"]
        ids = [row["id"] for row in held]
        assert ids == sorted(ids)
        replayed.extend(held)
        by_class.setdefault(block["class"], []).append(block)
        offset += block["byte_length"]
    assert offset == header["rows_bytes"]
    assert replayed == rows
    for class_blocks in by_class.values():
        for earlier, later in zip(class_blocks, class_blocks[1:], strict=False):
            assert earlier["last"] < later["first"]


def test_every_block_slices_out_of_the_rows_file_as_its_own_member(fixture_surface):
    _blocks_address_their_rows(fixture_surface)


def test_blocks_close_at_the_row_cap_and_at_every_class_change(tmp_path):
    """A block closes at `LOCATOR_BLOCK_ROWS` rows and at every class change: a class of two blocks and a few rows, then a class of one row, then a class that starts a new block instead of sharing the previous class's last one."""
    cap = app_index.LOCATOR_BLOCK_ROWS
    counts = {"a": 2 * cap + 3, "b": 1, "c": cap}
    fragments = {
        class_id: [
            {"id": f"u-{index:04d}", "batch": None, "class": class_id}
            for index in range(offset, offset + count)
        ]
        for class_id, count, offset in zip(counts, counts.values(), (0, 10 * cap, 20 * cap), strict=True)
    }
    surface = _rewrite_fragments(tmp_path, fragments)
    _blocks_address_their_rows(surface)
    blocks = app_index.load_rows(surface, app_index.LOCATOR_NAME)
    assert blocks is not None
    assert [(block["class"], block["units"]) for block in blocks] == [
        ("a", cap),
        ("a", cap),
        ("a", 3),
        ("b", 1),
        ("c", cap),
    ]


def test_a_locator_id_resolves_to_exactly_one_block_of_its_class(fixture_surface):
    """Replays the browser's deep link: for a machine id, the one block of its class whose first and last ids bracket it holds it, and no other class's blocks are consulted."""
    blocks = app_index.load_rows(fixture_surface, app_index.LOCATOR_NAME)
    rows = app_index.load_locator_rows(fixture_surface)
    assert blocks and rows
    for row in rows:
        holders = [
            block
            for block in blocks
            if block["class"] == row["class"] and block["first"] <= row["id"] <= block["last"]
        ]
        assert len(holders) == 1, row["id"]
        member = gzip.decompress(app_index.locator_block_bytes(fixture_surface, holders[0]))
        assert row in [json.loads(line) for line in member.splitlines()]


def test_a_class_whose_rows_do_not_ascend_is_refused(tmp_path):
    """The deep link's binary search assumes a class's blocks ascend by unit number, so the writer rejects rows that do not."""
    fragments = {
        "a": [{"id": "u-0002", "batch": None, "class": "a"}, {"id": "u-0001", "batch": None, "class": "a"}]
    }
    with pytest.raises(ValueError, match="must ascend"):
        _rewrite_fragments(tmp_path, fragments)


def test_every_row_carries_its_place_in_the_manifests_triage_index(fixture_surface):
    """`order` is the row's position in `human_unit_ids` and `batch` is that position divided by `batch_size`, both read from the manifest because a fragment carries neither, so the app pages the queue in the manifest's order whatever order the shards are written in."""
    manifest, _shards = _surface_shards(fixture_surface)
    rows = app_index.load_rows(fixture_surface, app_index.APP_INDEX_NAME)
    assert rows
    positions = {unit_id: position for position, unit_id in enumerate(manifest["human_unit_ids"])}
    for row in rows:
        assert row["order"] == positions[row["id"]]
        assert row["batch"] == positions[row["id"]] // manifest["batch_size"]


def test_a_rows_file_of_another_length_makes_the_locator_stale(fixture_surface):
    """The rows file has no header, so the table's `rows_bytes` is its stamp. A rows file that is truncated, missing, or left by another build would send every block fetch to the wrong bytes, so the locator reads as stale."""
    rows_path = app_index.artifact_path(fixture_surface, app_index.LOCATOR_ROWS_NAME)
    intact = rows_path.read_bytes()
    assert app_index.artifact_is_current(fixture_surface, app_index.LOCATOR_NAME, app_index.LOCATOR_FORMAT)
    rows_path.write_bytes(intact[:-1])
    assert not app_index.artifact_is_current(
        fixture_surface, app_index.LOCATOR_NAME, app_index.LOCATOR_FORMAT
    )
    rows_path.unlink()
    assert not app_index.artifact_is_current(
        fixture_surface, app_index.LOCATOR_NAME, app_index.LOCATOR_FORMAT
    )
    rows_path.write_bytes(intact)
    assert app_index.artifact_is_current(fixture_surface, app_index.LOCATOR_NAME, app_index.LOCATOR_FORMAT)


# --- the partition and the stamps ------------------------------------------------------------------


def _ids(surface: Path, name: str) -> list[str]:
    rows = (
        app_index.load_locator_rows(surface)
        if name == app_index.LOCATOR_NAME
        else app_index.load_rows(surface, name)
    )
    assert rows is not None
    return [row["id"] for row in rows]


def _shard_order_ids(manifest: dict, shards: dict[str, list[dict]], *, human: bool) -> list[str]:
    """Return the ids in shard order, the order every sidecar is written in: classes by `class_shard_key`, and each class's fragments as the shard lists them. This differs from `human_unit_ids`, which is in triage order."""
    return [
        fragment["id"]
        for meta in sorted(manifest["classes"], key=lambda entry: unit_index.class_shard_key(entry["id"]))
        for fragment in shards[meta["id"]]
        if (fragment["id"] in manifest["human_unit_ids"]) is human
    ]


def test_the_two_files_partition_the_corpus_on_the_manifests_own_split(fixture_surface):
    """The app index holds the manifest's human workload and the locator holds the rest, both in shard order, so every id the app can be deep-linked to resolves in exactly one file."""
    manifest, shards = _surface_shards(fixture_surface)
    human = _ids(fixture_surface, app_index.APP_INDEX_NAME)
    machine = _ids(fixture_surface, app_index.LOCATOR_NAME)
    assert set(human) == set(manifest["human_unit_ids"])
    assert human == _shard_order_ids(manifest, shards, human=True)
    assert machine == _shard_order_ids(manifest, shards, human=False)


def test_the_headers_stamp_the_manifest_beside_them(fixture_surface):
    digest = unit_index.manifest_sha256(fixture_surface)
    generated_at = json.loads((fixture_surface / "manifest.json").read_text(encoding="utf-8"))["generated_at"]
    counts = {
        app_index.APP_INDEX_NAME: len(_ids(fixture_surface, app_index.APP_INDEX_NAME)),
        app_index.LOCATOR_NAME: len(_ids(fixture_surface, app_index.LOCATOR_NAME)),
    }
    blocks = app_index.load_rows(fixture_surface, app_index.LOCATOR_NAME)
    assert blocks is not None
    rows_bytes = app_index.artifact_path(fixture_surface, app_index.LOCATOR_ROWS_NAME).stat().st_size
    for name, fmt in app_index.ARTIFACTS:
        header = app_index.artifact_header(fixture_surface, name)
        stamp = {
            "format": fmt,
            "manifest_sha256": digest,
            "generated_at": generated_at,
            "units": counts[name],
        }
        if name == app_index.LOCATOR_NAME:
            stamp.update(blocks=len(blocks), block_rows=app_index.LOCATOR_BLOCK_ROWS, rows_bytes=rows_bytes)
        assert header == stamp
        assert app_index.artifact_is_current(fixture_surface, name, fmt)


def test_a_refreshed_assets_component_leaves_both_sidecars_current(fixture_surface):
    """The stamp is the manifest's identity (`unit_index.manifest_sha256`), which leaves out `inputs_fingerprint.static`, so an assets refresh, which rewrites only that field, leaves every sidecar current. Otherwise a CSS edit would make the files the app boots from stale and send every reader back to the shards."""
    manifest_path = fixture_surface / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs_fingerprint"] = {**manifest["inputs_fingerprint"], "static": "refreshed"}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(fixture_surface, name, fmt) is True


def test_a_sidecar_stamped_for_another_manifest_is_refused(fixture_surface):
    """The stamp protects a tab holding rows from a surface that has since been rebuilt, whose ids may name reassigned units and whose spans would slice the wrong record out of a rewritten shard."""
    manifest = json.loads((fixture_surface / "manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at"] = "2099-01-01T00:00:00Z"
    (fixture_surface / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(fixture_surface, name, fmt) is False


def test_a_truncated_or_foreign_sidecar_is_refused(fixture_surface):
    for name, fmt in app_index.ARTIFACTS:
        path = app_index.artifact_path(fixture_surface, name)
        path.write_bytes(b"")
        assert app_index.artifact_header(fixture_surface, name) is None
        with gzip.open(path, "wb") as stream:
            stream.write((json.dumps({"format": "something-else"}) + "\n").encode())
        assert app_index.artifact_is_current(fixture_surface, name, fmt) is False
        assert app_index.load_rows(fixture_surface, name) == []


def test_a_failed_projection_leaves_the_previous_set_intact(fixture_surface, monkeypatch):
    """`app_row` asserts, so the projection can raise partway through rewriting a surface the app is serving. The sidecars are staged and renamed, so each keeps the bytes of the last good write and no `.partial` file is left behind."""
    intact = {name: app_index.artifact_path(fixture_surface, name).read_bytes() for name in _SIDECAR_NAMES}
    _manifest, shards = _surface_shards(fixture_surface)
    spans = {class_id: [(0, 0, 1)] * len(fragments) for class_id, fragments in shards.items()}

    def boom(*_args, **_kwargs):
        raise AssertionError("u-9999")

    monkeypatch.setattr(app_index, "app_row", boom)
    with pytest.raises(AssertionError):
        app_index.write_app_artifacts(fixture_surface, shards, spans)
    for name in _SIDECAR_NAMES:
        assert app_index.artifact_path(fixture_surface, name).read_bytes() == intact[name]
    assert not list(fixture_surface.glob("*.partial"))


def test_writing_the_sidecars_twice_writes_the_same_bytes(tmp_path):
    """The gzip mtime is pinned, so a rebuild of unchanged inputs leaves the output tree byte-identical, which the byte-identity tests in `rebuild/test_unit_cache.py` compare."""
    first = _rewrite_fixture_surface(tmp_path / "a")
    second = _rewrite_fixture_surface(tmp_path / "b")
    for name in _SIDECAR_NAMES:
        assert (
            app_index.artifact_path(first, name).read_bytes()
            == app_index.artifact_path(second, name).read_bytes()
        )


# --- the contract check ---------------------------------------------------------------------------


def test_the_contract_check_requires_every_sidecar(tmp_path):
    """The rows file is checked through the locator's stamp, so a missing rows file is reported as a stale locator."""
    surface = _rewrite_fixture_surface(tmp_path)
    (surface / "index.html").write_text("<html></html>", encoding="utf-8")
    unit_index.write_index(surface, [])
    manifest = {"classes": [], "fonts": {}}
    assert _check_output_files(surface, manifest) == []
    for name, _fmt in app_index.ARTIFACTS:
        app_index.artifact_path(surface, name).unlink()
        assert any(f"{name} is missing" in line for line in _check_output_files(surface, manifest))
        app_index.write_app_artifacts(surface, {}, {})
    app_index.artifact_path(surface, app_index.LOCATOR_ROWS_NAME).unlink()
    assert any(app_index.LOCATOR_NAME in line for line in _check_output_files(surface, manifest))


def test_the_contract_check_refuses_a_sidecar_stamped_for_another_manifest(tmp_path):
    surface = _rewrite_fixture_surface(tmp_path)
    (surface / "index.html").write_text("<html></html>", encoding="utf-8")
    unit_index.write_index(surface, [])
    (surface / "manifest.json").write_text("{}\n", encoding="utf-8")
    complaints = _check_output_files(surface, {"classes": [], "fonts": {}})
    for name, _fmt in app_index.ARTIFACTS:
        assert any(f"{name} is unreadable or stamped for another manifest" in line for line in complaints)


# --- what a real build writes ------------------------------------------------------------------------


def test_a_build_writes_every_sidecar_over_its_own_shards(mini_surface):
    """End to end: every row of a real build's sidecars projects its fragment from that build's shards, every span slices the fragment back out, and the partition matches the manifest."""
    manifest, shards = _surface_shards(mini_surface)
    by_id = {fragment["id"]: fragment for shard in shards.values() for fragment in shard}
    rows = app_index.load_rows(mini_surface, app_index.APP_INDEX_NAME)
    assert rows
    assert [row["id"] for row in rows] == _shard_order_ids(manifest, shards, human=True)
    assert set(row["id"] for row in rows) == set(manifest["human_unit_ids"])
    for row in rows:
        fragment = by_id[row["id"]]
        _assert_row_projects(row, fragment)
        assert _addressed(mini_surface, manifest, row) == fragment
    locator = app_index.load_locator_rows(mini_surface)
    assert locator is not None
    assert [row["id"] for row in locator] == _shard_order_ids(manifest, shards, human=False)
    assert set(row["id"] for row in locator) == set(by_id) - set(manifest["human_unit_ids"])
    for row in locator:
        assert _addressed(mini_surface, manifest, row) == by_id[row["id"]]
    _blocks_address_their_rows(mini_surface)


def test_a_build_satisfies_the_whole_surface_contract(mini_surface):
    """The only place the manifest-shape predicates and the file predicates beside the manifest run over a real m1-audit build. `_write_surface` runs only the shard predicates, feeding `_SurfaceCheck` one fragment at a time, because it wrote everything the other two read from its own inputs."""
    assert check_output_dir(mini_surface, REPO_ROOT) == []


def test_a_builds_class_records_count_the_machine_channels_it_shipped(mini_surface):
    """The app renders a machine fold's count and badge before it has any of the fold's units, so the split must be in the manifest and agree with the shards. The shard predicates check this on every build (through `check_shards`, or `_SurfaceCheck` in the m1 write), over every unit written, served ones included."""
    manifest, shards = _surface_shards(mini_surface)
    for meta in manifest["classes"]:
        observed = {
            channel: sum(1 for unit in shards[meta["id"]] if unit.get(channel) is True)
            for channel in MACHINE_CHANNELS
        }
        assert meta["machine_channels"] == observed
        assert sum(observed.values()) == meta["machine_approved_count"]
