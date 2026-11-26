"""Build Mono and Junior, Regular and Bold, from the rune files and the non-Quikscript glyph files (`build_mono_and_junior`). The command that builds the six shipped fonts into one directory, rebuild/tools/build_fonts.py (`make rebuild-fonts`), builds these two through it and takes Senior from the rebuild's own build.

Mono and Junior read the spec (`load_family_spec`), the font's non-Quikscript part (`compile_font.load_font_sources`) and, for Mono, the Departure Mono glyphs and layout tables `tools/build_font.py` copies in, and nothing else: no settlement table, so they build while the tables are stale or absent, and nothing of the old engine's family and stance compile, Senior emitter or join analysis. `family_glyphs` turns the runes into legacy `glyphs:` records beside the non-Quikscript ones, and `tools/build_font.build_font` compiles them from a glyph set that holds only those records. A record keyed by a bare glyph name is a Mono drawing and one keyed `<name>.prop` a Junior drawing, as in the glyph files, so Junior takes a bare record only where no `.prop` record has its name.

- Each letter's Mono drawing is its rune's `mono:` block, and its Junior drawing its isolated cell (`geometry.isolated_cell`), drawn as `run_m1.mint_raw_glyphs` draws Senior's raw cmap glyph. A ligature forms only in Senior, so neither font holds a ligature glyph, nor any glyph no text reaches.
- A variation sequence reaches the fonts its registry record's `fonts:` names (`model.ScriptRegistry.variation_sequences`) as a cmap format 14 entry (`compile_font.add_variation_sequences`). Its Junior glyph is drawn and named as Senior's (`surface.variation_plan`, `settle.cell_label`), and its Mono glyph, under the same name, is the stance's base bitmap, which must be `MONO_COLUMNS` wide.
- A letter whose unjoined cell a cosmetic set (`overlay: cosmetic`, ss06) redraws in Senior has its cosmetic copy in Junior, under the name Senior gives the copy of that cell (`model.cosmetic_copy_name`).

The registry decides each font's features. Mono carries none of its own, since `tools/build_font.py` copies Departure Mono's layout tables into it. Junior carries the registry's overlay sets (`junior_fea`), the cosmetic sets first and the isolated overlay (ss10) after them so that it wins: a cosmetic set substitutes each letter by its copy, and the isolated overlay every Junior glyph that is not a bare letter by its letter. Then the namer dot lowers before a Junior glyph of a rune in the `shorts` predicate class, except before a ZWNJ and after a mid-word glyph (`compile_font.FontSources.midword_glyphs`), by the rule shape Senior's emitter writes (`emit_gsub.namer_dot_lookup`). `tools/build_font.build_font` takes that feature code after `mark` (`quikscript_fea=`).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from rebuild.pipeline import compile_font, emit_gsub, geometry, spec_load, surface
from rebuild.pipeline.model import (
    COSMETIC_OVERLAY,
    CellId,
    GlyphRecord,
    ResolvedSpec,
    cosmetic_copy_name,
    cosmetic_features,
    cosmetic_record,
)
from rebuild.pipeline.settle import cell_label

REPO_ROOT = compile_font.REPO_ROOT
FILE_PREFIXES = {
    "mono": "AbbotsMortonSpaceportMono",
    "junior": "AbbotsMortonSpaceportSansJunior",
    "senior": "AbbotsMortonSpaceportSansSenior",
}
STYLES = (("Regular", False), ("Bold", True))
MONO_COLUMNS = 5
ISOLATED_OVERLAY = "isolated"
SHORTS_CLASS = "shorts"
NAMER_DOT = "periodcentered"
NAMER_DOT_LOWERED = "periodcentered.lowered"


class FamilyBuildError(Exception):
    pass


def font_file_name(font: str, style: str) -> str:
    """The shipped file name of one font (`mono`, `junior` or `senior`) and style, `AbbotsMortonSpaceportSansJunior-Bold.otf` for Junior Bold."""
    return f"{FILE_PREFIXES[font]}-{style}.otf"


def load_family_spec(root: Path = REPO_ROOT) -> ResolvedSpec:
    """Load the rune files, the registry and the schemas under `root`. It refuses while the alphabet switch (`spec_load.M1_RUNES_ENV`) is set, since the shipped fonts hold every rune."""
    if spec_load.m1_runes() is not None:
        raise FamilyBuildError(
            f"the alphabet switch ({spec_load.M1_RUNES_ENV}) narrows the spec, and the shipped fonts hold every rune"
        )
    root = Path(root)
    return spec_load.load_spec(
        root / spec_load.DEFAULT_RUNES_DIR.relative_to(spec_load.REPO_ROOT),
        root / spec_load.DEFAULT_REGISTRY_PATH.relative_to(spec_load.REPO_ROOT),
        root / spec_load.DEFAULT_SCHEMA_DIR.relative_to(spec_load.REPO_ROOT),
    )


@dataclass(frozen=True)
class FamilyGlyphs:
    """What `family_glyphs` makes of the runes for Mono and Junior: the legacy glyph records (`records`, keyed as the module docstring says), the letter each Junior Quikscript glyph draws (`letters`, where a bare letter names itself), each cosmetic set's substitutions from letter to copy (`cosmetic`), and each font's variation sequences from selector to base code point to glyph (`sequences`)."""

    records: Mapping[str, Mapping[str, Any]]
    letters: Mapping[str, str]
    cosmetic: Mapping[str, Mapping[str, str]]
    sequences: Mapping[str, Mapping[int, Mapping[int, str]]]


def _legacy_record(rows: Iterable[str], y_offset: int) -> dict[str, Any]:
    record: dict[str, Any] = {"bitmap": list(rows)}
    if y_offset:
        record["y_offset"] = y_offset
    return record


def _drawn(record: GlyphRecord) -> dict[str, Any]:
    """A realized cell's legacy record: its bitmap and `y_offset`, without the anchors, which neither Mono nor Junior carries."""
    return _legacy_record(record.bitmap, record.y_offset)


def _isolated(spec: ResolvedSpec, name: str) -> dict[str, Any]:
    cell = geometry.isolated_cell(spec, name)
    return _drawn(geometry.realize(spec, surface.resolve_cell(spec, cell), name=name))


def family_glyphs(spec: ResolvedSpec) -> FamilyGlyphs:
    """Turn the runes into Mono's and Junior's Quikscript glyphs, as the module docstring describes. Raises `FamilyBuildError` for a letter without a `mono:` drawing, for an alternate whose base bitmap Mono cannot draw in `MONO_COLUMNS`, and for a selector that reaches Mono but not Junior, whose Mono record Junior would take."""
    records: dict[str, dict[str, Any]] = {}
    letters: dict[str, str] = {}
    cosmetic: dict[str, dict[str, str]] = {}
    every_cosmetic = cosmetic_features(spec)
    for name, rune in spec.runes.items():
        if rune.sequence or rune.codepoint is None:
            continue
        if rune.mono is None:
            raise FamilyBuildError(f"{name} has no `mono:` drawing for Mono")
        records[name] = _legacy_record(rune.mono.rows, rune.mono.y_offset)
        records[f"{name}.prop"] = _isolated(spec, name)
        letters[name] = name
        unjoined = CellId(name, rune.default_stance, None, None, ())
        redrawn = cosmetic_record(spec, unjoined, every_cosmetic)
        if redrawn is not None:
            copy = cosmetic_copy_name(cell_label(spec, unjoined), redrawn.feature)
            records[f"{copy}.prop"] = _legacy_record(redrawn.bitmap.rows, redrawn.bitmap.y_offset)
            cosmetic.setdefault(redrawn.feature, {})[name] = copy
            letters[copy] = name

    sequences: dict[str, dict[int, dict[int, str]]] = {"mono": {}, "junior": {}}
    for selector, entry in sorted(spec.registry.variation_sequences.items()):
        fonts = [font for font in sequences if font in entry.fonts]
        if not fonts:
            continue
        if "junior" not in fonts:
            raise FamilyBuildError(
                f"U+{selector:04X} reaches Mono but not Junior, and Junior would take its Mono glyphs as its own"
            )
        for rune_name, stance in entry.runes.items():
            rune = spec.runes[rune_name]
            assert rune.codepoint is not None, rune_name
            cell = CellId(rune_name, stance, None, None, ())
            glyph = cell_label(spec, cell)
            if "mono" in fonts:
                bitmap = rune.stances[stance].bitmap
                widths = sorted({len(row) for row in bitmap.rows})
                if widths != [MONO_COLUMNS]:
                    raise FamilyBuildError(
                        f"U+{selector:04X} selects {rune_name}'s {stance!r} in Mono, which draws it from the stance's base bitmap, but its rows are {', '.join(map(str, widths))} columns wide, not {MONO_COLUMNS}"
                    )
                records[glyph] = _legacy_record(bitmap.rows, bitmap.y_offset)
            plan, _drawing = surface.variation_plan(spec, cell)
            records[f"{glyph}.prop"] = _drawn(geometry.realize(spec, plan, name=glyph))
            letters[glyph] = rune_name
            for font in fonts:
                sequences[font].setdefault(selector, {})[rune.codepoint] = glyph
    return FamilyGlyphs(records, letters, cosmetic, sequences)


def _single_feature(tag: str, pairs: Mapping[str, str]) -> str | None:
    if not pairs:
        return None
    lines = [f"feature {tag} {{"]
    lines.extend(f"    sub {source} by {target};" for source, target in sorted(pairs.items()))
    lines.append(f"}} {tag};")
    return "\n".join(lines)


def junior_fea(spec: ResolvedSpec, family: FamilyGlyphs, midword: Iterable[str]) -> str:
    """Junior's Quikscript feature code (the module docstring gives its parts and their order). `midword` names the glyphs after which a `·` is a mid-word middot and keeps its height."""
    features = spec.registry.features
    blocks = [
        _single_feature(tag, family.cosmetic.get(tag, {}))
        for tag in sorted(tag for tag, info in features.items() if info.overlay == COSMETIC_OVERLAY)
    ]
    isolated = {glyph: letter for glyph, letter in family.letters.items() if glyph != letter}
    blocks += [
        _single_feature(tag, isolated)
        for tag in sorted(tag for tag, info in features.items() if info.overlay == ISOLATED_OVERLAY)
    ]
    shorts = spec.registry.predicate_classes.get(SHORTS_CLASS, frozenset())
    followers = [glyph for glyph, letter in family.letters.items() if letter in shorts]
    stylistic = "\n".join(block for block in blocks if block)
    namer_dot = _namer_dot_fea(followers, midword)
    return "\n\n".join(part for part in (stylistic, namer_dot) if part)


def _namer_dot_fea(followers: Iterable[str], midword: Iterable[str]) -> str | None:
    """Junior's namer-dot `calt`: Senior's lookup (`emit_gsub.namer_dot_lookup`) under names without Senior's `m1_` prefix, as Junior's one `calt` lookup, or None without a follower."""
    followers = list(followers)
    if not followers:
        return None
    lookup = emit_gsub.namer_dot_lookup(NAMER_DOT, NAMER_DOT_LOWERED, followers, midword, prefix="")
    return "\n".join(lookup.lines) + f"\nfeature calt {{\n    lookup {lookup.name};\n}} calt;"


def _glyph_data(sources: compile_font.FontSources, family: FamilyGlyphs) -> dict[str, Any]:
    """The glyph-data dict `tools/build_font.build_font` reads: the non-Quikscript records and the runes' beside them, the metadata without its variation sequences, which come from the registry, and the Latin kern rules."""
    return {
        "metadata": {key: value for key, value in sources.metadata.items() if key != "variation_sequences"},
        "glyphs": {**sources.glyphs, **family.records},
        "glyph_families": {},
        "context_sets": {},
        "kerning": dict(sources.kerning),
        "senior_kerning": [],
        "restore_isolated_form_overrides": [],
        "predecessor_demote_overrides": [],
        "trailing_demote_overrides": [],
    }


def _saved(builder: Any, font: Any, variant: str) -> bytes:
    buffer = io.BytesIO()
    if variant == "mono":
        with builder._quiet_dm_coverage_sort_warning():
            font.save(buffer)
    else:
        font.save(buffer)
    return buffer.getvalue()


def build_mono_and_junior(
    out_dir: Path, fonts: Sequence[str] = ("mono", "junior"), root: Path = REPO_ROOT
) -> list[Path]:
    """Build the fonts `fonts` names of Mono and Junior, Regular and Bold, from the runes and glyph files under `root` into `out_dir` under their shipped file names, with Junior's `.fea` sidecar beside it, and return the font paths. A font whose bytes are already on disk is left alone (`tools/build_font._write_if_changed`)."""
    import build_font as builder
    from glyph_compiler import CompiledGlyphSet, _compile_legacy_glyphs

    if not fonts:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = load_family_spec(root)
    sources = compile_font.load_font_sources(root)
    family = family_glyphs(spec)
    glyph_data = _glyph_data(sources, family)
    junior_code = junior_fea(spec, family, sources.midword_glyphs())
    written: list[Path] = []
    for font in fonts:
        compiled = CompiledGlyphSet(
            legacy_glyphs=_compile_legacy_glyphs(cast(Any, glyph_data), font), join_glyphs={}, glyph_meta={}
        )
        for style, bold in STYLES:
            path = out_dir / font_file_name(font, style)
            built = builder.build_font(
                cast(Any, glyph_data),
                None,
                variant=font,
                bold=bold,
                quikscript_fea=junior_code if font == "junior" else None,
                compiled=compiled,
                quiet=True,
            )
            try:
                compile_font.add_variation_sequences(built, family.sequences[font])
                builder._write_if_changed(path, _saved(builder, built, font))
                fea_code = getattr(built, "_fea_code", None)
                if fea_code is not None:
                    builder._write_if_changed(path.with_suffix(".fea"), (fea_code + "\n").encode())
            finally:
                built.close()
            written.append(path)
    return written
