import collections
import json
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_SURFACE = ROOT / "rebuild/out/review"
SURFACE_ARCHIVES = ROOT / "rebuild/evidence/surfaces"
SOURCES = [
    (ROOT / "tmp/review-baseline", ROOT / "verdicts-11.33.00PM.json", None),
    (ROOT / "tmp/review-preview-bd1", ROOT / "verdicts-12.04.13AM.json", "2026-07-03"),
]
PRESENTATION_KEYS = {"id", "batch", "no_verdict", "exemplar", "explain", "drafts", "provenance"}


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


def latest_verdicts(path):
    best = {}
    for record in json.loads(path.read_text())["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def main():
    prior = {}
    for surface_root, verdict_file, at_floor in SOURCES:
        units_by_id = {u["id"]: u for u in load_surface(ensure_surface(surface_root))}
        used = 0
        for unit_id, record in latest_verdicts(verdict_file).items():
            if at_floor is not None and record["at"] < at_floor:
                continue
            unit = units_by_id.get(unit_id)
            if unit is None:
                continue
            key = content_key(unit)
            if key not in prior or record["at"] > prior[key][0]["at"]:
                prior[key] = (record, surface_root.name)
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
    for unit in human:
        hit = prior.get(content_key(unit))
        if hit is None:
            continue
        record, source = hit
        if record["verdict"] == "skip":
            continue
        provenance = f"[carried {record['unit']}@{source}, verdicted {record['at'][:10]}]"
        note = f"{provenance} {record['note']}".strip()
        carried.append({"unit": unit["id"], "verdict": record["verdict"], "note": note, "at": record["at"]})
        kinds[record["verdict"]] += 1

    carried.sort(key=lambda r: r["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": carried,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {OUT.name}: {len(carried)} carried onto manifest {manifest['generated_at']}")
    print(f"kinds: {dict(kinds)}")
    print(f"human queue: {len(human)} -> {len(human) - len(carried)} still needing fresh verdicts")


if __name__ == "__main__":
    main()
