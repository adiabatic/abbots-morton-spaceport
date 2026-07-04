"""The single source of truth for the review-surface census pins (rebuild/review-census-pins.json). Both the tests and the artifact-cycle regenerator call the functions here so the checked-in pins and the assertions can never drift.

Three grains coexist and must not be conflated. The manifest and built groups are read from a built surface (post-merge, after the ink-duplicate fold); the audit, ink, and families groups are computed from the source inputs (TSV + ledger + fonts + spec) at the pre-merge name grain, never from the surface.

Usage:
    uv run python -m rebuild.review.census            # --check against the checked-in pins
    uv run python -m rebuild.review.census --update    # recompute and rewrite the pins file
    uv run python -m rebuild.review.census --check --surface rebuild/out/review
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
import warnings
from pathlib import Path

from rebuild.review.audit import (
    Unit,
    _config_index,
    assign_batches,
    group_for,
    load_audit,
    load_workload,
    parse_codepoints,
    render_groups_for_rows,
)
from rebuild.review.enrich import LETTERS, Enricher, load_spec
from rebuild.review.families import FAMILY_ORDER, assign_family
from rebuild.review.ink import InkComparator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO_ROOT / "rebuild" / "review-census-pins.json"

AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
SUBSET_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

CLASS_UNIT_COUNT_KEYS = ("boundary-echo", "dangling-anchor-dropped", "bare-name-live-join")


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def manifest_group(manifest: dict) -> dict:
    """The post-merge facts read straight from the built surface's manifest.json."""
    by_id = {meta["id"]: meta for meta in manifest["classes"]}
    return {
        "totals": dict(manifest["totals"]),
        "classes_count": len(manifest["classes"]),
        "machine_approved": {
            "units": manifest["machine_approved"]["units"],
            "by_class": dict(manifest["machine_approved"]["by_class"]),
        },
        "class_unit_count": {key: by_id[key]["unit_count"] for key in CLASS_UNIT_COUNT_KEYS},
        "secondary_seams": dict(manifest["secondary_seams"]),
    }


def built_group(out_dir: Path, manifest: dict) -> dict:
    """The post-merge facts computed by walking the surface's unit shards — the human-workload size and the config-note histogram, neither of which is a manifest key."""
    out_dir = Path(out_dir)
    human_units = 0
    distribution: dict[str | None, int] = {}
    for meta in manifest["classes"]:
        for unit in json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8")):
            if unit["batch"] is not None:
                human_units += 1
            note = unit["config_note"]
            distribution[note] = distribution.get(note, 0) + 1
    return {
        "human_units": human_units,
        "config_note_distribution": _encode_note_distribution(distribution),
    }


def _encode_note_distribution(distribution: dict[str | None, int]) -> list[list]:
    """JSON forbids a null object key, so the config-note histogram is stored as a list of [note, count] pairs, null first then lexicographic."""
    return [
        [note, distribution[note]]
        for note in sorted(distribution, key=lambda note: (note is not None, note or ""))
    ]


def _decode_note_distribution(pairs) -> dict[str | None, int]:
    return {note: count for note, count in pairs}


def audit_group(repo_root: Path = REPO_ROOT) -> dict:
    """The pre-merge name-grain audit facts: the raw row count and the deduped unit count, cheap (no shaping)."""
    workload = load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))
    return {"row_count": workload.row_count, "units": len(workload.units)}


def ink_histogram(workload, comparator) -> dict:
    """The kern-neutral ink census over the pre-merge workload: flag every unit whose placed ink is identical in both fonts under every config in its set, tally the machine-approved units per class, assign batches, and count the boundary-echo no-verdict exemptions and the human workload. Mutates `workload` in place (sets ink_identical and batch), exactly as the census reference does."""
    machine_by_class: dict[str, int] = {}
    for unit in workload.units:
        if comparator.ink_identical(_text(unit), unit.configs):
            unit.ink_identical = True
            machine_by_class[unit.class_id] = machine_by_class.get(unit.class_id, 0) + 1
    batches = assign_batches(workload.units)
    machine_total = sum(machine_by_class.values())
    exempt = [unit for unit in workload.units if unit.no_verdict and not unit.ink_identical]
    human = [unit for unit in workload.units if not unit.ink_identical and not unit.no_verdict]
    return {
        "machine_total": machine_total,
        "non_identical": len(workload.units) - machine_total,
        "by_class": machine_by_class,
        "boundary_echo_exempt": len(exempt),
        "human_units": len(human),
        "batches": batches,
    }


def ink_group(repo_root: Path = REPO_ROOT) -> dict:
    workload = load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))
    comparator = InkComparator(BEFORE_FONT, AFTER_FONT)
    return ink_histogram(workload, comparator)


def family_assignments(repo_root: Path = REPO_ROOT) -> list[str]:
    """Assign every UNMATCHED window (pre-merge name grain) to its verdict family: load the audit, group by (codepoints, baseline, new) triple, enrich each triple whose class is UNMATCHED under any config, and run the seam-gain/seam-loss discriminator. Returns the family label per window in iteration order."""
    rows = load_audit(AUDIT_PATH)
    by_triple: dict[tuple, list] = {}
    for row in rows:
        by_triple.setdefault((row.codepoints, row.baseline, row.new), []).append(row)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(repo_root)
    enricher = Enricher(spec, SUBSET_DIR, AFTER_FONT, repo_root=repo_root, before_font=BEFORE_FONT)
    out: list[str] = []
    for (codepoints, baseline, new), members in by_triple.items():
        if not any(member.matched_entry == "UNMATCHED" for member in members):
            continue
        config_classes = {member.config: member.matched_entry for member in members}
        ordered = tuple(sorted(members, key=lambda member: _config_index(member.config)))
        unit = Unit(
            codepoints=codepoints,
            baseline=baseline,
            new=new,
            class_id="UNMATCHED",
            rows=ordered,
            configs=tuple(member.config for member in ordered),
            kinds=tuple(sorted({kind for member in members for kind in member.kinds})),
            group=group_for(parse_codepoints(codepoints), dict(LETTERS)),
            render_groups=render_groups_for_rows(ordered),
            config_classes=config_classes,
        )
        out.append(assign_family(enricher.enrich(unit)))
    return out


def family_census(assignments: list[str]) -> dict[str, int]:
    census: dict[str, int] = {}
    for family in assignments:
        census[family] = census.get(family, 0) + 1
    order = {family: index for index, family in enumerate(FAMILY_ORDER)}
    return dict(sorted(census.items(), key=lambda item: (order.get(item[0], len(order)), item[0])))


def families_group(repo_root: Path = REPO_ROOT) -> dict:
    census = family_census(family_assignments(repo_root))
    return {"census": census, "total": sum(census.values())}


@contextlib.contextmanager
def _build_or_load_surface(surface: Path | None):
    """Yield (out_dir, manifest). With --surface, read the given built surface read-only; otherwise build a fresh surface into a self-cleaning temp directory under the project tmp/ so the pins can never describe a stale surface and no multi-MB scratch surface is left behind."""
    if surface is not None:
        out_dir = Path(surface)
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        yield out_dir, manifest
        return
    from rebuild.review.build import build_m1

    scratch = REPO_ROOT / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ams-census-surface-", dir=scratch) as temp:
        out_dir = Path(temp)
        manifest = build_m1(out_dir)
        yield out_dir, manifest


def compute_pins(surface: Path | None = None, repo_root: Path = REPO_ROOT) -> dict:
    with _build_or_load_surface(surface) as (out_dir, manifest):
        return {
            "_surface": {
                "generated_at": manifest["generated_at"],
                "repo_head": manifest["repo_head"],
            },
            "manifest": manifest_group(manifest),
            "built": built_group(out_dir, manifest),
            "audit": audit_group(repo_root),
            "ink": ink_group(repo_root),
            "families": families_group(repo_root),
        }


def load_pins(path: Path = PINS_PATH) -> dict:
    """The checked-in pins, with the config-note histogram decoded back to a dict whose null key is the always-covered common case."""
    pins = json.loads(Path(path).read_text(encoding="utf-8"))
    pins["built"]["config_note_distribution"] = _decode_note_distribution(
        pins["built"]["config_note_distribution"]
    )
    return pins


def _dumps(pins: dict) -> str:
    return json.dumps(pins, indent=2) + "\n"


def _flatten(obj, prefix: str, out: dict) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    else:
        out[prefix] = obj


def _mismatches(old: dict, new: dict) -> list[tuple[str, object, object]]:
    """Per-key mismatches over the census-data groups, ignoring the descriptive _surface block (its generated_at/repo_head legitimately vary with the surface that produced the pins)."""
    old_flat: dict = {}
    new_flat: dict = {}
    _flatten({key: value for key, value in old.items() if key != "_surface"}, "", old_flat)
    _flatten({key: value for key, value in new.items() if key != "_surface"}, "", new_flat)
    keys = sorted(set(old_flat) | set(new_flat))
    return [(key, old_flat.get(key), new_flat.get(key)) for key in keys if old_flat.get(key) != new_flat.get(key)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="recompute and compare against the checked-in pins (default)")
    action.add_argument("--update", action="store_true", help="recompute and rewrite the pins file")
    parser.add_argument(
        "--surface",
        type=Path,
        default=None,
        help="reuse an existing built surface directory (read-only); default builds a fresh one in a temp directory",
    )
    args = parser.parse_args(argv)

    new = compute_pins(args.surface)
    if args.update:
        PINS_PATH.write_text(_dumps(new), encoding="utf-8")
        print(f"Wrote {PINS_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 0

    if not PINS_PATH.exists():
        print(f"{PINS_PATH} is missing — run --update first", file=sys.stderr)
        return 1
    old = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    mismatches = _mismatches(old, new)
    if mismatches:
        print("census pins are stale:", file=sys.stderr)
        for key, old_value, new_value in mismatches:
            print(f"  {key}: pinned {old_value!r} != computed {new_value!r}", file=sys.stderr)
        print("Re-baseline with: uv run python -m rebuild.review.census --update", file=sys.stderr)
        return 1
    print("census pins are current.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
