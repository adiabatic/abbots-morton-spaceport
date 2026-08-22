"""Tests for the persisted per-unit surface cache (issue 20; rebuild/review/unit_cache.py is the contract). The load-bearing claims: an incremental rebuild over an edited audit is byte-identical to a from-scratch build of the same inputs — ids, batches, echo numbering, seam homes, and the store itself included — a no-change rebuild serves every unit, a corrupt or bypassed store degrades to a full build rather than stale bytes, and the serial and parallel paths agree. The mini workload filters the real divergence audit down to a few families so each build costs seconds; the key and cluster byte-contracts are pinned separately over synthetic inputs."""

import hashlib
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from rebuild.pipeline import fixtures, settle
from rebuild.review import unit_cache
from rebuild.review.audit import AuditRow, Unit
from rebuild.review.build import SITE_BEFORE_FONT, SITE_JUNIOR_FONT, _cluster_id, build_m1

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "rebuild" / "m1-divergences.yaml"

_LETTERS = {"E650", "E652", "E653", "E668"}
_BOUNDARIES = {"0020", "200C", "00B7"}


@pytest.fixture(scope="module")
def mini_audit(tmp_path_factory, live_artifacts):
    lines = live_artifacts.audit.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    kept = []
    for row in rows:
        parts = set(row.split("\t")[1].split(":"))
        if parts <= (_LETTERS | _BOUNDARIES) and parts & _LETTERS:
            kept.append(row)
    assert len(kept) > 200, "the letter filter no longer selects a meaningful workload"
    path = tmp_path_factory.mktemp("unit-cache-audit") / "audit.tsv"
    path.write_text("\n".join([header] + kept) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def base_surface(mini_audit, tmp_path_factory):
    out = tmp_path_factory.mktemp("unit-cache-base") / "surface"
    build_m1(out, audit_path=mini_audit, ledger_path=LEDGER, jobs=1)
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


def test_no_change_rebuild_serves_every_unit_and_is_byte_stable(base_surface, mini_audit, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    before = _tree(surface)
    build_m1(surface, audit_path=mini_audit, ledger_path=LEDGER, jobs=1)
    served, total = _served(capfd)
    assert served == total
    assert _tree(surface) == before


def _edited_audit(mini_audit: Path, tmp_path: Path) -> Path:
    lines = mini_audit.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    windows: list[str] = []
    for row in rows:
        codepoints = row.split("\t")[1]
        if codepoints not in windows:
            windows.append(codepoints)
    dropped, retagged = windows[3], windows[7]
    edited = []
    for row in rows:
        fields = row.split("\t")
        if fields[1] == dropped:
            continue
        if fields[1] == retagged:
            fields[3] = "UNMATCHED"
        edited.append("\t".join(fields))
    path = tmp_path / "audit-edited.tsv"
    path.write_text("\n".join([header] + edited) + "\n", encoding="utf-8")
    return path


def test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit(
    base_surface, mini_audit, tmp_path, capfd
):
    """The soundness gate at mini scale: dropping one window renumbers every unit behind it and retagging another moves its class, and the incremental pass — serving nearly everything, re-patching ids, batches, echo numbers, and seam homes — must land byte-for-byte on what a cache-blind build of the same audit writes, the store included."""
    incremental = _copy(base_surface, tmp_path)
    edited = _edited_audit(mini_audit, tmp_path)
    capfd.readouterr()
    build_m1(incremental, audit_path=edited, ledger_path=LEDGER, jobs=1)
    served, total = _served(capfd)
    assert 0 < total - served <= 2
    scratch = tmp_path / "scratch"
    build_m1(scratch, audit_path=edited, ledger_path=LEDGER, jobs=1)
    assert _tree(incremental) == _tree(scratch)


def test_corrupt_store_degrades_to_a_full_build(base_surface, mini_audit, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    unit_cache.store_path(surface).write_bytes(b"not a gzip stream")
    build_m1(surface, audit_path=mini_audit, ledger_path=LEDGER, jobs=1)
    served, _total = _served(capfd)
    assert served == 0
    assert _tree(surface) == _tree(base_surface)


def test_fresh_unit_cache_bypasses_a_warm_store(base_surface, mini_audit, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    before = _tree(surface)
    build_m1(surface, audit_path=mini_audit, ledger_path=LEDGER, jobs=1, fresh_unit_cache=True)
    served, _total = _served(capfd)
    assert served == 0
    assert _tree(surface) == before


def test_serial_and_parallel_builds_are_byte_identical(base_surface, mini_audit, tmp_path):
    parallel = tmp_path / "parallel"
    build_m1(parallel, audit_path=mini_audit, ledger_path=LEDGER, jobs=2)
    assert _tree(parallel) == _tree(base_surface)


def _signatures(capfd) -> tuple[int, int]:
    match = re.search(r"signatures: (\d+) cached, (\d+) shaped", capfd.readouterr().err)
    assert match, "the build did not report its signature plan"
    return int(match.group(1)), int(match.group(2))


def test_no_change_rebuild_serves_every_signature(base_surface, mini_audit, tmp_path, capfd):
    surface = _copy(base_surface, tmp_path)
    build_m1(surface, audit_path=mini_audit, ledger_path=LEDGER, jobs=1)
    cached, shaped = _signatures(capfd)
    assert cached > 0
    assert shaped == 0


def test_corrupt_signature_store_reshapes_and_degrades_to_the_same_bytes(
    base_surface, mini_audit, tmp_path, capfd
):
    surface = _copy(base_surface, tmp_path)
    unit_cache.signature_store_path(surface).write_bytes(b"not a gzip stream")
    build_m1(surface, audit_path=mini_audit, ledger_path=LEDGER, jobs=1)
    cached, shaped = _signatures(capfd)
    assert cached == 0
    assert shaped > 0
    assert _tree(surface) == _tree(base_surface)


def test_unit_store_environment_tracks_each_kernel_settlement_mode(live_artifacts, monkeypatch):
    spec = fixtures.mini_spec()

    def stamp():
        return unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            live_artifacts.m1,
            SITE_BEFORE_FONT,
            SITE_JUNIOR_FONT,
            "after-helpers",
        )

    prospect = settle.SIMULATED_PROSPECT_DEFAULT
    votes = settle.VOTE_SLOTS_DEFAULT
    base = stamp()
    monkeypatch.setattr(settle, "SIMULATED_PROSPECT_DEFAULT", not prospect)
    assert stamp() != base
    monkeypatch.setattr(settle, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(settle, "VOTE_SLOTS_DEFAULT", not votes)
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
