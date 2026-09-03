"""Mini-font compilation via the prototype's verified read-only recipe (M1-PLAN section 5, Group 3).

`build_mini_font` hands `tools/build_font.build_font` a synthetic glyph-data dict containing only legacy `glyphs:` records (qs-named glyphs keyed `<name>.prop` so the senior variant compiler picks them), an empty `glyph_families` so the old IR emitter never runs, and the hand-built FEA threaded through `senior_fea=`. The build is asked for the unsaved TTFont (`output_path=None`) and this module writes the OTF and `.fea` sidecar itself, because the packing must happen before the first serialization: `pack_gsub.pack_font` repacks the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 groups on the in-memory font (see that module for why; `readback.verify_font` proves the packed lookup over the written bytes), unconditionally on every build so there is one code path for conform to gate — and since the stage-4b vote world the per-rule form can outgrow the Lookup's own uint16 subtable-offset array (≈6,400 merged rules), so saving the unpacked font first is not merely wasteful but impossible (fontTools' overflow resolution has no move for a lookup-level array overflow). The uint16 subtable-offset headroom that packing protects is read off the written bytes by `readback.verify_font`, in the parse that stage already makes, and held to `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`.
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
