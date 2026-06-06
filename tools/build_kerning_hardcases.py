"""Enumerate Quikscript "hard-case" form-junctions and emit JSON for site/kerning.html.

The kerning matrix in ``site/kerning.html`` shows only the isolated two-letter shaping of each family pair. Some form-to-form junctions can never appear that way: e.g. ·No·Utter shapes to ``qsNo.alt`` + ``qsUtter`` in isolation, but the (plain ·No, alt ·Utter) and (alt ·No, alt ·Utter) combinations only ever show up in longer context. This generator finds those hidden junctions two complementary ways and unions them:

1. **Alt-axis cross-product** (the primary source for families with discrete alternates): for every family pair where at least one side has an enabled ``traits: [alt]`` form, enumerate the ``{plain, alt}`` cross-product, drop the combo equal to the isolated two-letter rendering, and keep each remaining combo that some real context realizes. The emitted selector is collapsed to the kerning-relevant axis — ``qsUtter.alt`` (a prefix matching every alt variant), and "plain" as the family minus its ``.alt`` sub-family. ``half`` is wired but disabled (see ``ALT_AXIS_KINDS``).
2. **Demote/restore tables**: the ``predecessor_demote_overrides``, ``trailing_demote_overrides``, and ``restore_isolated_form_overrides`` tables in ``glyph_data/quikscript.yaml`` encode specific contextual corrections (``qsIt.ex-y0.before-day`` and the like) that the alt-axis pass doesn't cover. These are kept for every pair, except where a junction collapses onto a combo the alt-axis pass already owns (``superseded_by_alt_axis``).

For each surviving junction it looks for a context that reproduces it, preferring a literal/corpus context and falling back to a bounded, deterministic synthetic search, then records the dimming offsets the web page needs to highlight just the junction.

Run (after ``make all``)::

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

# The discrete-alternate axes (CLAUDE.md's genuine `traits`) the cross-product pass enumerates. `half` is deliberately disabled this pass — it is more entangled with join geometry (e.g. ·He carries a `shared_kern_entangled` skip) — but the machinery treats it identically, so promoting it is a one-tuple change here.
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
    return None if glyph_name == base else glyph_name


def _glyph_kind(glyph_name: str) -> str:
    """The discrete-alternate kind of a shaped glyph, collapsed to the enabled axes: ``"alt"`` (or ``"half"`` once enabled) when it carries that trait, else ``"plain"``.

    With ``half`` disabled, a half-traited glyph reads as ``"plain"`` — i.e. the whole family is treated as one kind — which is exactly what keeps non-enabled axes out of the cross-product.
    """
    meta = _compiled_meta().get(glyph_name)
    traits = meta.traits if meta is not None else frozenset()
    for kind in ALT_AXIS_KINDS:
        if kind in traits:
            return kind
    return "plain"


def _family_alt_kinds() -> dict[str, set[str]]:
    """Map each plain family to the set of *enabled* discrete-alternate kinds it actually has a form for (e.g. ``qsNo -> {"alt"}``)."""
    result: dict[str, set[str]] = defaultdict(set)
    for name, meta in _compiled_meta().items():
        if "_" in name:
            continue
        for kind in ALT_AXIS_KINDS:
            if kind in meta.traits:
                result[meta.base_name].add(kind)
    return result


def _selector_alt_kind(family: str, kind: str) -> dict:
    """Per-side selector for an alternate kind: a form-prefix (``qsNo.alt``) that the build's prefix-match catches uniformly across that kind's variants."""
    return {"family": family, "kind": kind, "form": f"{family}.{kind}", "except": None}


def _selector_plain(family: str, alt_kinds_present: set[str]) -> dict:
    """Per-side selector for the *plain* kind on an alt-axis pair: the whole family minus its enabled alternate sub-families (``except``), since a bare-family prefix would wrongly catch ``qsNo.alt``."""
    excepts = [f"{family}.{kind}" for kind in ALT_AXIS_KINDS if kind in alt_kinds_present]
    return {"family": family, "kind": "plain", "form": None, "except": excepts or None}


def _selector_from_form_prefix(form: str | None, base: str) -> dict:
    """Per-side selector for a table-derived junction. ``form is None`` is the bare whole family (the old ``leftForm: null`` semantics — no carve-out); a form-prefix is a specific contextual form override."""
    if form is None:
        return {"family": base, "kind": "plain", "form": None, "except": None}
    return {"family": base, "kind": "form", "form": form, "except": None}


def _selector_dedupe_key(selector: dict) -> tuple:
    return (selector["kind"], selector["form"], tuple(selector["except"] or ()))


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
    """Return ``(context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)`` for the first run whose adjacent shaped pair satisfies ``accept(leftGlyph, rightGlyph)``, or ``_NoContext`` if no run produces the pair, or ``_ClusterAmbiguous`` if a producing run's junction can't be carved into two disjoint contiguous input ranges with a clean remainder.

    ``accept`` is the match predicate (prefix-match for the demote tables, base+kind equality for the alt-axis pass). ``leftGlyph`` / ``rightGlyph`` are the actual rendered glyph names.
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


def _resolve_match(
    accept,
    context_sources: list[tuple[str, list[str]]],
    table_name: str,
) -> tuple[str, str, int, int, str, str] | _NoContext | _ClusterAmbiguous:
    """Try ``accept`` against each ``(source, candidate_contexts)`` group in order, self-checking the first clean hit. Return ``(source, context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)`` or a no-context / ambiguous sentinel (``_ClusterAmbiguous`` from one group does not block later groups)."""
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
    """Run a demote/restore target junction ``(target_left, target_right)`` through the full pipeline. Return the emit-ready record (new per-side schema, sans dedupe handling) or a skip-reason string.

    The hidden filter is applied once up front. A surviving junction on a pair the alt-axis pass already partitioned (``alt_owned_pairs``) is dropped as ``superseded_by_alt_axis``: that pass emits a complete ``{plain, alt}`` partition over the whole family×family space (cell + overrides), so any demote-table junction there would overlap a quadrant and break the disjoint-lookup invariant.
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
        "left": _selector_from_form_prefix(_form_prefix(target_left, left_base), left_base),
        "right": _selector_from_form_prefix(_form_prefix(target_right, right_base), right_base),
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
    """Index the corpus once by ``(leftBase, leftKind, rightBase, rightKind)`` so the alt-axis pass is a dictionary lookup rather than a re-scan per combo.

    Each signature maps to the first cleanly-carvable adjacency ``(context, beforeEnd, junctionEnd, leftGlyph, rightGlyph)``. Signatures only ever seen with an ambiguous cluster carve land in the returned ``ambiguous`` set so the caller can still report them.
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
    """Enumerate the discrete-alternate cross-product for every family pair where at least one side has an enabled alternate, keeping the realizable junctions that aren't the isolated two-letter rendering.

    Returns the emit-ready records (new per-side schema, with ``_key``) plus, per pair key, the set of ``(leftKind, rightKind)`` combos emitted — so the demote/restore passes can drop any table-derived junction that collapses onto the same combo. A pair only emits its isolated quadrant (``isolated: true``, not rendered as a row) when it has at least one hidden combo, so the page can carve the family-cell rule down to that residual quadrant.
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

    # Alt-axis cross-product first, so the demote/restore passes can defer to its collapsed selectors on any pair it already owns.
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
            # The build heals these author-written strings against the post-synthesis glyph set before they hit the font, so match the healed names against real shaped output.
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
