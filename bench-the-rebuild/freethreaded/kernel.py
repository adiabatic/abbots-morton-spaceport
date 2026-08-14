"""Shared slice definition for the free-threaded settlement-fixpoint experiment.

Everything here is read-only against the repo: `build_tables(spec, features)` builds in memory and
writes no files. The slice is `rebuild.pipeline.table.build_tables` over a
*subset spec* (a rune subset of the real 18) so a full scaling sweep fits inside the runner budget;
the kernel code exercised is byte-for-byte the production kernel.

`checksum(decision, treaty)` is a content digest of the built tables. Two runs that agree on it
computed the same answer, whichever interpreter and however many threads produced it.
"""

from __future__ import annotations

import hashlib
import os
import sys

REPO = os.environ.get("AMS_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from rebuild.pipeline import conform, table as table_module  # noqa: E402
from rebuild.pipeline.model import ResolvedSpec  # noqa: E402
from rebuild.pipeline.spec_load import load_default_spec  # noqa: E402

CONFIGS = list(conform.ACCEPTANCE_CONFIGS)


def subset_spec(spec: ResolvedSpec, keep: int | None) -> ResolvedSpec:
    """The spec cut to `keep` single-letter runes, every ligature component taken first so all three
    modelled ligatures survive — otherwise the subset silently drops the section 5.7 late-formation
    guard, whose engines are the one thing config-parallel threads genuinely share. `keep=None` is
    the untouched 18-rune production spec."""
    singles = sorted(n for n, r in spec.runes.items() if not r.sequence)
    if keep is None or keep >= len(singles):
        return spec
    import dataclasses

    needed = sorted({p for r in spec.runes.values() if r.sequence for p in r.sequence})
    ordered = needed + [n for n in singles if n not in needed]
    alive = set(ordered[:keep])
    for name, rune in spec.runes.items():
        if rune.sequence and all(part in alive for part in rune.sequence):
            alive.add(name)
    runes = {n: r for n, r in spec.runes.items() if n in alive}
    return dataclasses.replace(spec, runes=runes)


def load(keep: int | None) -> ResolvedSpec:
    return subset_spec(load_default_spec(), keep)


def checksum(decision, treaty) -> str:
    """A digest over everything the built tables assert: every rule row, every reachable cell,
    every enumerated window, every treaty row, and the fired-provenance set."""
    h = hashlib.blake2b(digest_size=16)
    h.update(decision.config.encode())
    for rule in decision.rules:
        h.update(repr(table_module._rule_row(rule)).encode())
        h.update(b"\x00")
    h.update(b"|cells|")
    for cell in sorted(repr(c) for c in decision.reachable_cells()):
        h.update(cell.encode())
        h.update(b"\x00")
    h.update(b"|windows|")
    for w in sorted(repr(t.key) for t in decision.transitions):
        h.update(w.encode())
        h.update(b"\x00")
    h.update(b"|treaty|")
    for row in sorted(repr(r) for r in treaty.rows):
        h.update(row.encode())
        h.update(b"\x00")
    h.update(b"|fired|")
    for p in sorted(decision.cited_provenance):
        h.update(p.encode())
        h.update(b"\x00")
    return h.hexdigest()


def build_one(spec: ResolvedSpec, config: str) -> tuple[str, int, int, int]:
    """One configuration's fixpoint. Returns (checksum, n_rules, n_windows, n_cells)."""
    features = conform.features_for_config(config)
    decision, treaty = table_module.build_tables(spec, features)
    return (
        checksum(decision, treaty),
        len(decision.rules),
        len(decision.transitions),
        len(decision.reachable_cells()),
    )
