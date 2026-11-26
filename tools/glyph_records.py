"""The glyph records `tools/build_font.py` builds a font from, and the variant's glyph set it compiles them into.

A record (`GlyphDef`) is one glyph's YAML mapping: its `bitmap` rows, `y_offset`, `advance_width`, a composite's `base`, `top` and `bottom`, and the mark and kerning keys. `GlyphData` is a whole font's input, as `build_font.load_glyph_data` reads the glyph files: the metadata, the records keyed by glyph name, and the Latin kern rules. The rebuild hands its own records in through the same shape (engine/pipeline/compile_font.py, engine/pipeline/build_family.py).

A record whose name carries `.prop` (`is_proportional_glyph`) is the proportional fonts' drawing of the glyph its base name names (`get_base_glyph_name`). `compile_glyph_records` gives a variant its records: Mono takes them as written, and Junior and Senior take each `.prop` record under its base name in place of the plain record of that name, with every composite's `base` renamed to match. `CompiledGlyphSet` holds the result for a build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

GlyphDef = dict[str, Any]


class GlyphData(TypedDict):
    metadata: dict[str, Any]
    glyphs: dict[str, GlyphDef | None]
    kerning: dict[str, Any]


def is_proportional_glyph(glyph_name: str) -> bool:
    return glyph_name.endswith(".prop") or ".prop." in glyph_name


def get_base_glyph_name(prop_glyph_name: str) -> str:
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    if ".prop." in prop_glyph_name:
        return prop_glyph_name.replace(".prop.", ".", 1)
    return prop_glyph_name


def prepare_proportional_glyphs(glyphs_def: dict[str, GlyphDef | None]) -> dict[str, GlyphDef | None]:
    """Return the proportional fonts' records: each `.prop` record under its base name, every other record whose name no `.prop` record takes, and each composite's `base` renamed to the name its base record now has."""
    rename_map = {name: get_base_glyph_name(name) for name in glyphs_def if is_proportional_glyph(name)}
    taken = set(rename_map.values())

    def _renamed(glyph_def: GlyphDef | None) -> GlyphDef | None:
        if not glyph_def or glyph_def.get("base") not in rename_map:
            return glyph_def
        return {**glyph_def, "base": rename_map[glyph_def["base"]]}

    prepared: dict[str, GlyphDef | None] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_name in rename_map:
            prepared[rename_map[glyph_name]] = _renamed(glyph_def)
        elif glyph_name not in taken:
            prepared[glyph_name] = _renamed(glyph_def)
    return prepared


def compile_glyph_records(glyph_data: GlyphData, variant: str) -> dict[str, GlyphDef | None]:
    """Return the records `variant` (`mono`, `junior` or `senior`) is built from, as the module docstring describes."""
    records = {
        name: (dict(glyph_def) if glyph_def is not None else None)
        for name, glyph_def in glyph_data.get("glyphs", {}).items()
    }
    if variant != "mono":
        records = prepare_proportional_glyphs(records)
    return records


@dataclass(slots=True)
class CompiledGlyphSet:
    """A variant's records (`compile_glyph_records`), with `glyph_definitions`, the ones that hold a mapping, which the build draws."""

    records: dict[str, GlyphDef | None]

    @property
    def glyph_definitions(self) -> dict[str, GlyphDef]:
        return {name: glyph_def for name, glyph_def in self.records.items() if glyph_def is not None}
