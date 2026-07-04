import argparse
import collections
import json
import pathlib
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import InkComparator  # noqa: E402

OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_SURFACE = ROOT / "rebuild/out/review"
SURFACE_ARCHIVES = ROOT / "rebuild/evidence/surfaces"
SOURCES = [
    (ROOT / "tmp/review-baseline", ROOT / "verdicts-11.33.00PM.json", None),
    (ROOT / "tmp/review-preview-bd1", ROOT / "verdicts-12.04.13AM.json", "2026-07-03"),
]
# secondary_seams is derived data whose `home` field embeds another unit's id, so it churns whenever the surface renumbers; everything adjudicable it describes is already covered by the window plus both fonts' glyphs, cells, and seams.
PRESENTATION_KEYS = {"id", "batch", "no_verdict", "exemplar", "explain", "drafts", "provenance", "secondary_seams"}


def ensure_surface(root):
    if not root.is_dir():
        archive = SURFACE_ARCHIVES / f"{root.name}.tar.gz"
        root.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tf:
            tf.extractall(root.parent, filter="data")
    return root


def load_surface(root):
    units = []
    for path in sorted((root / "units").glob("*.json")):
        units.extend(json.loads(path.read_text()))
    return units


def content_key(unit):
    return json.dumps(
        {k: v for k, v in sorted(unit.items()) if k not in PRESENTATION_KEYS},
        sort_keys=True,
    )


def surface_comparator(root):
    manifest = json.loads((root / "manifest.json").read_text())
    return InkComparator(root / manifest["fonts"]["before"]["file"], root / manifest["fonts"]["after"]["file"])


def ink_key(comparator, unit):
    """The cross-surface identity of a unit's visual question: its window plus the rendered-outcome signature both fonts produce for it. Ink-duplicate merging re-keys and re-configures units without moving any ink, so a prior verdict whose content key no longer exists still applies to the current unit with the same ink_key. None when the unit's own configs disagree (no single signature to carry against)."""
    text = "".join(chr(int(part, 16)) for part in unit["codepoints"].split(":"))
    signatures = {comparator.signature(text, config) for config in unit["configs"]}
    if len(signatures) != 1:
        return None
    return (unit["codepoints"], signatures.pop())


def latest_verdicts(path):
    best = {}
    for record in json.loads(path.read_text())["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def main():
    parser = argparse.ArgumentParser(description="Re-resolve prior verdicts against the surfaces they were recorded on and carry them onto the live surface.")
    parser.add_argument(
        "--source",
        nargs=2,
        action="append",
        metavar=("SURFACE_DIR", "VERDICTS_JSON"),
        help="a prior surface directory and the verdicts file recorded against it; repeatable; defaults to the historical round-3 sources",
    )
    parser.add_argument("--out", default=str(OUT), help="output verdicts file (default: %(default)s)")
    args = parser.parse_args()
    sources = (
        [(pathlib.Path(directory), pathlib.Path(verdicts), None) for directory, verdicts in args.source]
        if args.source
        else SOURCES
    )

    prior = {}
    surface_roots = {}
    for surface_root, verdict_file, at_floor in sources:
        root = ensure_surface(surface_root)
        surface_roots[root.name] = root
        units_by_id = {u["id"]: u for u in load_surface(root)}
        used = 0
        for unit_id, record in latest_verdicts(verdict_file).items():
            if at_floor is not None and record["at"] < at_floor:
                continue
            unit = units_by_id.get(unit_id)
            if unit is None:
                continue
            key = content_key(unit)
            if key not in prior or record["at"] > prior[key][0]["at"]:
                prior[key] = (record, root.name, unit)
            used += 1
        print(f"{verdict_file.name}: {used} verdicts resolved against {surface_root.name}")

    manifest = json.loads((CURRENT_SURFACE / "manifest.json").read_text())
    current = load_surface(CURRENT_SURFACE)
    human = [u for u in current if u.get("batch") is not None and not u.get("no_verdict")]

    keys_seen = collections.Counter(content_key(u) for u in current)
    collisions = {k for k, n in keys_seen.items() if n > 1}
    if collisions:
        raise SystemExit(f"{len(collisions)} content-key collisions on the current surface; refusing to carry")

    carried = []
    kinds = collections.Counter()

    def carry(unit, record, source):
        provenance = f"[carried {record['unit']}@{source}, verdicted {record['at'][:10]}]"
        note = f"{provenance} {record['note']}".strip()
        carried.append({"unit": unit["id"], "verdict": record["verdict"], "note": note, "at": record["at"]})
        kinds[record["verdict"]] += 1

    unhit = []
    for unit in human:
        hit = prior.get(content_key(unit))
        if hit is None:
            unhit.append(unit)
            continue
        record, source, _prior_unit = hit
        if record["verdict"] == "skip":
            continue
        carry(unit, record, source)

    current_keys = {content_key(u) for u in current}
    stranded = [
        (record, source, unit)
        for key, (record, source, unit) in prior.items()
        if key not in current_keys and record["verdict"] != "skip"
    ]
    if stranded and unhit:
        current_comparator = surface_comparator(CURRENT_SURFACE)
        prior_comparators = {name: surface_comparator(root) for name, root in surface_roots.items()}
        stranded_by_ink = collections.defaultdict(list)
        for record, source, unit in stranded:
            key = ink_key(prior_comparators[source], unit)
            if key is not None:
                stranded_by_ink[key].append((record, source))
        ink_carried = 0
        conflicts = []
        for unit in unhit:
            key = ink_key(current_comparator, unit)
            matches = stranded_by_ink.get(key) if key is not None else None
            if not matches:
                continue
            if len({record["verdict"] for record, _ in matches}) > 1:
                conflicts.append((unit["id"], matches))
                continue
            record, source = max(matches, key=lambda match: match[0]["at"])
            carry(unit, record, source)
            ink_carried += 1
        print(f"ink fallback: {ink_carried} verdicts carried onto re-keyed (merged) units")
        if conflicts:
            print(f"{len(conflicts)} merged units had conflicting prior verdicts and were left unverdicted for a fresh look:")
            for unit_id, matches in conflicts:
                sides = "; ".join(f"{record['unit']}@{source}={record['verdict']}" for record, source in matches)
                print(f"  {unit_id} <- {sides}")

    carried.sort(key=lambda r: r["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": carried,
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out.name}: {len(carried)} carried onto manifest {manifest['generated_at']}")
    print(f"kinds: {dict(kinds)}")
    print(f"human queue: {len(human)} -> {len(human) - len(carried)} still needing fresh verdicts")


if __name__ == "__main__":
    main()
