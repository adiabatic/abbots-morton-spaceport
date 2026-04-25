"""Slim, side-effect-free reachability view over compiled Quikscript IR.

The validator added in subtask A2 of the parent plan needs structural facts
about ``dict[str, JoinGlyph]`` (which families exist, which variants of a
family carry which entry/exit Ys, which pair-overrides apply under which
contexts) without dragging in the FEA emitter's lookup-DAG and cycle-detection
machinery in ``quikscript_fea._analyze_quikscript_joins``.

``JoinReachability`` is the narrow waist for that. Field shapes mirror the
corresponding entries on ``_JoinAnalysis`` for downstream familiarity, but the
population logic intentionally reads ``JoinGlyph`` attributes directly. Lookup
ordering, cycle detection, and the FEA emitter's policy-specific gates stay in
``quikscript_fea`` where they belong.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from quikscript_ir import JoinGlyph


__all__ = ["JoinReachability"]


@dataclass(frozen=True)
class JoinReachability:
    glyph_meta: Mapping[str, JoinGlyph]
    base_to_variants: Mapping[str, frozenset[str]]
    bk_replacements: Mapping[str, Mapping[int, str]]
    pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]
    fwd_replacements: Mapping[str, Mapping[int, str]]
    fwd_pair_overrides: Mapping[
        str, tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    ]
    gated_pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...], str], ...]]
    gated_fwd_pair_overrides: Mapping[
        str, tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]
    ]
    ligatures: tuple[tuple[str, tuple[str, ...]], ...]
    word_final_pairs: Mapping[str, str]
    entry_classes: Mapping[int, frozenset[str]]

    @classmethod
    def from_join_glyphs(
        cls, glyph_meta: Mapping[str, JoinGlyph]
    ) -> "JoinReachability":
        base_to_variants_buf: dict[str, set[str]] = {}
        bk_replacements_buf: dict[str, dict[int, str]] = {}
        pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        fwd_replacements_buf: dict[str, dict[int, str]] = {}
        fwd_pair_overrides_buf: dict[
            str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]
        ] = {}
        gated_pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...], str]]] = {}
        gated_fwd_pair_overrides_buf: dict[
            str, list[tuple[str, tuple[str, ...], tuple[str, ...], str]]
        ] = {}
        ligatures_buf: list[tuple[str, tuple[str, ...]]] = []
        word_final_pairs_buf: dict[str, str] = {}
        entry_classes_buf: dict[int, set[str]] = {}

        for glyph_name, meta in glyph_meta.items():
            base_to_variants_buf.setdefault(meta.base_name, set()).add(glyph_name)

            if meta.entry:
                for anchor in meta.entry:
                    entry_classes_buf.setdefault(anchor[1], set()).add(glyph_name)
                if not meta.after:
                    entry_y = meta.entry[0][1]
                    bk_replacements_buf.setdefault(meta.base_name, {}).setdefault(
                        entry_y, glyph_name
                    )

            if meta.after:
                after = tuple(meta.after)
                if meta.gate_feature:
                    gated_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, after, meta.gate_feature)
                    )
                else:
                    pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, after)
                    )

            if meta.exit and not meta.before:
                exit_y = meta.exit[0][1]
                fwd_replacements_buf.setdefault(meta.base_name, {}).setdefault(
                    exit_y, glyph_name
                )

            if meta.before:
                before = tuple(meta.before)
                not_after = tuple(meta.not_after)
                if meta.gate_feature:
                    gated_fwd_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, before, not_after, meta.gate_feature)
                    )
                else:
                    fwd_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, before, not_after)
                    )

            if meta.sequence and glyph_name == meta.base_name:
                ligatures_buf.append((glyph_name, meta.sequence))

            if meta.word_final:
                word_final_pairs_buf[meta.base_name] = glyph_name

        return cls(
            glyph_meta=MappingProxyType(dict(glyph_meta)),
            base_to_variants=MappingProxyType(
                {base: frozenset(names) for base, names in base_to_variants_buf.items()}
            ),
            bk_replacements=MappingProxyType(
                {base: MappingProxyType(dict(ys)) for base, ys in bk_replacements_buf.items()}
            ),
            pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in pair_overrides_buf.items()}
            ),
            fwd_replacements=MappingProxyType(
                {base: MappingProxyType(dict(ys)) for base, ys in fwd_replacements_buf.items()}
            ),
            fwd_pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in fwd_pair_overrides_buf.items()}
            ),
            gated_pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in gated_pair_overrides_buf.items()}
            ),
            gated_fwd_pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in gated_fwd_pair_overrides_buf.items()}
            ),
            ligatures=tuple(ligatures_buf),
            word_final_pairs=MappingProxyType(dict(word_final_pairs_buf)),
            entry_classes=MappingProxyType(
                {y: frozenset(names) for y, names in entry_classes_buf.items()}
            ),
        )
