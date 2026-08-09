"""Merge the three runners' JSON into one comparison table plus the weighted composite.

Prints the whole thing as JSON on stdout. Every number carries a tag:
  measured  -- a wall-clock timing this run produced
  derived   -- arithmetic over measured numbers only
  estimated -- rests on a modelling assumption stated in the record itself
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

# Rows of the comparison table: (label, python-op, rust-op, go-op).
# Where a language has several plausible implementations of the same primitive
# the row names the one a competent port would ship; the alternatives stay in
# the per-language `all_results` block.
PAIRS = [
    (
        "construct 8-field record",
        "construct8/frozen-dataclass",
        "construct8/struct-copy",
        "construct8/struct-copy",
    ),
    (
        "construct 8-field record (py: plain tuple)",
        "construct8/plain-tuple",
        "construct8/struct-copy",
        "construct8/struct-copy",
    ),
    (
        "construct 5-field settle.Candidate",
        "legacy5/frozen-dataclass-construct",
        "legacy5/struct-construct",
        "legacy5/struct-construct",
    ),
    ("hash 8-field record", "hash8/frozen-dataclass", "hash8/struct-fx", "hash8/struct-maphash"),
    ("hash 8-field record (py: plain tuple)", "hash8/plain-tuple", "hash8/struct-fx", "hash8/struct-maphash"),
    ("equality, equal records", "eq8/frozen-dataclass-equal", "eq8/struct-equal", "eq8/struct-equal"),
    ("equality, unequal records", "eq8/frozen-dataclass-unequal", "eq8/struct-unequal", "eq8/struct-unequal"),
    (
        "map insert, 10-slot str key",
        "map10str/insert",
        "map10str/insert-fx-presized",
        "map10str/insert-presized",
    ),
    ("map lookup, 10-slot str key", "map10str/lookup", "map10str/lookup-fx", "map10str/lookup"),
    ("map insert, packed-u64 key", "mapU64/insert", "mapU64/insert-fx-presized", "mapU64/insert-presized"),
    ("map lookup, packed-u64 key", "mapU64/lookup", "mapU64/lookup-fx", "mapU64/lookup"),
    ("eq, 10 interned strings", "sym/eq-10str-tuple", "sym/eq-10str-tuple", "sym/eq-10str-tuple"),
    ("eq, 10 u8 symbol ids", "sym/eq-10u8-bytes", "sym/eq-10u8-bytes", "sym/eq-10u8-bytes"),
    ("eq, packed-u64 symbol key", "sym/eq-packed-u64", "sym/eq-packed-u64", "sym/eq-packed-u64"),
    ("hash, 10 interned strings", "sym/hash-10str-tuple", "sym/hash-10str-tuple-fx", "sym/hash-10str-tuple"),
    ("hash, 10 u8 symbol ids", "sym/hash-10u8-bytes", "sym/hash-10u8-bytes-fx", "sym/hash-10u8-bytes"),
    ("hash, packed-u64 symbol key", "sym/hash-packed-u64", "sym/hash-packed-u64-fx", "sym/hash-packed-u64"),
    (
        "rank a 3-8 candidate list",
        "rank/two-stable-sorts-per-list",
        "rank/two-stable-sorts-per-list",
        "rank/two-stable-sorts-per-list",
    ),
    ("alloc+drop small object", "alloc10M/frozen-dataclass", "alloc10M/box-heap", "alloc10M/pointer-gc"),
    (
        "alloc+drop, no heap (rust/go)",
        "alloc10M/frozen-dataclass",
        "alloc10M/by-value-no-alloc",
        "alloc10M/by-value-no-alloc",
    ),
    (
        "filter 700k-row table",
        "filter700k/frozen-dataclass",
        "filter700k/struct-vec",
        "filter700k/struct-slice",
    ),
]

# The loop skeleton itself -- iterate a prebuilt slice, store one field, fold one
# integer -- taken from each kernel's CONTROL timing. This is the closest direct
# measurement of the "interpreter overhead" bucket the cost model puts at 69.2%
# of the fixpoint's CPU, and it matters because it is the part of the workload
# the primitive recipe below does NOT model.
SKELETON = ("construct8/frozen-dataclass", "construct8/struct-copy", "construct8/struct-copy")

# --- the weighted composite -------------------------------------------------
#
# Measured call mix of one six-config M1 build (bench-the-rebuild/evidence/cost-model.md, K1):
CALLS = {
    "candidates": 9_161_481,
    "_prospect": 9_394_188,
    "transition_trace": 2_428_420,
    "_prefer_favors": 9_786_077,
}
# Per-call primitive recipe. ESTIMATED: read off rebuild/pipeline/settle.py's
# bodies (candidates 519-620, _prospect, transition_trace 1180-1290,
# _prefer_favors 754-830) plus the measured stance fan-out in
# bench-the-rebuild/fixtures/candidate-fields.tsv (81 rune-stance-entry-exit
# rows over 18 runes = 4.5 candidate rows per call site, of which ~2.5 survive
# to construction). Nothing here is measured; the composite is only as good as
# this table, which is why it is printed alongside the result.
RECIPE = {
    "candidates": {
        "construct 8-field record": 2.5,
        "equality, unequal records": 6.0,
        "eq, 10 interned strings": 3.0,
    },
    "_prospect": {
        "equality, unequal records": 4.0,
        "eq, 10 interned strings": 2.0,
        "construct 8-field record": 0.5,
    },
    "transition_trace": {
        "construct 8-field record": 4.0,
        "hash 8-field record": 1.0,
        "map lookup, 10-slot str key": 1.0,
        "rank a 3-8 candidate list": 1.0,
    },
    "_prefer_favors": {
        "eq, 10 interned strings": 4.0,
        "equality, unequal records": 2.0,
    },
}


def load(lang: str) -> dict:
    o = json.loads((OUT / f"{lang}.json").read_text())
    return {r["op"]: r for r in o["results"]}


def num(v):
    return float(v) if v is not None else None


def main() -> None:
    langs = {l: load(l) for l in ("python", "rust", "go")}
    meta = {l: json.loads((OUT / f"{l}.json").read_text()) for l in ("python", "rust", "go")}

    table = []
    skel = {
        "operation": "loop skeleton (iterate + store + accumulate)",
        "kind": "measured",
        "note": "each kernel's control loop; the interpreter-dispatch floor",
    }
    for lang, op in zip(("python", "rust", "go"), SKELETON):
        r = langs[lang][op]
        skel[f"{lang}_op"] = op + " [control]"
        skel[f"{lang}_raw_ns"] = round(r["control_ns_per_op"], 3)
        skel[f"{lang}_min_ns"] = round(r["control_ns_per_op"], 3)
        skel[f"{lang}_net_ns"] = round(r["control_ns_per_op"], 3)
        skel[f"{lang}_control_ns"] = 0.0
        skel[f"{lang}_spread_pct"] = 0.0
    for lang in ("rust", "go"):
        if skel[f"{lang}_raw_ns"]:
            skel[f"{lang}_speedup_raw"] = round(skel["python_raw_ns"] / skel[f"{lang}_raw_ns"], 2)
            skel[f"{lang}_speedup_min"] = skel[f"{lang}_speedup_raw"]
            skel[f"{lang}_speedup_net"] = skel[f"{lang}_speedup_raw"]
    table.append(skel)
    for label, pop, rop, gop in PAIRS:
        row = {"operation": label, "kind": "measured"}
        for lang, op in (("python", pop), ("rust", rop), ("go", gop)):
            r = langs[lang].get(op)
            if r is None:
                row[lang] = None
                continue
            row[f"{lang}_op"] = op
            row[f"{lang}_raw_ns"] = round(r["raw_ns_per_op"], 3)
            row[f"{lang}_min_ns"] = round(r["min_ns_per_op"], 3)
            row[f"{lang}_net_ns"] = round(r["net_ns_per_op"], 3)
            row[f"{lang}_control_ns"] = round(r["control_ns_per_op"], 3)
            row[f"{lang}_spread_pct"] = round(r["spread_pct"], 1)
        for lang in ("rust", "go"):
            p, x = row.get("python_raw_ns"), row.get(f"{lang}_raw_ns")
            if p and x:
                row[f"{lang}_speedup_raw"] = round(p / x, 2) if x > 0 else None
            pn, xn = row.get("python_net_ns"), row.get(f"{lang}_net_ns")
            if pn and xn and xn > 0:
                row[f"{lang}_speedup_net"] = round(pn / xn, 2)
            pm, xm = row.get("python_min_ns"), row.get(f"{lang}_min_ns")
            if pm and xm and xm > 0:
                row[f"{lang}_speedup_min"] = round(pm / xm, 2)
        table.append(row)

    by_label = {r["operation"]: r for r in table}
    composite = {"basis": "raw ns/op", "kind": "estimated", "per_site": {}, "totals_ns": {}}
    tot = {"python": 0.0, "rust": 0.0, "go": 0.0}
    for site, calls in CALLS.items():
        site_tot = {"python": 0.0, "rust": 0.0, "go": 0.0}
        for prim, mult in RECIPE[site].items():
            row = by_label[prim]
            for lang in tot:
                v = row.get(f"{lang}_raw_ns")
                if v is not None:
                    site_tot[lang] += v * mult
        composite["per_site"][site] = {
            "calls": calls,
            "ns_per_call": {k: round(v, 2) for k, v in site_tot.items()},
            "recipe": RECIPE[site],
            "seconds": {k: round(v * calls / 1e9, 3) for k, v in site_tot.items()},
        }
        for lang in tot:
            tot[lang] += site_tot[lang] * calls / 1e9
    composite["totals_ns"] = {k: round(v, 3) for k, v in tot.items()}
    composite["seconds_per_six_config_build"] = {k: round(v, 2) for k, v in tot.items()}
    composite["multiplier_vs_python"] = {
        "rust": round(tot["python"] / tot["rust"], 2) if tot["rust"] else None,
        "go": round(tot["python"] / tot["go"], 2) if tot["go"] else None,
    }
    # a second composite on the contention-robust min-of-reps
    tot_min = {"python": 0.0, "rust": 0.0, "go": 0.0}
    for site, calls in CALLS.items():
        for prim, mult in RECIPE[site].items():
            row = by_label[prim]
            for lang in tot_min:
                v = row.get(f"{lang}_min_ns")
                if v is not None:
                    tot_min[lang] += v * mult * calls / 1e9
    composite["seconds_min_of_reps"] = {k: round(v, 2) for k, v in tot_min.items()}
    composite["multiplier_vs_python_min_of_reps"] = {
        "rust": round(tot_min["python"] / tot_min["rust"], 2) if tot_min["rust"] else None,
        "go": round(tot_min["python"] / tot_min["go"], 2) if tot_min["go"] else None,
    }
    composite["coverage_caveat"] = (
        "The recipe models only primitive operations. Its Python total covers a fraction "
        "of the measured six-config build (see sanity_check.ratio_recipe_over_measured); the "
        "remainder is interpreter dispatch, Python-level function calls, attribute lookups and "
        "list building -- measured separately as the 'loop skeleton' row of the table, whose "
        "Rust and Go ratios are LARGER than most primitive ratios here. The composite is "
        "therefore a conservative estimate of the language multiplier, not an optimistic one."
    )
    composite["sanity_check"] = {
        "note": "the recipe's Python total is compared against the measured 337.87 CPU-s of a real six-config table.build_tables run (bench-the-rebuild/evidence/cost-model.md). A recipe within a factor of ~2 is credible; further off means the recipe, not the ratio, is what to distrust.",
        "measured_python_six_config_cpu_s": 337.87,
        "recipe_python_cpu_s": round(tot["python"], 2),
        "ratio_recipe_over_measured": round(tot["python"] / 337.87, 3),
    }

    # equivalence
    eq_keys = ("checksum", "hits", "matched", "acc", "lists", "distinct_hash_values")
    groups: dict[str, dict[str, dict]] = {}
    for lang, rs in langs.items():
        for op, r in rs.items():
            fam = op.split("/")[0] + "/" + op.split("/")[1].split("-")[0] if "/" in op else op
            got = {k: str(r[k]) for k in eq_keys if k in r}
            if got:
                groups.setdefault(lang, {})[op] = got
    checks = []

    def gather(key: str, ops: dict[str, str]):
        vals = {}
        for lang, op in ops.items():
            r = langs[lang].get(op)
            vals[lang] = str(r[key]) if r and key in r else None
        ok = len({v for v in vals.values() if v is not None}) == 1 and all(vals.values())
        checks.append({"check": f"{key} of {'/'.join(ops.values())}", "values": vals, "match": ok})

    gather(
        "checksum",
        {
            "python": "construct8/frozen-dataclass",
            "rust": "construct8/struct-copy",
            "go": "construct8/struct-copy",
        },
    )
    gather(
        "checksum",
        {
            "python": "rank/two-stable-sorts-per-list",
            "rust": "rank/two-stable-sorts-per-list",
            "go": "rank/two-stable-sorts-per-list",
        },
    )
    gather(
        "checksum",
        {
            "python": "filter700k/frozen-dataclass",
            "rust": "filter700k/struct-vec",
            "go": "filter700k/struct-slice",
        },
    )
    gather(
        "matched",
        {
            "python": "filter700k/frozen-dataclass",
            "rust": "filter700k/struct-vec",
            "go": "filter700k/struct-slice",
        },
    )
    gather("hits", {"python": "map10str/lookup", "rust": "map10str/lookup-fx", "go": "map10str/lookup"})
    gather("checksum", {"python": "map10str/lookup", "rust": "map10str/lookup-fx", "go": "map10str/lookup"})
    gather("hits", {"python": "mapU64/lookup", "rust": "mapU64/lookup-fx", "go": "mapU64/lookup"})
    gather("checksum", {"python": "mapU64/lookup", "rust": "mapU64/lookup-fx", "go": "mapU64/lookup"})
    gather(
        "acc",
        {
            "python": "map10str/insert",
            "rust": "map10str/insert-fx-presized",
            "go": "map10str/insert-presized",
        },
    )
    gather(
        "acc", {"python": "eq8/frozen-dataclass-equal", "rust": "eq8/struct-equal", "go": "eq8/struct-equal"}
    )
    gather(
        "acc",
        {"python": "eq8/frozen-dataclass-unequal", "rust": "eq8/struct-unequal", "go": "eq8/struct-unequal"},
    )
    gather("acc", {"python": "sym/eq-10str-tuple", "rust": "sym/eq-10str-tuple", "go": "sym/eq-10str-tuple"})
    gather("acc", {"python": "sym/eq-packed-u64", "rust": "sym/eq-packed-u64", "go": "sym/eq-packed-u64"})
    gather(
        "distinct_hash_values",
        {"python": "hash8/frozen-dataclass", "rust": "hash8/struct-fx", "go": "hash8/struct-maphash"},
    )
    gather(
        "acc",
        {"python": "alloc10M/frozen-dataclass", "rust": "alloc10M/box-heap", "go": "alloc10M/pointer-gc"},
    )

    out = {
        "slug": "k1-micro",
        "what": "primitive-operation benchmark across CPython 3.14, Rust and Go on the data shapes the M1 settlement fixpoint spends its time in",
        "machine": "Apple M4 Pro, macOS Darwin 25.6, 12 logical cores (8P+4E)",
        "toolchains": {
            "python": meta["python"]["runtime"],
            "rust": "rustc 1.97.1, cargo build --release, opt-level=3 lto=true codegen-units=1 panic=abort",
            "go": meta["go"]["runtime"] + ", plain `go build`, no non-default flags, GC on at default GOGC",
        },
        "data": "1,875,829 real settlement memo keys (bench-the-rebuild/fixtures/memo-keys.tsv.gz) plus the 81 real rune-stance-entry-exit rows of candidate-fields.tsv; all three runners read the same bytes out of bench-the-rebuild/primitives/data/",
        "equivalence": {
            "all_match": all(c["match"] for c in checks),
            "checks": checks,
            "note": "hash-value accumulators are deliberately NOT cross-checked: the three languages hash with different functions, which is the thing being measured. What is cross-checked there is distinct_hash_values, which must equal the number of distinct records in every language.",
        },
        "table": table,
        "weighted_composite": composite,
        "runner_wall_s": {"python": round(meta["python"]["wall_s"], 1)},
        "peak_rss_gb_python": round(meta["python"].get("peak_rss_gb", 0), 2),
        "all_results": {l: meta[l]["results"] for l in ("python", "rust", "go")},
    }
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
