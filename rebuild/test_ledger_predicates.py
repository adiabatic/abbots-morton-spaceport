"""Guard tests for the divergence ledger's `match.predicate` values against the oracle that resolves them.

The oracle drops a ledger entry whose predicate it cannot resolve (`oracle.compile_ledger`), so a stale or mistyped predicate matches no row, and the rows it should have matched fall to UNMATCHED, which never fails the run_m1 gate. These tests check the live ledger against the oracle in both directions, and check `audit.LEDGER_PREDICATES`, the copy `audit.load_ledger` validates against at review-build time, against the oracle. They live apart from rebuild/test_review_audit.py so that only this file's closure holds the oracle.
"""

from collections import Counter
from pathlib import Path

import yaml

from rebuild.pipeline import oracle
from rebuild.review import audit

LEDGER_PATH = Path(__file__).resolve().parent / "m1-divergences.yaml"


def _predicates() -> Counter[str]:
    entries = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))
    return Counter(entry["match"]["predicate"] for entry in entries)


def test_the_review_builds_predicate_list_is_the_oracles():
    """`load_ledger` cannot import the oracle, so it checks predicates against its own list, which must name exactly what the oracle resolves."""
    assert audit.LEDGER_PREDICATES == frozenset(oracle.PREDICATES)


def test_every_ledger_predicate_resolves_in_the_oracle():
    unresolved = sorted(set(_predicates()) - set(oracle.PREDICATES))
    assert unresolved == [], f"ledger predicates the oracle does not define: {unresolved}"


def test_every_class_predicate_is_used_by_exactly_one_ledger_entry():
    """A `CLASS_PREDICATE_IDS` predicate no entry uses leaves its class's rows unmatched, and one that two entries use fails the gate on every row it matches. `oracle.PREDICATES` holds those and the two function predicates, which are held to the same count."""
    used = _predicates()
    assert set(oracle.CLASS_PREDICATE_IDS) <= set(oracle.PREDICATES)
    assert {name: used[name] for name in oracle.PREDICATES if used[name] != 1} == {}
