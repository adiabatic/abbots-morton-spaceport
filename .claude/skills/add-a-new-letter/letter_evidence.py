"""Old-font evidence for one letter's migration, read straight off the full baseline tables under rebuild/out/.

One pass over every baseline-<config>.tsv.gz collects, for the named letter: the pair-level join map in both directions (the definitive scope evidence for the rune file's entry `from:` and exit `toward:` lists — never scope those from FEA reconnaissance, which misses bare-carrier joins); the old compiled forms the letter takes across the rows that will join the oracle subset (the contextual-stance worklist, `.noentry` twins included); the alias worklist (names in those rows with no rebuild/m1-aliases.yaml entry yet — the same list run_m1's completeness gate would print, available before the first build); and the default-config subset growth, ending in the row count rebuild/test_review_enrich.py must pin after the migration.

"Would-be subset rows" are the rows whose codepoints all sit in M1_ALPHABET plus the named letter, which is exactly the set the letter's migration adds to rebuild/out/m1/baseline-*.subset.tsv.gz. Partners marked with * are not yet in M1_ALPHABET: a join against one is deferred-partner evidence — legal to record in a `from:`/`toward:` list, but re-verify it when that partner migrates.

Usage, from the repo root (a scan of all eleven tables takes on the order of a minute):

    uv run python .claude/skills/add-a-new-letter/letter_evidence.py ·Ooze

The letter may be spelled ·Ooze, Ooze, qsOoze, or E67E.
"""

from __future__ import annotations

import gzip
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from rebuild.pipeline.baseline_subset import M1_ALPHABET  # noqa: E402

BASELINE_DIR = REPO_ROOT / "rebuild" / "out"
SUBSET_DEFAULT = BASELINE_DIR / "m1" / "baseline-default.subset.tsv.gz"
ALIASES_PATH = REPO_ROOT / "rebuild" / "m1-aliases.yaml"
NAMES_DOC = REPO_ROOT / "doc" / "glyph-names.md"
BOUNDARY_LABELS = {0x0020: "space", 0x00B7: "namer-dot", 0x200C: "ZWNJ"}

NAME_ROW = re.compile(r"^\|\s*·(\S+)\s*\|\s*U\+([0-9A-Fa-f]{4})\s*\|\s*(qs\w+)\s*\|")


def load_name_table() -> list[tuple[str, int, str]]:
    rows = []
    for line in NAMES_DOC.read_text(encoding="utf-8").splitlines():
        match = NAME_ROW.match(line)
        if match:
            rows.append((match.group(1), int(match.group(2), 16), match.group(3)))
    if not rows:
        sys.exit(f"could not parse the letter table out of {NAMES_DOC}")
    return rows


def resolve_letter(argument: str, table: list[tuple[str, int, str]]) -> tuple[str, int, str]:
    hex_match = re.fullmatch(r"(?:0x|U\+)?([0-9A-Fa-f]{4,6})", argument.strip())
    if hex_match:
        codepoint = int(hex_match.group(1), 16)
        for row in table:
            if row[1] == codepoint:
                return row
        sys.exit(f"{argument} is not a codepoint in {NAMES_DOC}")
    wanted = argument.strip().lstrip("·").replace("'", "’").lower()
    for row in table:
        if wanted in (row[0].lower(), row[2].lower(), row[2][2:].lower()):
            return row
    sys.exit(f"no letter named {argument!r} in {NAMES_DOC}")


def display(codepoint_hex: str, table: dict[int, str]) -> str:
    codepoint = int(codepoint_hex, 16)
    if codepoint in BOUNDARY_LABELS:
        return BOUNDARY_LABELS[codepoint]
    name = table.get(codepoint, f"U+{codepoint_hex}")
    return name if codepoint in M1_ALPHABET else name + "*"


def alias_keys() -> set[str]:
    keys = set()
    for line in ALIASES_PATH.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([^\s:#][^:\s]*):", line)
        if match:
            keys.add(match.group(1))
    return keys


def subset_default_rows(hexcp: str) -> tuple[int, int] | None:
    """(total non-comment rows, rows already involving hexcp). The second number is nonzero when a stale artifact from an earlier alphabet experiment already carries the letter — without subtracting it, the growth prediction would double-count."""
    if not SUBSET_DEFAULT.exists():
        return None
    count = 0
    already = 0
    with gzip.open(SUBSET_DEFAULT, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            count += 1
            if hexcp in line.split("\t", 1)[0].split(":"):
                already += 1
    return count, already


def scan(hexcp: str, alphabet_hex: frozenset[str]) -> tuple[
    dict[tuple[str, str], dict[str, tuple[str, str]]],
    dict[str, tuple[int, str, str]],
    set[str],
    dict[int, int],
    list[str],
]:
    tables = sorted(
        BASELINE_DIR.glob("baseline-*.tsv.gz"), key=lambda p: (p.stem != "baseline-default", p.stem)
    )
    if not tables:
        sys.exit(
            f"no baseline-*.tsv.gz under {BASELINE_DIR} — the baseline extraction has not run on this box"
        )
    pairs: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
    variants: dict[str, tuple[int, str, str]] = {}
    kept_names: set[str] = set()
    kept_default_lengths: dict[int, int] = defaultdict(int)
    all_configs: list[str] = []
    for path in tables:
        config = path.name[len("baseline-") : -len(".tsv.gz")]
        all_configs.append(config)
        print(f"[scan] {config}", file=sys.stderr)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if hexcp not in line or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                codepoints = parts[0].split(":")
                if hexcp not in codepoints:
                    continue
                glyphs = parts[1].split("|")
                if len(codepoints) == 2:
                    if codepoints[0] == hexcp:
                        pairs[("left", codepoints[1])][config] = (parts[3], parts[1])
                    if codepoints[1] == hexcp:
                        pairs[("right", codepoints[0])][config] = (parts[3], parts[1])
                if all(c in alphabet_hex for c in codepoints):
                    if config == "default":
                        kept_default_lengths[len(codepoints)] += 1
                    kept_names.update(glyphs)
                    clusters = parts[2].split(",")
                    for index, codepoint in enumerate(codepoints):
                        if codepoint != hexcp or index >= len(clusters):
                            continue
                        glyph_index = int(clusters[index])
                        if glyph_index >= len(glyphs):
                            continue
                        name = glyphs[glyph_index]
                        count, first_window, first_config = variants.get(name, (0, parts[0], config))
                        variants[name] = (count + 1, first_window, first_config)
    return pairs, variants, kept_names, kept_default_lengths, all_configs


def config_label(configs: list[str], all_configs: list[str]) -> str:
    if len(configs) == len(all_configs):
        return "all configs"
    missing = [c for c in all_configs if c not in configs]
    if len(missing) < len(configs):
        return "all but " + ",".join(missing)
    return ",".join(sorted(configs, key=lambda c: (c != "default", c)))


def print_pair_section(
    side: str,
    heading: str,
    pairs: dict[tuple[str, str], dict[str, tuple[str, str]]],
    names_by_codepoint: dict[int, str],
    all_configs: list[str],
) -> None:
    print(f"\n--- {heading} ---")
    always_break = []
    partners = sorted((p for s, p in pairs if s == side), key=lambda h: int(h, 16))
    for partner in partners:
        per_config = pairs[(side, partner)]
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for config, value in per_config.items():
            groups[value].append(config)
        if len(groups) == 1 and next(iter(groups))[0] == "break":
            always_break.append(display(partner, names_by_codepoint))
            continue
        label = display(partner, names_by_codepoint)
        if len(groups) == 1:
            seam, glyph_field = next(iter(groups))
            print(f"  {label:<18} {seam:<6} all configs      {glyph_field}")
        else:
            print(f"  {label}")
            for (seam, glyph_field), configs in sorted(groups.items(), key=lambda kv: sorted(kv[1])):
                print(f"    {seam:<6} {config_label(configs, all_configs):<24} {glyph_field}")
    if always_break:
        print(f"  breaks in every config against: {', '.join(always_break)}")


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        sys.exit(
            "usage: uv run python .claude/skills/add-a-new-letter/letter_evidence.py <letter>   (·Ooze, Ooze, qsOoze, or E67E)"
        )
    table = load_name_table()
    letter_name, codepoint, qs_name = resolve_letter(arguments[0], table)
    names_by_codepoint = {row[1]: row[2] for row in table}
    hexcp = f"{codepoint:04X}"
    migrated = codepoint in M1_ALPHABET
    alphabet_hex = frozenset(f"{c:04X}" for c in M1_ALPHABET) | {hexcp}
    letter_count = sum(1 for c in M1_ALPHABET if c not in BOUNDARY_LABELS)

    print(f"=== ·{letter_name} — {qs_name}, U+{hexcp} ===")
    membership = "already in M1_ALPHABET" if migrated else "not yet in M1_ALPHABET"
    print(f"{letter_count} letters migrated; {qs_name} is {membership}. Partners marked * are unmigrated.")

    pairs, variants, kept_names, kept_default_lengths, all_configs = scan(hexcp, alphabet_hex)
    if not pairs and not variants:
        sys.exit(f"no baseline rows mention {hexcp} — is this letter in the extraction alphabet?")

    print_pair_section(
        "left",
        f"{qs_name} on the LEFT: its exit side, feeding the exit rows' toward: lists",
        pairs,
        names_by_codepoint,
        all_configs,
    )
    print_pair_section(
        "right",
        f"{qs_name} on the RIGHT: its entry side, feeding the entry rows' from: lists",
        pairs,
        names_by_codepoint,
        all_configs,
    )

    print(f"\n--- old compiled forms {qs_name} takes in would-be subset rows (all configs) ---")
    for name, (count, first_window, first_config) in sorted(variants.items()):
        print(f"  {name:<44} {count:>7} rows   first seen: {first_window} ({first_config})")

    missing = sorted(kept_names - alias_keys())
    print("\n--- alias worklist: subset-row names with no rebuild/m1-aliases.yaml entry ---")
    if missing:
        for name in missing:
            print(f"  {name}")
    else:
        print("  (none — every name these rows use is already aliased)")

    print("\n--- default-config subset growth ---")
    added = sum(kept_default_lengths.values())
    by_length = ", ".join(f"len {k}: {v}" for k, v in sorted(kept_default_lengths.items()))
    counted = subset_default_rows(hexcp)
    print(f"  rows involving {qs_name} that belong in the subset: {added} ({by_length})")
    if counted is None:
        print(
            "  (no baseline-default.subset.tsv.gz on disk yet — run run_m1 or rebuild.pipeline.baseline_subset)"
        )
    elif migrated:
        current, _ = counted
        print(f"  current subset total: {current} — rebuild/test_review_enrich.py pins this number")
    else:
        current, already = counted
        if already:
            print(
                f"  (the on-disk subset already carries {already} rows involving {qs_name} — a stale artifact built with the letter in the alphabet; predicting from the {current - already} rows without it)"
            )
        print(f"  current subset total: {current}; after migration expect {current - already + added}")
        print(
            f"  rebuild/test_review_enrich.py's subset-table row count must become {current - already + added}"
        )


if __name__ == "__main__":
    main()
