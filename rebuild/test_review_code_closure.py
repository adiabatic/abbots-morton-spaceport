"""Two rosters keep the review surface's stamps honest, and this walks the closures they claim.

`fingerprint.review_code_paths` is rebuild/review/ minus an exclusion list (`REVIEW_NON_BUILD_MODULES`), and the surface stamp's whole claim is that the list names exactly what the build cannot execute: an excluded module the build starts importing would leave the surface stale-blind to its edits, while a hashed module the build never reaches costs a full surface rebuild and both per-unit cache stores for an edit that proves nothing — the deforming pressure rebuild/test_memory_budget.py records declining a one-line hoist over. A hand-written exclusion list is only safe while something checks it, and this is that check: it walks the import graph from rebuild.review.build in both directions, at the same module grain as rebuild/test_plumbing_closure.py and rebuild/test_oracle_code_closure.py.

`unit_cache.surface_code_paths` is the second roster, and the per-unit store stamps' claim is the same one made of a wider tree: the pipeline and validation modules the build's walk reaches ride the stamp and no other does (`PIPELINE_NON_SURFACE_MODULES` is the pipeline side's exclusion list; validation is reached whole), so a pipeline edit the surface never executes keeps the store. The walk here therefore expands rebuild/pipeline and rebuild/validation as well as rebuild/review, and records without expanding what it reaches under rebuild/tools — the width and telemetry modules the build takes its fan-out and cost readings from, which cannot move a byte of a unit's products and are pinned here as the only such reach, so a new import that could cannot hide behind that boundary. The surface manifest's own fingerprint is untouched by any of this: its Stage A `pipeline_code` component still hashes the pipeline tree whole, because run_m1 records it and the readiness check reads it back, and only the two store stamps take the narrower closure.

The crate is the third closure and the one a Python walk cannot see. The surface reaches it through two verbs, `settle-cases` and `guard-sweep`, so `KERNEL_NON_SURFACE_MODULES` claims exactly the crate modules neither verb's handler reaches. That is walked at function grain through main.rs — the handler each verb's match arm calls, the module-level functions and impl blocks those name, and the crate modules any of them reach through the file's `use ams_m1_kernel::…` bindings — and then at module grain through the crate's own `crate::` references, comment lines dropped and the `#[cfg(test)]`-gated modules skipped, since a release binary compiles none of those. Both walks are regular expressions over rustfmt-formatted source rather than a parser, which errs in the safe direction: a reference the scan cannot place is a module hashed, never one left out.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.review import unit_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "review"
PIPELINE_DIR = REPO_ROOT / "rebuild" / "pipeline"
VALIDATION_DIR = REPO_ROOT / "rebuild" / "validation"
KERNEL_SRC = REPO_ROOT / "rebuild" / "kernel-rs" / "src"
EXPANDED_DIRS = (REVIEW_DIR, PIPELINE_DIR, VALIDATION_DIR)

BUILD_ENTRY_MODULES = ("rebuild.review.build",)
# What the build reaches under rebuild/tools, and all it may: the fan-out width (`artifact_cycle.surface_job_budget`, and the `memory_budget` arithmetic under it and under kernel_exec's own width) and the cost and progress readings (`peak_rss`, the `cycle_timings` pool record that files them, the `console` phase and progress lines the cycle reads back, and the `pile_tally` debug attribution a build prints only when its environment asks for one). None can move a byte of a unit's products — rebuild/test_unit_cache.py's serial-and-parallel byte identity holds the width half — so none rides `surface_code_paths`, and a new reach here is a claim to argue before the roster grows.
WIDTH_AND_TELEMETRY_MODULES = frozenset(
    {
        "rebuild.tools.artifact_cycle",
        "rebuild.tools.console",
        "rebuild.tools.cycle_timings",
        "rebuild.tools.memory_budget",
        "rebuild.tools.peak_rss",
        "rebuild.tools.pile_tally",
    }
)
KERNEL_SURFACE_VERBS = ("settle-cases", "guard-sweep")


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Every repo module the file names in an import, at any nesting: a `from X import y` is recorded both as X and as X.y so a submodule import is followed, and a relative `from .x import y` — rebuild/validation's own idiom — is resolved against the file's package before it is recorded."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                package = ".".join(base.relative_to(REPO_ROOT).parts)
                module = f"{package}.{node.module}" if node.module else package
            elif node.module:
                module = node.module
            else:
                continue
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """The transitive closure of repo modules the entry points import, keyed by module name, expanding modules under rebuild/review, rebuild/pipeline and rebuild/validation and recording without expanding whatever those reach elsewhere (see the module docstring for why the walk stops at the rebuild/tools boundary)."""
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
        if path.parent in EXPANDED_DIRS:
            queue.extend(_imports(path))
    return seen


def _reached() -> dict[str, Path]:
    reached = reachable_modules(BUILD_ENTRY_MODULES)
    assert "rebuild.review.unit_index" in reached, "the walk found nothing; the entry point moved"
    return reached


def _reached_files_under(directory: Path) -> set[Path]:
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    return {path for path in _reached().values() if path.parent == directory and path.name != "__init__.py"}


def _surface_stamped() -> set[Path]:
    return {path for path in unit_cache.surface_code_paths(REPO_ROOT) if path.name != "__init__.py"}


def _relative(paths) -> list[str]:
    return sorted(str(path.relative_to(REPO_ROOT)) for path in paths)


def test_every_review_module_the_build_reaches_is_stamped():
    stamped = set(fingerprint.review_code_paths(REPO_ROOT))
    unstamped = _relative(_reached_files_under(REVIEW_DIR) - stamped)
    assert unstamped == [], (
        "these modules run in the surface build but review_code does not hash them, so the surface would "
        "go stale-blind to their edits — remove them from REVIEW_NON_BUILD_MODULES: " + ", ".join(unstamped)
    )


def test_no_stamped_review_module_is_outside_the_builds_reach():
    reached = _reached_files_under(REVIEW_DIR)
    strays = _relative(
        path
        for path in fingerprint.review_code_paths(REPO_ROOT)
        if path not in reached and path.name != "__init__.py"
    )
    assert strays == [], (
        "review_code hashes these modules but the surface build never imports them, so an edit to one costs "
        "a full surface rebuild and both per-unit cache stores while proving nothing — add them to "
        "REVIEW_NON_BUILD_MODULES: " + ", ".join(strays)
    )


def test_every_pipeline_and_validation_module_the_build_reaches_rides_the_store_stamp():
    reached = _reached_files_under(PIPELINE_DIR) | _reached_files_under(VALIDATION_DIR)
    assert PIPELINE_DIR / "kernel_exec.py" in reached, "the walk never left rebuild/review; the seam moved"
    assert (
        VALIDATION_DIR / "shaping.py" in reached
    ), "the walk never reached the shaper; ink.py's import moved"
    unstamped = _relative(reached - _surface_stamped())
    assert unstamped == [], (
        "these modules run in the surface build but surface_code_paths does not hash them, so a served unit "
        "would outlive a fix to one — remove them from PIPELINE_NON_SURFACE_MODULES in "
        "rebuild/review/unit_cache.py: " + ", ".join(unstamped)
    )


def test_no_pipeline_or_validation_module_in_the_store_stamp_is_outside_the_builds_reach():
    reached = _reached_files_under(PIPELINE_DIR) | _reached_files_under(VALIDATION_DIR)
    strays = _relative(
        path
        for path in _surface_stamped()
        if path.parent in (PIPELINE_DIR, VALIDATION_DIR) and path not in reached
    )
    assert strays == [], (
        "surface_code_paths hashes these modules but the surface build never imports them, so an edit to one "
        "drops both per-unit stores and costs the next build a cold units phase while proving nothing — add "
        "them to PIPELINE_NON_SURFACE_MODULES in rebuild/review/unit_cache.py: " + ", ".join(strays)
    )


def test_the_build_reaches_nothing_outside_the_three_trees_but_width_and_telemetry():
    """The boundary the walk stops at, pinned in both directions: everything the build reaches outside the three expanded trees is a width or telemetry module, and every module on that roster is still reached. A module that could move a unit's bytes appearing here is the failure to take seriously; a roster entry the build stopped importing is only hygiene, but it is what keeps the roster the closure rather than a list."""
    outside = {name for name, path in _reached().items() if path.parent not in EXPANDED_DIRS}
    assert "rebuild.tools.memory_budget" in outside, (
        "the walk no longer crosses the rebuild/tools boundary at all, so this check exercises nothing; "
        "kernel_exec stopped taking its width from memory_budget"
    )
    assert outside == WIDTH_AND_TELEMETRY_MODULES, (
        "the surface build reaches modules outside rebuild/review, rebuild/pipeline and rebuild/validation that "
        "surface_code_paths does not hash and WIDTH_AND_TELEMETRY_MODULES does not vouch for — argue that each "
        f"cannot move a unit's bytes before adding it, or hash it: {sorted(outside ^ WIDTH_AND_TELEMETRY_MODULES)}"
    )


_CFG_TEST = re.compile(r"^#\[cfg\((?:test|any\(test)\b")
_ATTRIBUTE = re.compile(r"^#\[")


def _live_rust(path: Path) -> str:
    """The lines of one crate file a release build compiles and this scan may read: comment lines dropped, doc comments included — a `[`crate::fold`]` link in prose is not a reach — and every column-0 item under a `#[cfg(test)]` or `#[cfg(any(test, …))]` attribute skipped through its closing column-0 brace, with other attributes between the gate and the item stepped over. rustfmt is what makes column 0 the item boundary and the crate's fmt gate is what keeps it so; an indented `#[cfg(test)]` — a test-only method inside an impl — is left in, which over-includes and never under-includes."""
    kept: list[str] = []
    gated = False
    skipping = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if skipping:
            skipping = line != "}"
            continue
        if _CFG_TEST.match(line):
            gated = True
            continue
        if gated:
            if _ATTRIBUTE.match(line):
                continue
            gated = False
            skipping = line.rstrip().endswith("{")
            continue
        if line.lstrip().startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _main_blocks(source: str) -> dict[str, str]:
    """main.rs's column-0 functions by name and its column-0 impl blocks by `impl <Type>`, each with its body through the next column-0 closing brace."""
    blocks: dict[str, str] = {}
    for match in re.finditer(r"^(?:pub(?:\([^)]*\))? )?(?:fn (\w+)|impl(?:<[^>]*>)? (\w+))\b", source, re.M):
        end = source.find("\n}\n", match.start())
        body = source[match.start() : end + 2] if end != -1 else source[match.start() :]
        blocks[match.group(1) or f"impl {match.group(2)}"] = body
    return blocks


def _crate_bindings(source: str) -> dict[str, str]:
    """Every name main.rs binds from the library crate, mapped to the crate module it came from: `use ams_m1_kernel::engine::{Engine, EngineModes}` binds two names to `engine`, and `use ams_m1_kernel::{cases, guard}` binds each module to itself."""
    bindings: dict[str, str] = {}
    for match in re.finditer(r"^use ams_m1_kernel::(.+?);$", source, re.M | re.S):
        spec = match.group(1).replace("\n", " ")
        if spec.startswith("{"):
            names = [name.strip() for name in spec.strip("{} ").split(",")]
            for name in names:
                if name:
                    bindings[name] = name
            continue
        head, _, rest = spec.partition("::")
        names = [name.strip() for name in rest.strip("{} ").split(",")] if rest else [head]
        for name in names:
            if name:
                bindings[name.split(" as ")[-1].strip()] = head
    return bindings


def _verb_arm(source: str, verb: str) -> str:
    match = re.search(r'^\s+"' + re.escape(verb) + r'" => \{\n(.*?)^\s+\}\n', source, re.M | re.S)
    assert match, f"main.rs no longer dispatches {verb!r} through a match arm, so the crate walk has no entry"
    return match.group(1)


def _verb_reach() -> tuple[set[str], set[str]]:
    """The main.rs blocks the surface's two verbs reach, and the crate modules those blocks name — through the file's bindings, and through any inline `ams_m1_kernel::` path."""
    source = _live_rust(KERNEL_SRC / "main.rs")
    blocks = _main_blocks(source)
    bindings = _crate_bindings(source)
    assert bindings, "main.rs binds nothing from the library crate, so this walk would be vacuous"
    reached: set[str] = set()
    modules: set[str] = set()
    queue = [
        name for verb in KERNEL_SURFACE_VERBS for name in re.findall(r"\b(\w+)\(", _verb_arm(source, verb))
    ]
    while queue:
        name = queue.pop()
        if name in reached or name not in blocks:
            continue
        reached.add(name)
        body = blocks[name]
        for word in set(re.findall(r"\b\w+\b", body)):
            if word in bindings:
                modules.add(bindings[word])
            if f"impl {word}" in blocks:
                queue.append(f"impl {word}")
        queue.extend(re.findall(r"\b(\w+)\(", body))
        modules.update(re.findall(r"\bams_m1_kernel::(\w+)", body))
    return reached, modules


def _crate_references(path: Path) -> set[str]:
    return set(re.findall(r"\bcrate::(\w+)", _live_rust(path)))


def _verb_closure() -> set[Path]:
    """Every crate file the two verbs reach: main.rs and lib.rs always — the dispatcher and the crate root — and from the modules the handlers name, the module-grain `crate::` closure. A `crate::` path that names no file (`crate::SPEC_FORMAT`, a constant on the root) is a reach into lib.rs, which is already in."""
    reached, modules = _verb_reach()
    assert {"settle_cases", "guard_sweep"} <= reached, (
        f"the walk from the two verbs' match arms reached {sorted(reached)} and not both handlers; "
        "the handlers were renamed or the arms no longer call them directly"
    )
    files = {KERNEL_SRC / "main.rs", KERNEL_SRC / "lib.rs"}
    queue = sorted(modules)
    while queue:
        path = KERNEL_SRC / f"{queue.pop()}.rs"
        if path in files or not path.is_file():
            continue
        files.add(path)
        queue.extend(_crate_references(path))
    return files


def test_every_crate_module_the_settlement_verbs_reach_rides_the_store_stamp():
    reached = _verb_closure()
    for name in ("cases.rs", "guard.rs", "engine.rs", "parse.rs"):
        assert KERNEL_SRC / name in reached, f"the crate walk never reached {name}; the verbs' handlers moved"
    unstamped = _relative(reached - _surface_stamped())
    assert unstamped == [], (
        "settle-cases or guard-sweep runs these crate modules but surface_code_paths does not hash them, so "
        "a served unit's settlement or explain ladder would outlive an edit there — remove them from "
        "KERNEL_NON_SURFACE_MODULES in rebuild/review/unit_cache.py: " + ", ".join(unstamped)
    )


def test_no_crate_module_in_the_store_stamp_is_outside_the_verbs_reach():
    reached = _verb_closure()
    strays = _relative(path for path in _surface_stamped() if path.suffix == ".rs" and path not in reached)
    assert strays == [], (
        "surface_code_paths hashes these crate modules but neither settle-cases nor guard-sweep reaches them, "
        "so an edit to one drops both per-unit stores for a settlement that cannot have moved — add them to "
        "KERNEL_NON_SURFACE_MODULES in rebuild/review/unit_cache.py: " + ", ".join(strays)
    )


def test_the_enumeration_and_the_fold_stay_outside_the_store_stamp():
    """The narrowing stated at the grain a crate lever lands at: the enumeration, the fold and the fan-out are where the crate's own levers go, and an edit there must not cost the review surface a cold units phase — while the verbs' contract in main.rs, the crate root, the engine and both Cargo files stay in, since any of them can move what settle-cases answers."""
    stamped = {path.name for path in unit_cache.surface_code_paths(REPO_ROOT)}
    assert {"fixpoint.rs", "fold.rs", "fanout.rs", "artifacts.rs"} <= unit_cache.KERNEL_NON_SURFACE_MODULES
    assert not ({"fixpoint.rs", "fold.rs", "fanout.rs"} & stamped)
    assert {"main.rs", "lib.rs", "engine.rs", "cases.rs", "Cargo.toml", "Cargo.lock"} <= stamped


def test_every_roster_entry_is_on_disk():
    """A rename that leaves either roster behind would hash a module the surface runs under a name that no longer exists — `fingerprint.hash_paths` reads a missing path as a stable absence — so both are checked against the disk directly."""
    missing = sorted(
        name for name in unit_cache.PIPELINE_NON_SURFACE_MODULES if not (PIPELINE_DIR / name).is_file()
    )
    missing += sorted(
        name for name in unit_cache.KERNEL_NON_SURFACE_MODULES if not (KERNEL_SRC / name).is_file()
    )
    assert missing == [], f"the surface stamp's rosters name files that are not there: {', '.join(missing)}"


def test_the_store_stamp_is_strictly_narrower_than_the_run_record():
    """What the narrowing buys, spelled at the file grain a reader can check: the store stamp's non-review side is a strict subset of `pipeline_code_paths`, leaving out the driver, the oracle, the font compile and its tools roster, and the crate's enumeration — while keeping the kernel seam, the shaper, the engine and the enricher."""
    surface = set(unit_cache.surface_code_paths(REPO_ROOT))
    run_record = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    assert surface - set(fingerprint.review_code_paths(REPO_ROOT)) < run_record
    for relative in (
        "rebuild/pipeline/run_m1.py",
        "rebuild/pipeline/oracle.py",
        "rebuild/pipeline/compile_font.py",
        "tools/build_font.py",
        "rebuild/kernel-rs/src/fixpoint.rs",
    ):
        assert REPO_ROOT / relative in run_record and REPO_ROOT / relative not in surface, relative
    for relative in (
        "rebuild/pipeline/kernel_exec.py",
        "rebuild/validation/shaping.py",
        "rebuild/kernel-rs/src/engine.rs",
        "rebuild/review/enrich.py",
    ):
        assert REPO_ROOT / relative in surface, relative
