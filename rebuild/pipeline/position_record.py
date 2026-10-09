"""The position comparison's verdict in the form the oracle row store records it, and the width of the settled digest a record keeps beside it. Both sides import this module: the store's codec in rebuild/pipeline/oracle_cache.py and the position comparison in rebuild/pipeline/oracle_positions.py. It imports nothing from the repository, so the position comparison's import closure, which `oracle_cache.POSITION_CODE_PATHS` names, does not reach the store's module, its keys, or the settlement walk (rebuild/test_oracle_code_closure.py)."""

from __future__ import annotations

from dataclasses import dataclass

# Hex characters of `oracle_positions.settled_digest`, which every record stores after its row check digest.
SETTLED_DIGEST_WIDTH = 16
# The settled digest of a record written without one. It is no digest's spelling, so its position is never served across a row-stamp move.
NO_SETTLED_DIGEST = "-" * SETTLED_DIGEST_WIDTH


@dataclass(frozen=True, slots=True)
class CachedPosition:
    """One row's position-comparison verdict when the row's positions do not match: `oracle_positions._position_mismatch`'s mismatch descriptions, which the audit prints as a position-only row's new cells, and whether every mismatched slot follows a kern-attributable one. A row whose positions matched is stored as `None`, and a row the position comparison never shaped as `UNSHAPED`. Only a `CachedPosition` or `None` may be served, and `==` over them is the verification sample's check."""

    mismatches: tuple[str, ...]
    kern_attributable: bool


class _Unshaped:
    """The position record of a row the previous pass never shaped, because the row was kept out of the position comparison (a ligation or junction divergence, or a divergence without exactly one ledger match that claims identical ink) or no font was open. It is distinct from `None`, a shaped row that matched, so that a row the position comparison never saw is not counted as clean."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSHAPED"


UNSHAPED = _Unshaped()
PositionVerdict = CachedPosition | None | _Unshaped
