"""Build an HTML review page for scoped anchor selector suggestions.

The tool is read-only with respect to source data: it applies suggested
``entry_y`` / ``exit_y`` selector scopes to an in-memory copy of glyph data,
builds temporary Senior-Regular fonts under ``tmp/``, and writes an HTML page
showing selector expansion and dropped-match cases.

Usage::

    uv run python tools/review_scoped_anchor_selectors.py
    uv run python tools/review_scoped_anchor_selectors.py --family qsPea
    uv run python tools/review_scoped_anchor_selectors.py --path 'glyph_families.qsPea.forms.entry_xheight.select.after[0]'
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_font import build_font, load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import GlyphData, JoinGlyph
from suggest_scoped_anchor_selectors import (
    ScopedAnchorSuggestion,
    suggest_scoped_anchor_selectors,
)

DEFAULT_OUTPUT = ROOT / "tmp" / "scoped-anchor-review" / "index.html"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"
SENIOR_FONT_NAME = "AbbotsMortonSpaceportSansSenior-Regular.otf"


@dataclass(frozen=True)
class ShapedRun:
    families: tuple[str, ...]
    text: str
    glyphs: tuple[str, ...]


@dataclass(frozen=True)
class DroppedMatchCase:
    current: ShapedRun
    scoped: ShapedRun
    dropped_glyph: str
    feature_tag: str | None


def apply_suggestions_to_glyph_data(
    glyph_data: GlyphData,
    suggestions: list[ScopedAnchorSuggestion],
) -> GlyphData:
    """Return a deep-copied glyph data dict with *suggestions* applied."""
    patched = deepcopy(glyph_data)
    glyph_families = patched["glyph_families"]
    for suggestion in suggestions:
        if suggestion.required_y is None:
            raise ValueError(f"{suggestion.path} has no required_y metadata")
        family = glyph_families[suggestion.family_name]
        if suggestion.record_kind == "prop":
            record = family["prop"]
        elif suggestion.record_kind == "forms":
            record = family["forms"][suggestion.record_name]
        else:
            raise ValueError(
                f"{suggestion.path} has unknown record kind {suggestion.record_kind!r}"
            )
        selector = record["select"][suggestion.field_name][suggestion.selector_index]
        selector[suggestion.anchor_key] = suggestion.required_y
    return patched


def _load_ps_names() -> dict[str, int]:
    with PS_NAMES_PATH.open() as f:
        return yaml.safe_load(f)


def _plain_quikscript_families(
    ps_names: dict[str, int],
    glyph_data: GlyphData,
) -> tuple[str, ...]:
    glyph_families = glyph_data.get("glyph_families", {})
    rows = [
        (codepoint, name)
        for name, codepoint in ps_names.items()
        if name.startswith("qs")
        and "_" not in name
        and name not in {"qsAngleParenLeft", "qsAngleParenRight"}
        and name in glyph_families
    ]
    return tuple(name for _, name in sorted(rows))


def _family_sequence(glyph_data: GlyphData, family: str) -> tuple[str, ...]:
    family_def = glyph_data.get("glyph_families", {}).get(family)
    if isinstance(family_def, dict):
        sequence = family_def.get("sequence")
        if isinstance(sequence, list) and all(isinstance(item, str) for item in sequence):
            return tuple(sequence)
    return (family,)


def _families_to_text(families: tuple[str, ...], ps_names: dict[str, int]) -> str:
    missing = [family for family in families if family not in ps_names]
    if missing:
        raise ValueError(f"cannot shape families without codepoints: {', '.join(missing)}")
    return "".join(chr(ps_names[family]) for family in families)


def _hb_font(font_path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(font_path))
    return hb.Font(hb.Face(blob))


def _shape(font: hb.Font, text: str, features: dict[str, bool]) -> tuple[str, ...]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features)
    return tuple(font.glyph_to_string(info.codepoint) for info in buf.glyph_infos)


def _shape_families(
    font: hb.Font,
    families: tuple[str, ...],
    ps_names: dict[str, int],
    features: dict[str, bool],
) -> ShapedRun:
    text = _families_to_text(families, ps_names)
    return ShapedRun(families=families, text=text, glyphs=_shape(font, text, features))


def _input_spans(
    glyphs: tuple[str, ...],
    meta_map: dict[str, JoinGlyph],
) -> tuple[tuple[int, int], ...] | None:
    consumed = 0
    spans: list[tuple[int, int]] = []
    for glyph in glyphs:
        meta = meta_map.get(glyph)
        seq_len = len(meta.sequence) if meta is not None and meta.sequence else 1
        spans.append((consumed, consumed + seq_len))
        consumed += seq_len
    return tuple(spans)


def _is_selected_variant(
    name: str,
    selected_name: str,
    meta_map: dict[str, JoinGlyph],
) -> bool:
    current: str | None = name
    seen: set[str] = set()
    while current and current not in seen:
        if current == selected_name:
            return True
        seen.add(current)
        meta = meta_map.get(current)
        current = meta.generated_from if meta is not None else None
    return False


def _features_for_suggestion(
    suggestion: ScopedAnchorSuggestion,
    meta_map: dict[str, JoinGlyph],
) -> dict[str, bool]:
    selected = meta_map.get(suggestion.selected_name)
    if selected is not None and selected.gate_feature:
        return {selected.gate_feature: True}
    return {}


def _feature_tag_for_suggestion(
    suggestion: ScopedAnchorSuggestion,
    meta_map: dict[str, JoinGlyph],
) -> str | None:
    selected = meta_map.get(suggestion.selected_name)
    return selected.gate_feature if selected is not None else None


def _sequence_candidates(
    glyph_data: GlyphData,
    suggestion: ScopedAnchorSuggestion,
    context_families: tuple[str, ...],
    max_len: int,
) -> list[tuple[tuple[str, ...], int, int]]:
    source_seq = _family_sequence(glyph_data, suggestion.family_name)
    target_seq = _family_sequence(glyph_data, suggestion.target_family)
    if suggestion.field_name == "after":
        base = target_seq + source_seq
        source_start_in_base = len(target_seq)
    else:
        base = source_seq + target_seq
        source_start_in_base = 0
    source_len = len(source_seq)

    results: list[tuple[tuple[str, ...], int, int]] = []
    seen: set[tuple[str, ...]] = set()

    def add(prefix: tuple[str, ...], suffix: tuple[str, ...]) -> None:
        families = prefix + base + suffix
        if families in seen:
            return
        if len(families) > max_len and len(families) > len(base):
            return
        seen.add(families)
        source_start = len(prefix) + source_start_in_base
        results.append((families, source_start, source_start + source_len))

    add((), ())
    if max_len >= len(base) + 1:
        for context_family in context_families:
            add((context_family,), ())
            add((), (context_family,))
    if max_len >= len(base) + 2:
        for before_family in context_families:
            for after_family in context_families:
                add((before_family,), (after_family,))
    return results


def _candidate_incompatibility(
    suggestion: ScopedAnchorSuggestion,
    run: ShapedRun,
    meta_map: dict[str, JoinGlyph],
    source_start: int,
    source_end: int,
) -> str | None:
    spans = _input_spans(run.glyphs, meta_map)
    if spans is None:
        return None
    incompatible = set(suggestion.incompatible)
    for index, glyph in enumerate(run.glyphs):
        if spans[index] != (source_start, source_end):
            continue
        if not _is_selected_variant(glyph, suggestion.selected_name, meta_map):
            continue
        if suggestion.field_name == "after":
            if index == 0 or spans[index - 1][1] != source_start:
                continue
            if run.glyphs[index - 1] in incompatible:
                return run.glyphs[index - 1]
        else:
            if index + 1 >= len(run.glyphs) or spans[index + 1][0] != source_end:
                continue
            if run.glyphs[index + 1] in incompatible:
                return run.glyphs[index + 1]
    return None


def find_dropped_match_cases(
    suggestion: ScopedAnchorSuggestion,
    *,
    glyph_data: GlyphData,
    ps_names: dict[str, int],
    context_families: tuple[str, ...],
    current_font: hb.Font,
    scoped_font: hb.Font,
    current_meta: dict[str, JoinGlyph],
    max_len: int,
    max_cases: int,
) -> list[DroppedMatchCase]:
    features = _features_for_suggestion(suggestion, current_meta)
    feature_tag = _feature_tag_for_suggestion(suggestion, current_meta)
    cases: list[DroppedMatchCase] = []
    for families, source_start, source_end in _sequence_candidates(
        glyph_data,
        suggestion,
        context_families,
        max_len,
    ):
        try:
            current = _shape_families(current_font, families, ps_names, features)
        except ValueError:
            continue
        incompatible = _candidate_incompatibility(
            suggestion,
            current,
            current_meta,
            source_start,
            source_end,
        )
        if incompatible is None:
            continue
        scoped = _shape_families(scoped_font, families, ps_names, features)
        cases.append(
            DroppedMatchCase(
                current=current,
                scoped=scoped,
                dropped_glyph=incompatible,
                feature_tag=feature_tag,
            )
        )
        if len(cases) >= max_cases:
            break
    return cases


def _build_review_font(glyph_data: GlyphData, output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        font = build_font(glyph_data, output_path, variant="senior", bold=False)
    font.close()
    return buffer.getvalue()


def _anchor_ys_text(meta: JoinGlyph | None) -> str:
    if meta is None:
        return "missing"
    entries = sorted(set(meta.all_entry_ys))
    exits = sorted(set(meta.exit_ys))
    bits = [f"entry={entries or '-'}", f"exit={exits or '-'}"]
    if meta.entry_explicitly_none:
        bits.append("entry=null")
    if meta.gate_feature:
        bits.append(f"gate={meta.gate_feature}")
    if meta.generated_from:
        bits.append(f"from={meta.generated_from}")
    return "; ".join(bits)


def _family_label(family: str) -> str:
    special = {"qsIng": "-ing", "qsJai": "J'ai"}
    if family in special:
        return "\u00b7" + special[family]
    if family.startswith("qs"):
        return "\u00b7" + family[2:]
    return family


def _family_labels(families: tuple[str, ...]) -> str:
    return " ".join(_family_label(family) for family in families)


def _glyphs_text(glyphs: tuple[str, ...]) -> str:
    return " | ".join(glyphs)


def _text_entities(text: str) -> str:
    return "".join(f"&#x{ord(char):X};" for char in text)


def _rows_for_variants(
    names: tuple[str, ...],
    meta_map: dict[str, JoinGlyph],
    *,
    limit: int = 18,
) -> str:
    rows = []
    for name in names[:limit]:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(name)}</code></td>"
            f"<td>{html.escape(_anchor_ys_text(meta_map.get(name)))}</td>"
            "</tr>"
        )
    remaining = len(names) - limit
    if remaining > 0:
        rows.append(
            "<tr>"
            f"<td colspan=\"2\">and {remaining} more</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _dropped_match_rows(cases: list[DroppedMatchCase]) -> str:
    if not cases:
        return '<p class="empty">No dropped-match case found in the configured search depth.</p>'
    articles = []
    for case in cases:
        feature = f" +{case.feature_tag}" if case.feature_tag else ""
        changed = "yes" if case.current.glyphs != case.scoped.glyphs else "no"
        articles.append(
            "<article class=\"dropped-match-case\">"
            "<header class=\"case-meta\">"
            f"<span><strong>Sequence</strong> {html.escape(_family_labels(case.current.families))}{html.escape(feature)}</span>"
            f"<span><strong>Changed</strong> {changed}</span>"
            f"<span><strong>Dropped match</strong> <code>{html.escape(case.dropped_glyph)}</code></span>"
            "</header>"
            "<div class=\"comparison-grid\">"
            "<section>"
            "<h4>Current</h4>"
            f"<span class=\"qs current\">{_text_entities(case.current.text)}</span>"
            f"<code>{html.escape(_glyphs_text(case.current.glyphs))}</code>"
            "</section>"
            "<section>"
            "<h4>Scoped</h4>"
            f"<span class=\"qs scoped\">{_text_entities(case.scoped.text)}</span>"
            f"<code>{html.escape(_glyphs_text(case.scoped.glyphs))}</code>"
            "</section>"
            "</div>"
            "</article>"
        )
    return f"<div class=\"dropped-match-cases\">{''.join(articles)}</div>"


def _suggestion_card(
    suggestion: ScopedAnchorSuggestion,
    cases: list[DroppedMatchCase],
    meta_map: dict[str, JoinGlyph],
) -> str:
    compatible_rows = _rows_for_variants(suggestion.compatible, meta_map)
    incompatible_rows = _rows_for_variants(suggestion.incompatible, meta_map)
    scoped_anchor = f"{suggestion.anchor_key}: {suggestion.required_y}"
    why = (
        f"<code>{html.escape(suggestion.selected_name)}</code> has "
        f"{html.escape(suggestion.selected_side)} y={suggestion.required_y}; "
        f"the opposite side must provide {html.escape(suggestion.target_side)} y={suggestion.required_y}."
    )
    return f"""
<section class="suggestion" id="{html.escape(suggestion.path)}">
  <header>
    <h2><code>{html.escape(suggestion.path)}</code></h2>
    <p><code>{html.escape(suggestion.current)}</code> -&gt; <code>{html.escape(suggestion.suggested)}</code></p>
    <p>{why}</p>
  </header>
  <div class="variant-grid">
    <section>
      <h3>Variants still matched if you add <code>{html.escape(scoped_anchor)}</code> ({len(suggestion.compatible)})</h3>
      <table><tbody>{compatible_rows}</tbody></table>
    </section>
    <section>
      <h3>Variants no longer matched if you add <code>{html.escape(scoped_anchor)}</code> ({len(suggestion.incompatible)})</h3>
      <table><tbody>{incompatible_rows}</tbody></table>
    </section>
  </div>
  <h3>Dropped-match cases</h3>
  {_dropped_match_rows(cases)}
</section>
"""


def _html_page(
    *,
    suggestions: list[ScopedAnchorSuggestion],
    case_map: dict[str, list[DroppedMatchCase]],
    meta_map: dict[str, JoinGlyph],
    current_font: Path,
    scoped_font: Path,
    output_path: Path,
    max_len: int,
) -> str:
    current_font_rel = os.path.relpath(current_font, output_path.parent)
    scoped_font_rel = os.path.relpath(scoped_font, output_path.parent)
    cards = "\n".join(
        _suggestion_card(suggestion, case_map.get(suggestion.path, []), meta_map)
        for suggestion in suggestions
    )
    case_count = sum(1 for suggestion in suggestions if case_map.get(suggestion.path))
    suggestion_label = "suggestion" if len(suggestions) == 1 else "suggestions"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Scoped anchor selector review</title>
  <style>
    @font-face {{
      font-family: "AMS Current";
      src: url("{html.escape(current_font_rel)}") format("opentype");
    }}
    @font-face {{
      font-family: "AMS Scoped";
      src: url("{html.escape(scoped_font_rel)}") format("opentype");
    }}
    :root {{
      color-scheme: light dark;
      font-family: system-ui, sans-serif;
      line-height: 1.4;
      --bg: #fff;
      --text: #16191f;
      --border: #d4d7dd;
      --muted: #5c6470;
      --soft: #f5f6f8;
      --rule: #20242c;
      --code-bg: #eceff3;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101318;
        --text: #eceff3;
        --border: #3a414d;
        --muted: #a8b0bd;
        --soft: #191e26;
        --rule: #d9dee7;
        --code-bg: #252b35;
      }}
    }}
    body {{
      margin: 0;
      color: var(--text);
      background: var(--bg);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1, h2, h3 {{
      margin-block: 0 8px;
      line-height: 1.2;
    }}
    h1 {{
      font-size: 28px;
    }}
    h2 {{
      font-size: 18px;
    }}
    h3 {{
      font-size: 14px;
      color: var(--muted);
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.92em;
      background: var(--code-bg);
      border-radius: 4px;
      padding: 0.08em 0.28em;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      border-top: 1px solid var(--border);
      padding: 6px 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }}
    .summary {{
      margin-block: 16px 24px;
      color: var(--muted);
    }}
    .dropped-match-note {{
      max-width: 82ch;
      margin-block: -8px 24px;
    }}
    .suggestion {{
      border-top: 2px solid var(--rule);
      padding-block: 18px 28px;
    }}
    .suggestion header p {{
      margin-block: 6px;
    }}
    .variant-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      margin-block: 14px 18px;
    }}
    @media (width > 760px) {{
      .variant-grid {{
        grid-template-columns: 1fr 1fr;
      }}
    }}
    .variant-grid section {{
      min-width: 0;
      background: var(--soft);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
    }}
    .dropped-match-cases {{
      display: grid;
      gap: 12px;
    }}
    .dropped-match-case {{
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--soft);
      padding: 10px;
    }}
    .case-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 18px;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    .comparison-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }}
    @media (width > 760px) {{
      .comparison-grid {{
        grid-template-columns: 1fr 1fr;
      }}
    }}
    .comparison-grid section {{
      min-width: 0;
    }}
    h4 {{
      margin: 0 0 6px;
      font-size: 13px;
      color: var(--text);
    }}
    .qs {{
      display: block;
      min-height: 44px;
      margin-bottom: 6px;
      font-size: 34px;
      line-height: 1;
      white-space: nowrap;
    }}
    .qs.current {{
      font-family: "AMS Current";
    }}
    .qs.scoped {{
      font-family: "AMS Scoped";
    }}
    .empty {{
      margin: 8px 0 0;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main>
    <h1>Scoped anchor selector review</h1>
    <p class="summary">{len(suggestions)} {suggestion_label}; dropped-match cases found for {case_count} at max length {max_len}. The scoped font is built from an in-memory copy of the YAML, not from edited source files.</p>
    <p class="summary dropped-match-note">A scoped selector is a narrower selector like <code>{{family: qsMay, exit_y: 5}}</code> instead of <code>{{family: qsMay}}</code>; it still matches <code>qsMay</code> variants, but only the variants with the requested anchor Y.</p>
    <p class="summary dropped-match-note">A dropped-match case is a concrete Quikscript input sequence where the current broad selector reaches a variant that the proposed scoped selector would no longer match. The Current and Scoped columns show how that same sequence shapes before and after the simulated selector change; Changed says whether the final glyph names differ.</p>
    {cards}
  </main>
</body>
</html>
"""


def build_review(
    glyph_data: GlyphData,
    suggestions: list[ScopedAnchorSuggestion],
    *,
    output_path: Path,
    max_len: int,
    max_cases: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    current_font_path = output_path.parent / "current" / SENIOR_FONT_NAME
    scoped_font_path = output_path.parent / "scoped" / SENIOR_FONT_NAME
    scoped_data = apply_suggestions_to_glyph_data(glyph_data, suggestions)

    _build_review_font(glyph_data, current_font_path)
    _build_review_font(scoped_data, scoped_font_path)

    ps_names = _load_ps_names()
    context_families = _plain_quikscript_families(ps_names, glyph_data)
    current_meta = compile_glyph_set(glyph_data, "senior").glyph_meta
    current_font = _hb_font(current_font_path)
    scoped_font = _hb_font(scoped_font_path)

    case_map: dict[str, list[DroppedMatchCase]] = {}
    for suggestion in suggestions:
        case_map[suggestion.path] = find_dropped_match_cases(
            suggestion,
            glyph_data=glyph_data,
            ps_names=ps_names,
            context_families=context_families,
            current_font=current_font,
            scoped_font=scoped_font,
            current_meta=current_meta,
            max_len=max_len,
            max_cases=max_cases,
        )

    output_path.write_text(
        _html_page(
            suggestions=suggestions,
            case_map=case_map,
            meta_map=current_meta,
            current_font=current_font_path,
            scoped_font=scoped_font_path,
            output_path=output_path,
            max_len=max_len,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "glyph_data",
        nargs="?",
        type=Path,
        default=ROOT / "glyph_data",
        help="Glyph data YAML file or directory (default: glyph_data/).",
    )
    parser.add_argument(
        "--variant",
        default="senior",
        choices=["senior"],
        help="Compiled variant to inspect. The review renderer currently supports Senior only.",
    )
    parser.add_argument("--family", help="Only inspect selectors authored on this family.")
    parser.add_argument("--path", help="Only review one suggestion path.")
    parser.add_argument(
        "--max-len",
        type=int,
        default=3,
        help="Maximum input-family sequence length for dropped-match case search.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=6,
        help="Maximum dropped-match cases to show per suggestion.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="HTML output path (default: tmp/scoped-anchor-review/index.html).",
    )
    args = parser.parse_args()

    data = load_glyph_data(args.glyph_data)
    suggestions = suggest_scoped_anchor_selectors(
        data,
        variant=args.variant,
        family_filter=args.family,
    )
    if args.path:
        suggestions = [suggestion for suggestion in suggestions if suggestion.path == args.path]
        if not suggestions:
            raise SystemExit(f"No scoped anchor selector suggestion found for {args.path!r}.")
    if not suggestions:
        print("No family-scoped anchor selector suggestions.")
        return

    build_review(
        data,
        suggestions,
        output_path=args.output,
        max_len=args.max_len,
        max_cases=args.max_cases,
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
