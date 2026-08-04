"""The window-grain prospect-divergence inventory (issue #28 stage 0): every settlement window whose deliberately optimistic third join-count term disagrees with the follower's actual settled choice, dumped as a diff-stable TSV per acceptance configuration. This is exactly the comparison `table._flag_prospect_joints` folds into the joint flag — the walk is shared (`table.prospect_successor_index` / `table.prospect_successors`), so the inventory and the flag can never disagree — persisted at row grain instead of collapsed to a bit, because the flip inventory is the before-any-semantics-change record the simulated-prospect stages check their deltas against. Read-only: the tool builds the tables in memory and writes only its own artifact.

Run as: uv run python -m rebuild.tools.prospect_divergence [--jobs N]
"""

from __future__ import annotations

import argparse
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rebuild.pipeline import conform
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.run_m1 import OUT_DIR
from rebuild.pipeline.spec_load import load_default_spec

COLUMNS = (
    "input",
    "left",
    "lookahead1",
    "lookahead2",
    "lookahead3",
    "lookahead4",
    "outcome",
    "prospect",
    "realized",
    "successor_outcome",
)


def divergence_lines(decision: table_module.DecisionTable) -> list[str]:
    """One line per distinct (window, realized seam, successor outcome) where the window's optimistic prospect differs from what the matched successor actually settled — sorted, so the artifact is diff-stable and duplicate projections of several deep-slot successors collapse."""
    rows = [row for row in decision.transitions if isinstance(row, table_module.Transition)]
    if len(rows) != len(decision.transitions):
        raise SystemExit("the divergence inventory needs enumerated rows, not a serialized window")
    index = table_module.prospect_successor_index(rows)
    lines: set[str] = set()
    for row in rows:
        for successor in table_module.prospect_successors(index, row):
            realized = 1 if successor.settled.seam is not None else 0
            if realized == row.prospect:
                continue
            lines.add(
                "\t".join(
                    (
                        row.input_glyph,
                        row.left,
                        row.right1,
                        row.right2,
                        row.right3,
                        row.right4,
                        row.outcome,
                        str(row.prospect),
                        str(realized),
                        successor.outcome,
                    )
                )
            )
    return sorted(lines)


def write_divergences(decision: table_module.DecisionTable, path: Path) -> tuple[int, int]:
    """Write one configuration's inventory and return (divergence line count, distinct window count)."""
    lines = divergence_lines(decision)
    windows = {tuple(line.split("\t")[:6]) for line in lines}
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [f"# prospect divergence, config {decision.config}", "\t".join(COLUMNS)]
    path.write_text("\n".join(header + lines) + "\n")
    return len(lines), len(windows)


def _worker(spec: ResolvedSpec, config: str, out_dir: Path) -> tuple[str, int, int]:
    decision, _treaty = table_module.build_tables(spec, conform.features_for_config(config))
    lines, windows = write_divergences(decision, out_dir / f"prospect-divergence-{config}.tsv")
    return config, lines, windows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Dump the per-window prospect-divergence inventory.")
    parser.add_argument("--jobs", type=int, default=1, help="worker budget across configurations")
    args = parser.parse_args(argv)
    spec = load_default_spec()
    start = time.perf_counter()
    results: dict[str, tuple[int, int]] = {}
    if args.jobs > 1:
        workers = min(args.jobs, len(conform.ACCEPTANCE_CONFIGS))
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            futures = [pool.submit(_worker, spec, config, OUT_DIR) for config in conform.ACCEPTANCE_CONFIGS]
            for future in as_completed(futures):
                config, lines, windows = future.result()
                results[config] = (lines, windows)
    else:
        for config in conform.ACCEPTANCE_CONFIGS:
            config, lines, windows = _worker(spec, config, OUT_DIR)
            results[config] = (lines, windows)
    for config in conform.ACCEPTANCE_CONFIGS:
        lines, windows = results[config]
        print(f"{config}: {lines} divergence rows over {windows} windows", flush=True)
    print(f"[t] prospect_divergence_total {time.perf_counter() - start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
