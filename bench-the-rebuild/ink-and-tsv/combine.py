"""Fold the per-step JSON files into the one object run.sh prints, and derive the speedup table.

Every derived number is labelled: `measured` values come straight out of a step's own timer,
`derived` values are ratios of two measured values. Nothing here estimates.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path


def load(work: Path, name: str) -> dict:
    return json.loads((work / name).read_text())


def ver(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip().splitlines()[0]
    except Exception:
        return "unavailable"


def pick_pass(root: Path) -> tuple[Path, dict]:
    """The least contended pass, by the Python K3 baseline's own wall time. Contention can only make a
    pass slower, so the minimum is the closest available estimate of an uncontended machine."""
    passes = []
    for directory in sorted(root.glob("pass*")):
        try:
            value = json.loads((directory / "k3-python.json").read_text())["full_pass_baseline"]["seconds"]
        except Exception:
            continue
        passes.append((value, directory))
    if not passes:
        return root, {"passes": 0, "note": "no pass directories; reading the work root directly"}
    passes.sort()
    times = [value for value, _ in passes]
    return passes[0][1], {
        "passes": len(passes),
        "selector": "min k3-python full_pass_baseline seconds",
        "selected": passes[0][1].name,
        "python_k3_baseline_seconds_per_pass": times,
        "spread_slowest_over_fastest": times[-1] / times[0],
    }


def main() -> None:
    root = Path(sys.argv[1])
    here = Path(sys.argv[2])
    work, pass_info = pick_pass(root)
    py = load(work, "k3-python.json")
    r1 = load(work, "k3-rust-1.json")
    r8 = load(work, "k3-rust-8.json")
    g1 = load(work, "k3-go-1.json")
    g8 = load(work, "k3-go-8.json")
    k5py = load(work, "k5-python.json")
    k5r = load(work, "k5-rust.json")
    k5g = load(work, "k5-go.json")
    mm = load(work, "k5-mmap.json")
    reference = json.loads((here / "k3-reference.json").read_text())

    base = py["full_pass_baseline"]
    rows = py["rows"]

    k3_equiv = {
        "python_reference_checksum": reference["checksum"],
        "rust_checksum": r1["checksum"],
        "rust_parallel_checksum": r8["checksum"],
        "go_checksum": g1["checksum"],
        "go_parallel_checksum": g8["checksum"],
        "all_match": len(
            {reference["checksum"], r1["checksum"], r8["checksum"], g1["checksum"], g8["checksum"]}
        )
        == 1,
        "what_the_checksum_is": (
            "sha256 over one line per shaped run: unit, config, signature_digest (sha256 of CPython's "
            "repr of the (before pieces, after pieces) tuple) and delta_digest ('d-' + first 12 hex of "
            "sha1 of CPython's repr of the config_diff tuple). The ports reproduce CPython repr() text "
            "byte for byte, so this is equality of the persisted digests the repo actually stores, not "
            "of some looser summary."
        ),
    }

    k3_variants = {
        "python-baseline": {
            "seconds": base["seconds"],
            "us_per_row": base["us_per_row"],
            "measurement": "measured",
        },
        "python-optimized-digest": {
            "seconds": py["full_pass_marshal_digest"]["seconds"],
            "us_per_row": py["full_pass_marshal_digest"]["us_per_row"],
            "measurement": "measured",
            "note": "identical kernel; signature_digest hashes marshal v2 bytes instead of repr text",
        },
        "rust-single": {
            "seconds": r1["seconds"],
            "us_per_row": r1["us_per_row"],
            "measurement": "measured",
        },
        "rust-parallel": {
            "seconds": r8["seconds"],
            "us_per_row": r8["us_per_row"],
            "threads": 8,
            "measurement": "measured",
        },
        "go-single": {"seconds": g1["seconds"], "us_per_row": g1["us_per_row"], "measurement": "measured"},
        "go-parallel": {
            "seconds": g8["seconds"],
            "us_per_row": g8["us_per_row"],
            "threads": 8,
            "measurement": "measured",
        },
    }
    for name, entry in k3_variants.items():
        entry["speedup_vs_python_baseline"] = base["seconds"] / entry["seconds"]
        entry["speedup_kind"] = "derived"

    shaping = py.get("shaping_only", {}).get("seconds")
    if shaping is None:
        amdahl = {"note": "shipped fonts not present; no HarfBuzz floor measured"}
    else:
        whole = base["seconds"] + shaping
        amdahl = {
            "kind": "derived",
            "basis": (
                "the surface build's real per-row K3 cost with a memoized shaper: the measured Python "
                "arithmetic pass plus the measured cost of the two hb.shape calls per row that no "
                "rewrite may remove (an independent shaper is the gate's premise)."
            ),
            "python_arithmetic_seconds": base["seconds"],
            "harfbuzz_floor_seconds": shaping,
            "harfbuzz_share_of_whole": shaping / whole,
            "whole_kernel_python_seconds": whole,
            "whole_kernel_rust_single_seconds": r1["seconds"] + shaping,
            "whole_kernel_speedup_rust_single": whole / (r1["seconds"] + shaping),
            "whole_kernel_speedup_rust_parallel": whole / (r8["seconds"] + shaping),
            "whole_kernel_speedup_go_single": whole / (g1["seconds"] + shaping),
            "whole_kernel_ceiling_arithmetic_free": whole / shaping,
            "whole_kernel_speedup_python_marshal_digest_only": whole
            / (py["full_pass_marshal_digest"]["seconds"] + shaping),
        }

    k5 = {}
    for pykey, kernel in (
        ("rowmodel_from_tsv", "rows_from_tsv"),
        ("load_audit", "load_audit"),
    ):
        block = k5py[pykey]
        baseline = block["python_repo_baseline"]
        entry = {
            "rows": baseline["rows"],
            "python_repo_baseline_ns_per_row": baseline["ns_per_row"],
            "checksum": baseline["checksum"],
            "variants": {},
        }
        for name, value in block.items():
            if isinstance(value, dict) and "ns_per_row" in value:
                entry["variants"][name] = {
                    "ns_per_row": value["ns_per_row"],
                    "checksum_matches": value["checksum_matches_baseline"],
                    "speedup_vs_python_baseline": baseline["ns_per_row"] / value["ns_per_row"],
                }
        rustkey = "rust_owned_ns_per_row" if "rust_owned_ns_per_row" in k5r[kernel] else "rust_ns_per_row"
        rustck = "rust_owned_checksum" if "rust_owned_checksum" in k5r[kernel] else "rust_checksum"
        entry["variants"]["rust_owned"] = {
            "ns_per_row": k5r[kernel][rustkey],
            "checksum_matches": k5r[kernel][rustck] == baseline["checksum"],
            "speedup_vs_python_baseline": baseline["ns_per_row"] / k5r[kernel][rustkey],
        }
        if "rust_borrowed_ns_per_row" in k5r[kernel]:
            entry["variants"]["rust_borrowed"] = {
                "ns_per_row": k5r[kernel]["rust_borrowed_ns_per_row"],
                "checksum_matches": k5r[kernel]["rust_borrowed_checksum"] == baseline["checksum"],
                "speedup_vs_python_baseline": baseline["ns_per_row"]
                / k5r[kernel]["rust_borrowed_ns_per_row"],
            }
        entry["variants"]["go"] = {
            "ns_per_row": k5g[kernel]["go_ns_per_row"],
            "checksum_matches": k5g[kernel]["go_checksum"] == baseline["checksum"],
            "speedup_vs_python_baseline": baseline["ns_per_row"] / k5g[kernel]["go_ns_per_row"],
        }
        k5[pykey] = entry

    ft = k5py["filter_table"]
    ftr = k5r["filter_table"]
    ftg = k5g["filter_table"]
    ft_base = ft["python_repo_baseline"]["seconds"]
    k5["filter_table"] = {
        "source_rows": ft["source_rows"],
        "source_bytes": ft["source_bytes"],
        "kept_rows": ft["python_repo_baseline"]["kept"],
        "checksum": ft["python_repo_baseline"]["checksum"],
        "note": (
            "timed on the decompressed 554 MB table with a plain-text destination, so every language "
            "measures the same parse+filter+write. The repo's real gz-in/gz-out call is reported "
            "separately, with the zlib inflate cost broken out as a native floor."
        ),
        "variants": {
            "python-baseline": {
                "seconds": ft_base,
                "speedup_vs_python_baseline": 1.0,
                "checksum_matches": True,
            },
            "python-optimized-bytes": {
                "seconds": ft["python_opt_bytes"]["seconds"],
                "speedup_vs_python_baseline": ft_base / ft["python_opt_bytes"]["seconds"],
                "checksum_matches": ft["python_opt_matches_baseline"],
            },
            "rust-single": {
                "seconds": ftr["rust_single_seconds"],
                "speedup_vs_python_baseline": ft_base / ftr["rust_single_seconds"],
                "checksum_matches": ftr["rust_single_checksum"] == ft["python_repo_baseline"]["checksum"],
            },
            "rust-parallel": {
                "seconds": ftr["rust_parallel_seconds"],
                "threads": ftr["rust_parallel_threads"],
                "speedup_vs_python_baseline": ft_base / ftr["rust_parallel_seconds"],
                "checksum_matches": ftr["rust_parallel_checksum"] == ft["python_repo_baseline"]["checksum"],
            },
            "go-single": {
                "seconds": ftg["go_single_seconds"],
                "speedup_vs_python_baseline": ft_base / ftg["go_single_seconds"],
                "checksum_matches": ftg["go_single_checksum"] == ft["python_repo_baseline"]["checksum"],
            },
            "go-parallel": {
                "seconds": ftg["go_parallel_seconds"],
                "threads": ftg["go_parallel_threads"],
                "speedup_vs_python_baseline": ft_base / ftg["go_parallel_seconds"],
                "checksum_matches": ftg["go_parallel_checksum"] == ft["python_repo_baseline"]["checksum"],
            },
        },
        "repo_gz_in_gz_out_seconds": ft["repo_end_to_end_gz_seconds"],
        "zlib_inflate_seconds": ft["inflate_seconds"],
    }

    out = {
        "slug": "k3-k5",
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "rustc": ver(["rustc", "--version"]),
            "cargo": ver(["cargo", "--version"]),
            "go": ver(["go", "version"]),
            "rust_flags": "cargo build --release; opt-level=3, lto=true, codegen-units=1; sha2/sha1 'asm' feature",
            "go_flags": "go build (no flags)",
        },
        "contention_control": pass_info,
        "k3_placed_ink": {
            "rows": rows,
            "corpus": "bench-the-rebuild/fixtures/shaped-runs.jsonl (3,000 real shaped runs) + outlines-{before,after}.json",
            "equivalence": k3_equiv,
            "variants": k3_variants,
            "stage_split_python": py["stage_split"],
            "signature_digest_split": py["signature_digest_split"],
            "marshal_digest_check": py["marshal_digest_check"],
            "fidelity_vs_live_fonts": py.get("live_font_pass", {"note": "shipped fonts not present"}),
            "harfbuzz_floor": py.get("shaping_only", {"note": "shipped fonts not present"}),
            "translate_outline": {
                "python_ns_per_point": py["translate_outline"]["ns_per_point"],
                "rust_ns_per_point": r1["translate_outline_ns_per_point"],
                "go_ns_per_point": g1["translate_outline_ns_per_point"],
                "python_over_rust": py["translate_outline"]["ns_per_point"]
                / r1["translate_outline_ns_per_point"],
            },
            "sha256_throughput_mb_per_s": {
                "rust": r1["sha256_mb_per_s"],
                "go": g1["sha256_mb_per_s"],
            },
            "amdahl_with_the_harfbuzz_floor": amdahl,
            "repr_free_digest_in_the_ports": {
                "rust_seconds": r1["binary_digest_seconds"],
                "go_seconds": g1["binary_digest_seconds"],
                "note": "same kernel, signature digest taken over a packed binary encoding rather than repr text; the Rust and Go packings differ from each other so their checksums are not comparable — timing only",
            },
        },
        "k5_tsv_parsing": k5,
        "k5_mmap_vs_reparse": mm,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
