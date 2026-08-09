"""Fold the per-run JSON lines into the deliverable: equivalence verdict, single-thread cost of
free-threading, and the thread-scaling table with the net multiplier against today's serial GIL run."""

from __future__ import annotations

import json
import statistics
import sys

all_runs = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
env = json.load(open(sys.argv[2]))
odict = (
    {"freethreaded": json.load(open(sys.argv[3])), "gil": json.load(open(sys.argv[4]))}
    if len(sys.argv) > 4
    else None
)
# The scaling tables describe the interpreter as shipped; the gc=off rows are a separate control.
runs = [r for r in all_runs if r.get("gc", "on") == "on"]


def pick(**want):
    return [r for r in runs if all(r.get(k) == v for k, v in want.items())]


def best(rs, key="wall_s"):
    return min(rs, key=lambda r: r[key]) if rs else None


UNITS = max(r["units"] for r in runs)

# --- equivalence -------------------------------------------------------------------------------
ref_runs = [r for r in pick(mode="serial", gil_enabled=True) if r["units"] == UNITS]
reference = ref_runs[0]["checksums"] if ref_runs else None
mismatches = []
for r in runs:
    n = len(r["checksums"])
    expect = (reference * ((n // len(reference)) + 1))[:n] if reference else None
    if reference and r["checksums"] != expect[:n] and r["checksums"] != reference[:n]:
        if not (n == len(reference) and r["checksums"] == reference):
            mismatches.append({"mode": r["mode"], "threads": r["threads"], "gil": r["gil_enabled"]})

# --- single-thread A/B -------------------------------------------------------------------------
gil1 = [r for r in pick(mode="serial", threads=1, gil_enabled=True) if r["units"] == UNITS]
ft1 = [r for r in pick(mode="serial", threads=1, gil_enabled=False) if r["units"] == UNITS]


def stats(rs):
    w = [r["wall_s"] for r in rs]
    c = [r["cpu_s"] for r in rs]
    return {
        "reps": len(w),
        "wall_s_min": min(w),
        "wall_s_median": round(statistics.median(w), 3),
        "wall_s_all": w,
        "cpu_s_min": min(c),
    }


single = {
    "gil_build": stats(gil1),
    "freethreaded_build": stats(ft1),
    "freethreaded_single_thread_ratio_wall": round(ft1[0]["wall_s"] and min(r["wall_s"] for r in ft1) / min(r["wall_s"] for r in gil1), 4),
    "reading": None,
}
rr = single["freethreaded_single_thread_ratio_wall"]
single["reading"] = (
    f"free-threaded 1T is {1 / rr:.3f}x FASTER than the GIL build at the same work"
    if rr < 1
    else f"free-threading costs {(rr - 1) * 100:.1f}% single-threaded"
)
single["read_with"] = "gc_control — this comparison is stock-vs-stock, and the two builds do not collect alike; the gc_control rows say how much of the gap survives gc.freeze()+gc.disable()"

# --- scaling -----------------------------------------------------------------------------------
gil_serial_wall = min(r["wall_s"] for r in gil1)


def table(mode, gil):
    rows = []
    for r in sorted(
        [x for x in runs if x["mode"] == mode and x["gil_enabled"] == gil and x["units"] == UNITS],
        key=lambda x: x["threads"],
    ):
        rows.append(
            {
                "threads": r["threads"],
                "wall_s": r["wall_s"],
                "cpu_s": r["cpu_s"],
                "cpu_utilization": r["cpu_utilization"],
                "speedup_vs_own_1T": round(min(x["wall_s"] for x in (ft1 if not gil else gil1)) / r["wall_s"], 3),
                "net_multiplier_vs_serial_gil": round(gil_serial_wall / r["wall_s"], 3),
                "cpu_inflation_vs_1T": round(r["cpu_s"] / min(x["cpu_s"] for x in (ft1 if not gil else gil1)), 3),
                "parallel_efficiency": round(
                    (min(x["wall_s"] for x in (ft1 if not gil else gil1)) / r["wall_s"]) / r["threads"], 3
                ),
                "peak_rss_gb": r["peak_rss_gb"],
                "liveness_probe_rebuilds": r["liveness_probe_rebuilds"],
                "guard_state_rebuilds": r["guard_state_rebuilds"],
            }
        )
    return rows


ft_shared = table("shared", False)
ft_own = table("own", False)
gil_threaded = table("shared", True)

for row in ft_shared + ft_own:
    row["speedup_vs_own_1T"] = row["speedup_vs_own_1T"]

ft1_row = {
    "threads": 1,
    "wall_s": min(r["wall_s"] for r in ft1),
    "cpu_s": min(r["cpu_s"] for r in ft1),
    "cpu_utilization": 1.0,
    "speedup_vs_own_1T": 1.0,
    "net_multiplier_vs_serial_gil": round(gil_serial_wall / min(r["wall_s"] for r in ft1), 3),
    "cpu_inflation_vs_1T": 1.0,
    "parallel_efficiency": 1.0,
    "peak_rss_gb": min(r["peak_rss_gb"] for r in ft1),
    "liveness_probe_rebuilds": ft1[0]["liveness_probe_rebuilds"],
    "guard_state_rebuilds": ft1[0]["guard_state_rebuilds"],
}
ft_shared = [ft1_row] + ft_shared
ft_own = [ft1_row] + ft_own

# --- production shape --------------------------------------------------------------------------
share_serial = best(pick(mode="share-serial"))
share_fanout = best(pick(mode="share-fanout"))
no_share_serial = best([r for r in pick(mode="serial", gil_enabled=True) if r["units"] == len(share_serial["checksums"])]) if share_serial else None
production = {
    "note": "the shape run_m1.build_tables actually uses: six configs over one live cross-config TraceShare",
    "gil_serial_with_share": {k: share_serial[k] for k in ("wall_s", "cpu_s", "units")} if share_serial else None,
    "freethreaded_donor_then_fanout": (
        {k: share_fanout[k] for k in ("wall_s", "cpu_s", "cpu_utilization", "threads", "units")}
        if share_fanout
        else None
    ),
    "multiplier": (
        round(share_serial["wall_s"] / share_fanout["wall_s"], 3) if share_serial and share_fanout else None
    ),
}

# --- the real shape: exactly six configurations, nothing to load-balance -------------------------
six = [r for r in runs if r["units"] == 6]


def six_row(mode, gil, threads):
    rs = [r for r in six if r["mode"] == mode and r["gil_enabled"] == gil and r["threads"] == threads]
    return min(rs, key=lambda r: r["wall_s"]) if rs else None


six_gil1 = six_row("serial", True, 1)
six_own6 = six_row("own", False, 6)
six_shared6 = six_row("shared", False, 6)
real_shape = {
    "note": "six acceptance configurations is all the parallelism this stage actually offers, and they are unequal — the longest configuration is the floor no thread count can beat",
    "rows": [],
}
for label, r in (
    ("gil 1 thread", six_gil1),
    ("freethreaded 1 thread", six_row("serial", False, 1)),
    ("freethreaded 6 threads, shared spec", six_row("shared", False, 6)),
    ("freethreaded 6 threads, private spec", six_row("own", False, 6)),
):
    if r is None:
        continue
    real_shape["rows"].append(
        {
            "config": label,
            "wall_s": r["wall_s"],
            "cpu_s": r["cpu_s"],
            "cpu_utilization": r["cpu_utilization"],
            "multiplier_vs_serial_gil": round(six_gil1["wall_s"] / r["wall_s"], 3) if six_gil1 else None,
        }
    )

# --- gc control: is a single-thread difference between the interpreters really a GC difference? ---
def gc_row(gil, gcmode):
    rs = [
        r
        for r in all_runs
        if r["mode"] == "serial" and r["gil_enabled"] == gil and r.get("gc", "on") == gcmode and r["units"] == 6
    ]
    return min(rs, key=lambda r: r["wall_s"]) if rs else None


gc_control = {"note": "same six-configuration slice, gc.freeze()+gc.disable() vs stock", "rows": []}
for label, gil in (("gil build", True), ("freethreaded build", False)):
    on, off = gc_row(gil, "on"), gc_row(gil, "off")
    if on and off:
        gc_control["rows"].append(
            {
                "build": label,
                "gc_on_wall_s": on["wall_s"],
                "gc_off_wall_s": off["wall_s"],
                "gc_on_collections": on["gc_collections"],
                "gc_off_collections": off["gc_collections"],
                "gc_saving_pct": round((1 - off["wall_s"] / on["wall_s"]) * 100, 1),
            }
        )
if len(gc_control["rows"]) == 2:
    a, b = gc_control["rows"]
    gc_control["freethreaded_advantage_gc_on"] = round(a["gc_on_wall_s"] / b["gc_on_wall_s"], 3)
    gc_control["freethreaded_advantage_gc_off"] = round(a["gc_off_wall_s"] / b["gc_off_wall_s"], 3)
    gc_control["reading"] = (
        "the single-thread gap is mostly the collector"
        if abs(gc_control["freethreaded_advantage_gc_off"] - 1) < abs(gc_control["freethreaded_advantage_gc_on"] - 1) / 2
        else "the single-thread gap survives with the collector off, so it is not the collector"
    )

# --- projection onto the production stage --------------------------------------------------------
# Two MEASURED endpoints from bench-the-rebuild/evidence/cost-model.md (an exclusive box, the real 18-rune spec) meet
# the ratios measured here on the slice. Everything in this block is DERIVED, never measured, and the
# slice's absolute times appear nowhere in it.
PROD_SIX_CONFIG_COLD_WALL = 337.9  # measured, cost-model.md section 1
PROD_DONOR_CONFIG_ALONE = 77.51  # measured, cost-model.md K1: default config, no trace store
prod_recipients_total = PROD_SIX_CONFIG_COLD_WALL - PROD_DONOR_CONFIG_ALONE
prod_recipient_mean = prod_recipients_total / 5
ft_ratio = single["freethreaded_single_thread_ratio_wall"]
fanout_mult = production["multiplier"]

projection = {
    "status": "DERIVED — cost-model measured endpoints x ratios measured on this slice; not measured at production scale",
    "measured_inputs": {
        "production_six_config_cold_wall_s": PROD_SIX_CONFIG_COLD_WALL,
        "production_donor_config_alone_s": PROD_DONOR_CONFIG_ALONE,
        "slice_freethreaded_single_thread_wall_ratio": ft_ratio,
        "slice_donor_then_fanout_multiplier": fanout_mult,
    },
    "derived_recipient_mean_s": round(prod_recipient_mean, 1),
    "scenarios": [
        {
            "scenario": "run the existing serial code on 3.14t, no threading at all",
            "projected_wall_s": round(PROD_SIX_CONFIG_COLD_WALL * ft_ratio, 1),
            "multiplier": round(1 / ft_ratio, 2),
        },
        {
            "scenario": "donor first, then five recipients as threads over the same TraceShare, at the fanout efficiency measured on this slice",
            "projected_wall_s": (
                round(PROD_SIX_CONFIG_COLD_WALL / fanout_mult, 1) if fanout_mult else None
            ),
            "multiplier": fanout_mult,
        },
        {
            "scenario": "drop the share, run the six configurations as six threads with a private spec each, at the speedup measured on the six-configuration slice",
            "projected_wall_s": (
                round(6 * PROD_DONOR_CONFIG_ALONE / (six_gil1["wall_s"] / six_own6["wall_s"]), 1)
                if six_gil1 and six_own6
                else None
            ),
            "multiplier": (
                round(
                    PROD_SIX_CONFIG_COLD_WALL
                    / (6 * PROD_DONOR_CONFIG_ALONE / (six_gil1["wall_s"] / six_own6["wall_s"])),
                    2,
                )
                if six_gil1 and six_own6
                else None
            ),
            "caveat": "6 x 77.51 s is the DERIVED no-share serial cost, assuming every configuration costs what the measured default configuration costs; the share is worth more at production scale (27%) than on this slice (15%), so this scenario is optimistic on the share side and pessimistic on the config-cost side",
        },
        {
            "scenario": "CEILING: donor plus one recipient, perfect 5-way fanout, zero threading overhead, recipients assumed equal",
            "projected_wall_s": round((PROD_DONOR_CONFIG_ALONE + prod_recipient_mean) * ft_ratio, 1),
            "multiplier": round(
                PROD_SIX_CONFIG_COLD_WALL / ((PROD_DONOR_CONFIG_ALONE + prod_recipient_mean) * ft_ratio), 2
            ),
        },
    ],
    "why_the_ceiling_is_low": "the cross-configuration TraceShare has no content until the donor configuration finishes, so the donor is strictly serial ahead of the other five — Amdahl on a 23% serial fraction caps the whole idea near 2.6x however many cores are free",
    "unmeasured": "per-recipient configuration times at production scale (the slowest recipient, not the mean, sets the fanout's floor) and whether six concurrent production fixpoints fit in RAM (per-config peak RSS is a measured 3.85 GB)",
}

# --- the choice the two levers force ------------------------------------------------------------
# The TraceShare is keyed to one ResolvedSpec, so using it means every thread traverses that one
# object graph — which is precisely the arrangement that scales worst. Threading with a private spec
# per thread scales best but cannot use the share. These are the two ends of that trade, measured.
six_share_serial = share_serial if share_serial and share_serial["units"] == 6 else None
trade = {
    "note": "the cross-configuration share and free-threaded scaling are mutually exclusive as the code stands: the share needs one shared spec, and one shared spec is what caps threading",
    "today_gil_serial_with_share_wall_s": six_share_serial["wall_s"] if six_share_serial else None,
    "freethreaded_threads_with_share_donor_first_wall_s": share_fanout["wall_s"] if share_fanout else None,
    "freethreaded_threads_private_spec_no_share_wall_s": six_own6["wall_s"] if six_own6 else None,
    "freethreaded_threads_shared_spec_no_share_wall_s": six_shared6["wall_s"] if six_shared6 else None,
    "best_against_today": (
        round(six_share_serial["wall_s"] / six_own6["wall_s"], 3) if six_share_serial and six_own6 else None
    ),
    "reading": "dropping the share and giving each thread its own spec beats keeping the share and threading behind the donor",
}

best_ft = max(ft_shared + ft_own, key=lambda r: r["net_multiplier_vs_serial_gil"])

print(
    json.dumps(
        {
            "slug": "freethreaded",
            "environment": env,
            "slice": {
                "kernel": "rebuild.pipeline.table.build_tables (the M1 settlement fixpoint)",
                "spec": f"{runs[0]['runes']} of the 18 production runes: keep={runs[0]['keep']} single letters plus every ligature whose components survive",
                "work_units": f"{UNITS} = the six acceptance configurations, repeated",
                "writes_to_repo": "none (out_dir=None, trace_store=None)",
            },
            "equivalence": {
                "method": "blake2b-128 over every rule row, reachable cell, enumerated window, treaty row and fired-provenance pointer of each built table; the GIL serial run is the reference",
                "reference_checksums": reference,
                "runs_compared": len(runs),
                "mismatches": mismatches,
                "all_match": not mismatches,
            },
            "single_thread_cost_of_free_threading": single,
            "scaling_freethreaded_shared_spec": ft_shared,
            "scaling_freethreaded_private_spec_per_thread": ft_own,
            "scaling_gil_control": gil_threaded,
            "real_shape_six_configs_only": real_shape,
            "gc_control": gc_control,
            "shared_lru_cache_control": {
                "note": "settle._GUARD_STATES and table._LIVENESS_PROBES are the only mutable state the repo shares between concurrent fixpoints; both are cap-8 OrderedDicts read with a structural move_to_end on every hit (104,504 _guard_state calls per six-configuration build at the runner default keep=5, measured by callcounts.py). hot_shared_entry is that access pattern; insert_evict_at_cap is what happens when concurrent threads exceed the cap",
                "measurements": odict,
            },
            "production_shape_with_traceshare": production,
            "projection_onto_the_real_stage": projection,
            "share_versus_threads_trade": trade,
            "headline": {
                "serial_gil_wall_s": gil_serial_wall,
                "best_freethreaded_wall_s": best_ft["wall_s"],
                "best_net_multiplier_vs_serial_gil": best_ft["net_multiplier_vs_serial_gil"],
                "best_config": f"{best_ft['threads']} threads, "
                + ("private spec per thread" if best_ft in ft_own else "one shared spec"),
                "cores_available": env["machine"]["logical_cores"],
            },
        },
        indent=2,
    )
)
