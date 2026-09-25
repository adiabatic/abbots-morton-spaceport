"""Checks that `oracle_cache.ORACLE_ROW_CODE_PATHS` and `POSITION_CODE_PATHS` name exactly the code the oracle comparison runs.

`ORACLE_ROW_CODE_PATHS` is a hand-written list, not a glob over `rebuild/pipeline/`. If a module that runs in `_compare_row` or `_SettledWindowWalk` is missing from it, the store's stamp does not hash that module, and a fix to it would be skipped and the old row verdict served as current. This test walks the import graph from the module that defines those two entry points and requires every reachable module to be listed. It also checks the other direction, that every listed module is reachable, because a listed module the comparison never reaches drops the whole store on every commit that edits it.

The walk is at module grain, as in `rebuild/test_plumbing_closure.py`. That errs in the safe direction: a module conform.py imports for a purpose the comparison never uses is still stamped. The opposite error, a module the comparison runs that the import graph does not show, would need a dynamic import, and the walked modules use none. The walk follows `if TYPE_CHECKING:` imports too, because `ast.walk` ignores the guard, so a type-only import is stamped like any other.

Because of the module grain, the witness stage's rule replay is in `rebuild/pipeline/witness.py` and not in conform.py. The replay imports `rebuild/pipeline/emit_gsub.py`, and if it were in conform.py, the roster would have to name the emitter.

The position channel has its own walk, from `rebuild/pipeline/oracle_positions.py`, the only module `POSITION_CODE_PATHS` names. The position stamp is added on top of the row stamp, so everything the channel reaches must be named by one of the two rosters. `rebuild/pipeline/oracle.py`, the classifier, must be unreachable from the channel: it re-runs over every served verdict and is in neither roster, and if the channel imported it, a predicate edit would change `position_code` and re-shape every position.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import conform, oracle_cache, oracle_positions

REPO_ROOT = Path(__file__).resolve().parent.parent

ORACLE_ENTRY_MODULES = ("rebuild.pipeline.conform",)
POSITION_ENTRY_MODULES = ("rebuild.pipeline.oracle_positions",)
POSITION_CHANNEL_NAMES = frozenset(
    {
        "_position_drift",
        "_kern_normalized_positions",
        "_cached_position",
        "_served_position",
        "_verify_served_positions",
        "KernEvaluator",
        "_shaper_for",
    }
)


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Return every repo module the file imports. Only absolute imports are read, because the walked modules use none of the relative kind. A `from X import y` is recorded as both X and X.y so that a submodule import is followed."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """Return the repo modules the entry points import transitively, keyed by module name."""
    seen: dict[str, Path] = {}
    queue = list(entry_points)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        queue.extend(_imports(path))
    return seen


def _reached_files() -> set[Path]:
    reached = reachable_modules(ORACLE_ENTRY_MODULES)
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    return {path for path in reached.values() if path.name != "__init__.py"}


def test_both_comparison_entry_points_live_in_the_walked_module():
    """The walk starts from conform.py, so `_compare_row` and `_SettledWindowWalk` must be defined there. If either moved to another file, the walk tests would keep passing while covering the wrong graph."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "conform.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert {"_compare_row", "_SettledWindowWalk"} <= defined, (
        "the oracle cache's two comparison entry points are no longer defined in rebuild/pipeline/conform.py, "
        f"so ORACLE_ENTRY_MODULES names the wrong graph; conform.py defines {sorted(defined & {'_compare_row', '_SettledWindowWalk'})}"
    )
    assert hasattr(conform, "_compare_row") and hasattr(conform, "_SettledWindowWalk")


def test_the_rule_replay_lives_outside_the_comparisons_closure():
    """The witness stage's rule replay, which reads the GSUB emitter's rule fold, is defined in rebuild/pipeline/witness.py, the comparison's walk does not reach `emit_gsub`, and the roster does not name emit_gsub.py. An edit to the emitter alone therefore keeps every stored row verdict. The generic tests below would accept the replay moving back into conform.py if the roster gained emit_gsub.py; this test fails on that move."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "witness.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, ast.FunctionDef)}
    replay = {
        "_first_matching_rule",
        "_matched_windows",
        "_renamed_rules_by_input",
        "check_rule_certificates",
    }
    assert replay <= defined, f"witness.py defines {sorted(defined & replay)} of the replay's four functions"
    assert "rebuild.pipeline.emit_gsub" not in reachable_modules(
        ORACLE_ENTRY_MODULES
    ), "the comparison's import closure reaches emit_gsub again, so an emitter edit drops the oracle row store whole"
    assert "rebuild/pipeline/emit_gsub.py" not in oracle_cache.ORACLE_ROW_CODE_PATHS


def test_oracle_row_code_paths_covers_the_comparison_import_graph():
    files = _reached_files()
    assert (
        REPO_ROOT / "rebuild" / "pipeline" / "settle.py" in files
    ), "the walk found nothing; the entry module moved"
    named = {REPO_ROOT / relative for relative in oracle_cache.ORACLE_ROW_CODE_PATHS}
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - named)
    assert uncovered == [], (
        "these modules run in the oracle's comparison but ORACLE_ROW_CODE_PATHS does not name them, so the whole-store "
        "stamp does not hash them and a fix to one would be served around as though the previous pass had already "
        f"applied it: {', '.join(uncovered)}. Add each to ORACLE_ROW_CODE_PATHS in rebuild/pipeline/oracle_cache.py, "
        "or move the code out of the comparison's reach."
    )


def test_the_named_oracle_closure_holds_no_module_the_comparison_never_reaches():
    """Every module the roster names must be reachable from the comparison. An extra name does not make results wrong, but it drops the whole store on a commit that could not have changed a verdict."""
    files = _reached_files()
    strays = sorted(
        relative for relative in oracle_cache.ORACLE_ROW_CODE_PATHS if REPO_ROOT / relative not in files
    )
    assert strays == [], (
        "named in ORACLE_ROW_CODE_PATHS but unreachable from the comparison, so every store drops whenever one of "
        f"them moves and nothing is bought for it: {', '.join(strays)}"
    )


def test_every_named_oracle_path_exists():
    """Every path `oracle_code_paths` returns must exist. `fingerprint.hash_paths` skips a missing path without error, so a renamed module would drop out of the stamp silently. This also covers `Cargo.toml` and `Cargo.lock`, which `oracle_code_paths` names directly; the Rust sources come from a glob."""
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in oracle_cache.oracle_code_paths(REPO_ROOT)
        if not path.is_file()
    ]
    assert missing == [], f"the oracle row cache's stamp names files that are not there: {', '.join(missing)}"


def _position_reached_files() -> set[Path]:
    reached = reachable_modules(POSITION_ENTRY_MODULES)
    return {path for path in reached.values() if path.name != "__init__.py"}


def test_the_position_channel_s_entry_points_live_in_the_walked_module():
    """The position walk starts from oracle_positions.py, so every name in `POSITION_CHANNEL_NAMES` must be defined there. If one moved to oracle.py, the walk tests would keep passing while the stamp covered the wrong code."""
    source = ast.parse(
        (REPO_ROOT / "rebuild" / "pipeline" / "oracle_positions.py").read_text(encoding="utf-8")
    )
    defined = {node.name for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert POSITION_CHANNEL_NAMES <= defined, (
        "the position channel's entry points are not all defined in rebuild/pipeline/oracle_positions.py, so "
        f"POSITION_ENTRY_MODULES names the wrong graph; it defines {sorted(defined & POSITION_CHANNEL_NAMES)}"
    )
    assert all(hasattr(oracle_positions, name) for name in POSITION_CHANNEL_NAMES)


def test_the_position_channel_s_import_graph_is_inside_the_two_rosters():
    """The position stamp is added on top of the row stamp, so a module the channel reaches is covered when either roster names it. A fix to a module neither roster names would be skipped for served positions."""
    files = _position_reached_files()
    assert (
        REPO_ROOT / "rebuild" / "pipeline" / "conform.py" in files
    ), "the walk found nothing; the entry module moved"
    named = {
        REPO_ROOT / relative
        for relative in oracle_cache.ORACLE_ROW_CODE_PATHS + oracle_cache.POSITION_CODE_PATHS
    }
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - named)
    assert uncovered == [], (
        "these modules run in the oracle's position channel but neither ORACLE_ROW_CODE_PATHS nor "
        "POSITION_CODE_PATHS names them, so a fix to one would be served around as though the previous pass had "
        f"already applied it: {', '.join(uncovered)}"
    )


def test_the_classifier_lives_outside_the_position_channel_s_closure():
    """The classifier and the ledger match are defined in rebuild/pipeline/oracle.py, the channel's walk does not reach `rebuild.pipeline.oracle`, and neither roster names oracle.py. A predicate edit therefore keeps every stored position and every stored row. This test fails if the channel imports oracle.py, even when a roster is extended to cover it."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "oracle.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, ast.FunctionDef)}
    assert {"classify_divergence", "compile_ledger", "_match_compiled"} <= defined
    assert "rebuild.pipeline.oracle" not in reachable_modules(
        POSITION_ENTRY_MODULES
    ), "the position channel's import closure reaches oracle.py, so a classifier edit re-shapes every position"
    assert "rebuild/pipeline/oracle.py" not in oracle_cache.ORACLE_ROW_CODE_PATHS
    assert "rebuild/pipeline/oracle.py" not in oracle_cache.POSITION_CODE_PATHS


def test_the_position_roster_names_only_the_channel_and_every_entry_exists():
    """Every path in `POSITION_CODE_PATHS` must exist and be reachable from the channel. `oracle_code_paths` does not cover this roster and `fingerprint.hash_paths` skips a missing path, so a renamed channel would drop out of the position stamp silently. A name the walk never reaches would re-shape every position on an edit that could not change one."""
    files = _position_reached_files()
    missing = [
        relative for relative in oracle_cache.POSITION_CODE_PATHS if not (REPO_ROOT / relative).is_file()
    ]
    assert missing == [], f"POSITION_CODE_PATHS names files that are not there: {', '.join(missing)}"
    strays = [relative for relative in oracle_cache.POSITION_CODE_PATHS if REPO_ROOT / relative not in files]
    assert (
        strays == []
    ), f"named in POSITION_CODE_PATHS but unreachable from the position channel: {', '.join(strays)}"
