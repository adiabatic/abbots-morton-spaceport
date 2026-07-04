"""A/B diff of two scratch-build divergence audits (pre vs post) for the T7 Group C candidate: per-config UNMATCHED deltas, keyed by (config, codepoints), with the entering/leaving rows grouped by window shape."""

import collections
import csv
import sys


def load(path):
    rows = {}
    with open(path) as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows[(r["config"], r["codepoints"], r["kinds"])] = r
    return rows


pre, post = load(sys.argv[1]), load(sys.argv[2])

def unmatched(rows):
    return {k for k, r in rows.items() if r["matched_entry"] == "UNMATCHED"}

upre, upost = unmatched(pre), unmatched(post)
left, entered = upre - upost, upost - upre

print(f"UNMATCHED pre={len(upre)} post={len(upost)} left={len(left)} entered={len(entered)}")
print("\nper config:")
c1 = collections.Counter(k[0] for k in upre)
c2 = collections.Counter(k[0] for k in upost)
for cfg in sorted(set(c1) | set(c2)):
    print(f"  {cfg:16s} {c1[cfg]:5d} -> {c2[cfg]:5d}  {c2[cfg]-c1[cfg]:+d}")

def bucket(keys, rows):
    b = collections.Counter()
    for k in keys:
        b[k[1]] += 1
    return b

print("\nrows that LEFT unmatched (restorations), by window:")
for cps, n in sorted(bucket(left, pre).items(), key=lambda x: -x[1]):
    dest = collections.Counter(post[k]["matched_entry"] for k in left if k[1] == cps and k in post)
    gone = sum(1 for k in left if k[1] == cps and k not in post)
    print(f"  {cps:28s} x{n}  -> {dict(dest)}" + (f" +{gone} no-longer-divergent" if gone else ""))

print("\nrows that ENTERED unmatched (new divergence), by window:")
for cps, n in sorted(bucket(entered, post).items(), key=lambda x: -x[1]):
    src = collections.Counter(pre[k]["matched_entry"] if k in pre else "was-matched" for k in entered if k[1] == cps)
    print(f"  {cps:28s} x{n}  <- {dict(src)}")
