import glob
import json
import os
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNITS_DIR = os.path.join(REPO, "rebuild/out/review/units")
VERDICTS_PATH = os.path.join(REPO, "verdicts-06.29.03PM.json")
OUT_PATH = os.path.join(REPO, "rebuild/evidence/jitter-candidates.md")


def rune_of(cell):
    return cell.split("/", 1)[0]


def structural_key(unit):
    before = unit["before"]
    after = unit["after"]
    return (
        tuple(before["glyphs"]),
        tuple(before["seams"]),
        tuple(rune_of(c) for c in after["cells"]),
        tuple(after["seams"]),
    )


def load_units():
    by_id = {}
    for path in sorted(glob.glob(os.path.join(UNITS_DIR, "*.json"))):
        for unit in json.load(open(path)):
            by_id[unit["id"]] = {
                "class": unit["class"],
                "notation": unit.get("notation", ""),
                "skey": structural_key(unit),
            }
    return by_id


def load_verdicts():
    data = json.load(open(VERDICTS_PATH))
    return {v["unit"]: v["verdict"] for v in data["verdicts"]}


def main():
    by_id = load_units()
    verdicts = load_verdicts()

    class_members = defaultdict(list)
    for uid, info in by_id.items():
        if uid in verdicts:
            class_members[info["class"]].append(uid)

    family_dominant = {}
    family_share = {}
    family_offcount = {}
    family_size = {}
    for cls, members in class_members.items():
        counts = Counter(verdicts[u] for u in members)
        size = len(members)
        dominant, dom_count = counts.most_common(1)[0]
        family_size[cls] = size
        family_dominant[cls] = dominant
        family_share[cls] = dom_count / size if size else 0.0
        family_offcount[cls] = counts

    family_flagged = set()
    for cls, members in class_members.items():
        size = family_size[cls]
        dominant = family_dominant[cls]
        share = family_share[cls]
        if share < 0.90:
            continue
        threshold = max(3, 0.02 * size)
        for u in members:
            v = verdicts[u]
            if v == dominant:
                continue
            if family_offcount[cls][v] <= threshold:
                family_flagged.add(u)

    cluster_members = defaultdict(list)
    for uid, info in by_id.items():
        if uid in verdicts:
            cluster_members[(info["class"], info["skey"])].append(uid)

    cluster_flagged = set()
    cluster_info = {}
    for key, members in cluster_members.items():
        if len(members) < 8:
            continue
        counts = Counter(verdicts[u] for u in members)
        dominant, dom_count = counts.most_common(1)[0]
        cshare = dom_count / len(members)
        if cshare < 0.90:
            continue
        for u in members:
            if verdicts[u] != dominant:
                cluster_flagged.add(u)
                cluster_info[u] = (dominant, cshare, len(members))

    candidates = set()
    for u in family_flagged:
        cls = by_id[u]["class"]
        if cls == "ss10-isolation-completed":
            if u in cluster_flagged:
                candidates.add(u)
        else:
            candidates.add(u)

    rows = []
    for u in candidates:
        cls = by_id[u]["class"]
        v = verdicts[u]
        dom = family_dominant[cls]
        share = family_share[cls]
        offc = family_offcount[cls][v]
        reasons = []
        reasons.append(f"family share {share:.2f}, off-count {offc}")
        if u in cluster_flagged:
            cdom, cshare, csize = cluster_info[u]
            reasons.append(f"cluster {cshare:.2f} of {csize} -> {cdom}")
        rows.append(
            {
                "unit": u,
                "family": cls,
                "verdict": v,
                "dominant": dom,
                "share": share,
                "offc": offc,
                "why": "; ".join(reasons),
                "notation": by_id[u]["notation"],
            }
        )

    rows.sort(key=lambda r: (-r["share"], r["offc"], r["unit"]))

    caveat = (
        "Some off-votes are intentional — e.g. identical on ink-identical windows, "
        "or neither/reject on the qsDay_qsUtter ligature windows whose call is "
        "genuinely visual. This is a re-check list, not a correction."
    )

    headers = [
        "unit",
        "family",
        "your verdict",
        "dominant",
        "dominant share",
        "why flagged",
        "notation",
    ]
    table = []
    for r in rows:
        table.append(
            [
                r["unit"],
                r["family"],
                r["verdict"],
                r["dominant"],
                f"{r['share']:.2f}",
                r["why"],
                r["notation"],
            ]
        )

    widths = [len(h) for h in headers]
    for row in table:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row):
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) + " |"

    lines = []
    lines.append("# Jitter audit — likely-misclick verdicts")
    lines.append("")
    lines.append(caveat)
    lines.append("")
    lines.append(f"Flagged candidates: {len(rows)}")
    lines.append("")
    lines.append(fmt(headers))
    lines.append("| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |")
    for row in table:
        lines.append(fmt(row))
    lines.append("")

    with open(OUT_PATH, "w") as fh:
        fh.write("\n".join(lines))

    marker_flags = sum(1 for r in rows if r["family"] == "marker-staging-ligature-formation")
    zwnj_rows = [
        r
        for r in rows
        if r["family"] == "zwnj-follower-exit-restored" and r["verdict"] == "reject"
    ]

    print(f"flagged_count={len(rows)}")
    print(f"marker-staging-ligature-formation flags={marker_flags}")
    print(f"zwnj-follower-exit-restored reject flagged={len(zwnj_rows) >= 1}")
    for r in rows[:5]:
        print(r["unit"], r["family"], r["verdict"], r["dominant"], f"{r['share']:.2f}", r["notation"])
    return rows, marker_flags, zwnj_rows


if __name__ == "__main__":
    main()
