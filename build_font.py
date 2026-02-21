#!/usr/bin/env python3
"""
Build a pixel font from bitmap glyph definitions.
Uses fonttools FontBuilder to create OTF output.

Usage:
    uv run python build_font.py <glyph_data.yaml|glyph_data/> [output_dir]

    The first argument can be a single YAML file or a directory of YAML files.
    When a directory is given, all *.yaml files are loaded and merged.

Outputs:
    output_dir/AbbotsMortonSpaceportMono.otf        - Monospace font
    output_dir/AbbotsMortonSpaceportSansJunior.otf  - Proportional font (no cursive/calt)
    output_dir/AbbotsMortonSpaceportSansSenior.otf  - Proportional font (with cursive/calt)
"""

import sys
from datetime import datetime
from pathlib import Path

import yaml

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import newTable


def load_postscript_glyph_names() -> dict:
    """Load PostScript glyph name to Unicode codepoint mapping from YAML."""
    path = Path(__file__).parent / "postscript_glyph_names.yaml"
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


def is_proportional_glyph(glyph_name: str) -> bool:
    """Check if a glyph is a proportional variant."""
    return glyph_name.endswith(".prop") or ".prop." in glyph_name


def get_base_glyph_name(prop_glyph_name: str) -> str:
    """Get the base glyph name from a proportional glyph name."""
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    return prop_glyph_name


def prepare_proportional_glyphs(glyphs_def: dict) -> dict:
    """
    Prepare glyph definitions for the proportional font variant.

    For the proportional font:
    - .prop glyphs are renamed to their base names (e.g., qsPea.prop → qsPea)
    - Base glyphs that have .prop variants are excluded
    - Glyphs without .prop variants remain unchanged
    """
    # Find all base glyph names that have .prop variants
    prop_base_names = set()
    for glyph_name in glyphs_def.keys():
        if is_proportional_glyph(glyph_name):
            prop_base_names.add(get_base_glyph_name(glyph_name))

    # Build new glyph dict
    new_glyphs = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            # Rename .prop glyph to its base name
            base_name = get_base_glyph_name(glyph_name)
            new_glyphs[base_name] = glyph_def
        elif glyph_name in prop_base_names:
            # Skip base glyphs that have .prop variants
            continue
        else:
            # Keep glyphs without .prop variants unchanged
            new_glyphs[glyph_name] = glyph_def

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
    pixel_size: int,
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
        value = definition["value"] * pixel_size
        left = " ".join(sorted(left_glyphs))
        right = " ".join(right_glyphs)
        lines.append(f"    lookup kern_{tag_name} {{")
        lines.append(f"        pos [{left}] [{right}] {value};")
        lines.append(f"    }} kern_{tag_name};")
    lines.append("} kern;")
    return "\n".join(lines)


def generate_mark_fea(glyphs_def: dict, pixel_size: int) -> str | None:
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
            anchor_y = y_offset * pixel_size
            if has_adjustments:
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, True, base_x_adjust or {}, base_y_adjust or {})
            else:
                top_marks[glyph_name] = (anchor_x, anchor_y)
        else:
            # Bottom mark: anchor at the top of the drawn pixels
            bitmap_height = len(bitmap) if bitmap else 0
            anchor_y = (y_offset + bitmap_height) * pixel_size
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
            aw = advance_width * pixel_size
        else:
            bitmap = glyph_def.get("bitmap", [])
            if bitmap:
                max_col = max((len(row) for row in bitmap), default=0)
                aw = (max_col + 2) * pixel_size
            else:
                continue
        base_x = aw // 2
        if "top_mark_y" in glyph_def:
            top_x = base_x + glyph_def.get("top_mark_x", 0) * pixel_size
            base_y = glyph_def["top_mark_y"] * pixel_size
            top_bases[glyph_name] = (top_x, base_y)
        if "bottom_mark_y" in glyph_def:
            bottom_x = base_x + glyph_def.get("bottom_mark_x", 0) * pixel_size
            base_y = glyph_def["bottom_mark_y"] * pixel_size
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
            x_adj = base_x_adjust.get(glyph_name, 0) * pixel_size
            y_adj = base_y_adjust.get(glyph_name, 0) * pixel_size
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


def _is_contextual_variant(glyph_name: str) -> bool:
    """Check if a glyph name is a contextual variant (entry-*, exit-*, or half)."""
    parts = glyph_name.split(".")[1:]
    return any(
        p.startswith("entry-") or p.startswith("exit-") or p == "half"
        for p in parts
    )


def generate_calt_fea(glyphs_def: dict, pixel_size: int) -> str | None:
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
    # Pair-specific overrides: entry variants with calt_after lists
    pair_overrides: dict[str, list[tuple[str, list[str]]]] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or not _is_entry_variant(glyph_name):
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
        calt_after = glyph_def.get("calt_after")
        if calt_after:
            pair_overrides.setdefault(base_name, []).append(
                (glyph_name, list(calt_after))
            )
        else:
            bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name

    # --- Forward-looking: exit variants keyed by exit Y ---
    # Detects any variant with cursive_exit (catches .exit-* names and
    # .half variants alike). Entry variants (entry-* names) are excluded
    # since they are handled by the backward-looking rules.
    fwd_replacements: dict[str, dict[int, str]] = {}
    fwd_exclusions: dict[str, dict[int, list[str]]] = {}
    # Pair-specific forward overrides: variants with calt_before lists
    fwd_pair_overrides: dict[str, list[tuple[str, list[str]]]] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or "." not in glyph_name:
            continue
        if _is_entry_variant(glyph_name) or glyph_name.endswith(".noentry"):
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
            fwd_pair_overrides.setdefault(base_name, []).append(
                (glyph_name, resolved)
            )
        else:
            fwd_replacements.setdefault(base_name, {})[exit_y] = glyph_name
            not_before = glyph_def.get("calt_not_before")
            if not_before:
                resolved = [get_base_glyph_name(g) if g not in glyphs_def else g for g in not_before]
                fwd_exclusions.setdefault(base_name, {})[exit_y] = resolved

    if not bk_replacements and not fwd_replacements:
        return None

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
            if _is_entry_variant(glyph_name):
                base_name = glyph_name.split(".")[0]
                if base_name in glyphs_def:
                    entry_classes[anchor[1]].add(base_name)

    # --- Topological sort for backward-looking lookups ---
    # Only consider exit heights INTRODUCED by entry variants (not already
    # present on the base glyph) to avoid spurious dependency cycles.
    base_exit_ys: dict[str, set[int]] = {}
    for base_name, variants in bk_replacements.items():
        base_def = glyphs_def.get(base_name, {})
        base_ys = set()
        if base_def:
            raw_exit = base_def.get("cursive_exit")
            if raw_exit:
                for anchor in _normalize_anchors(raw_exit):
                    base_ys.add(anchor[1])
        new_exit_ys = set()
        for variant_name in variants.values():
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

    sorted_bases: list[str] = []
    visited: set[str] = set()
    temp: set[str] = set()

    def visit(node: str):
        if node in temp:
            raise ValueError(f"Circular dependency in calt lookups involving {node}")
        if node in visited:
            return
        temp.add(node)
        for dep in sorted(edges[node]):
            visit(dep)
        temp.remove(node)
        visited.add(node)
        sorted_bases.append(node)

    for base in sorted(base_order):
        visit(base)

    # --- Generate FEA ---
    bk_used_ys = set()
    for variants in bk_replacements.values():
        bk_used_ys.update(variants.keys())

    fwd_used_ys = set()
    for variants in fwd_replacements.values():
        fwd_used_ys.update(variants.keys())

    lines = ["feature calt {"]

    for y in sorted(bk_used_ys):
        if y in exit_classes:
            members = sorted(exit_classes[y])
            lines.append(f"    @exit_y{y} = [{' '.join(members)}];")

    for y in sorted(fwd_used_ys):
        if y in entry_classes:
            members = sorted(entry_classes[y])
            lines.append(f"    @entry_y{y} = [{' '.join(members)}];")

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

    # Lookup ordering: forward-only bases run first so they can change a
    # glyph's exit before backward rules for the *following* glyph commit to
    # an entry variant.  For bases with both backward and forward rules, the
    # backward lookup runs first (so a preceding exit wins over a following
    # entry), then its forward companion.
    def _emit_fwd(base_name: str):
        # Pair-specific forward overrides run first so they win over general
        if base_name in fwd_pair_overrides:
            for variant_name, before_glyphs in fwd_pair_overrides[base_name]:
                before_list = " ".join(sorted(before_glyphs))
                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_fwd_pair_{safe} {{")
                lines.append(
                    f"        sub {base_name}' [{before_list}] by {variant_name};"
                )
                lines.append(f"    }} calt_fwd_pair_{safe};")
        # General forward rule
        if base_name in fwd_replacements:
            variants = fwd_replacements[base_name]
            exclusions = fwd_exclusions.get(base_name, {})
            lookup_name = f"calt_fwd_{base_name}"
            lines.append("")
            lines.append(f"    lookup {lookup_name} {{")
            for exit_y in sorted(variants.keys(), reverse=True):
                variant_name = variants[exit_y]
                if exit_y in entry_classes:
                    excluded = exclusions.get(exit_y, [])
                    for eg in sorted(excluded):
                        lines.append(f"        ignore sub {base_name}' {eg};")
                    lines.append(f"        sub {base_name}' @entry_y{exit_y} by {variant_name};")
            lines.append(f"    }} {lookup_name};")

    def _emit_bk(base_name: str):
        # Pair-specific overrides run first so they win over the general rule
        if base_name in pair_overrides:
            for variant_name, after_glyphs in pair_overrides[base_name]:
                after_list = " ".join(sorted(after_glyphs))
                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_pair_{safe} {{")
                lines.append(
                    f"        sub [{after_list}] {base_name}' by {variant_name};"
                )
                lines.append(f"    }} calt_pair_{safe};")
        # General backward rule
        if base_name in bk_replacements:
            variants = bk_replacements[base_name]
            lookup_name = f"calt_{base_name}"
            lines.append("")
            lines.append(f"    lookup {lookup_name} {{")
            for entry_y in sorted(variants.keys()):
                variant_name = variants[entry_y]
                if entry_y in exit_classes:
                    lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
            lines.append(f"    }} {lookup_name};")

    # Add pair-override-only bases to sorted_bases (after the topo-sorted ones)
    pair_only = sorted(set(pair_overrides) - set(bk_replacements))
    all_bk_bases = sorted_bases + pair_only

    all_fwd_bases = set(fwd_replacements) | set(fwd_pair_overrides)
    fwd_only = sorted(all_fwd_bases - set(bk_replacements) - set(pair_overrides))
    for base_name in fwd_only:
        _emit_fwd(base_name)

    for base_name in all_bk_bases:
        _emit_bk(base_name)
        if base_name in all_fwd_bases:
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


def generate_curs_fea(glyphs_def: dict, pixel_size: int) -> str | None:
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
        raw_exit = glyph_def.get("cursive_exit")
        if raw_entry is None and raw_exit is None:
            continue
        entries = _normalize_anchors(raw_entry)
        exits = _normalize_anchors(raw_exit)
        y_values = {a[1] for a in entries} | {a[1] for a in exits}
        for y in y_values:
            entry_anchor = "<anchor NULL>"
            exit_anchor = "<anchor NULL>"
            for a in entries:
                if a[1] == y:
                    entry_anchor = f"<anchor {a[0] * pixel_size} {a[1] * pixel_size}>"
                    break
            for a in exits:
                if a[1] == y:
                    exit_anchor = f"<anchor {a[0] * pixel_size} {a[1] * pixel_size}>"
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
        noentry_def = {k: v for k, v in gdef.items() if k != "cursive_entry"}
        noentry_def["_noentry_for"] = name
        variants[name + ".noentry"] = noentry_def
    return variants


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
    pixel_size: int,
    y_offset: int = 0
) -> list[tuple[int, int, int, int]]:
    """
    Convert a 2D bitmap array to a list of rectangle coordinates.

    Args:
        bitmap: 2D array of 0s and 1s
        pixel_size: size of each pixel in font units
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
        y = (y_offset + height - 1 - row_idx) * pixel_size

        for col_idx, pixel in enumerate(row):
            if pixel:  # Pixel is "on"
                x = col_idx * pixel_size
                rectangles.append((x, y, pixel_size, pixel_size))

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


def build_font(glyph_data: dict, output_path: Path, variant: str = "mono"):
    """
    Build font from glyph data dictionary.
    Creates a CFF-based OpenType font (.otf).

    Args:
        glyph_data: Dictionary containing metadata and glyph definitions
        output_path: Path to write the font file
        variant: "mono", "junior", or "senior"
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

    # Font name differs per variant
    base_font_name = metadata["font_name"]
    suffixes = {"mono": " Mono", "junior": " Sans Junior", "senior": " Sans Senior"}
    font_name = base_font_name + suffixes[variant]
    version = metadata["version"]
    units_per_em = metadata["units_per_em"]
    pixel_size = metadata["pixel_size"]
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
    glyph_order = [".notdef", "space"] + sorted(glyph_names)

    # Build character map (Unicode codepoint -> glyph name)
    # Exclude .prop glyphs - they have no direct Unicode mapping
    postscript_glyph_names = load_postscript_glyph_names()
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
    mono_width = 7 * pixel_size  # 350 units

    # Create .notdef glyph (simple rectangle, sized to fit mono_width)
    pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
    pen.moveTo((50, 0))
    pen.lineTo((50, 250))
    pen.lineTo((250, 250))
    pen.lineTo((250, 0))
    pen.closePath()
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (mono_width, 50)

    # Create space glyph (empty)
    space_def = glyphs_def.get("space", {})
    space_width = space_def["advance_width"] * pixel_size
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
                width = int(width * pixel_size)
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
                if row_count != 9:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has y_offset=-3 but bitmap has {row_count} rows, expected 9"
                    )
            elif row_count not in (6, 9):
                raise ValueError(
                    f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9"
                )
        # Non-Quikscript glyphs: no height restrictions

        rectangles = bitmap_to_rectangles(bitmap, pixel_size, y_offset)

        # Calculate advance width
        advance_width = glyph_def.get("advance_width")
        if advance_width is None:
            if is_prop_glyph:
                # Proportional glyphs: bitmap width + 2 pixel spacing
                max_col = max((len(row) for row in bitmap), default=0)
                advance_width = (max_col + 2) * pixel_size
            else:
                # Monospace glyphs: use fixed mono_width
                advance_width = mono_width
        else:
            advance_width *= pixel_size

        # Calculate x_offset: center glyph within advance width
        bitmap_width = max((len(row) for row in bitmap), default=0) * pixel_size
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

    # Compile OpenType features into the proportional font only
    fea_code_parts = []

    kerning_defs = glyph_data.get("kerning", {})
    if is_proportional and kerning_defs:
        kerning_groups = collect_kerning_groups(glyphs_def)
        fea_code_parts.append(generate_kern_fea(
            kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_size
        ))

    if is_proportional:
        mark_fea = generate_mark_fea(glyphs_def, pixel_size)
        if mark_fea:
            fea_code_parts.append(mark_fea)

    if is_senior:
        liga_fea = generate_liga_fea(glyphs_def)
        if liga_fea:
            fea_code_parts.append(liga_fea)

    if is_senior:
        curs_fea = generate_curs_fea(glyphs_def, pixel_size)
        if curs_fea:
            fea_code_parts.append(curs_fea)

    if is_senior:
        calt_fea = generate_calt_fea(glyphs_def, pixel_size)
        if calt_fea:
            fea_code_parts.append(calt_fea)

    if fea_code_parts:
        fea_code = "\n\n".join(fea_code_parts)
        addOpenTypeFeaturesFromString(fb.font, fea_code)

        # Write .fea file alongside the font
        fea_path = output_path.with_suffix(".fea")
        fea_path.write_text(fea_code + "\n")
        print(f"  Feature code saved to: {fea_path}")

    # Save font
    fb.save(str(output_path))

    # Print summary
    print(f"Font saved to: {output_path}")
    print(f"  Variant: {variant}")
    print(f"  Glyphs: {len(glyph_order)}")
    print(f"  Units per em: {units_per_em}")
    print(f"  Pixel size: {pixel_size} units")


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python build_font.py <glyph_data.yaml|glyph_data/> [output_dir]")
        print("\nOutputs:")
        print("  output_dir/AbbotsMortonSpaceportMono.otf")
        print("  output_dir/AbbotsMortonSpaceportSansJunior.otf")
        print("  output_dir/AbbotsMortonSpaceportSansSenior.otf")
        print("\nExample:")
        print("  uv run python build_font.py glyph_data/ build/")
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

    # Build monospace font
    mono_path = output_dir / "AbbotsMortonSpaceportMono.otf"
    build_font(glyph_data, mono_path, variant="mono")

    # Build proportional Junior font (no curs/calt)
    junior_path = output_dir / "AbbotsMortonSpaceportSansJunior.otf"
    build_font(glyph_data, junior_path, variant="junior")

    # Build proportional Senior font (with curs/calt)
    senior_path = output_dir / "AbbotsMortonSpaceportSansSenior.otf"
    build_font(glyph_data, senior_path, variant="senior")


if __name__ == "__main__":
    main()
