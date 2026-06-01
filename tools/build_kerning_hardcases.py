"""Enumerate Quikscript "hard-case" form-junctions and emit JSON for test/kerning.html.

The kerning matrix in ``test/kerning.html`` shows only the isolated two-letter shaping of each family pair. Some form-to-form junctions can never appear that way: e.g. ·No·Utter shapes to ``qsNo.alt`` + ``qsUtter.alt`` in isolation, but in any real context where ·Utter takes its ``.alt`` form, ·No demotes back to plain ``qsNo``. Those hidden junctions live in the ``predecessor_demote_overrides`` and ``trailing_demote_overrides`` tables in ``glyph_data/quikscript.yaml``.

This generator walks both demote tables, derives the adjacent rendered glyph pair each override is really about, finds a real corpus context that produces that pair, and records the dimming offsets the web page needs to highlight just the junction.

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
) -> tuple[str, int, int] | _NoContext | _ClusterAmbiguous:
    """Return ``(context, beforeEnd, junctionEnd)`` for the first corpus run whose adjacent shaped pair is exactly ``(left, right)``, or ``_NoContext`` if no run produces the pair, or ``_ClusterAmbiguous`` if a producing run's junction can't be carved into two disjoint contiguous input ranges with a clean remainder."""
    found_pair_but_ambiguous = False
    for context in sequences:
        names, clusters = _shape_clusters(context)
        for i in range(len(names) - 1):
            if names[i] == left and names[i + 1] == right:
                text_len = len(context)
                left_start, left_end = _cluster_input_range(clusters, i, text_len)
                right_start, right_end = _cluster_input_range(clusters, i + 1, text_len)
                if left_start >= left_end or right_start >= right_end:
                    found_pair_but_ambiguous = True
                    continue
                if left_end != right_start:
                    found_pair_but_ambiguous = True
                    continue
                return context, left_start, right_end
    if found_pair_but_ambiguous:
        return _ClusterAmbiguous()
    return _NoContext()


def _verify(context: str, left: str, right: str, before_end: int, junction_end: int) -> bool:
    names, clusters = _shape_clusters(context)
    text_len = len(context)
    for i in range(len(names) - 1):
        if names[i] == left and names[i + 1] == right:
            left_start, left_end = _cluster_input_range(clusters, i, text_len)
            right_start, right_end = _cluster_input_range(clusters, i + 1, text_len)
            if left_start == before_end and left_end == right_start and right_end == junction_end:
                return True
    return False


def _junction_targets(table_name: str, entry: dict) -> tuple[str, str]:
    """The adjacent rendered glyph pair ``(left, right)`` an override entry is really about, named by the entry's raw (pre-heal) form strings."""
    if table_name == "predecessor_demote":
        return entry["isolated_form"], entry["trigger_form"]
    return entry["leader_form"], entry["isolated_form"]


def build(out_path: Path) -> None:
    with QUIKSCRIPT_YAML.open() as f:
        data = yaml.safe_load(f)

    tables = {
        "predecessor_demote": data.get("predecessor_demote_overrides", []),
        "trailing_demote": data.get("trailing_demote_overrides", []),
    }

    sequences = _harvest_sequences(CORPUS_FILES)

    family_names = set(data.get("glyph_families", {}))
    available_names = frozenset(_compiled_meta().keys())

    def heal(name: str) -> str:
        return heal_glyph_name(name, family_names, available_names)

    junctions: dict[str, list[dict]] = {}
    skipped: list[dict] = []
    seen_per_key: dict[str, set[tuple]] = {}

    for table_name, entries in tables.items():
        for entry in entries:
            raw_left, raw_right = _junction_targets(table_name, entry)
            # The build heals these author-written strings against the post-synthesis glyph set before they hit the font, so match the healed names against real shaped output.
            target_left, target_right = heal(raw_left), heal(raw_right)

            reason = _skip_reason(target_left, target_right)
            if reason is not None:
                skipped.append({"table": table_name, "entry": entry, "reason": reason})
                continue

            result = _find_context(sequences, target_left, target_right)
            if isinstance(result, _NoContext):
                skipped.append({"table": table_name, "entry": entry, "reason": "no_context"})
                continue
            if isinstance(result, _ClusterAmbiguous):
                skipped.append({"table": table_name, "entry": entry, "reason": "cluster_ambiguous"})
                continue

            context, before_end, junction_end = result

            shaped_names, _ = _shape_clusters(context)
            left_glyph = right_glyph = None
            for i in range(len(shaped_names) - 1):
                if shaped_names[i] == target_left and shaped_names[i + 1] == target_right:
                    left_glyph, right_glyph = shaped_names[i], shaped_names[i + 1]
                    break
            assert left_glyph is not None and right_glyph is not None

            if not _verify(context, left_glyph, right_glyph, before_end, junction_end):
                print(
                    f"self-check failed for {table_name} {left_glyph!r}+{right_glyph!r} in {context!r}; refusing to emit",
                    file=sys.stderr,
                )
                continue

            left_base = _base_name(left_glyph)
            right_base = _base_name(right_glyph)
            key = f"{left_base}|{right_base}"

            record = {
                "leftForm": _form_prefix(left_glyph, left_base),
                "rightForm": _form_prefix(right_glyph, right_base),
                "context": context,
                "beforeEnd": before_end,
                "junctionEnd": junction_end,
                "source": "corpus",
                "table": table_name,
            }

            dedupe_key = (
                record["leftForm"],
                record["rightForm"],
                record["context"],
                record["beforeEnd"],
                record["junctionEnd"],
                record["table"],
            )
            if dedupe_key in seen_per_key.setdefault(key, set()):
                continue
            seen_per_key[key].add(dedupe_key)
            junctions.setdefault(key, []).append(record)

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
