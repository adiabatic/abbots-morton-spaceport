"""Probe codepoint windows: old-font baseline (glyphs+seams, all configs) vs new settlement.
Usage: uv run python rebuild/tools/probe.py E653:E666:E652 [E652:E67A ...] [--no-baseline]

Every argument is a window, and every window rides one `explain_many` call across the acceptance configurations, so a before/after battery pays the explainer's warm-up once per process. The output is one `=== window X ===` block per argument in argument order, each block identical to what the same window prints alone. `--no-baseline` skips the baseline tables and marks their two lines as not read.
"""

import gzip
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rebuild.pipeline.explain import explain_many
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.run_m1 import OUT_DIR
from rebuild.pipeline.spec_load import load_default_spec

CONFIGS = ["default", "ss03", "ss05", "ss03+ss05", "ss04", "ss10"]
USAGE = "usage: uv run python rebuild/tools/probe.py [--no-baseline] E6XX:E6XX [E6XX:E6XX:E6XX ...]"
BASELINE_NOT_READ = "(baseline not read)"
NOT_IN_SUBSET = "(not in subset)"


def _scan_rows(lines: Iterable[str], wanted: frozenset[str]) -> dict[str, list[str]]:
    """Walk table lines until every wanted key has a row, skipping header and short lines the way the whole-table read does, and stop there."""
    found: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        if parts[0] in wanted and parts[0] not in found:
            found[parts[0]] = parts
            if len(found) == len(wanted):
                break
    return found


def baseline_rows(cfg: str, windows: Iterable[str]) -> dict[str, list[str]]:
    """The subset-table rows for the wanted windows under one configuration, keyed by window. `rebuild/pipeline/baseline_subset.filter_table` writes the table in the canonical (length, codepoints) order of `rebuild/baseline/alphabet.enumerate_basis`, so a two- or three-codepoint window sits near the front and the scan returns after a few thousand lines; a key that sorts late among the four-codepoint rows, or one absent from the subset, costs one full pass. `enumerate_basis` is a Cartesian product over distinct tuples, one row per window, so the first row found for a key is the only one and this agrees with a whole-table dict. An empty wanted set returns an empty answer without opening a table."""
    wanted = frozenset(windows)
    if not wanted:
        return {}
    p = OUT_DIR / f"baseline-{cfg}.subset.tsv.gz"
    with gzip.open(p, "rt") as f:
        return _scan_rows(f, wanted)


def parse_window(entry: str) -> list[int] | None:
    """The codepoints of a colon-joined hex window, or None for an entry that is not one."""
    fields = entry.split(":")
    try:
        return [int(x, 16) for x in fields] if all(fields) else None
    except ValueError:
        return None


def render_window(spec, window_key: str, reports, baselines: dict[str, dict[str, list[str]] | None]) -> None:
    """Print one window's block: the header, then per configuration the baseline glyphs and seams and the settled cells and seams. `baselines[cfg]` is None when the baseline was not read."""
    print(f"=== window {window_key} ===")
    for cfg, report in zip(CONFIGS, reports):
        sub = baselines[cfg]
        if sub is None:
            bg, bs = BASELINE_NOT_READ, ""
        else:
            b = sub.get(window_key)
            bg = b[1] if b else NOT_IN_SUBSET
            bs = b[3] if b else ""
        settled = report.settled
        cells = []
        seams = []
        for i, it in enumerate(settled):
            c = getattr(it, "cell", None)
            if c is not None and hasattr(c, "rune"):
                cells.append(f"{c.rune}.{c.stance}/en={c.entry}/ex={c.exit}/{'+'.join(c.adjustments)}")
            else:
                cells.append(getattr(it, "glyph_name", str(it)))
            if i < len(settled) - 1:
                sm = getattr(it, "seam", None)
                seams.append(
                    "break"
                    if sm is None
                    else (f"y{sm}" if isinstance(sm, int) else f"y{spec.registry.y_of(sm)}")
                )
        print(f"\n[{cfg}]")
        print(f"  OLD glyphs: {bg}")
        print(f"  OLD seams : {bs}")
        print(f"  NEW cells : {' | '.join(cells)}")
        print(f"  NEW seams : {','.join(seams)}")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    read_baseline = "--no-baseline" not in arguments
    entries = [a.upper() for a in arguments if a != "--no-baseline"]
    windows: list[tuple[str, list[int]]] = []
    for entry in entries:
        codepoints = parse_window(entry)
        if codepoints is None:
            print(f"probe: not a codepoint window: {entry}", file=sys.stderr)
            print(USAGE, file=sys.stderr)
            sys.exit(2)
        windows.append((entry, codepoints))
    if not windows:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    spec = load_default_spec()
    features = [features_for_config(config) for config in CONFIGS]
    reports = explain_many(spec, [(cps, active) for _key, cps in windows for active in features])
    keys = frozenset(key for key, _cps in windows)
    baselines: dict[str, dict[str, list[str]] | None] = {
        cfg: baseline_rows(cfg, keys) if read_baseline else None for cfg in CONFIGS
    }
    for index, (key, _cps) in enumerate(windows):
        start = index * len(CONFIGS)
        render_window(spec, key, reports[start : start + len(CONFIGS)], baselines)


if __name__ == "__main__":
    main()
