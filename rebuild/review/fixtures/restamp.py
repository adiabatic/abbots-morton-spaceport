"""Stamp the hand-written contract fixture beside this file after a unit is added or edited.

A unit's id is `unit_ids.unit_id_for` of its `content_key`, and a fixture unit's key is `fixture_identity`: the sha256 of what the unit shows, the fields `diff_units.unit_identity` reads for a built unit (its kinds, its row key, its configurations, its code points and both runs' glyph names). So editing one of those gives the unit a new id, and every place that names the old one goes stale. This recomputes every unit's key and id, keeps each shard sorted by id, rewrites every file outside the fixture that names an old id, and recomputes the manifest's triage index (`human_unit_ids`, in `audit.triage_key` order), each class's `batches`, unit and machine-approved counts, and the totals and the machine-approval record those counts give. Row counts are hand-set and left alone. A new unit can carry any id no other file contains, as a placeholder.

    uv run python rebuild/review/fixtures/restamp.py

Then run the gates the README's "Growing it" section names.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from rebuild.review.audit import (  # noqa: E402
    MACHINE_CHECKS,
    family_ranks,
    machine_approved,
    needs_no_verdict,
    parse_codepoints,
    triage_key,
)
from rebuild.review.enrich import LETTERS  # noqa: E402
from rebuild.review.unit_ids import is_content_id, unit_id_for  # noqa: E402

MANIFEST = HERE / "manifest.json"
UNITS = HERE / "units"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")


def _renamed(value: Any, renames: dict[str, str]) -> Any:
    if isinstance(value, str):
        return renames.get(value, value)
    if isinstance(value, list):
        return [_renamed(item, renames) for item in value]
    if isinstance(value, dict):
        return {key: _renamed(item, renames) for key, item in value.items()}
    return value


def fixture_identity(unit: dict) -> str:
    """Return a fixture unit's content key: the sha256 of its kinds, row key, configurations, code points and both runs' glyph names, the parts of `diff_units.unit_identity` a hand-written unit carries."""
    record = {
        "format": "table-diff/1",
        "kinds": sorted(unit.get("kinds") or ()),
        "row_key": unit.get("row_key"),
        "configs": list(unit.get("configs") or ()),
        "codepoints": unit.get("codepoints"),
        "before": list((unit.get("before") or {}).get("glyphs") or ()),
        "after": list((unit.get("after") or {}).get("cells") or ()),
    }
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def restamp() -> tuple[dict[str, str], list[str]]:
    """Re-stamp the fixture in place. Return each changed id mapped to its replacement, and the files outside the fixture that named an old id."""
    shards = {path: _read(path) for path in sorted(UNITS.glob("*.json"))}
    renames: dict[str, str] = {}
    for units in shards.values():
        for unit in units:
            key = fixture_identity(unit)
            unit["content_key"] = key
            if unit["id"] != unit_id_for(key):
                renames[unit["id"]] = unit_id_for(key)
    elsewhere = _renamed_elsewhere(renames) if renames else {}
    by_id: dict[str, dict] = {}
    for path, units in shards.items():
        units = sorted(_renamed(units, renames), key=lambda unit: unit["id"])
        by_id.update((unit["id"], unit) for unit in units)
        _write(path, units)
    manifest = _read(MANIFEST)
    class_index = {meta["id"]: index for index, meta in enumerate(manifest["classes"])}
    rank = family_ranks(dict(LETTERS))
    human_unit_ids = sorted(
        (unit_id for unit_id, unit in by_id.items() if not needs_no_verdict(unit)),
        key=lambda unit_id: triage_key(
            class_index[by_id[unit_id]["class"]],
            by_id[unit_id].get("group") or "",
            parse_codepoints(by_id[unit_id]["codepoints"]) if by_id[unit_id]["codepoints"] else (),
            unit_id,
            rank,
        ),
    )
    manifest["human_unit_ids"] = human_unit_ids
    batch_size = manifest["batch_size"]
    by_class: dict[str, int] = {}
    checks = {check_id: 0 for check_id in MACHINE_CHECKS}
    for meta in manifest["classes"]:
        units = [unit for unit in by_id.values() if unit["class"] == meta["id"]]
        meta["unit_count"] = len(units)
        meta["machine_approved_count"] = sum(1 for unit in units if machine_approved(unit))
        meta["machine_checks"] = {
            check_id: sum(1 for unit in units if unit.get(check_id) is True) for check_id in MACHINE_CHECKS
        }
        meta["batches"] = sorted(
            {
                position // batch_size
                for position, unit_id in enumerate(human_unit_ids)
                if by_id[unit_id]["class"] == meta["id"]
            }
        )
        if meta["machine_approved_count"]:
            by_class[meta["id"]] = meta["machine_approved_count"]
        for check_id, count in meta["machine_checks"].items():
            checks[check_id] += count
    manifest["totals"]["units"] = len(by_id)
    manifest["totals"]["rows"] = sum(meta["row_count"] for meta in manifest["classes"])
    manifest["totals"]["batches"] = (len(human_unit_ids) + batch_size - 1) // batch_size
    machine = manifest["machine_approved"]
    machine["units"] = sum(by_class.values())
    machine["rows"] = sum(meta["row_count"] for meta in manifest["classes"] if meta["id"] in by_class)
    machine["by_class"] = by_class
    for check_id, count in checks.items():
        machine["checks"][check_id]["units"] = count
        machine["checks"][check_id]["rows"] = count
    _write(MANIFEST, manifest)
    for path, text in elsewhere.items():
        path.write_text(text, encoding="utf-8")
    return renames, [str(path.relative_to(REPO_ROOT)) for path in elsewhere]


def _renamed_elsewhere(renames: dict[str, str]) -> dict[Path, str]:
    """Return the new text of every file outside the fixture that names an old id. A placeholder that is not a content id must name only the new unit, so one that another file already contains stops the run before anything is written."""
    patterns = [arg for old in renames for arg in ("-e", old)]
    listed = subprocess.run(
        [
            "git",
            "grep",
            "--untracked",
            "-l",
            "-F",
            *patterns,
            "--",
            ".",
            ":!rebuild/review/fixtures/mini",
            ":!rebuild/review/fixtures/units",
            ":!rebuild/review/fixtures/manifest.json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if listed.returncode not in (0, 1):
        raise SystemExit(f"git grep failed: {listed.stderr.strip()}")
    old_ids = re.compile(rf"(?<![\w-])({'|'.join(map(re.escape, renames))})(?![\w-])")
    rewritten: dict[Path, str] = {}
    for name in listed.stdout.splitlines():
        path = REPO_ROOT / name
        text = path.read_text(encoding="utf-8")
        for match in old_ids.finditer(text):
            if not is_content_id(match.group()):
                raise SystemExit(
                    f"{name} already contains the placeholder {match.group()}; pick one no other file contains"
                )
        renamed = old_ids.sub(lambda match: renames[match.group()], text)
        if renamed != text:
            rewritten[path] = renamed
    return rewritten


def main() -> None:
    renames, rewritten = restamp()
    if not renames:
        print("every fixture unit's stamp and id are current")
        return
    for old, new in renames.items():
        print(f"{old} -> {new}")
    for name in rewritten:
        print(f"rewrote {name}")


if __name__ == "__main__":
    main()
