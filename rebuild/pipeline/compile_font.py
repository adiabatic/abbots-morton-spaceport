"""Compile the M1 mini font (M1-PLAN section 5, Group 3).

`build_mini_font` passes `tools/build_font.build_font` a glyph-data dict that holds only legacy `glyphs:` records, with an empty `glyph_families` so the old IR emitter emits nothing, and passes the hand-built FEA as `senior_fea=`. Quikscript glyphs are keyed `<name>.prop` so the Senior build uses them as its proportional glyphs. `build_font` returns the unsaved TTFont (`output_path=None`), and this module packs it and writes the OTF and the `.fea` sidecar itself.

Packing must happen before the first save. `pack_gsub.pack_font` repacks the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 subtables on the in-memory font; that module's docstring says why. With vote slots on (`kernel_exec.VOTE_SLOTS_DEFAULT`), the unpacked lookup can overflow its own uint16 subtable-offset array (at about 6,400 merged rules). fontTools' overflow resolution cannot fix an overflow of that array, so the unpacked font cannot be saved at all. `pack_font` runs on every build, so conform tests one code path. `readback.verify_font` checks the packed lookup in the written bytes and reports a failure when the subtable-offset headroom is below `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, cast

from rebuild.pipeline import pack_gsub
from rebuild.pipeline.model import GlyphRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

if TYPE_CHECKING:
    from quikscript_ir import GlyphData, GlyphDef

METADATA = {
    "font_name": "AbbotsMortonSpaceportM1",
    "version": 1.0,
    "units_per_em": 550,
    "pixel_size": 50,
    "ascender": 550,
    "descender": -150,
    "cap_height": 400,
    "x_height": 300,
}


def _glyph_data(glyphs: Mapping) -> GlyphData:
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
    return {
        "metadata": dict(METADATA),
        "glyphs": records,
        "glyph_families": {},
        "context_sets": {},
        "kerning": {},
        "senior_kerning": [],
        "restore_isolated_form_overrides": [],
        "predecessor_demote_overrides": [],
        "trailing_demote_overrides": [],
    }


def build_mini_font(glyphs: Mapping, fea: str, out_path: Path) -> Path:
    from build_font import _write_if_changed, build_font

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    glyph_data = _glyph_data(glyphs)
    build_log = io.StringIO()
    with redirect_stdout(build_log):
        font = build_font(glyph_data, None, variant="senior", senior_fea=fea)

    try:
        pack_gsub.pack_font(font)
        buffer = io.BytesIO()
        font.save(buffer)
        _write_if_changed(out_path, buffer.getvalue())
        fea_code = cast(str, getattr(font, "_fea_code"))
        _write_if_changed(out_path.with_suffix(".fea"), (fea_code + "\n").encode())
    finally:
        font.close()

    return out_path
