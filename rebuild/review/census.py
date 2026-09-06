"""The review-surface census: the groups a surface build reduces its own state to, and the regenerator that writes them into rebuild/review-census-pins.json — the last accepted census. Every artifact-cycle pass rewrites that file from the surface's census-facts.json sidecar and prints its git diff, and committing the diff is the acceptance. Nothing asserts the checked-in numbers, so a moved count is something to read rather than a baseline to re-bless.

The file is two blocks so that diff reads. `volatile` holds what legitimately moves with every migrated letter: the manifest, built, audit, ink, and families groups, counts and all. `invariant` holds the structural facts whose movement deserves a human — how many classes the surface ships, which classes the build machine-approves, which are exempt from individual verdicts, and which verdict families the corpus reaches. The invariant block deliberately restates structure the volatile dicts' keys already carry; both blocks are machine-written from one emission so they cannot drift apart, and hoisting the structure out is what makes a new class or a new no-verdict exemption its own legible hunk instead of a line lost among moved totals.

The tests no longer read this file at all — a build asserting the numbers it just wrote proves nothing. They assert internal consistency (the deduped units still account for every audit row), source-derived invariants (the manifest's own totals, the ledger's no-verdict classes), and mirrors (a shard walk against the sidecar the same build wrote from memory).

Three grains coexist and must not be conflated. The manifest and built groups are post-merge, read from a built surface after the ink-duplicate fold; the audit, ink, and families groups are the pre-merge name grain, which no surface shard reports.

The pre-merge groups are read from the surface's census-facts.json sidecar, which `build_m1` writes as a by-product: it derives them from the pre-merge state it captured just before folding plus the phase-1 products it computed anyway — each post-merge unit's ink verdict and each UNMATCHED unit's verdict family — instead of re-shaping and re-enriching the whole corpus a second time. That deliberately trades some of the comparison's independence from the build for a census that costs milliseconds rather than minutes. `--from-scratch` retains the standalone re-derivation from the source inputs (TSV + ledger + fonts + spec) for when the two must be compared. What the derivation needs beyond that is not sampled but asserted where it is derived: `derive_premerge` refuses a default-novel UNMATCHED unit that was folded away, refuses an UNMATCHED unit that resolved to no family, and holds one ink flag per captured unit, which between them are the grain bookkeeping the retired sample tests were re-deriving.

Usage:
    uv run python -m rebuild.review.census --update --surface rebuild/out/review  # what every artifact-cycle pass runs
    uv run python -m rebuild.review.census --check --surface rebuild/out/review   # the manual comparison
    uv run python -m rebuild.review.census            # --check, building a fresh temporary surface first
    uv run python -m rebuild.review.census --check --from-scratch
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import tempfile
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rebuild.review import unit_index
from rebuild.review.audit import (
    BATCH_SIZE,
    UNMATCHED_CLASS,
    Unit,
    _config_index,
    assign_batches,
    group_for,
    load_audit,
    load_workload,
    parse_codepoints,
    render_groups_for_rows,
    slim_fragment,
)
from rebuild.review.enrich import LETTERS, Enricher, load_spec
from rebuild.review.families import FAMILY_ORDER, assign_family, deferred_family
from rebuild.review.ink import InkComparator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO_ROOT / "rebuild" / "review-census-pins.json"

FACTS_FILENAME = "census-facts.json"
FACTS_FORMAT = "ams-census-facts/2"
FACTS_REMEDY = "rebuild the surface with: uv run python -m rebuild.review.build"

AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
SUBSET_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

CLASS_UNIT_COUNT_KEYS = ("boundary-echo", "dangling-anchor-dropped", "bare-name-live-join")

WORKED_EXAMPLE_CODEPOINTS = "E670:E653:E652:E666"


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def manifest_group(manifest: dict) -> dict:
    """The post-merge facts read straight from the built surface's manifest.json."""
    by_id = {meta["id"]: meta for meta in manifest["classes"]}
    return {
        "totals": dict(manifest["totals"]),
        "machine_approved": {
            "units": manifest["machine_approved"]["units"],
            "by_class": dict(manifest["machine_approved"]["by_class"]),
        },
        "class_unit_count": {key: by_id[key]["unit_count"] for key in CLASS_UNIT_COUNT_KEYS},
        "secondary_seams": dict(manifest["secondary_seams"]),
    }


def invariant_group(manifest: dict, families_census: dict[str, int]) -> dict:
    """The structural half of the pins, in the orders their sources already carry: how many classes the surface ships, which classes the build machine-approves, which are exempt from individual verdicts, and which verdict families the corpus reaches. Every one of these is implied by some volatile group's keys, and that restatement is deliberate — both blocks come from one machine emission, so they cannot drift apart, and hoisting the structure into a block of its own is what lets a new class, a new no-verdict exemption, or a family appearing read as its own hunk when the rewritten pins are reviewed as a diff, instead of hiding among the counts a rune commit moves anyway."""
    return {
        "classes_count": len(manifest["classes"]),
        "machine_approved_classes": list(manifest["machine_approved"]["by_class"]),
        "no_verdict_classes": [meta["id"] for meta in manifest["classes"] if meta["no_verdict"]],
        "families": list(families_census),
    }


def _shard_units(out_dir: Path, meta: dict) -> Iterable[dict]:
    """One class's units, a shard part at a time so a large class is never resident whole."""
    for part in unit_index.class_shards(meta):
        yield from json.loads((out_dir / part).read_text(encoding="utf-8"))


def built_group(out_dir: Path, manifest: dict) -> dict:
    """The post-merge facts computed by walking the surface's unit shards — the human-workload size, the config-note histogram, and the worked example's echo-sibling count (the distinct windows one ·It·Day·Tea·No verdict answers), none of which is a manifest key. The echo-sibling count is None when the worked example is not in the human workload: only the live corpus is obliged to carry it, and the sidecar is written by every build — the unit-cache tests' mini surfaces included — so the obligation is enforced where it belongs, by the pins diff replacing an accepted count with a computed None."""
    out_dir = Path(out_dir)
    human_units = 0
    distribution: dict[str | None, int] = {}
    example_echo: str | None = None
    codepoints_by_echo: dict[str, set[str]] = {}
    for meta in manifest["classes"]:
        for unit in _shard_units(out_dir, meta):
            if not slim_fragment(unit):
                human_units += 1
                codepoints_by_echo.setdefault(unit["echo"], set()).add(unit["codepoints"])
                if unit["codepoints"] == WORKED_EXAMPLE_CODEPOINTS:
                    example_echo = unit["echo"]
            note = unit["config_note"]
            distribution[note] = distribution.get(note, 0) + 1
    return {
        "human_units": human_units,
        "worked_example_echo_siblings": (
            len(codepoints_by_echo[example_echo]) if example_echo is not None else None
        ),
        "config_note_distribution": _encode_note_distribution(distribution),
    }


def _encode_note_distribution(distribution: dict[str | None, int]) -> list[list]:
    """JSON forbids a null object key, so the config-note histogram is stored as a list of [note, count] pairs, null first then lexicographic."""
    return [
        [note, distribution[note]]
        for note in sorted(distribution, key=lambda note: (note is not None, note or ""))
    ]


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
    units: list[Unit] = []
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
        units.append(unit)
    return [assign_family(enriched) for enriched in enricher.enrich_many(units)]


def family_census(assignments: list[str]) -> dict[str, int]:
    census: dict[str, int] = {}
    for family in assignments:
        census[family] = census.get(family, 0) + 1
    order = {family: index for index, family in enumerate(FAMILY_ORDER)}
    return dict(sorted(census.items(), key=lambda item: (order.get(item[0], len(order)), item[0])))


def families_group(repo_root: Path = REPO_ROOT) -> dict:
    census = family_census(family_assignments(repo_root))
    return {"census": census, "total": sum(census.values())}


# --- the census-facts sidecar ---------------------------------------------------------


class CensusGrain(Protocol):
    """The four fields the pre-merge census is defined over. Both a live `Unit` and a captured `PremergeUnit` satisfy it, so the digest that identifies a workload can be taken from either side of the fold."""

    @property
    def codepoints(self) -> str: ...

    @property
    def class_id(self) -> str: ...

    @property
    def no_verdict(self) -> bool: ...

    @property
    def configs(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class PremergeUnit:
    """One pre-merge unit as the build saw it before the ink-duplicate fold: a reference to the live object plus the scalars the census grain is defined over, and the deferred stylistic-set bucket, which can only be decided here. Deferral is pure config logic over the pre-merge config classes, and folding moves those — an ss03-only survivor that absorbs an ss04-only sibling is deferred-ss03 before the fold and would read as deferred-ss04 after it."""

    unit: Unit
    codepoints: str
    class_id: str
    no_verdict: bool
    configs: tuple[str, ...]
    deferred: str | None


@dataclass(frozen=True)
class PremergeFacts:
    """The pre-merge census grain as a build can report it: how many units the workload held, the digest identifying them, one '0'/'1' ink flag per unit in capture order, and the verdict family of every UNMATCHED unit keyed by its index into that order."""

    units: int
    workload_digest: str
    ink_flags: str
    families: list[tuple[int, str]]


def capture_premerge(units: Sequence[Unit]) -> list[PremergeUnit]:
    """Snapshot the workload for the sidecar, called immediately before `merge_ink_duplicate_units`. The fold replaces a survivor's field values rather than mutating them and never touches the units it removes, so these scalars stay true for the rest of the build; only the deferral has to be computed now, from config classes the fold is about to widen. A matched unit is never asked for one — `deferred_family` falls back to reading every config as novel, which is meaningless outside UNMATCHED."""
    return [
        PremergeUnit(
            unit=unit,
            codepoints=unit.codepoints,
            class_id=unit.class_id,
            no_verdict=unit.no_verdict,
            configs=unit.configs,
            deferred=deferred_family(unit) if unit.class_id == UNMATCHED_CLASS else None,
        )
        for unit in units
    ]


def workload_digest(units: Iterable[CensusGrain]) -> str:
    """A sha256 over the census grain of a unit list, in order — what lets a consumer prove the workload it just loaded is the one a sidecar's flags are indexed against, since an index into that flag string means nothing against a different list."""
    payload = "\n".join(
        f"{unit.codepoints}\t{unit.class_id}\t{int(unit.no_verdict)}\t{','.join(unit.configs)}"
        for unit in units
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def derive_premerge(capture: list[PremergeUnit], live_units: Sequence[Unit]) -> PremergeFacts:
    """Project the build's post-merge phase-1 products back onto the pre-merge grain the census pins are defined over. A unit that survived the fold answers for itself; one that was folded away answers through its survivor, resolved as the unique live unit of the same window whose config set covers the folded unit's earliest config.

    Both projections are sound by construction, and neither rests on an agreement between two derivations any more. A fold happens only when every config of every folded sibling yields one identical `InkComparator.signature`, and that signature is now defined as the two run-order ink lists `config_diff` reads — so a folded sibling's delta, and therefore its ink verdict, is its survivor's by definition rather than by a sampled resemblance. And a pre-merge UNMATCHED unit that is novel under the default config necessarily leads its window's fold order, so it is always its own survivor and its family is the phase-1 family on that same object; everything else UNMATCHED never carries the default config, is therefore deferred, and took its bucket at capture time. That second argument is asserted rather than trusted below, at the one place it could fail — an UNMATCHED, undeferred snapshot that is not its own survivor — which is the whole of what the retired families sample was checking.
    """
    live = {id(unit) for unit in live_units}
    dead_windows = {snap.codepoints for snap in capture if id(snap.unit) not in live}
    survivors: dict[str, list[Unit]] = {}
    for unit in live_units:
        if unit.codepoints in dead_windows:
            survivors.setdefault(unit.codepoints, []).append(unit)

    flags: list[str] = []
    assigned: list[tuple[int, str]] = []
    for index, snap in enumerate(capture):
        assert snap.class_id != UNMATCHED_CLASS or snap.deferred is not None or id(snap.unit) in live, (
            f"window {snap.codepoints}: a default-novel UNMATCHED unit was folded away, so its family "
            "cannot be read off its own phase-1 object"
        )
        if id(snap.unit) in live:
            target = snap.unit
        else:
            candidates = [
                unit for unit in survivors.get(snap.codepoints, ()) if snap.configs[0] in unit.configs
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"window {snap.codepoints}: {len(candidates)} live units carry the folded unit's"
                    f" config {snap.configs[0]!r}"
                )
            target = candidates[0]
        flags.append("1" if target.ink_identical else "0")
        if snap.class_id == UNMATCHED_CLASS:
            family = snap.deferred or target.family_id
            if not family:
                raise ValueError(f"window {snap.codepoints}: UNMATCHED unit resolved to no verdict family")
            assigned.append((index, family))
    # The flags are an index into the capture, so one per captured unit is the whole of what makes an index into them mean anything.
    assert len(flags) == len(capture), f"{len(flags)} ink flags over {len(capture)} pre-merge units"
    return PremergeFacts(
        units=len(capture),
        workload_digest=workload_digest(capture),
        ink_flags="".join(flags),
        families=assigned,
    )


def ink_group_from_flags(class_rows: Sequence[tuple[str, bool]], flags: str) -> dict:
    """The ink group rebuilt from one '0'/'1' flag per pre-merge unit plus that unit's (class, no-verdict) pair. This and `ink_histogram` are mirrors and must agree key for key, insertion order of `by_class` included; rebuild/test_census_facts.py holds them equal over synthetic units. Neither Junior equivalence nor picture identity plays a part on either side — the pre-merge census counts the ink verdict alone, and the batch count is the plain slice of whatever is left over."""
    machine_by_class: dict[str, int] = {}
    exempt = 0
    human = 0
    for (class_id, no_verdict), flag in zip(class_rows, flags, strict=True):
        if flag == "1":
            machine_by_class[class_id] = machine_by_class.get(class_id, 0) + 1
        elif no_verdict:
            exempt += 1
        else:
            human += 1
    machine_total = sum(machine_by_class.values())
    return {
        "machine_total": machine_total,
        "non_identical": len(class_rows) - machine_total,
        "by_class": machine_by_class,
        "boundary_echo_exempt": exempt,
        "human_units": human,
        "batches": (human + BATCH_SIZE - 1) // BATCH_SIZE,
    }


def families_group_from(assignments: list[str]) -> dict:
    """The families group over families already assigned — the aggregate half of `families_group`, split out so a build can report the census over the families phase 1 computed instead of enriching every UNMATCHED window again."""
    census = family_census(assignments)
    return {"census": census, "total": sum(census.values())}


def built_group_from_memory(units: Sequence[Unit], config_notes: Mapping[str, str | None]) -> dict:
    """`built_group` over the build's own in-memory state rather than the shards it wrote — the same three facts by the same rules, the None-when-absent worked-example contract included, so the surface build can report them without re-parsing hundreds of megabytes it just serialized. `config_notes` is each unit's `config_note` by id, the one fragment field this group reads, kept by the build as the fragments went by rather than the fragments themselves."""
    human_units = 0
    distribution: dict[str | None, int] = {}
    example_echo: str | None = None
    codepoints_by_echo: dict[str, set[str]] = {}
    for unit in units:
        if unit.batch is not None:
            assert unit.echo is not None
            human_units += 1
            codepoints_by_echo.setdefault(unit.echo, set()).add(unit.codepoints)
            if unit.codepoints == WORKED_EXAMPLE_CODEPOINTS:
                example_echo = unit.echo
        note = config_notes[unit.unit_id]
        distribution[note] = distribution.get(note, 0) + 1
    return {
        "human_units": human_units,
        "worked_example_echo_siblings": (
            len(codepoints_by_echo[example_echo]) if example_echo is not None else None
        ),
        "config_note_distribution": _encode_note_distribution(distribution),
    }


def build_facts(
    manifest: dict,
    units: Sequence[Unit],
    config_notes: Mapping[str, str | None],
    capture: list[PremergeUnit],
    premerge: PremergeFacts,
    row_count: int,
) -> dict:
    """The sidecar payload: the finished pins the regenerator copies into the checked-in file, plus the pre-merge records they were reduced from, stamped with the identity of the surface that owns them. The records ride along so a reader can re-reduce the pre-merge groups itself, which is what `--from-scratch` compares against."""
    families = families_group_from([family for _index, family in premerge.families])
    return {
        "format": FACTS_FORMAT,
        "surface": {
            "generated_at": manifest["generated_at"],
            "repo_head": manifest["repo_head"],
            "inputs_fingerprint": manifest["inputs_fingerprint"],
        },
        "pins": {
            "invariant": invariant_group(manifest, families["census"]),
            "volatile": {
                "manifest": manifest_group(manifest),
                "built": built_group_from_memory(units, config_notes),
                "audit": {"row_count": row_count, "units": len(capture)},
                "ink": ink_group_from_flags(
                    [(snap.class_id, snap.no_verdict) for snap in capture], premerge.ink_flags
                ),
                "families": families,
            },
        },
        "premerge": {
            "units": premerge.units,
            "workload_digest": premerge.workload_digest,
            "ink_identical": premerge.ink_flags,
            "families": [[index, family] for index, family in premerge.families],
        },
    }


def write_facts(out_dir: Path, facts: dict) -> None:
    """Write the sidecar beside the manifest, compact: the flag string alone is one character per pre-merge unit, so the whole file stays a fraction of a single shard."""
    path = Path(out_dir) / FACTS_FILENAME
    path.write_text(json.dumps(facts, separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8")


def load_facts(out_dir: Path, manifest: dict) -> dict:
    """The sidecar of a built surface, refused unless it is that surface's own. A missing file or an unrecognized format means the surface predates the sidecar; a generated_at disagreeing with the manifest means the two were written by different builds and the pins would describe neither."""
    path = Path(out_dir) / FACTS_FILENAME
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ValueError(f"{path} is missing — {FACTS_REMEDY}") from None
    if facts.get("format") != FACTS_FORMAT:
        raise ValueError(f"{path} is not {FACTS_FORMAT} — {FACTS_REMEDY}")
    if facts["surface"]["generated_at"] != manifest["generated_at"]:
        raise ValueError(
            f"{path} was written for surface {facts['surface']['generated_at']},"
            f" not {manifest['generated_at']} — {FACTS_REMEDY}"
        )
    return facts


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


def compute_pins(
    surface: Path | None = None, repo_root: Path = REPO_ROOT, from_scratch: bool = False
) -> dict:
    """The full pin set, both blocks. By default every group is read from the surface's census-facts.json sidecar, which the build derived from the same state it shaped the surface out of; `from_scratch` recomputes all five volatile groups from the source artifacts instead, re-shaping and re-enriching the corpus."""
    if from_scratch:
        with _build_or_load_surface(surface) as (out_dir, manifest):
            families = families_group(repo_root)
            return {
                "invariant": invariant_group(manifest, families["census"]),
                "volatile": {
                    "manifest": manifest_group(manifest),
                    "built": built_group(out_dir, manifest),
                    "audit": audit_group(repo_root),
                    "ink": ink_group(repo_root),
                    "families": families,
                },
            }
    with _build_or_load_surface(surface) as (out_dir, manifest):
        return load_facts(out_dir, manifest)["pins"]


def _dumps(pins: dict) -> str:
    return json.dumps(pins, indent=2) + "\n"


def _flatten(obj, prefix: str, out: dict) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    else:
        out[prefix] = obj


def _mismatches(old: dict, new: dict) -> list[tuple[str, object, object]]:
    """Per-key mismatches over the whole pin set — every key of both blocks, since the file carries nothing descriptive to skip."""
    old_flat: dict = {}
    new_flat: dict = {}
    _flatten(old, "", old_flat)
    _flatten(new, "", new_flat)
    keys = sorted(set(old_flat) | set(new_flat))
    return [
        (key, old_flat.get(key), new_flat.get(key)) for key in keys if old_flat.get(key) != new_flat.get(key)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check", action="store_true", help="recompute and compare against the checked-in pins (default)"
    )
    action.add_argument("--update", action="store_true", help="recompute and rewrite the pins file")
    parser.add_argument(
        "--surface",
        type=Path,
        default=None,
        help="reuse an existing built surface directory (read-only); default builds a fresh one in a temp directory",
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="recompute the audit/ink/families groups from sources instead of reading the surface's census-facts.json sidecar — the slow, independent re-derivation",
    )
    args = parser.parse_args(argv)

    new = compute_pins(args.surface, from_scratch=args.from_scratch)
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
