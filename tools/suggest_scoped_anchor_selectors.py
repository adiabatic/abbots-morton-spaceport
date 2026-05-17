"""Suggest family-scoped anchor selectors for overbroad Quikscript selectors.

The tool is read-only: it reports selectors such as ``{family: qsMay}`` where
the selected form requires a specific opposite anchor Y and some concrete
variants in that family do not provide it.

Usage::

    uv run python tools/suggest_scoped_anchor_selectors.py
    uv run python tools/suggest_scoped_anchor_selectors.py --family qsPea
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_font import load_glyph_data
from quikscript_ir import (
    GlyphData,
    JoinGlyph,
    _compiled_family_glyph_name,
    _normalize_source_modifiers,
    _normalize_source_traits,
    _resolve_family_record,
    compile_quikscript_ir,
)


@dataclass(frozen=True)
class ScopedAnchorSuggestion:
    path: str
    current: str
    suggested: str
    incompatible: tuple[str, ...]
    compatible: tuple[str, ...] = ()
    family_name: str = ""
    record_name: str = ""
    record_kind: str = ""
    field_name: str = ""
    selector_index: int = -1
    selected_name: str = ""
    target_family: str = ""
    family_scope: str = ""
    selected_side: str = ""
    target_side: str = ""
    anchor_key: str = ""
    required_y: int | None = None


def _selector_text(selector: dict[str, Any]) -> str:
    parts = [f"family: {selector['family']}"]
    if selector.get("traits"):
        parts.append("traits: [" + ", ".join(selector["traits"]) + "]")
    if selector.get("modifiers"):
        parts.append("modifiers: [" + ", ".join(selector["modifiers"]) + "]")
    if "exit_y" in selector:
        parts.append(f"exit_y: {selector['exit_y']}")
    if "entry_y" in selector:
        parts.append(f"entry_y: {selector['entry_y']}")
    return "{" + ", ".join(parts) + "}"


def _scope_matches(name: str, meta: JoinGlyph, scope: str) -> bool:
    if "." not in scope:
        return meta.base_name == scope
    return name == scope or name.startswith(scope + ".")


def _scope_candidates(
    join_glyphs: dict[str, JoinGlyph],
    scope: str,
) -> tuple[JoinGlyph, ...]:
    return tuple(meta for name, meta in sorted(join_glyphs.items()) if _scope_matches(name, meta, scope))


def _anchor_ys(meta: JoinGlyph, side: str) -> set[int]:
    if side == "entry":
        return {anchor[1] for anchor in (*meta.entry, *meta.entry_curs_only)}
    return {anchor[1] for anchor in meta.exit}


def _selector_scope(
    selector: Any,
    *,
    family_names: set[str],
    context_family: str,
    context_label: str,
    field_name: str,
) -> str | None:
    if not isinstance(selector, dict):
        return None
    if "context_set" in selector or "entry_y" in selector or "exit_y" in selector:
        return None
    if "why_not_narrower" in selector:
        return None
    if set(selector) - {"family", "traits", "modifiers"}:
        return None
    target_family = selector.get("family")
    if not isinstance(target_family, str) or target_family not in family_names:
        return None
    traits = _normalize_source_traits(
        selector.get("traits", ()),
        family_name=context_family,
        context=f"{context_label} {field_name}",
    )
    modifiers = _normalize_source_modifiers(
        selector.get("modifiers", ()),
        family_name=context_family,
        context=f"{context_label} {field_name}",
    )
    return _compiled_family_glyph_name(target_family, traits, modifiers)


def _iter_source_records(
    family_name: str,
    family_def: dict[str, Any],
):
    if family_def.get("prop"):
        yield (
            "prop",
            family_def["prop"],
            f"glyph_families.{family_name}.prop",
            family_name,
            "prop",
        )
    for form_name, raw in (family_def.get("forms") or {}).items():
        yield (
            form_name,
            raw,
            f"glyph_families.{family_name}.forms.{form_name}",
            None,
            "forms",
        )


def suggest_scoped_anchor_selectors(
    glyph_data: GlyphData,
    *,
    variant: str = "senior",
    family_filter: str | None = None,
) -> list[ScopedAnchorSuggestion]:
    glyph_families = glyph_data.get("glyph_families", {})
    family_names = set(glyph_families)
    join_glyphs, _ = compile_quikscript_ir(glyph_data, variant)
    suggestions: list[ScopedAnchorSuggestion] = []

    for family_name, family_def in glyph_families.items():
        if family_filter and family_name != family_filter:
            continue
        cache: dict[str, dict[str, Any]] = {}
        for record_name, raw, path, base_output_name, record_kind in _iter_source_records(
            family_name,
            family_def,
        ):
            if not isinstance(raw, dict):
                continue
            raw_select = raw.get("select")
            if not isinstance(raw_select, dict):
                continue
            if base_output_name is None:
                resolved = _resolve_family_record(
                    family_name,
                    family_def,
                    record_name,
                    cache,
                    [],
                    glyph_families=glyph_families,
                )
                traits = _normalize_source_traits(
                    resolved.get("traits", ()),
                    family_name=family_name,
                    context=f"form {record_name!r}",
                )
                modifiers = _normalize_source_modifiers(
                    resolved.get("modifiers", ()),
                    family_name=family_name,
                    context=f"form {record_name!r}",
                )
                output_name = _compiled_family_glyph_name(family_name, traits, modifiers)
                context_label = f"form {record_name!r}"
            else:
                output_name = base_output_name
                context_label = "base record"
            selected = join_glyphs.get(output_name)
            if selected is None:
                continue

            for field_name, selected_side, target_side, anchor_key in (
                ("after", "entry", "exit", "exit_y"),
                ("before", "exit", "entry", "entry_y"),
            ):
                selectors = raw_select.get(field_name)
                if not isinstance(selectors, list):
                    continue
                selected_ys = _anchor_ys(selected, selected_side)
                if len(selected_ys) != 1:
                    continue
                y = next(iter(selected_ys))
                for index, selector in enumerate(selectors):
                    scope = _selector_scope(
                        selector,
                        family_names=family_names,
                        context_family=family_name,
                        context_label=context_label,
                        field_name=field_name,
                    )
                    if scope is None:
                        continue
                    candidates = _scope_candidates(join_glyphs, scope)
                    if not candidates:
                        continue
                    compatible = tuple(
                        candidate for candidate in candidates if y in _anchor_ys(candidate, target_side)
                    )
                    if not compatible or len(compatible) == len(candidates):
                        continue
                    suggested_selector = dict(selector)
                    suggested_selector[anchor_key] = y
                    suggestions.append(
                        ScopedAnchorSuggestion(
                            path=f"{path}.select.{field_name}[{index}]",
                            current=_selector_text(selector),
                            suggested=_selector_text(suggested_selector),
                            compatible=tuple(candidate.name for candidate in compatible),
                            incompatible=tuple(
                                candidate.name
                                for candidate in candidates
                                if y not in _anchor_ys(candidate, target_side)
                            ),
                            family_name=family_name,
                            record_name=record_name,
                            record_kind=record_kind,
                            field_name=field_name,
                            selector_index=index,
                            selected_name=selected.name,
                            target_family=selector["family"],
                            family_scope=scope,
                            selected_side=selected_side,
                            target_side=target_side,
                            anchor_key=anchor_key,
                            required_y=y,
                        )
                    )

    return suggestions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "glyph_data",
        nargs="?",
        type=Path,
        default=ROOT / "glyph_data",
        help="Glyph data YAML file or directory (default: glyph_data/).",
    )
    parser.add_argument("--variant", default="senior", help="Compiled variant to inspect.")
    parser.add_argument("--family", help="Only inspect selectors authored on this family.")
    parser.add_argument(
        "--show-incompatible",
        type=int,
        default=8,
        help="Number of incompatible concrete variants to print per suggestion.",
    )
    args = parser.parse_args()

    data = load_glyph_data(args.glyph_data)
    suggestions = suggest_scoped_anchor_selectors(
        data,
        variant=args.variant,
        family_filter=args.family,
    )
    if not suggestions:
        print("No family-scoped anchor selector suggestions.")
        return

    for suggestion in suggestions:
        print(f"{suggestion.path}: {suggestion.current} -> {suggestion.suggested}")
        shown = suggestion.incompatible[: args.show_incompatible]
        if shown:
            print("  incompatible: " + ", ".join(shown))
        remaining = len(suggestion.incompatible) - len(shown)
        if remaining > 0:
            print(f"  ...and {remaining} more")


if __name__ == "__main__":
    main()
