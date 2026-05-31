"""Build HTML review pages for scoped anchor selector suggestions.

The tool is read-only with respect to source data: it applies suggested ``entry_y`` / ``exit_y`` selector scopes to an in-memory copy of glyph data, builds temporary Senior-Regular fonts under ``tmp/``, and writes HTML pages showing selector expansion and dropped-match cases.

With no filters, builds ``index.html`` plus a per-family ``<family>.html`` page for every family that has suggestions, fanning the work out across worker processes (``--jobs``). Pass ``--family`` or ``--path`` to regenerate one per-letter page at a time.

Usage::

    uv run python tools/review_scoped_anchor_selectors.py
    uv run python tools/review_scoped_anchor_selectors.py --jobs 4
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
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
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

DEFAULT_OUTPUT_DIR = ROOT / "tmp" / "scoped-anchor-review"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"
SENIOR_FONT_NAME = "AbbotsMortonSpaceportSansSenior-Regular.otf"
SENIOR_FONT_STEM = Path(SENIOR_FONT_NAME).stem
SENIOR_FONT_SUFFIX = Path(SENIOR_FONT_NAME).suffix
DEFAULT_JOBS = 8


def _scoped_font_filename(family: str) -> str:
    return f"{SENIOR_FONT_STEM}--{family}{SENIOR_FONT_SUFFIX}"


@dataclass(frozen=True)
class ShapedRun:
    families: tuple[str, ...]
    text: str
    glyphs: tuple[str, ...]


@dataclass(frozen=True)
class VariantExample:
    status: str
    label: str
    title: str = ""
    families: tuple[str, ...] = ()
    text: str = ""
    glyphs: tuple[str, ...] = ()
    feature_tag: str | None = None


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
            raise ValueError(f"{suggestion.path} has unknown record kind {suggestion.record_kind!r}")
        selector = record["select"][suggestion.field_name][suggestion.selector_index]
        selector[suggestion.anchor_key] = suggestion.required_y
    return patched


def _load_ps_names() -> dict[str, int]:
    with PS_NAMES_PATH.open() as f:
        names: dict[str, int] = yaml.safe_load(f)
    names.setdefault("uni200C", 0x200C)
    return names


def _review_context_sequences(
    ps_names: dict[str, int],
    glyph_data: GlyphData,
) -> tuple[tuple[str, ...], ...]:
    glyph_families = glyph_data.get("glyph_families", {})
    plain_rows = [
        (codepoint, name)
        for name, codepoint in ps_names.items()
        if name.startswith("qs")
        and "_" not in name
        and name not in {"qsAngleParenLeft", "qsAngleParenRight"}
        and name in glyph_families
    ]
    sequences: list[tuple[tuple[str, ...], int]] = [((name,), codepoint) for codepoint, name in plain_rows]
    for extra in ("space", "uni200C"):
        if extra in ps_names:
            sequences.append(((extra,), ps_names[extra]))
    for ligature_name, ligature_def in glyph_families.items():
        if "_" not in ligature_name:
            continue
        if not isinstance(ligature_def, dict):
            continue
        sequence = ligature_def.get("sequence")
        if not isinstance(sequence, list) or not all(isinstance(item, str) for item in sequence):
            continue
        if len(sequence) < 2:
            continue
        if not all(component in ps_names for component in sequence):
            continue
        lead_codepoint = ps_names[sequence[0]]
        sequences.append((tuple(sequence), lead_codepoint))
    sequences.sort(key=lambda row: (row[1], row[0]))
    return tuple(seq for seq, _ in sequences)


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


def _feature_tag_for_variant(
    name: str,
    meta_map: dict[str, JoinGlyph],
) -> str | None:
    current: str | None = name
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        meta = meta_map.get(current)
        if meta is None:
            return None
        if meta.gate_feature:
            return meta.gate_feature
        current = meta.generated_from
    return None


def _selector_position_label(suggestion: ScopedAnchorSuggestion) -> str:
    if suggestion.field_name == "after":
        return f"before {suggestion.selected_name}"
    return f"after {suggestion.selected_name}"


def _exact_example_label(suggestion: ScopedAnchorSuggestion) -> str:
    return "Reviewed context"


def _exact_example_title(
    suggestion: ScopedAnchorSuggestion,
    variant_name: str,
) -> str:
    return (
        f"This example shows the reviewed selector context: it produces "
        f"{variant_name} {_selector_position_label(suggestion)}."
    )


def _internal_example_title() -> str:
    return (
        "No typed text within the search depth produced this glyph as a final shaped glyph; "
        "it may be an internal intermediate or need a longer context."
    )


def _sequence_candidates(
    glyph_data: GlyphData,
    suggestion: ScopedAnchorSuggestion,
    context_sequences: tuple[tuple[str, ...], ...],
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
    base_budget = max_len - len(base)
    if base_budget >= 1:
        for context_seq in context_sequences:
            if len(context_seq) > base_budget:
                continue
            add(context_seq, ())
            add((), context_seq)
    if base_budget >= 2:
        for before_seq in context_sequences:
            for after_seq in context_sequences:
                if len(before_seq) + len(after_seq) > base_budget:
                    continue
                add(before_seq, after_seq)
    return results


def _sequence_occurrences(
    families: tuple[str, ...],
    sequence: tuple[str, ...],
) -> tuple[tuple[int, int], ...]:
    if not sequence:
        return ()
    length = len(sequence)
    return tuple(
        (start, start + length)
        for start in range(0, len(families) - length + 1)
        if families[start : start + length] == sequence
    )


def _contextual_sequence_candidates(
    base: tuple[str, ...],
    context_sequences: tuple[tuple[str, ...], ...],
    max_len: int,
) -> list[tuple[tuple[str, ...], int, int]]:
    results: list[tuple[tuple[str, ...], int, int]] = []
    seen: set[tuple[str, ...]] = set()

    def add(prefix: tuple[str, ...], suffix: tuple[str, ...]) -> None:
        families = prefix + base + suffix
        if families in seen:
            return
        if len(families) > max_len and len(families) > len(base):
            return
        seen.add(families)
        start = len(prefix)
        results.append((families, start, start + len(base)))

    add((), ())
    base_budget = max_len - len(base)
    if base_budget >= 1:
        for context_seq in context_sequences:
            if len(context_seq) > base_budget:
                continue
            add(context_seq, ())
            add((), context_seq)
    if base_budget >= 2:
        for before_seq in context_sequences:
            for after_seq in context_sequences:
                if len(before_seq) + len(after_seq) > base_budget:
                    continue
                add(before_seq, after_seq)
    return results


def _typed_sequence_for_glyph(
    name: str,
    meta_map: dict[str, JoinGlyph],
    ps_names: dict[str, int],
) -> tuple[str, ...] | None:
    meta = meta_map.get(name)
    if meta is None:
        return None
    if meta.sequence:
        sequence = meta.sequence
    elif meta.base_name in ps_names:
        sequence = (meta.base_name,)
    elif meta.family and meta.family in ps_names:
        sequence = (meta.family,)
    elif name in ps_names:
        sequence = (name,)
    else:
        return None
    if all(family in ps_names for family in sequence):
        return sequence
    return None


class VariantExampleFinder:
    def __init__(
        self,
        *,
        glyph_data: GlyphData,
        ps_names: dict[str, int],
        context_sequences: tuple[tuple[str, ...], ...],
        current_font: hb.Font,
        current_meta: dict[str, JoinGlyph],
        max_len: int,
    ) -> None:
        self.glyph_data = glyph_data
        self.ps_names = ps_names
        self.context_sequences = context_sequences
        self.current_font = current_font
        self.current_meta = current_meta
        self.max_len = max_len
        self._shape_cache: dict[
            tuple[tuple[str, ...], tuple[tuple[str, bool], ...]],
            ShapedRun | None,
        ] = {}
        self._variant_only_cache: dict[str, VariantExample | None] = {}

    def find(
        self,
        suggestion: ScopedAnchorSuggestion,
        variant_name: str,
    ) -> VariantExample:
        exact = self._find_exact_context(suggestion, variant_name)
        if exact is not None:
            return exact
        variant_only = self._find_variant_only(variant_name)
        if variant_only is not None:
            return self._with_variant_only_context(suggestion, variant_name, variant_only)
        return VariantExample(
            status="internal",
            label="No typed example found",
            title=_internal_example_title(),
        )

    def _with_variant_only_context(
        self,
        suggestion: ScopedAnchorSuggestion,
        variant_name: str,
        example: VariantExample,
    ) -> VariantExample:
        source_label = _family_label(suggestion.family_name)
        source_spans = set(
            _sequence_occurrences(
                example.families,
                _family_sequence(self.glyph_data, suggestion.family_name),
            )
        )
        if not source_spans:
            return replace(
                example,
                label=f"Glyph-only example\n(no {source_label} input)",
                title=(
                    f"This example produces {variant_name}, but it does not include "
                    f"{source_label}. It only shows one way this glyph can appear."
                ),
            )

        spans = _input_spans(example.glyphs, self.current_meta)
        selected_source_present = spans is not None and any(
            spans[index] in source_spans
            and _is_selected_variant(glyph, suggestion.selected_name, self.current_meta)
            for index, glyph in enumerate(example.glyphs)
        )
        if not selected_source_present:
            return replace(
                example,
                label=f"Glyph-only example\n(different {source_label} form)",
                title=(
                    f"This example produces {variant_name} and includes {source_label}, "
                    f"but that input shapes as a different form, not "
                    f"{suggestion.selected_name}. It only shows one way this glyph can appear."
                ),
            )

        return replace(
            example,
            label="Glyph-only example\n(different position)",
            title=(
                f"This example produces {variant_name} and contains "
                f"{suggestion.selected_name}, but not next to this {suggestion.target_family} "
                "glyph in the reviewed position. It only shows one way this glyph can appear."
            ),
        )

    def _shape_cached(
        self,
        families: tuple[str, ...],
        features: dict[str, bool],
    ) -> ShapedRun | None:
        key = (families, tuple(sorted(features.items())))
        if key not in self._shape_cache:
            try:
                self._shape_cache[key] = _shape_families(
                    self.current_font,
                    families,
                    self.ps_names,
                    features,
                )
            except ValueError:
                self._shape_cache[key] = None
        return self._shape_cache[key]

    def _find_exact_context(
        self,
        suggestion: ScopedAnchorSuggestion,
        variant_name: str,
    ) -> VariantExample | None:
        target_seq = _family_sequence(self.glyph_data, suggestion.target_family)
        features = _features_for_suggestion(suggestion, self.current_meta)
        feature_tag = _feature_tag_for_suggestion(suggestion, self.current_meta)

        for families, source_start, source_end in _sequence_candidates(
            self.glyph_data,
            suggestion,
            self.context_sequences,
            self.max_len,
        ):
            run = self._shape_cached(families, features)
            if run is None:
                continue
            spans = _input_spans(run.glyphs, self.current_meta)
            if spans is None:
                continue
            if suggestion.field_name == "after":
                target_start = source_start - len(target_seq)
                target_end = source_start
            else:
                target_start = source_end
                target_end = source_end + len(target_seq)

            source_indices = tuple(
                index
                for index, glyph in enumerate(run.glyphs)
                if spans[index] == (source_start, source_end)
                and _is_selected_variant(glyph, suggestion.selected_name, self.current_meta)
            )
            target_indices = tuple(
                index
                for index, glyph in enumerate(run.glyphs)
                if spans[index] == (target_start, target_end) and glyph == variant_name
            )
            if not source_indices or not target_indices:
                continue
            if suggestion.field_name == "after":
                is_adjacent = any(
                    target_index + 1 == source_index
                    for target_index in target_indices
                    for source_index in source_indices
                )
            else:
                is_adjacent = any(
                    source_index + 1 == target_index
                    for source_index in source_indices
                    for target_index in target_indices
                )
            if is_adjacent:
                return VariantExample(
                    status="exact",
                    label=_exact_example_label(suggestion),
                    title=_exact_example_title(suggestion, variant_name),
                    families=run.families,
                    text=run.text,
                    glyphs=run.glyphs,
                    feature_tag=feature_tag,
                )
        return None

    def _find_variant_only(self, variant_name: str) -> VariantExample | None:
        if variant_name in self._variant_only_cache:
            return self._variant_only_cache[variant_name]

        base = _typed_sequence_for_glyph(variant_name, self.current_meta, self.ps_names)
        if base is None:
            self._variant_only_cache[variant_name] = None
            return None

        feature_tag = _feature_tag_for_variant(variant_name, self.current_meta)
        features = {feature_tag: True} if feature_tag else {}
        for families, start, end in _contextual_sequence_candidates(
            base,
            self.context_sequences,
            self.max_len,
        ):
            run = self._shape_cached(families, features)
            if run is None:
                continue
            spans = _input_spans(run.glyphs, self.current_meta)
            if spans is None:
                continue
            if any(
                glyph == variant_name and spans[index] == (start, end)
                for index, glyph in enumerate(run.glyphs)
            ):
                example = VariantExample(
                    status="variant",
                    label="Glyph-only example",
                    families=run.families,
                    text=run.text,
                    glyphs=run.glyphs,
                    feature_tag=feature_tag,
                )
                self._variant_only_cache[variant_name] = example
                return example

        self._variant_only_cache[variant_name] = None
        return None


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
    context_sequences: tuple[tuple[str, ...], ...],
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
        context_sequences,
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
        return "·" + special[family]
    if family.startswith("qs"):
        return "·" + family[2:]
    return family


def _family_labels(families: tuple[str, ...]) -> str:
    return "".join(_family_label(family) for family in families)


def _family_labels_html(
    families: tuple[str, ...],
    *,
    source_family: str,
    target_family: str,
) -> str:
    parts = []
    for family in families:
        classes = ["family-label"]
        if family == source_family:
            classes.append("source-family")
        if family == target_family:
            classes.append("target-family")
        label = html.escape(_family_label(family))
        parts.append(f"<span class=\"{' '.join(classes)}\">{label}</span>")
    return "".join(parts)


def _glyph_name_html(name: str) -> str:
    """Escape ``name`` and bias soft line breaks to its periods.

    Browsers break after hyphens by default, so names like ``qsMay.en-y5.after-fee`` wrap as ``qsMay.entry-`` / ``xheight.after-`` / ``fee``. Wrapping each period-separated chunk in a no-wrap span and adding a ``<wbr>`` after each period flips the preference to break at the periods. The copied text content is unchanged (``<wbr>`` and ``<span>`` contribute nothing extra to plain-text selection).
    """
    parts = [html.escape(part) for part in name.split(".")]
    if len(parts) == 1:
        return parts[0]
    return ".<wbr>".join(f'<span class="nobr">{part}</span>' for part in parts)


def _glyphs_sequence_html(glyphs: tuple[str, ...]) -> str:
    return " | ".join(_glyph_name_html(glyph) for glyph in glyphs)


def _glyph_relates_to_family(
    glyph: str,
    family: str,
    meta_map: dict[str, JoinGlyph],
) -> bool:
    meta = meta_map.get(glyph)
    if meta is None:
        return glyph == family or glyph.startswith(family + ".")
    return meta.family == family or meta.base_name == family or family in meta.sequence


def _glyphs_html(
    glyphs: tuple[str, ...],
    meta_map: dict[str, JoinGlyph],
    *,
    source_family: str,
    target_family: str,
) -> str:
    if not glyphs:
        return '<span class="empty">No shaped glyph output</span>'
    parts = []
    for glyph in glyphs:
        classes = ["glyph-token"]
        if _glyph_relates_to_family(glyph, source_family, meta_map):
            classes.append("source-glyph")
        if _glyph_relates_to_family(glyph, target_family, meta_map):
            classes.append("target-glyph")
        parts.append(f"<code class=\"{' '.join(classes)}\">{_glyph_name_html(glyph)}</code>")
    separator = '<span class="glyph-separator"> | </span>'
    return '<span class="glyph-list">' + separator.join(parts) + "</span>"


def _text_entities(text: str) -> str:
    return "".join(f"&#x{ord(char):X};" for char in text)


def _variant_example_input_html(
    example: VariantExample,
    suggestion: ScopedAnchorSuggestion,
) -> str:
    if example.status == "internal":
        return '<span class="empty">No typed example</span>'
    feature = f" <code>+{html.escape(example.feature_tag)}</code>" if example.feature_tag else ""
    family_labels = _family_labels_html(
        example.families,
        source_family=suggestion.family_name,
        target_family=suggestion.target_family,
    )
    return (
        '<div class="example-families">'
        f"{family_labels}"
        f"{feature}"
        "</div>"
        f'<span class="qs current variant-example-rendering">{_text_entities(example.text)}</span>'
    )


def _name_prefix_depth(name: str, names_set: frozenset[str]) -> int:
    parts = name.split(".")
    depth = 0
    for i in range(1, len(parts)):
        if ".".join(parts[:i]) in names_set:
            depth += 1
    return depth


def _rows_for_variants(
    names: tuple[str, ...],
    meta_map: dict[str, JoinGlyph],
    examples: dict[str, VariantExample],
    suggestion: ScopedAnchorSuggestion,
) -> str:
    def label_html(label: str) -> str:
        return "<br>".join(html.escape(line) for line in label.splitlines())

    names_set = frozenset(names)
    rows = []
    for name in names:
        example = examples.get(
            name,
            VariantExample(
                status="internal",
                label="No typed example found",
                title=_internal_example_title(),
            ),
        )
        glyph_output = _glyphs_html(
            example.glyphs,
            meta_map,
            source_family=suggestion.family_name,
            target_family=suggestion.target_family,
        )
        depth = _name_prefix_depth(name, names_set)
        glyph_td_attrs = f' style="--depth: {depth}"' if depth else ""
        title_attr = f' title="{html.escape(example.title, quote=True)}"' if example.title else ""
        rows.append(
            "<tr>"
            f'<td class="glyph-cell"{glyph_td_attrs}><code>{_glyph_name_html(name)}</code></td>'
            f"<td>{html.escape(_anchor_ys_text(meta_map.get(name)))}</td>"
            "<td>"
            f'<span class="example-status example-status-{html.escape(example.status)}"{title_attr}>'
            f"{label_html(example.label)}"
            "</span>"
            "</td>"
            f"<td>{_variant_example_input_html(example, suggestion)}</td>"
            "<td>"
            f"{glyph_output}"
            "</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _variant_table(
    names: tuple[str, ...],
    meta_map: dict[str, JoinGlyph],
    examples: dict[str, VariantExample],
    suggestion: ScopedAnchorSuggestion,
) -> str:
    rows = _rows_for_variants(names, meta_map, examples, suggestion)
    return (
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Glyph</th>"
        "<th>Anchors</th>"
        "<th>What this example shows</th>"
        "<th>QS input/rendering</th>"
        "<th>Shaped glyph output</th>"
        "</tr>"
        "</thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def _dropped_match_rows(cases: list[DroppedMatchCase]) -> str:
    if not cases:
        return '<p class="empty">No dropped-match case found in the configured search depth.</p>'
    articles = []
    for case in cases:
        feature = f" +{case.feature_tag}" if case.feature_tag else ""
        changed = "yes" if case.current.glyphs != case.scoped.glyphs else "no"
        articles.append(
            '<article class="dropped-match-case">'
            '<header class="case-meta">'
            f"<span><strong>Sequence</strong> {html.escape(_family_labels(case.current.families))}{html.escape(feature)}</span>"
            f"<span><strong>Changed</strong> {changed}</span>"
            f"<span><strong>Dropped match</strong> <code>{_glyph_name_html(case.dropped_glyph)}</code></span>"
            "</header>"
            '<div class="comparison-grid">'
            "<section>"
            "<h4>Current</h4>"
            f'<span class="qs current">{_text_entities(case.current.text)}</span>'
            f"<code>{_glyphs_sequence_html(case.current.glyphs)}</code>"
            "</section>"
            "<section>"
            "<h4>Scoped</h4>"
            f'<span class="qs scoped">{_text_entities(case.scoped.text)}</span>'
            f"<code>{_glyphs_sequence_html(case.scoped.glyphs)}</code>"
            "</section>"
            "</div>"
            "</article>"
        )
    return f"<div class=\"dropped-match-cases\">{''.join(articles)}</div>"


def _suggestion_card(
    suggestion: ScopedAnchorSuggestion,
    cases: list[DroppedMatchCase],
    meta_map: dict[str, JoinGlyph],
    variant_examples: dict[str, VariantExample],
    page_path: str,
) -> str:
    compatible_table = _variant_table(
        suggestion.compatible,
        meta_map,
        variant_examples,
        suggestion,
    )
    incompatible_table = _variant_table(
        suggestion.incompatible,
        meta_map,
        variant_examples,
        suggestion,
    )
    why = (
        f"<code>{_glyph_name_html(suggestion.selected_name)}</code> has "
        f"{html.escape(suggestion.selected_side)} y={suggestion.required_y}; "
        f"the opposite side must provide {html.escape(suggestion.target_side)} y={suggestion.required_y}."
    )
    selector_locator = (
        f"{suggestion.selected_name}.select." f"{suggestion.field_name}[{suggestion.selector_index}]"
    )
    target_family_html = f"<code>{_glyph_name_html(suggestion.target_family)}</code>"
    selector_locator_html = f"<code>{_glyph_name_html(selector_locator)}</code>"
    swap_html = (
        f"<code>{html.escape(suggestion.current)}</code> to "
        f"<code>{html.escape(suggestion.suggested)}</code>"
    )
    swap_text = f"`{suggestion.current}` to `{suggestion.suggested}`"
    target_family_text = f"`{suggestion.target_family}`"
    selector_locator_text = f"`{selector_locator}`"
    compatible_copy_text = (
        f"I’m looking at {page_path}. In “"
        f"{target_family_text} variants still matched after "
        f"{selector_locator_text} narrows from {swap_text} "
        f"({len(suggestion.compatible)})”"
    )
    incompatible_copy_text = (
        f"I’m looking at {page_path}. In “"
        f"{target_family_text} variants no longer matched after "
        f"{selector_locator_text} narrows from {swap_text} "
        f"({len(suggestion.incompatible)})”"
    )
    compatible_copy_attr = html.escape(compatible_copy_text, quote=True)
    incompatible_copy_attr = html.escape(incompatible_copy_text, quote=True)
    return f"""
<section class="suggestion" id="{html.escape(suggestion.path)}">
  <header>
    <h2><code>{_glyph_name_html(suggestion.path)}</code></h2>
    <p><code>{html.escape(suggestion.current)}</code> -&gt; <code>{html.escape(suggestion.suggested)}</code></p>
    <p>{why}</p>
  </header>
  <div class="variant-grid">
    <section>
      <h3><button type="button" class="copy-line" data-copy-text="{compatible_copy_attr}">Copy</button> {target_family_html} variants still matched after {selector_locator_html} narrows from {swap_html} ({len(suggestion.compatible)})</h3>
      {compatible_table}
    </section>
    <section>
      <h3><button type="button" class="copy-line" data-copy-text="{incompatible_copy_attr}">Copy</button> {target_family_html} variants no longer matched after {selector_locator_html} narrows from {swap_html} ({len(suggestion.incompatible)})</h3>
      {incompatible_table}
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
    variant_example_map: dict[str, dict[str, VariantExample]],
    meta_map: dict[str, JoinGlyph],
    current_font: Path,
    scoped_font: Path,
    output_path: Path,
    max_len: int,
) -> str:
    current_font_rel = os.path.relpath(current_font, output_path.parent)
    scoped_font_rel = os.path.relpath(scoped_font, output_path.parent)
    repo_root = Path(__file__).resolve().parent.parent
    try:
        page_path = str(output_path.resolve().relative_to(repo_root))
    except ValueError:
        page_path = output_path.name
    cards = "\n".join(
        _suggestion_card(
            suggestion,
            case_map.get(suggestion.path, []),
            meta_map,
            variant_example_map.get(suggestion.path, {}),
            page_path,
        )
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
    .nobr {{
      white-space: nowrap;
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
    .glyph-cell {{
      padding-left: calc(8px + var(--depth, 0) * 1.5ch);
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }}
    .back-to-index {{
      display: inline-block;
      margin-block: 0 8px;
      font-size: 22px;
      line-height: 1;
      color: var(--muted);
      text-decoration: none;
    }}
    .back-to-index:hover {{
      color: var(--text);
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
    .variant-example-rendering {{
      min-height: 34px;
      margin-block: 4px 0;
      font-size: 28px;
    }}
    .example-families {{
      margin-bottom: 4px;
      color: var(--muted);
    }}
    .family-label {{
      display: inline-block;
      border-bottom: 2px solid transparent;
    }}
    .source-family, .source-glyph {{
      border-color: #2f7de1;
      background: color-mix(in srgb, #2f7de1 16%, transparent);
    }}
    .target-family, .target-glyph {{
      border-color: #c26900;
      background: color-mix(in srgb, #c26900 16%, transparent);
    }}
    .source-family.target-family, .source-glyph.target-glyph {{
      border-color: #7b51d1;
      background: color-mix(in srgb, #7b51d1 18%, transparent);
    }}
    .glyph-list {{
      display: inline-flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 2px;
    }}
    .glyph-token {{
      border-bottom: 2px solid transparent;
    }}
    .glyph-separator {{
      color: var(--muted);
    }}
    .example-status-exact {{
      font-weight: 650;
    }}
    .example-status-internal {{
      color: var(--muted);
    }}
    .empty {{
      margin: 8px 0 0;
      color: var(--muted);
    }}
    .copy-line {{
      font: inherit;
      font-size: 11px;
      padding: 2px 8px;
      margin-right: 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--soft);
      color: var(--text);
      cursor: pointer;
      vertical-align: baseline;
    }}
    .copy-line:hover {{
      background: var(--code-bg);
    }}
    .copy-line.copied {{
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main>
    <a class="back-to-index" href="/scoped-anchor-review/" aria-label="Back to scoped-anchor review index">⎋</a>
    <h1>Scoped anchor selector review</h1>
    <p class="summary">{len(suggestions)} {suggestion_label}; dropped-match cases found for {case_count} at max length {max_len}. The scoped font is built from an in-memory copy of the YAML, not from edited source files.</p>
    <p class="summary dropped-match-note">A scoped selector is a narrower selector like <code>{{family: qsMay, exit_y: 5}}</code> instead of <code>{{family: qsMay}}</code>; it still matches <code>qsMay</code> variants, but only the variants with the requested anchor Y.</p>
    <p class="summary dropped-match-note">A dropped-match case is a concrete Quikscript input sequence where the current broad selector reaches a variant that the proposed scoped selector would no longer match. The Current and Scoped columns show how that same sequence shapes before and after the simulated selector change; Changed says whether the final glyph names differ.</p>
    {cards}
  </main>
  <script>
    document.addEventListener('click', (event) => {{
      const button = event.target.closest('.copy-line');
      if (!button) return;
      const text = button.dataset.copyText;
      if (text == null) return;
      navigator.clipboard.writeText(text).then(() => {{
        const original = button.textContent;
        button.textContent = 'Copied';
        button.classList.add('copied');
        setTimeout(() => {{
          button.textContent = original;
          button.classList.remove('copied');
        }}, 1200);
      }});
    }});
  </script>
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
    current_font_path: Path | None = None,
) -> None:
    if not suggestions:
        raise ValueError("build_review needs at least one suggestion")
    family = suggestions[0].family_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if current_font_path is None:
        current_font_path = output_path.parent / "current" / SENIOR_FONT_NAME
        _build_review_font(glyph_data, current_font_path)
    scoped_font_path = output_path.parent / "scoped" / _scoped_font_filename(family)
    scoped_data = apply_suggestions_to_glyph_data(glyph_data, suggestions)
    _build_review_font(scoped_data, scoped_font_path)

    ps_names = _load_ps_names()
    context_sequences = _review_context_sequences(ps_names, glyph_data)
    current_meta = compile_glyph_set(glyph_data, "senior").glyph_meta
    current_font = _hb_font(current_font_path)
    scoped_font = _hb_font(scoped_font_path)
    example_finder = VariantExampleFinder(
        glyph_data=glyph_data,
        ps_names=ps_names,
        context_sequences=context_sequences,
        current_font=current_font,
        current_meta=current_meta,
        max_len=max_len,
    )

    case_map: dict[str, list[DroppedMatchCase]] = {}
    variant_example_map: dict[str, dict[str, VariantExample]] = {}
    for suggestion in suggestions:
        case_map[suggestion.path] = find_dropped_match_cases(
            suggestion,
            glyph_data=glyph_data,
            ps_names=ps_names,
            context_sequences=context_sequences,
            current_font=current_font,
            scoped_font=scoped_font,
            current_meta=current_meta,
            max_len=max_len,
            max_cases=max_cases,
        )
        variant_example_map[suggestion.path] = {
            name: example_finder.find(suggestion, name)
            for name in (*suggestion.compatible, *suggestion.incompatible)
        }

    output_path.write_text(
        _html_page(
            suggestions=suggestions,
            case_map=case_map,
            variant_example_map=variant_example_map,
            meta_map=current_meta,
            current_font=current_font_path,
            scoped_font=scoped_font_path,
            output_path=output_path,
            max_len=max_len,
        )
    )


def _index_html_page(entries: list[tuple[str, int]]) -> str:
    if entries:
        items_html = "\n".join(
            "      <li>"
            f'<a href="{html.escape(family)}.html">{html.escape(_family_label(family))}</a> '
            '<span class="muted">('
            f"<code>{html.escape(family)}</code>; "
            f'{count} suggestion{"" if count == 1 else "s"}'
            ")</span>"
            "</li>"
            for family, count in entries
        )
        total = sum(count for _, count in entries)
        family_word = "family" if len(entries) == 1 else "families"
        suggestion_word = "suggestion" if total == 1 else "suggestions"
        summary = f"{total} {suggestion_word} across {len(entries)} {family_word}."
    else:
        items_html = '      <li class="empty">No scoped-anchor suggestions.</li>'
        summary = "No suggestions to review."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Scoped anchor selector review</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: system-ui, sans-serif;
      line-height: 1.4;
      --bg: #fff;
      --text: #16191f;
      --muted: #5c6470;
      --code-bg: #eceff3;

      @media (prefers-color-scheme: dark) {{
        --bg: #101318;
        --text: #eceff3;
        --muted: #a8b0bd;
        --code-bg: #252b35;
      }}
    }}
    body {{
      margin: 0;
      color: var(--text);
      background: var(--bg);
    }}
    main {{
      max-width: 720px;
      margin: 0 auto;
      padding: 24px;

      h1 {{
        margin-block: 0 8px;
        font-size: 28px;
        line-height: 1.2;
      }}
      p {{
        color: var(--muted);
      }}
      ul {{
        list-style: none;
        padding: 0;
        margin-block: 12px 0;

        li {{
          margin-block: 6px;
        }}
      }}
      a {{
        color: var(--text);
        text-decoration: underline;
      }}
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.92em;
      background: var(--code-bg);
      border-radius: 4px;
      padding: 0.08em 0.28em;
    }}
    .muted, .empty {{
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main>
    <h1>Scoped anchor selector review</h1>
    <p>{html.escape(summary)} Regenerate a per-letter page with <code>tools/review_scoped_anchor_selectors.py --family qsXxx</code>.</p>
    <ul>
{items_html}
    </ul>
  </main>
</body>
</html>
"""


def _empty_family_html_page(family: str) -> str:
    family_label = _family_label(family)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Scoped anchor selector review — {html.escape(family_label)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: system-ui, sans-serif;
      line-height: 1.4;
      --bg: #fff;
      --text: #16191f;
      --muted: #5c6470;
      --code-bg: #eceff3;

      @media (prefers-color-scheme: dark) {{
        --bg: #101318;
        --text: #eceff3;
        --muted: #a8b0bd;
        --code-bg: #252b35;
      }}
    }}
    body {{
      margin: 0;
      color: var(--text);
      background: var(--bg);
    }}
    main {{
      max-width: 720px;
      margin: 0 auto;
      padding: 24px;

      h1 {{
        margin-block: 0 8px;
        font-size: 28px;
        line-height: 1.2;
      }}
      p {{
        color: var(--muted);
      }}
      a {{
        color: var(--text);
        text-decoration: underline;
      }}
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.92em;
      background: var(--code-bg);
      border-radius: 4px;
      padding: 0.08em 0.28em;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(family_label)}: nothing to review</h1>
    <p>The latest run found no scoped-anchor selector suggestions for <code>{html.escape(family)}</code>. Return to the <a href="index.html">review index</a>.</p>
  </main>
</body>
</html>
"""


def build_review_index(
    suggestions: list[ScopedAnchorSuggestion],
    *,
    index_path: Path,
) -> None:
    """Write ``index_path`` as a list of links to per-family review pages."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    ps_names = _load_ps_names()
    counts: dict[str, int] = {}
    for suggestion in suggestions:
        counts[suggestion.family_name] = counts.get(suggestion.family_name, 0) + 1
    entries = sorted(
        counts.items(),
        key=lambda item: (ps_names.get(item[0], 0xFFFF), item[0]),
    )
    index_path.write_text(_index_html_page(entries))


_WORKER_STATE: dict[str, Any] = {}


def _worker_init(
    glyph_data: GlyphData,
    current_font_path: Path,
    max_len: int,
    max_cases: int,
) -> None:
    _WORKER_STATE["glyph_data"] = glyph_data
    _WORKER_STATE["current_font_path"] = current_font_path
    _WORKER_STATE["max_len"] = max_len
    _WORKER_STATE["max_cases"] = max_cases


def _worker_build_family(
    task: tuple[list[ScopedAnchorSuggestion], Path],
) -> str:
    suggestions, output_path = task
    build_review(
        _WORKER_STATE["glyph_data"],
        suggestions,
        output_path=output_path,
        max_len=_WORKER_STATE["max_len"],
        max_cases=_WORKER_STATE["max_cases"],
        current_font_path=_WORKER_STATE["current_font_path"],
    )
    return suggestions[0].family_name


def build_all_reviews(
    glyph_data: GlyphData,
    suggestions: list[ScopedAnchorSuggestion],
    *,
    index_path: Path,
    max_len: int,
    max_cases: int,
    jobs: int,
) -> list[Path]:
    """Build the index page and every per-family page in *index_path*'s directory.

    The unscoped font is built once and shared; each family's scoped font is built independently into ``scoped/<font-stem>--<family>.otf`` so workers don't clobber each other. Returns the list of per-family HTML paths in code-point order.
    """
    output_dir = index_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    per_family: dict[str, list[ScopedAnchorSuggestion]] = defaultdict(list)
    for suggestion in suggestions:
        per_family[suggestion.family_name].append(suggestion)
    ps_names = _load_ps_names()
    ordered_families = sorted(
        per_family,
        key=lambda family: (ps_names.get(family, 0xFFFF), family),
    )

    tasks: list[tuple[list[ScopedAnchorSuggestion], Path]] = [
        (per_family[family], output_dir / f"{family}.html") for family in ordered_families
    ]

    expected_family_paths = {output_path for _, output_path in tasks}
    for family in ps_names:
        if not family.startswith("qs"):
            continue
        family_path = output_dir / f"{family}.html"
        if family_path not in expected_family_paths:
            family_path.write_text(_empty_family_html_page(family))

    if tasks:
        current_font_path = output_dir / "current" / SENIOR_FONT_NAME
        _build_review_font(glyph_data, current_font_path)
        workers = max(1, min(jobs, len(tasks)))
        if workers == 1:
            _worker_init(glyph_data, current_font_path, max_len, max_cases)
            for task in tasks:
                _worker_build_family(task)
        else:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_worker_init,
                initargs=(glyph_data, current_font_path, max_len, max_cases),
            ) as pool:
                for _ in pool.map(_worker_build_family, tasks):
                    pass

    build_review_index(suggestions, index_path=index_path)
    return [output_path for _, output_path in tasks]


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
        default=5,
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
        default=None,
        help=(
            "HTML output path. Defaults to "
            "tmp/scoped-anchor-review/index.html, or "
            "tmp/scoped-anchor-review/<family>.html when --family / --path is set. "
            "In the no-filter case, per-family pages are written alongside the index."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=(
            "Maximum worker processes for building per-family pages "
            f"(default: {DEFAULT_JOBS}). Capped at the number of families with suggestions."
        ),
    )
    args = parser.parse_args()

    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1.")

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
    if (args.family or args.path) and not suggestions:
        print("No family-scoped anchor selector suggestions.")
        return

    if args.family or args.path:
        family_for_default = args.family or suggestions[0].family_name
        output_path = args.output or DEFAULT_OUTPUT_DIR / f"{family_for_default}.html"
        build_review(
            data,
            suggestions,
            output_path=output_path,
            max_len=args.max_len,
            max_cases=args.max_cases,
        )
        print(f"Wrote {output_path}")
    else:
        output_path = args.output or DEFAULT_OUTPUT_DIR / "index.html"
        family_paths = build_all_reviews(
            data,
            suggestions,
            index_path=output_path,
            max_len=args.max_len,
            max_cases=args.max_cases,
            jobs=args.jobs,
        )
        if family_paths:
            family_word = "family page" if len(family_paths) == 1 else "family pages"
            print(f"Wrote {output_path} and {len(family_paths)} {family_word} in {output_path.parent}")
        else:
            print(f"Wrote {output_path} (no suggestions to review)")


if __name__ == "__main__":
    main()
