"""Merge every variant's output into one JSON object, with the equivalence assertions and the derived ratios."""

import json
import re
import sys
from pathlib import Path


def parse_time(path: Path) -> dict:
    """/usr/bin/time -l output: real/user/sys plus the peak RSS line."""
    out: dict = {}
    if not path.exists():
        return out
    text = path.read_text()
    m = re.search(r"^\s*([\d.]+)\s+real\s+([\d.]+)\s+user\s+([\d.]+)\s+sys", text, re.M)
    if m:
        out["wall_seconds"] = float(m.group(1))
        out["cpu_seconds"] = float(m.group(2)) + float(m.group(3))
        out["user_seconds"] = float(m.group(2))
    m = re.search(r"^\s*(\d+)\s+maximum resident set size", text, re.M)
    if m:
        out["peak_rss_bytes"] = int(m.group(1))
    return out


def load(out: Path, name: str) -> dict | None:
    p = out / f"{name}.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
    proc = parse_time(out / f"{name}.time")
    # `uv run` spawns python as a grandchild, so /usr/bin/time cannot see its rusage; every Python variant
    # reports its own wall/cpu/rss and those win where present.
    for field in ("wall_seconds", "cpu_seconds", "peak_rss_bytes"):
        if payload.get(field) is not None:
            proc[field] = payload[field]
    payload["process"] = proc
    return payload


def main() -> None:
    out = Path(sys.argv[1])
    names = [
        "python-one",
        "python-six",
        "python-six-noshare",
        "rust-one",
        "rust-six",
        "rust-six-noshare",
        "rust-six-par",
        "rust-six-par-noshare",
        "go-one",
        "go-six",
        "go-six-noshare",
        "go-six-par",
        "go-six-par-noshare",
    ]
    variants = {}
    for n in names:
        v = load(out, n)
        if v is not None:
            variants[n] = v

    real = load(out, "real-kernel")
    memo = {k: load(out, f"memo-{k}") for k in ("python", "rust", "go")}

    # --- equivalence ---------------------------------------------------------
    per_config: dict[str, set] = {}
    for name, v in variants.items():
        for c in v["configs"]:
            per_config.setdefault(c["config"], set()).add((c["windows"], c["cells"], c["checksum"]))
    equivalence = {
        "window_checksums_agree": all(len(s) == 1 for s in per_config.values()),
        "per_config": {
            k: {
                "windows": sorted(s)[0][0],
                "cells": sorted(s)[0][1],
                "checksum": sorted(s)[0][2],
                "distinct_answers_across_variants": len(s),
            }
            for k, s in sorted(per_config.items())
        },
        "memo_checksums_agree": len({m["checksum"] for m in memo.values() if m}) == 1,
        "memo_checksum": next((m["checksum"] for m in memo.values() if m), None),
        "share_is_answer_preserving": None,
    }
    if "rust-six" in variants and "rust-six-noshare" in variants:
        a = [c["checksum"] for c in variants["rust-six"]["configs"]]
        b = [c["checksum"] for c in variants["rust-six-noshare"]["configs"]]
        equivalence["share_is_answer_preserving"] = a == b

    def cpu(name: str) -> float | None:
        v = variants.get(name)
        if not v:
            return None
        return v["process"].get("cpu_seconds")

    def wall(name: str) -> float | None:
        v = variants.get(name)
        if not v:
            return None
        return v["process"].get("wall_seconds")

    def ratio(a, b):
        if a is None or b is None or not b:
            return None
        return round(a / b, 3)

    # --- fidelity ------------------------------------------------------------
    fidelity = None
    if real and "python-one" in variants:
        rk = real
        model = variants["python-one"]["configs"][0]
        real_ops = (
            rk["counters"].get("candidates", 0)
            + rk["counters"].get("_prospect", 0)
            + rk["counters"].get("transition_trace", 0)
            + rk["counters"].get("_prefer_favors", 0)
        )
        model_ops = model["candidates"] + model["prospect"] + model["trace"] + model["favors"]
        real_cpu = rk["build_tables_cpu"]
        model_cpu = variants["python-one"]["cpu_seconds"]
        fidelity = {
            "real_kernel_build_tables_cpu_seconds": real_cpu,
            "real_kernel_windows": rk["n_windows"],
            "real_kernel_counters": rk["counters"],
            "real_kernel_peak_rss_bytes": real["process"].get("peak_rss_bytes"),
            "model_cpu_seconds": model_cpu,
            "model_windows": model["windows"],
            "model_counters": {
                "candidates": model["candidates"],
                "prospect": model["prospect"],
                "transition_trace": model["trace"],
                "prefer_favors": model["favors"],
            },
            "model_peak_rss_bytes": variants["python-one"]["process"].get("peak_rss_bytes"),
            "volume_ratio_windows": ratio(model["windows"], rk["n_windows"]),
            "volume_ratio_candidates": ratio(model["candidates"], rk["counters"].get("candidates")),
            "us_per_candidates_call_real": (
                round(real_cpu * 1e6 / rk["counters"]["candidates"], 4) if rk["counters"] else None
            ),
            "us_per_candidates_call_model": round(model_cpu * 1e6 / model["candidates"], 4),
            "us_per_kernel_op_real": round(real_cpu * 1e6 / real_ops, 4) if real_ops else None,
            "us_per_kernel_op_model": round(model_cpu * 1e6 / model_ops, 4),
        }
        if fidelity["us_per_kernel_op_real"]:
            fidelity["FIDELITY_RATIO_cost_per_kernel_op"] = round(
                fidelity["us_per_kernel_op_model"] / fidelity["us_per_kernel_op_real"], 3
            )
            fidelity["FIDELITY_RATIO_cost_per_candidates_call"] = round(
                fidelity["us_per_candidates_call_model"] / fidelity["us_per_candidates_call_real"], 3
            )

    multipliers = {
        "one_config": {
            "rust_vs_python_cpu": ratio(cpu("python-one"), cpu("rust-one")),
            "go_vs_python_cpu": ratio(cpu("python-one"), cpu("go-one")),
            "go_vs_rust_cpu": ratio(cpu("go-one"), cpu("rust-one")),
        },
        "six_config_serial": {
            "rust_vs_python_cpu": ratio(cpu("python-six"), cpu("rust-six")),
            "rust_vs_python_wall": ratio(wall("python-six"), wall("rust-six")),
            "go_vs_python_cpu": ratio(cpu("python-six"), cpu("go-six")),
        },
        "six_config_parallel_combined": {
            "rust_par_shared_vs_python_serial_wall": ratio(wall("python-six"), wall("rust-six-par")),
            "rust_par_noshare_vs_python_serial_wall": ratio(wall("python-six"), wall("rust-six-par-noshare")),
            "go_par_shared_vs_python_serial_wall": ratio(wall("python-six"), wall("go-six-par")),
            "go_par_noshare_vs_python_serial_wall": ratio(wall("python-six"), wall("go-six-par-noshare")),
        },
        "parallelism_alone": {
            "rust_par_shared_vs_rust_serial": ratio(wall("rust-six"), wall("rust-six-par")),
            "rust_par_noshare_vs_rust_serial": ratio(wall("rust-six"), wall("rust-six-par-noshare")),
            "go_par_shared_vs_go_serial": ratio(wall("go-six"), wall("go-six-par")),
            "go_par_noshare_vs_go_serial": ratio(wall("go-six"), wall("go-six-par-noshare")),
        },
        "does_the_share_still_pay": {
            "rust_serial_share_vs_noshare": ratio(wall("rust-six-noshare"), wall("rust-six")),
            "rust_best_with_share": wall("rust-six-par"),
            "rust_best_without_share": wall("rust-six-par-noshare"),
            "go_serial_share_vs_noshare": ratio(wall("go-six-noshare"), wall("go-six")),
        },
        "memo_lookup": {
            "python_ns": memo["python"]["ns_per_lookup"] if memo["python"] else None,
            "rust_ns": memo["rust"]["ns_per_lookup"] if memo["rust"] else None,
            "go_ns": memo["go"]["ns_per_lookup"] if memo["go"] else None,
            "rust_vs_python": ratio(
                memo["python"]["ns_per_lookup"] if memo["python"] else None,
                memo["rust"]["ns_per_lookup"] if memo["rust"] else None,
            ),
            "go_vs_python": ratio(
                memo["python"]["ns_per_lookup"] if memo["python"] else None,
                memo["go"]["ns_per_lookup"] if memo["go"] else None,
            ),
            "python_bytes_per_entry": memo["python"]["bytes_per_entry"] if memo["python"] else None,
            "packed_key_struct_bytes": memo["rust"]["key_struct_bytes"] if memo["rust"] else None,
        },
        "memory": {
            "python_one_peak_rss_bytes": variants.get("python-one", {})
            .get("process", {})
            .get("peak_rss_bytes"),
            "rust_one_peak_rss_bytes": variants.get("rust-one", {}).get("process", {}).get("peak_rss_bytes"),
            "go_one_peak_rss_bytes": variants.get("go-one", {}).get("process", {}).get("peak_rss_bytes"),
            "python_six_peak_rss_bytes": variants.get("python-six", {})
            .get("process", {})
            .get("peak_rss_bytes"),
            "rust_six_peak_rss_bytes": variants.get("rust-six", {}).get("process", {}).get("peak_rss_bytes"),
        },
    }

    print(
        json.dumps(
            {
                "slug": "k1-meso",
                "what": "the M1 settlement fixpoint (table.build_tables driving settle.Engine and table._ProspectLiveness), modelled in Python, Rust and Go",
                "real_kernel_calibration": real,
                "fidelity": fidelity,
                "equivalence": equivalence,
                "multipliers": multipliers,
                "variants": variants,
                "memo_benchmark": memo,
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
