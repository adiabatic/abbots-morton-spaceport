"""Enumerate Quikscript "hard-case" form-junctions and emit JSON for test/kerning.html.

The kerning matrix in ``test/kerning.html`` shows only the isolated two-letter shaping of each family pair. Some form-to-form junctions can never appear that way: e.g. ·No·Utter shapes to ``qsNo.alt`` + ``qsUtter.alt`` in isolation, but in any real context where ·Utter takes its ``.alt`` form, ·No demotes back to plain ``qsNo``. Those hidden junctions live in the ``predecessor_demote_overrides``, ``trailing_demote_overrides``, and ``restore_isolated_form_overrides`` tables in ``glyph_data/quikscript.yaml``.

This generator walks those tables, derives the adjacent rendered glyph pair each override is really about, and emits only the junctions that are genuinely *hidden* — i.e. that you can't reproduce by typing the two base families as bare letters (the isolated two-letter rendering). For each surviving junction it looks for a context that reproduces it (under relaxed prefix matching), preferring a literal/corpus context and falling back to a bounded, deterministic synthetic search, then records the dimming offsets the web page needs to highlight just the junction.

Run (after ``make all``)::

    uv run python tools/build_kerning_hardcases.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
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
    TEST_DIR / "the-manual.html",
    TEST_DIR / "index.html",
    TEST_DIR / "extra-senior-words.html",
)
QS_FIRST = 0xE650
QS_LAST = 0xE67F
QS_RUN_RE = re.compile("[\ue650-\ue67f\u200c]+")
ENTITY_HEX_RE = re.compile(r"&#x([0-9A-Fa-f]+);")
ENTITY_DEC_RE = re.compile(r"&#(\d+);")

ENTRYLESS_MARKERS = (".noentry", ".ex-noentry", ".nonjoining-left")


def _plain_families_by_codepoint() -> list[str]:
    """Plain Quikscript family names (no ligatures, no variant forms, no angle parens), in code-point order."""
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
    """The single code point a plain Quikscript family maps to, or ``None`` for anything not in the char map."""
    return _char_map().get(family)


def _prefix_match(glyph_name: str, target: str) -> bool:
    """``glyph_name`` reproduces ``target``.

    A bare-family ``target`` (no ``.`` in the name) requires an *exact* match: the demote tables' bare ``isolated_form`` means exactly that bare glyph, so a context that renders a sibling contextual form (``qsJai.en-y5.ex-y0`` for target ``qsJai``) is a *different* junction and must not be accepted. A dotted-form ``target`` (e.g. ``qsGay.ex-y0``) prefix-matches, tolerating deeper exit/entry modifiers (``qsGay.ex-y0.ex-ext-1``).
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
    """Shape ``text`` and return parallel lists of full glyph names and per-glyph ``cluster`` values."""
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


def _form_prefix(glyph_name: str, base: str) -> str | None:
    """The override prefix the web page needs: ``None`` when the shaped glyph is the bare base family, otherwise the full shaped glyph name."""
    return None if glyph_name == base else glyph_name


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
    sequences: list[str], left: str, right: str
) -> tuple[str, int, int, str, str] | _NoContext | _ClusterAmbiguous:
    """Return ``(context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)`` for the first run whose adjacent shaped pair prefix-matches ``(left, right)`` (per :func:`_prefix_match`), or ``_NoContext`` if no run produces the pair, or ``_ClusterAmbiguous`` if a producing run's junction can't be carved into two disjoint contiguous input ranges with a clean remainder.

    ``leftGlyph`` / ``rightGlyph`` are the actual rendered glyph names (which may carry extra exit/entry modifiers beyond the prefix targets).
    """
    found_pair_but_ambiguous = False
    for context in sequences:
        names, clusters = _shape_clusters(context)
        for i in range(len(names) - 1):
            if _prefix_match(names[i], left) and _prefix_match(names[i + 1], right):
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


def _verify(context: str, left: str, right: str, before_end: int, junction_end: int) -> bool:
    names, clusters = _shape_clusters(context)
    text_len = len(context)
    for i in range(len(names) - 1):
        if _prefix_match(names[i], left) and _prefix_match(names[i + 1], right):
            left_start, left_end = _cluster_input_range(clusters, i, text_len)
            right_start, right_end = _cluster_input_range(clusters, i + 1, text_len)
            if left_start == before_end and left_end == right_start and right_end == junction_end:
                return True
    return False


def _is_hidden(left: str, right: str) -> bool:
    """A junction is *hidden* when shaping the two base families as bare letters does not already reproduce it.

    Returns ``True`` (worth emitting) unless the isolated two-letter rendering of ``char(leftBase) + char(rightBase)`` yields an adjacent pair that prefix-matches ``(left, right)``.
    """
    left_char = _family_char(_base_name(left))
    right_char = _family_char(_base_name(right))
    if left_char is None or right_char is None:
        # Ligatures and other non-plain bases have no two-letter isolated rendering; treat as hidden.
        return True
    names, _ = _shape_clusters(left_char + right_char)
    for i in range(len(names) - 1):
        if _prefix_match(names[i], left) and _prefix_match(names[i + 1], right):
            return False
    return True


def _synthetic_contexts(left_base: str, right_base: str) -> list[str]:
    """Bounded, deterministic search space of candidate context strings for a target whose base families are ``(left_base, right_base)``.

    Ordered shortest-first, then by the documented family-position sweep, capped at four glyphs. ``None`` bases (ligatures etc.) yield no candidates.
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
    """The adjacent rendered glyph pair ``(left, right)`` a demote-table override entry is really about, named by the entry's raw (pre-heal) form strings."""
    if table_name == "predecessor_demote":
        return entry["isolated_form"], entry["trigger_form"]
    return entry["leader_form"], entry["isolated_form"]


def _resolve_record(
    target_left: str,
    target_right: str,
    table_name: str,
    context_sources: list[tuple[str, list[str]]],
) -> dict | str:
    """Run a target junction ``(target_left, target_right)`` through the full pipeline against each ``(source, candidate_contexts)`` group in order. Return the emit-ready record (sans dedupe handling) or a skip-reason string.

    The hidden filter (#1) is applied once up front; context groups are tried in the given order, with ``_ClusterAmbiguous`` from one group not blocking later groups.
    """
    skip = _skip_reason(target_left, target_right)
    if skip is not None:
        return skip

    if not _is_hidden(target_left, target_right):
        return "not_hidden"

    saw_ambiguous = False
    for source, candidates in context_sources:
        result = _find_context(candidates, target_left, target_right)
        if isinstance(result, _NoContext):
            continue
        if isinstance(result, _ClusterAmbiguous):
            saw_ambiguous = True
            continue

        context, before_end, junction_end, left_glyph, right_glyph = result
        if not _verify(context, left_glyph, right_glyph, before_end, junction_end):
            print(
                f"self-check failed for {table_name} {left_glyph!r}+{right_glyph!r} in {context!r}; refusing to emit",
                file=sys.stderr,
            )
            continue

        left_base = _base_name(left_glyph)
        right_base = _base_name(right_glyph)
        return {
            "leftForm": _form_prefix(target_left, left_base),
            "rightForm": _form_prefix(target_right, right_base),
            "context": context,
            "beforeEnd": before_end,
            "junctionEnd": junction_end,
            "source": source,
            "table": table_name,
            "_key": f"{left_base}|{right_base}",
        }

    return "cluster_ambiguous" if saw_ambiguous else "no_context"


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
            record["leftForm"],
            record["rightForm"],
            record["context"],
        )
        if dedupe_key in seen_per_key.setdefault(key, set()):
            return
        seen_per_key[key].add(dedupe_key)
        junctions.setdefault(key, []).append(record)

    def context_sources_for(target_left: str, target_right: str) -> list[tuple[str, list[str]]]:
        synthetic = _synthetic_contexts(_base_name(target_left), _base_name(target_right))
        return [("corpus", sequences), ("synthetic", synthetic)]

    # Demote tables: one target junction per entry.
    demote_tables = {
        "predecessor_demote": data.get("predecessor_demote_overrides", []),
        "trailing_demote": data.get("trailing_demote_overrides", []),
    }
    for table_name, entries in demote_tables.items():
        for entry in entries:
            raw_left, raw_right = _junction_targets(table_name, entry)
            # The build heals these author-written strings against the post-synthesis glyph set before they hit the font, so match the healed names against real shaped output.
            target_left, target_right = heal(raw_left), heal(raw_right)
            outcome = _resolve_record(
                target_left,
                target_right,
                table_name,
                context_sources_for(target_left, target_right),
            )
            if isinstance(outcome, str):
                skipped.append({"table": table_name, "entry": entry, "reason": outcome})
            else:
                emit(outcome)

    # restore_isolated_form: the literal 3-codepoint context yields two adjacent junctions, both run through the pipeline.
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
            # The literal output glyphs are already the healed, fully rendered forms, so they double as the target prefixes; the literal context is tried first, then corpus, then synthetic.
            context_sources = [("literal", [literal])] + context_sources_for(left_glyph, right_glyph)
            outcome = _resolve_record(
                left_glyph,
                right_glyph,
                "restore_isolated_form",
                context_sources,
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
        default=TEST_DIR / "kerning-hardcases.json",
        help="Output JSON path (default: test/kerning-hardcases.json)",
    )
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()
