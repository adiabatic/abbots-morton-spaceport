"""Print codepoint windows' old-font baseline (glyphs and seams, every configuration) beside the rebuild's settlement.
Usage: uv run python rebuild/tools/probe.py E653:E666:E652 [E652:E67A ...] [--no-baseline]

Every argument is a window. All windows go through one `explain_many` call across the acceptance configurations, so the explainer's warm-up is paid once per process. The output is one `=== window X ===` block per argument, in argument order, and each block is what that window prints when probed alone. `--no-baseline` skips reading the baseline tables and omits each configuration's two OLD lines.
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
NOT_IN_SUBSET = "(not in subset)"


def _scan_rows(lines: Iterable[str], wanted: frozenset[str]) -> dict[str, list[str]]:
    """Return the first row for each wanted key, skipping comment lines and lines with fewer than four fields, and stop reading once every key is found."""
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
    """Return the subset-table rows for the wanted windows under one configuration, keyed by window. `rebuild/pipeline/baseline_subset.filter_table` keeps the baseline table's row order, which is the (length, codepoints) order of `rebuild/baseline/alphabet.enumerate_basis`. A two- or three-codepoint window is therefore near the front and the scan stops early, while a key late among the four-codepoint rows, or one missing from the subset, costs a full pass. `enumerate_basis` yields each window once, so the first row found for a key is the only one. An empty wanted set returns {} without opening a table."""
    wanted = frozenset(windows)
    if not wanted:
        return {}
    p = OUT_DIR / f"baseline-{cfg}.subset.tsv.gz"
    with gzip.open(p, "rt") as f:
        return _scan_rows(f, wanted)


def parse_window(entry: str) -> list[int] | None:
    """Return the codepoints of a colon-joined hex window, or None for an entry that is not one."""
    fields = entry.split(":")
    try:
        return [int(x, 16) for x in fields] if all(fields) else None
    except ValueError:
        return None


def render_window(spec, window_key: str, reports, baselines: dict[str, dict[str, list[str]] | None]) -> None:
    """Print one window's block: the header, then per configuration the baseline glyphs and seams and the settled cells and seams. `baselines[cfg]` is None when the baseline was not read, and the block then carries no OLD lines."""
    print(f"=== window {window_key} ===")
    for cfg, report in zip(CONFIGS, reports):
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
        sub = baselines[cfg]
        if sub is not None:
            b = sub.get(window_key)
            print(f"  OLD glyphs: {b[1] if b else NOT_IN_SUBSET}")
            print(f"  OLD seams : {b[3] if b else ''}")
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
