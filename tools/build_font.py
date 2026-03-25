#!/usr/bin/env python3
"""
Build a pixel font from bitmap glyph definitions.
Uses fonttools FontBuilder to create OTF output.

Usage:
    uv run python tools/build_font.py <glyph_data.yaml|glyph_data/> [output_dir]

    The first argument can be a single YAML file or a directory of YAML files.
    When a directory is given, all *.yaml files are loaded and merged.

Outputs:
    output_dir/AbbotsMortonSpaceportMono.otf        - Monospace font
    output_dir/AbbotsMortonSpaceportSansJunior.otf  - Proportional font (no cursive/calt)
    output_dir/AbbotsMortonSpaceportSansSenior.otf  - Proportional font (with cursive/calt)
"""

import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import newTable
from fontTools.ttLib.tables._c_m_a_p import cmap_format_14


def load_postscript_glyph_names() -> dict:
    """Load PostScript glyph name to Unicode codepoint mapping from YAML."""
    path = Path(__file__).parent.parent / "postscript_glyph_names.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_glyph_data(path: Path) -> dict:
    """Load glyph definitions from a YAML file or directory of YAML files."""
    if path.is_dir():
        metadata = {}
        glyphs = {}
        kerning_defs = {}
        for yaml_file in sorted(path.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if data and "metadata" in data:
                metadata = data["metadata"]
            if data and "glyphs" in data:
                glyphs.update(data["glyphs"])
            if data and "kerning" in data:
                kerning_defs.update(data["kerning"])
        return {"metadata": metadata, "glyphs": glyphs, "kerning": kerning_defs}
    else:
        with open(path) as f:
            return yaml.safe_load(f)


def _resolve_codepoint(glyph_name: str, postscript_names: dict) -> int | None:
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
            return cp if cp > 0xFFFF else None
        except ValueError:
            return None
    return postscript_names.get(glyph_name)


def build_cmap14(variation_sequences: dict, glyphs_def: dict, name_to_codepoint: dict):
    """Build a cmap format 14 subtable for Unicode Variation Sequences."""
    if not variation_sequences:
        return None

    uvsDict = {}
    for vs_cp, mappings in variation_sequences.items():
        entries = []
        for base_name, target_name in mappings.items():
            base_cp = name_to_codepoint.get(base_name)
            if base_cp is None:
                continue
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
    subtable.platformID = 0
    subtable.platEncID = 5
    subtable.language = 0
    subtable.cmap = {}
    subtable.uvsDict = uvsDict
    return subtable


def is_proportional_glyph(glyph_name: str) -> bool:
    """Check if a glyph is a proportional variant."""
    return glyph_name.endswith(".prop") or ".prop." in glyph_name or ".fina" in glyph_name


def get_base_glyph_name(prop_glyph_name: str) -> str:
    """Get the base glyph name from a proportional glyph name.

    Strips .prop from the end or middle of the name:
      qsPea.prop       → qsPea
      qsFee_qsMay.prop → qsFee_qsMay
      U.prop.narrow    → U.narrow
    """
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    if ".prop." in prop_glyph_name:
        return prop_glyph_name.replace(".prop.", ".", 1)
    return prop_glyph_name


def prepare_proportional_glyphs(glyphs_def: dict) -> dict:
    """
    Prepare glyph definitions for the proportional font variant.

    For the proportional font:
    - .prop glyphs are renamed to their base names (e.g., qsPea.prop → qsPea)
    - Base glyphs that have .prop variants are excluded
    - Glyphs without .prop variants remain unchanged
    - Glyph name references inside definitions are updated accordingly
    """
    # Build rename map: old .prop name → new base name
    rename_map: dict[str, str] = {}
    prop_base_names = set()
    for glyph_name in glyphs_def.keys():
        if is_proportional_glyph(glyph_name):
            base_name = get_base_glyph_name(glyph_name)
            prop_base_names.add(base_name)
            rename_map[glyph_name] = base_name

    def _rename_refs(glyph_def: dict | None) -> dict | None:
        if not glyph_def or not rename_map:
            return glyph_def
        list_keys = (
            "calt_after", "calt_before", "calt_not_after", "calt_not_before",
            "extend_entry_after", "extend_exit_before",
            "doubly_extend_entry_after", "doubly_extend_exit_before",
            "noentry_after",
        )
        scalar_keys = ("base",)
        changed = False
        for key in list_keys:
            val = glyph_def.get(key)
            if not val:
                continue
            new_val = [rename_map.get(g, g) for g in val]
            if new_val != list(val):
                if not changed:
                    glyph_def = dict(glyph_def)
                    changed = True
                glyph_def[key] = new_val
        for key in scalar_keys:
            val = glyph_def.get(key)
            if val and val in rename_map:
                if not changed:
                    glyph_def = dict(glyph_def)
                    changed = True
                glyph_def[key] = rename_map[val]
        return glyph_def

    # Build new glyph dict
    new_glyphs = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            # Rename .prop glyph to its base name
            base_name = get_base_glyph_name(glyph_name)
            new_glyphs[base_name] = _rename_refs(glyph_def)
        elif glyph_name in prop_base_names:
            # Skip base glyphs that have .prop variants
            continue
        else:
            # Keep glyphs without .prop variants unchanged
            new_glyphs[glyph_name] = _rename_refs(glyph_def)

    return new_glyphs


def collect_kerning_groups(glyphs_def: dict) -> dict[str, list[str]]:
    """Build a mapping of {tag_name: [glyph_name, ...]} from kerning tags on glyphs."""
    groups = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        for tag in glyph_def.get("kerning", []):
            groups.setdefault(tag, []).append(glyph_name)
    return groups


def generate_kern_fea(
    kerning_defs: dict,
    kerning_groups: dict[str, list[str]],
    all_glyph_names: list[str],
    pixel_width: int,
) -> str:
    """Generate OpenType feature code for kern feature from kerning definitions."""
    lines = ["feature kern {"]
    for tag_name, definition in kerning_defs.items():
        if "left" in definition:
            left_glyphs = definition["left"]
        else:
            excluded = set(kerning_groups.get(tag_name, []))
            left_glyphs = [g for g in all_glyph_names if g not in excluded]
        if not left_glyphs:
            continue
        right_glyphs = definition["right"]
        value = definition["value"] * pixel_width
        left = " ".join(sorted(left_glyphs))
        right = " ".join(right_glyphs)
        lines.append(f"    lookup kern_{tag_name} {{")
        lines.append(f"        pos [{left}] [{right}] {value};")
        lines.append(f"    }} kern_{tag_name};")
    lines.append("} kern;")
    return "\n".join(lines)


def generate_mark_fea(glyphs_def: dict, pixel_width: int, pixel_height: int) -> str | None:
    """Generate OpenType feature code for mark positioning (combining diacriticals).

    Scans glyphs_def for marks (is_mark: true) and base glyphs with
    top_mark_y / bottom_mark_y anchors, then emits a GPOS 'mark' feature.

    Returns the FEA string, or None if there are no marks.
    """
    # Collect mark glyphs, split into top vs bottom.
    # Marks with base_x_adjust or base_y_adjust get their own mark class and lookup.
    top_marks = {}       # glyph_name -> (anchor_x, anchor_y)
    bottom_marks = {}
    adjusted_marks = {}  # glyph_name -> (anchor_x, anchor_y, is_top, base_x_adjust, base_y_adjust)
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or not glyph_def.get("is_mark"):
            continue
        bitmap = glyph_def.get("bitmap", [])
        y_offset = glyph_def.get("y_offset", 0)
        # Mark anchor x = 0 (bitmap is centered on origin by zero-width drawing)
        anchor_x = 0
        # Resolve base_x_adjust with mark-only overrides
        base_x_adjust = glyph_def.get("base_x_adjust")
        mark_x_override = glyph_def.get("mark_base_x_adjust")
        if mark_x_override and base_x_adjust:
            base_x_adjust = {**base_x_adjust, **mark_x_override}
        elif mark_x_override:
            base_x_adjust = mark_x_override
        # Resolve base_y_adjust with mark-only overrides
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
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, True, base_x_adjust or {}, base_y_adjust or {})
            else:
                top_marks[glyph_name] = (anchor_x, anchor_y)
        else:
            # Bottom mark: anchor at the top of the drawn pixels
            bitmap_height = len(bitmap) if bitmap else 0
            anchor_y = (y_offset + bitmap_height) * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, False, base_x_adjust or {}, base_y_adjust or {})
            else:
                bottom_marks[glyph_name] = (anchor_x, anchor_y)

    if not top_marks and not bottom_marks and not adjusted_marks:
        return None

    # Collect base glyphs with anchors
    top_bases = {}    # glyph_name -> (anchor_x, anchor_y)
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

    # Emit markClass definitions for standard marks
    for glyph_name in sorted(top_marks):
        ax, ay = top_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_top;")
    for glyph_name in sorted(bottom_marks):
        ax, ay = bottom_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_bottom;")
    # Emit markClass definitions for adjusted marks (each gets its own class)
    for glyph_name in sorted(adjusted_marks):
        ax, ay, _, _, _ = adjusted_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_{glyph_name};")

    # Emit lookup for standard top marks
    if top_marks and top_bases:
        lines.append("")
        lines.append("    lookup mark_top {")
        for glyph_name in sorted(top_bases):
            bx, by = top_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_top;")
        lines.append("    } mark_top;")

    # Emit lookup for standard bottom marks
    if bottom_marks and bottom_bases:
        lines.append("")
        lines.append("    lookup mark_bottom {")
        for glyph_name in sorted(bottom_bases):
            bx, by = bottom_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_bottom;")
        lines.append("    } mark_bottom;")

    # Emit lookups for adjusted marks (per-base anchor overrides)
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
            lines.append(f"        pos base {glyph_name} <anchor {int(bx + x_adj)} {int(by + y_adj)}> mark @mark_{mark_name};")
        lines.append(f"    }} mark_{mark_name};")

    lines.append("} mark;")
    return "\n".join(lines)


def _normalize_anchors(raw) -> list[list[int]]:
    """Normalize a single [x, y] or list of [x, y] pairs to a list of pairs."""
    if raw is None:
        return []
    if isinstance(raw[0], list):
        return raw
    return [raw]


def _is_entry_variant(glyph_name: str) -> bool:
    """Check if a glyph name contains an entry-* modifier segment."""
    return any(p.startswith("entry-") for p in glyph_name.split(".")[1:])


def _is_exit_variant(glyph_name: str) -> bool:
    """Check if a glyph name contains an exit-* modifier segment."""
    return any(p.startswith("exit-") for p in glyph_name.split(".")[1:])


def _extended_entry_suffix(glyph_name: str) -> str | None:
    """Return the .entry-extended* or .entry-doubly-extended* suffix segment if present, else None."""
    for part in glyph_name.split(".")[1:]:
        if part.startswith("entry-extended") or part.startswith("entry-doubly-extended"):
            return "." + part
    return None


def _extended_exit_suffix(glyph_name: str) -> str | None:
    """Return the .exit-extended* or .exit-doubly-extended* suffix segment if present, else None."""
    for part in glyph_name.split(".")[1:]:
        if part.startswith("exit-extended") or part.startswith("exit-doubly-extended"):
            return "." + part
    return None


_EXTENDED_HEIGHT_LABELS = {0: "baseline", 5: "xheight", 6: "y6", 8: "top"}


def _is_contextual_variant(glyph_name: str) -> bool:
    """Check if a glyph name is a contextual variant (entry-*, exit-*, or half)."""
    parts = glyph_name.split(".")[1:]
    return any(
        p.startswith("entry-") or p.startswith("exit-") or p == "half"
        for p in parts
    )


def generate_calt_fea(glyphs_def: dict, pixel_width: int) -> str | None:
    """Generate OpenType feature code for contextual alternates (calt).

    Generates two kinds of contextual substitution rules:

    1. Backward-looking: scans for entry-* variants with cursive_entry anchors,
       substitutes based on the preceding glyph's exit height.
    2. Forward-looking: scans for exit-* variants with cursive_exit anchors,
       substitutes based on the following glyph's entry height. These run after
       backward rules, so they only fire for glyphs not already substituted.

    Returns the FEA string, or None if no contextual variants are found.
    """
    # --- Backward-looking: entry variants keyed by entry Y ---
    bk_replacements: dict[str, dict[int, str]] = {}
    bk_exclusions: dict[str, dict[int, list[str]]] = {}
    # Pair-specific overrides: entry variants with calt_after lists
    pair_overrides: dict[str, list[tuple[str, list[str]]]] = {}
    # Each entry is (entry_exit_var, entry_only_var, exit_y, not_before_glyphs)
    fwd_upgrades: dict[str, list[tuple[str, str, int, list[str]]]] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        if glyph_def.get("calt_word_final"):
            continue
        if not _is_entry_variant(glyph_name):
            if not glyph_def.get("cursive_entry"):
                continue
            parts = glyph_name.split(".")[1:]
            if "half" not in parts and "alt" not in parts and not glyph_def.get("calt_after"):
                continue
        raw_entry = glyph_def.get("cursive_entry")
        if raw_entry is None:
            continue
        entries = _normalize_anchors(raw_entry)
        if not entries:
            continue
        entry_y = entries[0][1]
        base_name = glyph_name.split(".")[0]
        if base_name not in glyphs_def:
            continue
        if "alt" in glyph_name.split(".")[1:]:
            base_def = glyphs_def.get(base_name)
            if base_def:
                raw = base_def.get("cursive_entry")
                if raw and entry_y in {a[1] for a in _normalize_anchors(raw)}:
                    continue
        if glyph_def.get("calt_before"):
            continue
        calt_after = glyph_def.get("calt_after")
        if calt_after:
            pair_overrides.setdefault(base_name, []).append(
                (glyph_name, list(calt_after))
            )
        elif _extended_entry_suffix(glyph_name) is not None:
            pass
        elif _extended_exit_suffix(glyph_name) is not None:
            pass
        else:
            existing = bk_replacements.get(base_name, {}).get(entry_y)
            if existing is not None:
                existing_def = glyphs_def.get(existing, {}) or {}
                existing_has_exit = existing_def.get("cursive_exit") is not None
                new_has_exit = glyph_def.get("cursive_exit") is not None
                if existing_has_exit != new_has_exit:
                    if new_has_exit:
                        exit_y_val = _normalize_anchors(glyph_def["cursive_exit"])[0][1]
                        nb = glyph_def.get("calt_not_before", [])
                        fwd_upgrades.setdefault(base_name, []).append(
                            (glyph_name, existing, exit_y_val, list(nb))
                        )
                    else:
                        exit_y_val = _normalize_anchors(existing_def["cursive_exit"])[0][1]
                        nb = existing_def.get("calt_not_before", [])
                        fwd_upgrades.setdefault(base_name, []).append(
                            (existing, glyph_name, exit_y_val, list(nb))
                        )
                        bk_replacements[base_name][entry_y] = glyph_name
                else:
                    bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name
            else:
                bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name
            not_after = glyph_def.get("calt_not_after")
            if not_after:
                resolved = [get_base_glyph_name(g) if g not in glyphs_def else g for g in not_after]
                bk_exclusions.setdefault(base_name, {})[entry_y] = resolved

    # --- Pair override upgrades ---
    # When two pair overrides share the same base and calt_after list but
    # differ in having a cursive_exit, register an upgrade: the entry-only
    # variant is selected by the backward pair rule, and a forward upgrade
    # rule later converts it to the entry+exit variant when needed.
    for base_name, overrides in pair_overrides.items():
        by_after: dict[tuple, list[tuple[str, list[str]]]] = {}
        for vn, after in overrides:
            key = tuple(sorted(after))
            by_after.setdefault(key, []).append((vn, after))
        for group in by_after.values():
            with_exit = []
            without_exit = []
            for vn, after in group:
                vdef = glyphs_def.get(vn) or {}
                if vdef.get("cursive_exit"):
                    with_exit.append((vn, vdef))
                else:
                    without_exit.append((vn, vdef))
            if with_exit and without_exit:
                entry_only_var = without_exit[0][0]
                entry_exit_var = with_exit[0][0]
                exit_y = _normalize_anchors(with_exit[0][1]["cursive_exit"])[0][1]
                nb = (with_exit[0][1] or {}).get("calt_not_before", [])
                fwd_upgrades.setdefault(base_name, []).append(
                    (entry_exit_var, entry_only_var, exit_y, list(nb))
                )

    # --- Forward-looking: exit variants keyed by exit Y ---
    # Detects any variant with cursive_exit (catches .exit-* names and
    # .half variants alike). Entry variants (entry-* names) are excluded
    # since they are handled by the backward-looking rules.
    fwd_replacements: dict[str, dict[int, str]] = {}
    fwd_exclusions: dict[str, dict[int, list[str]]] = {}
    # Pair-specific forward overrides: variants with calt_before lists
    # Each entry is (variant_name, before_glyphs, not_after_glyphs)
    fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str]]]] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or "." not in glyph_name:
            continue
        if glyph_name.endswith(".noentry"):
            continue
        if _extended_entry_suffix(glyph_name) is not None:
            continue
        if _extended_exit_suffix(glyph_name) is not None and not glyph_def.get("calt_before"):
            continue
        if _is_entry_variant(glyph_name) and not glyph_def.get("calt_before"):
            continue
        if glyph_def.get("calt_word_final"):
            continue
        if glyph_def.get("calt_after"):
            continue
        parts = glyph_name.split(".")[1:]
        if "half" in parts and glyph_def.get("cursive_entry") and not glyph_def.get("calt_before"):
            continue
        extra_parts = [p for p in parts if p not in ("alt", "prop")]
        if extra_parts and "alt" in parts and glyph_def.get("cursive_entry") and not glyph_def.get("calt_before"):
            continue
        raw_exit = glyph_def.get("cursive_exit")
        if raw_exit is None:
            continue
        exits = _normalize_anchors(raw_exit)
        if not exits:
            continue
        exit_y = exits[0][1]
        base_name = glyph_name.split(".")[0]
        if base_name not in glyphs_def:
            continue
        calt_before = glyph_def.get("calt_before")
        if calt_before:
            resolved = [get_base_glyph_name(g) if g not in glyphs_def else g for g in calt_before]
            not_after = glyph_def.get("calt_not_after")
            resolved_not_after = (
                [get_base_glyph_name(g) if g not in glyphs_def else g for g in not_after]
                if not_after else []
            )
            fwd_pair_overrides.setdefault(base_name, []).append(
                (glyph_name, resolved, resolved_not_after)
            )
        else:
            fwd_replacements.setdefault(base_name, {})[exit_y] = glyph_name
            not_before = glyph_def.get("calt_not_before")
            if not_before:
                resolved = [get_base_glyph_name(g) if g not in glyphs_def else g for g in not_before]
                fwd_exclusions.setdefault(base_name, {})[exit_y] = resolved

    if not bk_replacements and not fwd_replacements:
        return None

    def _expand_all_variants(glyphs, *, include_base=False):
        """Expand glyph names to include all known variants from replacement dicts."""
        expanded = set(glyphs)
        for g in glyphs:
            base = g.split(".")[0]
            if include_base:
                expanded.add(base)
            if base in bk_replacements:
                expanded.update(bk_replacements[base].values())
            if base in fwd_replacements:
                expanded.update(fwd_replacements[base].values())
            if base in pair_overrides:
                expanded.update(pv for pv, _ in pair_overrides[base])
            if base in fwd_pair_overrides:
                expanded.update(pv for pv, _, _ in fwd_pair_overrides[base])
        return expanded

    # --- Build terminal sets ---
    # Entry-only terminal: has cursive_entry but no cursive_exit, and no
    # sibling variant has both entry at the same Y and any exit (i.e., no
    # upgrade path exists).  Exit-only terminal: the reverse.
    _base_anchors: dict[str, list[tuple[str, set[int], set[int]]]] = {}
    for gn, gd in glyphs_def.items():
        if gd is None or gn.endswith(".noentry"):
            continue
        eys = {a[1] for a in _normalize_anchors(gd.get("cursive_entry"))}
        exs = {a[1] for a in _normalize_anchors(gd.get("cursive_exit"))}
        _base_anchors.setdefault(gn.split(".")[0], []).append((gn, eys, exs))

    terminal_entry_only: set[str] = set()
    terminal_exit_only: set[str] = set()
    for base, siblings in _base_anchors.items():
        for gn, eys, exs in siblings:
            if eys and not exs:
                for y in eys:
                    if not any(
                        sn != gn and y in se and sx
                        for sn, se, sx in siblings
                    ):
                        terminal_entry_only.add(gn)
                        break
            if exs and not eys:
                for y in exs:
                    if not any(
                        sn != gn and y in sx2 and se2
                        for sn, se2, sx2 in siblings
                    ):
                        terminal_exit_only.add(gn)
                        break

    # --- Build exit classes (for backward rules) ---
    exit_classes: dict[int, set[str]] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or glyph_name.endswith(".noentry"):
            continue
        raw_exit = glyph_def.get("cursive_exit")
        if raw_exit is None:
            continue
        for anchor in _normalize_anchors(raw_exit):
            exit_classes.setdefault(anchor[1], set()).add(glyph_name)

    # --- Build entry classes (for forward rules) ---
    # Includes both glyphs with explicit cursive_entry anchors and base
    # glyphs that have entry-* variants (so forward rules can match the
    # base glyph before the backward rule substitutes it).
    entry_classes: dict[int, set[str]] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        raw_entry = glyph_def.get("cursive_entry")
        if raw_entry is None:
            continue
        for anchor in _normalize_anchors(raw_entry):
            entry_classes.setdefault(anchor[1], set()).add(glyph_name)
            is_bk = _is_entry_variant(glyph_name)
            if not is_bk:
                parts = glyph_name.split(".")[1:]
                is_bk = ("half" in parts or "alt" in parts) and glyph_def.get("cursive_entry") is not None
            if is_bk:
                base_name = glyph_name.split(".")[0]
                if base_name in glyphs_def and anchor[1] in bk_replacements.get(base_name, {}):
                    entry_classes[anchor[1]].add(base_name)

    # --- Add exit-only variants to entry classes ---
    # If a base glyph is in an entry class (because it has entry variants),
    # its exit-only variants should also be in the class — they can be
    # reverse-upgraded to entry+exit variants by a later backward rule.
    for base_name in fwd_upgrades:
        for entry_exit_var, entry_only_var, exit_y, _ in fwd_upgrades[base_name]:
            entry_def = glyphs_def.get(entry_only_var, {}) or {}
            raw_entry = entry_def.get("cursive_entry")
            if not raw_entry:
                continue
            entry_y_val = _normalize_anchors(raw_entry)[0][1]
            exit_only_var = fwd_replacements.get(base_name, {}).get(exit_y)
            if exit_only_var and entry_y_val in entry_classes:
                entry_classes[entry_y_val].add(exit_only_var)

    # --- Add forward-selected exit-only variants to entry classes ---
    # When the cycle's forward rules convert a glyph to an exit-only variant
    # (e.g., qsIt → qsIt.exit-baseline), later overrides still need to
    # recognise the converted glyph as "entering" at the base's entry height.
    for base_name, fwd_vars in fwd_replacements.items():
        base_entry_ys = {y for y, members in entry_classes.items() if base_name in members}
        if not base_entry_ys:
            continue
        for fwd_exit_y, fwd_var in fwd_vars.items():
            fwd_var_def = glyphs_def.get(fwd_var, {}) or {}
            if fwd_var_def.get("cursive_entry"):
                continue
            for y in base_entry_ys:
                entry_classes[y].add(fwd_var)

    # --- Exclusive entry classes (for restricted forward rules) ---
    # A glyph is in @entry_only_yN when it enters at y=N but at NO other
    # height.  Forward rules use this restricted class when the selected
    # variant is also a backward variant — preventing the forward rule from
    # firing when the next glyph could enter at a different height that
    # the backward rule would prefer.
    entry_exclusive: dict[int, set[str]] = {}
    all_entry_ys = set(entry_classes.keys())
    for y in all_entry_ys:
        exclusive = set(entry_classes[y])
        for other_y in all_entry_ys:
            if other_y != y:
                exclusive -= entry_classes[other_y]
        entry_exclusive[y] = exclusive

    fwd_use_exclusive: set[tuple[str, int]] = set()
    for base_name in fwd_replacements:
        if base_name in bk_replacements:
            bk_variant_names = set(bk_replacements[base_name].values())
            for exit_y, variant_name in fwd_replacements[base_name].items():
                if variant_name in bk_variant_names:
                    fwd_use_exclusive.add((base_name, exit_y))
        base_def = glyphs_def.get(base_name, {}) or {}
        raw = base_def.get("cursive_exit")
        if raw:
            base_exit_ys = {a[1] for a in _normalize_anchors(raw)}
            min_base_exit = min(base_exit_ys)
            for exit_y, variant_name in fwd_replacements[base_name].items():
                if exit_y not in base_exit_ys and exit_y < min_base_exit:
                    fwd_use_exclusive.add((base_name, exit_y))

    # --- preferred_over: two-glyph lookahead for exclusive variants ---
    # When a variant has preferred_over and uses exclusive matching, add a
    # two-glyph rule so it also fires when the next glyph is ambiguous but
    # the glyph after that exclusively enters at the sibling's exit height.
    fwd_preferred_lookahead: dict[str, list[tuple[str, int, int]]] = {}
    for base_name in fwd_replacements:
        for exit_y, variant_name in fwd_replacements[base_name].items():
            if (base_name, exit_y) not in fwd_use_exclusive:
                continue
            variant_def = glyphs_def.get(variant_name, {}) or {}
            preferred_over = variant_def.get("preferred_over")
            if not preferred_over:
                continue
            base_def = glyphs_def.get(base_name, {}) or {}
            base_exit = base_def.get("cursive_exit")
            if base_exit:
                sibling_exit_y = _normalize_anchors(base_exit)[0][1]
            else:
                for sibling in preferred_over:
                    sibling_def = glyphs_def.get(sibling, {}) or {}
                    raw = sibling_def.get("cursive_exit")
                    if raw:
                        sibling_exit_y = _normalize_anchors(raw)[0][1]
                        break
                else:
                    continue
            if sibling_exit_y != exit_y:
                fwd_preferred_lookahead.setdefault(base_name, []).append(
                    (variant_name, exit_y, sibling_exit_y)
                )

    # --- Topological sort for backward-looking lookups ---
    # Consider exit heights INTRODUCED by both entry (backward) and exit
    # (forward) variants — any variant that changes the base glyph's exit
    # height creates a dependency for bases whose backward rules consume
    # that height.
    base_exit_ys: dict[str, set[int]] = {}
    for base_name in bk_replacements:
        base_def = glyphs_def.get(base_name, {})
        base_ys = set()
        if base_def:
            raw_exit = base_def.get("cursive_exit")
            if raw_exit:
                for anchor in _normalize_anchors(raw_exit):
                    base_ys.add(anchor[1])
        new_exit_ys = set()
        all_variants = list(bk_replacements[base_name].values())
        if base_name in fwd_replacements:
            all_variants.extend(fwd_replacements[base_name].values())
        for variant_name in all_variants:
            variant_def = glyphs_def.get(variant_name, {})
            if variant_def:
                raw_exit = variant_def.get("cursive_exit")
                if raw_exit:
                    for anchor in _normalize_anchors(raw_exit):
                        if anchor[1] not in base_ys:
                            new_exit_ys.add(anchor[1])
        base_exit_ys[base_name] = new_exit_ys

    base_order = list(bk_replacements.keys())
    edges: dict[str, set[str]] = {b: set() for b in base_order}
    for base_a in base_order:
        for base_b in base_order:
            if base_a == base_b:
                continue
            b_entry_ys = set(bk_replacements[base_b].keys())
            if base_exit_ys[base_a] & b_entry_ys:
                edges[base_b].add(base_a)

    # Kahn's algorithm: topological sort that collects cycle members
    # instead of raising an error.  Cycle members are merged into a
    # single lookup so left-to-right rule processing resolves them.
    out_edges: dict[str, set[str]] = {b: set() for b in base_order}
    in_degree: dict[str, int] = {b: len(edges[b]) for b in base_order}
    for b in base_order:
        for dep in edges[b]:
            out_edges[dep].add(b)

    queue = deque(sorted(b for b in base_order if in_degree[b] == 0))
    sorted_bases: list[str] = []
    while queue:
        node = queue.popleft()
        sorted_bases.append(node)
        for neighbor in sorted(out_edges[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    cycle_bases: set[str] = set(base_order) - set(sorted_bases)
    sorted_bases.extend(sorted(cycle_bases))

    # --- Generate FEA ---
    bk_used_ys = set()
    for variants in bk_replacements.values():
        bk_used_ys.update(variants.keys())

    fwd_used_ys = set()
    for variants in fwd_replacements.values():
        fwd_used_ys.update(variants.keys())
    for upgrade_list in fwd_upgrades.values():
        for _, _, exit_y, _ in upgrade_list:
            fwd_used_ys.add(exit_y)
    for entries in fwd_preferred_lookahead.values():
        for _, exit_y, sibling_y in entries:
            fwd_used_ys.add(sibling_y)

    lines = ["feature calt {"]

    for y in sorted(bk_used_ys):
        if y in exit_classes:
            members = sorted(exit_classes[y])
            lines.append(f"    @exit_y{y} = [{' '.join(members)}];")

    for y in sorted(fwd_used_ys):
        if y in entry_classes:
            members = sorted(entry_classes[y])
            lines.append(f"    @entry_y{y} = [{' '.join(members)}];")
        preferred_needs_excl = any(
            sy == y for entries in fwd_preferred_lookahead.values() for _, _, sy in entries
        )
        needs_excl = any(ey == y for _, ey in fwd_use_exclusive) or preferred_needs_excl
        if needs_excl and y in entry_exclusive:
            excl_members = sorted(entry_exclusive[y])
            if excl_members:
                lines.append(f"    @entry_only_y{y} = [{' '.join(excl_members)}];")

    # ZWNJ chain-breaking: substitute glyphs after ZWNJ with .noentry
    # variants so the curs feature sees NULL entry and breaks the chain.
    zwnj = "uni200C"
    noentry_pairs = []
    for name in sorted(glyphs_def):
        if name.endswith(".noentry"):
            base = name[:-len(".noentry")]
            if base in glyphs_def:
                noentry_pairs.append((base, name))
    if zwnj in glyphs_def and noentry_pairs:
        cursive_names = [b for b, _ in noentry_pairs]
        noentry_names = [n for _, n in noentry_pairs]
        lines.append(f"    @qs_has_entry = [{' '.join(cursive_names)}];")
        lines.append(f"    @qs_noentry = [{' '.join(noentry_names)}];")
        lines.append("")
        lines.append("    lookup calt_zwnj {")
        lines.append(f"        sub {zwnj} @qs_has_entry' by @qs_noentry;")
        lines.append("    } calt_zwnj;")

    # Word-final substitutions: mark glyphs as word-final, then revert
    # when followed by another Quikscript letter. Net effect: .fina
    # variants only survive at end-of-word (before space/punctuation/EOT).
    word_final_pairs = {}
    for name, gdef in glyphs_def.items():
        if gdef and gdef.get("calt_word_final"):
            base = name.split(".")[0]
            if base in glyphs_def:
                word_final_pairs[base] = name

    if word_final_pairs:
        excluded_bases = {"qsAngleParenLeft", "qsAngleParenRight"}
        qs_letter_names = set()
        for name in glyphs_def:
            if not name.startswith("qs"):
                continue
            base = name.split(".")[0]
            if base in excluded_bases:
                continue
            if "." not in name and "_" not in name:
                qs_letter_names.add(name)
        qs_letter_names.update(word_final_pairs.values())
        qs_letters_sorted = " ".join(sorted(qs_letter_names))

        lines.append("")
        lines.append(f"    @qs_letters = [{qs_letters_sorted}];")

        for base, variant in sorted(word_final_pairs.items()):
            safe = variant.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_word_final_{safe} {{")
            lines.append(f"        sub {base} by {variant};")
            lines.append(f"    }} calt_word_final_{safe};")
            lines.append("")
            lines.append(f"    lookup calt_word_final_revert_{safe} {{")
            lines.append(f"        sub {variant}' @qs_letters by {base};")
            lines.append(f"    }} calt_word_final_revert_{safe};")

    # Lookup ordering: forward-only bases run first so they can change a
    # glyph's exit before backward rules for the *following* glyph commit to
    # an entry variant.  For bases with both backward and forward rules, the
    # backward lookup runs first (so a preceding exit wins over a following
    # entry), then its forward companion.
    _base_to_variants: dict[str, set[str]] = {}
    for _gn in glyphs_def:
        _base_to_variants.setdefault(_gn.split(".")[0], set()).add(_gn)

    def _expand_exclusions(eg_list: list[str]) -> set[str]:
        expanded = set()
        for eg in eg_list:
            eg_base = eg.split(".")[0]
            expanded.update(_base_to_variants.get(eg_base, ()))
        return expanded

    def _emit_fwd_pairs(base_name: str):
        if base_name in fwd_pair_overrides:
            for variant_name, before_glyphs, not_after_glyphs in fwd_pair_overrides[base_name]:
                expanded_before = _expand_all_variants(before_glyphs)
                before_list = " ".join(sorted(expanded_before))

                targets = {base_name}
                if base_name in bk_replacements:
                    targets.update(bk_replacements[base_name].values())
                if base_name in fwd_replacements:
                    targets.update(fwd_replacements[base_name].values())
                if base_name in pair_overrides:
                    for pv, _ in pair_overrides[base_name]:
                        targets.add(pv)

                variant_def = glyphs_def.get(variant_name, {}) or {}
                variant_raw = variant_def.get("cursive_entry")
                variant_entry_ys = (
                    {a[1] for a in _normalize_anchors(variant_raw)}
                    if variant_raw else None
                )

                expanded_not_after = _expand_all_variants(not_after_glyphs, include_base=True)

                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_fwd_pair_{safe} {{")
                for target in sorted(targets):
                    guard_list = None
                    target_def = glyphs_def.get(target, {}) or {}
                    target_raw = target_def.get("cursive_entry")
                    if variant_entry_ys is not None:
                        if target_raw:
                            target_entry_ys = {a[1] for a in _normalize_anchors(target_raw)}
                            if not target_entry_ys.issubset(variant_entry_ys):
                                incompatible_ys = target_entry_ys - variant_entry_ys
                                if incompatible_ys == target_entry_ys:
                                    continue
                                guard_glyphs = set()
                                for iy in incompatible_ys:
                                    guard_glyphs.update(exit_classes.get(iy, set()))
                                if guard_glyphs:
                                    guard_list = " ".join(sorted(guard_glyphs))
                    else:
                        if target_raw and _is_entry_variant(target):
                            target_exit_raw = target_def.get("cursive_exit")
                            if target_exit_raw:
                                target_exit_ys = {a[1] for a in _normalize_anchors(target_exit_raw)}
                                variant_exit_ys = {a[1] for a in _normalize_anchors(variant_def.get("cursive_exit", []))}
                                if variant_exit_ys <= target_exit_ys:
                                    continue
                            elif target_def.get("calt_after"):
                                continue
                        elif not target_raw:
                            variant_exit_raw = variant_def.get("cursive_exit")
                            if variant_exit_raw:
                                variant_exit_ys = {a[1] for a in _normalize_anchors(variant_exit_raw)}
                                base_for_target = target.split(".")[0]
                                protect_ys = set()
                                for bk_y, bk_var in bk_replacements.get(base_for_target, {}).items():
                                    bk_def = glyphs_def.get(bk_var, {}) or {}
                                    bk_exit_raw = bk_def.get("cursive_exit")
                                    if bk_exit_raw:
                                        bk_exit_ys = {a[1] for a in _normalize_anchors(bk_exit_raw)}
                                        if variant_exit_ys <= bk_exit_ys:
                                            protect_ys.add(bk_y)
                                if protect_ys:
                                    guard_glyphs = set()
                                    for py in protect_ys:
                                        guard_glyphs.update(exit_classes.get(py, set()))
                                    if guard_glyphs:
                                        guard_list = " ".join(sorted(guard_glyphs))
                    actual_variant = variant_name
                    suffix = _extended_entry_suffix(target)
                    if suffix:
                        extended = variant_name + suffix
                        if extended not in glyphs_def:
                            extended = variant_name + ".entry-extended"
                        if extended in glyphs_def:
                            actual_variant = extended
                    if guard_list:
                        lines.append(
                            f"        ignore sub [{guard_list}] {target}' [{before_list}];"
                        )
                    if expanded_not_after:
                        na_list = " ".join(sorted(expanded_not_after))
                        lines.append(
                            f"        ignore sub [{na_list}] {target}' [{before_list}];"
                        )
                    for teo in sorted(expanded_before & terminal_exit_only):
                        lines.append(f"        ignore sub {target}' {teo};")
                    lines.append(
                        f"        sub {target}' [{before_list}] by {actual_variant};"
                    )
                lines.append(f"    }} calt_fwd_pair_{safe};")

    def _emit_fwd_general(base_name: str):
        if base_name in fwd_replacements:
            variants = fwd_replacements[base_name]
            exclusions = fwd_exclusions.get(base_name, {})
            lookup_name = f"calt_fwd_{base_name}"
            lines.append("")
            lines.append(f"    lookup {lookup_name} {{")
            for exit_y in sorted(variants.keys(), reverse=True):
                variant_name = variants[exit_y]
                if exit_y not in entry_classes:
                    continue
                use_excl = (base_name, exit_y) in fwd_use_exclusive
                if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                    continue
                cls = f"@entry_only_y{exit_y}" if use_excl else f"@entry_y{exit_y}"
                base_ey = {y for y, m in entry_classes.items() if base_name in m}
                var_ey = {y for y, m in entry_classes.items() if variant_name in m}
                if var_ey:
                    for h in sorted(base_ey - var_ey):
                        if h in exit_classes:
                            lines.append(f"        ignore sub @exit_y{h} {base_name}' {cls};")
                excluded = _expand_exclusions(exclusions.get(exit_y, []))
                for eg in sorted(excluded):
                    lines.append(f"        ignore sub {base_name}' {eg};")
                lines.append(f"        sub {base_name}' {cls} by {variant_name};")
            if base_name in fwd_preferred_lookahead:
                for variant_name, exit_y, sibling_y in fwd_preferred_lookahead[base_name]:
                    if exit_y in entry_classes and sibling_y in entry_exclusive and entry_exclusive[sibling_y]:
                        lines.append(
                            f"        sub {base_name}' @entry_y{exit_y} @entry_only_y{sibling_y} by {variant_name};"
                        )
            lines.append(f"    }} {lookup_name};")

    def _emit_fwd(base_name: str):
        _emit_fwd_pairs(base_name)
        _emit_fwd_general(base_name)

    def _emit_bk_pairs(base_name: str):
        if base_name in pair_overrides:
            for variant_name, after_glyphs in pair_overrides[base_name]:
                expanded_after = _expand_all_variants(after_glyphs)
                suffix = _extended_entry_suffix(variant_name)
                if suffix:
                    label = suffix.removeprefix(".entry-extended-at-")
                    entry_y = next(
                        (y for y, lbl in _EXTENDED_HEIGHT_LABELS.items() if lbl == label),
                        None,
                    )
                    if entry_y is not None and entry_y in exit_classes:
                        expanded_after &= exit_classes[entry_y]
                after_list = " ".join(sorted(expanded_after))
                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_pair_{safe} {{")
                variant_def = glyphs_def.get(variant_name, {}) or {}
                not_before = variant_def.get("calt_not_before", [])
                if not_before:
                    resolved = [get_base_glyph_name(g) if g not in glyphs_def else g for g in not_before]
                    for nb in sorted(_expand_exclusions(resolved)):
                        lines.append(f"        ignore sub [{after_list}] {base_name}' {nb};")
                for tei in sorted(expanded_after & terminal_entry_only):
                    lines.append(f"        ignore sub {tei} {base_name}';")
                lines.append(
                    f"        sub [{after_list}] {base_name}' by {variant_name};"
                )
                lines.append(f"    }} calt_pair_{safe};")

    def _emit_bk_general(base_name: str):
        if base_name in bk_replacements:
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            lookup_name = f"calt_{base_name}"
            lines.append("")
            lines.append(f"    lookup {lookup_name} {{")
            for entry_y in sorted(variants.keys()):
                variant_name = variants[entry_y]
                if entry_y in exit_classes:
                    for eg in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                        lines.append(f"        ignore sub {eg} {base_name}';")
                    lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
            lines.append(f"    }} {lookup_name};")

    def _emit_bk_cycle(bases: list[str]):
        lines.append("")
        lines.append("    lookup calt_cycle {")
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            for entry_y in sorted(variants.keys()):
                variant_name = variants[entry_y]
                if entry_y in exit_classes:
                    excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                    if excluded:
                        filtered = sorted(exit_classes[entry_y] - excluded)
                        if filtered:
                            member_list = " ".join(filtered)
                            lines.append(f"        sub [{member_list}] {base_name}' by {variant_name};")
                    else:
                        lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
        for base_name in bases:
            if base_name in fwd_replacements:
                variants = fwd_replacements[base_name]
                exclusions = fwd_exclusions.get(base_name, {})
                for exit_y in sorted(variants.keys(), reverse=True):
                    variant_name = variants[exit_y]
                    if exit_y not in entry_classes:
                        continue
                    use_excl = (base_name, exit_y) in fwd_use_exclusive
                    if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                        continue
                    cls = f"@entry_only_y{exit_y}" if use_excl else f"@entry_y{exit_y}"
                    base_ey = {y for y, m in entry_classes.items() if base_name in m}
                    var_ey = {y for y, m in entry_classes.items() if variant_name in m}
                    if var_ey:
                        for h in sorted(base_ey - var_ey):
                            if h in exit_classes:
                                lines.append(f"        ignore sub @exit_y{h} {base_name}' {cls};")
                    excluded = _expand_exclusions(exclusions.get(exit_y, []))
                    for eg in sorted(excluded):
                        lines.append(f"        ignore sub {base_name}' {eg};")
                    lines.append(f"        sub {base_name}' {cls} by {variant_name};")
        lines.append("    } calt_cycle;")

    def _emit_upgrades(base_name: str):
        if base_name not in fwd_upgrades:
            return
        for entry_exit_var, entry_only_var, exit_y, not_before in fwd_upgrades[base_name]:
            if exit_y not in entry_classes:
                continue
            cls = f"@entry_y{exit_y}"
            safe = entry_exit_var.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_upgrade_{safe} {{")
            if not_before:
                nb_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                lines.append(f"        ignore sub {entry_only_var}' [{nb_list}];")
            lines.append(f"        sub {entry_only_var}' {cls} by {entry_exit_var};")
            lines.append(f"    }} calt_upgrade_{safe};")

    def _emit_post_upgrade_bk(bases: list[str]):
        upgrade_exit_ys: set[int] = set()
        for base_name in bases:
            if base_name in fwd_upgrades:
                for _, _, exit_y, _ in fwd_upgrades[base_name]:
                    upgrade_exit_ys.add(exit_y)
        if not upgrade_exit_ys:
            return
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            relevant = {y: v for y, v in variants.items()
                        if y in upgrade_exit_ys and y in exit_classes}
            if not relevant:
                continue
            safe = base_name.replace(".", "_").replace("-", "_")
            lines.append("")
            exclusions = bk_exclusions.get(base_name, {})
            lines.append(f"    lookup calt_post_upgrade_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                for eg in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                    lines.append(f"        ignore sub {eg} {base_name}';")
                lines.append(f"        sub @exit_y{entry_y} {base_name}' by {relevant[entry_y]};")
            lines.append(f"    }} calt_post_upgrade_bk_{safe};")

    def _emit_post_override_bk(bases: list[str]):
        """Re-run backward rules on exit-only variants after noentry overrides.

        When a noentry override converts a backward variant to a forward
        variant with a new exit height, the following glyph may already have
        been converted to an exit-only variant by the cycle.  This lookup
        re-processes those exit-only variants so they see the new exit.

        Only targets bases whose backward variant at the relevant height has
        BOTH entry and exit (i.e., is a complete variant), avoiding
        re-conversion of override targets whose backward variants are
        entry-only.
        """
        override_fwd_exit_ys: set[int] = set()
        for base_name in bases:
            if base_name not in bk_replacements or base_name not in fwd_replacements:
                continue
            for entry_y, bk_var in sorted(bk_replacements[base_name].items()):
                bk_def = glyphs_def.get(bk_var, {}) or {}
                if bk_def.get("cursive_exit"):
                    continue
                for fwd_exit_y, fwd_var in fwd_replacements[base_name].items():
                    fwd_var_def = glyphs_def.get(fwd_var, {}) or {}
                    raw_exit = fwd_var_def.get("cursive_exit")
                    if raw_exit:
                        for a in _normalize_anchors(raw_exit):
                            override_fwd_exit_ys.add(a[1])
        if not override_fwd_exit_ys:
            return
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            relevant = {}
            for y, v in variants.items():
                if y not in override_fwd_exit_ys or y not in exit_classes:
                    continue
                v_def = glyphs_def.get(v, {}) or {}
                if not v_def.get("cursive_exit"):
                    continue
                relevant[y] = v
            if not relevant:
                continue
            fwd_exit_only = []
            for fv_y, fv in fwd_replacements.get(base_name, {}).items():
                fv_def = glyphs_def.get(fv, {}) or {}
                if not fv_def.get("cursive_entry"):
                    fwd_exit_only.append(fv)
            if not fwd_exit_only:
                continue
            safe = f"post_override_{base_name}".replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(base_name, {})
            lines.append("")
            lines.append(f"    lookup calt_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                excl = sorted(_expand_exclusions(exclusions.get(entry_y, [])))
                for fv in sorted(fwd_exit_only):
                    for eg in excl:
                        lines.append(f"        ignore sub {eg} {fv}';")
                    lines.append(f"        sub @exit_y{entry_y} {fv}' by {relevant[entry_y]};")
            lines.append(f"    }} calt_{safe};")

    def _emit_reverse_upgrades():
        """Emit rules that convert exit-only variants to entry+exit variants.

        When a preceding glyph's forward rule fires late (after the cycle
        has already applied the exit-only variant to the current glyph),
        this rule upgrades the exit-only variant to entry+exit so the
        connection is established.
        """
        for base_name in sorted(fwd_upgrades):
            for entry_exit_var, entry_only_var, exit_y, not_before in fwd_upgrades[base_name]:
                entry_def = glyphs_def.get(entry_only_var, {}) or {}
                raw_entry = entry_def.get("cursive_entry")
                if not raw_entry:
                    continue
                entry_y_val = _normalize_anchors(raw_entry)[0][1]
                exit_only_var = fwd_replacements.get(base_name, {}).get(exit_y)
                if not exit_only_var or entry_y_val not in exit_classes:
                    continue
                safe = entry_exit_var.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_reverse_upgrade_{safe} {{")
                if not_before:
                    nb_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                    lines.append(f"        ignore sub {exit_only_var}' [{nb_list}];")
                lines.append(f"        sub @exit_y{entry_y_val} {exit_only_var}' by {entry_exit_var};")
                lines.append(f"    }} calt_reverse_upgrade_{safe};")

    def _emit_noentry_fwd_overrides(bases: list[str]):
        """Replace no-exit backward variants with forward-only variants."""
        for base_name in bases:
            if base_name not in bk_replacements or base_name not in fwd_replacements:
                continue
            for entry_y, bk_var in sorted(bk_replacements[base_name].items()):
                bk_def = glyphs_def.get(bk_var, {}) or {}
                if bk_def.get("cursive_exit"):
                    continue
                valid_overrides = []
                for fwd_exit_y, fwd_var in sorted(fwd_replacements[base_name].items()):
                    if fwd_exit_y not in entry_classes:
                        continue
                    fwd_var_def = glyphs_def.get(fwd_var, {}) or {}
                    if fwd_var_def.get("cursive_entry"):
                        continue
                    has_upgrade = any(
                        entry_only == bk_var and ey == fwd_exit_y
                        for _, entry_only, ey, _ in fwd_upgrades.get(base_name, [])
                    )
                    if has_upgrade:
                        continue
                    valid_overrides.append((fwd_exit_y, fwd_var))
                if not valid_overrides:
                    continue
                max_exit_y = max(ey for ey, _ in valid_overrides)
                for fwd_exit_y, fwd_var in valid_overrides:
                    use_exclusive = (
                        len(valid_overrides) > 1
                        and fwd_exit_y != max_exit_y
                    )
                    if use_exclusive:
                        if fwd_exit_y not in entry_exclusive or not entry_exclusive[fwd_exit_y]:
                            continue
                        cls = f"@entry_only_y{fwd_exit_y}"
                    else:
                        cls = f"@entry_y{fwd_exit_y}"
                    safe = f"{bk_var}_{fwd_exit_y}".replace(".", "_").replace("-", "_")
                    lines.append("")
                    lines.append(f"    lookup calt_fwd_override_{safe} {{")
                    fwd_def = glyphs_def.get(fwd_var, {}) or {}
                    not_before = fwd_def.get("calt_not_before", [])
                    if not_before:
                        resolved = [get_base_glyph_name(g) if g not in glyphs_def else g for g in not_before]
                        for nb in sorted(_expand_exclusions(resolved)):
                            lines.append(f"        ignore sub {bk_var}' {nb};")
                    lines.append(f"        sub {bk_var}' {cls} by {fwd_var};")
                    lines.append(f"    }} calt_fwd_override_{safe};")

    # Bases whose pair_overrides come entirely from entry-extended variants
    # should remain forward-only — entry-extended pairs are about the entry
    # side and don't affect the base's forward exit behaviour.
    entry_ext_pair_only: set[str] = set()
    for base_name, overrides in pair_overrides.items():
        if all(_extended_entry_suffix(vn) is not None for vn, _ in overrides):
            entry_ext_pair_only.add(base_name)

    # Add pair-override-only bases to sorted_bases (after the topo-sorted ones)
    pair_only = sorted(set(pair_overrides) - set(bk_replacements) - entry_ext_pair_only)
    all_bk_bases = sorted_bases + pair_only

    all_fwd_bases = set(fwd_replacements) | set(fwd_pair_overrides)
    fwd_only_set = all_fwd_bases - set(bk_replacements) - (set(pair_overrides) - entry_ext_pair_only)

    # --- Topological sort for forward-only lookups ---
    # If base A's forward rule checks an entry class containing base B,
    # and B's forward rule substitutes B with a variant NOT in that class,
    # then B must fire before A (so A sees B's final form).
    fwd_fwd_edges: dict[str, set[str]] = {b: set() for b in fwd_only_set}
    for base_a in fwd_only_set:
        for exit_y in fwd_replacements.get(base_a, {}):
            use_excl = (base_a, exit_y) in fwd_use_exclusive
            if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                continue
            cls = entry_exclusive[exit_y] if use_excl else entry_classes.get(exit_y, set())
            for base_b in fwd_only_set:
                if base_b == base_a or base_b not in cls:
                    continue
                for b_variant in fwd_replacements.get(base_b, {}).values():
                    if b_variant not in cls:
                        fwd_fwd_edges[base_a].add(base_b)
                        break

    fwd_out: dict[str, set[str]] = {b: set() for b in fwd_only_set}
    fwd_in_deg: dict[str, int] = {b: len(fwd_fwd_edges[b]) for b in fwd_only_set}
    for b in fwd_only_set:
        for dep in fwd_fwd_edges[b]:
            fwd_out[dep].add(b)

    fwd_queue = deque(sorted(b for b in fwd_only_set if fwd_in_deg[b] == 0))
    fwd_only: list[str] = []
    while fwd_queue:
        node = fwd_queue.popleft()
        fwd_only.append(node)
        for neighbor in sorted(fwd_out[node]):
            fwd_in_deg[neighbor] -= 1
            if fwd_in_deg[neighbor] == 0:
                fwd_queue.append(neighbor)

    fwd_only.extend(sorted(fwd_only_set - set(fwd_only)))

    lig_fwd_bases: set[str] = set()
    for base_name in fwd_only:
        if "_" not in base_name:
            continue
        components = base_name.split("_")
        if all(c in glyphs_def for c in components):
            lig_fwd_bases.add(base_name)

    # Pair-only bases whose fwd_upgrades come from pair overrides need their
    # backward pairs, upgrades, and forward rules emitted early — before the
    # main block's backward general rules — so other glyphs' backward rules
    # see the correct exit values.
    early_pair_upgrade_bases: set[str] = set()
    for base_name in pair_only:
        if base_name not in fwd_upgrades or base_name not in all_fwd_bases:
            continue
        pair_var_names = {vn for vn, _ in pair_overrides.get(base_name, [])}
        if any(entry_only in pair_var_names
               for _, entry_only, _, _ in fwd_upgrades[base_name]):
            early_pair_upgrade_bases.add(base_name)

    for base_name in fwd_only:
        if base_name in lig_fwd_bases:
            continue
        _emit_bk_pairs(base_name)
        _emit_fwd(base_name)

    for base_name in sorted(early_pair_upgrade_bases):
        _emit_bk_pairs(base_name)
        _emit_upgrades(base_name)
        _emit_fwd_general(base_name)

    # Split non-cycle bases into those the cycle depends on (emit before
    # the cycle) and the rest (emit after).  The topological order already
    # places dependencies first, so we just need to find the split point.
    pre_cycle: list[str] = []
    post_cycle: list[str] = []
    if cycle_bases:
        cycle_deps: set[str] = set()
        for cb in cycle_bases:
            cycle_deps |= edges.get(cb, set())
        cycle_deps -= cycle_bases
        for base_name in all_bk_bases:
            if base_name in cycle_bases:
                continue
            if base_name in cycle_deps:
                pre_cycle.append(base_name)
            else:
                post_cycle.append(base_name)
    else:
        post_cycle = list(all_bk_bases)

    # Identify bases whose forward pair lookups must run before backward
    # rules.  Two cases:
    # 1. Self-referencing: calt_before targets include the same base glyph.
    # 2. Cross-referencing: the variant's exit Y feeds into a before_glyph's
    #    backward entry Y, so the exit must be visible when that glyph's
    #    backward rule fires.
    early_fwd_pairs: set[str] = set()
    for base_name, overrides in fwd_pair_overrides.items():
        found = False
        for variant_name, before_glyphs, _ in overrides:
            if base_name in {g.split(".")[0] for g in before_glyphs}:
                early_fwd_pairs.add(base_name)
                found = True
                break
            variant_def = glyphs_def.get(variant_name, {}) or {}
            raw_exit = variant_def.get("cursive_exit")
            if raw_exit:
                exit_ys = {a[1] for a in _normalize_anchors(raw_exit)}
                for bg in before_glyphs:
                    bg_base = bg.split(".")[0] if "." in bg else bg
                    bk_ys = set(bk_replacements.get(bg_base, {}))
                    for pv, _ in pair_overrides.get(bg_base, []):
                        pv_def = glyphs_def.get(pv, {}) or {}
                        pv_entry = pv_def.get("cursive_entry")
                        if pv_entry:
                            bk_ys.update(a[1] for a in _normalize_anchors(pv_entry))
                    if exit_ys & bk_ys:
                        early_fwd_pairs.add(base_name)
                        found = True
                        break
            if found:
                break

    def _emit_block(bases: list[str], *, use_cycle: bool = False):
        for base_name in bases:
            if base_name not in early_pair_upgrade_bases:
                _emit_bk_pairs(base_name)
        for base_name in bases:
            if base_name in early_fwd_pairs:
                _emit_fwd_pairs(base_name)
        if use_cycle:
            _emit_bk_cycle(bases)
        else:
            for base_name in bases:
                _emit_bk_general(base_name)
        for base_name in bases:
            if base_name not in early_pair_upgrade_bases:
                _emit_upgrades(base_name)
        _emit_noentry_fwd_overrides(bases)
        if use_cycle:
            _emit_post_upgrade_bk(bases)
            _emit_post_override_bk(bases)
        for base_name in bases:
            if base_name in all_fwd_bases and base_name not in early_pair_upgrade_bases:
                if base_name in early_fwd_pairs:
                    _emit_fwd_general(base_name)
                else:
                    _emit_fwd(base_name)

    _emit_block(pre_cycle)

    if cycle_bases:
        cycle_list = sorted(cycle_bases)
        _emit_block(cycle_list, use_cycle=True)

    early_post = [b for b in post_cycle if b in early_fwd_pairs]
    late_post = [b for b in post_cycle if b not in early_fwd_pairs]
    _emit_block(early_post)
    _emit_block(late_post)

    if cycle_bases:
        pair_only_new_exit_ys: set[int] = set()
        for po_base in pair_only:
            base_def = glyphs_def.get(po_base, {})
            base_ys = set()
            if base_def:
                raw_exit = base_def.get("cursive_exit")
                if raw_exit:
                    for anchor in _normalize_anchors(raw_exit):
                        base_ys.add(anchor[1])
            for variant_name, _ in pair_overrides[po_base]:
                variant_def = glyphs_def.get(variant_name, {})
                if variant_def:
                    raw_exit = variant_def.get("cursive_exit")
                    if raw_exit:
                        for anchor in _normalize_anchors(raw_exit):
                            if anchor[1] not in base_ys:
                                pair_only_new_exit_ys.add(anchor[1])
        for cb in sorted(cycle_bases):
            if cb not in bk_replacements:
                continue
            variants = bk_replacements[cb]
            relevant = {y: v for y, v in variants.items()
                        if y in pair_only_new_exit_ys and y in exit_classes}
            if not relevant:
                continue
            safe = cb.replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(cb, {})
            lines.append("")
            lines.append(f"    lookup calt_post_pair_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                for eg in sorted(_expand_exclusions(
                        exclusions.get(entry_y, []))):
                    lines.append(f"        ignore sub {eg} {cb}';")
                lines.append(
                    f"        sub @exit_y{entry_y} {cb}'"
                    f" by {relevant[entry_y]};")
            lines.append(f"    }} calt_post_pair_bk_{safe};")

    _emit_reverse_upgrades()

    def _emit_exit_extended_bk_refinement():
        """Refine exit-extended variants with backward entry anchors.

        When a forward pair lookup replaces a base glyph with its
        exit-extended variant (which may lack an entry anchor), and a
        later lookup changes the preceding glyph to one that exits at a
        specific Y, this lookup converts the exit-extended variant to a
        combined entry+exit-extended variant so the connection works.
        """
        for base_name in sorted(fwd_pair_overrides):
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            for fwd_var, _, _ in fwd_pair_overrides[base_name]:
                ext_suffix = _extended_exit_suffix(fwd_var)
                if not ext_suffix:
                    continue
                fwd_def = glyphs_def.get(fwd_var, {}) or {}
                if fwd_def.get("cursive_entry"):
                    continue
                emitted_any = False
                safe = fwd_var.replace(".", "_").replace("-", "_")
                for entry_y in sorted(variants.keys()):
                    bk_var = variants[entry_y]
                    combined = bk_var + ext_suffix
                    if combined not in glyphs_def:
                        continue
                    if entry_y not in exit_classes:
                        continue
                    if not emitted_any:
                        lines.append("")
                        lines.append(
                            f"    lookup calt_ext_bk_{safe} {{"
                        )
                        emitted_any = True
                    excluded = set(
                        _expand_exclusions(exclusions.get(entry_y, []))
                    )
                    if excluded:
                        filtered = sorted(
                            exit_classes[entry_y] - excluded
                        )
                        if filtered:
                            member_list = " ".join(filtered)
                            lines.append(
                                f"        sub [{member_list}]"
                                f" {fwd_var}' by {combined};"
                            )
                    else:
                        lines.append(
                            f"        sub @exit_y{entry_y}"
                            f" {fwd_var}' by {combined};"
                        )
                if emitted_any:
                    lines.append(
                        f"    }} calt_ext_bk_{safe};"
                    )

    _emit_exit_extended_bk_refinement()

    # Ligature substitutions live in calt (not liga) so that forward
    # rules selecting alternate glyphs can block the ligature.  For
    # example, ·Day·Utter·Low: the forward rule replaces ·Utter with
    # alt ·Utter first, so the ·Day·Utter ligature pattern no longer
    # matches.
    ligatures = []
    for glyph_name in glyphs_def:
        if "_" not in glyph_name:
            continue
        if _extended_exit_suffix(glyph_name) is not None:
            continue
        components = glyph_name.split("_")
        if all(c in glyphs_def for c in components):
            ligatures.append((glyph_name, components))
    if ligatures:
        from itertools import product as _product

        lines.append("")
        lines.append("    lookup calt_liga {")
        for lig_name, components in sorted(ligatures):
            variant_sets: list[list[str]] = []
            for i, comp in enumerate(components):
                variants: set[str] = set()
                if comp in bk_replacements:
                    variants.update(bk_replacements[comp].values())
                if comp in pair_overrides:
                    for variant_name, _ in pair_overrides[comp]:
                        variants.add(variant_name)
                if comp in fwd_pair_overrides:
                    for variant_name, _, _ in fwd_pair_overrides[comp]:
                        variants.add(variant_name)
                if i == 0:
                    if comp in fwd_replacements:
                        variants.update(fwd_replacements[comp].values())
                variant_sets.append([comp] + sorted(variants))
            for combo in _product(*variant_sets):
                component_str = " ".join(combo)
                actual_lig = lig_name
                suffix = _extended_entry_suffix(combo[0])
                if suffix:
                    ext_lig = lig_name + suffix
                    if ext_lig not in glyphs_def:
                        ext_lig = lig_name + ".entry-extended"
                    if ext_lig in glyphs_def:
                        actual_lig = ext_lig
                exit_suffix = _extended_exit_suffix(combo[-1])
                if exit_suffix:
                    ext_lig = actual_lig + ".exit-extended"
                    if ext_lig in glyphs_def:
                        actual_lig = ext_lig
                lines.append(f"        sub {component_str} by {actual_lig};")
        lines.append("    } calt_liga;")

        lig_glyph_names = {lig_name for lig_name, _ in ligatures}
        post_liga_rules: list[tuple[str, str, list[str]]] = []
        for base_name in sorted(pair_overrides):
            for variant_name, after_glyphs in pair_overrides[base_name]:
                if any(g in lig_glyph_names for g in after_glyphs):
                    post_liga_rules.append((base_name, variant_name, after_glyphs))

        # noentry_after: select .noentry variant of ligature glyphs when
        # preceded by specific glyphs, blocking cursive attachment.
        for lig_name in sorted(lig_glyph_names):
            lig_def = glyphs_def.get(lig_name, {}) or {}
            noentry_after = lig_def.get("noentry_after")
            if not noentry_after:
                continue
            noentry_name = lig_name + ".noentry"
            if noentry_name not in glyphs_def:
                continue
            post_liga_rules.append((lig_name, noentry_name, sorted(_expand_all_variants(noentry_after, include_base=True))))

        if post_liga_rules:
            lines.append("")
            lines.append("    lookup calt_post_liga {")
            for base_name, variant_name, after_glyphs in post_liga_rules:
                after_list = " ".join(sorted(after_glyphs))
                targets = {base_name}
                if base_name in bk_replacements:
                    targets.update(bk_replacements[base_name].values())
                for target in sorted(targets):
                    lines.append(
                        f"        sub [{after_list}] {target}' by {variant_name};"
                    )
            lines.append("    } calt_post_liga;")

        for base_name in sorted(lig_fwd_bases):
            _emit_fwd(base_name)

    lines.append("} calt;")
    return "\n".join(lines)


def generate_liga_fea(glyphs_def: dict) -> str | None:
    """Generate OpenType feature code for ligatures (liga).

    Finds glyphs whose names contain underscores (e.g., qsDay_qsUtter),
    splits on underscores to get component glyph names, and emits
    a liga feature that substitutes the component sequence with the ligature.

    Returns the FEA string, or None if no ligature glyphs exist.
    """
    ligatures = []
    for glyph_name in glyphs_def:
        if "_" not in glyph_name:
            continue
        components = glyph_name.split("_")
        if all(c in glyphs_def for c in components):
            ligatures.append((glyph_name, components))

    if not ligatures:
        return None

    lines = ["feature liga {"]
    for lig_name, components in sorted(ligatures):
        component_str = " ".join(components)
        lines.append(f"    sub {component_str} by {lig_name};")
    lines.append("} liga;")
    return "\n".join(lines)


def generate_curs_fea(glyphs_def: dict, pixel_width: int, pixel_height: int) -> str | None:
    """Generate OpenType feature code for cursive attachment (curs).

    Scans glyphs_def for cursive_entry / cursive_exit anchors and emits
    a GPOS 'curs' feature with separate lookups grouped by Y value.
    This prevents cross-pair attachment between glyphs at different heights.

    Pixel coordinates: x in pixels from x=0 of the glyph coordinate space,
    y in pixels from baseline (0 = baseline, positive = up).

    Returns the FEA string, or None if no glyphs declare cursive anchors.
    """
    # Group glyphs by Y value: y_groups[y] = list of (glyph_name, entry_anchor, exit_anchor)
    y_groups: dict[int, list[tuple[str, str, str]]] = {}

    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        raw_entry = glyph_def.get("cursive_entry")
        raw_entry_curs = glyph_def.get("cursive_entry_curs_only")
        raw_exit = glyph_def.get("cursive_exit")
        if raw_entry is None and raw_entry_curs is None and raw_exit is None:
            continue
        entries = _normalize_anchors(raw_entry) + _normalize_anchors(raw_entry_curs)
        exits = _normalize_anchors(raw_exit)
        y_values = {a[1] for a in entries} | {a[1] for a in exits}
        for y in y_values:
            entry_anchor = "<anchor NULL>"
            exit_anchor = "<anchor NULL>"
            for a in entries:
                if a[1] == y:
                    entry_anchor = f"<anchor {a[0] * pixel_width} {a[1] * pixel_height}>"
                    break
            for a in exits:
                if a[1] == y:
                    exit_anchor = f"<anchor {a[0] * pixel_width} {a[1] * pixel_height}>"
                    break
            y_groups.setdefault(y, []).append((glyph_name, entry_anchor, exit_anchor))

    if not y_groups:
        return None

    # Add .noentry glyphs (no cursive_exit) to the Y-groups they need to
    # appear in so they break the cursive chain.  .noentry glyphs WITH
    # cursive_exit are already picked up by the normal scan above.
    for glyph_name, glyph_def in glyphs_def.items():
        if not glyph_name.endswith(".noentry") or glyph_def is None:
            continue
        if glyph_def.get("cursive_exit"):
            continue
        original_name = glyph_def.get("_noentry_for")
        if not original_name:
            continue
        original_def = glyphs_def.get(original_name)
        if not original_def:
            continue
        for anchor in _normalize_anchors(original_def.get("cursive_entry")):
            y = anchor[1]
            y_groups.setdefault(y, []).append(
                (glyph_name, "<anchor NULL>", "<anchor NULL>")
            )

    lines = ["feature curs {"]
    for y in sorted(y_groups):
        lines.append(f"    lookup cursive_y{y} {{")
        for glyph_name, entry_anchor, exit_anchor in sorted(y_groups[y]):
            lines.append(f"        pos cursive {glyph_name} {entry_anchor} {exit_anchor};")
        lines.append(f"    }} cursive_y{y};")
    lines.append("} curs;")
    return "\n".join(lines)


def generate_noentry_variants(glyphs_def: dict) -> dict:
    """Create .noentry glyph variants for ZWNJ chain-breaking.

    For each base glyph with cursive_entry (no dot in name), creates a copy
    without cursive_entry.  These are substituted by calt when ZWNJ precedes
    them, so the curs feature sees NULL entry and breaks the chain.
    """
    if "uni200C" not in glyphs_def:
        return {}

    variants = {}
    for name, gdef in sorted(glyphs_def.items()):
        if gdef is None or "." in name:
            continue
        if not gdef.get("cursive_entry"):
            continue
        noentry_def = {k: v for k, v in gdef.items() if k not in ("cursive_entry", "extend_entry_after", "extend_exit_before")}
        noentry_def["_noentry_for"] = name
        variants[name + ".noentry"] = noentry_def
    return variants


def _shift_anchor(entry, dx=-1):
    if isinstance(entry[0], list):
        return [[x + dx, y] for x, y in entry]
    return [entry[0] + dx, entry[1]]


def _widen_bitmap_with_connector(bitmap, entry_y, y_offset=0, count=1):
    """Widen bitmap by `count` pixels on the left, adding connecting pixels at entry_y."""
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = entry_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap = []
    for i, row in enumerate(bitmap):
        prefix = "#" * count if i == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(prefix + row)
        else:
            new_bitmap.append([int(c == "#") for c in prefix] + list(row))
    return new_bitmap


def _widen_bitmap_right_with_connector(bitmap, exit_y, y_offset=0, count=1):
    """Widen bitmap by `count` pixels on the right, adding connecting pixels at exit_y."""
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = exit_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap = []
    for i, row in enumerate(bitmap):
        suffix = "#" * count if i == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(row + suffix)
        else:
            new_bitmap.append(list(row) + [int(c == "#") for c in suffix])
    return new_bitmap


_CALT_KEYS = frozenset((
    "calt_after", "calt_before", "calt_not_after", "calt_not_before",
    "calt_word_final", "extend_entry_after", "extend_exit_before",
    "doubly_extend_entry_after", "doubly_extend_exit_before",
    "noentry_after",
))


def _generate_extended_entry_variants(
    glyphs_def: dict, *, count: int, yaml_key: str, suffix_word: str,
) -> dict:
    """Create entry-extended variants for glyphs with the given yaml_key.

    For each glyph with the yaml_key, creates a copy whose bitmap is widened
    by `count` pixels on the left with a connecting pixel at the entry y-coordinate.
    The entry anchor stays unchanged; the exit anchor (if any) shifts right.

    Also generates extended versions of related glyphs (exit variants, ligatures)
    so that forward substitutions and ligatures preserve the extension.
    """
    variants = {}
    for name, gdef in sorted(glyphs_def.items()):
        if gdef is None:
            continue
        extend_after = gdef.get(yaml_key)
        if not extend_after:
            continue
        entry = gdef.get("cursive_entry")
        if not entry:
            continue

        entries = _normalize_anchors(entry)
        multi = len(entries) > 1

        if multi:
            for anchor in entries:
                y = anchor[1]
                label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
                ext_name = f"{name}.entry-{suffix_word}-at-{label}"
                if ext_name not in glyphs_def:
                    variant_def = {k: v for k, v in gdef.items() if k != yaml_key}
                    variant_def["cursive_entry"] = [anchor[0], anchor[1]]
                    variant_def["bitmap"] = _widen_bitmap_with_connector(
                        variant_def["bitmap"], anchor[1], variant_def.get("y_offset", 0), count=count
                    )
                    if "cursive_exit" in variant_def:
                        variant_def["cursive_exit"] = _shift_anchor(variant_def["cursive_exit"], dx=count)
                    variant_def["calt_after"] = list(extend_after)
                    variants[ext_name] = variant_def
        else:
            ext_name = f"{name}.entry-{suffix_word}"
            if ext_name not in glyphs_def:
                variant_def = {k: v for k, v in gdef.items() if k != yaml_key}
                variant_def["bitmap"] = _widen_bitmap_with_connector(
                    variant_def["bitmap"], entries[0][1], variant_def.get("y_offset", 0), count=count
                )
                if "cursive_exit" in variant_def:
                    variant_def["cursive_exit"] = _shift_anchor(variant_def["cursive_exit"], dx=count)
                variant_def["calt_after"] = list(extend_after)
                variants[ext_name] = variant_def

        base_name = name.split(".")[0]
        for other_name, other_gdef in sorted(glyphs_def.items()):
            if other_gdef is None or other_name == name:
                continue
            if _extended_entry_suffix(other_name) is not None:
                continue
            other_base = other_name.split(".")[0]
            is_variant = other_base == base_name and "." in other_name
            is_ligature = other_name.startswith(base_name + "_")
            if not (is_variant or is_ligature):
                continue
            other_entry = other_gdef.get("cursive_entry")
            if not other_entry:
                continue
            if multi:
                other_entries = _normalize_anchors(other_entry)
                for anchor in other_entries:
                    y = anchor[1]
                    label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
                    sec_name = f"{other_name}.entry-{suffix_word}-at-{label}"
                    if sec_name not in glyphs_def:
                        extended = {k: v for k, v in other_gdef.items() if k not in _CALT_KEYS}
                        extended["cursive_entry"] = [anchor[0], anchor[1]]
                        extended["bitmap"] = _widen_bitmap_with_connector(
                            extended["bitmap"], anchor[1], extended.get("y_offset", 0), count=count
                        )
                        if "cursive_exit" in extended:
                            extended["cursive_exit"] = _shift_anchor(extended["cursive_exit"], dx=count)
                        variants[sec_name] = extended
            else:
                sec_name = f"{other_name}.entry-{suffix_word}"
                if sec_name not in glyphs_def:
                    extended = {k: v for k, v in other_gdef.items() if k not in _CALT_KEYS}
                    other_entries_norm = _normalize_anchors(other_entry)
                    extended["bitmap"] = _widen_bitmap_with_connector(
                        extended["bitmap"], other_entries_norm[0][1], extended.get("y_offset", 0), count=count
                    )
                    if "cursive_exit" in extended:
                        extended["cursive_exit"] = _shift_anchor(extended["cursive_exit"], dx=count)
                    variants[sec_name] = extended

    return variants


def _generate_extended_exit_variants(
    glyphs_def: dict, *, count: int, yaml_key: str, suffix_word: str,
) -> dict:
    """Create exit-extended variants for glyphs with the given yaml_key.

    For each glyph with the yaml_key, creates a copy whose bitmap is widened
    by `count` pixels on the right with a connecting pixel at the exit y-coordinate.
    The exit anchor shifts right; the entry anchor stays unchanged.

    Also generates extended versions of related glyphs (entry variants, ligatures)
    so that substitutions preserve the extension.
    """
    variants = {}
    for name, gdef in sorted(glyphs_def.items()):
        if gdef is None:
            continue
        extend_before = gdef.get(yaml_key)
        if not extend_before:
            continue
        raw_exit = gdef.get("cursive_exit")
        if not raw_exit:
            continue

        exits = _normalize_anchors(raw_exit)
        exit_y = exits[0][1]

        ext_name = f"{name}.exit-{suffix_word}"
        if ext_name not in glyphs_def:
            skip_keys = {yaml_key, "extend_exit_no_entry"}
            variant_def = {k: v for k, v in gdef.items() if k not in skip_keys}
            variant_def["bitmap"] = _widen_bitmap_right_with_connector(
                variant_def["bitmap"], exit_y, variant_def.get("y_offset", 0), count=count
            )
            variant_def["cursive_exit"] = _shift_anchor(variant_def["cursive_exit"], dx=count)
            variant_def["calt_before"] = list(extend_before)
            if gdef.get("extend_exit_no_entry"):
                variant_def.pop("cursive_entry", None)
            variants[ext_name] = variant_def

        base_name = name.split(".")[0]
        for other_name, other_gdef in sorted(glyphs_def.items()):
            if other_gdef is None or other_name == name:
                continue
            if _extended_exit_suffix(other_name) is not None:
                continue
            other_base = other_name.split(".")[0]
            is_variant = other_base == base_name and "." in other_name
            is_ligature = other_name.startswith(base_name + "_")
            if not (is_variant or is_ligature):
                continue
            other_exit = other_gdef.get("cursive_exit")
            if not other_exit:
                continue
            sec_name = f"{other_name}.exit-{suffix_word}"
            if sec_name not in glyphs_def:
                extended = {k: v for k, v in other_gdef.items() if k not in _CALT_KEYS}
                other_exits_norm = _normalize_anchors(other_exit)
                extended["bitmap"] = _widen_bitmap_right_with_connector(
                    extended["bitmap"], other_exits_norm[0][1], extended.get("y_offset", 0), count=count
                )
                extended["cursive_exit"] = _shift_anchor(extended["cursive_exit"], dx=count)
                variants[sec_name] = extended

    return variants


def generate_extended_entry_variants(glyphs_def: dict) -> dict:
    return _generate_extended_entry_variants(
        glyphs_def, count=1, yaml_key="extend_entry_after", suffix_word="extended",
    )


def generate_extended_exit_variants(glyphs_def: dict) -> dict:
    return _generate_extended_exit_variants(
        glyphs_def, count=1, yaml_key="extend_exit_before", suffix_word="extended",
    )


def generate_doubly_extended_entry_variants(glyphs_def: dict) -> dict:
    return _generate_extended_entry_variants(
        glyphs_def, count=2, yaml_key="doubly_extend_entry_after", suffix_word="doubly-extended",
    )


def generate_doubly_extended_exit_variants(glyphs_def: dict) -> dict:
    return _generate_extended_exit_variants(
        glyphs_def, count=2, yaml_key="doubly_extend_exit_before", suffix_word="doubly-extended",
    )


def parse_bitmap(bitmap: list) -> list[list[int]]:
    """
    Convert bitmap to a 2D array of 0s and 1s.
    Accepts either string rows ("#" = on) or int arrays.
    """
    if not bitmap:
        return []

    if isinstance(bitmap[0], str):
        return [
            [1 if c == '#' or c == '1' else 0 for c in row]
            for row in bitmap
        ]
    return bitmap


def bitmap_to_rectangles(
    bitmap: list[list[int]],
    pixel_width: int,
    pixel_height: int,
    y_offset: int = 0,
) -> list[tuple[int, int, int, int]]:
    """
    Convert a 2D bitmap array to a list of rectangle coordinates.

    Args:
        bitmap: 2D array of 0s and 1s
        pixel_width: width of each pixel in font units
        pixel_height: height of each pixel in font units
        y_offset: vertical offset in pixels (negative for descenders)
                  0 = bottom of bitmap on baseline
                  -3 = bottom of bitmap is 3 pixels below baseline

    Returns list of (x, y, width, height) tuples for each "on" pixel.
    Coordinates are in font units, with y=0 at baseline.
    """
    rectangles = []
    height = len(bitmap)

    for row_idx, row in enumerate(bitmap):
        # Flip y-axis: bitmap row 0 is top, font y increases upward
        # y_offset shifts the whole glyph (negative = below baseline)
        y = (y_offset + height - 1 - row_idx) * pixel_height

        for col_idx, pixel in enumerate(row):
            if pixel:  # Pixel is "on"
                x = col_idx * pixel_width
                rectangles.append((x, y, pixel_width, pixel_height))

    return rectangles


def draw_rectangles_to_glyph(rectangles: list[tuple], glyph_set):
    """
    Draw rectangles as a TrueType glyph using T2CharStringPen.
    Returns a T2CharString for CFF/OTF fonts.
    """
    pen = T2CharStringPen(width=0, glyphSet=glyph_set)

    for x, y, w, h in rectangles:
        # Draw counter-clockwise for CFF (outer contour)
        pen.moveTo((x, y))
        pen.lineTo((x, y + h))
        pen.lineTo((x + w, y + h))
        pen.lineTo((x + w, y))
        pen.closePath()

    return pen.getCharString()


def compose_bitmaps(
    base_bitmap: list[list[int]],
    base_y_offset: int,
    accent_bitmap: list[list[int]],
    mark_y: int,
    is_top: bool,
    accent_x_adjust: int = 0,
) -> tuple[list[list[int]], int]:
    """
    Overlay an accent bitmap onto a base bitmap.

    Args:
        base_bitmap: Parsed 2D bitmap of the base glyph
        base_y_offset: y_offset of the base glyph (negative for descenders)
        accent_bitmap: Parsed 2D bitmap of the accent glyph
        mark_y: The anchor y position (in pixels above baseline) from the base glyph
        is_top: True for top accents, False for bottom accents

    Returns:
        (combined_bitmap, combined_y_offset) where combined_bitmap is the
        merged 2D array and combined_y_offset is the y_offset for the result.
    """
    base_h = len(base_bitmap)
    accent_h = len(accent_bitmap)
    base_w = max((len(row) for row in base_bitmap), default=0)
    accent_w = max((len(row) for row in accent_bitmap), default=0)

    canvas_w = max(base_w, accent_w)

    # Base occupies pixel rows [base_y_offset, base_y_offset + base_h)
    # (in font-pixel coordinates where 0 = baseline, positive = up)
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

    # Combined extent in font-pixel coordinates
    combined_bottom = min(base_bottom, accent_bottom)
    combined_top = max(base_top, accent_top)
    combined_h = combined_top - combined_bottom

    # Build canvas (row 0 = top of combined glyph)
    canvas = [[0] * canvas_w for _ in range(combined_h)]

    def blit(bitmap, bm_w, bm_bottom, x_adjust=0):
        """Blit a bitmap onto the canvas, centered horizontally."""
        x_off = (canvas_w - bm_w) // 2 + x_adjust
        bm_h = len(bitmap)
        for row_idx, row in enumerate(bitmap):
            # bitmap row 0 is top; font-pixel y for this row:
            pixel_y = bm_bottom + bm_h - 1 - row_idx
            # canvas row for this pixel_y:
            canvas_row = combined_top - 1 - pixel_y
            for col_idx, val in enumerate(row):
                if val:
                    canvas[canvas_row][x_off + col_idx] = 1

    blit(base_bitmap, base_w, base_bottom)
    blit(accent_bitmap, accent_w, accent_bottom, accent_x_adjust)

    return canvas, combined_bottom


def resolve_composite(
    glyph_name: str,
    glyph_def: dict,
    glyphs_def: dict,
    is_proportional: bool,
) -> tuple[list[list[int]], int]:
    """
    Resolve a composite glyph definition into a bitmap and y_offset.

    Args:
        glyph_name: Name of the composite glyph (for error messages)
        glyph_def: The composite glyph definition (has 'base', 'top'/'bottom')
        glyphs_def: All glyph definitions (to look up base and accent)
        is_proportional: Whether building the proportional variant

    Returns:
        (bitmap, y_offset) for the resolved composite
    """
    base_name = glyph_def["base"]

    # Try .prop variant first if building proportional font
    if is_proportional and base_name + ".prop" in glyphs_def:
        base_ref = base_name + ".prop"
    else:
        base_ref = base_name

    base_glyph = glyphs_def.get(base_ref)
    if base_glyph is None:
        raise ValueError(
            f"Composite glyph '{glyph_name}' references base '{base_name}' which doesn't exist"
        )
    base_bitmap = parse_bitmap(base_glyph.get("bitmap", []))
    base_y_offset = base_glyph.get("y_offset", 0)

    result_bitmap = base_bitmap
    result_y_offset = base_y_offset

    # Build mapping from spacing accent bitmap identity -> adjustments
    # (YAML aliases make the combining mark's bitmap the same object as the
    # spacing accent's bitmap, so we can use 'is' for matching)
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

    if "top" in glyph_def:
        accent_name = glyph_def["top"]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references top accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get("top_mark_y")
        if mark_y is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' needs top_mark_y on base '{base_ref}'"
            )
        bitmap_id = id(accent_glyph.get("bitmap"))
        accent_x_adjust = accent_x_adjusts.get(bitmap_id, {}).get(base_name, 0)
        accent_y_adjust = accent_y_adjusts.get(bitmap_id, {}).get(base_name, 0)
        result_bitmap, result_y_offset = compose_bitmaps(
            result_bitmap, result_y_offset, accent_bitmap,
            mark_y + accent_y_adjust, is_top=True,
            accent_x_adjust=accent_x_adjust,
        )

    if "bottom" in glyph_def:
        accent_name = glyph_def["bottom"]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references bottom accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get("bottom_mark_y")
        if mark_y is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' needs bottom_mark_y on base '{base_ref}'"
            )
        bitmap_id = id(accent_glyph.get("bitmap"))
        accent_x_adjust = accent_x_adjusts.get(bitmap_id, {}).get(base_name, 0)
        accent_y_adjust = accent_y_adjusts.get(bitmap_id, {}).get(base_name, 0)
        result_bitmap, result_y_offset = compose_bitmaps(
            result_bitmap, result_y_offset, accent_bitmap,
            mark_y + accent_y_adjust, is_top=False,
            accent_x_adjust=accent_x_adjust,
        )

    return result_bitmap, result_y_offset


def build_font(
    glyph_data: dict,
    output_path: Path | None = None,
    variant: str = "mono",
    pixel_width: int | None = None,
):
    """
    Build font from glyph data dictionary.
    Creates a CFF-based OpenType font (.otf).

    Args:
        glyph_data: Dictionary containing metadata and glyph definitions
        output_path: Path to write the font file (None to skip saving)
        variant: "mono", "junior", or "senior"
        pixel_width: Width of each pixel in font units. Defaults to
                     metadata["pixel_size"]. Height is always metadata["pixel_size"].

    Returns:
        The built TTFont object.
    """
    is_proportional = variant != "mono"
    is_senior = variant == "senior"

    metadata = glyph_data.get("metadata", {})
    glyphs_def = glyph_data["glyphs"]

    # Filter out .unused glyphs — they're stubs not yet ready for compilation
    glyphs_def = {k: v for k, v in glyphs_def.items() if ".unused" not in k}

    # For non-senior proportional fonts, exclude contextual variants
    if not is_senior:
        glyphs_def = {k: v for k, v in glyphs_def.items() if not _is_contextual_variant(k)}

    # For proportional font, transform glyphs: .prop becomes default
    if is_proportional:
        glyphs_def = prepare_proportional_glyphs(glyphs_def)

    # For senior font, create .noentry variants for ZWNJ chain-breaking
    if is_senior:
        glyphs_def.update(generate_noentry_variants(glyphs_def))
        glyphs_def.update(generate_extended_entry_variants(glyphs_def))
        glyphs_def.update(generate_extended_exit_variants(glyphs_def))
        glyphs_def.update(generate_doubly_extended_entry_variants(glyphs_def))
        glyphs_def.update(generate_doubly_extended_exit_variants(glyphs_def))

    # Font name differs per variant
    base_font_name = metadata["font_name"]
    suffixes = {"mono": " Mono", "junior": " Sans Junior", "senior": " Sans Senior"}
    font_name = base_font_name + suffixes[variant]
    version = metadata["version"]
    units_per_em = metadata["units_per_em"]
    pixel_height = metadata["pixel_size"]
    if pixel_width is None:
        pixel_width = pixel_height
    ascender = metadata["ascender"]
    descender = metadata["descender"]
    cap_height = metadata["cap_height"]
    x_height = metadata["x_height"]

    # Build glyph order (must include .notdef first)
    # For mono font, exclude .prop glyphs entirely
    glyph_names = [
        name for name in glyphs_def.keys()
        if name not in (".notdef", "space")
        and (is_proportional or (not is_proportional_glyph(name) and not _is_contextual_variant(name)))
        and (is_senior or not _is_contextual_variant(name))
    ]
    postscript_glyph_names = load_postscript_glyph_names()

    name_to_codepoint = {}
    for name in glyphs_def:
        cp = _resolve_codepoint(name, postscript_glyph_names)
        if cp is not None:
            name_to_codepoint[name] = cp

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
        return (float('inf'), name)

    glyph_order = [".notdef", "space"] + sorted(glyph_names, key=_sort_key)

    # Build character map (Unicode codepoint -> glyph name)
    # Exclude .prop glyphs - they have no direct Unicode mapping
    cmap = {32: "space"}  # Always include space
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            continue  # Proportional variants are accessed via ss01, not cmap
        if len(glyph_name) == 1:
            cmap[ord(glyph_name)] = glyph_name
        elif glyph_name.startswith("uni") and len(glyph_name) == 7:
            # Handle uniXXXX naming convention (4 hex digits)
            try:
                codepoint = int(glyph_name[3:], 16)
                cmap[codepoint] = glyph_name
            except ValueError:
                pass  # Not a valid hex code, skip
        elif glyph_name.startswith("u") and not glyph_name.startswith("uni") and len(glyph_name) == 6:
            try:
                codepoint = int(glyph_name[1:], 16)
                if codepoint > 0xFFFF:
                    cmap[codepoint] = glyph_name
            except ValueError:
                pass
        elif glyph_name in postscript_glyph_names:
            cmap[postscript_glyph_names[glyph_name]] = glyph_name

    # Initialize FontBuilder for CFF-based OTF
    fb = FontBuilder(units_per_em, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)

    # Build charstrings and metrics
    charstrings = {}
    metrics = {}

    # Placeholder glyph set for pen (not strictly needed for simple drawing)
    class GlyphSet:
        pass
    glyph_set = GlyphSet()

    # Standard monospace width: 7 pixels (bitmap 5 + 2 spacing)
    mono_width = 7 * pixel_width

    # Create .notdef glyph (simple rectangle, sized to fit mono_width)
    pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
    pen.moveTo((pixel_width, 0))
    pen.lineTo((pixel_width, 5 * pixel_height))
    pen.lineTo((5 * pixel_width, 5 * pixel_height))
    pen.lineTo((5 * pixel_width, 0))
    pen.closePath()
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (mono_width, pixel_width)

    # Create space glyph (empty)
    space_def = glyphs_def.get("space", {})
    space_width = space_def["advance_width"] * pixel_width
    pen = T2CharStringPen(width=space_width, glyphSet=glyph_set)
    charstrings["space"] = pen.getCharString()
    metrics["space"] = (space_width, 0)

    # Create all other glyphs
    for glyph_name in glyph_order:
        if glyph_name in (".notdef", "space"):
            continue

        glyph_def = glyphs_def.get(glyph_name, {})

        # Handle composite glyphs (have 'base' key, no 'bitmap')
        if "base" in glyph_def and not glyph_def.get("bitmap"):
            composed_bitmap, composed_y_offset = resolve_composite(
                glyph_name, glyph_def, glyphs_def, is_proportional
            )
            # Inject the resolved bitmap so the rest of the loop handles it normally
            glyph_def = dict(glyph_def)
            glyph_def["bitmap"] = composed_bitmap
            glyph_def["y_offset"] = composed_y_offset
            # Remove 'base'/'top'/'bottom' so they don't confuse later logic
            glyph_def.pop("base", None)
            glyph_def.pop("top", None)
            glyph_def.pop("bottom", None)

        bitmap = glyph_def.get("bitmap", [])

        # Validate bitmap width
        # In proportional font, all glyphs use proportional validation
        # In monospace font, only .prop suffixed glyphs use proportional validation
        is_prop_glyph = is_proportional or is_proportional_glyph(glyph_name)
        if bitmap:
            if is_prop_glyph:
                # Proportional glyphs: all rows must have consistent width
                row_widths = [len(row) for row in bitmap]
                if len(set(row_widths)) > 1:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}"
                    )
            else:
                # Monospace glyphs: check width requirements
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
                    # Non-Quikscript glyphs: all rows must have consistent width
                    row_widths = [len(row) for row in bitmap]
                    if len(set(row_widths)) > 1:
                        raise ValueError(
                            f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}"
                        )

        if not bitmap:
            # Empty glyph — use explicit advance_width if set, else mono_width
            width = glyph_def.get("advance_width")
            if width is not None:
                width = int(width * pixel_width)
            else:
                width = mono_width
            pen = T2CharStringPen(width=width, glyphSet=glyph_set)
            charstrings[glyph_name] = pen.getCharString()
            metrics[glyph_name] = (width, 0)
            continue

        # Parse and convert bitmap
        bitmap = parse_bitmap(bitmap)
        y_offset = glyph_def.get("y_offset", 0)  # negative for descenders

        # Validate bitmap height
        row_count = len(bitmap)

        # Check if this is a Quikscript glyph (uniE6xx or uniE6xx.prop)
        base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
        is_quikscript = base_name.startswith("uniE6") or base_name.startswith("qs")

        if is_quikscript:
            # Strict height validation for Quikscript glyphs
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
                raise ValueError(
                    f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9"
                )
        # Non-Quikscript glyphs: no height restrictions

        rectangles = bitmap_to_rectangles(bitmap, pixel_width, pixel_height, y_offset)

        # Calculate advance width
        advance_width = glyph_def.get("advance_width")
        if advance_width is None:
            if is_prop_glyph:
                # Proportional glyphs: bitmap width + 2 pixel spacing
                max_col = max((len(row) for row in bitmap), default=0)
                advance_width = (max_col + 2) * pixel_width
            else:
                # Monospace glyphs: use fixed mono_width
                advance_width = mono_width
        else:
            advance_width *= pixel_width

        # Calculate x_offset: center glyph within advance width
        bitmap_width = max((len(row) for row in bitmap), default=0) * pixel_width
        if advance_width == 0:
            # Zero-width (combining mark): center bitmap on the origin
            x_offset = -(bitmap_width // 2)
        else:
            x_offset = (advance_width - bitmap_width) // 2

        # Calculate left side bearing (LSB) with offset applied
        if advance_width == 0:
            lsb = 0
        elif rectangles:
            lsb = min(r[0] for r in rectangles) + x_offset
        else:
            lsb = x_offset

        # Draw glyph with x_offset applied
        pen = T2CharStringPen(width=advance_width, glyphSet=glyph_set)
        for x, y, w, h in rectangles:
            pen.moveTo((x + x_offset, y))
            pen.lineTo((x + x_offset, y + h))
            pen.lineTo((x + x_offset + w, y + h))
            pen.lineTo((x + x_offset + w, y))
            pen.closePath()

        charstrings[glyph_name] = pen.getCharString()
        metrics[glyph_name] = (advance_width, lsb)

    # Setup CFF table
    ps_name = font_name.replace(" ", "") + "-Regular"
    fb.setupCFF(
        psName=ps_name,
        fontInfo={"FamilyName": font_name, "FullName": f"{font_name} Regular"},
        charStringsDict=charstrings,
        privateDict={}
    )

    # Setup horizontal metrics
    fb.setupHorizontalMetrics(metrics)

    # Setup horizontal header
    fb.setupHorizontalHeader(ascent=ascender, descent=descender)

    # Setup name table
    name_strings = {
        "familyName": {"en": font_name},
        "styleName": {"en": "Regular"},
        "uniqueFontIdentifier": f"FontBuilder:{font_name}.Regular",
        "fullName": {"en": f"{font_name} Regular"},
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

    # Setup OS/2 table
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

    # Setup post table
    # Monospace font: isFixedPitch=1, Proportional font: isFixedPitch=0
    fb.setupPost(isFixedPitch=0 if is_proportional else 1)

    # Setup gasp table for pixel-crisp rendering
    gasp = newTable("gasp")
    gasp.gaspRange = {0xFFFF: 0x0001}  # Grid-fit only, no antialiasing
    fb.font["gasp"] = gasp

    # Add head table (required)
    fb.setupHead(unitsPerEm=units_per_em, fontRevision=version)

    vs_defs = metadata.get("variation_sequences", {})
    cmap14 = build_cmap14(vs_defs, glyphs_def, name_to_codepoint)
    if cmap14:
        fb.font["cmap"].tables.append(cmap14)

    # Compile OpenType features into the proportional font only
    fea_code_parts = []

    kerning_defs = glyph_data.get("kerning", {})
    if is_proportional and kerning_defs:
        kerning_groups = collect_kerning_groups(glyphs_def)
        fea_code_parts.append(generate_kern_fea(
            kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_width
        ))

    if is_proportional:
        mark_fea = generate_mark_fea(glyphs_def, pixel_width, pixel_height)
        if mark_fea:
            fea_code_parts.append(mark_fea)

    if is_senior:
        curs_fea = generate_curs_fea(glyphs_def, pixel_width, pixel_height)
        if curs_fea:
            fea_code_parts.append(curs_fea)

    if is_senior:
        calt_fea = generate_calt_fea(glyphs_def, pixel_width)
        if calt_fea:
            fea_code_parts.append(calt_fea)

    fea_code = None
    if fea_code_parts:
        fea_code = "\n\n".join(fea_code_parts)
        addOpenTypeFeaturesFromString(fb.font, fea_code)

        if output_path is not None:
            fea_path = output_path.with_suffix(".fea")
            fea_path.write_text(fea_code + "\n")
            print(f"  Feature code saved to: {fea_path}")

    if output_path is not None:
        fb.save(str(output_path))
        print(f"Font saved to: {output_path}")

    print(f"  Variant: {variant}")
    print(f"  Glyphs: {len(glyph_order)}")
    print(f"  Units per em: {units_per_em}")
    print(f"  Pixel: {pixel_width}×{pixel_height} units")

    font = fb.font
    font._fea_code = fea_code
    return font


def build_variable_font(glyph_data: dict, output_path: Path, variant: str):
    """
    Build a variable font with a wght axis (ExtraLight 200, Regular 400, Bold 800).

    Weight controls pixel_width relative to a constant pixel_height of 50:
      200 → pixel_width=25  (half-wide pixels)
      400 → pixel_width=50  (square pixels)
      800 → pixel_width=100 (2× wide pixels)

    All three masters share the same bitmap data and feature structure,
    differing only in x-coordinates and advance widths.
    """
    from fontTools.designspaceLib import (
        AxisDescriptor,
        DesignSpaceDocument,
        InstanceDescriptor,
        SourceDescriptor,
    )
    from fontTools.varLib import build as varLib_build

    metadata = glyph_data["metadata"]
    pixel_height = metadata["pixel_size"]

    print(f"\nBuilding variable font: {output_path.name}")

    thin = build_font(glyph_data, variant=variant, pixel_width=pixel_height // 2)
    regular = build_font(glyph_data, variant=variant, pixel_width=pixel_height)
    bold = build_font(glyph_data, variant=variant, pixel_width=pixel_height * 2)

    ds = DesignSpaceDocument()

    axis = AxisDescriptor()
    axis.tag = "wght"
    axis.name = "Weight"
    axis.minimum = 200
    axis.default = 400
    axis.maximum = 800
    ds.addAxis(axis)

    src_thin = SourceDescriptor()
    src_thin.font = thin
    src_thin.location = {"Weight": 200}
    ds.addSource(src_thin)

    src_regular = SourceDescriptor()
    src_regular.font = regular
    src_regular.location = {"Weight": 400}
    ds.addSource(src_regular)

    src_bold = SourceDescriptor()
    src_bold.font = bold
    src_bold.location = {"Weight": 800}
    ds.addSource(src_bold)

    for style_name, wght in (("ExtraLight", 200), ("Regular", 400), ("Bold", 800)):
        inst = InstanceDescriptor()
        inst.name = f"{regular['name'].getDebugName(1)} {style_name}"
        inst.familyName = regular["name"].getDebugName(1)
        inst.styleName = style_name
        inst.location = {"Weight": wght}
        ds.addInstance(inst)

    vf, _, _ = varLib_build(ds)

    fea_code = getattr(regular, "_fea_code", None)
    if fea_code:
        fea_path = output_path.with_suffix(".fea")
        fea_path.write_text(fea_code + "\n")
        print(f"  Feature code saved to: {fea_path}")

    vf.save(str(output_path))
    print(f"Variable font saved to: {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python tools/build_font.py <glyph_data.yaml|glyph_data/> [output_dir]")
        print("\nOutputs:")
        print("  output_dir/AbbotsMortonSpaceportMono.otf")
        print("  output_dir/AbbotsMortonSpaceportSansJunior.otf  (variable, wght 200-800)")
        print("  output_dir/AbbotsMortonSpaceportSansSenior.otf  (variable, wght 200-800)")
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

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    glyph_data = load_glyph_data(input_path)

    # Build monospace font (static, no variable font)
    mono_path = output_dir / "AbbotsMortonSpaceportMono.otf"
    build_font(glyph_data, mono_path, variant="mono")

    # Build proportional Junior font (variable, wght 400-800)
    junior_path = output_dir / "AbbotsMortonSpaceportSansJunior.otf"
    build_variable_font(glyph_data, junior_path, variant="junior")

    # Build proportional Senior font (variable, wght 400-800)
    senior_path = output_dir / "AbbotsMortonSpaceportSansSenior.otf"
    build_variable_font(glyph_data, senior_path, variant="senior")


if __name__ == "__main__":
    main()
