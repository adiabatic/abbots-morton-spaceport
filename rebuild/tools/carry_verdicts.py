import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import InkComparator  # noqa: E402
from rebuild.review.unit_cache import CARRY_PRESENTATION_KEYS, carry_projection  # noqa: E402
from rebuild.tools.verdict_notes import cap_markers  # noqa: E402

OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_SURFACE = ROOT / "rebuild/out/review"
# The exclusion set and the projection recipe live in rebuild.review.unit_cache so the build stamps each unit with exactly the hash this tool probes; the rationale for what is excluded rides the definition there.
PRESENTATION_KEYS = CARRY_PRESENTATION_KEYS


def load_surface(root):
    units = []
    for path in sorted((root / "units").glob("*.json")):
        units.extend(json.loads(path.read_text()))
    return units


def content_key(unit):
    return carry_projection(unit)


def content_hash(unit):
    """The unit's carry identity as a digest: the build-time `content_key` stamp when the surface carries one, else the sha256 of the projection computed here — the same value, so stamped and unstamped surfaces (every snapshot predating the stamp) resolve against each other freely."""
    stamped = unit.get("content_key")
    return stamped if stamped else hashlib.sha256(content_key(unit).encode()).hexdigest()


def surface_comparator(root):
    manifest = json.loads((root / "manifest.json").read_text())
    return InkComparator(
        root / manifest["fonts"]["before"]["file"], root / manifest["fonts"]["after"]["file"]
    )


def ink_key(comparator, unit):
    """The cross-surface identity of a unit's visual question: its window plus the rendered-outcome signature both fonts produce for it. Ink-duplicate merging re-keys and re-configures units without moving any ink, so a prior verdict whose content key no longer exists still applies to the current unit with the same ink_key. None when the unit's own configs disagree (no single signature to carry against)."""
    text = "".join(chr(int(part, 16)) for part in unit["codepoints"].split(":"))
    signatures = {comparator.signature(text, config) for config in unit["configs"]}
    if len(signatures) != 1:
        return None
    return (unit["codepoints"], signatures.pop())


def latest_verdicts(payload):
    best = {}
    for record in payload["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def check_source_stamps(root, verdict_file, payload):
    """Refuse a source pair whose stamps disagree: the verdicts' unit ids only mean anything on the surface they were recorded against, so resolving them against a different snapshot silently carries them onto the wrong windows (the qsEt cycle carried 589 that way before the mistake surfaced)."""
    recorded = payload.get("manifest_generated_at")
    held = json.loads((root / "manifest.json").read_text()).get("generated_at")
    if recorded != held:
        raise SystemExit(
            f"{verdict_file.name} is stamped {recorded}, but {root} holds the surface generated at {held}. "
            "These verdicts were recorded against a different surface, and resolving their unit ids here would carry them onto the wrong windows. "
            "Pair the file with the surface it was recorded on — the stamp-matching tmp/review-pre-* snapshot — and rerun."
        )


def main():
    parser = argparse.ArgumentParser(
        description="Re-resolve prior verdicts against the surfaces they were recorded on and carry them onto the live surface."
    )
    parser.add_argument(
        "--source",
        nargs=2,
        action="append",
        required=True,
        metavar=("SURFACE_DIR", "VERDICTS_JSON"),
        help="a prior surface directory and the verdicts file recorded against it; repeatable",
    )
    parser.add_argument("--out", default=str(OUT), help="output verdicts file (default: %(default)s)")
    parser.add_argument(
        "--current-surface",
        type=pathlib.Path,
        default=CURRENT_SURFACE,
        help="the freshly built surface to carry onto (default: the live review surface)",
    )
    args = parser.parse_args()
    sources = [(pathlib.Path(directory), pathlib.Path(verdicts)) for directory, verdicts in args.source]

    prior = {}
    surface_roots = {}
    for root, verdict_file in sources:
        payload = json.loads(verdict_file.read_text())
        check_source_stamps(root, verdict_file, payload)
        surface_roots[root.name] = root
        units_by_id = {u["id"]: u for u in load_surface(root)}
        used = 0
        for unit_id, record in latest_verdicts(payload).items():
            unit = units_by_id.get(unit_id)
            if unit is None:
                continue
            key = content_hash(unit)
            if key not in prior or record["at"] > prior[key][0]["at"]:
                prior[key] = (record, root.name, unit)
            used += 1
        print(f"{verdict_file.name}: {used} verdicts resolved against {root.name}")

    manifest = json.loads((args.current_surface / "manifest.json").read_text())
    current = load_surface(args.current_surface)
    human = [u for u in current if u.get("batch") is not None and not u.get("no_verdict")]

    key_by_id = {u["id"]: content_hash(u) for u in current}
    keys_seen = collections.Counter(key_by_id.values())
    collisions = {k for k, n in keys_seen.items() if n > 1}
    if collisions:
        raise SystemExit(
            f"{len(collisions)} content-key collisions on the current surface; refusing to carry"
        )

    carried = []
    kinds = collections.Counter()

    def carry(unit, record, source):
        provenance = f"[carried {record['unit']}@{source}, verdicted {record['at'][:10]}]"
        note = cap_markers(f"{provenance} {record['note']}".strip())
        carried.append({"unit": unit["id"], "verdict": record["verdict"], "note": note, "at": record["at"]})
        kinds[record["verdict"]] += 1

    unhit = []
    for unit in human:
        hit = prior.get(key_by_id[unit["id"]])
        if hit is None:
            unhit.append(unit)
            continue
        record, source, _prior_unit = hit
        if record["verdict"] == "skip":
            continue
        carry(unit, record, source)

    current_keys = set(keys_seen)
    stranded = [
        (record, source, unit)
        for key, (record, source, unit) in prior.items()
        if key not in current_keys and record["verdict"] != "skip"
    ]
    if stranded and unhit:
        current_comparator = surface_comparator(args.current_surface)
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
            print(
                f"{len(conflicts)} merged units had conflicting prior verdicts and were left unverdicted for a fresh look:"
            )
            for unit_id, matches in conflicts:
                sides = "; ".join(
                    f"{record['unit']}@{source}={record['verdict']}" for record, source in matches
                )
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
