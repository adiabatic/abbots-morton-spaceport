#!/usr/bin/env python3
"""
Build a pixel font from bitmap glyph definitions. Uses fonttools FontBuilder to create OTF output.

Usage:
    uv run python tools/build_font.py <glyph_data.yaml|glyph_data/> [output_dir]

    The first argument can be a single YAML file or a directory of YAML files. When a directory is given, all *.yaml files are loaded and merged.

Outputs (Regular and Bold, six static OTFs):
    output_dir/AbbotsMortonSpaceportMono-Regular.otf
    output_dir/AbbotsMortonSpaceportMono-Bold.otf
    output_dir/AbbotsMortonSpaceportSansJunior-Regular.otf  (proportional, no cursive joins)
    output_dir/AbbotsMortonSpaceportSansJunior-Bold.otf
    output_dir/AbbotsMortonSpaceportSansSenior-Regular.otf  (proportional, with cursive/calt)
    output_dir/AbbotsMortonSpaceportSansSenior-Bold.otf

Bold draws each pixel as a rectangle 1.5 pixels wide, extended half a pixel to the right. The pixel grid, advance widths, kerning, and anchors are the same as Regular.
"""

import hashlib
import io
import logging
import re
import struct
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.otlLib.maxContextCalc import maxCtxFont
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.ttLib.tables._c_m_a_p import cmap_format_14
from departure_mono_import import import_departure_mono
from glyph_compiler import CompiledGlyphSet, compile_glyph_set, is_proportional_glyph
from quikscript_fea import emit_namer_dot_calt, emit_quikscript_senior_features, emit_quikscript_ss
from typing import Any, cast

from quikscript_ir import (
    GlyphData,
    GlyphDef,
    JoinGlyph,
    family_names_from_compiled,
    get_base_glyph_name,
    heal_glyph_name,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
# `memory_budget` reaches `peak_rss` by package path, so the repo root is what has to go on `sys.path`; `rebuild/tools/` is not enough.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rebuild.tools.memory_budget import usable_cores  # noqa: E402

_DEPARTURE_MONO_OTF = _REPO_ROOT / "reference" / "DepartureMono-Regular.otf"
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_postscript_glyph_names() -> dict[str, int]:
    path = Path(__file__).parent.parent / "postscript_glyph_names.yaml"
    with open(path) as f:
        return yaml.load(f, Loader=_SAFE_LOADER)


# Top-level keys that mark a YAML document as one of the structured glyph-data records. A document carrying none of these is read as a bare Senior-only kerning rule (see senior_quikscript_kerning.yaml).
_STRUCTURAL_KEYS = frozenset(
    {
        "metadata",
        "glyphs",
        "glyph_families",
        "context_sets",
        "kerning",
        "restore_isolated_form_overrides",
        "predecessor_demote_overrides",
        "trailing_demote_overrides",
    }
)


def load_glyph_data(path: Path) -> GlyphData:
    """Load glyph definitions from a YAML file or directory of YAML files.

    Each file may hold one document or several `---`-separated documents. A mapping with none of `_STRUCTURAL_KEYS` is one Senior-only kerning rule. Such rules have no names, so `_assemble_fea` names each lookup by its position.
    """
    metadata: dict[str, Any] = {}
    glyphs: dict[str, Any] = {}
    glyph_families: dict[str, Any] = {}
    context_sets: dict[str, Any] = {}
    kerning_defs: dict[str, Any] = {}
    senior_kerning_rules: list[dict[str, Any]] = []
    restore_isolated_form_overrides: list[Any] = []
    predecessor_demote_overrides: list[Any] = []
    trailing_demote_overrides: list[Any] = []

    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    for yaml_file in files:
        with open(yaml_file) as f:
            documents = list(yaml.load_all(f, Loader=_SAFE_LOADER))
        for data in documents:
            if not isinstance(data, dict) or not data:
                continue
            if "metadata" in data:
                metadata = data["metadata"]
            if "glyphs" in data:
                glyphs.update(data["glyphs"])
            if "glyph_families" in data:
                glyph_families.update(data["glyph_families"])
            if "context_sets" in data:
                context_sets.update(data["context_sets"])
            if "kerning" in data:
                kerning_defs.update(data["kerning"])
            if "restore_isolated_form_overrides" in data:
                restore_isolated_form_overrides.extend(data["restore_isolated_form_overrides"] or [])
            if "predecessor_demote_overrides" in data:
                predecessor_demote_overrides.extend(data["predecessor_demote_overrides"] or [])
            if "trailing_demote_overrides" in data:
                trailing_demote_overrides.extend(data["trailing_demote_overrides"] or [])
            if not _STRUCTURAL_KEYS.intersection(data):
                senior_kerning_rules.append(data)

    return {
        "metadata": metadata,
        "glyphs": glyphs,
        "glyph_families": glyph_families,
        "context_sets": context_sets,
        "kerning": kerning_defs,
        "senior_kerning": senior_kerning_rules,
        "restore_isolated_form_overrides": restore_isolated_form_overrides,
        "predecessor_demote_overrides": predecessor_demote_overrides,
        "trailing_demote_overrides": trailing_demote_overrides,
    }


def _extract_feature_lookup_names(fea_code: str | None, feature_tag: str) -> list[str]:
    if fea_code is None:
        return []

    names = []
    in_feature = False
    for line in fea_code.splitlines():
        stripped = line.strip()
        if not in_feature:
            if stripped.startswith(f"feature {feature_tag} ") and stripped.endswith("{"):
                in_feature = True
            continue
        if stripped == f"}} {feature_tag};":
            break
        if stripped.startswith("lookup ") and stripped.endswith("{"):
            parts = stripped.split()
            if len(parts) >= 2:
                names.append(parts[1])
    return names


def _write_if_changed(path: Path, data: bytes) -> bool:
    # The build is byte-deterministic, so rewriting an identical output would only change its mtime, and the review surface's fixture cache, which keys on mtimes, would treat it as a new build and discard its work. The existing file is hashed in chunks so the comparison does not hold a second copy of the output in memory.
    try:
        with path.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").digest() == hashlib.sha256(data).digest():
                return False
    except FileNotFoundError:
        pass
    path.write_bytes(data)
    return True


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _report_gsub_budget(font_path: Path, fea_code: str | None) -> None:
    font = TTFont(font_path)
    try:
        reader = cast(Any, font.reader)
        if "GSUB" not in font or reader is None or "GSUB" not in reader.tables:
            return

        record = reader.tables["GSUB"]
        reader.file.seek(record.offset)
        data = reader.file.read(record.length)
        if len(data) < 10:
            return

        lookup_list_offset = _read_u16(data, 8)
        lookup_count = _read_u16(data, lookup_list_offset)
        lookup_offsets = [_read_u16(data, lookup_list_offset + 2 + 2 * i) for i in range(lookup_count)]

        max_lookup_offset = max(lookup_offsets, default=0)
        max_subtable_offset = 0
        max_subtable_lookup_index: int | None = None
        for lookup_index, lookup_offset in enumerate(lookup_offsets):
            lookup_table_offset = lookup_list_offset + lookup_offset
            subtable_count = _read_u16(data, lookup_table_offset + 4)
            for subtable_index in range(subtable_count):
                subtable_offset = _read_u16(
                    data,
                    lookup_table_offset + 6 + 2 * subtable_index,
                )
                if subtable_offset > max_subtable_offset:
                    max_subtable_offset = subtable_offset
                    max_subtable_lookup_index = lookup_index

        lookup_list = font["GSUB"].table.LookupList
        lookups = lookup_list.Lookup if lookup_list else []
        subtable_total = sum(len(lookup.SubTable) for lookup in lookups)

        print(
            "  GSUB budget: "
            f"{record.length:,} bytes, "
            f"{lookup_count:,} lookups, "
            f"{subtable_total:,} subtables"
        )
        print(
            "  GSUB offset headroom: "
            f"LookupList {65_535 - max_lookup_offset:,} bytes, "
            f"subtable {65_535 - max_subtable_offset:,} bytes"
            + (f" in lookup {max_subtable_lookup_index}" if max_subtable_lookup_index is not None else "")
        )

        feature_records = font["GSUB"].table.FeatureList.FeatureRecord
        calt_indices = []
        for feature_record in feature_records:
            if feature_record.FeatureTag == "calt":
                calt_indices = list(feature_record.Feature.LookupListIndex)
                break
        if not calt_indices:
            return

        calt_names = _extract_feature_lookup_names(fea_code, "calt")
        rows = []
        for position, lookup_index in enumerate(calt_indices):
            lookup = lookups[lookup_index]
            lookup_name = calt_names[position] if position < len(calt_names) else f"lookup[{lookup_index}]"
            rows.append((len(lookup.SubTable), lookup_index, lookup_name))

        top_rows = sorted(rows, reverse=True)[:5]
        if top_rows:
            top = ", ".join(f"{name}={subtable_count}" for subtable_count, _, name in top_rows)
            print(f"  Largest calt lookups by subtable count: {top}")
    finally:
        font.close()


def _resolve_codepoint(glyph_name: str, postscript_names: dict[str, int]) -> int | None:
    if len(glyph_name) == 1:
        return ord(glyph_name)
    if glyph_name.startswith("uni") and len(glyph_name) == 7:
        try:
            return int(glyph_name[3:], 16)
        except ValueError:
            return None
    if glyph_name.startswith("u") and not glyph_name.startswith("uni") and len(glyph_name) == 6:
        try:
            cp = int(glyph_name[1:], 16)
        except ValueError:
            return postscript_names.get(glyph_name)
        return cp if cp > 0xFFFF else None
    return postscript_names.get(glyph_name)


def build_cmap14(
    variation_sequences: dict[str, dict[str, str]],
    glyphs_def: dict[str, GlyphDef],
    name_to_codepoint: dict[str, int],
) -> cmap_format_14 | None:
    if not variation_sequences:
        return None

    # Variation-sequence targets in metadata.yaml, such as `qsWay.half`, lack the modifiers `_synthesize_anchor_modifiers` adds, so the compiled stance is `qsWay.half.ex-y0`. Heal each target against the compiled glyph set. A target that heals to no single stance, such as `qsTea.half`, is skipped without a warning.
    available_names = frozenset(glyphs_def)
    family_names = family_names_from_compiled(available_names)
    uvsDict = {}
    for vs_cp, mappings in variation_sequences.items():
        entries = []
        for base_name, target_name in mappings.items():
            base_cp = name_to_codepoint.get(base_name)
            if base_cp is None:
                continue
            try:
                resolved = heal_glyph_name(target_name, family_names, available_names)
            except ValueError:
                resolved = target_name
            if resolved not in glyphs_def:
                resolved = get_base_glyph_name(resolved)
            if resolved not in glyphs_def:
                continue
            entries.append((base_cp, resolved))
        if entries:
            uvsDict[vs_cp] = entries

    if not uvsDict:
        return None

    subtable = cmap_format_14(14)
    subtable.platformID = 0  # pyright: ignore[reportAttributeAccessIssue]
    subtable.platEncID = 5  # pyright: ignore[reportAttributeAccessIssue]
    subtable.language = 0
    subtable.cmap = {}
    subtable.uvsDict = uvsDict
    return subtable


def collect_kerning_groups(glyphs_def: dict[str, GlyphDef]) -> dict[str, list[str]]:
    groups = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        for tag in glyph_def.get("kerning", []):
            groups.setdefault(tag, []).append(glyph_name)
    return groups


def generate_kern_fea(
    kerning_defs: dict[str, dict[str, Any]],
    kerning_groups: dict[str, list[str]],
    all_glyph_names: list[str],
    pixel_width: int,
    twin_by_bare: dict[str, str] | None = None,
) -> str:
    """Emit the `kern` feature for `kerning_defs`. `twin_by_bare` maps each letter's bare glyph to its ss10 twin. A twin is kerned wherever its bare glyph is and nowhere else, whatever the rule's selectors match by name."""
    twins = twin_by_bare or {}
    twin_names = set(twins.values())

    def with_twins(glyphs: list[str]) -> list[str]:
        kept = [g for g in glyphs if g not in twin_names]
        return kept + [twins[g] for g in kept if g in twins]

    def matches_prefix(glyph: str, prefix: str) -> bool:
        return glyph == prefix or glyph.startswith(prefix + ".")

    def expand_prefixes(prefixes: list[str]) -> list[str]:
        return [g for g in all_glyph_names if any(matches_prefix(g, p) for p in prefixes)]

    def subtract_prefixes(glyphs: list[str], prefixes: list[str]) -> list[str]:
        return [g for g in glyphs if not any(matches_prefix(g, p) for p in prefixes)]

    preamble = []
    lines = ["feature kern {"]
    for tag_name, definition in kerning_defs.items():
        if "left_family" in definition:
            left_glyphs = expand_prefixes(definition["left_family"])
        elif "left_stance" in definition:
            left_glyphs = expand_prefixes(definition["left_stance"])
        elif "left" in definition:
            left_glyphs = definition["left"]
        else:
            excluded = set(kerning_groups.get(tag_name, []))
            left_glyphs = [g for g in all_glyph_names if g not in excluded]
        if "except_left" in definition:
            left_glyphs = subtract_prefixes(left_glyphs, definition["except_left"])
        left_glyphs = with_twins(left_glyphs)
        if not left_glyphs:
            continue
        if "right_group" in definition:
            suffix = "." + definition["right_group"]
            right_glyphs = [g for g in all_glyph_names if g.endswith(suffix)]
        elif "right_family" in definition:
            right_glyphs = expand_prefixes(definition["right_family"])
        elif "right_stance" in definition:
            right_glyphs = expand_prefixes(definition["right_stance"])
        else:
            right_glyphs = definition["right"]
        if "except_right" in definition and "right_group" not in definition:
            right_glyphs = subtract_prefixes(right_glyphs, definition["except_right"])
        right_glyphs = with_twins(right_glyphs)
        if not right_glyphs:
            continue
        value = definition["value"] * pixel_width
        left = " ".join(sorted(left_glyphs))
        right = " ".join(sorted(right_glyphs))
        if definition.get("right_group") == "noentry":
            val_lookup = f"kern_{tag_name}_val"
            preamble.append(f"lookup {val_lookup} {{")
            for g in sorted(left_glyphs):
                preamble.append(f"    pos {g} <0 0 {value} 0>;")
            preamble.append(f"}} {val_lookup};")
            lines.append(f"    lookup kern_{tag_name} {{")
            lines.append(f"        pos [{left}]' lookup {val_lookup} uni200C;")
            lines.append(f"    }} kern_{tag_name};")
        else:
            lines.append(f"    lookup kern_{tag_name} {{")
            lines.append(f"        pos [{left}] [{right}] {value};")
            lines.append(f"    }} kern_{tag_name};")
    lines.append("} kern;")
    return "\n".join(preamble + [""] + lines) if preamble else "\n".join(lines)


def generate_ccmp_fea(glyphs_def: dict[str, GlyphDef]) -> str | None:
    """Generate OpenType feature code for dotted-base substitutions.

    Rewrites dotted lowercase bases to their dotless forms before top combining marks are attached.
    """
    top_marks = [
        glyph_name
        for glyph_name, glyph_def in glyphs_def.items()
        if glyph_def is not None and glyph_def.get("is_mark") and glyph_def.get("y_offset", 0) >= 0
    ]
    if not top_marks:
        return None

    substitutions = [
        (base_name, dotless_name)
        for base_name, dotless_name in (("i", "dotlessi"), ("j", "dotlessj"))
        if base_name in glyphs_def and dotless_name in glyphs_def
    ]
    if not substitutions:
        return None

    lines = ["feature ccmp {"]
    lines.append(f"    @top_marks = [{' '.join(sorted(top_marks))}];")
    for base_name, dotless_name in substitutions:
        lines.append("")
        lines.append(f"    lookup ccmp_{base_name}_before_top_marks {{")
        lines.append(f"        sub {base_name}' @top_marks by {dotless_name};")
        lines.append(f"    }} ccmp_{base_name}_before_top_marks;")
    lines.append("} ccmp;")
    return "\n".join(lines)


def generate_mark_fea(glyphs_def: dict[str, GlyphDef], pixel_width: int, pixel_height: int) -> str | None:
    """Generate OpenType feature code for mark positioning (combining diacriticals).

    Scans glyphs_def for marks (is_mark: true) and base glyphs with top_mark_y / bottom_mark_y anchors, then emits a GPOS 'mark' feature.

    Returns the FEA string, or None if there are no marks.
    """
    top_marks = {}  # glyph_name -> (anchor_x, anchor_y)
    bottom_marks = {}
    adjusted_marks = {}  # glyph_name -> (anchor_x, anchor_y, is_top, base_x_adjust, base_y_adjust)
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or not glyph_def.get("is_mark"):
            continue
        bitmap = glyph_def.get("bitmap", [])
        y_offset = glyph_def.get("y_offset", 0)
        # Mark anchor x = 0 (bitmap is centered on origin by zero-width drawing)
        anchor_x = 0
        base_x_adjust = glyph_def.get("base_x_adjust")
        mark_x_override = glyph_def.get("mark_base_x_adjust")
        if mark_x_override and base_x_adjust:
            base_x_adjust = {**base_x_adjust, **mark_x_override}
        elif mark_x_override:
            base_x_adjust = mark_x_override
        base_y_adjust = glyph_def.get("base_y_adjust")
        mark_y_override = glyph_def.get("mark_base_y_adjust")
        if mark_y_override and base_y_adjust:
            base_y_adjust = {**base_y_adjust, **mark_y_override}
        elif mark_y_override:
            base_y_adjust = mark_y_override
        has_adjustments = base_x_adjust or base_y_adjust
        if y_offset >= 0:
            # Top mark: anchor at the bottom of the drawn pixels
            anchor_y = y_offset * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (
                    anchor_x,
                    anchor_y,
                    True,
                    base_x_adjust or {},
                    base_y_adjust or {},
                )
            else:
                top_marks[glyph_name] = (anchor_x, anchor_y)
        else:
            # Bottom mark: anchor at the top of the drawn pixels
            bitmap_height = len(bitmap) if bitmap else 0
            anchor_y = (y_offset + bitmap_height) * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (
                    anchor_x,
                    anchor_y,
                    False,
                    base_x_adjust or {},
                    base_y_adjust or {},
                )
            else:
                bottom_marks[glyph_name] = (anchor_x, anchor_y)

    if not top_marks and not bottom_marks and not adjusted_marks:
        return None

    top_bases = {}  # glyph_name -> (anchor_x, anchor_y)
    bottom_bases = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or glyph_def.get("is_mark"):
            continue
        advance_width = glyph_def.get("advance_width")
        if advance_width is not None:
            aw = advance_width * pixel_width
        else:
            bitmap = glyph_def.get("bitmap", [])
            if bitmap:
                max_col = max((len(row) for row in bitmap), default=0)
                aw = (max_col + 2) * pixel_width
            else:
                continue
        base_x = aw // 2
        if "top_mark_y" in glyph_def:
            top_x = base_x + glyph_def.get("top_mark_x", 0) * pixel_width
            base_y = glyph_def["top_mark_y"] * pixel_height
            top_bases[glyph_name] = (top_x, base_y)
        if "bottom_mark_y" in glyph_def:
            bottom_x = base_x + glyph_def.get("bottom_mark_x", 0) * pixel_width
            base_y = glyph_def["bottom_mark_y"] * pixel_height
            bottom_bases[glyph_name] = (bottom_x, base_y)

    if not top_bases and not bottom_bases:
        return None

    lines = ["feature mark {"]

    for glyph_name in sorted(top_marks):
        ax, ay = top_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_top;")
    for glyph_name in sorted(bottom_marks):
        ax, ay = bottom_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_bottom;")
    for glyph_name in sorted(adjusted_marks):
        ax, ay, _, _, _ = adjusted_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_{glyph_name};")

    if top_marks and top_bases:
        lines.append("")
        lines.append("    lookup mark_top {")
        for glyph_name in sorted(top_bases):
            bx, by = top_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_top;")
        lines.append("    } mark_top;")

    if bottom_marks and bottom_bases:
        lines.append("")
        lines.append("    lookup mark_bottom {")
        for glyph_name in sorted(bottom_bases):
            bx, by = bottom_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_bottom;")
        lines.append("    } mark_bottom;")

    for mark_name in sorted(adjusted_marks):
        _, _, is_top, base_x_adjust, base_y_adjust = adjusted_marks[mark_name]
        bases = top_bases if is_top else bottom_bases
        if not bases:
            continue
        lines.append("")
        lines.append(f"    lookup mark_{mark_name} {{")
        for glyph_name in sorted(bases):
            bx, by = bases[glyph_name]
            x_adj = base_x_adjust.get(glyph_name, 0) * pixel_width
            y_adj = base_y_adjust.get(glyph_name, 0) * pixel_height
            lines.append(
                f"        pos base {glyph_name} <anchor {int(bx + x_adj)} {int(by + y_adj)}> mark @mark_{mark_name};"
            )
        lines.append(f"    }} mark_{mark_name};")

    lines.append("} mark;")
    return "\n".join(lines)


def parse_bitmap(bitmap: list[str] | list[list[int]]) -> list[list[int]]:
    if not bitmap:
        return []

    if isinstance(bitmap[0], str):
        return [[1 if c == "#" or c == "1" else 0 for c in row] for row in bitmap]
    return cast(list[list[int]], bitmap)


def bitmap_to_rectangles(
    bitmap: list[list[int]],
    pixel_width: int,
    pixel_height: int,
    y_offset: int = 0,
) -> list[tuple[int, int, int, int]]:
    """Return an `(x, y, width, height)` rectangle in font units for each filled pixel of *bitmap*, with y=0 at the baseline.

    *y_offset* is in pixels: 0 puts the bitmap's bottom row on the baseline, and -3 puts it 3 pixels below.
    """
    rectangles = []
    height = len(bitmap)

    for row_idx, row in enumerate(bitmap):
        # Bitmap row 0 is the top row, and font y increases upward.
        y = (y_offset + height - 1 - row_idx) * pixel_height

        for col_idx, pixel in enumerate(row):
            if pixel:
                x = col_idx * pixel_width
                rectangles.append((x, y, pixel_width, pixel_height))

    return rectangles


def compose_bitmaps(
    base_bitmap: list[list[int]],
    base_y_offset: int,
    accent_bitmap: list[list[int]],
    mark_y: int,
    is_top: bool,
    accent_x_adjust: int = 0,
) -> tuple[list[list[int]], int]:
    """Overlay an accent bitmap on a base bitmap and return `(combined_bitmap, combined_y_offset)`.

    *mark_y* is the base glyph's mark anchor in pixels above the baseline. A top accent's bottom edge sits there, and a bottom accent's top edge. Both bitmaps are centered horizontally on the wider of the two, and *accent_x_adjust* shifts the accent by that many pixels.
    """
    base_h = len(base_bitmap)
    accent_h = len(accent_bitmap)
    base_w = max((len(row) for row in base_bitmap), default=0)
    accent_w = max((len(row) for row in accent_bitmap), default=0)

    canvas_w = max(base_w, accent_w)

    # Pixel rows in font coordinates, where 0 is the baseline and y increases upward.
    base_bottom = base_y_offset
    base_top = base_y_offset + base_h

    if is_top:
        # Top accent: its bottom edge sits at mark_y
        accent_bottom = mark_y
        accent_top = mark_y + accent_h
    else:
        # Bottom accent: its top edge sits at mark_y, extending downward
        accent_top = mark_y
        accent_bottom = mark_y - accent_h

    combined_bottom = min(base_bottom, accent_bottom)
    combined_top = max(base_top, accent_top)
    combined_h = combined_top - combined_bottom

    canvas = [[0] * canvas_w for _ in range(combined_h)]

    def blit(bitmap, bm_w, bm_bottom, x_adjust=0):
        x_off = (canvas_w - bm_w) // 2 + x_adjust
        bm_h = len(bitmap)
        for row_idx, row in enumerate(bitmap):
            # bitmap row 0 is top; font-pixel y for this row:
            pixel_y = bm_bottom + bm_h - 1 - row_idx
            canvas_row = combined_top - 1 - pixel_y
            for col_idx, val in enumerate(row):
                if val:
                    canvas[canvas_row][x_off + col_idx] = 1

    blit(base_bitmap, base_w, base_bottom)
    blit(accent_bitmap, accent_w, accent_bottom, accent_x_adjust)

    return canvas, combined_bottom


def resolve_composite(
    glyph_name: str,
    glyph_def: GlyphDef,
    glyphs_def: dict[str, GlyphDef],
    is_proportional: bool,
) -> tuple[list[list[int]], int]:
    base_name = glyph_def["base"]

    if is_proportional and base_name + ".prop" in glyphs_def:
        base_ref = base_name + ".prop"
    else:
        base_ref = base_name

    base_glyph = glyphs_def.get(base_ref)
    if base_glyph is None:
        raise ValueError(f"Composite glyph '{glyph_name}' references base '{base_name}' which doesn't exist")
    base_bitmap = parse_bitmap(base_glyph.get("bitmap", []))
    base_y_offset = base_glyph.get("y_offset", 0)

    result_bitmap = base_bitmap
    result_y_offset = base_y_offset

    # Map each combining mark's bitmap object to its adjustments. A YAML alias makes a spacing accent's bitmap the same object as its combining mark's, so an `id()` lookup finds the mark's adjustments from the accent.
    accent_x_adjusts = {}
    accent_y_adjusts = {}
    for gn, gd in glyphs_def.items():
        if not gd.get("is_mark"):
            continue
        bitmap_obj = gd.get("bitmap")
        if bitmap_obj is None:
            continue
        if "base_x_adjust" in gd:
            accent_x_adjusts[id(bitmap_obj)] = gd["base_x_adjust"]
        if "base_y_adjust" in gd:
            accent_y_adjusts[id(bitmap_obj)] = gd["base_y_adjust"]

    def _apply_accent(position, result_bitmap, result_y_offset):
        is_top = position == "top"
        mark_key = "top_mark_y" if is_top else "bottom_mark_y"
        accent_name = glyph_def[position]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references {position} accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get(mark_key)
        if mark_y is None:
            raise ValueError(f"Composite glyph '{glyph_name}' needs {mark_key} on base '{base_ref}'")
        bitmap_id = id(accent_glyph.get("bitmap"))
        accent_x_adjust = accent_x_adjusts.get(bitmap_id, {}).get(base_name, 0)
        accent_y_adjust = accent_y_adjusts.get(bitmap_id, {}).get(base_name, 0)
        return compose_bitmaps(
            result_bitmap,
            result_y_offset,
            accent_bitmap,
            mark_y + accent_y_adjust,
            is_top=is_top,
            accent_x_adjust=accent_x_adjust,
        )

    if "top" in glyph_def:
        result_bitmap, result_y_offset = _apply_accent("top", result_bitmap, result_y_offset)

    if "bottom" in glyph_def:
        result_bitmap, result_y_offset = _apply_accent("bottom", result_bitmap, result_y_offset)

    return result_bitmap, result_y_offset


_NAMER_DOT = "periodcentered"
_NAMER_DOT_LOWERED = "periodcentered.lowered"


def _resolve_short_families(context_sets: dict[str, Any]) -> set[str]:
    """Collect the family names in the `shorts` context set, following any nested context-set references."""
    families: set[str] = set()

    def walk(items: Any) -> None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            if "family" in item:
                families.add(item["family"])
            elif "context_set" in item:
                walk(context_sets.get(item["context_set"], []))

    walk(context_sets.get("shorts", []))
    return families


def _is_orthodox_letter_or_digit(codepoint: int | None) -> bool:
    """True for ASCII and Latin letters and digits, the glyphs after which a `·` is a mid-word middot and not a namer dot. The Quikscript letters are excluded, so in back-to-back names like ·Bay·No the second dot is still lowered."""
    if codepoint is None:
        return False
    in_latin_range = (
        0x30 <= codepoint <= 0x39
        or 0x41 <= codepoint <= 0x5A
        or 0x61 <= codepoint <= 0x7A
        or 0xC0 <= codepoint <= 0x24F
    )
    return in_latin_range and chr(codepoint).isalnum()


def _namer_dot_calt_fea(
    glyph_data: GlyphData,
    join_glyphs: dict[str, JoinGlyph],
    glyph_order: list[str],
    name_to_codepoint: dict[str, int],
) -> str | None:
    """Build the namer-dot `calt` for a proportional variant. The follower class is every compiled short-family stance present in this variant (base stances only in Junior, base plus contextual variants in Senior), plus ligatures whose lead component is a short."""
    order = set(glyph_order)
    if _NAMER_DOT_LOWERED not in order or _NAMER_DOT not in order:
        return None
    short_families = _resolve_short_families(glyph_data.get("context_sets", {}))
    if not short_families:
        return None

    followers = []
    for name in glyph_order:
        meta = join_glyphs.get(name)
        if meta is None:
            continue
        family = meta.family
        if family is None and meta.sequence:
            lead = join_glyphs.get(meta.sequence[0])
            family = lead.family if lead else None
        if family in short_families:
            followers.append(name)

    midword = [name for name in glyph_order if _is_orthodox_letter_or_digit(name_to_codepoint.get(name))]
    return emit_namer_dot_calt(_NAMER_DOT, _NAMER_DOT_LOWERED, followers, midword)


@dataclass(frozen=True)
class _GlyphInventory:
    """A variant's glyph definitions (with Departure Mono's merged in for mono), glyph order, cmap, and code point for each glyph name. `build_font` and `compile_senior_otl` both use it, so the Senior layout tables are compiled against the glyph order the Senior fonts have."""

    glyphs_def: dict[str, GlyphDef]
    glyph_order: list[str]
    cmap: dict[int, str]
    name_to_codepoint: dict[str, int]


def _glyph_inventory(compiled: CompiledGlyphSet, variant: str, pixel_height: int) -> _GlyphInventory:
    is_proportional = variant != "mono"
    glyphs_def = compiled.glyph_definitions

    # The mono build is Departure Mono plus the Quikscript letters. DM's glyphs win on name collisions such as `zero` and `space`, and its code points go into the cmap and glyph order below.
    if variant == "mono":
        dm_glyphs, dm_codepoints = import_departure_mono(str(_DEPARTURE_MONO_OTF), pixel_height)
        glyphs_def = {**glyphs_def, **dm_glyphs}
    else:
        dm_codepoints = {}

    # `.notdef` must come first in the glyph order. The mono build excludes `.prop` glyphs. The IR already keeps contextual stances out of mono and Junior, so no other name filter is needed.
    glyph_names = [
        name
        for name in glyphs_def.keys()
        if name not in (".notdef", "space") and (is_proportional or not is_proportional_glyph(name))
    ]
    postscript_glyph_names = load_postscript_glyph_names()

    name_to_codepoint: dict[str, int] = {}
    for name in glyphs_def:
        cp = _resolve_codepoint(name, postscript_glyph_names)
        if cp is not None:
            name_to_codepoint[name] = cp
    name_to_codepoint.update(dm_codepoints)

    def _sort_key(name):
        cp = name_to_codepoint.get(name)
        if cp is not None:
            return (cp, name)
        base = name.split(".")[0]
        if "_" in base:
            base = base.split("_")[0]
        cp = name_to_codepoint.get(base)
        if cp is not None:
            return (cp, name)
        return (float("inf"), name)

    glyph_order = [".notdef", "space"] + sorted(glyph_names, key=_sort_key)

    # `.prop` glyphs have no code point of their own.
    cmap = {32: "space"}
    for glyph_name in glyphs_def:
        if is_proportional_glyph(glyph_name):
            continue
        cp = name_to_codepoint.get(glyph_name)
        if cp is not None:
            cmap[cp] = glyph_name

    return _GlyphInventory(glyphs_def, glyph_order, cmap, name_to_codepoint)


def _assemble_fea(
    glyph_data: GlyphData,
    inventory: _GlyphInventory,
    join_glyphs: dict[str, JoinGlyph],
    ss10_twins: dict[str, JoinGlyph],
    variant: str,
    pixel_width: int,
    pixel_height: int,
    senior_fea: str | None,
) -> str | None:
    """Return the feature file a variant compiles, or None for mono, which copies Departure Mono's tables. `senior_fea` is the precomputed Senior join code (see `build_font`). When it is None, a Senior build emits its own. `ss10_twins` (empty outside Senior) are kerned like their bare glyphs and are in the namer-dot follower class like their bare glyphs."""
    is_proportional = variant != "mono"
    is_senior = variant == "senior"
    glyphs_def = inventory.glyphs_def
    twin_by_bare = {meta.base_name: name for name, meta in ss10_twins.items()}
    fea_code_parts = []

    kerning_defs = glyph_data.get("kerning", {})
    if is_proportional and kerning_defs:
        kerning_groups = collect_kerning_groups(glyphs_def)
        fea_code_parts.append(
            generate_kern_fea(
                kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_width, twin_by_bare
            )
        )

    if is_proportional:
        ccmp_fea = generate_ccmp_fea(glyphs_def)
        if ccmp_fea:
            fea_code_parts.append(ccmp_fea)

        mark_fea = generate_mark_fea(glyphs_def, pixel_width, pixel_height)
        if mark_fea:
            fea_code_parts.append(mark_fea)

    if is_senior:
        # The Senior FEA is the same for Regular and Bold, and emitting it takes ≈3.2s, so `main()` emits it once and passes it as `senior_fea`. A caller that builds a Senior font directly, such as a test, gets it emitted here.
        if senior_fea is None:
            senior_fea = build_senior_fea(glyph_data, join_glyphs, pixel_width, pixel_height)
        if senior_fea:
            fea_code_parts.append(senior_fea)

        senior_kerning_rules = glyph_data.get("senior_kerning", [])
        if senior_kerning_rules:
            kerning_groups = collect_kerning_groups(glyphs_def)
            senior_kerning_defs = {f"senior_{i}": rule for i, rule in enumerate(senior_kerning_rules)}
            fea_code_parts.append(
                generate_kern_fea(
                    senior_kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_width, twin_by_bare
                )
            )
    elif is_proportional:
        ss_fea = emit_quikscript_ss(join_glyphs)
        if ss_fea:
            fea_code_parts.append(ss_fea)

    if is_proportional:
        # The namer-dot calt goes last so its lookup runs after the join lookups. The join rules, some of which list `periodcentered` in `not_after`, see the dot unchanged, and the dot is lowered before a Short letter only after the join lookups have chosen that letter's stance. In Senior this is a second `feature calt {}` block, which feaLib merges with the first. In Junior it is the only calt.
        namer_dot_fea = _namer_dot_calt_fea(
            glyph_data, {**join_glyphs, **ss10_twins}, inventory.glyph_order, inventory.name_to_codepoint
        )
        if namer_dot_fea:
            fea_code_parts.append(namer_dot_fea)

    if not fea_code_parts:
        return None
    return "\n\n".join(fea_code_parts)


SENIOR_OTL_TAGS = ("GDEF", "GSUB", "GPOS")


@dataclass(frozen=True)
class CompiledSeniorOTL:
    """The Senior layout tables as compiled bytes, keyed by tag, with the `usMaxContext` value feaLib computes for them. The font that receives the tables does not run feaLib, so it takes its OS/2 `usMaxContext` from here."""

    tables: dict[str, bytes]
    max_context: int


def compile_senior_otl(
    glyph_data: GlyphData,
    compiled: CompiledGlyphSet,
    senior_fea: str | None,
    pixel_width: int | None = None,
) -> CompiledSeniorOTL:
    """Compile the Senior GDEF, GSUB, and GPOS once, on a font that has only the glyph order. Senior Regular and Bold share their glyph order and feature file, so these tables are byte-identical, and the feaLib compile is the slowest and most memory-intensive step of a Senior build. `main()` runs this in a pool worker beside the other builds and passes the result to both Senior jobs as `build_font(senior_otl=…)`."""
    metadata = glyph_data.get("metadata", {})
    pixel_height: int = metadata["pixel_size"]
    if pixel_width is None:
        pixel_width = pixel_height
    inventory = _glyph_inventory(compiled, "senior", pixel_height)
    fea_code = _assemble_fea(
        glyph_data,
        inventory,
        compiled.join_glyphs,
        compiled.ss10_twins,
        "senior",
        pixel_width,
        pixel_height,
        senior_fea,
    )
    assert fea_code is not None, "the senior build always carries feature code"
    font = TTFont()
    font.setGlyphOrder(inventory.glyph_order)
    addOpenTypeFeaturesFromString(font, fea_code)
    tables = {tag: font.getTableData(tag) for tag in SENIOR_OTL_TAGS if tag in font}
    return CompiledSeniorOTL(tables, maxCtxFont(font))


def build_senior_fea(
    glyph_data: GlyphData,
    join_glyphs: dict[str, JoinGlyph],
    pixel_width: int,
    pixel_height: int,
) -> str | None:
    """Emit the Senior feature code (curs, calt, ss…) for the compiled join glyphs. It is the same for Regular and Bold, so `main()` emits it once for both."""
    # The override tables name stances without the anchor-Y modifiers `_synthesize_anchor_modifiers` adds, so `heal_glyph_name` maps each name to its compiled stance.
    senior_family_names = set(glyph_data.get("glyph_families", {}))
    senior_available_names = frozenset(join_glyphs)

    def _heal(name: str) -> str:
        return heal_glyph_name(name, senior_family_names, senior_available_names)

    raw_restore_isolated_form = glyph_data.get("restore_isolated_form_overrides", []) or []
    restore_isolated_form_tuples = tuple(
        (
            _heal(entry["prior"]),
            _heal(entry["target"]),
            _heal(entry["follower"]),
            _heal(entry["isolated_form"]),
        )
        for entry in raw_restore_isolated_form
    )
    raw_pred_demote = glyph_data.get("predecessor_demote_overrides", []) or []
    predecessor_demote_tuples = tuple(
        (
            _heal(entry["backtrack_stance"]) if "backtrack_stance" in entry else None,
            _heal(entry["predecessor_stance"]),
            _heal(entry["trigger_stance"]),
            _heal(entry["isolated_form"]),
        )
        for entry in raw_pred_demote
    )
    raw_trailing_demote = glyph_data.get("trailing_demote_overrides", []) or []
    trailing_demote_tuples = tuple(
        (
            _heal(entry["leader_stance"]),
            _heal(entry["trailing_stance"]),
            _heal(entry["isolated_form"]),
        )
        for entry in raw_trailing_demote
    )
    return emit_quikscript_senior_features(
        join_glyphs,
        pixel_width,
        pixel_height,
        restore_isolated_form_overrides=restore_isolated_form_tuples,
        predecessor_demote_overrides=predecessor_demote_tuples,
        trailing_demote_overrides=trailing_demote_tuples,
    )


def build_font(
    glyph_data: GlyphData,
    output_path: Path | None = None,
    variant: str = "mono",
    pixel_width: int | None = None,
    bold: bool = False,
    senior_fea: str | None = None,
    compiled: CompiledGlyphSet | None = None,
    senior_otl: CompiledSeniorOTL | None = None,
) -> TTFont:
    """Build a CFF OpenType font for one variant and style, write it to *output_path* unless that is None, and return the TTFont.

    Args:
        variant: "mono", "junior", or "senior".
        pixel_width: Width of each pixel in font units. Defaults to metadata["pixel_size"], which is always the pixel height.
        bold: Widen each drawn pixel by `pixel_width // 2` units to the right, and name and style-link the font as Bold. The pixel grid, advance widths, and anchors do not change.
        senior_fea: Precomputed Senior feature code from `build_senior_fea`, used only for Senior. When None, the build emits its own.
        compiled: The variant's compiled glyph set. `bold` does not affect it, so `main()` compiles Senior once for both Senior jobs. When None, the build compiles its own.
        senior_otl: The Senior GDEF, GSUB, and GPOS from `compile_senior_otl`, used only for Senior. When given, the build writes these bytes instead of compiling the feature code.
    """
    metadata = glyph_data.get("metadata", {})
    is_proportional = variant != "mono"
    is_senior = variant == "senior"
    style_name = "Bold" if bold else "Regular"
    if compiled is None:
        compiled = compile_glyph_set(glyph_data, variant)
    join_glyphs = compiled.join_glyphs

    base_font_name = metadata["font_name"]
    suffixes = {"mono": " Mono", "junior": " Sans Junior", "senior": " Sans Senior"}
    font_name = base_font_name + suffixes[variant]
    version = metadata["version"]
    units_per_em: int = metadata["units_per_em"]
    pixel_height: int = metadata["pixel_size"]
    if pixel_width is None:
        pixel_width = pixel_height

    inventory = _glyph_inventory(compiled, variant, pixel_height)
    glyphs_def = inventory.glyphs_def
    glyph_order = inventory.glyph_order
    name_to_codepoint = inventory.name_to_codepoint
    ascender = metadata["ascender"]
    descender = metadata["descender"]
    cap_height = metadata["cap_height"]
    x_height = metadata["x_height"]

    fb = FontBuilder(units_per_em, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(inventory.cmap)

    charstrings = {}
    metrics = {}

    glyph_set = None

    # Standard monospace width: 7 pixels (bitmap 5 + 2 spacing)
    mono_width = 7 * pixel_width

    # Bold widens each painted rectangle to the right by this many units. The pixel grid does not change.
    overstrike = pixel_width // 2 if bold else 0

    pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
    pen.moveTo((pixel_width, 0))
    pen.lineTo((pixel_width, 5 * pixel_height))
    pen.lineTo((5 * pixel_width + overstrike, 5 * pixel_height))
    pen.lineTo((5 * pixel_width + overstrike, 0))
    pen.closePath()
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (mono_width, pixel_width)

    space_def = glyphs_def.get("space", {})
    space_width = space_def["advance_width"] * pixel_width
    pen = T2CharStringPen(width=space_width, glyphSet=glyph_set)
    charstrings["space"] = pen.getCharString()
    metrics["space"] = (space_width, 0)

    for glyph_name in glyph_order:
        if glyph_name in (".notdef", "space"):
            continue

        glyph_def = glyphs_def.get(glyph_name, {})

        if "base" in glyph_def and not glyph_def.get("bitmap"):
            composed_bitmap, composed_y_offset = resolve_composite(
                glyph_name, glyph_def, glyphs_def, is_proportional
            )
            glyph_def = dict(glyph_def)
            glyph_def["bitmap"] = composed_bitmap
            glyph_def["y_offset"] = composed_y_offset
            glyph_def.pop("base", None)
            glyph_def.pop("top", None)
            glyph_def.pop("bottom", None)

        bitmap = glyph_def.get("bitmap", [])

        # A proportional font validates every glyph as proportional. The mono font validates only `.prop` glyphs that way.
        is_prop_glyph = is_proportional or is_proportional_glyph(glyph_name)
        if bitmap:
            if is_prop_glyph:
                row_widths = [len(row) for row in bitmap]
                if len(set(row_widths)) > 1:
                    raise ValueError(f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}")
            else:
                base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
                is_quikscript_glyph = base_name.startswith("uniE6") or base_name.startswith("qs")
                if is_quikscript_glyph:
                    # Quikscript glyphs: all rows must be exactly 5 characters wide
                    for row_idx, row in enumerate(bitmap):
                        row_len = len(row)
                        if row_len != 5:
                            raise ValueError(
                                f"Glyph '{glyph_name}' row {row_idx} has width {row_len}, expected 5"
                            )
                else:
                    row_widths = [len(row) for row in bitmap]
                    if len(set(row_widths)) > 1:
                        raise ValueError(f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}")

        if not bitmap:
            width = glyph_def.get("advance_width")
            if width is not None:
                width = int(width * pixel_width)
            else:
                width = mono_width
            pen = T2CharStringPen(width=width, glyphSet=glyph_set)
            charstrings[glyph_name] = pen.getCharString()
            metrics[glyph_name] = (width, 0)
            continue

        bitmap = parse_bitmap(bitmap)
        y_offset = glyph_def.get("y_offset", 0)  # negative for descenders

        row_count = len(bitmap)

        base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
        is_quikscript = base_name.startswith("uniE6") or base_name.startswith("qs")

        if is_quikscript:
            if glyph_name in ("uniE66E", "uniE66F", "qsAngleParenLeft", "qsAngleParenRight"):
                if row_count != 12:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has {row_count} rows, expected 12 (angled parenthesis)"
                    )
            elif y_offset == -3:
                if row_count not in (9, 12):
                    raise ValueError(
                        f"Glyph '{glyph_name}' has y_offset=-3 but bitmap has {row_count} rows, expected 9 or 12"
                    )
            elif row_count not in (6, 9):
                raise ValueError(f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9")

        rectangles = bitmap_to_rectangles(bitmap, pixel_width, pixel_height, y_offset)

        advance_width = glyph_def.get("advance_width")
        senior_tighten = False
        if advance_width is None:
            if is_prop_glyph:
                max_col = max((len(row) for row in bitmap), default=0)
                advance_width = (max_col + 2) * pixel_width
                # Senior is set one pixel tighter than Junior: each Quikscript letter without an explicit advance loses a pixel of right sidebearing. Anchors position cursive joins, so this only narrows the gap at non-joining boundaries.
                senior_tighten = is_senior and is_quikscript
            else:
                advance_width = mono_width
        else:
            advance_width *= pixel_width

        bitmap_width = max((len(row) for row in bitmap), default=0) * pixel_width
        if advance_width == 0:
            # Zero-width (combining mark): center bitmap on the origin
            x_offset = -(bitmap_width // 2)
        else:
            x_offset = (advance_width - bitmap_width) // 2

        # Trim the right sidebearing after centering so the ink keeps its pixel-grid position and only the advance shrinks.
        if senior_tighten:
            advance_width -= pixel_width

        if advance_width == 0:
            lsb = 0
        elif rectangles:
            lsb = min(r[0] for r in rectangles) + x_offset
        else:
            lsb = x_offset

        # `overstrike` is 0 for Regular.
        pen = T2CharStringPen(width=advance_width, glyphSet=glyph_set)
        for x, y, w, h in rectangles:
            pen.moveTo((x + x_offset, y))
            pen.lineTo((x + x_offset, y + h))
            pen.lineTo((x + x_offset + w + overstrike, y + h))
            pen.lineTo((x + x_offset + w + overstrike, y))
            pen.closePath()

        charstrings[glyph_name] = pen.getCharString()
        metrics[glyph_name] = (advance_width, lsb)

    ps_name = f"{font_name.replace(' ', '')}-{style_name}"
    fb.setupCFF(
        psName=ps_name,
        fontInfo={"FamilyName": font_name, "FullName": f"{font_name} {style_name}"},
        charStringsDict=charstrings,
        privateDict={},
    )

    fb.setupHorizontalMetrics(metrics)

    fb.setupHorizontalHeader(ascent=ascender, descent=descender)

    name_strings = {
        "familyName": {"en": font_name},
        "styleName": {"en": style_name},
        "uniqueFontIdentifier": f"FontBuilder:{font_name}.{style_name}",
        "fullName": {"en": f"{font_name} {style_name}"},
        "psName": ps_name,
        "version": f"Version {version}",
    }

    if "copyright" in metadata:
        copyright_str = metadata["copyright"]
        if "© " in copyright_str:
            year = datetime.now().year
            copyright_str = copyright_str.replace("© ", f"© {year} ", 1)
        name_strings["copyright"] = {"en": copyright_str}
    if "license" in metadata:
        name_strings["licenseDescription"] = {"en": metadata["license"]}
    if "license_url" in metadata:
        name_strings["licenseInfoURL"] = {"en": metadata["license_url"]}
    if "sample_text" in metadata:
        name_strings["sampleText"] = {"en": metadata["sample_text"]}
    if "vendor_url" in metadata:
        name_strings["vendorURL"] = {"en": metadata["vendor_url"]}
    if "description" in metadata:
        name_strings["description"] = {"en": metadata["description"]}
    if "designer" in metadata:
        name_strings["designer"] = {"en": metadata["designer"]}
    if "manufacturer" in metadata:
        name_strings["manufacturer"] = {"en": metadata["manufacturer"]}

    fb.setupNameTable(name_strings)

    fb.setupOS2(
        sTypoAscender=ascender,
        sTypoDescender=descender,
        sTypoLineGap=0,
        usWinAscent=ascender,
        usWinDescent=abs(descender),
        sxHeight=x_height,
        sCapHeight=cap_height,
        fsType=0,  # Installable embedding - no restrictions
    )
    # RIBBI style linking: set the BOLD bit on Bold fonts, REGULAR on Regular.
    fb.font["OS/2"].fsSelection = 0x20 if bold else 0x40  # pyright: ignore[reportAttributeAccessIssue]

    fb.setupPost(isFixedPitch=0 if is_proportional else 1)

    gasp = newTable("gasp")
    # Grid-fit only, no antialiasing
    gasp.gaspRange = {0xFFFF: 0x0001}  # pyright: ignore[reportAttributeAccessIssue]
    fb.font["gasp"] = gasp

    fb.setupHead(unitsPerEm=units_per_em, fontRevision=version)
    if bold:
        fb.font["head"].macStyle |= 0x01  # pyright: ignore[reportAttributeAccessIssue]  # Bold bit

    vs_defs = metadata.get("variation_sequences", {})
    cmap14 = build_cmap14(vs_defs, glyphs_def, name_to_codepoint)
    if cmap14:
        fb.font["cmap"].tables.append(cmap14)

    fea_code = _assemble_fea(
        glyph_data,
        inventory,
        join_glyphs,
        compiled.ss10_twins,
        variant,
        pixel_width,
        pixel_height,
        senior_fea,
    )
    if fea_code is not None:
        if is_senior and senior_otl is not None:
            for tag, data in senior_otl.tables.items():
                table = DefaultTable(tag)
                table.data = data
                fb.font[tag] = table
            fb.font["OS/2"].usMaxContext = (
                senior_otl.max_context
            )  # pyright: ignore[reportAttributeAccessIssue]
        else:
            addOpenTypeFeaturesFromString(fb.font, fea_code)

        if output_path is not None:
            fea_path = output_path.with_suffix(".fea")
            if _write_if_changed(fea_path, (fea_code + "\n").encode()):
                print(f"  Feature code saved to: {fea_path}")
            else:
                print(f"  Feature code unchanged: {fea_path}")

    # The mono build has no feature code of its own. It copies Departure Mono's layout tables so DM's shaping (ccmp, marks, kerning) works for the DM glyphs. The Quikscript letters are in none of these lookups.
    if variant == "mono":
        assert fea_code is None, "mono build should emit no authored FEA; DM owns its OTL"
        dm_font = TTFont(str(_DEPARTURE_MONO_OTF))
        missing = set(dm_font.getGlyphOrder()) - {".notdef"} - set(glyph_order)
        assert not missing, f"DM glyphs missing from mono glyph order: {sorted(missing)[:10]}"
        for tag in ("GDEF", "GSUB", "GPOS"):
            fb.font[tag] = dm_font[tag]  # pyright: ignore[reportArgumentType]

    if output_path is not None:
        buffer = io.BytesIO()
        if variant == "mono":
            with _quiet_dm_coverage_sort_warning():
                fb.save(buffer)
        else:
            fb.save(buffer)
        if _write_if_changed(output_path, buffer.getvalue()):
            print(f"Font saved to: {output_path}")
        else:
            print(f"Font unchanged: {output_path}")
        if is_senior:
            _report_gsub_budget(output_path, fea_code)

    print(f"  Variant: {variant}")
    print(f"  Glyphs: {len(glyph_order)}")
    print(f"  Units per em: {units_per_em}")
    print(f"  Pixel: {pixel_width}×{pixel_height} units")

    font = fb.font
    font._fea_code = fea_code  # pyright: ignore[reportAttributeAccessIssue]
    return font


_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _show_build_warning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    if issubclass(category, UserWarning):
        name = category.__name__
        if name.endswith("Warning"):
            name = name[: -len("Warning")]
        tag = _CAMEL_BOUNDARY.sub("-", name).lower()
        sys.stdout.write(f"WARN[{tag}]: {message}\n")
        return
    sys.stdout.write(warnings.formatwarning(message, category, filename, lineno, line))


def _install_warning_hook() -> None:
    warnings.filterwarnings("always", category=UserWarning)
    warnings.showwarning = _show_build_warning


class _DropCoverageSortMessage(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "Coverage is not sorted by glyph ids" not in record.getMessage()


@contextmanager
def _quiet_dm_coverage_sort_warning() -> Any:
    # Some coverage tables in the copied Departure Mono GSUB and GPOS are not in glyph-id order. fontTools sorts them on write, so the output is correct, but it logs "Coverage is not sorted by glyph ids." for each one. Drop that message while the mono font is written.
    logger = logging.getLogger("fontTools.ttLib.tables.otTables")
    log_filter = _DropCoverageSortMessage()
    logger.addFilter(log_filter)
    try:
        yield
    finally:
        logger.removeFilter(log_filter)


def _build_one_font(
    input_path_str: str,
    output_path_str: str,
    variant: str,
    bold: bool,
    compiled: CompiledGlyphSet | None = None,
    senior_fea: str | None = None,
    senior_otl: CompiledSeniorOTL | None = None,
) -> None:
    # Pool worker entry point. Each worker reloads the glyph data from disk (≈125 ms) so the parent does not have to pickle it. The Senior jobs receive the shared compiled glyph set, feature code, and layout tables.
    _install_warning_hook()
    glyph_data = load_glyph_data(Path(input_path_str))
    build_font(
        glyph_data,
        Path(output_path_str),
        variant=variant,
        bold=bold,
        senior_fea=senior_fea,
        compiled=compiled,
        senior_otl=senior_otl,
    )


def _compile_senior_otl_job(
    input_path_str: str, compiled: CompiledGlyphSet, senior_fea: str | None
) -> CompiledSeniorOTL:
    _install_warning_hook()
    glyph_data = load_glyph_data(Path(input_path_str))
    return compile_senior_otl(glyph_data, compiled, senior_fea)


def main() -> None:
    _install_warning_hook()
    if len(sys.argv) < 2:
        print("Usage: uv run python tools/build_font.py <glyph_data.yaml|glyph_data/> [output_dir]")
        print("\nOutputs:")
        print("  output_dir/AbbotsMortonSpaceportMono-Regular.otf")
        print("  output_dir/AbbotsMortonSpaceportMono-Bold.otf")
        print("  output_dir/AbbotsMortonSpaceportSansJunior-Regular.otf")
        print("  output_dir/AbbotsMortonSpaceportSansJunior-Bold.otf")
        print("  output_dir/AbbotsMortonSpaceportSansSenior-Regular.otf")
        print("  output_dir/AbbotsMortonSpaceportSansSenior-Bold.otf")
        print("\nExample:")
        print("  uv run python tools/build_font.py glyph_data/ build/")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if len(sys.argv) > 2:
        output_dir = Path(sys.argv[2])
    else:
        output_dir = Path(".")

    if not input_path.exists():
        print(f"Error: Input path not found: {input_path}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Senior Regular and Bold share one compiled glyph set, one feature file, and one set of layout tables. The glyph set and feature code are made here. The layout tables, which need the feature code, are compiled in a pool worker beside the mono and Junior builds. The two Senior jobs then only draw outlines.
    parent_data = load_glyph_data(input_path)
    pixel_size: int = parent_data.get("metadata", {})["pixel_size"]
    senior_compiled = compile_glyph_set(parent_data, "senior")
    shared_senior_fea = build_senior_fea(parent_data, senior_compiled.join_glyphs, pixel_size, pixel_size)

    families = (
        ("AbbotsMortonSpaceportMono", "mono"),
        ("AbbotsMortonSpaceportSansJunior", "junior"),
        ("AbbotsMortonSpaceportSansSenior", "senior"),
    )
    jobs: list[tuple[str, str, str, bool]] = []
    for style_suffix, bold in (("-Regular", False), ("-Bold", True)):
        for file_prefix, variant in families:
            output_path = output_dir / f"{file_prefix}{style_suffix}.otf"
            jobs.append((str(input_path), str(output_path), variant, bold))
    other_jobs = [job for job in jobs if job[2] != "senior"]
    senior_jobs = [job for job in jobs if job[2] == "senior"]

    # Half the machine's cores, because the pool often shares the machine: when the test suite's `conftest.py` runs this build, pyright runs beside it, and `build_check_html.py` runs that suite in-process.
    max_workers = max(1, min(len(other_jobs) + 1, usable_cores() // 2))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        otl_future = executor.submit(
            _compile_senior_otl_job, str(input_path), senior_compiled, shared_senior_fea
        )
        futures = [executor.submit(_build_one_font, *job) for job in other_jobs]
        senior_otl = otl_future.result()
        futures.extend(
            executor.submit(_build_one_font, *job, senior_compiled, shared_senior_fea, senior_otl)
            for job in senior_jobs
        )
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
