"""Mini-font compilation via the prototype's verified read-only recipe, plus the budget gate (M1-PLAN section 5, Group 3).

`build_mini_font` hands `tools/build_font.build_font` a synthetic glyph-data dict containing only legacy `glyphs:` records (qs-named glyphs keyed `<name>.prop` so the senior variant compiler picks them), an empty `glyph_families` so the old IR emitter never runs, and the hand-built FEA threaded through `senior_fea=`. The build is asked for the unsaved TTFont (`output_path=None`) and this module writes the OTF and `.fea` sidecar itself, because the packing must happen before the first serialization: `pack_gsub.pack_font` repacks the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 groups on the in-memory font (see that module for why and for the built-in round-trip proof), unconditionally on every build so there is one code path for conform to gate — and since the stage-4b vote world the per-rule form can outgrow the Lookup's own uint16 subtable-offset array (≈6,400 merged rules), so saving the unpacked font first is not merely wasteful but impossible (fontTools' overflow resolution has no move for a lookup-level array overflow). The budget gate then runs `_report_gsub_budget` plus a direct table parse and writes `budget.json` next to the font — its numbers describe the packed reality, with the packing stats under `measured.packing`: it FAILS (BudgetError) when per-rule format-3 chained-context subtables remain and the uint16 subtable-offset headroom is below the 16,384-byte floor (the outcome-partition consequence, prototype follow-up 1).
"""

from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, cast

from rebuild.pipeline import pack_gsub
from rebuild.pipeline.model import GlyphRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

if TYPE_CHECKING:
    from quikscript_ir import GlyphData, GlyphDef

K2_HEADROOM_FLOOR = 16_384

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


class BudgetError(Exception):
    pass


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


def _parse_budget_report(report: str) -> dict:
    parsed: dict = {}
    headroom = re.search(r"LookupList ([\d,]+) bytes, subtable ([\d,]+) bytes in lookup (\d+)", report)
    if headroom:
        parsed["lookuplist_offset_headroom"] = int(headroom.group(1).replace(",", ""))
        parsed["subtable_offset_headroom"] = int(headroom.group(2).replace(",", ""))
        parsed["tightest_lookup_index"] = int(headroom.group(3))
    return parsed


def _table_metrics(font_path: Path) -> dict:
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables.otBase import OTTableWriter

    font = TTFont(str(font_path))
    try:
        reader = cast(Any, font.reader)
        gsub_bytes = reader.tables["GSUB"].length
        lookups = font["GSUB"].table.LookupList.Lookup
        extension_lookups: list[int] = []
        format3_subtables = 0
        settle_index = None
        settle_subtables = 0
        for index, lookup in enumerate(lookups):
            lookup_type = lookup.LookupType
            subtables = list(lookup.SubTable)
            if lookup_type == 7:
                extension_lookups.append(index)
                subtables = [st.ExtSubTable for st in subtables]
                lookup_type = subtables[0].LookupType if subtables else 7
            if lookup_type == 6:
                format3_subtables += sum(1 for st in subtables if getattr(st, "Format", None) == 3)
                if lookup.SubTableCount > settle_subtables:
                    settle_index = index
                    settle_subtables = lookup.SubTableCount
        settle_bytes = None
        if settle_index is not None:
            writer = OTTableWriter()
            lookups[settle_index].compile(writer, font)
            settle_bytes = len(writer.getAllData())
        return {
            "gsub_bytes": gsub_bytes,
            "lookup_count": len(lookups),
            "subtable_count": sum(lookup.SubTableCount for lookup in lookups),
            "settle_lookup_index": settle_index,
            "settle_lookup_subtables": settle_subtables,
            "settle_lookup_bytes": settle_bytes,
            "extension_promoted_lookups": extension_lookups,
            "format3_chained_subtables": format3_subtables,
        }
    finally:
        font.close()


def build_mini_font(glyphs: Mapping, fea: str, out_path: Path) -> Path:
    from build_font import _report_gsub_budget, _write_if_changed, build_font

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    glyph_data = _glyph_data(glyphs)
    build_log = io.StringIO()
    with redirect_stdout(build_log):
        font = build_font(glyph_data, None, variant="senior", senior_fea=fea)

    try:
        packing = pack_gsub.pack_font(font)
        buffer = io.BytesIO()
        font.save(buffer)
        _write_if_changed(out_path, buffer.getvalue())
        fea_code = cast(str, getattr(font, "_fea_code"))
        _write_if_changed(out_path.with_suffix(".fea"), (fea_code + "\n").encode())
    finally:
        font.close()

    budget_capture = io.StringIO()
    with redirect_stdout(budget_capture):
        _report_gsub_budget(out_path, fea)
    budget_report = budget_capture.getvalue()

    metrics = _table_metrics(out_path)
    parsed = _parse_budget_report(budget_report)
    headroom = parsed.get("subtable_offset_headroom")
    format3 = metrics["format3_chained_subtables"]

    budget = {
        "measured": {
            **metrics,
            **parsed,
            "packing": packing,
            "report_text": budget_report.strip().splitlines(),
        },
        "gate": {
            "headroom_floor": K2_HEADROOM_FLOOR,
            "format3_chained_subtables": format3,
            "failed": bool(format3 and headroom is not None and headroom < K2_HEADROOM_FLOOR),
        },
    }
    budget_path = out_path.parent / "budget.json"
    budget_path.write_text(json.dumps(budget, indent=2) + "\n")

    if budget["gate"]["failed"]:
        raise BudgetError(
            f"per-rule format-3 chained-context fallback ({format3} subtables) with subtable offset headroom {headroom} below the {K2_HEADROOM_FLOOR}-byte floor; see {budget_path}"
        )
    return out_path
