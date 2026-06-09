"""Read-only oracle for a derived demote-geometry contract.

This spike asks whether the hand-authored `predecessor_demote_overrides` and `trailing_demote_overrides` rows can be reproduced from compiled anchor geometry; the default run builds a temporary Senior font under `tmp/` with those two authored tables omitted, shapes letter sequences through depth 4, derives demote triples from the non-joining adjacent pairs that survive, and diffs that derived set against the healed authored rows without editing glyph YAML or emitter source.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import itertools
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import uharfbuzz as hb

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
TEST_DIR = ROOT / "test"
for path in (TOOLS_DIR, TEST_DIR):
    path_string = str(path)
    if path_string not in sys.path:
        sys.path.insert(0, path_string)

from build_font import build_font, load_glyph_data  # noqa: E402
from glyph_compiler import compile_glyph_set  # noqa: E402
from leak_contract_report import parse_snapshot  # noqa: E402
from leak_static_analysis import Rule, joins, parse_calt  # noqa: E402
from quikscript_shaping_helpers import _plain_quikscript_letters, _qs_text  # noqa: E402
from quikscript_ir import GlyphData, JoinGlyph, heal_glyph_name  # noqa: E402

FEA_PATH = TEST_DIR / "AbbotsMortonSpaceportSansSenior-Regular.fea"
DUMP_PATH = ROOT / "tmp" / "derived-demote-oracle.txt"
NO_AUTHORED_FONT_PATH = (
    ROOT / "tmp" / "derived-demote-no-authored" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
)

Side = Literal["predecessor", "trailing"]
Pair = tuple[str, str]
Triple = tuple[str, str, str]


@dataclass(frozen=True)
class IsolatedCandidate:
    name: str
    preserves_opposite_edge: bool
    reason: str


@dataclass(frozen=True)
class DerivedRow:
    side: Side
    triple: Triple
    pair_source_count: int
    isolated_reason: str


@dataclass(frozen=True)
class AuthoredProbe:
    side: Side
    triple: Triple
    derived_isolated_form: str | None
    reason: str

    @property
    def reproduces(self) -> bool:
        return self.derived_isolated_form == self.triple[2]


def _edge_modifier_count(meta: JoinGlyph, edge: Literal["entry", "exit"]) -> int:
    prefixes = ("en-", "after-") if edge == "entry" else ("ex-", "before-")
    return sum(1 for modifier in meta.modifiers if modifier.startswith(prefixes))


def _same_base_siblings(glyph_meta: dict[str, JoinGlyph], name: str) -> Iterable[JoinGlyph]:
    source = glyph_meta[name]
    for candidate in glyph_meta.values():
        if candidate.name != name and candidate.base_name == source.base_name:
            yield candidate


def _derive_isolated_form(
    glyph_meta: dict[str, JoinGlyph],
    *,
    side: Side,
    left_stance: str,
    right_stance: str,
) -> IsolatedCandidate | None:
    source_name = left_stance if side == "predecessor" else right_stance
    source = glyph_meta.get(source_name)
    left = glyph_meta.get(left_stance)
    right = glyph_meta.get(right_stance)
    if source is None or left is None or right is None:
        return None
    if side == "predecessor" and not source.exit_ys:
        return IsolatedCandidate("", False, "no predecessor exit anchor to demote")
    if side == "trailing" and not source.all_entry_ys:
        return IsolatedCandidate("", False, "no trailing entry anchor to demote")

    if side == "predecessor":
        opposite_edge = "entry"
        dropped_edge = "exit"
        source_opposite_ys = set(source.all_entry_ys)
        source_dropped_ys = set(source.exit_ys)

        def remains_non_joining(candidate: JoinGlyph) -> bool:
            return not joins(candidate.name, right_stance)

        def dropped_edge_changed(candidate: JoinGlyph) -> bool:
            return (
                _edge_modifier_count(candidate, dropped_edge) < _edge_modifier_count(source, dropped_edge)
                or set(candidate.exit_ys) != source_dropped_ys
            )

        def opposite_ys(candidate: JoinGlyph) -> set[int]:
            return set(candidate.all_entry_ys)

    else:
        opposite_edge = "exit"
        dropped_edge = "entry"
        source_opposite_ys = set(source.exit_ys)
        source_dropped_ys = set(source.all_entry_ys)

        def remains_non_joining(candidate: JoinGlyph) -> bool:
            return not joins(left_stance, candidate.name)

        def dropped_edge_changed(candidate: JoinGlyph) -> bool:
            return (
                _edge_modifier_count(candidate, dropped_edge) < _edge_modifier_count(source, dropped_edge)
                or set(candidate.all_entry_ys) != source_dropped_ys
            )

        def opposite_ys(candidate: JoinGlyph) -> set[int]:
            return set(candidate.exit_ys)

    source_dropped_count = _edge_modifier_count(source, dropped_edge)
    ranked: list[tuple[tuple[int, int, int, int, int, int, str], JoinGlyph, bool]] = []
    for candidate in _same_base_siblings(glyph_meta, source_name):
        if not remains_non_joining(candidate):
            continue
        if not dropped_edge_changed(candidate):
            continue
        if (
            source_dropped_count
            and _edge_modifier_count(candidate, dropped_edge) >= source_dropped_count
            and opposite_ys(candidate) == source_opposite_ys
        ):
            continue
        candidate_opposite_ys = opposite_ys(candidate)
        opposite_delta = len(source_opposite_ys ^ candidate_opposite_ys)
        preserves_opposite = opposite_delta == 0
        extra_modifiers = len(set(candidate.modifiers) - set(source.modifiers))
        remaining_modifiers = len(candidate.modifiers)
        contextual_penalty = 1 if candidate.is_contextual else 0
        non_base_penalty = 0 if candidate.name == source.base_name else 1
        rank = (
            opposite_delta,
            _edge_modifier_count(candidate, dropped_edge),
            extra_modifiers,
            remaining_modifiers,
            contextual_penalty,
            non_base_penalty,
            candidate.name,
        )
        ranked.append((rank, candidate, preserves_opposite))
    if not ranked:
        return None
    _, chosen, preserves_opposite = min(ranked, key=lambda item: item[0])
    if preserves_opposite:
        reason = f"keeps {opposite_edge} edge and reduces {dropped_edge} edge"
    else:
        reason = f"drops {opposite_edge} edge too; no same-base non-joining candidate preserved it"
    return IsolatedCandidate(chosen.name, preserves_opposite, reason)


def _is_demote_lookup(lookup_name: str) -> bool:
    return "demote" in lookup_name


def _initial_glyphs(glyph_meta: dict[str, JoinGlyph]) -> frozenset[str]:
    return frozenset(
        name for name, meta in glyph_meta.items() if name.startswith("qs") and not meta.is_contextual
    )


def _add_pair(
    pair_sources: dict[Pair, set[str]],
    pair: Pair,
    source: str,
    *,
    source_limit: int,
) -> None:
    sources = pair_sources[pair]
    if len(sources) < source_limit:
        sources.add(source)


def _derive_reachable_pairs(
    glyph_meta: dict[str, JoinGlyph],
    *,
    fea_path: Path,
    source_limit: int = 8,
) -> dict[Pair, set[str]]:
    program = parse_calt(str(fea_path))
    initial = _initial_glyphs(glyph_meta)
    pair_sources: dict[Pair, set[str]] = defaultdict(set)
    for left in initial:
        for right in initial:
            _add_pair(pair_sources, (left, right), "initial noncontextual pair", source_limit=source_limit)

    for lookup_name in program.lookups:
        if _is_demote_lookup(lookup_name):
            continue
        rules = [
            rule
            for rule in program.rules_by_lookup.get(lookup_name, ())
            if rule.kind == "sub" and rule.replacement is not None and rule.replacement in glyph_meta
        ]
        if not rules:
            continue
        current_pairs = set(pair_sources)
        by_left: dict[str, set[str]] = defaultdict(set)
        by_right: dict[str, set[str]] = defaultdict(set)
        for left, right in current_pairs:
            by_left[left].add(right)
            by_right[right].add(left)
        for rule in rules:
            _apply_rule_to_pairs(
                rule, current_pairs, by_left, by_right, pair_sources, source_limit=source_limit
            )
    return pair_sources


def _font(font_path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(font_path))
    return hb.Font(hb.Face(blob))


def _shape(font: hb.Font, families: tuple[str, ...]) -> list[str]:
    buf = hb.Buffer()
    buf.add_str(_qs_text(*families))
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": False})
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


def _is_letter_glyph(name: str) -> bool:
    if not name.startswith("qs"):
        return False
    base = name.split(".", 1)[0]
    return base not in {"qsAngleParenLeft", "qsAngleParenRight"}


def _build_no_authored_demote_font(glyph_data: GlyphData) -> Path:
    output_path = NO_AUTHORED_FONT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_data: GlyphData = copy.deepcopy(glyph_data)
    analysis_data["predecessor_demote_overrides"] = []
    analysis_data["trailing_demote_overrides"] = []
    with contextlib.redirect_stdout(io.StringIO()):
        build_font(analysis_data, output_path, variant="senior")
    return output_path


def _derive_shaped_no_authored_pairs(
    glyph_data: GlyphData,
    glyph_meta: dict[str, JoinGlyph],
    *,
    max_len: int,
    source_limit: int = 8,
) -> dict[Pair, set[str]]:
    font_path = _build_no_authored_demote_font(glyph_data)
    font = _font(font_path)
    alphabet = [name for name, _codepoint in _plain_quikscript_letters()]
    pair_sources: dict[Pair, set[str]] = defaultdict(set)
    for length in range(2, max_len + 1):
        for families in itertools.product(alphabet, repeat=length):
            glyphs = [name for name in _shape(font, families) if _is_letter_glyph(name)]
            for left_stance, right_stance in zip(glyphs, glyphs[1:]):
                if left_stance in glyph_meta and right_stance in glyph_meta:
                    _add_pair(
                        pair_sources,
                        (left_stance, right_stance),
                        " ".join(families),
                        source_limit=source_limit,
                    )
    return pair_sources


def _apply_rule_to_pairs(
    rule: Rule,
    current_pairs: set[Pair],
    by_left: dict[str, set[str]],
    by_right: dict[str, set[str]],
    pair_sources: dict[Pair, set[str]],
    *,
    source_limit: int,
) -> None:
    replacement = rule.replacement
    if replacement is None:
        return
    source = f"{rule.lookup}:{rule.line_no}"
    for pivot in rule.pivot:
        if rule.lookahead:
            for right in rule.lookahead[0]:
                if (pivot, right) in current_pairs:
                    _add_pair(pair_sources, (replacement, right), source, source_limit=source_limit)
        else:
            for right in by_left.get(pivot, ()):
                _add_pair(pair_sources, (replacement, right), source, source_limit=source_limit)
        if rule.backtrack:
            for left in rule.backtrack[-1]:
                if (left, pivot) in current_pairs:
                    _add_pair(pair_sources, (left, replacement), source, source_limit=source_limit)
        else:
            for left in by_right.get(pivot, ()):
                _add_pair(pair_sources, (left, replacement), source, source_limit=source_limit)


def _load_authored_triples(glyph_meta: dict[str, JoinGlyph]) -> tuple[set[Triple], set[Triple]]:
    data = load_glyph_data(ROOT / "glyph_data")
    family_names = set(data.get("glyph_families", {}))
    available_names = frozenset(glyph_meta)

    def heal(name: str) -> str:
        return heal_glyph_name(name, family_names, available_names)

    predecessor = {
        (
            heal(entry["predecessor_stance"]),
            heal(entry["trigger_stance"]),
            heal(entry["isolated_form"]),
        )
        for entry in data.get("predecessor_demote_overrides", []) or []
    }
    trailing = {
        (
            heal(entry["leader_stance"]),
            heal(entry["trailing_stance"]),
            heal(entry["isolated_form"]),
        )
        for entry in data.get("trailing_demote_overrides", []) or []
    }
    return predecessor, trailing


def _derive_rows(
    glyph_meta: dict[str, JoinGlyph],
    pair_sources: dict[Pair, set[str]],
) -> tuple[dict[Triple, DerivedRow], dict[Triple, DerivedRow]]:
    predecessor: dict[Triple, DerivedRow] = {}
    trailing: dict[Triple, DerivedRow] = {}
    for left_stance, right_stance in sorted(pair_sources):
        if left_stance not in glyph_meta or right_stance not in glyph_meta:
            continue
        if joins(left_stance, right_stance):
            continue
        left_meta = glyph_meta[left_stance]
        right_meta = glyph_meta[right_stance]
        if left_meta.is_contextual or left_meta.generated_from is not None:
            candidate = _derive_isolated_form(
                glyph_meta,
                side="predecessor",
                left_stance=left_stance,
                right_stance=right_stance,
            )
            if candidate is not None and candidate.name:
                triple = (left_stance, right_stance, candidate.name)
                predecessor.setdefault(
                    triple,
                    DerivedRow(
                        side="predecessor",
                        triple=triple,
                        pair_source_count=len(pair_sources[(left_stance, right_stance)]),
                        isolated_reason=candidate.reason,
                    ),
                )
        if right_meta.is_contextual or right_meta.generated_from is not None:
            candidate = _derive_isolated_form(
                glyph_meta,
                side="trailing",
                left_stance=left_stance,
                right_stance=right_stance,
            )
            if candidate is not None and candidate.name:
                triple = (left_stance, right_stance, candidate.name)
                trailing.setdefault(
                    triple,
                    DerivedRow(
                        side="trailing",
                        triple=triple,
                        pair_source_count=len(pair_sources[(left_stance, right_stance)]),
                        isolated_reason=candidate.reason,
                    ),
                )
    return predecessor, trailing


def _probe_authored(
    glyph_meta: dict[str, JoinGlyph],
    *,
    side: Side,
    authored: set[Triple],
) -> list[AuthoredProbe]:
    probes: list[AuthoredProbe] = []
    for left_stance, right_stance, isolated_form in sorted(authored):
        candidate = _derive_isolated_form(
            glyph_meta,
            side=side,
            left_stance=left_stance,
            right_stance=right_stance,
        )
        if candidate is None:
            probes.append(
                AuthoredProbe(
                    side=side,
                    triple=(left_stance, right_stance, isolated_form),
                    derived_isolated_form=None,
                    reason="no same-base non-joining sibling candidate",
                )
            )
        elif not candidate.name:
            probes.append(
                AuthoredProbe(
                    side=side,
                    triple=(left_stance, right_stance, isolated_form),
                    derived_isolated_form=None,
                    reason=candidate.reason,
                )
            )
        else:
            probes.append(
                AuthoredProbe(
                    side=side,
                    triple=(left_stance, right_stance, isolated_form),
                    derived_isolated_form=candidate.name,
                    reason=candidate.reason,
                )
            )
    return probes


def _live_snapshot_indexes() -> tuple[set[Triple], set[Triple]]:
    predecessor: set[Triple] = set()
    trailing: set[Triple] = set()
    for signature, _label in parse_snapshot():
        isolated_left, left_chosen, isolated_right, right_chosen = signature
        if isolated_left != left_chosen:
            predecessor.add((left_chosen, right_chosen, isolated_left))
        if isolated_right != right_chosen:
            trailing.add((left_chosen, right_chosen, isolated_right))
    return predecessor, trailing


def _bucket_authored_miss(
    glyph_meta: dict[str, JoinGlyph],
    *,
    side: Side,
    triple: Triple,
    derived_isolated_form: str | None,
) -> str:
    left_stance, right_stance, _isolated_form = triple
    left = glyph_meta[left_stance]
    right = glyph_meta[right_stance]
    if joins(left_stance, right_stance):
        return "real-join"
    if side == "predecessor" and not left.exit_ys:
        return "inverted/predecessor-aware"
    if side == "trailing" and not right.all_entry_ys:
        return "inverted/predecessor-aware"
    if len(left.sequence) > 1 or len(right.sequence) > 1:
        return "ligature-compose"
    if derived_isolated_form is not None:
        return "predecessor-of-predecessor"
    return "predecessor-of-predecessor"


def _format_triple(triple: Triple) -> str:
    return "  - {" + f"left: {triple[0]}, right: {triple[1]}, isolated_form: {triple[2]}" + "}"


def _format_report(
    *,
    pair_source_label: str,
    glyph_meta: dict[str, JoinGlyph],
    pair_sources: dict[Pair, set[str]],
    authored_predecessor: set[Triple],
    authored_trailing: set[Triple],
    derived_predecessor: dict[Triple, DerivedRow],
    derived_trailing: dict[Triple, DerivedRow],
    predecessor_probes: list[AuthoredProbe],
    trailing_probes: list[AuthoredProbe],
) -> str:
    live_predecessor, live_trailing = _live_snapshot_indexes()

    def partition(
        authored: set[Triple], derived: dict[Triple, DerivedRow]
    ) -> tuple[set[Triple], set[Triple], set[Triple]]:
        derived_set = set(derived)
        return authored & derived_set, authored - derived_set, derived_set - authored

    reproduced_predecessor, missing_predecessor, extra_predecessor = partition(
        authored_predecessor, derived_predecessor
    )
    reproduced_trailing, missing_trailing, extra_trailing = partition(authored_trailing, derived_trailing)
    probe_by_key = {(probe.side, probe.triple): probe for probe in (*predecessor_probes, *trailing_probes)}
    predecessor_reachability_only_missing = sum(
        1
        for triple in missing_predecessor
        if (probe := probe_by_key.get(("predecessor", triple))) is not None and probe.reproduces
    )
    trailing_reachability_only_missing = sum(
        1
        for triple in missing_trailing
        if (probe := probe_by_key.get(("trailing", triple))) is not None and probe.reproduces
    )
    live_extra_predecessor = extra_predecessor & live_predecessor
    live_extra_trailing = extra_trailing & live_trailing

    lines = [
        "# Derived demote oracle report. Generated by tools/derived_demote_oracle.py; do not hand-edit.",
        "",
        "## Summary",
        f"- pair source: {pair_source_label}",
        f"- candidate adjacent pairs: {len(pair_sources)}",
        f"- authored predecessor rows: {len(authored_predecessor)}",
        f"- authored trailing rows: {len(authored_trailing)}",
        f"- derived predecessor rows: {len(derived_predecessor)}",
        f"- derived trailing rows: {len(derived_trailing)}",
        f"- predecessor reproduced/missing/extra: {len(reproduced_predecessor)}/{len(missing_predecessor)}/{len(extra_predecessor)}",
        f"- trailing reproduced/missing/extra: {len(reproduced_trailing)}/{len(missing_trailing)}/{len(extra_trailing)}",
        f"- missing rows where authored-pair target derivation succeeds but the pair source did not surface the row: {predecessor_reachability_only_missing + trailing_reachability_only_missing}",
        f"- extra rows that are live visible depth-4 snapshot signatures: {len(live_extra_predecessor) + len(live_extra_trailing)}",
        f"- authored-pair isolated-target probes reproduced: {sum(1 for probe in predecessor_probes + trailing_probes if probe.reproduces)}/{len(predecessor_probes) + len(trailing_probes)}",
        "",
    ]

    missing_buckets: Counter[str] = Counter()
    for triple in missing_predecessor:
        probe = probe_by_key.get(("predecessor", triple))
        missing_buckets[
            _bucket_authored_miss(
                glyph_meta,
                side="predecessor",
                triple=triple,
                derived_isolated_form=None if probe is None else probe.derived_isolated_form,
            )
        ] += 1
    for triple in missing_trailing:
        probe = probe_by_key.get(("trailing", triple))
        missing_buckets[
            _bucket_authored_miss(
                glyph_meta,
                side="trailing",
                triple=triple,
                derived_isolated_form=None if probe is None else probe.derived_isolated_form,
            )
        ] += 1
    lines.append("## Missing authored bucket counts")
    for bucket, count in sorted(missing_buckets.items()):
        lines.append(f"- {bucket}: {count}")
    lines.append("")

    lines.append("## Authored-pair isolated-target mismatches")
    for probe in sorted(predecessor_probes + trailing_probes, key=lambda probe: (probe.side, probe.triple)):
        if probe.reproduces:
            continue
        left_stance, right_stance, isolated_form = probe.triple
        derived = probe.derived_isolated_form or "<none>"
        lines.append(
            f"- {probe.side}: {left_stance} + {right_stance} -> authored {isolated_form}, derived {derived} ({probe.reason})"
        )
    lines.append("")

    def append_partition(
        title: str, rows: Iterable[Triple], derived: dict[Triple, DerivedRow], live_index: set[Triple]
    ) -> None:
        lines.append(f"## {title}")
        for triple in sorted(rows):
            row = derived.get(triple)
            live = " live-depth4" if triple in live_index else ""
            if row is None:
                probe = probe_by_key.get(("predecessor" if "predecessor" in title else "trailing", triple))
                detail = (
                    ""
                    if probe is None
                    else f" derived_target={probe.derived_isolated_form or '<none>'} reason={probe.reason}"
                )
                lines.append(_format_triple(triple) + detail + live)
            else:
                lines.append(
                    _format_triple(triple)
                    + f" sources={row.pair_source_count} reason={row.isolated_reason}"
                    + live
                )
        lines.append("")

    append_partition("Reproduced predecessor", reproduced_predecessor, derived_predecessor, live_predecessor)
    append_partition("Missing predecessor", missing_predecessor, derived_predecessor, live_predecessor)
    append_partition("Extra predecessor", extra_predecessor, derived_predecessor, live_predecessor)
    append_partition("Reproduced trailing", reproduced_trailing, derived_trailing, live_trailing)
    append_partition("Missing trailing", missing_trailing, derived_trailing, live_trailing)
    append_partition("Extra trailing", extra_trailing, derived_trailing, live_trailing)

    lines.append("## Pair sources for derived extras")
    for side, extras, derived in (
        ("predecessor", extra_predecessor, derived_predecessor),
        ("trailing", extra_trailing, derived_trailing),
    ):
        lines.append(f"### {side}")
        for triple in sorted(extras):
            left_stance, right_stance, _isolated_form = triple
            sources = ", ".join(sorted(pair_sources[(left_stance, right_stance)]))
            lines.append(f"- {left_stance} + {right_stance}: {sources}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive demote override triples from compiled anchor geometry and compare them with the authored tables."
    )
    parser.add_argument(
        "--fea",
        type=Path,
        default=FEA_PATH,
        help="Senior FEA path to parse for non-demote calt pair reachability.",
    )
    parser.add_argument("--dump", type=Path, default=DUMP_PATH, help="Report path.")
    parser.add_argument(
        "--pair-source",
        choices=("shaped-no-authored", "static-calt"),
        default="shaped-no-authored",
        help="Use an exhaustive temporary no-authored-demote shaping sweep, or the older static non-demote calt pair reachability over-approximation.",
    )
    parser.add_argument(
        "--max-len", type=int, default=4, help="Maximum input length for --pair-source shaped-no-authored."
    )
    args = parser.parse_args()

    glyph_data = load_glyph_data(ROOT / "glyph_data")
    glyph_meta = compile_glyph_set(glyph_data, "senior").glyph_meta
    authored_predecessor, authored_trailing = _load_authored_triples(glyph_meta)
    if args.pair_source == "static-calt":
        pair_sources = _derive_reachable_pairs(glyph_meta, fea_path=args.fea)
        pair_source_label = f"static non-demote calt reachability from {args.fea.relative_to(ROOT)}"
    else:
        pair_sources = _derive_shaped_no_authored_pairs(glyph_data, glyph_meta, max_len=args.max_len)
        pair_source_label = f"exhaustive no-authored-demote shaping sweep to depth {args.max_len}"
    derived_predecessor, derived_trailing = _derive_rows(glyph_meta, pair_sources)
    predecessor_probes = _probe_authored(glyph_meta, side="predecessor", authored=authored_predecessor)
    trailing_probes = _probe_authored(glyph_meta, side="trailing", authored=authored_trailing)

    report = _format_report(
        pair_source_label=pair_source_label,
        glyph_meta=glyph_meta,
        pair_sources=pair_sources,
        authored_predecessor=authored_predecessor,
        authored_trailing=authored_trailing,
        derived_predecessor=derived_predecessor,
        derived_trailing=derived_trailing,
        predecessor_probes=predecessor_probes,
        trailing_probes=trailing_probes,
    )
    args.dump.parent.mkdir(exist_ok=True)
    args.dump.write_text(report)

    reproduced_predecessor = len(authored_predecessor & set(derived_predecessor))
    missing_predecessor_set = authored_predecessor - set(derived_predecessor)
    missing_predecessor = len(missing_predecessor_set)
    extra_predecessor_set = set(derived_predecessor) - authored_predecessor
    extra_predecessor = len(extra_predecessor_set)
    reproduced_trailing = len(authored_trailing & set(derived_trailing))
    missing_trailing_set = authored_trailing - set(derived_trailing)
    missing_trailing = len(missing_trailing_set)
    extra_trailing_set = set(derived_trailing) - authored_trailing
    extra_trailing = len(extra_trailing_set)
    probe_total = len(predecessor_probes) + len(trailing_probes)
    probe_reproduced = sum(1 for probe in predecessor_probes + trailing_probes if probe.reproduces)
    probe_by_key = {(probe.side, probe.triple): probe for probe in (*predecessor_probes, *trailing_probes)}
    reachability_only_missing = sum(
        1
        for side, triple in (
            *(("predecessor", triple) for triple in missing_predecessor_set),
            *(("trailing", triple) for triple in missing_trailing_set),
        )
        if (probe := probe_by_key.get((side, triple))) is not None and probe.reproduces
    )
    live_predecessor, live_trailing = _live_snapshot_indexes()
    live_extras = len(extra_predecessor_set & live_predecessor) + len(extra_trailing_set & live_trailing)

    print(f"Pair source: {pair_source_label}")
    print(f"Candidate adjacent pairs: {len(pair_sources)}")
    print(
        f"Predecessor authored/derived/reproduced/missing/extra: {len(authored_predecessor)}/{len(derived_predecessor)}/{reproduced_predecessor}/{missing_predecessor}/{extra_predecessor}"
    )
    print(
        f"Trailing authored/derived/reproduced/missing/extra: {len(authored_trailing)}/{len(derived_trailing)}/{reproduced_trailing}/{missing_trailing}/{extra_trailing}"
    )
    print(f"Reachability-only missing rows: {reachability_only_missing}")
    print(f"Extra rows live in depth-4 snapshots: {live_extras}")
    print(f"Authored-pair isolated-target probes reproduced: {probe_reproduced}/{probe_total}")
    print(f"Full report written to {args.dump.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
