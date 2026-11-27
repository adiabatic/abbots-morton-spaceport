"""The settle memo's keys: the whole-memo stamp a part records and the per-family keys its rows are served under. The memo itself, its parts and its walks are in engine/pipeline/conform.py (`SettleMemoFile`, `_MemoStore`, `_SettledWindowWalk`); the string replay fills it, and the witness stage and the conformance sweep map it.

A memo entry is one settled window, and by the window locality rule (`doc/engine-design.md` §10) its settlement depends on the rune files its six slots name, their `spec_load.rune_closure`, and the inputs that state the cross-rune structure. So there are two keys. The stamp (`settle_memo_stamp`) covers everything that can change a settlement without changing a named family's key: the walk's code closure (`SETTLE_MEMO_CODE_PATHS` and the crate, `settle_memo_code_paths`), the non-rune data (`stamped_data_paths`), the resolved spec structure and the capability-feature set, the engine's settlement flags and the configuration's feature set. The family keys (`settle_family_keys`) cover each family's prose-insensitive rune digest and the digests of the runes in its closure. A family present in only one of the recorded and current key maps counts as moved (`moved_families`).

An entry's reach is read from its window's labels: the six slots' families, the rune a formed ligature label names, and every ligature rune whose components all appear among them. `StaleMask.bit_of` maps one label to its bit. The memo is a base and segments under a head (`row_delta`'s probe merge), and the head records the stamp and family keys each part was settled under, so a reader builds one `StaleMask` a part, over `moved_families` between that part's recorded keys and the current ones: a row a segment added in one pass and a family key moved in the next is masked whether or not that pass asked its window again, and the next absorb names it in its segment's dead bitmap (`conform._MemoStore`, `conform.absorb_settle_memo`). `SettleMemoInputs` is the disk-derived half of both keys, read before the spec is loaded, as `run_build.tables_inputs` is. The settlements are then at least as new as the content the keys name, so a rune edited during a run ends up under a key the next pass reports as moved, whichever side of the load the edit happened on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Iterable, Mapping

from engine.pipeline import fingerprint, kernel_exec, spec_load
from engine.pipeline.fingerprint import EnvironmentStamp
from engine.pipeline.model import ResolvedSpec

# The modules the settle memo's walk (`conform._SettledWindowWalk`) imports, directly or transitively: the walk and its settlement, the stream vocabulary (`labels`), the crate driver, the spec loader, the fingerprints the keys are computed from, and this module. engine/test_settle_memo_code_closure.py walks the import graph from conform.py on every contracts run and fails when a reachable module outside its stop-set is missing here or a listed module is unreachable. The shapers are listed because conform.py imports them for the conformance sweep, `output_sink` because conform.py writes its summary through it, and `row_delta` because `table` reads the windows outputs through it. The witness stage's rule replay is in witness.py, which imports conform.py and emit_gsub.py, so emit_gsub.py is outside this closure and an emitter edit keeps the memo. The walk's stop-set is engine/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, so the engine/tools modules the walk imports are not listed, and none of them can change a settlement: `kernel_exec` imports `memory_budget` to size its fan-out and `peak_rss` to read its peaks, and the streams are byte-identical at any width. `fingerprint` imports `lock_digest.lock_digest`, which no settlement reads. The tables stamp and the run_build skip key leave them out too.
SETTLE_MEMO_CODE_PATHS = (
    "engine/pipeline/conform.py",
    "engine/pipeline/fingerprint.py",
    "engine/pipeline/geometry.py",
    "engine/pipeline/kernel_exec.py",
    "engine/pipeline/kernel_io.py",
    "engine/pipeline/labels.py",
    "engine/pipeline/model.py",
    "engine/pipeline/output_sink.py",
    "engine/pipeline/row_delta.py",
    "engine/pipeline/settle.py",
    "engine/pipeline/settle_memo_keys.py",
    "engine/pipeline/shapers.py",
    "engine/pipeline/spec_load.py",
    "engine/pipeline/table.py",
)


def settle_memo_code_paths(repo_root: Path) -> list[Path]:
    """Return `SETTLE_MEMO_CODE_PATHS` resolved against a checkout, plus the kernel crate's manifest, lock and every Rust source file. The crate decides every settlement the memo stores, and a hand-kept list of its modules would miss the next one added."""
    root = Path(repo_root)
    kernel = root / "engine" / "kernel-rs"
    return (
        [root / relative for relative in SETTLE_MEMO_CODE_PATHS]
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
    )


def stamped_data_paths(repo_root: Path) -> list[Path]:
    """Return the data inputs the settle memo's stamp hashes, each as `fingerprint.data_path_lines` hashes it, so a schema hover or a comment in a data file does not move it: `fingerprint.data_paths` without the rune files, which the family keys cover. The contact allow-list, which no settlement reads, is not in `fingerprint.data_paths`."""
    root = Path(repo_root)
    runes = set(fingerprint.rune_paths(root))
    return [path for path in fingerprint.data_paths(root) if path.is_file() and path not in runes]


def reach_lines(name: str, digests: Mapping[str, str], closure: Mapping[str, frozenset[str]]) -> list[str]:
    """Return the `member\\tdigest` lines for the family and every rune in its `spec_load.rune_closure` (the runes reachable from it through ligature trailing components and `resolve.against` targets, transitively), which are the runes whose content its records read directly."""
    reach = sorted({name} | set(closure.get(name, frozenset())))
    return [f"{member}\t{digests.get(member, '-')}" for member in reach]


@dataclass(frozen=True)
class SettleMemoInputs:
    """The disk-derived half of the settle memo's keys, computed before the spec they describe is loaded, as `run_build.tables_inputs` is, so the settlements are at least as new as the content these keys name. `settle_memo_stamp` computes the spec-derived half (`spec_structure`, `capability_features`) from the loaded spec that settles."""

    rune_digests: Mapping[str, str]
    code: str
    data: str


def settle_memo_inputs(repo_root: Path) -> SettleMemoInputs:
    root = Path(repo_root)
    return SettleMemoInputs(
        rune_digests=fingerprint.rune_digests(root),
        code=fingerprint.hash_paths(root, settle_memo_code_paths(root)),
        data=fingerprint.digest_lines(fingerprint.data_path_lines(root, stamped_data_paths(root))),
    )


def settle_family_keys(inputs: SettleMemoInputs, spec: ResolvedSpec) -> dict[str, str]:
    """Return, per rune family, the key a memo entry's staleness test compares: a digest over the family's prose-insensitive rune digest and the digests of its `spec_load.rune_closure`. A settlement depends only on the rune files a window names and their closure."""
    closure = spec_load.rune_closure(spec)
    return {
        name: fingerprint.digest_lines(reach_lines(name, inputs.rune_digests, closure))
        for name in sorted(inputs.rune_digests)
    }


def settle_memo_stamp(
    inputs: SettleMemoInputs, spec: ResolvedSpec, config: str, features: Collection[str]
) -> EnvironmentStamp:
    """Return the stamp over everything that can change a memoized settlement without changing a named family's key. The memo's format is not a line, because each part of the memo records it in its own header. It uses `settlement_flags` and not `kernel_exec.enumeration_tokens`, because the walk settles windows one at a time and never enumerates, so deep-class grain cannot affect it. While the alphabet switch is set, the lines end with its token (`spec_load.alphabet_lines`), so a memo over the whole alphabet never serves a small one, nor the reverse; with it unset no line is added."""
    lines = (
        f"config\t{config}",
        "features\t" + json.dumps(sorted(features)),
        f"code\t{inputs.code}",
        f"data\t{inputs.data}",
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        *spec_load.alphabet_lines(),
    )
    return EnvironmentStamp(lines=lines)


def moved_families(recorded: Mapping[str, str], current: Mapping[str, str]) -> frozenset[str]:
    """Return every family whose key differs between the recorded and current maps. A name present in only one map counts as moved, so a rune file that appears or disappears is a miss and never a match."""
    moved = set(current.keys() ^ recorded.keys())
    for name in current.keys() & recorded.keys():
        if current[name] != recorded[name]:
            moved.add(name)
    return frozenset(moved)


class StaleMask:
    """The per-entry staleness test, as a bitmask over the families an entry can reach. There is one bit per family, in sorted registry order. `mask_of` builds a mask from codepoints and `bit_of` from one label, and `stale` returns whether any moved family is inside it: directly, for a family with a codepoint, or through the ligature clause, which applies only when every component of a moved ligature rune is in the mask. The ligature clause is read from `spec.registry.families[...].sequence` instead of a `_`-split of the name, because `settle.form_ligatures` routes on the sequence. A moved ligature rune also sets its own bit, because the settle memo keys windows on formed labels: a window whose slot holds `qsThey_qsUtter` names that rune and none of its components, so the component clause alone would serve it after an edit to the ligature's file. A moved family that the registry places by neither codepoint nor sequence makes every entry stale, since over-invalidation is the safe direction."""

    def __init__(self, spec: ResolvedSpec, moved: Collection[str] = ()) -> None:
        families = spec.registry.families
        self.spec = spec
        self._bit: dict[str, int] = {name: 1 << index for index, name in enumerate(sorted(families))}
        self._family_of: dict[int, str] = {
            info.codepoint: name for name, info in families.items() if info.codepoint is not None
        }
        self._ligatures: dict[str, int] = {}
        for name, info in families.items():
            if not info.sequence:
                continue
            bits = 0
            for component in info.sequence:
                bits |= self._bit.get(component, 0)
            self._ligatures[name] = bits
        self.moved = frozenset(moved)
        self.everything = False
        symbols = 0
        ligatures: list[int] = []
        for name in sorted(self.moved):
            info = families.get(name)
            if info is not None and info.codepoint is not None:
                symbols |= self._bit[name]
            elif info is not None and info.sequence:
                symbols |= self._bit[name]
                ligatures.append(self._ligatures[name])
            else:
                self.everything = True
        self._stale_symbols = symbols
        self._stale_ligatures = tuple(sorted(set(ligatures)))
        self._reach: dict[int, tuple[str, ...]] = {}

    def mask_of(self, codepoints: Iterable[int]) -> int:
        mask = 0
        for codepoint in codepoints:
            name = self._family_of.get(codepoint)
            if name is not None:
                mask |= self._bit[name]
        return mask

    def bit_of(self, family: str) -> int:
        """Return the bit for one family name, or zero for a name the registry does not place (a boundary label, a window edge, or an unknown family). The settle memo uses this because its windows name families by label, not by codepoint. A formed ligature label maps to the ligature rune's own bit, which `stale` checks directly when that rune moved."""
        return self._bit.get(family, 0)

    def stale(self, mask: int) -> bool:
        if self.everything or mask & self._stale_symbols:
            return True
        return bool(self._stale_ligatures) and any((mask & bits) == bits for bits in self._stale_ligatures)

    def families_of(self, mask: int) -> tuple[str, ...]:
        """Return every family a mask can reach: the letters its codepoints name, plus each ligature rune whose components are all among them. Memoized on the mask, because many entries share a mask."""
        cached = self._reach.get(mask)
        if cached is None:
            names = [name for name, bit in self._bit.items() if mask & bit]
            names += [name for name, bits in self._ligatures.items() if bits and (mask & bits) == bits]
            cached = tuple(sorted(set(names)))
            self._reach[mask] = cached
        return cached
