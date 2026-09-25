"""Labels shared by the modules that read a settled stream: the spec's alphabet, a configuration's feature set, a formed stream's labels after the configuration's marker renaming, the boundary glyph names, and the alias map from the old font's glyph names to cells.

This module imports only `model`, `settle`, and `rowmodel` from the repository. That lets the review surface build use these labels without importing the conformance sweep or the other modules in `unit_cache.PIPELINE_NON_SURFACE_MODULES`, which are left out of the per-unit store stamps. `rebuild/test_review_code_closure.py` checks the import list.
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
    """Returns the formed stream's labels under the configuration's renaming: each letter becomes its marker twin when the configuration's features change its capability, and a letter that follows a ZWNJ and has an entry gets the `.noentry` suffix of the ZWNJ chokepoint. Labels are interned so that window keys built from many texts share one string per label."""
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
    """Reads `rebuild/m1-aliases.yaml`, which maps each old compiled glyph name to CellId fields or to one of the strings "boundary", "ignore", or "pending". A "pending" entry acknowledges a name that has no alias yet: the completeness check accepts it, but the comparison still treats the name as unaliased."""
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
