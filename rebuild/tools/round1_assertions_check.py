"""Phase 3 assertion-bucket gate for policy round 1 (plan section 3, step 4). Compares the regenerated divergence audit (rebuild/out/m1/divergence-audit.tsv) against the committed M1 audit (tmp/lab/v-control/out/divergence-audit.tsv, proven byte-identical to the pre-edit committed audit) and the section 13.1 baseline, bucket by bucket, with the plan's documented carve-outs. Never records verdicts; the either/skip/neither/unverdicted buckets only report."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEW_AUDIT = ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
CONTROL_AUDIT = ROOT / "tmp" / "lab" / "v-control" / "out" / "divergence-audit.tsv"
ASSERTIONS = ROOT / "rebuild" / "out" / "policy-round-1-assertions.json"

INK_IDENTICAL_CLASSES = {"zwnj-word-initial-unification", "dangling-anchor-dropped", "bare-name-live-join"}
RESIDUAL_CLASSES = {"halves-entry-extension-restored", "may-exit-withdrawal-generalized"}

REJECTED_CARVE_OUTS = {
    # Phenomenon 1b (qsMay extend[3] composing en-ext-1+ex-ext-1 under ss03-before-·Tea): contradictory verdict signal, no edit this round; must be unchanged from M1.
    "u-0461",
    "u-0462",
    "u-0463",
    "u-0464",
    "u-0465",
    "u-0466",
    "u-0467",
    "u-0469",
    "u-0471",
    "u-0474",
    # The two same-seam rejects, presumed noise pending one ruling; must be unchanged from M1.
    "u-0636",
    "u-0656",
}
# Approved units whose only allowed change is the deletion of the rejected en-ext-1 pixel (plan section 1, edit 1 carve-outs).
APPROVED_PIXEL_CARVE_OUTS = {"u-0383", "u-0424", "u-0272", "u-0356", "u-0267"}
# The lone approve inside the rejected class; expected unchanged (recon A corrects recon B here).
APPROVED_MUST_NOT_CHANGE = {"u-0468"}


def load_audit(path):
    rows = {}
    with open(path) as handle:
        next(handle)
        for line in handle:
            config, codepoints, kinds, matched, baseline, new = line.rstrip("\n").split("\t")
            rows[(config, codepoints)] = (kinds, matched, baseline, new)
    return rows


def main():
    new_rows = load_audit(NEW_AUDIT)
    control_rows = load_audit(CONTROL_AUDIT)
    data = json.loads(ASSERTIONS.read_text())
    buckets = data["assertions"]
    failures = []
    report = {}

    def unit_rows(table, unit):
        return {
            (config, unit["codepoints"]): table.get((config, unit["codepoints"]))
            for config in unit["configs"]
        }

    # --- rejected: flip to baseline (ink-identical or documented residual), carve-outs unchanged from M1 ---
    ink_identical_units, residual_units = [], []
    for unit_id, unit in sorted(buckets["rejected"]["units"].items()):
        old = unit_rows(control_rows, unit)
        new = unit_rows(new_rows, unit)
        if unit_id in REJECTED_CARVE_OUTS:
            if old != new:
                failures.append(f"rejected carve-out {unit_id} changed from M1")
            continue
        classes = set()
        for key, row in new.items():
            if row is None:
                continue
            kinds, matched, _baseline, _new = row
            classes.add(matched)
            if matched in INK_IDENTICAL_CLASSES:
                continue
            if matched in RESIDUAL_CLASSES:
                if "seam" in kinds or "ligation" in kinds:
                    failures.append(f"rejected {unit_id} {key}: residual row not at seam grain ({kinds})")
                continue
            failures.append(f"rejected {unit_id} {key}: unexpected class {matched}")
        if classes & RESIDUAL_CLASSES:
            residual_units.append(unit_id)
        else:
            ink_identical_units.append(unit_id)
    report["rejected"] = {
        "flipped_to_old_ink": len(ink_identical_units),
        "flipped_with_documented_residual": len(residual_units),
        "residual_units": residual_units,
        "carved_out_unchanged": len(REJECTED_CARVE_OUTS),
    }
    if len(ink_identical_units) != 79:
        failures.append(f"expected 79 byte-identical reject flips, measured {len(ink_identical_units)}")
    if len(residual_units) != 16:
        failures.append(f"expected 16 residual reject flips, measured {len(residual_units)}")

    # --- approved: unchanged from M1 except the five pixel-deletion carve-outs ---
    changed_approved = []
    for unit_id, unit in sorted(buckets["approved"]["units"].items()):
        old = unit_rows(control_rows, unit)
        new = unit_rows(new_rows, unit)
        if old == new:
            if unit_id in APPROVED_PIXEL_CARVE_OUTS:
                failures.append(f"approved carve-out {unit_id} expected to lose en-ext-1 but is unchanged")
            continue
        changed_approved.append(unit_id)
        if unit_id in APPROVED_MUST_NOT_CHANGE:
            failures.append(f"u-0468 must be unchanged from M1 but changed")
            continue
        if unit_id not in APPROVED_PIXEL_CARVE_OUTS:
            failures.append(f"approved {unit_id} changed from M1 outside the documented carve-outs")
            continue
        for key in old:
            old_row, new_row = old[key], new[key]
            if old_row == new_row:
                continue
            if old_row is None or new_row is None:
                failures.append(f"approved carve-out {unit_id} {key}: row appeared/vanished")
                continue
            old_kinds, old_matched, old_baseline, old_new = old_row
            new_kinds, new_matched, new_baseline, new_new = new_row
            if old_baseline != new_baseline:
                failures.append(f"approved carve-out {unit_id} {key}: baseline column drifted")
            stripped = old_new.replace("en-ext-1+", "").replace("en-ext-1", "")
            if new_new != stripped:
                failures.append(
                    f"approved carve-out {unit_id} {key}: change is not exactly the en-ext-1 deletion\n  old {old_new}\n  new {new_new}"
                )
            if ("seam" in old_kinds) != ("seam" in new_kinds):
                failures.append(f"approved carve-out {unit_id} {key}: seam topology changed (join lost?)")
    report["approved"] = {
        "unchanged": buckets["approved"]["unit_count"] - len(changed_approved),
        "changed": changed_approved,
    }

    # --- watch list: every gained join survives. Binding for approved/skipped units; either units may legitimately flip to the old behavior (the bucket semantics permit it), so a lost regrouped join there is reported, not failed. ---
    watch_either_flips = []
    for unit_id in data["watch"]["units"]:
        unit, bucket_name = None, None
        for name, bucket in buckets.items():
            if unit_id in bucket["units"]:
                unit, bucket_name = bucket["units"][unit_id], name
                break
        if unit is None:
            failures.append(f"watch unit {unit_id} not found in any bucket")
            continue
        for config in unit["configs"]:
            key = (config, unit["codepoints"])
            old_row, new_row = control_rows.get(key), new_rows.get(key)
            if old_row is None or "seam" not in old_row[0]:
                continue
            if new_row is None or "seam" not in new_row[0]:
                if bucket_name == "either":
                    watch_either_flips.append(f"{unit_id} {key}")
                else:
                    failures.append(f"watch {unit_id} ({bucket_name}) {key}: gained join did not survive")
    report["watch_units_checked"] = len(data["watch"]["units"])
    report["watch_either_flips_to_old"] = watch_either_flips

    # --- either / skip / neither / unverdicted: report only ---
    for bucket_name in ("either", "skip", "neither", "unverdicted"):
        changed, converged = [], []
        for unit_id, unit in sorted(buckets[bucket_name]["units"].items()):
            old = unit_rows(control_rows, unit)
            new = unit_rows(new_rows, unit)
            if old == new:
                continue
            changed.append(unit_id)
            statuses = set()
            for key in old:
                row = new.get(key)
                if row is None:
                    statuses.add("conformant")
                elif row[1] in INK_IDENTICAL_CLASSES:
                    statuses.add("ink-identical")
                else:
                    statuses.add(row[1])
            if statuses <= {"conformant", "ink-identical"}:
                converged.append(unit_id)
        report[bucket_name] = {
            "changed_units": len(changed),
            "changed_ids": changed if bucket_name != "unverdicted" else changed[:0],
            "fully_reconverged_to_old_ink": len(converged),
        }
        if bucket_name == "unverdicted":
            report[bucket_name]["changed_ids_sample"] = changed[:10]
            rows_changed = 0
            for unit_id in changed:
                unit = buckets[bucket_name]["units"][unit_id]
                for config in unit["configs"]:
                    key = (config, unit["codepoints"])
                    if control_rows.get(key) != new_rows.get(key):
                        rows_changed += 1
            report[bucket_name]["rows_changed"] = rows_changed

    print(json.dumps(report, indent=2))
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for failure in failures:
            print(" -", failure)
        sys.exit(1)
    print("\nall assertion buckets green")


if __name__ == "__main__":
    main()
