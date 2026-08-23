"""The surface cache against the move it exists for: a recompiled after font.

Issue 20's per-unit cache had never served a unit in any recorded build, and rebuild/test_unit_cache.py was green throughout, because every case it perturbs is an *audit* edit — the one direction the per-unit content keys were already built for. What actually happens on a rune-edit cycle is that the after font is recompiled: its GSUB lookup list moves whether or not any window's shaping does, and the edited letter's compiled glyphs move with it. Both whole-store stamps folded the GSUB wiring in, so the store was discarded wholesale every time.

So this module perturbs the font. The edited font here does both things at once — it appends a GSUB lookup no feature references, and it widens every glyph of one family — because that is the shape of a real cycle, and the two halves must land differently: the wiring must not reach the stamp at all, and the family must invalidate exactly the windows that can reach it. The bundle under rebuild/review/fixtures/mini/ is what makes this a contracts-lane test; see its regenerate.py for what it holds and when it needs remaking.
"""

import copy
import re
import shutil
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from rebuild.review import unit_cache
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
        jobs=1,
        **kwargs,
    )


def _with_extra_gsub_lookup(source: Path, target: Path) -> Path:
    """The font again, plus one GSUB lookup nothing references. It moves the wiring digest — the lookup-type list grows — and cannot move a single shaped run, which is exactly the perturbation a rune edit hands the cache for free."""
    font = TTFont(str(source))
    lookups = font["GSUB"].table.LookupList  # pyright: ignore[reportAttributeAccessIssue]
    lookups.Lookup.append(copy.deepcopy(lookups.Lookup[0]))
    lookups.LookupCount = len(lookups.Lookup)
    font.save(str(target))
    return target


def _with_a_widened_family(source: Path, target: Path) -> Path:
    """The font again, with every glyph of one family advancing wider. The family's compiled-glyph digest moves, so every window that can reach that letter must be recomputed — and every window that cannot must still be served."""
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
    """Both halves at once: the shape of a real cycle's after font."""
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
        for unit in json.loads((surface / meta["shard"]).read_text(encoding="utf-8")):
            keys[unit["codepoints"]] = unit["content_key"]
    return keys


def _served(capfd) -> tuple[int, int]:
    match = re.search(r"served (\d+) of (\d+) units from cache", capfd.readouterr().err)
    assert match, "the build did not report its cache plan"
    return int(match.group(1)), int(match.group(2))


@pytest.fixture(scope="module")
def spec():
    return load_spec(REPO_ROOT)


@pytest.fixture(scope="module")
def base_surface(tmp_path_factory, mini_bundle):
    out = tmp_path_factory.mktemp("environment-base") / "surface"
    _mini_build(out, MINI_FONT, mini_bundle)
    return out


def test_a_gsub_only_recompile_leaves_every_cache_key_alone(tmp_path, spec):
    """The finding this module exists for, stated at the grain it is decided at: appending a GSUB lookup moves nothing the cache stamps. Before this, it moved `after_helpers`, which both whole-store stamps carry, so the whole store went."""
    families, helpers = unit_cache.family_content_keys(REPO_ROOT, spec, MINI_FONT)
    rewired = _with_extra_gsub_lookup(MINI_FONT, tmp_path / "rewired.otf")
    families_after, helpers_after = unit_cache.family_content_keys(REPO_ROOT, spec, rewired)
    assert helpers == helpers_after
    assert families == families_after
    for stamp in (
        lambda digest: unit_cache.environment_stamp(REPO_ROOT, spec, MINI, MINI_FONT, MINI_FONT, digest),
        lambda digest: unit_cache.signature_environment(REPO_ROOT, MINI_FONT, digest),
    ):
        assert stamp(helpers) == stamp(helpers_after)


def test_a_widened_family_moves_that_family_key_and_leaves_the_environment(tmp_path, spec):
    """The other half of a recompile, which must land the opposite way: a family whose compiled glyphs moved invalidates at per-unit grain through its own key, and touches no whole-store stamp."""
    families, helpers = unit_cache.family_content_keys(REPO_ROOT, spec, MINI_FONT)
    widened = _with_a_widened_family(MINI_FONT, tmp_path / "widened.otf")
    families_after, helpers_after = unit_cache.family_content_keys(REPO_ROOT, spec, widened)
    assert helpers == helpers_after
    moved = {name for name in families if families[name] != families_after.get(name)}
    assert moved, "widening a family did not move its content key"
    assert all(MOVED_FAMILY in name.split("_") for name in moved), moved


def test_a_recompiled_font_serves_the_untouched_units_and_lands_on_a_from_scratch_build(
    base_surface, mini_bundle, tmp_path, capfd
):
    """The end-to-end claim, at the only scale a test can afford: rebuild the mini surface over a font recompiled the way a rune edit recompiles one, and the store must serve the windows the moved family cannot reach — some, not all, and not none — while the tree it writes stays byte-for-byte what a cache-blind build of the same inputs writes. The content keys are asserted first and on their own, because they are what carry a recorded verdict across the cycle: a served fragment whose key drifted would strand every verdict recorded against it, and `patch_cached_fragment` re-stamps twelve fields over a served fragment without recomputing that key."""
    incremental = tmp_path / "surface"
    shutil.copytree(base_surface, incremental)
    recompiled = _recompiled(MINI_FONT, tmp_path / "recompiled.otf")

    capfd.readouterr()
    _mini_build(incremental, recompiled, mini_bundle)
    served, total = _served(capfd)
    assert 0 < served < total, f"served {served} of {total}"

    scratch = tmp_path / "scratch"
    _mini_build(scratch, recompiled, mini_bundle, fresh_unit_cache=True)

    assert _content_keys(incremental) == _content_keys(scratch)
    assert _tree(incremental) == _tree(scratch)
