"""The verdicts-to-triage-YAML CLI (rebuild/REVIEW-PLAN.md §4.2): join an exported verdicts.json to the built review directory's units, re-validate every selected draft, and write one triage YAML with four sections (pins, policy_edits, any_of, neither) for human placement. Nothing is auto-applied to the corpus or the rune files.

Usage: uv run python -m rebuild.review.export verdicts.json --out tmp/review-triage.yaml
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_REVIEW_DIR = REPO_ROOT / "rebuild" / "out" / "review"

VERDICTS_FORMAT = "ams-review-verdicts/1"
VERDICT_VALUES = ("approve", "reject", "either", "neither", "skip")


def load_units(review_dir: Path) -> tuple[dict, dict[str, dict]]:
    manifest = json.loads((review_dir / "manifest.json").read_text(encoding="utf-8"))
    units: dict[str, dict] = {}
    for meta in manifest.get("classes", ()):
        shard = json.loads((review_dir / meta["shard"]).read_text(encoding="utf-8"))
        for unit in shard:
            units[unit["id"]] = unit
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


def _compact_id_ranges(unit_ids: list[str]) -> list[str]:
    """Collapse sorted u-NNNN ids into "u-0000..u-0024"-style range strings so 1,549 machine-approved ids stay readable in the triage YAML."""
    numbers = sorted(int(unit_id.split("-", 1)[1]) for unit_id in unit_ids)
    ranges: list[str] = []
    start = previous = None
    for number in numbers:
        if start is None:
            start = previous = number
            continue
        if number == previous + 1:
            previous = number
            continue
        ranges.append(f"u-{start:04d}" if start == previous else f"u-{start:04d}..u-{previous:04d}")
        start = previous = number
    if start is not None:
        ranges.append(f"u-{start:04d}" if start == previous else f"u-{start:04d}..u-{previous:04d}")
    return ranges


def machine_approved_section(manifest: dict, units: dict[str, dict]) -> dict:
    """The triage YAML's machine_approved record: machine-verdicted (ink-identical) units are reported as counts, per-class counts, the verification method, and compact unit-id ranges — never as drafted pins, which remain a human-verdict artifact."""
    machine = [unit for unit in units.values() if unit.get("ink_identical")]
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
        "unit_ids": _compact_id_ranges([unit["id"] for unit in machine]),
    }


def build_triage(manifest: dict, units: dict[str, dict], verdicts: dict) -> dict:
    counts = {"approve": 0, "reject": 0, "either": 0, "neither": 0, "skip": 0}
    covered = 0
    pins: list[dict] = []
    policy_edits: list[dict] = []
    any_of: list[dict] = []
    neither: list[dict] = []
    missing: list[str] = []

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

    if missing:
        print(f"warning: {len(missing)} verdicts reference unknown units: {missing[:5]}", file=sys.stderr)

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
            "human_units_total": len(units) - machine["count"],
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
        f"any-of {len(triage['any_of'])}, neither {len(triage['neither'])}; rows covered {counts['rows_covered']}; "
        f"machine-approved {machine['count']} units / {machine['rows_covered']} rows)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
