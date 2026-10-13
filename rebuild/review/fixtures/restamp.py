"""Stamp the hand-written contract fixture beside this file after a unit is added or an edit moves a unit's `content_key`.

A unit's id is `unit_cache.unit_id_for` of its `content_key`, and the key is `unit_cache.carry_content_hash` of every field outside `CARRY_PRESENTATION_KEYS`. So hand-editing a judged field, renaming a term the units carry, or changing the projection itself gives the unit a new id, and every place that names the old one goes stale. This recomputes every unit's key and id, keeps each shard sorted by id, rewrites the manifest's `human_unit_ids` in `audit.triage_key` order and each class's `batches` along it, and replaces each old id, as a whole token, in every other file git can see outside `mini/`: the README, the Python tests, and the node tests. A new unit's placeholder id is replaced the same way. Every id the stamp does not cover (`secondary_junctions`, `drafts`) is mapped too, so one pass settles the fixture. A run with nothing stale writes nothing.

    uv run python rebuild/review/fixtures/restamp.py

Then run the gates the README's "Growing it" section names.
"""

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
from rebuild.review.audit import family_ranks, parse_codepoints, triage_key  # noqa: E402
from rebuild.review.enrich import LETTERS  # noqa: E402
from rebuild.review.unit_cache import carry_content_hash, is_content_id, unit_id_for  # noqa: E402

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


def restamp() -> tuple[dict[str, str], list[str]]:
    """Re-stamp the fixture in place. Return each changed id mapped to its replacement, and the files outside the fixture that named an old id."""
    shards = {path: _read(path) for path in sorted(UNITS.glob("*.json"))}
    renames: dict[str, str] = {}
    for units in shards.values():
        for unit in units:
            key = carry_content_hash({name: value for name, value in unit.items() if name != "content_key"})
            if unit.get("content_key") != key:
                unit["content_key"] = key
                renames[unit["id"]] = unit_id_for(key)
    if not renames:
        return renames, []
    elsewhere = _renamed_elsewhere(renames)
    by_id: dict[str, dict] = {}
    for path, units in shards.items():
        units = sorted(_renamed(units, renames), key=lambda unit: unit["id"])
        by_id.update((unit["id"], unit) for unit in units)
        _write(path, units)
    manifest = _read(MANIFEST)
    class_index = {meta["id"]: index for index, meta in enumerate(manifest["classes"])}
    rank = family_ranks(dict(LETTERS))
    human_unit_ids = sorted(
        _renamed(manifest["human_unit_ids"], renames),
        key=lambda unit_id: triage_key(
            class_index[by_id[unit_id]["class"]],
            by_id[unit_id].get("group") or "",
            parse_codepoints(by_id[unit_id]["codepoints"]),
            unit_id,
            rank,
        ),
    )
    manifest["human_unit_ids"] = human_unit_ids
    for meta in manifest["classes"]:
        meta["batches"] = sorted(
            {
                position // manifest["batch_size"]
                for position, unit_id in enumerate(human_unit_ids)
                if by_id[unit_id]["class"] == meta["id"]
            }
        )
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
