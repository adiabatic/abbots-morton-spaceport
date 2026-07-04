import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNITS_PATH = REPO / "rebuild/out/review/units/ss10-isolation-completed.json"
VERDICTS_PATH = REPO / "verdicts-06.29.03PM.json"
MANIFEST_PATH = REPO / "rebuild/out/review/manifest.json"
OUTPUT_PATH = REPO / "verdicts-ss10-auto.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    units = json.loads(UNITS_PATH.read_text())
    by_id = {u["id"]: u for u in units}

    verdicts_doc = json.loads(VERDICTS_PATH.read_text())
    already_verdicted = {v["unit"] for v in verdicts_doc["verdicts"]}
    verdicts_manifest_generated_at = verdicts_doc["manifest_generated_at"]

    manifest_doc = json.loads(MANIFEST_PATH.read_text())
    manifest_generated_at = manifest_doc["generated_at"]

    if verdicts_manifest_generated_at != manifest_generated_at:
        print("!!! LOUD WARNING: manifest_generated_at MISMATCH !!!")
        print(f"  verdicts file manifest_generated_at: {verdicts_manifest_generated_at!r}")
        print(f"  manifest.json generated_at:          {manifest_generated_at!r}")

    records = []
    skipped = []
    for uid, unit in by_id.items():
        if uid in already_verdicted:
            continue

        cells = unit["after"]["cells"]
        ligature_runes = []
        for cell in cells:
            rune = cell.split("/", 1)[0]
            if "_" in rune:
                ligature_runes.append(rune)

        if not ligature_runes:
            note = "auto: clean ss10 isolation (no ligature)"
        elif all(r == "qsTea_qsOy" for r in ligature_runes):
            note = "auto: ss10 isolation, ·Tea·Oy ligature off the judged seam"
        else:
            skipped.append(uid)
            continue

        records.append({"unit": uid, "verdict": "approve", "note": note, "at": now_iso()})

    records.sort(key=lambda r: r["unit"])
    skipped.sort()

    output = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": verdicts_manifest_generated_at,
        "exported_at": now_iso(),
        "verdicts": records,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")

    for rec in records:
        assert rec["unit"] not in already_verdicted

    print(f"emitted: {len(records)}")
    print(f"skipped ({len(skipped)}): {skipped}")
    print("counts by note:")
    for note, count in sorted(Counter(r["note"] for r in records).items()):
        print(f"  {count}  {note}")


if __name__ == "__main__":
    main()
