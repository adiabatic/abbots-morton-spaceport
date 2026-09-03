"""The two sidecars the review app boots from, and the byte spans that address the shards they were projected out of.

Three claims carry the change and each is checked here rather than in the browser. The projection is faithful: `app_row` is held against the shard fragment field for field, the standard `rebuild/test_unit_index.py` sets for the plumbing's index, because a field that silently drifts out does not read as an error — a card simply stops drawing something. The spans are real addresses: every fragment of every class is sliced back out of the bytes `_write_shard` wrote, including across a forced part split and around a fragment too large to share a part, which is what would catch a change to the dump's framing that leaves the offsets pointing at garbage. And the two files partition the corpus: the app index is exactly the manifest's `human_unit_ids`, in shard order, and the locator is exactly the rest, so no id the app can be linked to is unresolvable.

Nothing here reads the live surface. The fixture units are rewritten through the real writer in a temp directory, and the end-to-end arm is a mini build over the frozen bundle — seconds, contracts lane, full width.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from rebuild.review import app_index, unit_index
from rebuild.review.audit import MACHINE_CHANNELS
from rebuild.review.build import _check_output_files, _write_shard, build_m1

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
MINI = FIXTURES / "mini"

# Named rather than derived, for the reason `rebuild/test_unit_index.py` names its own: adding a field to what the app carries should be a deliberate act, and dropping one the app draws should fail here rather than as a blank line on a card.
APP_ROW_KEYS = {
    "id",
    "batch",
    "class",
    "group",
    "echo",
    "cluster",
    "notation",
    "notation_tokens",
    "codepoints",
    "text_entities",
    "pair",
    "pair_codepoints",
    "highlight",
    "boundary_marks",
    "secondary_seams",
    "after",
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
ADDRESS_KEYS = {"shard_part", "byte_start", "byte_length"}
LIST_DEFAULTED = ("notation_tokens", "boundary_marks", "configs", "kinds")


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
    """The checked-in fixture units, rewritten through the real shard writer into a temp directory so their spans are captured over real fragments, with both sidecars beside them."""
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


@pytest.fixture
def fixture_surface(tmp_path) -> Path:
    return _rewrite_fixture_surface(tmp_path)


@pytest.fixture(scope="module")
def mini_surface(tmp_path_factory, mini_bundle) -> Path:
    """One real build of the frozen mini bundle, so the sidecars under test are the ones `_write_surface` emits rather than ones a test assembled."""
    out = tmp_path_factory.mktemp("app-index") / "surface"
    build_m1(
        out,
        audit_path=MINI / "audit.tsv",
        ledger_path=mini_bundle.ledger,
        subset_dir=MINI,
        after_font=MINI / "M1.otf",
        spec_root=mini_bundle.spec_root,
        jobs=1,
    )
    return out


def _addressed(surface: Path, manifest: dict, row: dict) -> dict:
    """The fragment a row's span points at, read the way the browser's Range request reads it: the named part, that slice of its bytes, parsed alone."""
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
    """The whole byte-addressing contract in its ordinary shape: nothing but the element's own bytes lies between `byte_start` and `byte_start + byte_length`, and the part is ASCII, so a character offset is a byte offset."""
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
    """`ensure_ascii=True` is what makes the character count a byte count, and the corpus is full of `·` names and curly quotes — so an escaped fragment has to slice back out at the same offsets."""
    fragments = [{"id": "u-0000", "notation": "·Tea·Oy — “joined”"}, {"id": "u-0001"}]
    _spans_address_their_fragments(tmp_path, "prose", fragments)


def test_an_empty_class_addresses_nothing(tmp_path):
    assert _write_shard(tmp_path, "empty", []) == (["units/empty.json"], [])


# --- the projection ------------------------------------------------------------------------------


def _assert_row_projects(row: dict, fragment: dict) -> None:
    assert set(row) == APP_ROW_KEYS, row["id"]
    for field, value in row.items():
        if field in ADDRESS_KEYS:
            continue
        if field == "after":
            homeless = any(seam.get("home") is None for seam in fragment.get("secondary_seams") or ())
            expected = {"cells": list((fragment.get("after") or {}).get("cells") or [])} if homeless else None
            assert value == expected, f"{row['id']}.after"
        elif field in LIST_DEFAULTED:
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
    for name in (app_index.APP_INDEX_NAME, app_index.LOCATOR_NAME):
        rows = app_index.load_rows(fixture_surface, name)
        assert rows
        for row in rows:
            assert _addressed(fixture_surface, manifest, row)["id"] == row["id"]


def test_the_slimmed_flags_are_absent_and_every_row_carries_an_integer_batch(fixture_surface):
    """A row in this file is provably non-machine and non-exempt — `check_unit` enforces that a unit with any machine channel, or with `no_verdict`, carries a null batch, on every unit a build computes and through the content-key stamp on every one it serves from the cache — so the four flags are dropped rather than carried as four falses per unit. A reader finds them undefined, which is falsy, which is what `false` already meant."""
    rows = app_index.load_rows(fixture_surface, app_index.APP_INDEX_NAME)
    assert rows
    for row in rows:
        assert isinstance(row["batch"], int)
        for flag in (*MACHINE_CHANNELS, "no_verdict"):
            assert flag not in row


def test_a_row_whose_flags_are_not_false_refuses_to_be_written():
    """The argument for dropping them stays executable: a build that ever put a machine-approved unit into the human workload fails loudly here rather than shipping a row the app would draw as human."""
    fragment = {"id": "u-0000", "batch": 3, "picture_identical": True}
    with pytest.raises(AssertionError):
        app_index.app_row(fragment, 0, 0, 10)


@pytest.mark.parametrize(
    ("seams", "carries_cells"),
    (
        (None, False),
        ([{"pair": {"left": 0, "right": 1}, "home": "u-0009"}], False),
        ([{"pair": {"left": 0, "right": 1}, "home": None}], True),
        (
            [
                {"pair": {"left": 0, "right": 1}, "home": "u-0009"},
                {"pair": {"left": 1, "right": 2}, "home": None},
            ],
            True,
        ),
    ),
)
def test_after_cells_ship_only_where_a_homeless_seam_needs_them(seams, carries_cells):
    """`onlyHereSeamSpans` reads `after.cells` to place the homeless secondary seams and answers `[]` for every other unit, so a row with no homeless seam behaves identically with the cells dropped — and the cells are the largest field the row would otherwise carry."""
    fragment = {
        "id": "u-0000",
        "batch": 0,
        "secondary_seams": seams,
        "after": {"cells": ["a", "b", "c"], "seams": [], "extensions": []},
    }
    row = app_index.app_row(fragment, 0, 0, 10)
    assert row["after"] == ({"cells": ["a", "b", "c"]} if carries_cells else None)
    assert row["secondary_seams"] == seams


def test_the_locator_carries_an_address_and_nothing_else(fixture_surface):
    rows = app_index.load_rows(fixture_surface, app_index.LOCATOR_NAME)
    assert rows
    for row in rows:
        assert set(row) == LOCATOR_ROW_KEYS


# --- the partition and the stamps ------------------------------------------------------------------


def _ids(surface: Path, name: str) -> list[str]:
    rows = app_index.load_rows(surface, name)
    assert rows is not None
    return [row["id"] for row in rows]


def _shard_order_ids(manifest: dict, shards: dict[str, list[dict]], *, human: bool) -> list[str]:
    """The ids a shard walk hands over, which is the order both sidecars are written in — classes by `class_shard_key`, each class's fragments as the shard lists them. Deliberately not `human_unit_ids`, which is the workload's own id order and runs the classes in ledger order instead."""
    return [
        fragment["id"]
        for meta in sorted(manifest["classes"], key=lambda entry: unit_index.class_shard_key(entry["id"]))
        for fragment in shards[meta["id"]]
        if (fragment["batch"] is not None) is human
    ]


def test_the_two_files_partition_the_corpus_on_the_manifests_own_split(fixture_surface):
    """The app index holds the manifest's human workload and the locator exactly the rest, so every id the app can be deep-linked to resolves in one file or the other and never in both — each in shard order, so a reader walking either file walks the shards alongside it."""
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
    for name, fmt in app_index.ARTIFACTS:
        header = app_index.artifact_header(fixture_surface, name)
        assert header == {
            "format": fmt,
            "manifest_sha256": digest,
            "generated_at": generated_at,
            "units": counts[name],
        }
        assert app_index.artifact_is_current(fixture_surface, name, fmt)


def test_a_refreshed_assets_component_leaves_both_sidecars_current(fixture_surface):
    """The stamp is the manifest's identity, not its bytes, so rewriting `inputs_fingerprint.static` in place — which is the whole of what an assets refresh does to a served surface — leaves both sidecars describing the manifest beside them. Without that, a CSS edit would orphan the two files the app boots from and send every reader back to the shards."""
    manifest_path = fixture_surface / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs_fingerprint"] = {**manifest["inputs_fingerprint"], "static": "refreshed"}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(fixture_surface, name, fmt) is True


def test_a_sidecar_stamped_for_another_manifest_is_refused(fixture_surface):
    """The hazard the stamp closes is a tab holding rows from a surface that has since been rebuilt: its ids name units this build reassigned, and its spans would slice a neighboring record out of a rewritten shard."""
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


def test_a_failed_projection_leaves_the_previous_pair_intact(fixture_surface, monkeypatch):
    """`app_row` asserts, so the projection can raise partway through a rewrite of a surface the app is still being served. Staged and renamed, that failure costs nothing: both sidecars keep the bytes the last good write left, and no `.partial` survives to be mistaken for one of them."""
    intact = {
        name: app_index.artifact_path(fixture_surface, name).read_bytes()
        for name, _fmt in app_index.ARTIFACTS
    }
    _manifest, shards = _surface_shards(fixture_surface)
    spans = {class_id: [(0, 0, 1)] * len(fragments) for class_id, fragments in shards.items()}

    def boom(*_args, **_kwargs):
        raise AssertionError("u-9999")

    monkeypatch.setattr(app_index, "app_row", boom)
    with pytest.raises(AssertionError):
        app_index.write_app_artifacts(fixture_surface, shards, spans)
    for name, _fmt in app_index.ARTIFACTS:
        assert app_index.artifact_path(fixture_surface, name).read_bytes() == intact[name]
    assert not list(fixture_surface.glob("*.partial"))


def test_writing_the_sidecars_twice_writes_the_same_bytes(tmp_path):
    """A pinned gzip mtime, so a rebuild of unchanged inputs leaves the whole output tree byte-identical — which is what `test_builds_are_byte_identical` reads the tree for."""
    first = _rewrite_fixture_surface(tmp_path / "a")
    second = _rewrite_fixture_surface(tmp_path / "b")
    for name, _fmt in app_index.ARTIFACTS:
        assert (
            app_index.artifact_path(first, name).read_bytes()
            == app_index.artifact_path(second, name).read_bytes()
        )


# --- the contract check ---------------------------------------------------------------------------


def test_the_contract_check_requires_both_sidecars(tmp_path):
    surface = _rewrite_fixture_surface(tmp_path)
    (surface / "index.html").write_text("<html></html>", encoding="utf-8")
    unit_index.write_index(surface, [])
    manifest = {"classes": [], "fonts": {}}
    assert _check_output_files(surface, manifest) == []
    for name, _fmt in app_index.ARTIFACTS:
        app_index.artifact_path(surface, name).unlink()
        assert any(f"{name} is missing" in line for line in _check_output_files(surface, manifest))
        app_index.write_app_artifacts(surface, {}, {})


def test_the_contract_check_refuses_a_sidecar_stamped_for_another_manifest(tmp_path):
    surface = _rewrite_fixture_surface(tmp_path)
    (surface / "index.html").write_text("<html></html>", encoding="utf-8")
    unit_index.write_index(surface, [])
    (surface / "manifest.json").write_text("{}\n", encoding="utf-8")
    complaints = _check_output_files(surface, {"classes": [], "fonts": {}})
    for name, _fmt in app_index.ARTIFACTS:
        assert any(f"{name} is unreadable or stamped for another manifest" in line for line in complaints)


# --- what a real build writes ------------------------------------------------------------------------


def test_a_build_writes_both_sidecars_over_its_own_shards(mini_surface):
    """The end-to-end arm: the sidecars a build emits, held against the shards that same build wrote — every row projecting its fragment, every span slicing it back out, and the partition falling exactly where the manifest says it does."""
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
    locator = app_index.load_rows(mini_surface, app_index.LOCATOR_NAME)
    assert locator is not None
    assert [row["id"] for row in locator] == _shard_order_ids(manifest, shards, human=False)
    assert set(row["id"] for row in locator) == set(by_id) - set(manifest["human_unit_ids"])
    for row in locator:
        assert _addressed(mini_surface, manifest, row) == by_id[row["id"]]


def test_a_builds_class_records_count_the_machine_channels_it_shipped(mini_surface):
    """The app renders a machine fold's count and badge before it has any of the fold's units, so the split has to be in the manifest — and it has to agree with the shards, which is what `check_shards` holds it to on every build, over every unit it shipped rather than only the ones it computed."""
    manifest, shards = _surface_shards(mini_surface)
    for meta in manifest["classes"]:
        observed = {
            channel: sum(1 for unit in shards[meta["id"]] if unit.get(channel) is True)
            for channel in MACHINE_CHANNELS
        }
        assert meta["machine_channels"] == observed
        assert sum(observed.values()) == meta["machine_approved_count"]
