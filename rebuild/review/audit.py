"""The divergence audit and ledger as the review corpus build's Python side holds them (rebuild/REVIEW-PLAN.md §1.1, §2.1): the ledger's classes (`load_ledger`), the audit held open for the length of a build (`AuditParts`), whose descriptors the review crate's build process inherits and reads the audit through, and the vocabulary the build, its checker and the corpus's readers share: the acceptance configurations, the ledger statuses, the review app's sections (`class_section`), the machine checks and the slim fragment's rule (`slim_fragment`), and the order a corpus pages its human units in (`triage_key`).

The crate (`rebuild/review-rs/`) loads the audit, dedupes its rows to (codepoints, baseline, new) units, merges a window's ink duplicates and orders the units for triage. A unit's id is `unit_cache.unit_id_for` over the content key the build stamps once the unit is enriched, so it depends on what the reviewer judges and not on where the unit sits in the order.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from rebuild.pipeline import row_delta
from rebuild.review import change_groups

# Restate `conform.ORACLE_CONFIGS` and `conform.M1_ONLY_CONFIGS`, not `conform.ACCEPTANCE_CONFIGS`: importing conform would pull it into the corpus code closure. `rebuild/test_review_tablediff.py` ties them.
ACCEPTANCE_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss07", "ss10")
# Restate `baseline_subset.WINDOW_SCOPED_CONFIGS`, each configuration the oracle compares only on the windows that hold one of its letters, with those letters as the code point strings a window spells; `rebuild/test_review_tablediff.py` ties the two. A unit of any other window cannot hold the configuration, so the build resolves its config badge without it.
WINDOW_SCOPED_CONFIGS = (("ss07", ("E67C",)),)
M1_ONLY_CONFIGS = ("ss08",)
BATCH_SIZE = 300

UNMATCHED_CLASS = "UNMATCHED"

RESERVED_CLASS_IDS = frozenset({UNMATCHED_CLASS, *change_groups.CHANGE_GROUP_ORDER})

# The status vocabulary of the divergence ledger. `by-design` means a rule of the rebuild's design decides the class, `approved` that the user ruled the class's change correct, and `per-unit` that no ruling covers the class as a whole, so each unit's verdict decides it. The change groups are `per-unit` too, and a table-diff bucket has no status.
LEDGER_STATUSES = ("by-design", "approved", "per-unit")
RULED_STATUSES = ("by-design", "approved")

# The review app's sections, in the order the manifest lists their classes (`class_section`).
OPEN_QUESTIONS = "open-questions"
SPOT_CHECK = "spot-check"
NO_REVIEW_NEEDED = "no-review-needed"
SECTIONS = (OPEN_QUESTIONS, SPOT_CHECK, NO_REVIEW_NEEDED)

AUDIT_HEADER = ("config", "codepoints", "kinds", "matched_entry", "baseline", "new")


@dataclass(frozen=True)
class LedgerClass:
    """One class of the review corpus: a divergence-ledger entry (`load_ledger`) or a change group the build synthesizes. `title`, `summary` and the optional `note` are the review app's copy for the class, and `why` its full rationale."""

    id: str
    status: str
    why: str
    ink_identical: bool
    no_verdict: bool
    exemplar_keys: frozenset[tuple[str, str]]  # (config, codepoints)
    title: str = ""
    summary: str = ""
    note: str = ""


def class_section(status: str | None, no_verdict: bool, human_units: int) -> str:
    """Return the review app's section for a class. A class that is `no_verdict`, or whose every unit a machine check approves (`human_units` is zero), needs no review, whatever its status. Otherwise a ruled status (`RULED_STATUSES`) makes it a spot-check, and `per-unit`, or None for a table-diff bucket, an open question. Any other status raises."""
    if status is not None and status not in LEDGER_STATUSES:
        raise ValueError(f"{status!r} is not a ledger status ({', '.join(LEDGER_STATUSES)})")
    if no_verdict or human_units == 0:
        return NO_REVIEW_NEEDED
    if status in RULED_STATUSES:
        return SPOT_CHECK
    return OPEN_QUESTIONS


MACHINE_CHECKS = ("ink_identical", "picture_identical", "junior_equivalent")


def machine_approved(fragment) -> bool:
    """Whether a unit's JSON fragment has any machine-approval flag set. The build tries the checks in `MACHINE_CHECKS` order (picture identity only where ink identity fails, Junior equivalence only where both fail), so at most one is true."""
    return any(fragment.get(check_id) is True for check_id in MACHINE_CHECKS)


# The keys a slim fragment leaves out. A unit that is machine-approved (any of MACHINE_CHECKS) or exempt by its ledger class (`no_verdict`) is never paged to a human. The app reaches its fragment only from a show-machine fold or a deep link, which draw the window, both fonts' cells and junctions, the badge and the summary, but not the explain panel's candidate table, the drafts, or the pair band (`highlight`). The build omits these keys because `explain` and `drafts` are the largest fields of a full fragment, and computing the pin draft costs a shaping per unit. The keys are absent, not null, so the app can tell a slim fragment from a full one with a blank field. `build.check_unit` checks the shape in both directions, the review crate's fragment writer is the only writer, and `rebuild/review/static/slim.js` reads the same rule.
SLIM_OMITTED_KEYS = ("highlight", "explain", "drafts")


def slim_fragment(fragment) -> bool:
    """Whether a unit's JSON fragment is written slim: true for every machine-approved or verdict-exempt unit, and no other. It reads the fragment's flags, not which keys are missing, so the checker can check a fragment against the shape its flags require. The unit cache's store record keeps the same value as its `slim` flag, because the exemption comes from the ledger, which the content key does not cover, and a cached fragment must have the shape this build would write."""
    return machine_approved(fragment) or fragment.get("no_verdict") is True


def parse_codepoints(codepoints: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in codepoints.split(":"))


def _prose_field(path: Path, identifier: str, entry: Mapping, key: str, *, required: bool) -> str:
    value = entry.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {identifier} needs a nonempty string {key}, not {value!r}")
    return value.strip()


def load_ledger(path: Path) -> list[LedgerClass]:
    """Load the divergence ledger's entries as classes, in file order. An entry is refused unless it has a nonempty string id that no other entry and no class the build synthesizes (`RESERVED_CLASS_IDS`) uses, a status from `LEDGER_STATUSES`, and a nonempty `title` and `summary`. Its `match` is not read here: run_m1's pre-gate guards compile the ledger (`oracle.compile_ledger`) and refuse a predicate the oracle does not define before any gate runs. A pass refused there before its first write leaves the Stage A record as it found it, if there is one (only a run whose gates pass writes it), whose `data` value the edited ledger no longer gives, and one refused after its refilter wrote leaves none, so a corpus built over either carries stale or null Stage A values, which the readiness checker flags."""
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    classes: list[LedgerClass] = []
    seen: set[str] = set()
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{path}: every ledger entry needs a nonempty string id, not {identifier!r}")
        if identifier in RESERVED_CLASS_IDS:
            raise ValueError(f"{path}: {identifier} is a class the build synthesizes itself")
        if identifier in seen:
            raise ValueError(f"{path}: {identifier} is declared twice")
        seen.add(identifier)
        status = entry.get("status")
        if status not in LEDGER_STATUSES:
            raise ValueError(
                f"{path}: {identifier} has status {status!r}, not one of {', '.join(LEDGER_STATUSES)}"
            )
        classes.append(
            LedgerClass(
                id=identifier,
                status=status,
                why=(entry.get("why") or "").strip(),
                ink_identical=bool(entry.get("ink_identical", False)),
                no_verdict=bool(entry.get("no_verdict", False)),
                exemplar_keys=frozenset(
                    (exemplar["config"], exemplar["codepoints"]) for exemplar in entry.get("exemplars", ())
                ),
                title=_prose_field(path, identifier, entry, "title", required=True),
                summary=_prose_field(path, identifier, entry, "summary", required=True),
                note=_prose_field(path, identifier, entry, "note", required=False),
            )
        )
    return classes


INPUT_KEY_BYTES = 32


def family_ranks(family_of: Mapping[int, str]) -> dict[str, int]:
    """Map each family name to its code point, the key a group's two families sort by."""
    return {name: value for value, name in family_of.items()}


def triage_key(
    class_index: int,
    group: str,
    codepoint_values: tuple[int, ...],
    unit_id: str,
    family_rank: Mapping[str, int],
) -> tuple:
    """The sort key for the order a corpus pages its human units in (the manifest's `human_unit_ids`): the class's index in the manifest's class list, the group's families by code point, the window's length and codepoints, and last the unit's id. The id breaks ties between sibling units of one window by content instead of by audit order. Every term depends only on the unit and the ledger, so the same units sort the same way on every corpus. `build.check_shards` checks every manifest's index against this key."""
    return (
        class_index,
        tuple(family_rank.get(name, 10**6) for name in group.split(":")),
        len(codepoint_values),
        codepoint_values,
        unit_id,
    )


class AuditParts:
    """The audit as the corpus build holds it from its load to its end: under a head (`row_delta`, the plain keyed merge run_m1's oracle writes), every part the head lists, opened as the head is read and checked against the sizes it records, so a later commit's deletes cannot reach them; or, for a tree with no head (every fixture audit among them), the single file. `descriptors` are the base's and then each segment's, never reopened by path, since a `--gates-only` pass can commit a new head during a build, and `members` is each one's member index from the head it holds, aligned with them, None for a plain part (`row_delta.Part.members`), with `compressed` counting the parts held as gzip members. A head read between a commit that folds and its deletes of the old generation can list a part already gone, so the head is read again, up to three times, before that fails the build."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.head: row_delta.Head | None = None
        self._opened: row_delta.OpenParts | None = None
        self._single: int | None = None
        for attempt in range(3):
            head = row_delta.read_head(self.path.parent, self.path.name)
            if head is None:
                self._single = os.open(self.path, os.O_RDONLY)
                return
            try:
                self._opened = row_delta.open_parts(self.path.parent, head)
            except row_delta.HeadError:
                if attempt == 2:
                    raise
                continue
            self.head = head
            return

    @property
    def descriptors(self) -> tuple[int, ...]:
        if self._opened is not None:
            return tuple(handle.fileno() for handle in self._opened.handles)
        assert self._single is not None
        return (self._single,)

    @property
    def members(self) -> tuple[tuple[row_delta.Member, ...] | None, ...]:
        if self.head is None:
            return (None,)
        return tuple(part.members for part in self.head.parts)

    @property
    def compressed(self) -> int:
        return sum(members is not None for members in self.members)

    @property
    def content_time(self) -> float:
        """When the audit's content last moved, in seconds since the epoch: the head's `row_delta.CONTENT_TIME_STAMP`, or the single file's modification time."""
        if self.head is not None:
            return int(self.head.stamps[row_delta.CONTENT_TIME_STAMP]) / 1e9
        return os.fstat(self.descriptors[0]).st_mtime

    @property
    def segment_rows(self) -> int:
        return 0 if self.head is None else sum(part.rows for part in self.head.segments)

    def close(self) -> None:
        if self._opened is not None:
            self._opened.close()
            self._opened = None
        if self._single is not None:
            os.close(self._single)
            self._single = None
