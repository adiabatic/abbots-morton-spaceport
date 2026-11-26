"""Print the rebuild's settlement of codepoint windows in every configuration: each window's settled cells and the junctions between them.
Usage: uv run python rebuild/tools/probe.py E653:E666:E652 [E652:E67A ...]

Every argument is a window. All windows go through one `explain_many` call across the acceptance configurations, so the explainer's warm-up is paid once per process. The output is one `=== window X ===` block per argument, in argument order, and each block is what that window prints when probed alone.
"""

import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rebuild.pipeline.explain import explain_many
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.spec_load import load_default_spec

CONFIGS = ["default", "ss03", "ss05", "ss03+ss05", "ss04", "ss07", "ss08", "ss10"]
USAGE = "usage: uv run python rebuild/tools/probe.py E6XX:E6XX [E6XX:E6XX:E6XX ...]"


def parse_window(entry: str) -> list[int] | None:
    """Return the codepoints of a colon-joined hex window, or None for an entry that is not one."""
    fields = entry.split(":")
    try:
        return [int(x, 16) for x in fields] if all(fields) else None
    except ValueError:
        return None


def render_window(spec, window_key: str, reports) -> None:
    """Print one window's block: the header, then per configuration the settled cells and junctions."""
    print(f"=== window {window_key} ===")
    for cfg, report in zip(CONFIGS, reports):
        settled = report.settled
        cells = []
        junctions = []
        for i, it in enumerate(settled):
            c = getattr(it, "cell", None)
            if c is not None and hasattr(c, "rune"):
                cells.append(f"{c.rune}.{c.stance}/en={c.entry}/ex={c.exit}/{'+'.join(c.adjustments)}")
            else:
                cells.append(getattr(it, "glyph_name", str(it)))
            if i < len(settled) - 1:
                sm = getattr(it, "junction", None)
                junctions.append(
                    "break"
                    if sm is None
                    else (f"y{sm}" if isinstance(sm, int) else f"y{spec.registry.y_of(sm)}")
                )
        print(f"\n[{cfg}]")
        print(f"  cells : {' | '.join(cells)}")
        print(f"  junctions : {','.join(junctions)}")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    entries = [a.upper() for a in arguments]
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
    for index, (key, _cps) in enumerate(windows):
        start = index * len(CONFIGS)
        render_window(spec, key, reports[start : start + len(CONFIGS)])


if __name__ == "__main__":
    main()
