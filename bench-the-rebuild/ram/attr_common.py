"""Shared instrument for the RAM attribution harnesses (issue #51). Each harness measures one shape of the rebuild's memory story in phases; this module owns the phase accounting so both report the same way: tracemalloc with one stack frame per allocation (the question is which allocation site owns the bytes, and one frame keeps the tracer light enough to run the full-alphabet fixpoint on a 32 GB box), a top-sites table per phase, the process high-water via the repo-wide peak_rss yardstick, and a JSON row under ram/out/ (gitignored raw; the curated copy lives in evidence/ram/).

Two peaks ride every phase record on purpose. The traced peak counts what Python asked for and is blind to page compression; the RSS high-water is what Darwin kept resident and understates a compressed run. Their ratio at a known window count is the instrument for the tracker's open extrapolation tension. On a traced run the RSS figure includes the tracer's own bookkeeping, so it is not comparable to an untraced run's — set AMS_ATTR_TRACE=0 to run a harness untraced and get the clean high-water (no site tables) instead.
"""

import json
import os
import sys
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.tools.peak_rss import bytes_to_gb, peak_rss_self_bytes  # noqa: E402

OUT = HERE.parent / "out"
TOP_SITES = 40
TRACING = os.environ.get("AMS_ATTR_TRACE", "1") != "0"


def start(frames: int = 1) -> None:
    if TRACING:
        tracemalloc.start(frames)


def _site(frame) -> str:
    try:
        rel = Path(frame.filename).resolve().relative_to(ROOT)
    except ValueError, OSError:
        rel = Path(frame.filename).name
    return f"{rel}:{frame.lineno}"


def phase(label: str, since=None):
    """Close out one measured phase: returns (record, snapshot). The top-sites table is absolute when `since` is None and growth against that snapshot otherwise; the traced peak is reset on the way out so the next phase's figure is its own. Untraced mode records the high-water only and returns None for the snapshot."""
    rss = peak_rss_self_bytes()
    record = {
        "phase": label,
        "rss_high_water_bytes": rss,
        "rss_high_water_gb": round(bytes_to_gb(rss), 2),
    }
    if not TRACING:
        return record, None
    traced_current, traced_peak = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    record["traced_current_bytes"] = traced_current
    record["traced_peak_bytes"] = traced_peak
    record["traced_peak_gb"] = round(bytes_to_gb(traced_peak), 2)
    if since is None:
        stats = snapshot.statistics("lineno")
        record["top_sites"] = [
            {"site": _site(stat.traceback[0]), "bytes": stat.size, "count": stat.count}
            for stat in stats[:TOP_SITES]
        ]
    else:
        stats = sorted(snapshot.compare_to(since, "lineno"), key=lambda stat: -stat.size_diff)
        record["top_sites"] = [
            {
                "site": _site(stat.traceback[0]),
                "bytes": stat.size,
                "grown_bytes": stat.size_diff,
                "count": stat.count,
            }
            for stat in stats[:TOP_SITES]
        ]
    tracemalloc.reset_peak()
    return record, snapshot


def print_summary(record: dict) -> None:
    bits = [f"rss_high_water={record['rss_high_water_gb']}GB"]
    if "traced_peak_gb" in record:
        bits.insert(0, f"traced_peak={record['traced_peak_gb']}GB")
    print(f"[attr] {record['phase']}: {' '.join(bits)}", flush=True)
    for site in record.get("top_sites", [])[:12]:
        grown = site.get("grown_bytes", site["bytes"])
        print(f"[attr]   {grown / 1e6:>10.1f}MB  {site['site']}  x{site['count']}", flush=True)


def write_result(name: str, payload: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
