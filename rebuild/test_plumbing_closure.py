"""The verdict plumbing's green record claims that re-running the chain would write nothing, and that claim is only as good as the key's coverage of the chain's own code. This walks the import graph from the one entry point — rebuild.tools.verdict_chain, which runs every step — and requires every repo module it reaches to sit in one of the fingerprints the key already carries. Hashing the whole of rebuild/tools/ would be sound but would make a commit touching any unrelated tool re-run the chain; naming the closure is only safe while something checks the name, and this is that check. The cycle driver is not an entry point even though it builds the chain's argv: every argument it hands over names an input the key already hashes — the surface, a snapshot of that same surface, the master, the store — or a flag that disables the skip outright, and the chain's own flag parsing lives in verdict_chain.

One scoping choice, the same one rebuild/test_review_code_closure.py makes at the rebuild/tools boundary and for the same reason: the walk records a module whose file rides `fingerprint.pipeline_code_paths` but does not expand it. The plumbing key carries that component whole through its manifest line, so what a pipeline module reaches beyond it is that component's coverage question rather than this roster's — kernel_exec takes memory_budget and peak_rss out of rebuild/tools as a fan-out width and a cost reading, neither of which can move a byte the chain writes, and `pipeline_code_paths` deliberately leaves both out. Stopping there is safe because of the direction rule: rebuild/review and rebuild/tools import rebuild/pipeline and never the reverse, so nothing on the chain's own side can hide behind the boundary. `oracle_cache.ORACLE_ROW_CODE_PATHS` chose the other way and hashes both yardsticks into its store's key, which is right for a store whose rows are keyed on the width a fan-out ran at; this key belongs to a chain that fans nothing out and takes no reading, so a width or telemetry edit leaves it where it was.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.review import serve
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import review_server

REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Every repo module the file names in an import, absolute form only — this tree has no relative imports, and a `from X import y` is recorded both as X and as X.y so a submodule import is followed."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """The transitive closure of repo modules the entry points import, keyed by module name, recording but not expanding a module that rides the manifest fingerprint's pipeline_code component (see the module docstring for why the walk stops at that boundary)."""
    seen: dict[str, Path] = {}
    pipeline = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    queue = list(entry_points)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        if path not in pipeline:
            queue.extend(_imports(path))
    return seen


def _covered(root: Path) -> set[Path]:
    covered = set(ac.plumbing_code_paths(root))
    covered.update(fingerprint.review_code_paths(root))
    covered.update(fingerprint.pipeline_code_paths(root))
    covered.add(root / "rebuild" / "review" / "serve.py")
    covered.add(root / "rebuild" / "review" / "status.py")
    covered.add(root / "rebuild" / "review" / "journal.py")
    return covered


def test_the_plumbing_key_covers_every_module_its_chain_reaches():
    reached = reachable_modules(ac.PLUMBING_ENTRY_POINTS)
    assert "rebuild.tools.standing_verdicts" in reached, "the walk found nothing; the entry points moved"
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    files = {path for path in reached.values() if path.name != "__init__.py"}
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - _covered(REPO_ROOT))
    assert uncovered == [], (
        "these modules run in the verdict chain but no fingerprint the plumbing key carries hashes them, "
        f"so a fix to one would be skipped as already proven: {', '.join(uncovered)}"
    )


def test_the_named_tool_closure_holds_no_module_the_chain_never_reaches():
    """The other direction, so the list stays the closure rather than drifting back into 'everything under rebuild/tools': every file it names must actually be reachable from the entry points."""
    reached = {path for path in reachable_modules(ac.PLUMBING_ENTRY_POINTS).values()}
    strays = sorted(
        str(path.relative_to(REPO_ROOT)) for path in ac.plumbing_code_paths(REPO_ROOT) if path not in reached
    )
    assert strays == [], f"named in PLUMBING_TOOL_MODULES but unreachable from the chain: {', '.join(strays)}"


def test_every_named_path_exists():
    missing = [str(path) for path in ac.plumbing_code_paths(REPO_ROOT) if not path.is_file()]
    assert missing == [], f"PLUMBING_TOOL_MODULES names files that are not there: {', '.join(missing)}"


def test_the_driver_and_the_width_and_telemetry_tools_stay_outside_the_chain():
    """Naming the closure only buys anything while the four modules it was named to shed stay shed: the cycle driver, the timings journal every run files a verdict through, and the two width yardsticks the pipeline takes its fan-out from are all edited for reasons that can never move a verdict, and each of them inside the key would re-run the whole chain. A chain tool that starts importing the driver again should fail here rather than be answered by putting the driver back on the roster."""
    outside = ("artifact_cycle", "cycle_timings", "memory_budget", "peak_rss")
    reached = reachable_modules(ac.PLUMBING_ENTRY_POINTS)
    inside = sorted(name for name in outside if f"rebuild.tools.{name}" in reached)
    assert inside == [], f"the chain reaches these again, so an edit to one re-runs it: {', '.join(inside)}"
    assert "rebuild.pipeline.kernel_exec" in reached, (
        "the walk no longer reaches the pipeline module the two yardsticks hide behind, "
        "so this test is passing by not exercising the boundary at all"
    )
    named = sorted(path.stem for path in ac.plumbing_code_paths(REPO_ROOT) if path.stem in outside)
    assert named == [], f"PLUMBING_TOOL_MODULES names them anyway: {', '.join(named)}"


def test_the_port_the_chain_probes_is_the_port_the_server_binds():
    """merge_verdicts refuses to write the store while the app is up, so the probe it asks has to name the port rebuild.review.serve actually binds. The two literals sat in separate files with nothing holding them equal until the probe became a module of its own."""
    assert "rebuild.tools.review_server" in reachable_modules(ac.PLUMBING_ENTRY_POINTS)
    assert review_server.REVIEW_PORT == serve.PORT
