"""Apply the checked-in standing approvals (rebuild/standing-approvals.yaml) to the live review surface: for every rule, find the blank human units whose before→after delta matches the rule's structural pattern — a pivot letter whose backward join drops as it ligates with its follower, with every other seam unchanged — and emit fill records for them into an importable verdicts file. This is the zero-touch sibling of echo_verdicts.py: echo fill extends the user's past verdicts to pixel-identical lookalikes, while a standing rule extends a recorded once-and-for-all decision to instances the user has never seen (new left letters minted by later migrations), so those units never queue. The guard list is the point of authoring a rule at all: a rule's except_left families are held for review, so the one context the user does want to see still reaches the docket. Records are stamped with the manifest's generated_at, so any human verdict beats a standing fill on merge, and a parked unit (a skip verdict) is not blank and is never filled. The artifact cycle runs this after the echo fill, with a merge_verdicts pass to land the file."""

import argparse
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.tools.echo_verdicts import latest_verdicts, load_units  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
RULES = ROOT / "rebuild/standing-approvals.yaml"
OUT = ROOT / "verdicts-standing-fill.json"
FORMAT = "ams-standing-approvals/1"
ALLOWED_VERDICTS = ("approve", "either")


def _fail(message):
    raise SystemExit(f"rebuild/standing-approvals.yaml: {message}")


def load_rules(path):
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        _fail(f"format must be {FORMAT!r}")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        _fail("rules must be a nonempty list")
    seen = set()
    for rule in rules:
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            _fail("every rule needs a nonempty string id")
        if rule_id in seen:
            _fail(f"duplicate rule id {rule_id!r}")
        seen.add(rule_id)
        if rule.get("verdict") not in ALLOWED_VERDICTS:
            _fail(f"rule {rule_id!r}: verdict must be one of {ALLOWED_VERDICTS}")
        if not isinstance(rule.get("note"), str) or not rule["note"]:
            _fail(f"rule {rule_id!r}: note must be a nonempty string")
        match = rule.get("match")
        if not isinstance(match, dict):
            _fail(f"rule {rule_id!r}: match must be a mapping")
        before = match.get("before")
        if not isinstance(before, dict) or not all(
            isinstance(before.get(key), str) and before[key]
            for key in ("pivot", "seam_into", "seam_out", "follower")
        ):
            _fail(f"rule {rule_id!r}: match.before needs pivot, seam_into, seam_out, follower")
        after = match.get("after")
        if not isinstance(after, dict) or not all(
            isinstance(after.get(key), str) and after[key] for key in ("ligature", "seam_into")
        ):
            _fail(f"rule {rule_id!r}: match.after needs ligature, seam_into")
        except_left = match.get("except_left", [])
        if not isinstance(except_left, list) or not all(
            isinstance(family, str) and family for family in except_left
        ):
            _fail(f"rule {rule_id!r}: match.except_left must be a list of family names")
    return rules


def _joining_family(glyph_name):
    return glyph_name.split(".", 1)[0].rsplit("_", 1)[-1]


def _matches(match, unit, *, guard=True):
    before, after = unit.get("before"), unit.get("after")
    if not before or not after:
        return False
    glyphs, seams = before["glyphs"], before["seams"]
    cells, after_seams = after["cells"], after["seams"]
    mb, ma = match["before"], match["after"]
    pivot = mb["pivot"]
    excluded = set(match.get("except_left", [])) if guard else set()
    for i in range(1, len(glyphs) - 1):
        name = glyphs[i]
        if name != pivot and not name.startswith(pivot + "."):
            continue
        if seams[i - 1] != mb["seam_into"] or seams[i] != mb["seam_out"]:
            continue
        if _joining_family(glyphs[i + 1]) != mb["follower"]:
            continue
        if _joining_family(glyphs[i - 1]) in excluded:
            continue
        for j in range(1, len(cells)):
            if cells[j].split("/", 1)[0] != ma["ligature"]:
                continue
            if after_seams[j - 1] != ma["seam_into"]:
                continue
            if seams[: i - 1] == after_seams[: j - 1] and seams[i + 1 :] == after_seams[j:]:
                return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split(":")[0] + ".")
    parser.add_argument(
        "verdicts", help="the verdicts file that defines blankness (an export or the autosave)"
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(RULES))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    data = json.loads(pathlib.Path(args.verdicts).read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    rules = load_rules(pathlib.Path(args.rules))
    records = latest_verdicts(pathlib.Path(args.verdicts))
    units = [
        unit
        for unit in load_units(surface)
        if not unit.get("no_verdict") and len(unit.get("render_groups") or []) == 1
    ]

    fills = []
    lines = []
    for rule in rules:
        matched = [unit for unit in units if _matches(rule["match"], unit)]
        held = [
            unit
            for unit in units
            if _matches(rule["match"], unit, guard=False) and not _matches(rule["match"], unit)
        ]
        blanks = [unit for unit in matched if unit["id"] not in records]
        note = f"[standing: {rule['id']}] {rule['note']}"
        for unit in blanks:
            fills.append(
                {
                    "unit": unit["id"],
                    "verdict": rule["verdict"],
                    "note": note,
                    "at": manifest["generated_at"],
                }
            )
        lines.append(
            f"  {rule['id']}: {len(blanks)} filled, {len(matched) - len(blanks)} already verdicted, "
            f"{len(held)} held for review by except_left"
        )

    fills.sort(key=lambda record: record["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": fills,
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        f"wrote {out.name}: {len(fills)} standing-approval verdicts onto manifest {manifest['generated_at']}"
    )
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
