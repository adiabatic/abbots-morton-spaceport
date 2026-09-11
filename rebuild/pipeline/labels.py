"""The spelling every reader of a settled stream shares: the spec's alphabet, a configuration's feature set, a formed stream's labels in the configuration's renamed space, the boundary glyphs and boundary kinds those labels name, and the alias map from the old font's compiled names into cell identity.

This module is a leaf on purpose. It imports `model`, `settle` and `rowmodel` and nothing else under the repo, so the review surface's build reaches this vocabulary without reaching the conformance sweep, the baseline oracle's row cache, the GSUB emitter or the pixel geometry that also read it, which `unit_cache.PIPELINE_NON_SURFACE_MODULES` keeps off both per-unit store stamps. rebuild/test_review_code_closure.py pins the import list, so a later import here cannot quietly regrow the surface's closure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from rebuild.pipeline import settle
from rebuild.pipeline.model import CellId, ResolvedSpec, marker_glyph_name, relevant_marker_features
from rebuild.validation.rowmodel import CONFIGS

BOUNDARY_GLYPH_NAMES = {"space", "uni200C", "periodcentered", "periodcentered.lowered"}
_BOUNDARY_KIND_LABELS = {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}


def spec_alphabet(spec: ResolvedSpec) -> tuple[str, ...]:
    codepoints = sorted(
        [rune.codepoint for rune in spec.runes.values() if rune.codepoint is not None]
        + [token.codepoint for token in spec.registry.boundary_tokens.values()]
    )
    return tuple(chr(cp) for cp in codepoints)


def features_for_config(config: str) -> frozenset[str]:
    return frozenset(tag for tag, on in CONFIGS[config].items() if on)


def formed_labels(spec: ResolvedSpec, formed: list[settle.RightToken], features: frozenset[str]) -> list[str]:
    """The post-formation stream's labels in the config's renamed space: marker fold, then the ZWNJ chokepoint's `.noentry` suffix on entry-bearing letters. Interned, so the window keys built from millions of texts share one string object per label instead of holding a fresh fold per text alive."""
    labels: list[str] = []
    for position, token in enumerate(formed):
        if token.kind != "letter":
            labels.append(_BOUNDARY_KIND_LABELS[token.kind])
            continue
        name = token.letter
        rune = spec.runes.get(name)
        label = name
        if rune is not None:
            relevant = frozenset(relevant_marker_features(rune)) & features
            label = marker_glyph_name(name, relevant)
        if (
            position > 0
            and formed[position - 1].kind == "zwnj"
            and rune is not None
            and any(stance.surface.entries for stance in rune.stances.values())
        ):
            label = f"{label}.noentry"
        labels.append(sys.intern(label))
    return labels


def load_alias_map(path: Path) -> dict[str, CellId | str]:
    """rebuild/m1-aliases.yaml: old compiled glyph name -> CellId fields, or the literal strings "boundary" / "ignore" / "pending" (an acknowledged not-yet-authored entry: the completeness gate lets it through, but the comparison still treats the name as unaliased)."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    aliases: dict[str, CellId | str] = {}
    for old_name, value in raw.items():
        if isinstance(value, str):
            aliases[old_name] = value
            continue
        aliases[old_name] = CellId(
            rune=value["rune"],
            stance=value["stance"],
            entry=value.get("entry"),
            exit=value.get("exit"),
            adjustments=tuple(value.get("adjustments", ())),
        )
    return aliases
