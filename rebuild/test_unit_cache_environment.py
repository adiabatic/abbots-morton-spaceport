"""Tests that the surface cache keeps serving across a recompiled after font.

A rune edit recompiles the after font: its GSUB lookup list changes whether or not any window's shaping does, and the edited letter's compiled glyphs change with it. The audit edits in rebuild/test_unit_cache.py do not cover this case.

The edited font here makes both changes at once, as a real cycle does: it appends a GSUB lookup that no feature references, and it widens every glyph of one family. The GSUB change must not reach either whole-store stamp, and the widened family must invalidate only the windows that can reach it. The bundle under rebuild/review/fixtures/mini/ keeps this module in the contracts lane; its regenerate.py describes what the bundle holds.
"""

import copy
import re
import shutil
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from rebuild.review import unit_cache, unit_index
from rebuild.review.build import build_m1
from rebuild.review.enrich import load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_FONT = MINI / "M1.otf"
MOVED_FAMILY = "qsRoe"
WIDENED_BY = 10


def _mini_build(out: Path, after_font: Path, bundle, **kwargs) -> dict:
    return build_m1(
        out,
        audit_path=MINI / "audit.tsv",
        ledger_path=bundle.ledger,
        subset_dir=MINI,
        after_font=after_font,
        spec_root=bundle.spec_root,
        subset_pack=bundle.subset_pack,
        jobs=1,
        **kwargs,
    )


def _with_extra_gsub_lookup(source: Path, target: Path) -> Path:
    """Copy the font with one extra GSUB lookup that nothing references. This changes the GSUB lookup list, as a rune edit's recompile does, and cannot change any shaped run."""
    font = TTFont(str(source))
    lookups = font["GSUB"].table.LookupList  # pyright: ignore[reportAttributeAccessIssue]
    lookups.Lookup.append(copy.deepcopy(lookups.Lookup[0]))
    lookups.LookupCount = len(lookups.Lookup)
    font.save(str(target))
    return target


def _with_a_widened_family(source: Path, target: Path) -> Path:
    """Copy the font with every glyph of `MOVED_FAMILY` given a wider advance. The family's compiled-glyph digest changes, so every window that can reach that letter must be recomputed, and every window that cannot must still be served."""
    font = TTFont(str(source))
    metrics = font["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
    widened = 0
    for name in list(metrics):
        if name.split(".")[0] == MOVED_FAMILY:
            advance, lsb = metrics[name]
            metrics[name] = (advance + WIDENED_BY, lsb)
            widened += 1
    assert widened, f"no {MOVED_FAMILY} glyphs in {source}"
    font.save(str(target))
    return target


def _recompiled(source: Path, target: Path) -> Path:
    """Apply both changes, as a real cycle's after font has them."""
    scratch = target.with_name("wiring-" + target.name)
    return _with_a_widened_family(_with_extra_gsub_lookup(source, scratch), target)


def _tree(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(Path(path).rglob("*")) if p.is_file()
    }


def _content_keys(surface: Path) -> dict[str, str]:
    import json

    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    keys: dict[str, str] = {}
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            for unit in json.loads((surface / part).read_text(encoding="utf-8")):
                keys[unit["codepoints"]] = unit["content_key"]
    return keys


def _served(capfd) -> tuple[int, int]:
    match = re.search(r"served (\d[\d,]*) of (\d[\d,]*) units from cache", capfd.readouterr().err)
    assert match, "the build did not report its cache plan"
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


@pytest.fixture(scope="module")
def spec(mini_bundle):
    """Load the bundle's own spec. The stamps below are taken over the bundle's spec root. Each test compares keys from the same root before and after a font change, so any root that has the families would work, and the bundle's root keeps the live runes out of these tests' closure."""
    return load_spec(mini_bundle.spec_root)


def test_a_gsub_only_recompile_leaves_every_cache_key_alone(tmp_path, spec, mini_bundle):
    """Appending a GSUB lookup changes nothing the cache stamps: not the family keys, not the helpers digest, and not either whole-store stamp."""
    root = mini_bundle.spec_root
    families, helpers = unit_cache.family_content_keys(root, spec, MINI_FONT)
    rewired = _with_extra_gsub_lookup(MINI_FONT, tmp_path / "rewired.otf")
    families_after, helpers_after = unit_cache.family_content_keys(root, spec, rewired)
    assert helpers == helpers_after
    assert families == families_after
    for stamp in (
        lambda digest: unit_cache.environment_stamp(root, spec, MINI, MINI_FONT, MINI_FONT, digest),
        lambda digest: unit_cache.signature_environment(root, MINI_FONT, digest),
    ):
        assert stamp(helpers) == stamp(helpers_after)


def test_a_widened_family_moves_that_family_key_and_leaves_the_environment(tmp_path, spec, mini_bundle):
    """Widening one family's glyphs changes only family keys that name that family, and leaves unchanged the helpers digest that both whole-store stamps include."""
    root = mini_bundle.spec_root
    families, helpers = unit_cache.family_content_keys(root, spec, MINI_FONT)
    widened = _with_a_widened_family(MINI_FONT, tmp_path / "widened.otf")
    families_after, helpers_after = unit_cache.family_content_keys(root, spec, widened)
    assert helpers == helpers_after
    moved = {name for name in families if families[name] != families_after.get(name)}
    assert moved, "widening a family did not move its content key"
    assert all(MOVED_FAMILY in name.split("_") for name in moved), moved


def test_a_recompiled_font_serves_the_untouched_units_and_lands_on_a_from_scratch_build(
    mini_surface, mini_bundle, tmp_path, capfd
):
    """End to end at mini scale: rebuild the mini surface over a font recompiled the way a rune edit recompiles one. The store must serve the windows the widened family cannot reach (some units, not all and not none), and the tree it writes must be byte-identical to a from-scratch build of the same inputs. The content keys are compared first and separately, because they carry a recorded verdict across the cycle: a served fragment with a wrong key would strand every verdict recorded against it, and `patch_fragment` rewrites a served fragment's scaffold fields without recomputing that key. The base copied here is conftest's `mini_surface`, built over the unmodified `MINI/M1.otf`; only the fonts passed to `_mini_build` are recompiled."""
    incremental = tmp_path / "surface"
    shutil.copytree(mini_surface, incremental)
    recompiled = _recompiled(MINI_FONT, tmp_path / "recompiled.otf")

    capfd.readouterr()
    _mini_build(incremental, recompiled, mini_bundle)
    served, total = _served(capfd)
    assert 0 < served < total, f"served {served} of {total}"

    scratch = tmp_path / "scratch"
    _mini_build(scratch, recompiled, mini_bundle, fresh_unit_cache=True)

    assert _content_keys(incremental) == _content_keys(scratch)
    assert _tree(incremental) == _tree(scratch)


def _with_a_version_bump(source: Path, target: Path) -> Path:
    """Copy the font with `head.fontRevision` and every `name` record changed, as `make all` changes both site fonts on a version bump. This change must move neither whole-store stamp."""
    font = TTFont(str(source))
    head = font["head"]
    head.fontRevision = head.fontRevision + 0.001  # pyright: ignore[reportAttributeAccessIssue]
    for record in font["name"].names:  # pyright: ignore[reportAttributeAccessIssue]
        record.string = record.toUnicode() + " bumped"  # pyright: ignore[reportAttributeAccessIssue]
    font.save(str(target))
    return target


def test_a_version_bump_of_the_before_font_leaves_both_whole_store_stamps(tmp_path, spec, mini_bundle):
    """Both stamps hash the site fonts table by table, leaving out `head` and `name`, so the fonts a version bump's `make all` rewrites leave the unit store and the ink-signature store serving, while widening a glyph advance in the same font changes both stamps. The bumped font's bytes differ from the fixture's, so a digest over the whole file would have changed."""
    root = mini_bundle.spec_root
    _families, helpers = unit_cache.family_content_keys(root, spec, MINI_FONT)
    bumped = _with_a_version_bump(MINI_FONT, tmp_path / "bumped.otf")
    widened = _with_a_widened_family(MINI_FONT, tmp_path / "widened.otf")
    assert bumped.read_bytes() != MINI_FONT.read_bytes()
    for stamp in (
        lambda font: unit_cache.environment_stamp(root, spec, MINI, font, font, helpers),
        lambda font: unit_cache.signature_environment(root, font, helpers),
    ):
        base = stamp(MINI_FONT)
        assert stamp(bumped) == base
        assert stamp(widened) != base
