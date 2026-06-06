"""Reconcile the human triage verdicts in doc/history/2026-06-03--leak-cleanup/leak-emergent-verdicts.txt against the join-contract classification.

Read-only. Reuses leak_contract_report's parse_snapshot + classify so the signature mapping is identical to the report and to site/check.html. Confirms every verdict lands on an "emergent" snapshot row (the contract cannot reach it), buckets the verdicts into the actionable "broken" backlog vs the accepted residue, and flags any verdict that does not map cleanly. See doc/history/2026-06-03--leak-cleanup/leak-triage.md.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT / "tools"), str(ROOT / "test")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import leak_classify  # noqa: E402
import leak_contract_report as rep  # noqa: E402
from leak_static_analysis import parse_calt  # noqa: E402

VERDICTS_PATH = ROOT / "doc" / "history" / "2026-06-03--leak-cleanup" / "leak-emergent-verdicts.txt"

# Canonical preset verdict text -> short bucket. Anything else is a free-text "custom" verdict.
PRESET = {
    "in context is outright broken": "broken",
    "in context is just better than halves-shaped-separately": "in-context-better",
    "in context is OK, but halves-shaped-separately is better": "halves-better",
}


def signature_of(snapshot_line: str) -> rep.Signature:
    """Parse one verbatim snapshot line into its (il, lc, ir, rc) signature, matching rep.parse_snapshot's per-line logic."""
    _label, _, diff = snapshot_line.partition(" :: ")
    il = lc = ir = rc = ""
    for clause in diff.split(" | "):
        clause = clause.strip().lstrip("*").strip()
        if clause.startswith("L ") and "->" in clause:
            il, _, lc = clause[2:].partition("->")
        elif clause.startswith("R ") and "->" in clause:
            ir, _, rc = clause[2:].partition("->")
    return (il.strip(), lc.strip(), ir.strip(), rc.strip())


def load_verdicts() -> list[tuple[str, rep.Signature, str, str]]:
    """Each entry: (verbatim snapshot line, signature, raw verdict text, bucket)."""
    out: list[tuple[str, rep.Signature, str, str]] = []
    for raw in VERDICTS_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        snap, sep, verdict = line.partition(" => ")
        if not sep:
            raise SystemExit(f"no ' => ' separator in: {line!r}")
        snap, verdict = snap.strip(), verdict.strip()
        out.append((snap, signature_of(snap), verdict, PRESET.get(verdict, "custom")))
    return out


def main() -> None:
    rules = parse_calt(str(rep.FEA_PATH)).rules
    klass_by_sig = {v.signature: v.klass for v in rep.classify(rep.parse_snapshot(), rules)}
    rows = load_verdicts()
    print(f"verdict lines: {len(rows)}")

    buckets = Counter(b for *_, b in rows)
    print("\n== verdict buckets ==")
    for b, n in buckets.most_common():
        print(f"  {n:4d}  {b}")
    broken = buckets["broken"]
    accepted = len(rows) - broken
    print(f"\n  actionable broken backlog: {broken}")
    print(f"  accepted (kept in snapshot): {accepted}")

    crosstab: dict[str, Counter] = defaultdict(Counter)
    for _snap, sig, _v, bucket in rows:
        crosstab[bucket][klass_by_sig.get(sig, "MISSING")] += 1
    print("\n== verdict-bucket x contract-class ==")
    for bucket in sorted(crosstab):
        parts = ", ".join(f"{k}:{n}" for k, n in crosstab[bucket].most_common())
        print(f"  {bucket:18s} {parts}")

    non_emergent = [
        (s, klass_by_sig.get(sig))
        for s, sig, _v, _b in rows
        if klass_by_sig.get(sig) not in (None, "emergent")
    ]
    print(f"\n== verdicts NOT on an emergent row (should be 0): {len(non_emergent)} ==")
    for snap, klass in non_emergent:
        print(f"  [{klass}] {snap}")

    verdict_sigs = {sig for _s, sig, _v, _b in rows}
    emergent_sigs = {v.signature for v in rep.classify(rep.parse_snapshot(), rules) if v.klass == "emergent"}
    print("\n== verdict-set vs emergent-set ==")
    print(f"  verdict sigs: {len(verdict_sigs)}; emergent sigs: {len(emergent_sigs)}")
    print(
        f"  emergent without verdict: {len(emergent_sigs - verdict_sigs)}; verdict not emergent: {len(verdict_sigs - emergent_sigs)}"
    )
    for sig in sorted(emergent_sigs - verdict_sigs):
        print(f"    MISSING VERDICT: {sig}")

    confusion_matrix(rows)


def confusion_matrix(rows: list[tuple[str, rep.Signature, str, str]]) -> None:
    """Score the bad/benign proxy in leak_classify against the human verdicts: every "broken" verdict should classify bad, every accepted one benign. Print precision/recall and the two override-seed lists (the disagreements) so the proxy is trusted *with* its overrides before the loop acts on it. Every verdict comes from the depth-4 snapshot, which only records visible (diff) leaks, so visible=True throughout."""
    force_bad = leak_classify.force_bad_signatures()
    force_benign = leak_classify.force_benign_signatures()
    tp = fp = tn = fn = 0
    broken_misses: list[tuple[str, rep.Signature]] = []  # human bad, proxy benign -> force-bad seed
    accepted_misses: list[tuple[str, rep.Signature]] = []  # human benign, proxy bad -> force-benign seed
    for snap, sig, _v, bucket in rows:
        human_bad = bucket == "broken"
        proxy = leak_classify.classify(sig, visible=True, force_bad=force_bad, force_benign=force_benign)
        proxy_bad = proxy == "bad"
        if human_bad and proxy_bad:
            tp += 1
        elif human_bad and not proxy_bad:
            fn += 1
            broken_misses.append((snap, sig))
        elif not human_bad and proxy_bad:
            fp += 1
            accepted_misses.append((snap, sig))
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    print("\n== proxy bad/benign vs human verdict ==")
    print(f"  tp(bad,bad)={tp}  fp(benign,bad)={fp}  tn(benign,benign)={tn}  fn(bad,benign)={fn}")
    print(f"  precision={precision:.3f}  recall={recall:.3f}")
    print(f"\n  broken-but-proxy-benign (force-bad seeds): {len(broken_misses)}")
    for snap, sig in broken_misses:
        print(f"    - {list(sig)}    # {snap}")
    print(f"\n  accepted-but-proxy-bad (force-benign seeds): {len(accepted_misses)}")
    for snap, sig in accepted_misses:
        print(f"    - {list(sig)}    # {snap}")


if __name__ == "__main__":
    main()
