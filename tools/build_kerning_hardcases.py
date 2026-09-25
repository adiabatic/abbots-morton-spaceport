"""Enumerate Quikscript "hard-case" stance junctions and write JSON for site/kerning.html.

The kerning matrix in ``site/kerning.html`` shows only the isolated two-letter shaping of each family pair, so some stance-to-stance junctions never appear there. For example, ·No·Utter shapes to ``qsNo.alt`` + ``qsUtter`` in isolation, so the (plain ·No, alt ·Utter) and (alt ·No, alt ·Utter) combinations appear only in longer text. This tool finds those junctions from two sources and takes their union:

1. **Alt-axis cross-product.** For every family pair where at least one side has an enabled ``traits: [alt]`` stance, it enumerates the ``{plain, alt}`` combinations and keeps each one that some context produces. When a pair has at least one other combination, the combination its isolated two-letter shaping produces is also emitted, with ``isolated: true``. Each side's selector covers a whole kind: ``qsUtter.alt`` is a prefix that matches every alt variant, and "plain" is the family minus its ``.alt`` variants. ``half`` is supported but disabled (see ``ALT_AXIS_KINDS``).
2. **Demote and restore tables.** The ``predecessor_demote_overrides``, ``trailing_demote_overrides``, and ``restore_isolated_form_overrides`` tables in ``glyph_data/quikscript.yaml`` name specific contextual stances (such as ``qsIt.ex-y0.before-day``) that the alt-axis pass does not cover. A junction from these tables is dropped as ``superseded_by_alt_axis`` when the alt-axis pass already emitted junctions for its family pair.

For each junction it looks for a context that produces it, trying corpus text first and then a bounded, deterministic synthetic search, and records the offsets site/kerning.html uses to highlight the junction within that context.

Run after ``make all`` (``make build-kerning-hardcases`` runs both)::

    uv run python tools/build_kerning_hardcases.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
SITE_DIR = ROOT / "site"
QUIKSCRIPT_YAML = ROOT / "glyph_data" / "quikscript.yaml"

if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from quikscript_shaping_helpers import (  # noqa: E402
    ZWNJ,
    _char_map,
    _compiled_meta,
    _font,
    _gid_to_full_name,
)

# Importing the shaping helpers puts ``tools/`` on ``sys.path``, so this resolves.
from quikscript_ir import heal_glyph_name  # noqa: E402

CORPUS_FILES: tuple[Path, ...] = (
    SITE_DIR / "the-manual.html",
    SITE_DIR / "index.html",
    SITE_DIR / "extra-senior-words.html",
)
QS_FIRST = 0xE650
QS_LAST = 0xE67F
QS_RUN_RE = re.compile("[\ue650-\ue67f\u200c]+")
ENTITY_HEX_RE = re.compile(r"&#x([0-9A-Fa-f]+);")
ENTITY_DEC_RE = re.compile(r"&#(\d+);")

ENTRYLESS_MARKERS = (".noentry", ".ex-noentry", ".nonjoining-left")

# The trait kinds the cross-product pass enumerates. `half` is left out because it interacts more with join geometry (·He has a `shared_kern_entangled` skip). The code handles any kind the same way, so enabling it means adding it to this tuple.
ALT_AXIS_KINDS = ("alt",)


def _plain_families_by_codepoint() -> list[str]:
    chars = _char_map()
    plain = [
        name
        for name in chars
        if name.startswith("qs")
        and "_" not in name
        and "." not in name
        and name not in {"qsAngleParenLeft", "qsAngleParenRight"}
    ]
    return sorted(plain, key=lambda name: ord(chars[name]))


def _family_char(family: str) -> str | None:
    return _char_map().get(family)


def _prefix_match(glyph_name: str, target: str) -> bool:
    """Return whether ``glyph_name`` matches the target stance ``target``.

    A bare family name must match exactly, because the demote tables' bare ``isolated_form`` means that bare glyph: ``qsJai.en-y5.ex-y0`` is a different junction from ``qsJai``. A dotted stance name also matches any name that extends it with further modifiers (``qsGay.ex-y0`` matches ``qsGay.ex-y0.ex-ext-1``).
    """
    if "." not in target:
        return glyph_name == target
    return glyph_name == target or glyph_name.startswith(target + ".")


def _decode_entities(text: str) -> str:
    text = ENTITY_HEX_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = ENTITY_DEC_RE.sub(lambda m: chr(int(m.group(1))), text)
    return text


def _harvest_sequences(paths: tuple[Path, ...]) -> list[str]:
    seen: set[str] = set()
    for path in paths:
        text = _decode_entities(path.read_text())
        for run in QS_RUN_RE.findall(text):
            qs_letters = sum(1 for c in run if QS_FIRST <= ord(c) <= QS_LAST)
            if qs_letters >= 2:
                seen.add(run)
    return sorted(seen)


def _shape_clusters(text: str) -> tuple[list[str], list[int]]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    names = [_gid_to_full_name(info.codepoint) for info in buf.glyph_infos]
    clusters = [info.cluster for info in buf.glyph_infos]
    return names, clusters


def _base_name(glyph_name: str) -> str:
    meta = _compiled_meta().get(glyph_name)
    if meta is not None:
        return meta.base_name
    return glyph_name.split(".", 1)[0].split("_", 1)[0]


def _stance_prefix(glyph_name: str, base: str) -> str | None:
    return None if glyph_name == base else glyph_name


def _glyph_kind(glyph_name: str) -> str:
    """Return the first kind in ``ALT_AXIS_KINDS`` that the glyph has as a trait, or ``"plain"``.

    A glyph whose only trait is a disabled kind, such as ``half``, counts as ``"plain"``, which keeps disabled kinds out of the cross-product.
    """
    meta = _compiled_meta().get(glyph_name)
    traits = meta.traits if meta is not None else frozenset()
    for kind in ALT_AXIS_KINDS:
        if kind in traits:
            return kind
    return "plain"


def _family_alt_kinds() -> dict[str, set[str]]:
    """Map each family to the kinds in ``ALT_AXIS_KINDS`` it has a non-ligature stance for (for example ``qsNo -> {"alt"}``)."""
    result: dict[str, set[str]] = defaultdict(set)
    for name, meta in _compiled_meta().items():
        if "_" in name:
            continue
        for kind in ALT_AXIS_KINDS:
            if kind in meta.traits:
                result[meta.base_name].add(kind)
    return result


def _selector_alt_kind(family: str, kind: str) -> dict:
    """Return the selector for one side's alternate kind: the stance prefix ``qsNo.alt``, which matches every variant of that kind."""
    return {"family": family, "kind": kind, "stance": f"{family}.{kind}", "except": None}


def _selector_plain(family: str, alt_kinds_present: set[str]) -> dict:
    """Return the selector for one side's plain kind: the whole family with its enabled alternate stances in ``except``, because the bare family name alone would also match ``qsNo.alt``."""
    excepts = [f"{family}.{kind}" for kind in ALT_AXIS_KINDS if kind in alt_kinds_present]
    return {"family": family, "kind": "plain", "stance": None, "except": excepts or None}


def _selector_from_stance_prefix(stance: str | None, base: str) -> dict:
    """Return the selector for one side of a table-derived junction: the whole family when ``stance`` is None, otherwise that stance prefix."""
    if stance is None:
        return {"family": base, "kind": "plain", "stance": None, "except": None}
    return {"family": base, "kind": "stance", "stance": stance, "except": None}


def _selector_dedupe_key(selector: dict) -> tuple:
    return (selector["kind"], selector["stance"], tuple(selector["except"] or ()))


def _skip_reason(left: str, right: str) -> str | None:
    if "_" in left or "_" in right:
        return "ligature"
    if any(marker in left for marker in ENTRYLESS_MARKERS) or any(
        marker in right for marker in ENTRYLESS_MARKERS
    ):
        return "entryless"
    if _base_name(left) == "qsHe" and ".noentry" in right:
        return "shared_kern_entangled"
    return None


def _cluster_input_range(clusters: list[int], index: int, text_len: int) -> tuple[int, int]:
    """Half-open input range owned by output glyph ``index``, using monotonic clusters."""
    start = clusters[index]
    end = clusters[index + 1] if index + 1 < len(clusters) else text_len
    return start, end


class _NoContext:
    pass


class _ClusterAmbiguous:
    pass


def _find_context(
    sequences: list[str], accept
) -> tuple[str, int, int, str, str] | _NoContext | _ClusterAmbiguous:
    """Return ``(context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)`` for the first sequence with an adjacent shaped pair that satisfies ``accept(leftGlyph, rightGlyph)``.

    The pair must map to two non-empty, contiguous input ranges. Returns ``_ClusterAmbiguous`` when some sequence produces the pair but never with such ranges, and ``_NoContext`` when none produces it. ``leftGlyph`` and ``rightGlyph`` are the shaped glyph names.
    """
    found_pair_but_ambiguous = False
    for context in sequences:
        names, clusters = _shape_clusters(context)
        for i in range(len(names) - 1):
            if accept(names[i], names[i + 1]):
                text_len = len(context)
                left_start, left_end = _cluster_input_range(clusters, i, text_len)
                right_start, right_end = _cluster_input_range(clusters, i + 1, text_len)
                if left_start >= left_end or right_start >= right_end:
                    found_pair_but_ambiguous = True
                    continue
                if left_end != right_start:
                    found_pair_but_ambiguous = True
                    continue
                return context, left_start, right_end, names[i], names[i + 1]
    if found_pair_but_ambiguous:
        return _ClusterAmbiguous()
    return _NoContext()


def _verify(context: str, accept, before_end: int, junction_end: int) -> bool:
    names, clusters = _shape_clusters(context)
    text_len = len(context)
    for i in range(len(names) - 1):
        if accept(names[i], names[i + 1]):
            left_start, left_end = _cluster_input_range(clusters, i, text_len)
            right_start, right_end = _cluster_input_range(clusters, i + 1, text_len)
            if left_start == before_end and left_end == right_start and right_end == junction_end:
                return True
    return False


def _is_hidden(left: str, right: str) -> bool:
    """Return True unless shaping the two base letters alone produces an adjacent pair that matches ``(left, right)`` under ``_prefix_match``."""
    left_char = _family_char(_base_name(left))
    right_char = _family_char(_base_name(right))
    if left_char is None or right_char is None:
        # A ligature or other non-plain base has no two-letter isolated shaping.
        return True
    names, _ = _shape_clusters(left_char + right_char)
    for i in range(len(names) - 1):
        if _prefix_match(names[i], left) and _prefix_match(names[i + 1], right):
            return False
    return True


def _synthetic_contexts(left_base: str, right_base: str) -> list[str]:
    """Return the candidate context strings for a junction between ``left_base`` and ``right_base``.

    Each candidate is the pair with up to two filler characters (every plain family and ZWNJ), ordered shortest first: the bare pair, then one filler after, one before, two after, one on each side, and two before. A base with no character of its own, such as a ligature, yields no candidates.
    """
    left_char = _family_char(left_base)
    right_char = _family_char(right_base)
    if left_char is None or right_char is None:
        return []
    fillers: list[str] = [_char_map()[name] for name in _plain_families_by_codepoint()]
    fillers.append(ZWNJ)
    pair = left_char + right_char
    candidates: list[str] = [pair]
    for x in fillers:
        candidates.append(pair + x)
    for x in fillers:
        candidates.append(x + pair)
    for x in fillers:
        for y in fillers:
            candidates.append(pair + x + y)
    for x in fillers:
        for y in fillers:
            candidates.append(x + pair + y)
    for x in fillers:
        for y in fillers:
            candidates.append(x + y + pair)
    return candidates


def _junction_targets(table_name: str, entry: dict) -> tuple[str, str]:
    """Return the adjacent glyph pair ``(left, right)`` a demote-table entry describes, as the entry's stance names before healing."""
    if table_name == "predecessor_demote":
        return entry["isolated_form"], entry["trigger_stance"]
    return entry["leader_stance"], entry["isolated_form"]


def _resolve_match(
    accept,
    context_sources: list[tuple[str, list[str]]],
    table_name: str,
) -> tuple[str, str, int, int, str, str] | _NoContext | _ClusterAmbiguous:
    """Return ``(source, context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)`` for the first ``(source, candidate_contexts)`` group with a match that reshaping confirms.

    Otherwise returns ``_ClusterAmbiguous`` if any group returned it, or ``_NoContext``. An ambiguous group does not stop later groups from being tried.
    """
    saw_ambiguous = False
    for source, candidates in context_sources:
        result = _find_context(candidates, accept)
        if isinstance(result, _NoContext):
            continue
        if isinstance(result, _ClusterAmbiguous):
            saw_ambiguous = True
            continue

        context, before_end, junction_end, left_glyph, right_glyph = result
        if not _verify(context, accept, before_end, junction_end):
            print(
                f"self-check failed for {table_name} {left_glyph!r}+{right_glyph!r} in {context!r}; refusing to emit",
                file=sys.stderr,
            )
            continue

        return source, context, before_end, junction_end, left_glyph, right_glyph

    return _ClusterAmbiguous() if saw_ambiguous else _NoContext()


def _resolve_record(
    target_left: str,
    target_right: str,
    table_name: str,
    context_sources: list[tuple[str, list[str]]],
    alt_owned_pairs: set[str],
) -> dict | str:
    """Return the output record for a demote or restore table junction ``(target_left, target_right)``, or a skip reason.

    A junction whose family pair is in ``alt_owned_pairs`` is skipped as ``superseded_by_alt_axis``. On such a pair the plain and alt selectors do not overlap, and site/kerning.html writes one kerning rule per alt-axis record on the assumption that no two rules overlap. The alt-axis pass emits only the kind combinations some context produces, so the records need not cover every glyph pair. A table-derived selector there would overlap one of them.
    """
    skip = _skip_reason(target_left, target_right)
    if skip is not None:
        return skip

    if not _is_hidden(target_left, target_right):
        return "not_hidden"

    accept = lambda left, right: _prefix_match(left, target_left) and _prefix_match(right, target_right)
    match = _resolve_match(accept, context_sources, table_name)
    if isinstance(match, _NoContext):
        return "no_context"
    if isinstance(match, _ClusterAmbiguous):
        return "cluster_ambiguous"

    source, context, before_end, junction_end, left_glyph, right_glyph = match
    left_base = _base_name(left_glyph)
    right_base = _base_name(right_glyph)
    key = f"{left_base}|{right_base}"
    if key in alt_owned_pairs:
        return "superseded_by_alt_axis"

    return {
        "left": _selector_from_stance_prefix(_stance_prefix(target_left, left_base), left_base),
        "right": _selector_from_stance_prefix(_stance_prefix(target_right, right_base), right_base),
        "context": context,
        "beforeEnd": before_end,
        "junctionEnd": junction_end,
        "source": source,
        "origin": table_name,
        "isolated": False,
        "_key": key,
    }


def _index_corpus_by_kind(
    sequences: list[str],
) -> tuple[dict[tuple, tuple[str, int, int, str, str]], set[tuple]]:
    """Index the corpus by ``(leftBase, leftKind, rightBase, rightKind)`` so the alt-axis pass does not rescan it for every combination.

    Each signature maps to its first adjacency with contiguous input ranges, as ``(context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)``. The returned set holds every signature seen at least once without such ranges.
    """
    index: dict[tuple, tuple[str, int, int, str, str]] = {}
    ambiguous: set[tuple] = set()
    for context in sequences:
        names, clusters = _shape_clusters(context)
        text_len = len(context)
        for i in range(len(names) - 1):
            left_glyph, right_glyph = names[i], names[i + 1]
            signature = (
                _base_name(left_glyph),
                _glyph_kind(left_glyph),
                _base_name(right_glyph),
                _glyph_kind(right_glyph),
            )
            if signature in index:
                continue
            left_start, left_end = _cluster_input_range(clusters, i, text_len)
            right_start, right_end = _cluster_input_range(clusters, i + 1, text_len)
            if left_start >= left_end or right_start >= right_end or left_end != right_start:
                ambiguous.add(signature)
                continue
            index[signature] = (context, left_start, right_end, left_glyph, right_glyph)
    return index, ambiguous


def _alt_axis_junctions(
    sequences: list[str],
) -> tuple[list[dict], dict[str, set[tuple[str, str]]]]:
    """Enumerate the kind combinations for every family pair where at least one side has an enabled alternate kind, and return the records for those some context produces.

    Also returns, per pair key, the set of ``(leftKind, rightKind)`` combinations emitted other than the isolated one. ``build`` uses its keys to skip table-derived junctions on the same pairs. A pair's isolated combination is emitted (with ``isolated: true``) only when the pair has at least one other combination, so site/kerning.html can store the pair's cell value on that quadrant.
    """
    alt_kinds = _family_alt_kinds()
    families = _plain_families_by_codepoint()
    corpus_index, corpus_ambiguous = _index_corpus_by_kind(sequences)

    records: list[dict] = []
    signatures: dict[str, set[tuple[str, str]]] = defaultdict(set)

    def selector(family: str, kind: str) -> dict:
        if kind == "plain":
            return _selector_plain(family, alt_kinds.get(family, set()))
        return _selector_alt_kind(family, kind)

    def variant_kinds(family: str) -> list[str]:
        return ["plain"] + [kind for kind in ALT_AXIS_KINDS if kind in alt_kinds.get(family, set())]

    def find(left_family: str, left_kind: str, right_family: str, right_kind: str):
        signature = (left_family, left_kind, right_family, right_kind)
        if signature in corpus_index:
            return ("corpus", *corpus_index[signature])
        accept = (
            lambda left, right: _base_name(left) == left_family
            and _glyph_kind(left) == left_kind
            and _base_name(right) == right_family
            and _glyph_kind(right) == right_kind
        )
        result = _find_context(_synthetic_contexts(left_family, right_family), accept)
        if isinstance(result, tuple):
            return ("synthetic", *result)
        if isinstance(result, _ClusterAmbiguous) or signature in corpus_ambiguous:
            return _ClusterAmbiguous()
        return _NoContext()

    for left_family in families:
        for right_family in families:
            if not (alt_kinds.get(left_family) or alt_kinds.get(right_family)):
                continue
            left_char = _family_char(left_family)
            right_char = _family_char(right_family)
            if left_char is None or right_char is None:
                continue
            isolated_names, _ = _shape_clusters(left_char + right_char)
            isolated_combo = None
            for i in range(len(isolated_names) - 1):
                if (
                    _base_name(isolated_names[i]) == left_family
                    and _base_name(isolated_names[i + 1]) == right_family
                ):
                    isolated_combo = (
                        _glyph_kind(isolated_names[i]),
                        _glyph_kind(isolated_names[i + 1]),
                    )
                    break

            key = f"{left_family}|{right_family}"
            hidden_records: list[dict] = []
            isolated_record: dict | None = None
            for left_kind in variant_kinds(left_family):
                for right_kind in variant_kinds(right_family):
                    combo = (left_kind, right_kind)
                    is_isolated = combo == isolated_combo
                    found = find(left_family, left_kind, right_family, right_kind)
                    if not isinstance(found, tuple):
                        continue
                    source, context, before_end, junction_end, _left_glyph, _right_glyph = found
                    record = {
                        "left": selector(left_family, left_kind),
                        "right": selector(right_family, right_kind),
                        "context": context,
                        "beforeEnd": before_end,
                        "junctionEnd": junction_end,
                        "source": source,
                        "origin": "alt-axis",
                        "isolated": is_isolated,
                        "_key": key,
                    }
                    if is_isolated:
                        isolated_record = record
                    else:
                        hidden_records.append(record)
                        signatures[key].add(combo)

            if hidden_records:
                records.extend(hidden_records)
                if isolated_record is not None:
                    records.append(isolated_record)

    return records, signatures


def build(out_path: Path) -> None:
    with QUIKSCRIPT_YAML.open() as f:
        data = yaml.safe_load(f)

    sequences = _harvest_sequences(CORPUS_FILES)

    family_names = set(data.get("glyph_families", {}))
    available_names = frozenset(_compiled_meta().keys())

    def heal(name: str) -> str:
        return heal_glyph_name(name, family_names, available_names)

    junctions: dict[str, list[dict]] = {}
    skipped: list[dict] = []
    seen_per_key: dict[str, set[tuple]] = {}

    def emit(record: dict) -> None:
        key = record.pop("_key")
        dedupe_key = (
            _selector_dedupe_key(record["left"]),
            _selector_dedupe_key(record["right"]),
            record["context"],
        )
        if dedupe_key in seen_per_key.setdefault(key, set()):
            return
        seen_per_key[key].add(dedupe_key)
        junctions.setdefault(key, []).append(record)

    def context_sources_for(target_left: str, target_right: str) -> list[tuple[str, list[str]]]:
        synthetic = _synthetic_contexts(_base_name(target_left), _base_name(target_right))
        return [("corpus", sequences), ("synthetic", synthetic)]

    # The alt-axis pass runs first because the table passes skip every pair it emitted.
    alt_records, alt_signatures = _alt_axis_junctions(sequences)
    alt_owned_pairs = set(alt_signatures)
    for record in alt_records:
        emit(record)

    demote_tables = {
        "predecessor_demote": data.get("predecessor_demote_overrides", []),
        "trailing_demote": data.get("trailing_demote_overrides", []),
    }
    for table_name, entries in demote_tables.items():
        for entry in entries:
            raw_left, raw_right = _junction_targets(table_name, entry)
            # build_font.py heals these hand-written names before compiling them, so match the healed names.
            target_left, target_right = heal(raw_left), heal(raw_right)
            outcome = _resolve_record(
                target_left,
                target_right,
                table_name,
                context_sources_for(target_left, target_right),
                alt_owned_pairs,
            )
            if isinstance(outcome, str):
                skipped.append({"table": table_name, "entry": entry, "reason": outcome})
            else:
                emit(outcome)

    # A restore_isolated_form entry's three letters form two junctions, and each is resolved separately.
    for entry in data.get("restore_isolated_form_overrides", []):
        prior, target, follower = entry["prior"], entry["target"], entry["follower"]
        prior_char = _family_char(prior)
        target_char = _family_char(target)
        follower_char = _family_char(follower)
        if prior_char is None or target_char is None or follower_char is None:
            skipped.append({"table": "restore_isolated_form", "entry": entry, "reason": "no_context"})
            continue
        literal = prior_char + target_char + follower_char
        literal_names, _ = _shape_clusters(literal)
        if len(literal_names) < 3:
            skipped.append({"table": "restore_isolated_form", "entry": entry, "reason": "no_context"})
            continue

        for left_glyph, right_glyph in (
            (literal_names[0], literal_names[1]),
            (literal_names[1], literal_names[2]),
        ):
            # The shaped glyph names are already current stance names, so they serve as the targets. The literal context is tried before the corpus and synthetic ones.
            context_sources = [("literal", [literal])] + context_sources_for(left_glyph, right_glyph)
            outcome = _resolve_record(
                left_glyph,
                right_glyph,
                "restore_isolated_form",
                context_sources,
                alt_owned_pairs,
            )
            if isinstance(outcome, str):
                skipped.append({"table": "restore_isolated_form", "entry": entry, "reason": outcome})
            else:
                emit(outcome)

    output: dict = {key: junctions[key] for key in sorted(junctions)}
    output["_skipped"] = skipped

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")

    n_junctions = sum(len(v) for v in junctions.values())
    reason_counts: dict[str, int] = {}
    for item in skipped:
        reason_counts[item["reason"]] = reason_counts.get(item["reason"], 0) + 1
    breakdown = ", ".join(f"{r}={c}" for r, c in sorted(reason_counts.items()))
    print(
        f"{n_junctions} junctions across {len(junctions)} pairs, {len(skipped)} skipped ({breakdown})",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=SITE_DIR / "kerning-hardcases.json",
        help="Output JSON path (default: site/kerning-hardcases.json)",
    )
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()
