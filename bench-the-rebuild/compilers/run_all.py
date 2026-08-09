"""Driver: run the settlement-kernel benchmark under every accelerator variant and emit one JSON report.

Each variant runs in its own subprocess against its own tree, so an accelerator's import machinery never leaks into another's measurement. Equivalence is decided by comparing every variant's `combined_sha256` (which covers windows, rules, reachable cells, treaty rows and cited provenance) against the pure-CPython run of the untouched repo source.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

MYPYC = str(HERE / "venv-mypyc/bin/python")
CYTHON = str(HERE / "venv-cython/bin/python")
PYPY = str(HERE / "venv-pypy/bin/python")

VARIANTS = [
    {
        "name": "cpython-repo-env",
        "what": "untouched repo source, the repo's own `uv run python` environment (harness-noise control)",
        "cwd": str(REPO),
        "python": None,  # uv run
        "pythonpath": [str(REPO), str(HERE)],
        "reps": 1,
    },
    {
        "name": "cpython-baseline",
        "what": "untouched repo source, the scratch venv interpreter the mypyc build uses (the control)",
        "cwd": str(REPO),
        "python": MYPYC,
        "pythonpath": [str(REPO), str(HERE)],
        "reps": 2,
    },
    {
        "name": "cpython-baseline-nogc",
        "what": "same, with gc.freeze() + gc.disable() — the cheap non-compiler lever, for comparison",
        "cwd": str(REPO),
        "python": MYPYC,
        "pythonpath": [str(REPO), str(HERE)],
        "reps": 2,
        "gc": "off",
    },
    {
        "name": "mypyc-settle-table",
        "what": "mypyc, settle.py + table.py compiled; model.py and specificity.py left interpreted",
        "cwd": str(HERE / "tree-mypyc"),
        "python": MYPYC,
        "pythonpath": [str(HERE / "tree-mypyc"), str(HERE)],
        "reps": 2,
    },
    {
        "name": "mypyc-all",
        "what": "mypyc, model.py + specificity.py + settle.py + table.py all compiled",
        "cwd": str(HERE / "tree-mypyc-all"),
        "python": MYPYC,
        "pythonpath": [str(HERE / "tree-mypyc-all"), str(HERE)],
        "reps": 2,
    },
    {
        "name": "mypyc-all-nogc",
        "what": "mypyc all four modules, plus gc.freeze() + gc.disable() — do the two levers stack?",
        "cwd": str(HERE / "tree-mypyc-all"),
        "python": MYPYC,
        "pythonpath": [str(HERE / "tree-mypyc-all"), str(HERE)],
        "reps": 2,
        "gc": "off",
    },
    {
        "name": "cython-purepy",
        "what": "Cython 3.2.9 pure-Python mode (annotation_typing, no .pyx), same four modules",
        "cwd": str(HERE / "tree-cython"),
        "python": CYTHON,
        "pythonpath": [str(HERE / "tree-cython"), str(HERE)],
        "reps": 2,
    },
    {
        "name": "pypy",
        "what": "PyPy 3.11.15 (7.3.23) JIT, source unchanged except PEP 758 except-clauses parenthesized",
        "cwd": str(HERE / "tree-pypy"),
        "python": PYPY,
        "pythonpath": [str(HERE / "tree-pypy"), str(HERE)],
        "reps": 3,
    },
]


def run_variant(v: dict, subset: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = ":".join(v["pythonpath"])
    env.pop("VIRTUAL_ENV", None)
    if v.get("gc") == "off":
        env["AMS_BENCH_GC"] = "off"
    else:
        env.pop("AMS_BENCH_GC", None)
    if v["python"] is None:
        cmd = ["uv", "run", "--project", str(REPO), "python", str(HERE / "bench_kernel.py")]
    else:
        cmd = [v["python"], str(HERE / "bench_kernel.py")]
    cmd += ["--label", v["name"], "--subset", subset, "--reps", str(v["reps"])]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=v["cwd"], env=env, capture_output=True, text=True)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        return {
            "name": v["name"],
            "what": v["what"],
            "status": "FAILED",
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr[-2000:],
            "driver_wall_s": elapsed,
        }
    payload = json.loads(proc.stdout)
    payload["name"] = v["name"]
    payload["what"] = v["what"]
    payload["status"] = "ok"
    payload["driver_wall_s"] = elapsed
    return payload


def memo_key_microbench() -> dict:
    """Per-memo-key cost of the three key shapes, under each runtime. Explains the kernel numbers."""
    dc = HERE / "dcbench"
    runs = [
        ("cpython", str(HERE / "venv-mypyc/bin/python"), str(dc / "pure")),
        ("mypyc", str(HERE / "venv-mypyc/bin/python"), str(dc)),
        ("pypy", str(HERE / "venv-pypy/bin/python"), str(dc / "pure")),
    ]
    out = {}
    checksums = set()
    for label, py, cwd in runs:
        proc = subprocess.run(
            [py, "run_dc.py", label], cwd=cwd, capture_output=True, text=True, env={**os.environ}
        )
        if proc.returncode != 0:
            out[label] = {"status": "FAILED", "stderr": proc.stderr[-400:]}
            continue
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        out[label] = payload
        for k in ("frozen_memo", "handwritten_memo", "tuple_memo"):
            checksums.add(payload[k]["checksum"])
    out["all_runtimes_agree_on_checksum"] = len(checksums) == 1
    out["reading"] = (
        "ns per memo key = construct the key, hash it, probe the dict, insert on miss — the shape of "
        "Engine.transition_trace's cache. 'frozen_memo' is the repo's actual @dataclass(frozen=True); "
        "'handwritten_memo' writes __init__/__hash__/__eq__ by hand; 'tuple_memo' is the packed floor. "
        "Every result escapes into a dict, so no compiler can delete the allocation, and every variant "
        "returns the same checksum."
    )
    return out


def teardown_cost(name: str) -> dict | None:
    """Process real time minus the work the process itself timed = interpreter teardown.

    The cost model calls out ~22 s of unattributed teardown after the six-config M1 run, freeing a
    7.46 GB object graph. Whether an accelerator removes that is a real wall-clock question.
    """
    tpath = HERE / f"out/full-{name}.time"
    jpath = HERE / f"out/full-{name}.json"
    if not tpath.exists() or not jpath.exists():
        return None
    payload = json.loads(jpath.read_text())
    real = maxrss = None
    for line in tpath.read_text().splitlines():
        s = line.strip()
        if s.endswith("real") or " real " in s:
            parts = s.split()
            if "real" in parts:
                real = float(parts[parts.index("real") - 1])
        if "maximum resident set size" in s:
            maxrss = int(s.split()[0])
    if real is None:
        return None
    inside = payload["import_s"] + payload["spec_load_s"] + sum(r["wall_s"] for r in payload["reps"])
    return {
        "process_real_s": round(real, 2),
        "timed_work_s": round(inside, 2),
        "startup_plus_teardown_s": round(real - inside, 2),
        "peak_rss_gb": round(maxrss / 1e9, 2) if maxrss else None,
    }


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main() -> int:
    subset = os.environ.get("AMS_BENCH_SUBSET", "k9")
    rounds = int(os.environ.get("AMS_BENCH_ROUNDS", "3"))

    # Round-robin, not variant-by-variant: a contention burst or a thermal drift then lands on every
    # variant instead of on whichever one happened to be running. Each round is a fresh process per
    # variant, so PyPy pays its JIT warmup every round — its first rep of each round is dropped below.
    merged: dict[str, dict] = {}
    for _ in range(rounds):
        for v in VARIANTS:
            r = run_variant(v, subset)
            prior = merged.get(v["name"])
            if prior is None or prior["status"] != "ok":
                merged[v["name"]] = r
                continue
            if r["status"] != "ok":
                continue
            reps = r["reps"][1:] if v["name"] == "pypy" and len(r["reps"]) > 1 else r["reps"]
            prior["reps"] = prior["reps"] + reps
            prior["peak_rss_mb"] = max(prior.get("peak_rss_mb", 0), r.get("peak_rss_mb", 0))
    for name, r in merged.items():
        if r["status"] == "ok" and name == "pypy" and len(r["reps"]) > 1:
            # drop the first (cold-JIT) rep of the very first round too
            r["reps"] = r["reps"][1:]
    results = [merged[v["name"]] for v in VARIANTS]

    baseline = next((r for r in results if r["name"] == "cpython-baseline" and r["status"] == "ok"), None)
    base_sha = baseline["result"]["combined_sha256"] if baseline else None
    base_best = baseline["best_wall_s"] if baseline else None

    rows = []
    for r in results:
        if r["status"] != "ok":
            rows.append(
                {
                    "variant": r["name"],
                    "what": r["what"],
                    "status": "FAILED",
                    "detail": r.get("stderr_tail", "")[-400:],
                }
            )
            continue
        cpus = [x["cpu_s"] for x in r["reps"]]
        walls = [x["wall_s"] for x in r["reps"]]
        rows.append(
            {
                "variant": r["name"],
                "what": r["what"],
                "status": "ok",
                "runtime": f"{r['implementation']} {r['python']}",
                "kernel_modules_native": r["native_modules"],
                "reps": len(r["reps"]),
                "wall_s_per_build": {
                    "best": round(min(walls), 4),
                    "median": round(sorted(walls)[len(walls) // 2], 4),
                    "all": [round(w, 4) for w in walls],
                },
                "cpu_s_per_build": {
                    "best": round(min(cpus), 4),
                    "median": round(sorted(cpus)[len(cpus) // 2], 4),
                    "all": [round(c, 4) for c in cpus],
                },
                "speedup_vs_cpython_baseline_best_wall": (
                    round(base_best / min(walls), 3) if base_best else None
                ),
                "speedup_vs_cpython_baseline_best_cpu": (
                    round(min(x["cpu_s"] for x in baseline["reps"]) / min(cpus), 3) if baseline else None
                ),
                "speedup_vs_cpython_baseline_median_cpu": (
                    round(
                        sorted(x["cpu_s"] for x in baseline["reps"])[len(baseline["reps"]) // 2]
                        / sorted(cpus)[len(cpus) // 2],
                        3,
                    )
                    if baseline
                    else None
                ),
                "import_s": round(r["import_s"], 4),
                "peak_rss_mb": round(r.get("peak_rss_mb", 0.0), 1),
                "gc_disabled": r.get("gc_disabled", False),
                "combined_sha256": r["result"]["combined_sha256"],
                "equivalent_to_cpython": (r["result"]["combined_sha256"] == base_sha),
                "counts": {k: r["result"][k] for k in r["result"] if k.startswith("n_") or k == "config"},
            }
        )

    micro = memo_key_microbench()

    full_runs = {
        name: load_json(HERE / f"out/full-{name}.json") for name in ("cpython", "mypyc", "pypy", "cython")
    }
    full_cpy = full_runs["cpython"]
    full_mypyc = full_runs["mypyc"]
    build_costs = load_json(HERE / "out/build-costs.json")
    pytest_log = HERE / "out/pytest-mypyc.log"

    report = {
        "slug": "compilers",
        "what_it_models": (
            "rebuild.pipeline.table.build_tables — the M1 settlement-kernel fixpoint — over a fixed "
            f"{subset} subset of the real ResolvedSpec, default feature configuration, no trace store or "
            "cross-config share. Same code, same data, same answer; only the Python accelerator changes."
        ),
        "measured_on": {
            "host": os.uname().nodename,
            "machine": os.uname().machine,
            "sysname": os.uname().sysname,
            "release": os.uname().release,
            "logical_cpus": os.cpu_count(),
            "loadavg_1m_at_start": os.getloadavg()[0],
        },
        "subset": subset,
        "rounds": rounds,
        "measurement_protocol": (
            f"{rounds} round-robin rounds; within a round each variant runs its own fresh process. "
            "Reps are pooled across rounds; PyPy's first rep of each round is discarded as JIT warmup "
            "(the real six-config stage warms once and reuses the JIT for the rest). Headline is the "
            "median; the best-of is reported alongside because it is the least contention-polluted number."
        ),
        "baseline_combined_sha256": base_sha,
        "variants": rows,
        "equivalence_check": {
            "method": (
                "Every variant checksums the full built artifact: all emitted windows "
                "(input/left/right1..right4/outcome), all ordered rules (input, five class slots, outcome, "
                "provenance, joint flag), the reachable-cell set, every treaty row, and the cited-provenance "
                "set. combined_sha256 folds those five. A variant passes only on an exact match with the "
                "pure-CPython run of the untouched repo source."
            ),
            "all_variants_match": all(
                row.get("equivalent_to_cpython") for row in rows if row["status"] == "ok"
            ),
        },
        "full_spec_cross_check": {
            "note": (
                "Same harness, all 18 runes, default configuration — the real single-configuration M1 "
                "build the cost model measured at 77.51 CPU-s. Run once out of band (60-120 CPU-s per "
                "variant, too slow for this runner) and read back here. The counts reproduce the cost "
                "model's default-config figures exactly: 682,842 windows, 2,667 rules, 197 reachable "
                "cells, 3,563 treaty rows. Timings were taken under sibling-agent contention and are "
                "indicative; the equivalence verdict is not."
            ),
            "runs": {
                name: (
                    {
                        "counts": {k: d["result"][k] for k in d["result"] if k.startswith("n_")},
                        "combined_sha256": d["result"]["combined_sha256"],
                        "wall_s": [round(r["wall_s"], 2) for r in d["reps"]],
                        "cpu_s": [round(r["cpu_s"], 2) for r in d["reps"]],
                        "peak_rss_mb": round(d.get("peak_rss_mb", 0.0), 1) or None,
                        "contended": True,
                    }
                    if d
                    else None
                )
                for name, d in full_runs.items()
            },
            "all_match_cpython": (
                bool(full_cpy)
                and all(
                    d["result"]["combined_sha256"] == full_cpy["result"]["combined_sha256"]
                    for d in full_runs.values()
                    if d
                )
            ),
        },
        "full_spec_process_cost": {
            "note": (
                "/usr/bin/time -l on one full-spec single-configuration build, minus the wall the "
                "process itself timed. The remainder is interpreter startup plus teardown — the cost "
                "model's ~22 s of unattributed graph-freeing, at one-configuration scale."
            ),
            "runs": {n: teardown_cost(n) for n in ("cpython", "mypyc", "pypy", "cython")},
        },
        "memo_key_microbenchmark": micro,
        "build_costs": build_costs,
        "repo_kernel_tests_under_mypyc": {
            "command": (
                "AMS_COMPILED_TREE=bench-the-rebuild/compilers/tree-mypyc-all "
                "PYTHONPATH=bench-the-rebuild/compilers uv run pytest -p preload_compiled "
                "rebuild/test_settle.py rebuild/test_table.py -n auto --dist worksteal"
            ),
            "result": (pytest_log.read_text().strip().splitlines()[-1] if pytest_log.exists() else "not run"),
        },
        "toolchain": {
            "cc": shutil.which("cc"),
            "mypy": subprocess.run(
                [str(HERE / "venv-mypyc/bin/mypy"), "--version"], capture_output=True, text=True
            ).stdout.strip(),
            "cython": subprocess.run(
                [str(HERE / "venv-cython/bin/cython"), "--version"], capture_output=True, text=True
            ).stdout.strip(),
            "pypy": subprocess.run(
                [str(HERE / "venv-pypy/bin/python"), "-VV"], capture_output=True, text=True
            ).stdout.strip(),
            "c_flags_used_by_mypyc_and_cython": (
                "setuptools default for this interpreter plus mypyc/Cython additions: "
                "-DNDEBUG -g -O3 -Wall -O3 -arch arm64 -mmacosx-version-min=11.0 -fPIC, then "
                "-O3 -g1 -Werror -Wno-... (mypyc) / plain -O3 (Cython). No PGO, no LTO, no -march=native."
            ),
        },
        "dead_code_note": (
            "Nothing here can be optimized away: every variant runs the real build_tables, materializes "
            "the full DecisionTable and TreatyTable, walks every row into a SHA-256, and prints the digest. "
            "A compiler that elided the work would print a different digest."
        ),
        "loadavg_1m_at_end": os.getloadavg()[0],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
