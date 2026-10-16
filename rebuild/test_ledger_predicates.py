"""Guard tests for the divergence ledger's `match.predicate` values against the oracle that resolves them.

`oracle.compile_ledger` refuses a predicate it does not define wherever it compiles a ledger, and run_m1 compiles the ledger first in its pre-gate guards, before the pass builds a table or writes its Stage A record. These tests check the live ledger against the oracle in both directions: every predicate the ledger names resolves, and every predicate the oracle defines is used by exactly one entry.
"""

from collections import Counter
from pathlib import Path

import yaml

from rebuild.pipeline import oracle

LEDGER_PATH = Path(__file__).resolve().parent / "m1-divergences.yaml"


def _predicates() -> Counter[str]:
    entries = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))
    return Counter(entry["match"]["predicate"] for entry in entries)


def test_every_ledger_predicate_resolves_in_the_oracle():
    unresolved = sorted(set(_predicates()) - set(oracle.PREDICATES))
    assert unresolved == [], f"ledger predicates the oracle does not define: {unresolved}"


def test_every_class_predicate_is_used_by_exactly_one_ledger_entry():
    """A `CLASS_PREDICATE_IDS` predicate no entry uses leaves its class's rows unmatched, and one that two entries use fails the gate on every row it matches. `oracle.PREDICATES` holds those and the two function predicates, which are held to the same count."""
    used = _predicates()
    assert set(oracle.CLASS_PREDICATE_IDS) <= set(oracle.PREDICATES)
    assert {name: used[name] for name in oracle.PREDICATES if used[name] != 1} == {}
