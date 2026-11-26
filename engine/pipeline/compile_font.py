"""Compile the rebuild's font (M1-PLAN section 5, Group 3).

`build_font` passes `tools/build_font.build_font` a glyph-data dict of glyph records (`tools/glyph_records.py`) and the rune FEA as `quikscript_fea=`. Quikscript glyphs are keyed `<name>.prop` so the Senior build uses them as its proportional glyphs, and `tools/build_font.py` trims one pixel from the advance of every glyph whose name starts with `qs` or `uniE6` (the Senior tightening). `tools/build_font.build_font` returns the unsaved TTFont and prints nothing, and this module packs it and writes the OTF and the `.fea` sidecar itself. Nothing redirects stdout around the call, because a redirect swaps `sys.stdout` for the whole process: run_build's table-only branch writes its `[t]` and `[phase]` lines from another thread while the font compiles, and `engine/tools/console.py` resolves `sys.stdout` when each line is written.

The non-Quikscript part of the font is optional, so the tests that compile a font from a few runes run the same code path as the build. Without `FontSources` the font holds the rune glyphs, `space` and `uni200C`, named by `METADATA`. With them (`load_font_sources`) it is the whole Senior Regular: the glyph records of every `fingerprint.font_data_paths` file, the angle parentheses and `space` among them, with the rune pipeline's records winning where both name a glyph (the namer dot pair, `space`, `uni200C`); the metadata of glyph_data/metadata.yaml, so the name table is the shipped Senior's; and the Latin `kern`, `ccmp` and `mark` features, which `tools/build_font._assemble_fea` writes ahead of the rune FEA, the order the old build writes them in. A Latin kern rule may not name a Quikscript glyph (`_names_a_rune`), since the runes' `kern:` blocks own every kern between rune glyphs (engine/KERN-PLAN.md section 1), so `load_font_sources` refuses one. `variation_sequences` adds a cmap format 14 subtable (`add_variation_sequences`), from the glyphs `run_build.mint_variation_glyphs` minted. A `bold_path` also writes Bold beside Regular: the overstruck outlines, with Regular's packed GDEF, GSUB and GPOS bytes and its `usMaxContext` (`tools/build_font.CompiledSeniorOTL`), so the two fonts shape alike by construction and the layout is compiled and packed once. Read-back checks both (`readback.verify_bold`).

Packing must happen before the first save. `pack_gsub.pack_font` repacks the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 subtables on the in-memory font; that module's docstring says why. With follower prefer slots on (`kernel_exec.FOLLOWER_PREFER_SLOTS_DEFAULT`), the unpacked lookup can overflow its own uint16 subtable-offset array (at about 6,400 merged rules). fontTools' overflow resolution cannot fix an overflow of that array, so the unpacked font cannot be saved at all. `pack_font` runs on every build, so conform tests one code path. `readback.verify_font` checks the packed lookup in the written bytes and reports a failure when the subtable-offset headroom is below `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR` or the largest packed group passes `readback.GROUP_RULE_BYTES_CEILING`.

Between the pack and the save, `build_font` measures every format-2 chained-context subtable in GSUB (`pack_gsub.largest_group`) and raises `GroupTooLargeError` when one holds more than `GROUP_RULE_BYTES_LIMIT` bytes of rule tables, so a build that jumps past read-back's ceiling in one edit fails in seconds. fontTools serializes GSUB through hb.repack, which can split GSUB type-4 subtables and GPOS PairPos and MarkBasePos subtables but not a chained-context one. Past the limit hb.repack raises `RepackerError` and fontTools falls back to its own serializer, whose `splitTable` has no entry for GSUB types 6 and 7 either: it marks one subtable or promotes one lookup to Extension per pass and recompiles the whole GSUB each time, for tens of minutes on the settlement lookup, before it raises `OTLOffsetOverflowError`. fontTools' `USE_HARFBUZZ_REPACKER` option cannot cut that short, because `tryPackingHarfbuzz` falls back whatever the option says. The limit sits under the measured boundary: with harfbuzz 12.3.2 as uharfbuzz 0.53.3 bundles it, one group added to the settlement lookup serialized at 64,800 bytes of rules, and groups of 65,880 bytes and more raised `RepackerError`. How hb.repack lays out the rest of the table can move that boundary somewhat, which read-back's ceiling leaves room for.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, cast

from engine.pipeline import fingerprint, pack_gsub
from engine.pipeline.model import GlyphRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

if TYPE_CHECKING:
    from glyph_records import GlyphData, GlyphDef

GROUP_RULE_BYTES_LIMIT = 64_000
LAYOUT_TABLES = ("GDEF", "GSUB", "GPOS")
# The selectors of a Latin kern rule that name glyphs or glyph families; a rule with a `qs` name in one of them kerns a rune glyph by name (`_names_a_rune`).
KERN_NAME_SELECTORS = ("left", "right", "left_family", "right_family", "left_stance", "right_stance")

METADATA = {
    "font_name": "AbbotsMortonSpaceportSenior",
    "version": 1.0,
    "units_per_em": 550,
    "pixel_size": 50,
    "ascender": 550,
    "descender": -150,
    "cap_height": 400,
    "x_height": 300,
}


class GroupTooLargeError(Exception):
    pass


class FontSourceError(Exception):
    pass


@dataclass(frozen=True)
class FontSources:
    """The non-Quikscript part of the full font, as `load_font_sources` reads it: the metadata, the glyph records keyed as the YAML keys them (`<name>.prop` for a proportional record), and the Latin kern rules."""

    metadata: Mapping[str, Any]
    glyphs: Mapping[str, Any]
    kerning: Mapping[str, Any]
    postscript_names: Mapping[str, int] = field(default_factory=dict)

    def compiled_names(self) -> dict[str, str]:
        """Each record's glyph name in the proportional font, keyed by its YAML key: a `.prop` record takes its base name, and a plain record whose name a `.prop` record also takes is left out, as `glyph_records.prepare_proportional_glyphs` does."""
        from glyph_records import get_base_glyph_name, is_proportional_glyph

        proportional = {get_base_glyph_name(key) for key in self.glyphs if is_proportional_glyph(key)}
        names: dict[str, str] = {}
        for key in self.glyphs:
            if is_proportional_glyph(key):
                names[key] = get_base_glyph_name(key)
            elif key not in proportional:
                names[key] = key
        return names

    def midword_glyphs(self) -> tuple[str, ...]:
        """The glyphs after which a `·` is a mid-word middot and not a namer dot: every glyph whose code point is an ASCII or Latin letter or digit (`tools/build_font._is_orthodox_letter_or_digit`), sorted. `emit_gsub`'s namer-dot stage reads them as its mid-word exception."""
        from build_font import _is_orthodox_letter_or_digit, _resolve_codepoint

        postscript = dict(self.postscript_names)
        return tuple(
            sorted(
                name
                for name in set(self.compiled_names().values())
                if _is_orthodox_letter_or_digit(_resolve_codepoint(name, postscript))
            )
        )


def _names_a_rune(rule: Mapping[str, Any]) -> bool:
    """Whether a Latin kern rule names a Quikscript glyph or family in one of `KERN_NAME_SELECTORS`, as `{left_family: [qsHe], …}` would name `qsHe`. A rule with no left side (`leave-space-for-j`) names none, though it kerns every glyph before its right side."""
    return any(str(name).startswith("qs") for key in KERN_NAME_SELECTORS for name in rule.get(key) or ())


def load_font_sources(root: Path = REPO_ROOT) -> FontSources:
    """Read the non-Quikscript part of the full font from `fingerprint.font_data_paths` in its order, merged as `tools/build_font.load_glyph_data` merges a directory. Raises `FontSourceError` when a file carries a top-level key no build reads (`load_glyph_data` refuses it) or a Latin kern rule names a rune glyph (`_names_a_rune`)."""
    from build_font import load_glyph_data, load_postscript_glyph_names

    metadata: dict[str, Any] = {}
    glyphs: dict[str, Any] = {}
    kerning: dict[str, Any] = {}
    *glyph_files, _postscript = fingerprint.font_data_paths(root)
    for path in glyph_files:
        try:
            data = load_glyph_data(path)
        except ValueError as error:
            raise FontSourceError(str(error)) from error
        if data.get("metadata"):
            metadata = dict(data["metadata"])
        glyphs.update(data.get("glyphs") or {})
        kerning.update(data.get("kerning") or {})
    naming = sorted(tag for tag, rule in kerning.items() if _names_a_rune(rule))
    if naming:
        raise FontSourceError(
            f"the Latin kern rules {', '.join(naming)} name a Quikscript glyph; the runes' `kern:` blocks own every kern between rune glyphs"
        )
    return FontSources(
        metadata=metadata, glyphs=glyphs, kerning=kerning, postscript_names=load_postscript_glyph_names()
    )


def _check_group_sizes(font: Any) -> None:
    """Raise `GroupTooLargeError` when a format-2 chained-context subtable of the in-memory GSUB holds more than `GROUP_RULE_BYTES_LIMIT` bytes of rule tables, the size hb.repack cannot serialize in one subtable."""
    if "GSUB" not in font:
        return
    for index, lookup in enumerate(font["GSUB"].table.LookupList.Lookup):
        size, subtable = pack_gsub.largest_group(lookup)
        if size > GROUP_RULE_BYTES_LIMIT:
            raise GroupTooLargeError(
                f"GSUB lookup {index} subtable {subtable} holds {size:,} bytes of format-2 rules, over the {GROUP_RULE_BYTES_LIMIT:,} bytes hb.repack can serialize in one chained-context subtable"
            )


def _rune_records(glyphs: Mapping) -> dict[str, GlyphDef | None]:
    records: dict[str, GlyphDef | None] = {}
    for record in glyphs.values():
        assert isinstance(record, GlyphRecord)
        key = f"{record.name}.prop" if record.name.startswith("qs") else record.name
        definition: GlyphDef = {}
        if record.bitmap:
            definition["bitmap"] = list(record.bitmap)
        if record.y_offset:
            definition["y_offset"] = record.y_offset
        if record.advance_width is not None:
            definition["advance_width"] = record.advance_width
            if not record.bitmap:
                definition["bitmap"] = []
        records[key] = definition
    if "space" not in records:
        records["space"] = {"bitmap": [], "advance_width": 7}
    if "uni200C" not in records:
        records["uni200C"] = {"bitmap": [], "advance_width": 0}
    return records


def _glyph_data(glyphs: Mapping, sources: FontSources | None = None) -> GlyphData:
    records = _rune_records(glyphs)
    metadata: dict[str, Any] = dict(METADATA)
    kerning: dict[str, Any] = {}
    if sources is not None:
        metadata = dict(sources.metadata)
        kerning = dict(sources.kerning)
        supplied = {key.removesuffix(".prop") for key in records}
        compiled = sources.compiled_names()
        latin = {key: sources.glyphs[key] for key, name in compiled.items() if name not in supplied}
        records = {**latin, **records}
    return {"metadata": metadata, "glyphs": records, "kerning": kerning}


def _cmap14(variation_sequences: Mapping[int, Mapping[int, str]]) -> Any:
    """The cmap format 14 subtable mapping each (base code point, selector) pair to its glyph, in code point order, or None when there is no sequence."""
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_14

    uvs = {
        selector: sorted(mapping.items())
        for selector, mapping in sorted(variation_sequences.items())
        if mapping
    }
    if not uvs:
        return None
    subtable = cmap_format_14(14)
    subtable.platformID = 0  # pyright: ignore[reportAttributeAccessIssue]
    subtable.platEncID = 5  # pyright: ignore[reportAttributeAccessIssue]
    subtable.language = 0
    subtable.cmap = {}
    subtable.uvsDict = uvs
    return subtable


def add_variation_sequences(font: Any, variation_sequences: Mapping[int, Mapping[int, str]] | None) -> None:
    """Append the cmap format 14 subtable of `variation_sequences` (selector to base code point to glyph name) to the font's cmap, when there is a sequence. `build_family` adds Mono's and Junior's sequences through it too."""
    subtable = _cmap14(variation_sequences or {})
    if subtable is not None:
        font["cmap"].tables.append(subtable)


def _saved(font: Any) -> bytes:
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def _layout_of(data: bytes) -> Any:
    """The packed layout tables of a saved font, as the bytes it holds them in, and its `usMaxContext`, in the form `tools/build_font.build_font(senior_otl=…)` writes into another font."""
    from build_font import CompiledSeniorOTL
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data), lazy=True)
    try:
        reader = cast(Any, font.reader)
        tables = {tag: reader[tag] for tag in LAYOUT_TABLES if tag in reader}
        return CompiledSeniorOTL(
            tables, font["OS/2"].usMaxContext
        )  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        font.close()


def build_font(
    glyphs: Mapping,
    fea: str,
    out_path: Path,
    sources: FontSources | None = None,
    variation_sequences: Mapping[int, Mapping[int, str]] | None = None,
    bold_path: Path | None = None,
    fea_path: Path | None = None,
) -> Path:
    """Compile `glyphs` and the rune FEA `fea` into the OTF at `out_path` and its FEA sidecar at `fea_path` (by default `out_path` with a `.fea` suffix), and Bold at `bold_path` when one is given, and return `out_path`. With `sources` the font is the whole Senior (the module docstring says what that adds); without them it holds the rune glyphs alone. `variation_sequences` maps each selector to its base code points' glyphs."""
    import build_font as builder
    from glyph_records import CompiledGlyphSet, compile_glyph_records

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    glyph_data = _glyph_data(glyphs, sources)
    compiled = CompiledGlyphSet(compile_glyph_records(glyph_data, "senior"))
    font = builder.build_font(glyph_data, variant="senior", quikscript_fea=fea, compiled=compiled)

    try:
        add_variation_sequences(font, variation_sequences)
        pack_gsub.pack_font(font)
        _check_group_sizes(font)
        regular = _saved(font)
        builder._write_if_changed(out_path, regular)
        fea_code = cast(str, getattr(font, "_fea_code"))
        builder._write_if_changed(fea_path or out_path.with_suffix(".fea"), (fea_code + "\n").encode())
    finally:
        font.close()

    if bold_path is not None:
        bold = builder.build_font(
            glyph_data,
            variant="senior",
            bold=True,
            quikscript_fea=fea,
            compiled=compiled,
            senior_otl=_layout_of(regular),
        )
        try:
            add_variation_sequences(bold, variation_sequences)
            builder._write_if_changed(Path(bold_path), _saved(bold))
        finally:
            bold.close()

    return out_path
