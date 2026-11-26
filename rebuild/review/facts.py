"""The review facts: the counts and structural facts an m1-audit corpus build reduces its state to, and the regenerator that writes them to rebuild/review-facts-pins.json, the last accepted review facts. `--update` regenerates that file from an m1-audit corpus's review-facts.json sidecar, writing it only when its bytes change; no artifact-cycle pass runs it, since the cycle builds the table-diff corpus, and the file and this module go with the m1-audit corpus (rebuild/CUTOVER-PLAN.md stage 4). Committing the rewritten file accepts the new review facts. No gate checks the checked-in numbers, so a changed count is something to read, not a failure.

The file has two blocks so the cycle can say what kind of change a pass made. `volatile` holds the manifest, built, audit, ink, and unmatched_groups groups, which change with every migrated letter. The unmatched_groups group counts the pre-merge units in each change group, the UNMATCHED units and the units re-routed out of an `ink_identical` class because their picture changes, and under `rerouted` how many each `ink_identical` class re-routed, so a broken identical claim shows as a number rather than as change-group growth. `invariant` holds the structural facts a person should review when they change: which classes the corpus ships, which classes the build machine-approves, which are exempt from individual verdicts, and which change groups the corpus reaches. Its class lists are in ledger order and then `CHANGE_GROUP_ORDER` (`class_rank`), not in the manifest's section order, so a class that changes section does not reorder them. The invariant block repeats structure that the volatile groups' keys already carry. Both blocks come from one emission, so they cannot disagree, and the separate block lets `invariant_delta` name a new class or a new no-verdict exemption in one summary line and lets the cycle print a diff of that block alone. `rebuild/out/cycle_summary.json` also records the corpus's totals. The machine-approved and change-group lists are recorded nowhere else, because both are emergent: a class is machine-approved when the build approved at least one of its units through any machine check, whatever the ledger declares. `ledger_coverage` compares the ledger's declarations with them.

No test reads this file, since a build asserting the numbers it just wrote would check nothing. The tests check the sidecar a build writes for internal consistency (the deduplicated units account for every audit row) and against invariants derived from the sources (the manifest's own totals, the ledger's no-verdict classes).

The manifest and built groups are post-merge: they describe the corpus after the ink-duplicate merge. The audit, ink, and unmatched_groups groups count pre-merge units, the (codepoints, baseline, new) triples before the merge, which no corpus shard reports, so their class counts can differ from the manifest's. `audit.row_count` counts raw audit rows.

The pre-merge groups come from the review-facts.json sidecar, which the review crate's build writes (`build.build_m1`). It derives them from the pre-merge state it captured just before the merge and the phase-1 products it computes anyway (each post-merge unit's machine flags and change group), instead of shaping and enriching the whole corpus a second time, so the derivation takes milliseconds and is not independent of the build. The build checks the derivation's assumptions: it fails on a unit that takes its survivor's change group but was merged away, on a unit that takes a change group and resolves to none, and on a unit the build re-routes and the review facts do not, or the reverse. The sidecar holds the finished pins and the identity of the corpus that owns them (`load_facts`); the per-unit ink flags and change groups that the ink and unmatched_groups groups are reduced from stay in the build's memory.

Usage:
    uv run python -m rebuild.review.facts --update [--corpus DIR]  # DIR defaults to rebuild/out/review; every non-staging artifact-cycle pass runs this
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rebuild.pipeline import output_sink
from rebuild.review.audit import LedgerClass
from rebuild.review.change_groups import CHANGE_GROUP_ORDER

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO_ROOT / "rebuild" / "review-facts-pins.json"
CORPUS = REPO_ROOT / "rebuild" / "out" / "review"

FACTS_FILENAME = "review-facts.json"
FACTS_FORMAT = "ams-review-facts/8"
FACTS_REMEDY = "rebuild the corpus with: uv run python -m rebuild.review.build"

# What the facts reduction in the review crate reads through the build input's vocabulary (`corpus_exec.VOCABULARY`): the classes whose unit counts the manifest group records, and the reference window, ·It·Day·Tea·No, whose duplicate siblings the built group counts.
CLASS_UNIT_COUNT_KEYS = ("split-by-space-or-zwnj", "identical-unused-join-name", "identical-variant-name")
REFERENCE_WINDOW_CODEPOINTS = "E670:E653:E652:E666"


def class_rank(manifest: Mapping) -> Callable[[str], tuple[int, int]]:
    """The sort key the pins list a corpus's classes by: the ledger classes in ledger order, which is the order of the manifest's `ledger_classes` lookup, then the change groups in `CHANGE_GROUP_ORDER`. The manifest itself lists its classes by section first, so a class whose section changes would otherwise move in the pins. The key raises KeyError on a class that is neither."""
    ranks = {identifier: (0, index) for index, identifier in enumerate(manifest["ledger_classes"])}
    ranks.update((identifier, (1, index)) for index, identifier in enumerate(CHANGE_GROUP_ORDER))
    return ranks.__getitem__


def invariant_group(manifest: dict, unmatched_groups: Mapping[str, int]) -> dict:
    """The invariant block: which classes the corpus ships, which the build machine-approves, and which are exempt from individual verdicts, each in `class_rank` order, and which change groups the corpus reaches, in the order of the pre-merge counts (`CHANGE_GROUP_ORDER`). The classes are listed, not counted, so `invariant_delta` can say which class appeared or went."""
    rank = class_rank(manifest)
    classes = sorted(manifest["classes"], key=lambda meta: rank(meta["id"]))
    return {
        "classes": [meta["id"] for meta in classes],
        "machine_approved_classes": sorted(manifest["machine_approved"]["by_class"], key=rank),
        "no_verdict_classes": [meta["id"] for meta in classes if meta["no_verdict"]],
        "unmatched_groups": list(unmatched_groups),
    }


INVARIANT_LABELS = {
    "classes": "classes",
    "machine_approved_classes": "machine-approved",
    "no_verdict_classes": "no-verdict",
    "unmatched_groups": "change groups",
}


def _named(ids: Sequence[str]) -> str:
    return f"{len(ids)} ({', '.join(ids)})" if ids else "0"


def invariant_delta(accepted: Mapping, current: Mapping) -> list[str]:
    """The changes in the invariant block since the accepted review facts, one finding per list and direction (`classes +2 (a, b)`, `machine-approved -1 (c)`), in the block's key order. The list is empty only when nothing changed. A list with the same members in a new order is reported only as `reordered`, because its order comes from the ledger and `CHANGE_GROUP_ORDER` and says nothing new about the corpus. A key that only one side has is reported as newly or no longer recorded, so a change to the block's shape does not read as a list of new classes."""
    findings: list[str] = []
    keys = list(accepted) + [key for key in current if key not in accepted]
    for key in keys:
        label = INVARIANT_LABELS.get(key, key)
        if key not in current:
            findings.append(f"{label} no longer recorded")
            continue
        if key not in accepted:
            findings.append(f"{label} newly recorded")
            continue
        old, new = accepted[key], current[key]
        if not isinstance(old, list) or not isinstance(new, list):
            if old != new:
                findings.append(f"{label} {old!r} -> {new!r}")
            continue
        gained = [item for item in new if item not in old]
        lost = [item for item in old if item not in new]
        if gained:
            findings.append(f"{label} +{_named(gained)}")
        if lost:
            findings.append(f"{label} -{_named(lost)}")
        if not gained and not lost and old != new:
            findings.append(f"{label} reordered")
    return findings


def invariant_diff(accepted: Mapping, current: Mapping) -> list[str]:
    """The invariant block's unified diff, accepted against current, in the pins file's JSON formatting: the part of `git diff -- rebuild/review-facts-pins.json` a reader needs, without the volatile hunks."""
    return list(
        difflib.unified_diff(
            json.dumps(accepted, indent=2).splitlines(),
            json.dumps(current, indent=2).splitlines(),
            fromfile="invariant (accepted)",
            tofile="invariant (this corpus)",
            lineterm="",
        )
    )


@dataclass(frozen=True)
class LedgerCoverage:
    """The ledger's declarations compared with what the corpus reached, from the ledger the corpus was built over and the invariant block. `unreached` lists the ledger entries the corpus ships no class for: no unit matched them, or every unit that did was re-routed to a change group. `machine_approved` is emergent (a class is in it when the build approved at least one of its units through any machine check) and is compared with the ledger's `ink_identical` declarations both ways: `ink_declared_unapproved` lists declared classes with no machine-approved unit, and `machine_approved_undeclared` lists approving classes the ledger never declared. `no_verdict` is declared in the ledger and copied onto each class the corpus ships, so its only possible disagreement is a declared class the corpus never reached. `describe` puts all of this on one line."""

    ledger: tuple[str, ...]
    unreached: tuple[str, ...]
    machine_approved: tuple[str, ...]
    ink_declared: tuple[str, ...]
    ink_declared_unapproved: tuple[str, ...]
    machine_approved_undeclared: tuple[str, ...]
    no_verdict_declared: tuple[str, ...]
    no_verdict_reached: tuple[str, ...]
    no_verdict_unreached: tuple[str, ...]

    def describe(self) -> str:
        """The one summary line. Approving classes without a declaration are counted, not named, because approval through the picture or Junior check, or of a change group's units, is the usual case and `invariant_delta` already names a class that joins them; `as_json` has the names. Each declaration the corpus does not show is named."""
        machine = (
            f"machine-approved: {len(self.machine_approved)} classes approve units,"
            f" {len(self.machine_approved_undeclared)} undeclared;"
            f" ink-identical: {len(self.ink_declared)} declared, "
            + (
                f"{_named(self.ink_declared_unapproved)} approving none"
                if self.ink_declared_unapproved
                else "all approving"
            )
        )
        no_verdict = (
            f"no-verdict: {len(self.no_verdict_reached)} of {len(self.no_verdict_declared)} declared reached"
            + (f", unreached {_named(self.no_verdict_unreached)}" if self.no_verdict_unreached else "")
        )
        ledger = f"ledger: {len(self.ledger) - len(self.unreached)} of {len(self.ledger)} classes reached" + (
            f", unreached {_named(self.unreached)}" if self.unreached else ""
        )
        return "; ".join((machine, no_verdict, ledger))

    def as_json(self) -> dict:
        return {
            "ledger": list(self.ledger),
            "unreached": list(self.unreached),
            "machine_approved": list(self.machine_approved),
            "ink_declared": list(self.ink_declared),
            "ink_declared_unapproved": list(self.ink_declared_unapproved),
            "machine_approved_undeclared": list(self.machine_approved_undeclared),
            "no_verdict_declared": list(self.no_verdict_declared),
            "no_verdict_reached": list(self.no_verdict_reached),
            "no_verdict_unreached": list(self.no_verdict_unreached),
        }


def ledger_coverage(ledger: Sequence[LedgerClass], invariant: Mapping) -> LedgerCoverage:
    """`LedgerCoverage` over a loaded ledger and an invariant block, every list in its source's order: ledger lists in ledger order, reached lists in the block's."""
    reached = set(invariant["classes"])
    machine = tuple(invariant["machine_approved_classes"])
    ink_declared = tuple(entry.id for entry in ledger if entry.ink_identical)
    no_verdict_declared = tuple(entry.id for entry in ledger if entry.no_verdict)
    return LedgerCoverage(
        ledger=tuple(entry.id for entry in ledger),
        unreached=tuple(entry.id for entry in ledger if entry.id not in reached),
        machine_approved=machine,
        ink_declared=ink_declared,
        ink_declared_unapproved=tuple(identifier for identifier in ink_declared if identifier not in machine),
        machine_approved_undeclared=tuple(
            identifier for identifier in machine if identifier not in ink_declared
        ),
        no_verdict_declared=no_verdict_declared,
        no_verdict_reached=tuple(invariant["no_verdict_classes"]),
        no_verdict_unreached=tuple(
            identifier
            for identifier in no_verdict_declared
            if identifier not in invariant["no_verdict_classes"]
        ),
    )


# --- the review-facts sidecar ---------------------------------------------------------


def load_facts(out_dir: Path, manifest: dict) -> dict:
    """The sidecar of a built corpus. Raises ValueError when the file is missing or in another format (the corpus predates the sidecar), or when its generated_at differs from the manifest's (the two came from different builds)."""
    path = Path(out_dir) / FACTS_FILENAME
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ValueError(f"{path} is missing — {FACTS_REMEDY}") from None
    if facts.get("format") != FACTS_FORMAT:
        raise ValueError(f"{path} is not {FACTS_FORMAT} — {FACTS_REMEDY}")
    if facts["corpus"]["generated_at"] != manifest["generated_at"]:
        raise ValueError(
            f"{path} was written for corpus {facts['corpus']['generated_at']},"
            f" not {manifest['generated_at']} — {FACTS_REMEDY}"
        )
    return facts


def compute_pins(corpus: Path) -> dict:
    """Both blocks of the pins for the built corpus at `corpus`, which is only read: the volatile block from its review-facts.json sidecar, and the invariant block reduced again from its manifest and the sidecar's change-group counts, the same sources the build used, so the checked-in file has the block's current shape whichever build wrote the sidecar."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    volatile = load_facts(corpus, manifest)["pins"]["volatile"]
    return {
        "invariant": invariant_group(manifest, volatile["unmatched_groups"]["counts"]),
        "volatile": volatile,
    }


def _dumps(pins: dict) -> str:
    return json.dumps(pins, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        required=True,
        help="rewrite the pins file from the corpus's review-facts.json sidecar",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS,
        help="the built corpus to read, read-only (default: rebuild/out/review)",
    )
    args = parser.parse_args(argv)

    shown = PINS_PATH.relative_to(REPO_ROOT)
    if output_sink.write_output(PINS_PATH, _dumps(compute_pins(args.corpus)), keep_if_equal=True):
        print(f"Wrote {shown}", file=sys.stderr)
    else:
        print(f"{shown} unchanged, left in place", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
