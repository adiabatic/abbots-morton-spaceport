"""Tests for the persisted per-unit surface cache (issue 20; rebuild/review/unit_cache.py is the contract). The load-bearing claims: an incremental rebuild over an edited audit is byte-identical to a from-scratch build of the same inputs — ids, batches, echo numbering, seam homes, and the store itself included — a no-change rebuild serves every unit, a corrupt or bypassed store degrades to a full build rather than stale bytes, and the serial and parallel paths agree.

None of that is a property of any glyph, so none of it needs the live build: the workload is the frozen mini-M1 bundle under rebuild/review/fixtures/mini/ — a thousand-odd real windows over four letters, their subset-table slices, and the after-font they were extracted with — and the whole module runs in the contracts lane at full width, each build costing seconds rather than the twelve-and-a-half a live subset-table parse cost before serving a workload that never read it. `fixtures/mini/regenerate.py` is how the bundle is refreshed; the key and cluster byte-contracts below are pinned separately over synthetic inputs.
"""

import gzip
import hashlib
import json
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fixtures, kernel_exec, spec_load
from rebuild.review import unit_cache, unit_index
from rebuild.review.audit import SLIM_OMITTED_KEYS, AuditRow, Unit
from rebuild.review.build import (
    SITE_BEFORE_FONT,
    SITE_JUNIOR_FONT,
    _cluster_id,
    _cluster_id_from_repr,
    _write_shard,
    build_m1,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"


def _build(out, bundle, audit_path=MINI_AUDIT, ledger_path=None, **kwargs):
    """One mini surface, always over the frozen bundle: its subset tables, its after-font, and the ledger and spec the `mini_bundle` fixture materializes from the bundle's pin. That pinned spec is what keeps the bundle hermetic — the enricher re-settles every window from it, so reading the repo's live runes would make a rune edit break this module until the bundle was regenerated. A `ledger_path` stands in for the pinned ledger when a test edits one."""
    return build_m1(
        out,
        audit_path=audit_path,
        ledger_path=ledger_path or bundle.ledger,
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
    match = re.search(r"served (\d[\d,]*) of (\d[\d,]*) units from cache", capfd.readouterr().err)
    assert match, "the build did not report its cache plan"
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


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


def _class_meta(surface: Path, class_id: str) -> dict:
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    return next(meta for meta in manifest["classes"] if meta["id"] == class_id)


def _class_fragments(surface: Path, class_id: str) -> list[dict]:
    return [
        fragment
        for part in unit_index.class_shards(_class_meta(surface, class_id))
        for fragment in json.loads((surface / part).read_text(encoding="utf-8"))
    ]


def _ledger_with(bundle, tmp_path: Path, class_id: str, *, no_verdict: bool) -> Path:
    """The bundle's pinned ledger with one class's exemption set as asked — the ledger edit that moves a key-stable unit between the slim and the full fragment shape without moving its content key."""
    entries = yaml.safe_load(bundle.ledger.read_text(encoding="utf-8"))
    entry = next(entry for entry in entries if entry["id"] == class_id)
    assert entry.get("no_verdict", False) != no_verdict
    entry["no_verdict"] = no_verdict
    path = tmp_path / f"ledger-{class_id}.yaml"
    path.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def test_a_slim_fragment_is_the_shape_of_every_unit_that_takes_no_verdict(base_surface):
    """Over the whole mini surface: a fragment omits the explain, the drafts and the highlight exactly when its unit is machine-approved or in a no-verdict class, and carries all three otherwise — and the store records which shape it wrote for each."""
    manifest = json.loads((base_surface / "manifest.json").read_text(encoding="utf-8"))
    store = unit_cache.load_store(base_surface, _store_environment(base_surface))
    assert store is not None
    slim_by_id = {record.prior_id: record.slim for record in store.values()}
    shapes = {True: 0, False: 0}
    for meta in manifest["classes"]:
        for fragment in _class_fragments(base_surface, meta["id"]):
            slim = fragment["batch"] is None
            assert slim == (
                bool(meta["no_verdict"])
                or any(fragment[c] for c in ("ink_identical", "picture_identical", "junior_equivalent"))
            )
            assert [key in fragment for key in SLIM_OMITTED_KEYS] == [not slim] * len(
                SLIM_OMITTED_KEYS
            ), fragment["id"]
            assert slim_by_id[fragment["id"]] is slim
            shapes[slim] += 1
    assert shapes[True] and shapes[False], "the mini surface must hold both fragment shapes"


def _store_environment(surface: Path) -> str:
    with gzip.open(unit_cache.store_path(surface), "rt", encoding="utf-8") as stream:
        return json.loads(next(stream))["environment"]


def _crossing_class(surface: Path, *, no_verdict: bool) -> tuple[str, int]:
    """A class of the mini surface whose exemption is as asked and which holds units no machine channel approves — the units a flip of that exemption moves between the fragment shapes — with their count."""
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    for meta in manifest["classes"]:
        crossing = meta["unit_count"] - meta["machine_approved_count"]
        if bool(meta["no_verdict"]) is no_verdict and crossing > 0:
            return meta["id"], crossing
    raise AssertionError(
        f"the mini surface holds no {'exempt' if no_verdict else 'human'} class a flip could move"
    )


def test_a_unit_crossing_into_the_human_workload_is_re_enriched_in_full(
    base_surface, mini_bundle, tmp_path, capfd
):
    """The exemption is the ledger's and sits outside the content key, so a key-stable unit can be served a fragment of the wrong shape unless the store says which it holds: when a class loses its `no_verdict`, every unit of it that no machine channel approves is a miss — drafted in full rather than served slim — while the machine-approved ones stay served, and the surface lands byte-for-byte on a from-scratch build under the edited ledger."""
    EXEMPT_CLASS, crossing = _crossing_class(base_surface, no_verdict=True)
    assert not _class_meta(base_surface, EXEMPT_CLASS)["batches"]
    ledger = _ledger_with(mini_bundle, tmp_path, EXEMPT_CLASS, no_verdict=False)
    incremental = _copy(base_surface, tmp_path)
    capfd.readouterr()
    _build(incremental, mini_bundle, ledger_path=ledger, jobs=1)
    served, total = _served(capfd)
    assert total - served == crossing
    for fragment in _class_fragments(incremental, EXEMPT_CLASS):
        assert fragment["no_verdict"] is False
        whole = fragment["batch"] is not None
        assert all((key in fragment) == whole for key in SLIM_OMITTED_KEYS), fragment["id"]
        if whole:
            assert fragment["drafts"]["pin"]["expect"]
    scratch = tmp_path / "scratch"
    _build(scratch, mini_bundle, ledger_path=ledger, jobs=1)
    assert _tree(incremental) == _tree(scratch)


def test_a_unit_crossing_out_of_the_human_workload_is_written_slim(
    base_surface, mini_bundle, tmp_path, capfd
):
    """The other direction of the same flip: a class that gains `no_verdict` has its human units re-drafted slim rather than served whole with drafts nobody will read, so a served surface is the surface a cache-blind build writes."""
    HUMAN_CLASS, crossing = _crossing_class(base_surface, no_verdict=False)
    ledger = _ledger_with(mini_bundle, tmp_path, HUMAN_CLASS, no_verdict=True)
    incremental = _copy(base_surface, tmp_path)
    capfd.readouterr()
    _build(incremental, mini_bundle, ledger_path=ledger, jobs=1)
    served, total = _served(capfd)
    assert total - served == crossing
    for fragment in _class_fragments(incremental, HUMAN_CLASS):
        assert fragment["no_verdict"] is True and fragment["batch"] is None
        assert not any(key in fragment for key in SLIM_OMITTED_KEYS), fragment["id"]
    scratch = tmp_path / "scratch"
    _build(scratch, mini_bundle, ledger_path=ledger, jobs=1)
    assert _tree(incremental) == _tree(scratch)


def _shard_paths(surface: Path) -> list[Path]:
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    return [surface / part for meta in manifest["classes"] for part in unit_index.class_shards(meta)]


def test_a_fragment_whose_stamp_moved_is_not_served(base_surface, mini_bundle, tmp_path, capfd):
    """The cache fetches a prior fragment by id, and the id alone says nothing about what is in the file. So the store records the stamp the fragment was emitted with and the build serves it only when the shard on disk still carries that stamp; a fragment edited underneath the store falls back to a fresh computation, which is what puts the correct bytes back."""
    surface = _copy(base_surface, tmp_path)
    path = next(path for path in _shard_paths(surface) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    fragments[0]["content_key"] = "0" * 64
    path.write_text(json.dumps(fragments), encoding="utf-8")
    capfd.readouterr()
    _build(surface, mini_bundle, jobs=1)
    served, total = _served(capfd)
    assert served == total - 1
    assert _tree(surface) == _tree(base_surface)


def _rewrite_store(surface: Path, edit) -> None:
    """The store rewritten in place with `edit` applied to every record, the header kept."""
    path = unit_cache.store_path(surface)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        lines = stream.read().splitlines()
    edited = [lines[0]] + [json.dumps(edit(json.loads(line))) for line in lines[1:]]
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("\n".join(edited) + "\n")


def _walked(monkeypatch) -> list[dict[str, set[str]]]:
    """Every `wanted` map the build hands the walk, so a test can say whether the shards were parsed to place a unit."""
    calls: list[dict[str, set[str]]] = []
    real = unit_cache.locate_prior_fragments

    def spy(out_dir, wanted):
        calls.append({class_id: set(ids) for class_id, ids in wanted.items()})
        return real(out_dir, wanted)

    monkeypatch.setattr(unit_cache, "locate_prior_fragments", spy)
    return calls


def test_a_served_build_places_every_unit_from_the_store_without_walking_the_shards(
    base_surface, mini_bundle, tmp_path, capfd, monkeypatch
):
    """The store record carries the address the shard writer returned for its fragment, so a no-change rebuild's plan is a lookup into the store: the previous surface's shards are never parsed to find a unit, and each served fragment is parsed exactly once, at the write, where the reader holds it to the record's id and stamp."""
    surface = _copy(base_surface, tmp_path)
    store = unit_cache.load_store(surface, _environment(surface))
    assert store is not None and all(cached.address is not None for cached in store.values())
    calls = _walked(monkeypatch)
    _build(surface, mini_bundle, jobs=1)
    served, total = _served(capfd)
    assert served == total
    assert calls == []
    assert _tree(surface) == _tree(base_surface)


def _environment(surface: Path) -> str:
    with gzip.open(unit_cache.store_path(surface), "rt", encoding="utf-8") as stream:
        return json.loads(next(stream))["environment"]


def test_a_store_without_addresses_still_serves_every_unit_through_the_walk(
    base_surface, mini_bundle, tmp_path, capfd, monkeypatch
):
    """A store written before addresses were recorded names each unit's fragment by id and class alone, and the build still serves from it — the walk over the previous surface's shards places what the store cannot — and lands byte for byte on the surface an addressed store serves, the rewritten store's addresses included."""
    surface = _copy(base_surface, tmp_path)
    _rewrite_store(surface, lambda record: {key: value for key, value in record.items() if key != "address"})
    store = unit_cache.load_store(surface, _environment(surface))
    assert store is not None and all(cached.address is None for cached in store.values())
    calls = _walked(monkeypatch)
    capfd.readouterr()
    _build(surface, mini_bundle, jobs=1)
    served, total = _served(capfd)
    assert served == total
    assert sum(len(ids) for call in calls for ids in call.values()) == total
    assert _tree(surface) == _tree(base_surface)


def test_a_shard_rewritten_underneath_the_store_is_walked_rather_than_trusted(
    base_surface, mini_bundle, tmp_path, capfd, monkeypatch
):
    """An address is only as good as the bytes it was taken over, and the manifest stamp says nothing about a shard's bytes. The store records each part's size instead, so a part rewritten underneath it — here compactly, every stamp intact — is placed by the walk again and every unit still serves, while the parts that did not move are trusted as before."""
    surface = _copy(base_surface, tmp_path)
    path = next(path for path in _shard_paths(surface) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(fragments), encoding="utf-8")
    calls = _walked(monkeypatch)
    capfd.readouterr()
    _build(surface, mini_bundle, jobs=1)
    served, total = _served(capfd)
    assert served == total
    assert sum(len(ids) for call in calls for ids in call.values()) == len(fragments)
    assert _tree(surface) == _tree(base_surface)


def test_an_edit_in_place_under_a_trusted_address_is_refused_at_the_write(
    base_surface, mini_bundle, tmp_path
):
    """The size guard cannot see an edit that leaves a part exactly as long as it was, so such a fragment is trusted into the plan and caught where every served fragment is held to its record: the reader refuses it at the write, loudly, rather than serving bytes the store does not describe."""
    surface = _copy(base_surface, tmp_path)
    path = next(path for path in _shard_paths(surface) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    stamp = fragments[0]["content_key"]
    raw = path.read_bytes()
    edited = raw.replace(stamp.encode(), ("0" * len(stamp)).encode(), 1)
    assert len(edited) == len(raw)
    path.write_bytes(edited)
    with pytest.raises(SystemExit) as raised:
        _build(surface, mini_bundle, jobs=1)
    assert "cannot be read back" in str(raised.value)


def test_a_store_whose_ink_deltas_moved_fails_the_verification_sample(base_surface, mini_bundle, tmp_path):
    """The ink deltas sit outside the content key (they are a carry-presentation field), so the stamp cannot speak for them and the served-vs-recomputed sample compares them beside it. A store whose deltas no longer describe the fonts is exactly the drift that would otherwise ship silently."""
    surface = _copy(base_surface, tmp_path)
    path = unit_cache.store_path(surface)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        lines = stream.read().splitlines()
    edited = [lines[0]]
    for line in lines[1:]:
        record = json.loads(line)
        record["ink_deltas"] = {**record["ink_deltas"], "bogus": "d-000000000000"}
        edited.append(json.dumps(record))
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("\n".join(edited) + "\n")
    with pytest.raises(SystemExit) as raised:
        _build(surface, mini_bundle, jobs=1)
    assert "fresh recomputation" in str(raised.value)


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
    match = re.search(r"signatures: (\d[\d,]*) cached, (\d[\d,]*) shaped", capfd.readouterr().err)
    assert match, "the build did not report its signature plan"
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


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


def test_unit_store_environment_ignores_the_contact_allow_list(tmp_path):
    """The store this stamp guards is the expensive one — dropping it rebuilds every unit of the surface cold — and the contact allow-list is the input least entitled to drop it: the defect gate is its only reader, nothing under the review build opens it, and a bless is two lines. The `data` line folds `fingerprint.data_paths`, and the allow-list sits outside that list, so the fold cannot reach it by any door. A hand-built root rather than the repo's, so the edit is a real one and the assertion is not about a file this suite may not write."""
    spec = fixtures.mini_spec()
    root = tmp_path / "repo"
    (root / "rebuild").mkdir(parents=True)
    registry = root / "rebuild" / "script.yaml"
    registry.write_text("alphabet: []\n", encoding="utf-8")
    allow = root / "rebuild" / "m1-contact-allow.yaml"

    def stamp():
        return unit_cache.environment_stamp(root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers")

    base = stamp()
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y1'}\n", encoding="utf-8")
    assert stamp() == base
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y2'}\n", encoding="utf-8")
    assert stamp() == base
    registry.write_text("alphabet: [edited]\n", encoding="utf-8")
    assert stamp() != base


SURFACE_UNREAD_CODE = (
    "rebuild/pipeline/run_m1.py",
    "rebuild/pipeline/oracle.py",
    "rebuild/pipeline/defects.py",
    "rebuild/pipeline/compile_font.py",
    "rebuild/kernel-rs/src/fixpoint.rs",
    "rebuild/kernel-rs/src/fold.rs",
    "rebuild/kernel-rs/src/fanout.rs",
)
SURFACE_READ_CODE = (
    "rebuild/pipeline/kernel_exec.py",
    "rebuild/validation/shaping.py",
    "rebuild/review/enrich.py",
    "rebuild/kernel-rs/src/engine.rs",
    "rebuild/kernel-rs/Cargo.lock",
)


def test_both_store_stamps_survive_a_pipeline_or_crate_edit_the_surface_never_reads(tmp_path):
    """The narrowing both stamps' code line makes (`unit_cache.surface_code_paths`), stated as the cost it avoids: an edit to the driver, the oracle, a gate, the font compile or the crate's enumeration and fold — code the surface build never executes — would drop both stores through a whole-tree `pipeline_code` component and cost the next build a cold units phase. It moves neither stamp, while an edit to a module the build does run — the kernel seam, the shaper, the enricher, the crate's engine, the crate's lock file — moves both. A hand-built root, so the edits are real files and the assertion is about the rosters rather than about this checkout; rebuild/test_review_code_closure.py is what holds those rosters to the walked closure."""
    spec = fixtures.mini_spec()
    root = tmp_path / "repo"
    for relative in SURFACE_UNREAD_CODE + SURFACE_READ_CODE:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(f"{relative}\n", encoding="utf-8")

    def stamps() -> tuple[str, str]:
        return (
            unit_cache.environment_stamp(root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers"),
            unit_cache.signature_environment(root, MINI_FONT, "after-helpers"),
        )

    base = stamps()
    for relative in SURFACE_UNREAD_CODE:
        (root / relative).write_text(f"{relative} edited\n", encoding="utf-8")
        assert stamps() == base, relative
    previous = base
    for relative in SURFACE_READ_CODE:
        (root / relative).write_text(f"{relative} edited\n", encoding="utf-8")
        current = stamps()
        assert current[0] != previous[0], relative
        assert current[1] != previous[1], relative
        previous = current


REFUSE_RUNE = """\
rune: qsPea
policy:
  refuse:
  - {exit: baseline, why: two verticals render thick}
"""


def test_a_refuse_why_edit_moves_only_that_family_key(tmp_path):
    """The grain the quoted prose invalidates at. A refusal's `why` is quoted into the explain text a unit serves, so rewording one has to re-enrich the windows that show it — but only those: the families whose keys move are the reworded rune and whatever reaches it through `resolve.against`, and the whole-store stamp does not move at all, so nothing outside those windows is rebuilt. A hand-built root rather than the repo's, so the edit is a real one and the fixture spec supplies the closure."""
    spec = fixtures.mini_spec()
    runes = tmp_path / "glyph_data" / "runes"
    runes.mkdir(parents=True)
    (runes / "qsTea.yaml").write_text("rune: qsTea\n", encoding="utf-8")
    (runes / "qsPea.yaml").write_text(REFUSE_RUNE, encoding="utf-8")

    def stamp():
        return unit_cache.environment_stamp(tmp_path, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers")

    before, _ = unit_cache.family_content_keys(tmp_path, spec, MINI_FONT)
    before_stamp = stamp()
    (runes / "qsPea.yaml").write_text(REFUSE_RUNE.replace("render thick", "render thin"), encoding="utf-8")
    after, _ = unit_cache.family_content_keys(tmp_path, spec, MINI_FONT)
    assert set(after) == set(before)
    moved = {name for name in before if after[name] != before[name]}
    assert "qsPea" in moved
    closure = spec_load.rune_closure(spec)
    assert all(name == "qsPea" or "qsPea" in closure.get(name, frozenset()) for name in moved)
    assert stamp() == before_stamp


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
    """The c- ids recorded in rebuild/standing-approvals.yaml and prior verdicts are hashes of `repr((tuple(configs), class_id, diffs))`; the piecewise hashing the runner does over the diffs' repr bytes must reproduce that byte stream exactly, empty diffs and one-element config tuples included."""
    piece = ((("moveTo", ((0, 0),)), ("lineTo", ((5, 0),))),)
    for configs, class_id, diffs in (
        (("default",), "seam-loss-withdrawal", ((), (), 0)),
        (("default", "ss03"), "boundary-echo", ((piece, (), 3), ((), piece, -2))),
        (("ss10",), "ss10-isolation-completed", ((piece, piece, 0),)),
    ):
        expected = "c-" + hashlib.sha1(repr((tuple(configs), class_id, diffs)).encode()).hexdigest()[:8]
        assert _cluster_id(configs, class_id, diffs) == expected
        assert _cluster_id_from_repr(configs, class_id, repr(diffs).encode()) == expected


def test_the_cluster_a_fresh_unit_carries_keys_on_its_final_class(base_surface):
    """The runner computes a unit's cluster where it assigns the verdict family, so an UNMATCHED unit's cluster must key on that family — the class its fragment is sharded under — and a ledger-classed unit's on its ledger class. Every fragment carries its class, a human fragment carries its cluster, and the diffs behind the id are gone with the worker, so the witness is the store: every unit's record carries a cluster, machine-approved ones included, a served unit's cluster is trusted from it, and that is only sound if the fresh computation keyed on the same class the store records as the unit's."""
    manifest = json.loads((base_surface / "manifest.json").read_text(encoding="utf-8"))
    with gzip.open(unit_cache.store_path(base_surface), "rt", encoding="utf-8") as stream:
        environment = json.loads(next(stream))["environment"]
    store = unit_cache.load_store(base_surface, environment)
    assert store
    by_id = {cached.prior_id: cached for cached in store.values()}
    seen_family = False
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            for fragment in json.loads((base_surface / part).read_text(encoding="utf-8")):
                cached = by_id[fragment["id"]]
                assert cached.cluster.startswith("c-")
                if fragment["batch"] is not None:
                    assert fragment["cluster"] == cached.cluster
                assert cached.prior_class == meta["id"]
                if cached.family:
                    seen_family = True
                    assert meta["id"] == cached.family
    assert seen_family


def test_from_record_interns_what_repeats_across_records():
    """Two records that name the same class, cluster, family, config, delta, cell name or seam token parse to the same string objects, so a million-record store costs one instance per distinct name; the per-record keys, which never repeat, are left alone."""
    first = unit_cache.CachedUnit.from_record(json.loads(json.dumps(_round_trip_unit().to_record())))
    second = unit_cache.CachedUnit.from_record(json.loads(json.dumps(_round_trip_unit().to_record())))
    assert first.prior_class is second.prior_class is sys.intern("boundary-echo")
    assert first.cluster is second.cluster
    assert first.diffs_digest is second.diffs_digest
    assert first.family is second.family
    assert next(iter(first.ink_deltas)) is next(iter(second.ink_deltas)) is sys.intern("default")
    assert first.ink_deltas["default"] is second.ink_deltas["default"]
    for name in ("after_cells", "after_seams", "before_glyphs", "before_seams"):
        assert all(a is b for a, b in zip(first.proj[name], second.proj[name], strict=True)), name
    assert first.proj["after_seams"][0] is sys.intern("y5")
    assert first == second


def test_an_absent_manifest_hashes_to_a_sentinel_rather_than_raising(tmp_path):
    """The store's stamp is the identity of the manifest beside it, and the surface it stamps may have none yet — a first build, or a crash between the two writes. The sentinel is what turns that into a stamp mismatch and a full rebuild instead of an exception out of load_store, so it is pinned against both shapes of unreadable rather than left resting on the streamed read happening to raise what the read-whole one did."""
    assert unit_cache._manifest_stamp(tmp_path) == "missing"
    assert unit_cache._sha256_file(tmp_path / "manifest.json") == "missing"
    assert unit_cache._sha256_file(tmp_path) == "missing"
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert unit_cache._manifest_stamp(tmp_path) != "missing"
    assert unit_cache._sha256_file(tmp_path / "manifest.json") != "missing"


def _round_trip_unit() -> unit_cache.CachedUnit:
    return unit_cache.CachedUnit(
        key="k1",
        prior_id="u-0001",
        prior_class="boundary-echo",
        content_key="f" * 64,
        slim=False,
        address=None,
        ink_identical=False,
        picture_identical=False,
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


def test_store_round_trip_and_invalidation(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    cached = _round_trip_unit()
    unit_cache.write_store(tmp_path, "env-a", [cached])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == cached
    slim = replace(cached, key="k2", slim=True)
    unit_cache.write_store(tmp_path, "env-a", [cached, slim])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k2"].slim is True and loaded["k1"].slim is False
    assert unit_cache.load_store(tmp_path, "env-b") is None
    (tmp_path / "manifest.json").write_text('{"changed": true}', encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is None


def test_a_record_keeps_its_address_only_while_its_part_is_the_size_the_store_recorded(tmp_path):
    """The address round-trips beside the stamp, and `load_store` drops it — the record itself surviving — once the part it points into is no longer the size the header recorded, which is what routes a rewritten part to the walk; a record written without an address loads with none."""
    fragments = [{"id": "u-0000", "content_key": "k0"}, {"id": "u-0001", "content_key": "f" * 64}]
    parts, spans = _write_shard(tmp_path, "small", fragments)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    addressed = replace(_round_trip_unit(), address=(parts[0], spans[1][1], spans[1][2]))
    unaddressed = replace(_round_trip_unit(), key="k2", prior_id="u-0002")
    unit_cache.write_store(tmp_path, "env-a", [addressed, unaddressed])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == addressed and loaded["k2"].address is None
    located = loaded["k1"].located()
    assert located is not None and (located.unit_id, located.content_key) == ("u-0001", "f" * 64)
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert reader.read(located) == fragments[1]
    (tmp_path / parts[0]).write_text(json.dumps(fragments), encoding="utf-8")
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == replace(addressed, address=None)
    assert loaded["k1"].located() is None


def test_an_assets_refresh_leaves_the_store_loadable(tmp_path):
    """The store is stamped with the manifest's identity rather than its bytes, so rewriting `inputs_fingerprint.static` over a served surface — the whole of what an assets refresh does — leaves the store describing the shards beside it, where anything the served units depend on still drops it and costs the next build a full pass."""
    manifest = {"generated_at": "2026-01-01T00:00:00Z", "inputs_fingerprint": {"data": "d", "static": "s"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    unit_cache.write_store(tmp_path, "env-a", [_round_trip_unit()])
    refreshed = {**manifest, "inputs_fingerprint": {"data": "d", "static": "refreshed"}}
    (tmp_path / "manifest.json").write_text(json.dumps(refreshed, indent=2), encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is not None
    (tmp_path / "manifest.json").write_text(
        json.dumps({**refreshed, "generated_at": "2099-01-01T00:00:00Z"}), encoding="utf-8"
    )
    assert unit_cache.load_store(tmp_path, "env-a") is None


def _prior_surface(root: Path, classes: list[dict], *, legacy: bool) -> None:
    key = "shard" if legacy else "shards"
    entries = [{"id": meta["id"], key: meta["shards"][0] if legacy else meta["shards"]} for meta in classes]
    (root / "manifest.json").write_text(json.dumps({"classes": entries}), encoding="utf-8")


def _sliced(root: Path, located: unit_cache.PriorFragment) -> dict:
    """The fragment at a located address, read the way the browser's Range request and `PriorFragmentReader` both read it: that slice of the part's bytes, parsed alone."""
    raw = (root / located.part).read_bytes()
    return json.loads(raw[located.start : located.start + located.length])


def test_locate_prior_fragments_addresses_a_class_the_last_build_split(tmp_path, monkeypatch):
    """The cache asks the prior manifest which files a class was written as, because a class large enough to be split has no single name to guess at — and what it keeps of each wanted fragment is an address and the stamp found there, never the fragment, so the plan can be made without holding the previous surface. A class the manifest does not list contributes nothing, exactly as a missing file does."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 32)
    fragments = [{"id": f"u-{index:04d}", "content_key": f"k{index}"} for index in range(6)]
    parts, _spans = _write_shard(tmp_path, "big", fragments)
    assert len(parts) > 1
    _prior_surface(tmp_path, [{"id": "big", "shards": parts}], legacy=False)
    located = unit_cache.locate_prior_fragments(tmp_path, {"big": {"u-0001", "u-0005"}, "gone": {"u-0000"}})
    assert set(located) == {"u-0001", "u-0005"}
    assert {found.part for found in located.values()} <= set(parts)
    for index in (1, 5):
        found = located[f"u-{index:04d}"]
        assert (found.unit_id, found.content_key) == (f"u-{index:04d}", f"k{index}")
        assert _sliced(tmp_path, found) == fragments[index]
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert [reader.read(located[uid]) for uid in ("u-0001", "u-0005")] == [fragments[1], fragments[5]]


def test_locate_prior_fragments_reads_a_format_1_prior_surface(tmp_path):
    """A prior surface may still be `ams-review-manifest/1`, one `shard` string per class, and the cache has to serve from it or the next build re-enriches every unit it could have carried."""
    fragments = [{"id": "u-0000"}, {"id": "u-0001", "content_key": "k1"}]
    parts, _spans = _write_shard(tmp_path, "small", fragments)
    assert parts == ["units/small.json"]
    _prior_surface(tmp_path, [{"id": "small", "shards": parts}], legacy=True)
    located = unit_cache.locate_prior_fragments(tmp_path, {"small": {"u-0001"}})
    assert list(located) == ["u-0001"]
    assert located["u-0001"].content_key == "k1"
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert reader.read(located["u-0001"]) == fragments[1]


def test_locate_prior_fragments_with_no_manifest_contributes_nothing(tmp_path):
    """A first build, or a prior surface deleted by hand, has no manifest to read — its units fall back to a fresh computation rather than raising."""
    assert unit_cache.locate_prior_fragments(tmp_path, {"small": {"u-0000"}}) == {}


def test_locate_prior_fragments_walks_a_shard_rewritten_by_hand(tmp_path):
    """The address is taken off the part's own text rather than assumed from the writer's framing, so a shard something rewrote compactly — the shape `test_a_fragment_whose_stamp_moved_is_not_served` leaves on disk — still locates every fragment, and the recorded bytes still parse to it."""
    fragments = [{"id": "u-0000", "content_key": "k0"}, {"id": "u-0001", "content_key": "k1"}]
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "small.json").write_text(json.dumps(fragments), encoding="utf-8")
    _prior_surface(tmp_path, [{"id": "small", "shards": ["units/small.json"]}], legacy=False)
    located = unit_cache.locate_prior_fragments(tmp_path, {"small": {"u-0000", "u-0001"}})
    assert [_sliced(tmp_path, located[fragment["id"]]) for fragment in fragments] == fragments


def test_a_part_that_is_not_ascii_is_not_addressable(tmp_path):
    """A character offset is a byte offset only under `ensure_ascii`, which every part this build writes shares and a hand-edited one may not. Rather than record an address that would read the wrong bytes, the locate pass declines such a part, and its units fall back to a fresh computation — over-invalidation being the safe direction here as everywhere in the cache."""
    fragments = [{"id": "u-0000", "content_key": "k0", "notation": "·Tea"}]
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "small.json").write_text(
        json.dumps(fragments, ensure_ascii=False), encoding="utf-8"
    )
    _prior_surface(tmp_path, [{"id": "small", "shards": ["units/small.json"]}], legacy=False)
    assert unit_cache.locate_prior_fragments(tmp_path, {"small": {"u-0000"}}) == {}


def test_the_reader_refuses_a_fragment_that_moved_under_its_address(tmp_path):
    """An address is recorded at plan time and read at write time. The shard writer keeps the previous surface whole between the two by deferring its renames, and the reader holds every read against the id and stamp the address was located with, so anything that slips past that discipline is a refusal rather than another unit's bytes served under this one's id."""
    fragments = [{"id": "u-0000", "content_key": "k0"}, {"id": "u-0001", "content_key": "k1"}]
    parts, _spans = _write_shard(tmp_path, "small", fragments)
    _prior_surface(tmp_path, [{"id": "small", "shards": parts}], legacy=False)
    located = unit_cache.locate_prior_fragments(tmp_path, {"small": {"u-0001"}})
    _write_shard(tmp_path, "small", [fragments[0], {"id": "u-0009", "content_key": "k1"}])
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        with pytest.raises(ValueError, match="changed underneath"):
            reader.read(located["u-0001"])
