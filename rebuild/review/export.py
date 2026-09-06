"""The verdicts-to-triage-YAML CLI (rebuild/REVIEW-PLAN.md §4.2): join an exported verdicts.json to the built review directory's units, re-validate every selected draft, and write one triage YAML with five sections (pins, policy_edits, any_of, neither, identical) for human placement. Nothing is auto-applied to the corpus or the rune files.

Usage: uv run python -m rebuild.review.export verdicts.json --out tmp/review-triage.yaml
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

from rebuild.review import unit_index
from rebuild.review.audit import SLIM_OMITTED_KEYS, machine_approved, slim_fragment

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_REVIEW_DIR = REPO_ROOT / "rebuild" / "out" / "review"

VERDICTS_FORMAT = "ams-review-verdicts/1"
VERDICT_VALUES = ("approve", "reject", "either", "identical", "neither", "skip")


TRIAGE_KEYS = (
    "id",
    "class",
    "batch",
    "no_verdict",
    "configs",
    "ink_identical",
    "picture_identical",
    "junior_equivalent",
    "codepoints",
    "text_entities",
    "notation",
    "provenance",
    "drafts",
)


def _triage_projection(unit: dict, shard: str, *, batch: int | None = None) -> dict:
    """One shard unit narrowed to the fields the triage YAML is written from. A key this file reads but the shard does not carry is a build and this reader disagreeing about the unit shape, which as a silent `.get(...) or None` writes a triage YAML that is wrong rather than missing — so it stops here instead, naming the key. `TRIAGE_KEYS` is the export's read-set exactly and not a safe-to-pad allowlist in either direction: a key declared but never read costs a refusal on any surface that legitimately omits it, and a key read but never declared is the null this refusal exists to prevent. Two keys are read from somewhere other than the fragment: `batch` is the manifest's triage index speaking (`unit_index.slot_reader`), handed in by the caller, since a fragment carries no batch; and a slim fragment's (`audit.slim_fragment`) omitted keys — a machine-approved or exempt unit ships without `drafts`, and the export never drafts from one, its verdicts being counted as inert history — are read as null there rather than refused. `test_load_units_keeps_exactly_the_fields_the_triage_export_reads` names the set independently, and `test_export_round_trip` runs `build_triage` over the projection so the reads and the declaration are held together."""
    omitted = set(SLIM_OMITTED_KEYS) if slim_fragment(unit) else set()
    missing = [key for key in TRIAGE_KEYS if key != "batch" and key not in unit and key not in omitted]
    if missing:
        raise SystemExit(
            f"{shard}: unit {unit.get('id')} carries no {', '.join(missing)}; "
            "the triage export reads that field, so this surface was built by a version this reader does not understand"
        )
    return {
        key: batch if key == "batch" else unit.get(key) if key in omitted else unit[key]
        for key in TRIAGE_KEYS
    }


def load_units(review_dir: Path) -> tuple[dict, dict[str, dict]]:
    """The manifest and every unit on the surface, narrowed to `TRIAGE_KEYS` one shard part at a time, each unit's batch read off the manifest's triage index. The corpus runs to gigabytes, of which the triage export reads under a third — `explain` alone is two fifths of it and nothing here opens it — so each part is released before the next is parsed and only the projection is kept."""
    manifest = json.loads((review_dir / "manifest.json").read_text(encoding="utf-8"))
    slot = unit_index.slot_reader(review_dir)
    units: dict[str, dict] = {}
    for meta in manifest.get("classes", ()):
        for part in unit_index.class_shards(meta):
            shard = json.loads((review_dir / part).read_text(encoding="utf-8"))
            for unit in shard:
                units[unit["id"]] = _triage_projection(unit, part, batch=slot(unit)["batch"])
            del shard
    return manifest, units


def load_verdicts(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("format") != VERDICTS_FORMAT:
        raise SystemExit(f"{path}: format {payload.get('format')!r}, expected {VERDICTS_FORMAT!r}")
    for record in payload.get("verdicts", ()):
        if record.get("verdict") not in VERDICT_VALUES:
            raise SystemExit(f"{path}: unknown verdict {record.get('verdict')!r} on {record.get('unit')}")
    return payload


def _reparse_status(expect: str) -> str:
    """Re-run the repo's real parser on an expect string when the test tree is importable; otherwise carry the generation-time status."""
    try:
        from rebuild.review.drafts import _import_test_shaping

        ts = _import_test_shaping()
        ts.parse_expect(expect)
        return "pass"
    except ValueError as error:
        return f"fail: {error}"
    except Exception:  # noqa: BLE001 — the corpus parser is optional at export time
        return "unavailable"


def rows_covered(unit: dict) -> int:
    """A verdict always covers the whole unit — every config its audit rows carry."""
    return len(unit.get("configs", ()))


def _machine_unit_ids(unit_ids: list[str]) -> list[str]:
    """The machine-approved ids in id order — content ids have no runs to collapse, so the list is the list."""
    return sorted(unit_ids)


def machine_approved_section(manifest: dict, units: dict[str, dict]) -> dict:
    """The triage YAML's machine_approved record: machine-verdicted units (ink-identical, picture-identical, or junior-equivalent) are reported as counts, per-class counts, the verification method, and their ids — never as drafted pins, which remain a human-verdict artifact."""
    machine = [unit for unit in units.values() if machine_approved(unit)]
    by_class: dict[str, int] = {}
    for unit in machine:
        by_class[unit["class"]] = by_class.get(unit["class"], 0) + 1
    meta = manifest.get("machine_approved") or {}
    return {
        "count": len(machine),
        "rows_covered": sum(rows_covered(unit) for unit in machine),
        "by_class": by_class,
        "method": meta.get("method")
        or "Both fonts shape and place identical outlines for these units under every config in their sets.",
        "unit_ids": _machine_unit_ids([unit["id"] for unit in machine]),
    }


def build_triage(manifest: dict, units: dict[str, dict], verdicts: dict) -> dict:
    counts = {"approve": 0, "reject": 0, "either": 0, "identical": 0, "neither": 0, "skip": 0}
    covered = 0
    pins: list[dict] = []
    policy_edits: list[dict] = []
    any_of: list[dict] = []
    neither: list[dict] = []
    identical: list[dict] = []
    missing: list[str] = []
    exempt: list[str] = []
    machine_exempt: list[str] = []

    if verdicts.get("manifest_generated_at") not in (None, manifest.get("generated_at")):
        print(
            f"warning: verdicts were exported against manifest {verdicts.get('manifest_generated_at')}, "
            f"the loaded manifest is {manifest.get('generated_at')}",
            file=sys.stderr,
        )

    for record in verdicts.get("verdicts", ()):
        unit = units.get(record.get("unit", ""))
        if unit is None:
            missing.append(record.get("unit", "<missing>"))
            continue
        if unit.get("no_verdict"):
            exempt.append(unit["id"])
            continue
        if machine_approved(unit):
            machine_exempt.append(unit["id"])
            continue
        verdict = record["verdict"]
        counts[verdict] += 1
        covered += rows_covered(unit)
        note = record.get("note") or ""
        drafts = unit.get("drafts") or {}

        if verdict == "approve" and drafts.get("pin"):
            pin = drafts["pin"]
            pins.append(
                {
                    "unit": unit["id"],
                    "codepoints": unit.get("codepoints"),
                    "text_entities": unit.get("text_entities"),
                    "expect": pin["expect"],
                    "attribute": pin["attribute"],
                    "stylistic_set": pin["stylistic_set"],
                    "validated": {
                        "syntax": _reparse_status(pin["expect"]),
                        "semantics_after_font": pin["semantics_after_font"],
                    },
                    "suggested_home": pin["suggested_home"],
                    "duplicate_of": pin["duplicate_of"],
                    "note": note,
                }
            )
        elif verdict == "reject":
            policy = drafts.get("policy")
            if policy:
                why_stub = policy["why_stub"] + (f": {note}" if note else "")
                policy_edits.append(
                    {
                        "unit": unit["id"],
                        "codepoints": unit.get("codepoints"),
                        "file": policy["file"],
                        "keypath": policy["keypath"],
                        "suggested_record": policy["suggested_record"],
                        "names_provenance": policy["names_provenance"],
                        "decided_stage": policy["decided_stage"],
                        "why_stub": why_stub,
                        "schema_valid": policy["schema_valid"],
                    }
                )
            else:
                why_stub = (
                    f"Reviewer rejected the new outcome for {unit.get('codepoints') or unit.get('notation')} ({unit.get('notation')})"
                    + (f": {note}" if note else "")
                )
                policy_edits.append(
                    {
                        "unit": unit["id"],
                        "codepoints": unit.get("codepoints"),
                        "file": None,
                        "keypath": None,
                        "suggested_record": None,
                        "names_provenance": unit.get("provenance", []),
                        "decided_stage": None,
                        "why_stub": why_stub,
                        "schema_valid": None,
                        "no_mechanical_draft": "the divergence has no one-line counter-lever (name-grain locked twin, bind pullback, or suppressed extension); start from names_provenance and the unit's explain panel",
                    }
                )
        elif verdict == "either" and drafts.get("any_of"):
            record_any = drafts["any_of"]
            any_of.append(
                {
                    "unit": unit["id"],
                    "text": record_any["text"],
                    "features": record_any["features"],
                    "candidates": record_any["candidates"],
                    "candidates_parse": [_reparse_status(c) for c in record_any["candidates"]],
                    "realized_as": "_assert_expect_any",
                    "note": note,
                }
            )
        elif verdict == "neither":
            # Neither behavior is right: no pin, no policy edit, no any-of is drafted — the unit needs follow-up authoring work, so it carries only the reviewer's note and the provenance records that are the follow-up author's levers.
            neither.append(
                {
                    "unit": unit["id"],
                    "codepoints": unit.get("codepoints"),
                    "notation": unit.get("notation"),
                    "note": note,
                    "names_provenance": unit.get("provenance", []),
                }
            )
        elif verdict == "identical":
            # The reviewer cannot see the flagged difference: nothing is drafted — these claims are signal for the ink-comparator and highlight tooling, which flagged a difference no human can spot.
            identical.append(
                {
                    "unit": unit["id"],
                    "codepoints": unit.get("codepoints"),
                    "notation": unit.get("notation"),
                    "note": note,
                }
            )

    if missing:
        print(f"warning: {len(missing)} verdicts reference unknown units: {missing[:5]}", file=sys.stderr)
    if exempt:
        print(
            f"warning: {len(exempt)} verdicts land on no-verdict units and are inert history: {exempt[:5]}",
            file=sys.stderr,
        )
    if machine_exempt:
        print(
            f"warning: {len(machine_exempt)} verdicts land on machine-approved units and are inert history: "
            f"{machine_exempt[:5]}",
            file=sys.stderr,
        )

    machine = machine_approved_section(manifest, units)
    review = {
        "mode": manifest.get("mode"),
        "source": manifest.get("source"),
        "manifest_generated_at": manifest.get("generated_at"),
        "exported_at": datetime.datetime.now(tz=datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        # rows_covered counts human-verdict rows only; the machine-approved units' rows are reported separately under machine_approved.rows_covered.
        "counts": {
            **counts,
            "units_total": len(units),
            "human_units_total": sum(1 for unit in units.values() if unit.get("batch") is not None),
            "skipped_no_verdict": len(exempt),
            "skipped_machine_approved": len(machine_exempt),
            "rows_covered": covered,
        },
    }
    return {
        "review": review,
        "machine_approved": machine,
        "pins": pins,
        "policy_edits": policy_edits,
        "any_of": any_of,
        "neither": neither,
        "identical": identical,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verdicts", type=Path)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tmp" / "review-triage.yaml")
    args = parser.parse_args(argv)

    manifest, units = load_units(args.review_dir)
    verdicts = load_verdicts(args.verdicts)
    triage = build_triage(manifest, units, verdicts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump(triage, sort_keys=False, allow_unicode=True, width=10**6), encoding="utf-8"
    )
    counts = triage["review"]["counts"]
    machine = triage["machine_approved"]
    print(
        f"Wrote {args.out} (pins {len(triage['pins'])}, policy edits {len(triage['policy_edits'])}, "
        f"any-of {len(triage['any_of'])}, neither {len(triage['neither'])}, "
        f"identical {len(triage['identical'])}; rows covered {counts['rows_covered']}; "
        f"machine-approved {machine['count']} units / {machine['rows_covered']} rows)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
