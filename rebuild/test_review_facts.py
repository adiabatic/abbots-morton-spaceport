"""Tests for the review facts' reader and the pins regenerator (`rebuild/review/facts.py`): reading the sidecar a corpus build writes, the invariant block and its delta and diff, the ledger's declarations against what a corpus reached, and the `--update` path that rewrites the pins from a corpus's sidecar.

The tests write their own sidecars and manifests (`_write_sidecar`, `_cli_corpus`), with no fonts, no shaping, and no live workload. A failure here is a bug in the reader or the reductions, not a change in the corpus.
"""

import json
import os
from pathlib import Path

import pytest

from rebuild.review import facts
from rebuild.review.audit import LedgerClass
from rebuild.review.change_groups import CHANGE_GROUP_ORDER
from rebuild.review.facts import (
    FACTS_FORMAT,
    invariant_delta,
    invariant_diff,
    invariant_group,
    ledger_coverage,
    load_facts,
)

LONG_AGO_NS = 1_000_000_000


def _ledger_entry(identifier: str, *, ink_identical: bool = False, no_verdict: bool = False) -> LedgerClass:
    return LedgerClass(
        id=identifier,
        status="per-unit",
        why="",
        ink_identical=ink_identical,
        no_verdict=no_verdict,
        exemplar_keys=frozenset(),
    )


def _pins(row_count: int) -> dict:
    """A small pin set in the checked-in file's two-block shape: an invariant block over three classes, and two volatile groups."""
    return {
        "invariant": {
            "classes": ["split-by-space-or-zwnj", "identical-variant-name", "join-dropped"],
            "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
            "no_verdict_classes": ["split-by-space-or-zwnj"],
            "unmatched_groups": ["join-dropped"],
        },
        "volatile": {
            "audit": {"row_count": row_count, "units": 1},
            "unmatched_groups": {
                "counts": {"join-dropped": 1},
                "total": 1,
                "rerouted": {"identical-variant-name": 0},
            },
        },
    }


def _facts(pins: dict, generated_at: str = "2026-01-01T00:00:00Z") -> dict:
    manifest = {
        "generated_at": generated_at,
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
    }
    return {
        "format": FACTS_FORMAT,
        "corpus": manifest,
        "pins": pins,
    }


def _write_sidecar(directory: Path, written: dict) -> None:
    """Write `written` as a corpus's review-facts sidecar, compact ASCII JSON on one line, as the build writes it."""
    (directory / facts.FACTS_FILENAME).write_text(
        json.dumps(written, separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8"
    )


def test_load_facts_reads_back_the_corpus_sidecar(tmp_path):
    """`load_facts` returns the sidecar as written when its `generated_at` is the manifest's."""
    written = _facts(_pins(row_count=2))
    _write_sidecar(tmp_path, written)
    assert load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"}) == written


def test_load_facts_refuses_a_missing_wrong_format_or_orphaned_sidecar(tmp_path):
    """`load_facts` accepts only the corpus's own sidecar. It raises on a missing file, an unknown format, or a `generated_at` that differs from the manifest's, since then the sidecar and the corpus came from different builds."""
    with pytest.raises(ValueError, match="rebuild.review.build"):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    stale = _facts({})
    stale["format"] = "ams-review-facts/0"
    _write_sidecar(tmp_path, stale)
    with pytest.raises(ValueError, match=FACTS_FORMAT):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    _write_sidecar(tmp_path, _facts({}, generated_at="2026-01-01T00:00:00Z"))
    with pytest.raises(ValueError, match="2026-02-02T00:00:00Z"):
        load_facts(tmp_path, {"generated_at": "2026-02-02T00:00:00Z"})


def test_invariant_group_lists_classes_in_ledger_then_group_order():
    """The invariant block lists the classes, the machine-approved classes and the no-verdict classes in ledger order (the manifest's `ledger_classes`) and then `CHANGE_GROUP_ORDER`, not in the manifest's section order, so a class that changes section does not reorder them; the change groups keep the order of the sidecar's change-group counts, which is `CHANGE_GROUP_ORDER`'s. A class that is neither a ledger class nor a change group raises."""
    manifest = {
        "ledger_classes": dict.fromkeys(
            [
                "split-by-space-or-zwnj",
                "join-switches-sides",
                "halves-closer-to-no",
                "identical-variant-name",
            ],
            {},
        ),
        "classes": [
            {"id": "join-switches-sides", "no_verdict": False},
            {"id": "join-dropped", "no_verdict": False},
            {"id": "split-by-space-or-zwnj", "no_verdict": True},
            {"id": "halves-closer-to-no", "no_verdict": True},
            {"id": "identical-variant-name", "no_verdict": False},
        ],
        "machine_approved": {
            "units": 5,
            "by_class": {"identical-variant-name": 3, "split-by-space-or-zwnj": 2},
        },
    }
    assert invariant_group(manifest, {"new-join-at-baseline": 8, "only-with-ss03": 1}) == {
        "classes": [
            "split-by-space-or-zwnj",
            "join-switches-sides",
            "halves-closer-to-no",
            "identical-variant-name",
            "join-dropped",
        ],
        "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
        "no_verdict_classes": ["split-by-space-or-zwnj", "halves-closer-to-no"],
        "unmatched_groups": ["new-join-at-baseline", "only-with-ss03"],
    }
    with pytest.raises(KeyError, match="not-a-class"):
        invariant_group({**manifest, "classes": [{"id": "not-a-class", "no_verdict": False}]}, {})


def _cli_corpus(tmp_path: Path, pins: dict) -> Path:
    """Write a corpus for the review-facts CLI: a manifest whose class list, ledger lookup and machine-approved histogram agree with the invariant block of `pins`, and a sidecar carrying `pins`."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    invariant = pins["invariant"]
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0000000",
        "classes": [
            {"id": identifier, "no_verdict": identifier in invariant["no_verdict_classes"]}
            for identifier in invariant["classes"]
        ],
        "ledger_classes": {
            identifier: {} for identifier in invariant["classes"] if identifier not in CHANGE_GROUP_ORDER
        },
        "machine_approved": {
            "units": len(invariant["machine_approved_classes"]),
            "by_class": {identifier: 1 for identifier in invariant["machine_approved_classes"]},
        },
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_sidecar(corpus, _facts(pins))
    return corpus


def test_update_copies_the_sidecars_volatile_block_and_reduces_the_invariant_again(tmp_path, monkeypatch):
    """`--update` copies the sidecar's volatile block into the pins file unchanged and recomputes the invariant block from the corpus's manifest and the sidecar's change-group counts. The file gets the invariant block's current shape even when an older build wrote the sidecar with a different one. A second `--update` over the same corpus leaves the file in place, keeping its inode and modification time."""
    pins_path = tmp_path / "pins.json"
    monkeypatch.setattr(facts, "PINS_PATH", pins_path)
    monkeypatch.setattr(facts, "REPO_ROOT", tmp_path)
    pins = _pins(row_count=2)
    stale = {"invariant": {"classes_count": 3}, "volatile": pins["volatile"]}
    corpus = _cli_corpus(tmp_path, pins)
    _write_sidecar(corpus, _facts(stale))
    assert facts.main(["--update", "--corpus", str(corpus)]) == 0
    assert json.loads(pins_path.read_text(encoding="utf-8")) == pins
    os.utime(pins_path, ns=(LONG_AGO_NS, LONG_AGO_NS))
    inode = pins_path.stat().st_ino
    assert facts.main(["--update", "--corpus", str(corpus)]) == 0
    assert (pins_path.stat().st_ino, pins_path.stat().st_mtime_ns) == (inode, LONG_AGO_NS)


_ACCEPTED_INVARIANT = {
    "classes": ["split-by-space-or-zwnj", "identical-variant-name", "only-with-ss10"],
    "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name"],
    "no_verdict_classes": ["split-by-space-or-zwnj"],
    "unmatched_groups": ["new-join-at-baseline", "only-with-ss10"],
}


def test_invariant_delta_is_empty_exactly_when_nothing_moved():
    assert invariant_delta(_ACCEPTED_INVARIANT, dict(_ACCEPTED_INVARIANT)) == []


def test_invariant_delta_names_what_appeared_and_what_went_in_the_blocks_own_order():
    """`invariant_delta` names the ids that were added to or removed from each list (classes, machine-approved classes, no-verdict classes, change groups), each in the block's own order, so the cycle log can be searched the same way on every pass."""
    current = {
        "classes": [
            "split-by-space-or-zwnj",
            "identical-variant-name",
            "identical-see-out-fusion",
            "only-with-ss04",
        ],
        "machine_approved_classes": [
            "split-by-space-or-zwnj",
            "identical-variant-name",
            "identical-see-out-fusion",
        ],
        "no_verdict_classes": ["split-by-space-or-zwnj", "identical-see-out-fusion"],
        "unmatched_groups": ["new-join-at-baseline", "only-with-ss04"],
    }
    assert invariant_delta(_ACCEPTED_INVARIANT, current) == [
        "classes +2 (identical-see-out-fusion, only-with-ss04)",
        "classes -1 (only-with-ss10)",
        "machine-approved +1 (identical-see-out-fusion)",
        "no-verdict +1 (identical-see-out-fusion)",
        "change groups +1 (only-with-ss04)",
        "change groups -1 (only-with-ss10)",
    ]


def test_invariant_delta_tells_a_reorder_and_a_shape_change_from_a_corpus_change():
    """A ledger reorder changes a list's order but not its members, and a key that changes shape (a count replaced by a list) is a change to the block, not to the corpus. Neither may be reported as classes appearing."""
    reordered = {
        **_ACCEPTED_INVARIANT,
        "classes": ["identical-variant-name", "split-by-space-or-zwnj", "only-with-ss10"],
    }
    assert invariant_delta(_ACCEPTED_INVARIANT, reordered) == ["classes reordered"]
    counted = {**_ACCEPTED_INVARIANT}
    counted["classes_count"] = counted.pop("classes")
    assert invariant_delta(counted, _ACCEPTED_INVARIANT) == [
        "classes_count no longer recorded",
        "classes newly recorded",
    ]


def test_invariant_diff_is_the_blocks_own_unified_diff():
    """The cycle prints this when the invariant block changed: the block's unified diff without the volatile block, formatted like the pins file so the lines match what `git diff` shows for those keys."""
    current = {**_ACCEPTED_INVARIANT, "unmatched_groups": ["new-join-at-baseline"]}
    lines = invariant_diff(_ACCEPTED_INVARIANT, current)
    assert lines[:2] == ["--- invariant (accepted)", "+++ invariant (this corpus)"]
    assert '-    "only-with-ss10"' in lines
    assert all("units" not in line for line in lines)
    assert invariant_diff(_ACCEPTED_INVARIANT, dict(_ACCEPTED_INVARIANT)) == []


def test_ledger_coverage_holds_the_ledgers_declarations_against_what_the_corpus_reached():
    """A class is machine-approved when the build approved any of its units, so the ledger's ink-identical declarations and the machine-approved classes can disagree in both directions. A no-verdict declaration or a ledger entry is unreached when no unit in the corpus matches it. `ledger_coverage` compares the ledger's declarations with the invariant block."""
    ledger = [
        _ledger_entry("split-by-space-or-zwnj", no_verdict=True),
        _ledger_entry("identical-variant-name", ink_identical=True),
        _ledger_entry("identical-see-out-fusion", ink_identical=True),
        _ledger_entry("vie-closer-at-baseline-join", no_verdict=True),
    ]
    invariant = {
        "classes": [
            "split-by-space-or-zwnj",
            "identical-variant-name",
            "identical-see-out-fusion",
            "only-with-ss10",
        ],
        "machine_approved_classes": ["split-by-space-or-zwnj", "identical-variant-name", "only-with-ss10"],
        "no_verdict_classes": ["split-by-space-or-zwnj"],
        "unmatched_groups": ["only-with-ss10"],
    }
    coverage = ledger_coverage(ledger, invariant)
    assert coverage.unreached == ("vie-closer-at-baseline-join",)
    assert coverage.ink_declared == ("identical-variant-name", "identical-see-out-fusion")
    assert coverage.ink_declared_unapproved == ("identical-see-out-fusion",)
    assert coverage.machine_approved_undeclared == ("split-by-space-or-zwnj", "only-with-ss10")
    assert coverage.no_verdict_declared == ("split-by-space-or-zwnj", "vie-closer-at-baseline-join")
    assert coverage.no_verdict_reached == ("split-by-space-or-zwnj",)
    assert coverage.no_verdict_unreached == ("vie-closer-at-baseline-join",)
    assert coverage.describe() == (
        "machine-approved: 3 classes approve units, 2 undeclared;"
        " ink-identical: 2 declared, 1 (identical-see-out-fusion) approving none;"
        " no-verdict: 1 of 2 declared reached, unreached 1 (vie-closer-at-baseline-join);"
        " ledger: 3 of 4 classes reached, unreached 1 (vie-closer-at-baseline-join)"
    )
    assert coverage.as_json()["ink_declared_unapproved"] == ["identical-see-out-fusion"]


def test_ledger_coverage_reads_clean_when_the_ledger_and_the_corpus_agree():
    ledger = [
        _ledger_entry("split-by-space-or-zwnj", no_verdict=True),
        _ledger_entry("identical-variant-name", ink_identical=True),
    ]
    coverage = ledger_coverage(ledger, _ACCEPTED_INVARIANT)
    assert coverage.unreached == ()
    assert coverage.ink_declared_unapproved == ()
    assert coverage.no_verdict_unreached == ()
    assert coverage.describe() == (
        "machine-approved: 2 classes approve units, 1 undeclared; ink-identical: 1 declared, all approving;"
        " no-verdict: 1 of 1 declared reached; ledger: 2 of 2 classes reached"
    )
