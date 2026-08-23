"""Tests for the persisted per-unit surface cache (issue 20; rebuild/review/unit_cache.py is the contract). The load-bearing claims: an incremental rebuild over an edited audit is byte-identical to a from-scratch build of the same inputs — ids, batches, echo numbering, seam homes, and the store itself included — a no-change rebuild serves every unit, a corrupt or bypassed store degrades to a full build rather than stale bytes, and the serial and parallel paths agree.

None of that is a property of any glyph, so none of it needs the live build: the workload is the frozen mini-M1 bundle under rebuild/review/fixtures/mini/ — a thousand-odd real windows over four letters, their subset-table slices, and the after-font they were extracted with — and the whole module runs in the contracts lane at full width, each build costing seconds rather than the twelve-and-a-half a live subset-table parse cost before serving a workload that never read it. `fixtures/mini/regenerate.py` is how the bundle is refreshed; the key and cluster byte-contracts below are pinned separately over synthetic inputs.
"""

import hashlib
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from rebuild.pipeline import fixtures, kernel_exec
from rebuild.review import unit_cache
from rebuild.review.audit import AuditRow, Unit
from rebuild.review.build import SITE_BEFORE_FONT, SITE_JUNIOR_FONT, _cluster_id, build_m1

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"


def _build(out, bundle, audit_path=MINI_AUDIT, **kwargs):
    """One mini surface, always over the frozen bundle: its subset tables, its after-font, and the ledger and spec the `mini_bundle` fixture materializes from the bundle's pin. That pinned spec is what keeps the bundle hermetic — the enricher re-settles every window from it, so reading the repo's live runes would make a rune edit break this module until the bundle was regenerated."""
    return build_m1(
        out,
        audit_path=audit_path,
        ledger_path=bundle.ledger,
        subset_dir=MINI,
        after_font=MINI_FONT,
        spec_root=bundle.spec_root,
        **kwargs,
    )


@pytest.fixture(scope="module")
def base_surface(tmp_path_factory, mini_bundle):
    out = tmp_path_factory.mktemp("unit-cache-base") / "surface"
    _build(out, mini_bundle, jobs=1)
    return out


def _tree(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(Path(path).rglob("*")) if p.is_file()
    }


def _served(capfd) -> tuple[int, int]:
    match = re.search(r"served (\d+) of (\d+) units from cache", capfd.readouterr().err)
    assert match, "the build did not report its cache plan"
    return int(match.group(1)), int(match.group(2))


def _copy(base: Path, tmp_path: Path) -> Path:
    target = tmp_path / "surface"
    shutil.copytree(base, target)
    return target


def test_no_change_rebuild_serves_every_unit_and_is_byte_stable(base_surface, mini_bundle, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    before = _tree(surface)
    _build(surface, mini_bundle, jobs=1)
    served, total = _served(capfd)
    assert served == total
    assert _tree(surface) == before


RETAG_CLASS = "dangling-anchor-dropped"


def _edited_audit(tmp_path: Path) -> Path:
    """The mini audit with one window dropped and one moved to another ledger class — the two edits that make an incremental rebuild renumber ids, batches, echoes, and seam homes rather than merely patch a unit in place.

    The retag lands on a matched class rather than on UNMATCHED for a data reason: `derive_premerge` refuses an ink-identical window that claims a verdict family, which is true of the live corpus (every UNMATCHED window is a real new join under review) but not of a window a test declares UNMATCHED by editing a TSV. Every row of the window moves together, since two matched classes on one triple is a classification bug the loader raises on.
    """
    lines = MINI_AUDIT.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    windows: list[str] = []
    classes: dict[str, str] = {}
    for row in rows:
        fields = row.split("\t")
        if fields[1] not in classes:
            windows.append(fields[1])
        classes.setdefault(fields[1], fields[3])
    dropped = windows[3]
    retagged = next(window for window in windows[4:] if classes[window] != RETAG_CLASS and window != dropped)
    edited = []
    for row in rows:
        fields = row.split("\t")
        if fields[1] == dropped:
            continue
        if fields[1] == retagged:
            fields[3] = RETAG_CLASS
        edited.append("\t".join(fields))
    path = tmp_path / "audit-edited.tsv"
    path.write_text("\n".join([header] + edited) + "\n", encoding="utf-8")
    return path


def test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit(
    base_surface, mini_bundle, tmp_path, capfd
):
    """The soundness gate at mini scale: dropping one window renumbers every unit behind it and retagging another moves its class, and the incremental pass — serving nearly everything, re-patching ids, batches, echo numbers, and seam homes — must land byte-for-byte on what a cache-blind build of the same audit writes, the store included."""
    incremental = _copy(base_surface, tmp_path)
    edited = _edited_audit(tmp_path)
    capfd.readouterr()
    _build(incremental, mini_bundle, audit_path=edited, jobs=1)
    served, total = _served(capfd)
    assert 0 < total - served <= 2
    scratch = tmp_path / "scratch"
    _build(scratch, mini_bundle, audit_path=edited, jobs=1)
    assert _tree(incremental) == _tree(scratch)


def test_corrupt_store_degrades_to_a_full_build(base_surface, mini_bundle, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    unit_cache.store_path(surface).write_bytes(b"not a gzip stream")
    _build(surface, mini_bundle, jobs=1)
    served, _total = _served(capfd)
    assert served == 0
    assert _tree(surface) == _tree(base_surface)


def test_fresh_unit_cache_bypasses_a_warm_store(base_surface, mini_bundle, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    before = _tree(surface)
    _build(surface, mini_bundle, jobs=1, fresh_unit_cache=True)
    served, _total = _served(capfd)
    assert served == 0
    assert _tree(surface) == before


def test_serial_and_parallel_builds_are_byte_identical(base_surface, mini_bundle, tmp_path):
    parallel = tmp_path / "parallel"
    _build(parallel, mini_bundle, jobs=2)
    assert _tree(parallel) == _tree(base_surface)


def _signatures(capfd) -> tuple[int, int]:
    match = re.search(r"signatures: (\d+) cached, (\d+) shaped", capfd.readouterr().err)
    assert match, "the build did not report its signature plan"
    return int(match.group(1)), int(match.group(2))


def test_no_change_rebuild_serves_every_signature(base_surface, mini_bundle, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    _build(surface, mini_bundle, jobs=1)
    cached, shaped = _signatures(capfd)
    assert cached > 0
    assert shaped == 0


def test_corrupt_signature_store_reshapes_and_degrades_to_the_same_bytes(
    base_surface, mini_bundle, tmp_path, capfd
):
    surface = _copy(base_surface, tmp_path)
    unit_cache.signature_store_path(surface).write_bytes(b"not a gzip stream")
    _build(surface, mini_bundle, jobs=1)
    cached, shaped = _signatures(capfd)
    assert cached == 0
    assert shaped > 0
    assert _tree(surface) == _tree(base_surface)


def test_unit_store_environment_tracks_each_kernel_settlement_mode(monkeypatch):
    """The stamp a cached store is keyed on has to move when the kernel's settlement mode does, or a store written under one mode would serve units the other never produced. The subset directory is only hashed, never read for content, so the frozen bundle stands in for the live one."""
    spec = fixtures.mini_spec()

    def stamp():
        return unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            MINI,
            SITE_BEFORE_FONT,
            SITE_JUNIOR_FONT,
            "after-helpers",
        )

    prospect = kernel_exec.SIMULATED_PROSPECT_DEFAULT
    votes = kernel_exec.VOTE_SLOTS_DEFAULT
    base = stamp()
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", not prospect)
    assert stamp() != base
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "VOTE_SLOTS_DEFAULT", not votes)
    assert stamp() != base


# --- the key and cluster byte-contracts ------------------------------------------------


def _unit(codepoints: str, matched: str = "seam-loss-withdrawal") -> Unit:
    row = AuditRow(
        config="default",
        codepoints=codepoints,
        kinds=("seam",),
        matched_entry=matched,
        baseline=("a", "b"),
        new=("c", "d"),
    )
    return Unit(codepoints=codepoints, baseline=row.baseline, new=row.new, class_id=matched, rows=(row,))


_FAMILY_OF = {0xE650: "qsPea", 0xE652: "qsTea", 0xE668: "qsRoe"}


def _keyer(**overrides) -> unit_cache.UnitKeyer:
    family_keys = {"qsPea": "p0", "qsTea": "t0", "qsRoe": "r0", "qsPea_qsTea": "pt0", **overrides}
    return unit_cache.UnitKeyer(family_keys, _FAMILY_OF)


def test_unit_key_moves_only_with_window_families():
    unit = _unit("E650:E652")
    base = _keyer().key(unit)
    assert _keyer(qsRoe="r1").key(unit) == base
    assert _keyer(qsTea="t1").key(unit) != base
    assert _keyer(qsPea_qsTea="pt1").key(unit) != base
    solo = _unit("0020:E650")
    assert _keyer().key(solo) != _keyer(qsPea="p1").key(solo)
    assert _keyer().key(solo) == _keyer(qsPea_qsTea="pt1", qsTea="t1", qsRoe="r1").key(solo)


def test_unit_key_moves_with_row_content():
    assert _keyer().key(_unit("E650:E652")) != _keyer().key(_unit("E650:E652", matched="UNMATCHED"))


_SIGNATURE_ROW = AuditRow(
    config="default",
    codepoints="E650:E652",
    kinds=("seam",),
    matched_entry="seam-loss-withdrawal",
    baseline=("a", "b"),
    new=("c", "d"),
)


def test_signature_key_moves_with_render_identity_not_classification():
    """The soundness split the signature store rests on: everything that can move the placed ink — the window, the config, either font's rendered names, a window family's rune or compiled glyphs — moves the key, while the row's classification fields (kinds, matched_entry) leave it alone, so a ledger edit never re-shapes a window."""
    row = _SIGNATURE_ROW
    base = _keyer().signature_key(row)
    assert _keyer().signature_key(replace(row, kinds=("cell",))) == base
    assert _keyer().signature_key(replace(row, matched_entry="UNMATCHED")) == base
    assert _keyer().signature_key(replace(row, config="ss03")) != base
    assert _keyer().signature_key(replace(row, codepoints="E650:E650")) != base
    assert _keyer().signature_key(replace(row, baseline=("a", "x"))) != base
    assert _keyer().signature_key(replace(row, new=("c", "x"))) != base
    assert _keyer(qsTea="t1").signature_key(row) != base
    assert _keyer(qsPea_qsTea="pt1").signature_key(row) != base
    assert _keyer(qsRoe="r1").signature_key(row) == base


def test_signature_store_round_trip_and_invalidation(tmp_path):
    entries = {"k2": "d2", "k1": "d1"}
    unit_cache.write_signature_store(tmp_path, "env-a", entries)
    assert unit_cache.load_signature_store(tmp_path, "env-a") == entries
    assert unit_cache.load_signature_store(tmp_path, "env-b") is None
    assert unit_cache.load_signature_store(tmp_path / "missing", "env-a") is None
    unit_cache.signature_store_path(tmp_path).write_bytes(b"not a gzip stream")
    assert unit_cache.load_signature_store(tmp_path, "env-a") is None


def test_cluster_id_from_repr_matches_the_tuple_recipe():
    """The c- ids recorded in rebuild/standing-approvals.yaml and prior verdicts are hashes of `repr((tuple(configs), class_id, diffs))`; the repr-string composition the cache rides must reproduce that byte stream exactly, empty diffs and one-element config tuples included."""
    piece = ((("moveTo", ((0, 0),)), ("lineTo", ((5, 0),))),)
    for configs, class_id, diffs in (
        (("default",), "seam-loss-withdrawal", ((), (), 0)),
        (("default", "ss03"), "boundary-echo", ((piece, (), 3), ((), piece, -2))),
        (("ss10",), "ss10-isolation-completed", ((piece, piece, 0),)),
    ):
        expected = "c-" + hashlib.sha1(repr((tuple(configs), class_id, diffs)).encode()).hexdigest()[:8]
        assert _cluster_id(configs, class_id, diffs) == expected


def test_an_absent_manifest_hashes_to_a_sentinel_rather_than_raising(tmp_path):
    """The store's stamp is a hash of the manifest beside it, and the surface it stamps may have none yet — a first build, or a crash between the two writes. The sentinel is what turns that into a stamp mismatch and a full rebuild instead of an exception out of load_store, so it is pinned against both shapes of unreadable rather than left resting on the streamed read happening to raise what the read-whole one did."""
    assert unit_cache._sha256_file(tmp_path / "manifest.json") == "missing"
    assert unit_cache._sha256_file(tmp_path) == "missing"
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert unit_cache._sha256_file(tmp_path / "manifest.json") != "missing"


def test_store_round_trip_and_invalidation(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    cached = unit_cache.CachedUnit(
        key="k1",
        prior_id="u-0001",
        prior_class="boundary-echo",
        ink_identical=False,
        junior_equivalent=False,
        ink_deltas={"default": "d-0123456789ab"},
        diffs_digest="deadbeef",
        cluster="c-12345678",
        family="",
        pair_codepoints=(1, 2),
        proj={
            "pair": [0, 1],
            "after_spans": [[0, 1], [1, 2]],
            "after_cells": ["c", "d"],
            "after_seams": ["y5"],
            "before_spans": [[0, 1], [1, 2]],
            "before_glyphs": ["a", "b"],
            "before_seams": ["break"],
        },
        seams=[
            {
                "pair": [1, 2],
                "before": {"x_min": 0, "x_max": 5, "advance_total": 9},
                "after": {"x_min": 1, "x_max": 6, "advance_total": 9},
            }
        ],
        mismatches=[],
    )
    unit_cache.write_store(tmp_path, "env-a", [cached])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == cached
    assert unit_cache.load_store(tmp_path, "env-b") is None
    (tmp_path / "manifest.json").write_text('{"changed": true}', encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is None
