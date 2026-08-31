"""The verdict plumbing's green record claims that re-running the chain would write nothing, and that claim is only as good as the key's coverage of the chain's own code. This walks the import graph from the two entry points — the chain itself and the driver that builds its argv — and requires every repo module it reaches to sit in one of the fingerprints the key already carries. The key used to hash the whole of rebuild/tools/ instead, which was sound but made a commit touching any unrelated tool re-run the chain; naming the closure is only safe while something checks the name, and this is that check."""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.tools import artifact_cycle as ac

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
    """The transitive closure of repo modules the entry points import, keyed by module name."""
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
